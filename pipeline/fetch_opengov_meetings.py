"""Collect public bodies' meetings, agendas and minutes from Rhode Island's Open Meetings portal.

Any Rhode Island public body files its notices, agendas and minutes with the
Secretary of State (see pipeline/opengov.py); South Kingstown's School
Committee is read this way. Meetings are recorded alongside the town's under
data/meetings/: [opengov_meetings] bodies maps each public body, named exactly
as the portal has it, to the site's board.

Each run reads each body's upcoming, completed and cancelled feeds once. A
meeting from `since` on is recorded from its feed item (date, time,
location), and its page is read when it's new, upcoming, or recent
(recheck_days, default 90: minutes are filed once approved, often weeks
later) without minutes. Every agenda filed for it (an amended agenda after
the first) and its minutes are saved as the portal serves them, oldest filed
first, so the latest is the meeting's; a file once saved isn't downloaded
again. At most max_documents_per_run (default 60) are saved a run, newest
meetings first; a cancelled meeting's aren't saved. A page that's an annual calendar or a public announcement
isn't a meeting: it's left out, and listed in the run's summary. An upcoming
meeting that leaves the feeds is marked as no longer listed.

Usage:
    python -m pipeline.fetch_opengov_meetings [--town south-kingstown]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import opengov
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import load_store, meetings_dir, merge, pdf_has_text, record_change, save_json, slugify, unique_slug
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient

STATUS_FILE = "opengov_status.json"
POSTED_ON = "the Open Meetings portal"


def new_meeting(store: dict, item: dict, body: str, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": f"{opengov.SOURCE}-{item['id']}",
        "source": opengov.SOURCE,
        "source_url": opengov.meeting_url(item["id"]),
        "source_name": settings["source_name"],
        "first_seen": stamp,
        "slug": unique_slug(f"{item['date']}-{slugify(body)}", store),
        "date": item["date"],
        "body": body,
        # As first listed, so the first run's merge records no change.
        "start_time": item["start_time"],
        "end_time": None,
        "location": item["location"],
        "status": item["status"],
    }
    store[meeting["id"]] = meeting
    return meeting


def wants_page(meeting: dict, today: str, recent: str) -> bool:
    """Whether to read a meeting's page this run: never read, upcoming (an agenda can be amended),
    or recent and still without minutes."""
    if "page_checked" not in meeting:
        return True
    if meeting["status"] == "cancelled":
        return False
    return meeting["date"] >= today or (meeting["date"] >= recent and not meeting.get("minutes"))


def save_documents(client, meeting: dict, page: dict, storage, settings: dict, stamp: str, counts: dict,
                   room: int) -> bool:
    """Save the agendas and minutes on a meeting's page not saved yet, oldest filed first, until
    the run has saved `room`. Returns whether every one listed is saved."""
    for field, kind in (("agendas", "agenda"), ("minutes", "minutes")):
        docs = meeting.get(field, [])
        saved = {d.get("portal_path") for d in docs}
        for listed in page[field]:
            if listed["path"] in saved:
                continue
            if counts["added"] >= room:
                return False
            url = opengov.file_url(listed["path"])
            response = client.get(url)
            content = response.content
            if not content.startswith(b"%PDF"):
                raise FetchError(f"{url}: not a PDF")
            sha = hashlib.sha256(content).hexdigest()
            doc_id = f"{opengov.SOURCE}-{listed['number'] or sha[:12]}-{sha[:8]}"
            storage.put(field, f"{doc_id}.pdf", content)
            doc = {
                "id": doc_id,
                "title": listed["label"],
                "source_url": url,
                "portal_path": listed["path"],
                "posted_by": settings["source_name"],
                "posted_on": POSTED_ON,
                "file": f"{doc_id}.pdf",
                "original_filename": listed["path"].rsplit("\\", 1)[-1],
                "sha256": sha,
                "bytes": len(content),
                "has_text": pdf_has_text(content),
                "filed_at": listed["filed"] or None,
                "fetched_at": stamp,
            }
            if docs:
                record_change(meeting, kind, docs[-1]["id"], doc_id, stamp)
            docs.append(doc)
            meeting[field] = docs
            counts["added"] += 1
    return True


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["opengov_meetings"]
    since = str(settings["since"])
    recent = (now.date() - timedelta(days=settings.get("recheck_days", 90))).isoformat()
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)
    status_path = meetings_dir(data_dir) / STATUS_FILE
    previous = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    # Pages found not to be meetings (an annual calendar, an announcement): not read again.
    not_meetings = {int(k): v for k, v in previous.get("not_meetings", {}).items()}

    errors, found = [], {}
    for portal_body, board in settings["bodies"].items():
        for feed in ("completed", "upcoming", "cancelled"):
            url = opengov.feed_url(feed, portal_body)
            try:
                items = opengov.parse_feed(client.get(url).text)
            except ValueError as e:
                raise FetchError(f"{url}: {e}") from None
            for item in items:
                if item["date"] < since or item["id"] in not_meetings:
                    continue
                known = found.get(item["id"])
                found[item["id"]] = {**(known or item), "board": board, "portal_body": portal_body,
                                     "status": "cancelled" if feed == "cancelled" else (known or {}).get("status", "scheduled")}

    created, listed = 0, set()
    for item in found.values():
        meeting = store.get(f"{opengov.SOURCE}-{item['id']}")
        if meeting is None:
            meeting = new_meeting(store, item, item["board"], settings, stamp)
            created += 1
        # A meeting its page marks cancelled stays so, whichever feed lists it.
        status = "cancelled" if meeting.get("cancelled_on_page") else item["status"]
        merge(meeting, {"date": item["date"], "start_time": item["start_time"], "location": item["location"],
                        "body": item["board"], "title": title_for(item["board"], False), "status": status,
                        "listed": True}, stamp, track=True)
        meeting["last_seen"] = stamp
        listed.add(meeting["id"])

    counts, waiting, checked = {"added": 0}, 0, 0
    limit = settings.get("max_documents_per_run", 60)
    for meeting in sorted(store.values(), key=lambda m: (m["date"], m["id"]), reverse=True):
        if meeting.get("source") != opengov.SOURCE or meeting["date"] < since:
            continue
        # An upcoming meeting no longer in any feed was most likely withdrawn or moved.
        if meeting["id"] not in listed:
            if meeting["date"] >= today and meeting.get("listed", True):
                merge(meeting, {"listed": False}, stamp, track=True)
            continue
        if not wants_page(meeting, today, recent):
            continue
        if counts["added"] >= limit:
            waiting += 1
            continue
        meeting_id = int(meeting["id"].split("-", 1)[1])
        try:
            page = opengov.parse_meeting(client.get(opengov.meeting_url(meeting_id)).text)
        except FetchError as e:
            errors.append(str(e))
            continue
        checked += 1
        if page["annual_calendar"] or page["announcement"]:
            # Not a meeting: the year's calendar of regular meetings, or an announcement.
            not_meetings[meeting_id] = "annual calendar" if page["annual_calendar"] else "public announcement"
            del store[meeting["id"]]
            continue
        if page["cancelled"]:
            meeting["cancelled_on_page"] = True
            merge(meeting, {"status": "cancelled"}, stamp, track=True)
        if meeting["status"] == "cancelled":
            # As for the other sources, a cancelled meeting's notice isn't saved.
            meeting["page_checked"] = stamp
            continue
        try:
            complete = save_documents(client, meeting, page, storage, settings, stamp, counts, limit)
        except FetchError as e:
            errors.append(str(e))
            continue
        if complete:
            meeting["page_checked"] = stamp
        else:
            waiting += 1

    save_json(meetings_dir(data_dir) / "meetings.json", store)
    status = {
        "updated_at": stamp,
        "meetings_listed": len(found),
        "meetings_created": created,
        "pages_read": checked,
        "documents_added": counts["added"],
        "meetings_waiting": waiting,
        "requests": getattr(client, "request_count", None),
        "not_meetings": {str(k): v for k, v in sorted(not_meetings.items())},
        "errors": errors,
    }
    save_json(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "opengov_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["opengov_meetings"].get("request_delay", 2.0),
                          timeout=90)
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::Rhode Island's Open Meetings portal could not be read: {e}")
        return 1
    print(json.dumps({k: v for k, v in status.items() if k != "not_meetings"}, indent=2))
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
