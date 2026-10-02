"""Collect public meetings from the city's calendars and archive their agendas.

Writes data/meetings/meetings.json (one record per meeting, never deleted) and
saves each posted agenda PDF under data/meetings/agendas/ (or in the town's
bucket; see pipeline/documents.py). Changes the city
makes after posting (new time, new place, revised agenda, cancellation) are
recorded in each meeting's history so they stay visible.

A town's calendars are the tables in [meetings]: a CivicPlus calendar, read
month by month ([meetings.civicplus], which also saves agendas linked from
its events) or from its feed (calendar_feed, which reaches only a week or two
ahead), a CivicPlus Agenda Center
([meetings.agenda_center], which also saves agendas and lists minutes), a
CivicClerk portal ([meetings.civicclerk], which also saves agendas), a
DotNetNuke city calendar ([meetings.dnn], which also saves agendas linked as
PDFs named for the meeting's date) and a town website with a meetings calendar
and one documents page for every board's agendas and minutes
([meetings.file_list], which also saves agendas and lists minutes), and a
school district's calendar feed ([ical_meetings]: its board's meetings, as
the district's own, without documents). A town can
have several; Manchester's aldermanic meetings are on CivicClerk and its other
boards on the city calendar, and Malden lists its meetings on its calendar and
posts their agendas in its Agenda Center. A meeting listed in more than one
place is kept as one record for each, and shown as one (pipeline/listings.py).

Usage:
    python -m pipeline.fetch_meetings [--town gloucester]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from pypdf import PdfReader

from pipeline import agendacenter, civicclerk, civicplus, dnn, filelist, ical
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.http import FetchError, PoliteClient
from pipeline.meeting_names import words

# Fields whose changes are recorded in a meeting's history.
TRACKED_FIELDS = ("title", "date", "start_time", "location_name", "address", "status", "listed")


def meetings_dir(data_dir: Path) -> Path:
    return data_dir / "meetings"


def load_store(data_dir: Path) -> dict:
    path = meetings_dir(data_dir) / "meetings.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def normalize_body(body: str, aliases: dict) -> str:
    for name, alias in aliases.items():
        if body.lower() == name.lower():
            return alias
    return body


def known_body(body: str, known: dict, town: str) -> str:
    """A board's name as already recorded, when this one is the same words written another
    way ("Open Space and Recreation Committee" for "Open Space & Recreation Committee"), or
    with the town's name before it ("Malden Cultural Council" for "Cultural Council"). known
    maps words() of each recorded name to the name."""
    w = words(body)
    if w in known:
        return known[w]
    prefix = words(town)
    if w.startswith(prefix) and " " + w[len(prefix):] in known:
        return known[" " + w[len(prefix):]]
    return body


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", "and")).strip("-")


def unique_slug(slug: str, store: dict) -> str:
    """Page slugs are fixed when a meeting is first recorded so links never break."""
    taken = {m.get("slug") for m in store.values()}
    candidate, n = slug, 2
    while candidate in taken:
        candidate, n = f"{slug}-{n}", n + 1
    return candidate


def pdf_has_text(content: bytes) -> bool | None:
    """True if the PDF has a text layer; False for a scanned image; None if unreadable."""
    try:
        reader = PdfReader(io.BytesIO(content))
        return any((page.extract_text() or "").strip() for page in reader.pages[:3])
    except Exception:
        return None


def record_change(meeting: dict, field: str, old, new, at: str) -> None:
    meeting.setdefault("history", []).append({"at": at, "field": field, "old": old, "new": new})


def merge(meeting: dict, updates: dict, at: str, track: bool) -> None:
    for field, new in updates.items():
        old = meeting.get(field)
        if field in meeting and old == new:
            continue
        if track and field in TRACKED_FIELDS and field in meeting:
            record_change(meeting, field, old, new, at)
        meeting[field] = new


def fetch_agenda(client, meeting: dict, details: dict, storage, now: str) -> None:
    agenda_id = details.get("agenda_id")
    if not agenda_id:
        return
    agendas = meeting.setdefault("agendas", [])
    if any(a["id"] == agenda_id for a in agendas):
        return
    response = client.get(details["agenda_url"])
    content = response.content
    if not content.startswith(b"%PDF"):
        raise FetchError(f"agenda {agenda_id} is not a PDF")
    disposition = response.headers.get("content-disposition", "")
    filename = re.search(r'filename="?([^";]+)', disposition)
    name = f"{agenda_id}.pdf"
    storage.put("agendas", name, content)
    if agendas:
        record_change(meeting, "agenda", agendas[-1]["id"], agenda_id, now)
    agendas.append({
        "id": agenda_id,
        "source_url": details["agenda_url"],
        "file": name,
        "original_filename": filename.group(1).strip() if filename else None,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "has_text": pdf_has_text(content),
        "fetched_at": now,
    })


def adopt_drive_meeting(store: dict, event: dict) -> dict | None:
    """A meeting first recorded from documents in Google Drive (see
    fetch_drive_meetings) that has now appeared on the calendar. It becomes the
    calendar's record, keeping its page address and documents; the title and
    time are the calendar's, not changes to record."""
    for key, m in list(store.items()):
        if (m.get("source") == "drive" and not m.get("variant") and m["date"] == event["date"]
                and slugify(m["body"]) == slugify(event["body"])):
            del store[key]
            for field in ("source", "source_name", "variant", "title", "start_time", "end_time"):
                m.pop(field, None)
            m["id"] = event["id"]
            store[event["id"]] = m
            return m
    return None


