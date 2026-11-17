"""Collect a city's meetings from its WordPress calendar (The Events Calendar), and their agendas and minutes from its document library.

The meetings are the events of the site's calendar that are boards' meetings (pipeline/tribe.py), recorded
alongside the town's under data/meetings/, as posted by [wordpress_meetings] source_name. With a
[wordpress_meetings.documents] table, each meeting's agendas and minutes come from the site's document
library, each joined to its meeting by the event it links (pipeline/wpdocs.py). The two halves are apart:
a site with the calendar alone has no documents table.

Each run:
- lists the calendar's events from `since` (the first run) or recheck_days (default 60) ago to days_ahead
  (default 90) ahead, 50 a page, about three months at a time: a year of meetings and other events is a page
  or two a month. Each
  board's meeting is recorded, or updated with what changed (its time, place, status), and an upcoming
  meeting the calendar no longer lists in that window is marked as no longer listed. The categories its
  settings don't list are reported (unlisted_categories), and the names whose board came from their
  category alone, without naming it (filed_by_category: "COA Meeting" -> "Council on Aging"), so a person
  can check [wordpress_meetings.bodies];
- lists the library's agendas and minutes changed since the previous listing, less two days (one request
  on most days; the first run lists those changed from 90 days before `since`), resolving the two
  categories by name (one request) and the files' addresses all at once (one request). A document is
  saved when it's new or its post has changed since it was saved (its `modified`), and saved as a new
  version, with the change in the meeting's history, only when its content changed. Documents are saved
  newest meeting first, until the run has saved max_documents_per_run (default 60); the rest wait in
  meetings/wordpress_status.json (to_read) for the next run, as does a document whose download failed (for
  three runs). A document whose meeting isn't recorded (its event isn't a listed board's, or it links none
  and its title doesn't name one meeting) is kept there too (unmatched), for a year, and read again once
  its meeting is recorded. `minutes = false` leaves minutes out.

The status file counts the documents listed that link no event (documents_without_event): a few are saved
without the link, but most of them is the sign that the site has changed its document fields, and the run
warns of it. A run that couldn't read the library says why (documents_error), rather than counting none.

Requests are spaced request_delay (default 2) seconds apart, across every process on the machine that reads
the same site (pipeline/http.py HostLimiter). A refusal (HTTP 401, 403, or 429 after the client's retries)
or a browser check stops the run with an error: Publick never works around one.

Usage:
    python -m pipeline.fetch_wordpress_meetings [--town brockton]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from pipeline import htmlfile, tribe, wordfile, wordpress, wpdocs
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import (
    load_store, meetings_dir, merge, pdf_has_text, record_change, save_json, save_store, slugify, unique_slug,
)
from pipeline.filelist import title_for
from pipeline.http import FetchError, HostLimiter, PoliteClient, is_browser_check
from pipeline.waiting import Waiting

STATUS_FILE = "wordpress_status.json"
# The most pages one listing reads: more is an error, not a part of the listing. The calendar is listed a
# stretch of EVENT_DAYS at a time, so the limit is a stretch's, whatever the days asked for (a first run from
# a `since` years back).
EVENT_PAGES = 60
EVENT_DAYS = 92
DOCUMENT_PAGES = 50
# How far before `since` the first run lists the documents changed from.
FIRST_RUN_BACK = timedelta(days=90)
# How many runs a document whose download fails is tried on.
TRIES = 3
# How long a document whose meeting isn't recorded is kept, by its date, to be read once it is.
KEEP_UNMATCHED = timedelta(days=366)
# A site's refusals: the run stops rather than ask again.
REFUSED = (401, 403, 429)


class Blocked(FetchError):
    """The site answered with a browser check, or refused: the run stops."""


def stop_at_browser_check(response, *args, **kwargs):
    """A response hook: a browser check stops the run (Blocked), whatever was asked."""
    if is_browser_check(response):
        raise Blocked(f"{response.url}: answered with a browser check", response.status_code)


def stop_if_refused(error: FetchError) -> None:
    """Raise a refusal or a browser check again, to stop the run: never read around one."""
    if isinstance(error, Blocked) or error.status in REFUSED:
        raise error


def meeting_id(event_id: str) -> str:
    return f"{tribe.SOURCE}-{event_id}"


def new_meeting(store: dict, event: dict, info: dict, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": meeting_id(event["id"]),
        "source": tribe.SOURCE,
        "source_url": event["url"],
        "source_name": settings["source_name"],
        "event_id": event["id"],
        "first_seen": stamp,
        "slug": unique_slug(f"{event['date']}-{slugify(info['body'])}", store),
        "date": event["date"],
        "body": info["body"],
        # As first listed, so the first run's merge records no change.
        "start_time": event["start_time"],
        "end_time": event["end_time"],
        "location_name": event["location_name"],
        "address": event["address"],
        "remote_url": event["remote_url"],
        "status": info["status"],
    }
    store[meeting["id"]] = meeting
    return meeting


def record_events(store: dict, events: list[dict], settings: dict, since: str, stamp: str) -> dict:
    """Record or update each board's meeting the events list. Returns the ids of the meetings listed, how many
    were created, the unlisted categories' counts, and the names filed under their category's board alone."""
    listed, created, unlisted, by_category = set(), 0, Counter(), {}
    listed_categories = set(tribe.categories(settings))
    known = listed_categories | {c.strip().lower() for c in settings.get("exclude_categories", [])}
    for event in events:
        if not event["date"] or event["date"] < since:
            continue
        info = tribe.meeting_info(event, settings)
        if info is None:
            names = [c.strip().lower() for c in event["categories"]]
            # An event exclude_categories leaves out says nothing of its other categories.
            if not any(c in known and c not in listed_categories for c in names):
                unlisted.update(c for c in event["categories"] if c.strip().lower() not in known)
            continue
        name = tribe.name_of(event["title"])
        if info["by_category"] and not tribe.says_board(name, info["body"]):
            by_category[name] = info["body"]
        meeting = store.get(meeting_id(event["id"]))
        if meeting is None:
            meeting = new_meeting(store, event, info, settings, stamp)
            created += 1
        fields = {"date": event["date"], "body": info["body"], "status": info["status"], "special": info["special"],
                  "title": title_for(info["body"], info["special"]), "posted_title": event["title"],
                  "location_name": event["location_name"], "address": event["address"],
                  "remote_url": event["remote_url"], "documents_url": event["url"], "listed": True}
        # An event made all-day has no time any longer.
        fields.update(start_time=event["start_time"], end_time=event["end_time"])
        merge(meeting, fields, stamp, track=True)
        meeting["last_seen"] = stamp
        listed.add(meeting["id"])
    return {"listed": listed, "created": created, "unlisted": unlisted, "by_category": by_category}


def mark_gone(store: dict, listed: set, today: str, until: str, stamp: str) -> None:
    """Mark the upcoming meetings the listing no longer has, within the days it covers, as no longer listed:
    an event deleted, or moved out of its board's category."""
    for meeting in store.values():
        if (meeting.get("source") == tribe.SOURCE and today <= meeting["date"] <= until
                and meeting["id"] not in listed and meeting.get("listed", True)):
            merge(meeting, {"listed": False}, stamp, track=True)


