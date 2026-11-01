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
that posts its agendas on one page and its minutes on another). A short link
(an Apptegy aptg.co link) is followed to the file it redirects to, with a HEAD
request, once: where each leads is kept in meetings/page_status.json
(short_links) and asked again only of a link not resolved before.

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

from pipeline import linkpage
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_finalsite_meetings import update_documents
from pipeline.fetch_meetings import load_store, meetings_dir, merge, save_json, save_store, slugify, unique_slug
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient

STATUS_FILE = "page_status.json"


def new_meeting(store: dict, found: dict, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": f"{linkpage.SOURCE}-{found['key']}",
        "source": linkpage.SOURCE,
        "source_url": found["page_url"],
        "source_name": settings["source_name"],
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
    known, short_links = previous.get("short_links", {}), {}

    def resolve(url: str) -> str | None:
        # A short link leads to the same file for good: each is asked once, and its answer kept.
        if url not in known:
            target = url
            try:
                for _ in range(3):   # a short link to another short link, at most
                    target = client.location(target) or target
                    if not linkpage.SHORT_LINK.match(target):
                        break
            except FetchError as e:
                errors.append(f"{url}: {e}")
                return None
            if target == url or linkpage.SHORT_LINK.match(target):
                errors.append(f"{url}: doesn't lead to a file")
                return None
            known[url] = target
        short_links[url] = known[url]
        return known[url]

    more = []
    for url in settings.get("other_pages", []):
        try:
            more.append((client.get(url).text, url))
        except FetchError as e:
            errors.append(str(e))
    for url, board in [(settings["page_url"], None), *settings.get("pages", {}).items()]:
        try:
            page = client.get(url).text
        except FetchError as e:
            if board is None:
                raise
            errors.append(str(e))
            continue
        on_page, not_read = linkpage.meetings(page, url, settings, board, more if board is None else [], resolve)
        if not on_page:
            # A page lists at least the current year's meetings: none means its layout changed.
            if board is None:
                raise FetchError(f"{url}: no meetings found")
            errors.append(f"{url}: no meetings found")
        found += [{**m, "page_url": url} for m in on_page]
        unrecognized += not_read

    created, listed = 0, set()
    for item in found:
        if item["date"] < since:
            continue
        meeting = store.get(f"{linkpage.SOURCE}-{item['key']}")
        if meeting is None:
            meeting = new_meeting(store, item, settings, stamp)
            created += 1
        fields = {k: v for k, v in item.items() if k not in ("key", "name", "start_time", "page_url")}
        fields["source_url"] = item["page_url"]
        if item["start_time"]:
            fields["start_time"] = item["start_time"]
        merge(meeting, {**fields, "title": title_for(item["body"], item["special"]), "posted_title": item["name"],
                        "listed": True}, stamp, track=True)
        meeting["last_seen"] = stamp
        listed.add(meeting["id"])

    counts = {"added": 0, "changed": 0, "checked": 0}
    limit, waiting = settings.get("max_documents_per_run", 60), 0
    for meeting in sorted(store.values(), key=lambda m: (m["date"], m["id"]), reverse=True):
        if meeting.get("source") != linkpage.SOURCE or meeting["date"] < since:
            continue
        # An upcoming meeting no longer on the page was most likely called off or moved.
        if meeting["id"] not in listed and meeting["date"] >= today and meeting.get("listed", True):
            merge(meeting, {"listed": False}, stamp, track=True)
        if counts["added"] + counts["changed"] >= limit:
            waiting += sum(1 for kind, field in (("agenda", "agendas"), ("minutes", "minutes"))
                           if meeting.get(f"{kind}_url") and not meeting.get(field)
                           and not (kind == "agenda" and meeting["status"] == "cancelled"))
            continue
        update_documents(client, meeting, storage, settings, today, recent, stamp, counts, errors)

    save_store(data_dir, store, config)
    reported = previous.get("unrecognized", [])
    status = {
        "updated_at": stamp,
        "meetings_listed": len(found),
        "meetings_created": created,
        "documents_added": counts["added"],
        "documents_changed": counts["changed"],
        "documents_rechecked": counts["checked"],
        "documents_waiting": waiting,
        "requests": getattr(client, "request_count", None),
        # Entries with a document but no date, or no board: add the board to [page_meetings.bodies] if it's one.
        "unrecognized": sorted(set(unrecognized)),
        "new_unrecognized": sorted(set(unrecognized) - set(reported)),
        "errors": errors,
        # Where each short link on the pages leads, so it isn't asked again.
        "short_links": dict(sorted(short_links.items())),
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