@dataclass
class Calendar:
    """One place the city lists its meetings.

    events(client, today, store) returns the meetings it lists now. owns(meeting)
    says whether a recorded meeting came from it, so one that drops off can be
    marked as removed. details(client, meeting, storage, stamp), if any, reads
    an upcoming meeting's own page."""
    name: str
    events: Callable[..., list[dict]]
    owns: Callable[[dict], bool]
    details: Callable[..., None] | None = None


def matches(settings: dict, title: str) -> bool:
    """Whether a calendar entry is a public meeting, by the calendar's
    include_pattern and exclude_pattern (both optional)."""
    include, exclude = settings.get("include_pattern"), settings.get("exclude_pattern")
    return (not include or bool(re.search(include, title, re.I))) and not (exclude and re.search(exclude, title, re.I))


def civicplus_calendar(config: dict) -> Calendar:
    """The CivicPlus calendar feed, with each event page's location and agenda."""
    source = config["meetings"]
    base_url = source["base_url"]

    def events(client, today, store):
        feed = client.get(base_url.rstrip("/") + "/" + source["calendar_feed"].lstrip("/"))
        return [e for e in civicplus.parse_calendar_feed(feed.content, base_url) if matches(source, e["title"])]

    def details(client, meeting, storage, stamp):
        page = client.get(meeting["source_url"])
        found = civicplus.parse_event_page(page.text, base_url)
        merge(meeting, {k: found[k] for k in ("location_name", "address", "remote_url") if found.get(k)}, stamp, track=True)
        fetch_agenda(client, meeting, found, storage, stamp)

    return Calendar("city calendar", events, lambda m: m.get("source", "calendar") == "calendar" and not m["id"].startswith("dnn-"), details)


def civicplus_list_calendar(config: dict) -> Calendar:
    """A CivicPlus city calendar's list view ([meetings.civicplus]), month by month: this
    month and months_ahead (default 2) more, one request each. Only the city's calendars
    named in `calendars` are read ("City Meetings"), or all of them; include_pattern and
    exclude_pattern pick the public meetings among their events. A town with an Agenda
    Center gets its agendas there, so its event pages aren't read; another reads each
    upcoming meeting's page for its online link and its agenda, as from the feed."""
    source = config["meetings"]
    settings = source["civicplus"]
    base_url = settings.get("base_url", source.get("base_url"))
    wanted = {c.lower() for c in settings.get("calendars", [])}
    # The patterns can be given in [meetings], as for the feed.
    rules = {k: settings.get(k, source.get(k)) for k in ("include_pattern", "exclude_pattern")}

    def events(client, today, store):
        month, found = today.replace(day=1), {}
        for _ in range(settings.get("months_ahead", 2) + 1):
            for e in civicplus.parse_list(client.get(civicplus.list_url(base_url, month)).text, base_url):
                if (not wanted or e["calendar"].lower() in wanted) and matches(rules, e["raw_title"]):
                    e.pop("calendar"), e.pop("calendar_id")
                    found[e["id"]] = e
            month = (month + timedelta(days=32)).replace(day=1)
        return list(found.values())

    def details(client, meeting, storage, stamp):
        found = civicplus.parse_event_page(client.get(meeting["source_url"]).text, base_url)
        merge(meeting, {k: found[k] for k in ("remote_url",) if found.get(k)}, stamp, track=True)
        fetch_agenda(client, meeting, found, storage, stamp)

    return Calendar("city calendar", events, lambda m: m.get("source", "calendar") == "calendar" and m["id"].isdigit(),
                    None if "agenda_center" in source else details)