class Saver:
    """Saves meetings' agendas and minutes from the library's documents, counting what it saved."""

    def __init__(self, client, storage, settings: dict, stamp: str):
        self.client, self.storage, self.settings, self.stamp = client, storage, settings, stamp
        self.added = self.changed = 0

    @property
    def saved(self) -> int:
        return self.added + self.changed

    @staticmethod
    def up_to_date(meeting: dict, doc: dict) -> bool:
        """Whether this version of the document's post is saved already, or a document the meeting's event
        links already stands for this one, which only its title joined."""
        saved = meeting.get("agendas" if doc["kind"] == "agenda" else "minutes", [])
        if any(d.get("post_id") == doc["id"] and d.get("post_modified") == doc["modified"] for d in saved):
            return True
        return doc["joined"] == "title" and any(d.get("joined") == "event" for d in saved)

    def document(self, meeting: dict, doc: dict, file_urls: dict[str, str]) -> None:
        """Save a document's file, or its text as a PDF, unless it's the same as the meeting's latest of its kind."""
        if doc["media_id"]:
            url = file_urls.get(doc["media_id"])
            if not url:
                raise FetchError(f"{doc['link']}: its file (media {doc['media_id']}) isn't listed")
            content, converted = wordfile.as_pdf(self.client.get(url).content, url)
            source_url, original = url, unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        else:
            if not htmlfile.paragraphs(doc["html"]):
                raise FetchError(f"{doc['link']}: no file, and no text")
            content, converted = htmlfile.to_pdf(doc["html"]), "html"
            source_url, original = doc["link"], None
        self.keep(meeting, doc, content, {"title": doc["title"], "source_url": source_url, "original_filename": original,
                                          **({"converted_from": converted} if converted else {})})

    def keep(self, meeting: dict, doc: dict, content: bytes, record: dict) -> None:
        field = "agendas" if doc["kind"] == "agenda" else "minutes"
        saved = meeting.get(field, [])
        latest = saved[-1] if saved else None
        sha = hashlib.sha256(content).hexdigest()
        post = {"post_id": doc["id"], "post_modified": doc["modified"], "joined": doc["joined"]}
        if latest and latest["sha256"] == sha:
            # The post changed, not its document: remember the change, so it isn't read again.
            latest.update(post, source_url=record["source_url"])
            return
        doc_id = f"{tribe.SOURCE}-{doc['id']}-{sha[:8]}"
        self.storage.put(field, f"{doc_id}.pdf", content)
        saved_doc = {"id": doc_id, **record, **post, "page_url": doc["link"], "file": f"{doc_id}.pdf", "sha256": sha,
                     "bytes": len(content), "has_text": pdf_has_text(content), "fetched_at": self.stamp}
        if latest:
            record_change(meeting, doc["kind"], latest["id"], doc_id, self.stamp)
            self.changed += 1
        else:
            self.added += 1
        meeting.setdefault(field, []).append(saved_doc)


