"""Collect a board's meetings, agendas and minutes from a city's listing of its events' documents.

Somerville's School Committee posts its agendas, minutes and recordings on the city's
"Agendas, Minutes, & More" page (see pipeline/meetingdocs.py). Meetings are recorded
alongside the town's under data/meetings/, as posted by [meetingdocs_meetings] source_name:
the boards are named in its bodies and body.

Each run reads the listing from `since` on, a page at a time, newest first, until a page
reaches back past `since` or is the last (at most max_pages, default 10), and keeps meetings
to days_ahead (default 90) days ahead. A meeting's agenda and minutes are saved when first
posted, and again, as a new version with the change in the meeting's history, when a newer
one is posted (a revised agenda, the minutes once adopted) and its content differs. Minutes
posted as a Word file are saved as a PDF of their text. A cancelled meeting's documents
aren't saved. Newest meetings first, until a run has saved max_documents_per_run (default
60). An upcoming meeting that leaves the listing is marked as no longer listed.

Meetings with no board are listed in the run's summary, not guessed at.

Usage:
    python -m pipeline.fetch_meetingdocs_meetings [--town somerville]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import meetingdocs, wordfile
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import load_store, meetings_dir, merge, pdf_has_text, record_change, save_json, save_store, slugify, unique_slug
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient

STATUS_FILE = "meetingdocs_status.json"


def new_meeting(store: dict, found: dict, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": f"{meetingdocs.SOURCE}-{found['id']}",
        "source": meetingdocs.SOURCE,
        "source_url": found["url"],
        "source_name": settings["source_name"],
        "first_seen": stamp,
        "slug": unique_slug(f"{found['date']}-{slugify(found['body'])}", store),
        "date": found["date"],
        "body": found["body"],
        # As first listed, so the first run's merge records no change.
        "start_time": found["start_time"],
        "end_time": None,
        "status": found["status"],
    }
    store[meeting["id"]] = meeting
    return meeting


def save_document(client, meeting: dict, kind: str, picked: dict, storage, settings: dict, stamp: str,
                  counts: dict) -> None:
    """Save the agenda or minutes the listing names if it's new or newer, adding to `counts`."""
    field = "agendas" if kind == "agenda" else "minutes"
    docs = meeting.get(field, [])
    latest = docs[-1] if docs else None
    if latest and latest["source_url"] == picked["url"] and latest.get("posted") == picked["posted"]:
        return
    response = client.get(picked["url"])
    content, converted = response.content, None
    if wordfile.is_docx(content):
        # Saved as a PDF of its text (pipeline/wordfile.py); the page links the city's own file too.
        content, converted = wordfile.to_pdf(content), "docx"
    if not content.startswith(b"%PDF"):
        raise FetchError(f"{picked['url']}: not a PDF or a Word (.docx) file")
    sha = hashlib.sha256(content).hexdigest()
    if latest and latest["sha256"] == sha:
        # Posted again with the same content: remember it, so it isn't downloaded again.
        latest.update(source_url=picked["url"], posted=picked["posted"])
        return
    doc_id = f"{meetingdocs.SOURCE}-{meetingdocs.posted_stamp(picked['posted']) or 'undated'}-{sha[:8]}"
    storage.put(field, f"{doc_id}.pdf", content)
    doc = {
        "id": doc_id,
        "title": picked["label"] or picked["file"],
        "source_url": picked["url"],
        "file": f"{doc_id}.pdf",
        "original_filename": picked["file"] or None,
        "sha256": sha,
        "bytes": len(content),
        "has_text": pdf_has_text(content),
        "posted": picked["posted"],
        "fetched_at": stamp,
        **({"converted_from": converted} if converted else {}),
    }
    if latest:
        record_change(meeting, kind, latest["id"], doc_id, stamp)
        counts["changed"] += 1
    else:
        counts["added"] += 1
    meeting.setdefault(field, []).append(doc)


def read_listing(client, settings: dict, since: str) -> list[dict]:
    """Every event the listing has from `since` on, newest first."""
    events = []
    for page in range(settings.get("max_pages", 10)):
        text = client.get(meetingdocs.listing_url(settings, since, page)).text
        found = meetingdocs.parse_listing(text, settings["page_url"])
        events += found
        if not found or not meetingdocs.has_next(text) or min(e["date"] for e in found) < since:
            break
    return events


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["meetingdocs_meetings"]
    since = str(settings["since"])
    until = (now.date() + timedelta(days=settings.get("days_ahead", 90))).isoformat()
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)

    events = [e for e in read_listing(client, settings, since) if since <= e["date"] <= until]
    # One event can be on two pages as the listing moves between requests.
    events = list({e["id"]: e for e in events}.values())
    found, unrecognized = meetingdocs.to_meetings(events, settings)

    created, listed = 0, {}
    for item in found:
        meeting = store.get(f"{meetingdocs.SOURCE}-{item['id']}")
        if meeting is None:
            meeting = new_meeting(store, item, settings, stamp)
            created += 1
        fields = {k: item[k] for k in ("date", "body", "status", "special")}
        if item["start_time"]:
            fields["start_time"] = item["start_time"]
        fields.update(item.get("video") or {})
        merge(meeting, {**fields, "title": title_for(item["body"], item["special"]), "posted_title": item["title"],
                        "documents_url": item["url"], "listed": True}, stamp, track=True)
        meeting["last_seen"] = stamp
        listed[meeting["id"]] = item

    errors, counts = [], {"added": 0, "changed": 0}
    limit, waiting = settings.get("max_documents_per_run", 60), 0
    for meeting in sorted(store.values(), key=lambda m: (m["date"], m["id"]), reverse=True):
        if meeting.get("source") != meetingdocs.SOURCE or meeting["date"] < since:
            continue
        # An upcoming meeting no longer listed was most likely called off or moved.
        if meeting["id"] not in listed:
            if meeting["date"] >= today and meeting.get("listed", True):
                merge(meeting, {"listed": False}, stamp, track=True)
            continue
        item = listed[meeting["id"]]
        if item["status"] == "cancelled":
            continue
        for kind in ("agenda", "minutes"):
            if kind not in item:
                continue
            if counts["added"] + counts["changed"] >= limit:
                waiting += 1
                continue
            try:
                save_document(client, meeting, kind, item[kind], storage, settings, stamp, counts)
            except FetchError as e:
                errors.append(str(e))

    save_store(data_dir, store, config)
    status_path = meetings_dir(data_dir) / STATUS_FILE
    reported = json.loads(status_path.read_text(encoding="utf-8")).get("unrecognized", []) if status_path.exists() else []
    status = {
        "updated_at": stamp,
        "meetings_listed": len(found),
        "meetings_created": created,
        "documents_added": counts["added"],
        "documents_changed": counts["changed"],
        "documents_waiting": waiting,
        "requests": getattr(client, "request_count", None),
        # Titles with no board: add them, or words in them, to [meetingdocs_meetings.bodies] if they're a board's.
        "unrecognized": sorted(set(unrecognized)),
        "new_unrecognized": sorted(set(unrecognized) - set(reported)),
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
    if not configured(config, "meetingdocs_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["meetingdocs_meetings"].get("request_delay", 2.0))
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::The city's listing of meeting documents could not be read: {e}")
        return 1
    print(json.dumps(status, indent=2))
    for name in status["new_unrecognized"]:
        print(f"::warning::No listed board for {name!r}")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