def agenda_center_calendar(config: dict) -> Calendar:
    """Every board's agendas in a CivicPlus Agenda Center ([meetings.agenda_center])."""
    source = config["meetings"]
    settings = source["agenda_center"]
    excluded = {c.lower() for c in settings.get("exclude_categories", [])}

    def events(client, today, store):
        # The first run lists every meeting since `since`. Later runs re-read
        # the last few weeks (for minutes and late changes) and the weeks ahead.
        start = date.fromisoformat(settings["since"])
        if any(m.get("source") == "agendacenter" for m in store.values()):
            start = max(start, today - timedelta(days=settings.get("recheck_days", 60)))
        end = today + timedelta(days=settings.get("days_ahead", 60))
        page = client.get(agendacenter.search_url(settings["base_url"], start, end))
        rows = agendacenter.parse_listing(page.text, settings["base_url"])
        return [agendacenter.to_event(r, source.get("aliases", {}), settings.get("committees", {}))
                for r in rows if r["category"].lower() not in excluded]

    def details(client, meeting, storage, stamp):
        fetch_agenda(client, meeting, meeting, storage, stamp)

    return Calendar("Agenda Center", events, lambda m: m.get("source") == "agendacenter", details)


def civicclerk_calendar(config: dict) -> Calendar:
    """Meetings on the city's CivicClerk portal ([meetings.civicclerk])."""
    source = config["meetings"]
    settings = source["civicclerk"]
    states = {config["town"]["state"]: config["town"]["state_abbr"]}
    # A town that collects documents ([meetings] documents, default true) saves
    # agendas here and minutes in pipeline.fetch_minutes.
    api_url = settings["api_url"] if source.get("documents", True) else None

    def events(client, today, store):
        since = date.fromisoformat(settings["since"])
        # The first run collects every meeting since `since`. Later runs re-read
        # the last few weeks and everything ahead, where changes happen. So does
        # the first run after documents are turned on, for earlier meetings' files.
        recorded = [m for m in store.values() if m.get("source") == "civicclerk"]
        if recorded and (not api_url or any("agenda_id" in m for m in recorded)):
            since = max(since, today - timedelta(days=settings.get("recheck_days", 60)))
        url, found = civicclerk.events_url(settings["api_url"], since), []
        for _ in range(settings.get("max_pages", 200)):
            data = client.get(url).json()
            found += civicclerk.parse_events(data, settings["portal_url"], source.get("boards", []), source.get("aliases", {}),
                                             states, api_url)
            url = civicclerk.next_page(data)
            if not url:
                break
        return [e for e in found if matches(settings, e["title"])]

    def details(client, meeting, storage, stamp):
        fetch_agenda(client, meeting, meeting, storage, stamp)

    return Calendar("CivicClerk", events, lambda m: m.get("source") == "civicclerk", details if api_url else None)


def dnn_calendar(config: dict) -> Calendar:
    """Meetings on a DotNetNuke city calendar ([meetings.dnn]), month by month."""
    source = config["meetings"]
    settings = source["dnn"]
    calendar_url = settings["calendar_url"]
    # Meetings this calendar links to CivicClerk are collected from CivicClerk.
    portal = source.get("civicclerk", {}).get("portal_url")
    documents = source.get("documents", True)

    def events(client, today, store):
        month = today.replace(day=1)
        if not any(m["id"].startswith("dnn-") for m in store.values()):
            month = date.fromisoformat(settings.get("since", month.isoformat())).replace(day=1)
        last = today.replace(day=1)
        for _ in range(settings.get("months_ahead", 1)):
            last = (last + timedelta(days=32)).replace(day=1)
        found = {}
        while month <= last:
            page = client.get(dnn.month_url(calendar_url, settings["module_id"], month))
            for e in dnn.parse_month(page.text, calendar_url, source.get("boards", []), source.get("aliases", {})):
                links = e.pop("links")
                if (portal and any(link["url"].startswith(portal) for link in links)) or not matches(settings, e["title"]):
                    continue
                e["documents_url"] = next((link["url"] for link in links if "agenda" in link["text"].lower()), None)
                if documents:
                    e.update(dnn.agenda_file(links, e["date"]) or {})
                found[e["id"]] = e
            month = (month + timedelta(days=32)).replace(day=1)
        return list(found.values())

    def details(client, meeting, storage, stamp):
        merge(meeting, dnn.parse_event_page(client.get(meeting["source_url"]).text), stamp, track=True)
        if documents:
            fetch_agenda(client, meeting, meeting, storage, stamp)

    return Calendar("city calendar", events, lambda m: m["id"].startswith("dnn-"), details)


