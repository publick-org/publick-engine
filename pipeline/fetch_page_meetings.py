"""Collect a board's meetings, agendas and minutes from a page that links each meeting's documents.

Some school districts post their board's agendas and minutes as files linked
from one page, with no post or calendar entry a meeting (see
pipeline/linkpage.py): Methuen Public Schools in a table with a row a meeting,
Waltham Public Schools as a list of links by month. Meetings are recorded
alongside the town's under data/meetings/, as the district's own: the board
is [page_meetings] body, or the one [page_meetings.bodies] maps a meeting's
name to ("Policy Subcommittee" -> "School Committee Policy Subcommittee").

Each run reads the page once, and each of [page_meetings.pages], a page of one
board's meetings (Nashua's Board of Education has one for each committee). Agendas and minutes are saved as PDFs (a Drive
file as Google gives it for download, a Google Doc as exported), newest
meetings first, until a run has saved max_documents_per_run (default 60; a
meeting's agenda and minutes are saved together, so it can go one over): the
first run of a long year reads the rest on later runs. A Google Doc is edited in
place, so it is exported again while it can still change, as for a Finalsite
board (pipeline/fetch_finalsite_meetings.py); a Drive file's address names
one file, which isn't read again. A YouTube link is the meeting's recording;
other links (postings, folders, videos on Drive) are kept as links, never
downloaded. A cancelled meeting's agenda is linked, not saved. An upcoming
meeting that leaves the page is marked as no longer listed.

[page_meetings] other_pages are read with the page, as one list (a district
that posts its agendas on one page and its minutes on another).

A page of [page_meetings.pages] may be a table instead of a board's name, with
settings of its own (linkpage.PAGE_SETTINGS) over the table's: its board, the name of
whoever posts it (source_name), one_meeting_a_day, exclude_pattern,
name_ignore_pattern, and other_pages read with it. A town whose
[page_meetings] is one department's pages can read another body's this way: Manchester's are the Planning and
Community Development department's, and each of the school district's
document folders is a page with its own board and source_name. An Apptegy
folder of years ("2025", "2026") is read through its years from since on, so
a page needn't change each January. A short link
(an Apptegy aptg.co link) is followed to the file it redirects to, with a HEAD
request, once (apptegy.ShortLinks): where each leads is kept in
meetings/page_status.json (short_links) and asked again only of a link not
resolved before.

A board that links its minutes only from the next meeting's agenda
([page_meetings] minutes_in_agenda, Biddeford School Committee's) has them read
from there (pipeline/agenda_minutes.py); what each agenda linked is kept in
meetings/page_status.json (agenda_minutes), so a past agenda isn't read again.

Entries with a document but no date or no board are listed in the run's
summary, not guessed at.

Usage:
    python -m pipeline.fetch_page_meetings [--town methuen]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import agenda_minutes, apptegy, linkpage
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_finalsite_meetings import update_documents
from pipeline.fetch_meetings import load_store, meetings_dir, merge, save_json, save_store, slugify, unique_slug
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient
from pipeline.waiting import Waiting

STATUS_FILE = "page_status.json"


def page_settings(settings: dict, value) -> tuple[str, dict]:
    """A page of [page_meetings.pages]: its board, and the settings it's read with (the table's, with its own
    over them). Its other_pages are its own, never the table's."""
    if isinstance(value, str):
        return value, {**settings, "other_pages": []}
    return value["board"], {**settings, "other_pages": [],
                            **{key: value[key] for key in linkpage.PAGE_SETTINGS if key in value}}