def by_day(store: dict) -> dict[tuple[str, str], list[dict]]:
    """The reader's meetings by board and date."""
    found: dict[tuple[str, str], list[dict]] = {}
    for meeting in store.values():
        if meeting.get("source") == tribe.SOURCE:
            found.setdefault((meeting["body"], meeting["date"]), []).append(meeting)
    return found


def find_meeting(store: dict, days: dict, doc: dict, settings: dict, since: str) -> tuple[dict | None, str]:
    """A document's meeting, and how it was found ("event", "title"), or None and why not ("before", "no
    meeting", "no date"). `days` is by_day(store)."""
    if doc["event_id"]:
        meeting = store.get(meeting_id(doc["event_id"]))
        if meeting:
            return meeting, "event"
        return None, "before" if doc["date"] and doc["date"] < since else "no meeting"
    if not doc["date"]:
        return None, "no date"
    if doc["date"] < since:
        return None, "before"
    found = days.get((wpdocs.board_of(doc["title"], settings), doc["date"]), [])
    return (found[0], "title") if len(found) == 1 else (None, "no meeting")


def resolvable(entry: dict, store: dict, days: dict, settings: dict) -> bool:
    """Whether a document whose meeting wasn't recorded may now be: its event is, or a meeting of the board its
    title names, on its date."""
    if entry.get("event_id"):
        return meeting_id(entry["event_id"]) in store
    # As find_meeting joins it: only to the one meeting of its board that day.
    return len(days.get((wpdocs.board_of(entry["title"], settings), entry.get("date")), [])) == 1