def file_list_calendar(config: dict) -> Calendar:
    """A town website's meetings calendar and its documents page of every board's
    agendas and minutes ([meetings.file_list]): one request for the documents page
    and one for each month, this month and months_ahead (default 1). Documents
    count from `since`; only the boards in [meetings] boards are collected."""
    source = config["meetings"]
    settings = source["file_list"]
    boards, aliases = source.get("boards", []), source.get("aliases", {})
    if not boards:
        raise SystemExit("[meetings.file_list] needs [meetings] boards: the boards to collect.")
    listed_boards = {words(b) for b in boards}
    documents = source.get("documents", True)

    def events(client, today, store):
        month, listed = today.replace(day=1), []
        for _ in range(settings.get("months_ahead", 1) + 1):
            page = client.get(filelist.month_url(settings["calendar_url"], month))
            listed += [e for e in filelist.parse_month(page.text, settings["calendar_url"], boards, aliases)
                       if words(normalize_body(e["body"], aliases)) in listed_boards]
            month = (month + timedelta(days=32)).replace(day=1)
        files = filelist.parse_documents(client.get(settings["documents_url"]).text, settings["documents_url"])
        found = filelist.meeting_documents(files, boards, aliases, settings["since"])
        folders = {words(b) for f in files if (b := filelist.folder_board(f["folders"][0], boards, aliases))}
        filelist.adopt(store, listed)
        recorded = [m for m in store.values() if m.get("source") == filelist.SOURCE]
        return filelist.combine(listed, found, recorded, settings["documents_url"], folders)

    def details(client, meeting, storage, stamp):
        # A calendar meeting's own page gives its location; it is read once.
        if "location_name" not in meeting and meeting["source_url"] != settings["documents_url"]:
            merge(meeting, filelist.parse_event_page(client.get(meeting["source_url"]).text), stamp, track=True)
        if documents:
            fetch_agenda(client, meeting, meeting, storage, stamp)

    return Calendar("town website", events, lambda m: m.get("source") == filelist.SOURCE, details)


def ical_calendar(config: dict) -> Calendar:
    """A school district's (or another body's) calendar feed ([ical_meetings]): its meetings of
    the boards in `bodies`, from `since` to days_ahead (default 90) days out, one request."""
    settings = config["ical_meetings"]

    def events(client, today, store):
        ahead = (today + timedelta(days=settings.get("days_ahead", 90))).isoformat()
        found = []
        for event in ical.parse_events(client.get(settings["ical_url"]).text):
            meeting = ical.to_meeting(event, settings["bodies"])
            if meeting and settings["since"] <= meeting["date"] <= ahead:
                found.append({**meeting, "source_url": meeting["source_url"] or settings["page_url"],
                              "source_name": settings["source_name"]})
        return found

    return Calendar(settings["source_name"], events, lambda m: m.get("source") == ical.SOURCE)