def new_meeting(store: dict, found: dict, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": f"{linkpage.SOURCE}-{found['key']}",
        "source": linkpage.SOURCE,
        "source_url": found["page_url"],
        "source_name": found.get("source_name") or settings["source_name"],
        "first_seen": stamp,
        "slug": unique_slug(f"{found['date']}-{slugify(found['body'])}", store),
        "date": found["date"],
        "body": found["body"],
        # The time the page gives, so a meeting's first record doesn't show it as a change.
        "start_time": found.get("start_time"),
        "end_time": None,
        "location": "",
    }
    store[meeting["id"]] = meeting
    return meeting


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["page_meetings"]
    since = str(settings["since"])
    recent = (now.date() - timedelta(days=settings.get("recheck_days", 60))).isoformat()
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)

    errors, found, unrecognized = [], [], []
    status_path = meetings_dir(data_dir) / STATUS_FILE
    previous = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    short_links = apptegy.ShortLinks(client, previous.get("short_links", {}), errors)

    def pages_of(page: str, url: str) -> list[tuple[str, str]]:
        # An Apptegy folder of years: its years from since on, each a page read with it.
        read = []
        for year_url in apptegy.year_folders(page, url, since):
            try:
                read.append((client.get(year_url).text, year_url))
            except FetchError as e:
                errors.append(str(e))
        return read

    pages = [(settings["page_url"], None, settings),
             *((url, *page_settings(settings, value)) for url, value in settings.get("pages", {}).items())]
    for url, board, read_with in pages:
        more = []
        for other in read_with.get("other_pages", []):
            try:
                other_page = client.get(other).text
            except FetchError as e:
                errors.append(str(e))
                continue
            more += [(other_page, other), *pages_of(other_page, other)]
        try:
            page = client.get(url).text
        except FetchError as e:
            if board is None:
                raise
            errors.append(str(e))
            continue
        more = pages_of(page, url) + more
        on_page, not_read = linkpage.meetings(page, url, read_with, board, more, short_links.resolve)
        if not on_page:
            # A page lists at least the current year's meetings: none means its layout changed.
            if board is None:
                raise FetchError(f"{url}: no meetings found")
            errors.append(f"{url}: no meetings found")
        found += [{**m, "page_url": url, "source_name": read_with["source_name"]} for m in on_page]
        unrecognized += not_read

    # A board that links its minutes only from the next meeting's agenda (pipeline/agenda_minutes.py).
    in_agendas = settings.get("minutes_in_agenda", False)
    minutes_in_agendas = previous.get("agenda_minutes", {}) if in_agendas else {}
    if in_agendas:
        agenda_minutes.link(client, found, since, today, minutes_in_agendas, errors)

    created, listed = 0, set()
    for item in found:
        if item["date"] < since:
            continue
        meeting = store.get(f"{linkpage.SOURCE}-{item['key']}")
        if meeting is None:
            meeting = new_meeting(store, item, settings, stamp)
            created += 1
        fields = {k: v for k, v in item.items() if k not in ("key", "name", "start_time", "page_url", "source_name")}
        fields["source_url"] = item["page_url"]
        if item["start_time"]:
            fields["start_time"] = item["start_time"]
        merge(meeting, {**fields, "title": title_for(item["body"], item["special"]), "posted_title": item["name"],
                        "listed": True}, stamp, track=True)
        meeting["last_seen"] = stamp
        listed.add(meeting["id"])

    counts = {"added": 0, "changed": 0, "checked": 0}
    limit, waiting = settings.get("max_documents_per_run", 60), Waiting()
    for meeting in sorted(store.values(), key=lambda m: (m["date"], m["id"]), reverse=True):
        if meeting.get("source") != linkpage.SOURCE or meeting["date"] < since:
            continue
        # An upcoming meeting no longer on the page was most likely called off or moved.
        if meeting["id"] not in listed and meeting["date"] >= today and meeting.get("listed", True):
            merge(meeting, {"listed": False}, stamp, track=True)
        if counts["added"] + counts["changed"] >= limit:
            waiting.add(meeting["date"], sum(1 for kind, field in (("agenda", "agendas"), ("minutes", "minutes"))
                                             if meeting.get(f"{kind}_url") and not meeting.get(field)
                                             and not (kind == "agenda" and meeting["status"] == "cancelled")))
            continue
        # Saved as posted by whoever posts its page ("the Manchester School District website").
        update_documents(client, meeting, storage, {**settings, "source_name": meeting.get("source_name") or settings["source_name"]},
                         today, recent, stamp, counts, errors)

    save_store(data_dir, store, config)
    reported = previous.get("unrecognized", [])
    status = {
        "updated_at": stamp,
        "meetings_listed": len(found),
        "meetings_created": created,
        "documents_added": counts["added"],
        "documents_changed": counts["changed"],
        "documents_rechecked": counts["checked"],
        **waiting.status("documents"),
        "requests": getattr(client, "request_count", None),
        # Entries with a document but no date, or no board: add the board to [page_meetings.bodies] if it's one.
        "unrecognized": sorted(set(unrecognized)),
        "new_unrecognized": sorted(set(unrecognized) - set(reported)),
        "errors": errors,
        # Where each short link on the pages leads, so it isn't asked again.
        "short_links": dict(sorted(short_links.used.items())),
        # The minutes each agenda links ([page_meetings] minutes_in_agenda), so a packet isn't read again.
        **({"agenda_minutes": dict(sorted(minutes_in_agendas.items()))} if in_agendas else {}),
    }
    save_json(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "page_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["page_meetings"].get("request_delay", 3.0))
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::The board's documents page could not be read: {e}")
        return 1
    print(json.dumps(status, indent=2))
    for entry in status["new_unrecognized"]:
        print(f"::warning::No date or no listed board for {entry!r}")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