def read_documents(client, store: dict, settings: dict, previous: dict, saver: Saver, since: str, stamp: str,
                   errors: list, waiting: Waiting) -> dict:
    """List the library's changed agendas and minutes and save them to their meetings. Returns the status
    file's fields of this half."""
    site = settings["site_url"]
    terms = wordpress.read_all(client, site, wpdocs.TAXONOMY_ROUTE, fields=wpdocs.TERM_FIELDS, max_pages=5)
    try:
        term_kinds = wpdocs.kinds(terms, settings["documents"])
    except ValueError as e:
        raise FetchError(f"{wordpress.api_url(site, wpdocs.TAXONOMY_ROUTE)}: {e}") from None
    first = (date.fromisoformat(since) - FIRST_RUN_BACK).isoformat()
    after = wordpress.changed_since(previous.get("documents_listed_at")) or f"{first}T00:00:00"
    listed = [wpdocs.parse_document(item, term_kinds) for item in wordpress.read_all(
        client, site, wpdocs.ROUTE, wpdocs.listing_params(list(term_kinds), after), fields=wpdocs.LISTING_FIELDS,
        max_pages=DOCUMENT_PAGES)]
    docs = {d["id"]: d for d in listed}

    # Documents left from earlier runs: those waiting, and those whose meeting may be recorded now.
    # The documents waiting, and how many runs each has failed (kept if a document is listed again).
    tries = {k: v.get("tries", 0) for k, v in previous.get("to_read", {}).items()}
    to_read = {k: v for k, v in previous.get("to_read", {}).items() if k not in docs}
    days = by_day(store)
    year_ago = (datetime.fromisoformat(stamp).date() - KEEP_UNMATCHED).isoformat()
    unmatched = {k: v for k, v in previous.get("unmatched", {}).items()
                 if k not in docs and (v.get("date") or v.get("listed_on", "")) >= year_ago}
    again = sorted(set(to_read) | {k for k, v in unmatched.items() if resolvable(v, store, days, settings)})
    for start in range(0, len(again), wordpress.PER_PAGE):
        ids = again[start:start + wordpress.PER_PAGE]
        for item in wordpress.read_all(client, site, wpdocs.ROUTE, wpdocs.include_params(ids),
                                       fields=wpdocs.LISTING_FIELDS, max_pages=1):
            docs[str(item["id"])] = wpdocs.parse_document(item, term_kinds)
        # A document no longer published isn't read again.
        for post_id in ids:
            if post_id not in docs:
                to_read.pop(post_id, None)
                unmatched.pop(post_id, None)

    plans = []
    for doc in docs.values():
        if doc["kind"] is None or (doc["kind"] == "minutes" and not settings.get("minutes", True)):
            to_read.pop(doc["id"], None)
            continue
        meeting, how = find_meeting(store, days, doc, settings, since)
        if meeting is None:
            to_read.pop(doc["id"], None)
            if how == "before":
                unmatched.pop(doc["id"], None)
            else:
                unmatched[doc["id"]] = {"title": doc["title"], "link": doc["link"], "kind": doc["kind"], "date": doc["date"],
                                        "event_id": doc["event_id"], "why": how,
                                        "listed_on": unmatched.get(doc["id"], {}).get("listed_on", stamp[:10])}
            continue
        unmatched.pop(doc["id"], None)
        doc["joined"] = how
        if Saver.up_to_date(meeting, doc):
            to_read.pop(doc["id"], None)
            continue
        plans.append((meeting, doc))

    # The files' addresses, all at once.
    media = sorted({doc["media_id"] for _, doc in plans if doc["media_id"]})
    file_urls = {}
    for start in range(0, len(media), wordpress.PER_PAGE):
        file_urls.update(wpdocs.file_urls(wordpress.read_all(
            client, site, wpdocs.MEDIA_ROUTE, wpdocs.include_params(media[start:start + wordpress.PER_PAGE]),
            fields=wpdocs.MEDIA_FIELDS, max_pages=1)))

    limit = settings.get("max_documents_per_run", 60)
    # Newest meeting first; a meeting's documents that link its event before those its title joined (which they
    # stand for), each in the order they were changed, so its latest is the newest.
    plans.sort(key=lambda plan: (plan[1]["joined"] != "event", plan[1]["modified"]))
    for meeting, doc in sorted(plans, key=lambda plan: (plan[0]["date"], plan[0]["id"]), reverse=True):
        if Saver.up_to_date(meeting, doc):
            # Another post the run saved stands for this one (two posts of one agenda).
            to_read.pop(doc["id"], None)
            continue
        if saver.saved >= limit:
            to_read[doc["id"]] = {"tries": tries.get(doc["id"], 0)}
            waiting.add(meeting["date"])
            continue
        try:
            saver.document(meeting, doc, file_urls)
        except FetchError as e:
            stop_if_refused(e)
            errors.append(str(e))
            failed = tries.get(doc["id"], 0) + 1
            if failed < TRIES:
                to_read[doc["id"]] = {"tries": failed}
                continue
        to_read.pop(doc["id"], None)

    return {
        "documents_listed_at": stamp,
        "documents_listed": len(listed),
        # Documents that link no event: a few are saved without it; most of them means the site's fields changed.
        "documents_without_event": sum(1 for d in listed if not d["event_id"]),
        "to_read": to_read,
        "unmatched": unmatched,
    }


def stretches(start: str, end: str, days: int = EVENT_DAYS) -> list[tuple[str, str]]:
    """The days from start to end (YYYY-MM-DD, both included) in stretches of at most `days`."""
    found, first, last = [], date.fromisoformat(start), date.fromisoformat(end)
    while first <= last:
        found.append((first.isoformat(), min(first + timedelta(days=days - 1), last).isoformat()))
        first += timedelta(days=days)
    return found