def calendars(config: dict) -> list[Calendar]:
    """The town's meeting calendars, by the tables in [meetings]."""
    source = config["meetings"]
    found = []
    if "civicplus" in source:
        found.append(civicplus_list_calendar(config))
    elif "calendar_feed" in source:
        found.append(civicplus_calendar(config))
    if "agenda_center" in source:
        found.append(agenda_center_calendar(config))
    if "civicclerk" in source:
        found.append(civicclerk_calendar(config))
    if "dnn" in source:
        found.append(dnn_calendar(config))
    if "file_list" in source:
        found.append(file_list_calendar(config))
    if "ical_meetings" in config:
        found.append(ical_calendar(config))
    return found


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    """Update the meetings store. Returns a summary for logging."""
    tz = ZoneInfo(config["site"]["timezone"])
    now = now or datetime.now(tz)
    stamp = now.isoformat(timespec="seconds")
    today = now.date()
    source = config["meetings"]
    aliases = source.get("aliases", {})

    store = load_store(data_dir)
    storage = open_documents(config, data_dir)
    status_path = meetings_dir(data_dir) / "status.json"
    previous = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    # Each calendar's last successful check, kept when a check fails.
    checked = {name: c for name, c in previous.get("calendars", {}).items()}
    errors, failed = [], []
    seen: dict[str, Calendar] = {}
    new_count = 0
    # Each board's name as first recorded, so one listed in two places is one board.
    known = {words(m["body"]): m["body"] for m in sorted(store.values(), key=lambda m: m.get("first_seen", ""), reverse=True)}
    for calendar in calendars(config):
        try:
            events = calendar.events(client, today, store)
        except FetchError as e:
            # Keep what is recorded; the site shows when meetings were last checked.
            errors.append(f"{calendar.name}: {e}")
            failed.append(calendar.name)
            continue
        checked[calendar.name] = {"updated_at": stamp, "listed": len(events)}
        for event in events:
            seen[event["id"]] = calendar
            event["body"] = known_body(normalize_body(event["body"], aliases), known, config["town"]["name"])
            known.setdefault(words(event["body"]), event["body"])
            event.pop("raw_title", None)
            meeting = store.get(event["id"]) or adopt_drive_meeting(store, event)
            if meeting is None:
                meeting = store[event["id"]] = {
                    "id": event["id"],
                    "first_seen": stamp,
                    "slug": unique_slug(f"{event['date']}-{slugify(event['body'])}", store),
                }
                new_count += 1
            merge(meeting, {**event, "listed": True}, stamp, track=True)
            meeting["last_seen"] = stamp

        # A future meeting that drops off its calendar while other meetings on
        # or after its date are still listed was most likely removed by the city.
        latest_listed = max((e["date"] for e in events), default=None)
        for meeting in store.values():
            if meeting["id"] in seen or not meeting.get("listed", True) or not calendar.owns(meeting):
                continue
            if meeting["date"] >= today.isoformat() and latest_listed and meeting["date"] <= latest_listed:
                merge(meeting, {"listed": False}, stamp, track=True)

    # Event pages hold details the listing leaves out (location, agenda). Only
    # upcoming meetings are re-checked, so a run makes a few dozen requests at most.
    pages = 0
    for meeting in sorted(store.values(), key=lambda m: (m["date"], m["id"])):
        calendar = seen.get(meeting["id"])
        if not calendar or not calendar.details or meeting["date"] < today.isoformat():
            continue
        if pages >= source.get("max_event_pages", 40):
            break
        try:
            pages += 1
            calendar.details(client, meeting, storage, stamp)
            meeting["checked_at"] = stamp
        except FetchError as e:
            errors.append(str(e))

    save_json(meetings_dir(data_dir) / "meetings.json", store)
    names = [c.name for c in calendars(config)]
    checked = {name: c for name, c in checked.items() if name in names}
    # Meetings are as fresh as the calendar checked longest ago, so the site's
    # "checked" date and the stale-data alert (pipeline/freshness.py) notice
    # one calendar failing day after day. One never checked counts from the last update.
    since_last = previous.get("updated_at", stamp)
    status = {
        "updated_at": min([checked[n]["updated_at"] if n in checked else since_last for n in names], default=stamp),
        "listed_in_feed": sum(checked[n]["listed"] for n in names if n in checked and n not in failed),
        "calendars": checked,
        "new_meetings": new_count,
        "event_pages_checked": pages,
        "requests": getattr(client, "request_count", None),
        "errors": errors,
        "failed_calendars": failed,
    }
    save_json(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["meetings"].get("request_delay", 3.0))
    status = run(config, client, args.data)
    print(json.dumps(status, indent=2))
    for error in status["errors"]:
        print(f"::warning::{error}")
    if status["failed_calendars"]:
        # Other calendars' updates are kept; the site shows when meetings were last checked.
        print(f"::error::Meetings could not be fetched from: {', '.join(status['failed_calendars'])}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