def list_events(client, settings: dict, start: str, end: str) -> list[dict]:
    """The calendar's events from start to end, a stretch at a time; an event over two stretches once."""
    found = {}
    for first, last in stretches(start, end):
        for item in wordpress.read_all(client, settings["site_url"], tribe.ROUTE, tribe.listing_params(first, last),
                                       key="events", max_pages=EVENT_PAGES):
            event = tribe.parse_event(item)
            found.setdefault(event["id"], event)
    return list(found.values())


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["wordpress_meetings"]
    since = str(settings["since"])
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)
    status_path = meetings_dir(data_dir) / STATUS_FILE
    previous = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}

    # The events from `since` on the first run, else from recheck_days ago, to days_ahead ahead.
    recent = (now.date() - timedelta(days=settings.get("recheck_days", 60))).isoformat()
    start = since if "events_listed_at" not in previous else max(since, recent)
    until = (now.date() + timedelta(days=settings.get("days_ahead", 90))).isoformat()
    events = list_events(client, settings, start, until)
    found = record_events(store, events, settings, since, stamp)
    mark_gone(store, found["listed"], max(today, start), until, stamp)

    errors, waiting = [], Waiting()
    saver = Saver(client, storage, settings, stamp)
    documents, documents_error = {}, None
    if "documents" in settings:
        try:
            documents = read_documents(client, store, settings, previous, saver, since, stamp, errors, waiting)
        except FetchError as e:
            if isinstance(e, Blocked) or e.status in REFUSED:
                # The meetings the calendar listed are kept; the run stops.
                save_store(data_dir, store, config)
                raise
            # Listed again from the same time on the next run.
            errors.append(str(e))
            documents_error = str(e)
            documents = {k: previous[k] for k in ("documents_listed_at", "to_read", "unmatched") if k in previous}

    save_store(data_dir, store, config)
    unlisted = dict(sorted(found["unlisted"].items()))
    status = {
        "updated_at": stamp,
        "events_listed_at": stamp,
        "events_listed": len(events),
        "meetings_listed": len(found["listed"]),
        "meetings_created": found["created"],
        # Why the documents weren't read this run, if they weren't: then none are counted as listed.
        "documents_error": documents_error,
        "documents_listed": documents.get("documents_listed", 0),
        "documents_without_event": documents.get("documents_without_event", 0),
        "documents_added": saver.added,
        "documents_changed": saver.changed,
        **waiting.status("documents"),
        "requests": getattr(client, "request_count", None),
        # The calendar's categories [wordpress_meetings.categories] doesn't list, with their events this run.
        "unlisted_categories": unlisted,
        "new_unlisted_categories": sorted(set(unlisted) - set(previous.get("unlisted_categories", {}))),
        # Names filed under their category's board without naming it: [wordpress_meetings.bodies] can name them.
        "filed_by_category": dict(sorted(found["by_category"].items())),
        "errors": errors,
        "documents_listed_at": documents.get("documents_listed_at"),
        # Documents whose meeting isn't recorded, by post id: read again once it is.
        "unmatched": documents.get("unmatched", {}),
        "to_read": documents.get("to_read", {}),
    }
    save_json(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "wordpress_meetings"):
        return 0
    settings = config["wordpress_meetings"]
    client = PoliteClient(config["site"]["user_agent"], delay=settings.get("request_delay", 2.0), limiter=HostLimiter())
    client.session.hooks["response"].append(stop_at_browser_check)
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        refused = isinstance(e, Blocked) or e.status in REFUSED
        print(f"::error::The city website's meetings could not be read: {e}"
              + (". The site refused or checked the browser; Publick doesn't work around that." if refused else ""))
        return 1
    print(json.dumps({k: v for k, v in status.items() if k not in ("to_read", "unmatched")}, indent=2))
    for name in status["new_unlisted_categories"]:
        print(f"::warning::An event category not in [wordpress_meetings.categories]: {name!r}")
    listed, unlinked = status["documents_listed"], status["documents_without_event"]
    if listed >= 4 and unlinked * 2 > listed:
        print(f"::warning::{unlinked} of the {listed} documents listed link no event: has the site changed its "
              f"document fields ({wpdocs.EVENT_FIELD}, {wpdocs.SNAPSHOT_FIELD})?")
    if status["documents_error"]:
        print("::warning::The document library wasn't read this run (listed again from the same time next run): "
              f"{status['documents_error']}")
    for error in status["errors"]:
        if error != status["documents_error"]:
            print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
