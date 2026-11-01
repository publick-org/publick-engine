"""Collect a board's meetings, agendas and minutes from its organization's page on BoardBook Premier.

Bristol Public Schools posts its Board of Education's meetings and those of
its committees on BoardBook Premier (see pipeline/boardbook.py). Meetings are
recorded alongside the town's under data/meetings/, as the district's own:
the boards are named in [boardbook_meetings] bodies and body.

Each run reads the organization's page once: every meeting it has posted,
of which those from `since` on are kept, with their date, time, place, online
link and recording. A meeting's agenda page is read when the meeting is new
or upcoming, or its agenda hasn't been saved yet: the agenda can be revised up
to the meeting. The agenda package is downloaded only when the page's items
or attached files changed since the agenda last saved, and the agenda alone
(the package's pages before its attachments) is saved as a new version only
when its content changed. Minutes, where the district posts them on BoardBook
(minutes = false where it doesn't), are read while the meeting is recent
(recheck_days, default 60), a draft then the adopted minutes, and saved as a
new version when their content changes. Newest meetings first, until a run
has saved max_documents_per_run (default 60). A cancelled meeting's documents
aren't read. An upcoming meeting that leaves the page is marked as no longer
listed.

Meetings with no board are listed in the run's summary, not guessed at.

Usage:
    python -m pipeline.fetch_boardbook_meetings [--town bristol]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import boardbook
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import load_store, meetings_dir, merge, pdf_has_text, record_change, save_json, save_store, slugify, unique_slug
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient

STATUS_FILE = "boardbook_status.json"
POSTED_ON = "BoardBook"
# The fields of a listing kept on the meeting's record, changes to them recorded.
FIELDS = ("date", "body", "status", "special", "location", "remote_url")


def new_meeting(store: dict, found: dict, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": f"{boardbook.SOURCE}-{found['id']}",
        "source": boardbook.SOURCE,
        "source_url": found["agenda_page"] or settings["page_url"],
        "source_name": settings["source_name"],
        "first_seen": stamp,
        "slug": unique_slug(f"{found['date']}-{slugify(found['body'])}", store),
        "date": found["date"],
        "body": found["body"],
        # As first listed, so the first run's merge records no change.
        "start_time": found["start_time"],
        "end_time": None,
        "location": found["location"],
        "remote_url": found["remote_url"],
        "status": found["status"],
    }
    store[meeting["id"]] = meeting
    return meeting


def save(meeting: dict, kind: str, content: bytes, source_url: str, storage, settings: dict, stamp: str,
         counts: dict, extra: dict) -> bool:
    """Save an agenda or minutes PDF unless it's the meeting's latest already; True if saved."""
    field = "agendas" if kind == "agenda" else "minutes"
    if not content.startswith(b"%PDF"):
        raise FetchError(f"{source_url}: the {kind} isn't a PDF")
    docs = meeting.get(field, [])
    latest = docs[-1] if docs else None
    sha = hashlib.sha256(content).hexdigest()
    if latest and latest["sha256"] == sha:
        latest.update(extra)
        return False
    doc_id = f"{boardbook.SOURCE}-{meeting['id'].split('-', 1)[1]}-{kind}-{sha[:8]}"
    storage.put(field, f"{doc_id}.pdf", content)
    doc = {
        "id": doc_id,
        "source_url": source_url,
        "posted_by": settings["source_name"],
        "posted_on": POSTED_ON,
        "file": f"{doc_id}.pdf",
        "sha256": sha,
        "bytes": len(content),
        "has_text": pdf_has_text(content),
        "fetched_at": stamp,
        **extra,
    }
    if latest:
        record_change(meeting, kind, latest["id"], doc_id, stamp)
        counts["changed"] += 1
    else:
        counts["added"] += 1
    meeting.setdefault(field, []).append(doc)
    return True


def update_agenda(client, meeting: dict, item: dict, storage, settings: dict, stamp: str, counts: dict) -> None:
    """Read the agenda's page, and download its package if the agenda changed since it was saved."""
    page = client.get(item["agenda_page"]).text
    fingerprint = boardbook.agenda_fingerprint(page)
    latest = (meeting.get("agendas") or [None])[-1]
    if latest and fingerprint and latest.get("page_fingerprint") == fingerprint:
        return
    package_url = boardbook.download_url(page, item["agenda_page"])
    if not package_url:
        raise FetchError(f"{item['agenda_page']}: the agenda page has no Download link")
    package = client.get(package_url).content
    if not package.startswith(b"%PDF"):
        raise FetchError(f"{package_url}: the agenda package isn't a PDF")
    content, pages, total = boardbook.agenda_alone(package)
    save(meeting, "agenda", content, item["agenda_page"], storage, settings, stamp, counts,
         {"package_url": package_url, "pages": pages, "package_pages": total, "page_fingerprint": fingerprint})


def update_minutes(client, meeting: dict, item: dict, storage, settings: dict, stamp: str, counts: dict) -> None:
    """Read the minutes viewer for the PDF it shows, and save it if it's new or changed."""
    viewer = client.get(item["minutes_page"])
    url = boardbook.snapshot_url(viewer.text, getattr(viewer, "url", None) or item["minutes_page"])
    if not url:
        raise FetchError(f"{item['minutes_page']}: the minutes page shows no PDF")
    save(meeting, "minutes", client.get(url).content, item["minutes_page"], storage, settings, stamp, counts,
         {"download_url": url})


def wants_agenda(meeting: dict, item: dict, today: str) -> bool:
    return bool(item["agenda_page"]) and (not meeting.get("agendas") or meeting["date"] >= today)


def wants_minutes(meeting: dict, item: dict, settings: dict, today: str, recent: str) -> bool:
    if not item["minutes_page"] or not settings.get("minutes", True) or meeting["date"] > today:
        return False
    return not meeting.get("minutes") or meeting["date"] >= recent


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["boardbook_meetings"]
    since = str(settings["since"])
    recent = (now.date() - timedelta(days=settings.get("recheck_days", 60))).isoformat()
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)

    page = client.get(settings["page_url"]).text
    found, unrecognized = boardbook.parse_listing(page, settings)
    if not found and boardbook.TABLE.search(page) is None:
        raise FetchError(f"{settings['page_url']}: no table of meetings on the page")

    created, listed, items = 0, set(), {}
    for item in found:
        meeting = store.get(f"{boardbook.SOURCE}-{item['id']}")
        if meeting is None:
            meeting = new_meeting(store, item, settings, stamp)
            created += 1
        fields = {k: item[k] for k in FIELDS}
        if item["start_time"]:
            fields["start_time"] = item["start_time"]
        if item["agenda_page"]:
            fields["source_url"] = item["agenda_page"]
        for key in ("video_id", "video_start"):
            if key in item:
                fields[key] = item[key]
        merge(meeting, {**fields, "title": title_for(item["body"], item["special"]), "posted_title": item["name"],
                        "meeting_type": item["type"], "links": item["links"], "listed": True}, stamp, track=True)
        meeting["last_seen"] = stamp
        listed.add(meeting["id"])
        items[meeting["id"]] = item

    errors, counts = [], {"added": 0, "changed": 0, "checked": 0}
    limit, waiting = settings.get("max_documents_per_run", 60), 0
    for meeting in sorted(store.values(), key=lambda m: (m["date"], m["id"]), reverse=True):
        if meeting.get("source") != boardbook.SOURCE or meeting["date"] < since:
            continue
        # An upcoming meeting no longer listed was most likely called off or moved.
        if meeting["id"] not in listed and meeting["date"] >= today and meeting.get("listed", True):
            merge(meeting, {"listed": False}, stamp, track=True)
        item = items.get(meeting["id"])
        if item is None or meeting["status"] == "cancelled":
            continue
        wanted = [(kind, update) for kind, wants, update in (
            ("agenda", wants_agenda(meeting, item, today), update_agenda),
            ("minutes", wants_minutes(meeting, item, settings, today, recent), update_minutes)) if wants]
        if not wanted:
            continue
        if counts["added"] + counts["changed"] >= limit:
            waiting += 1
            continue
        counts["checked"] += 1
        for kind, update in wanted:
            try:
                update(client, meeting, item, storage, settings, stamp, counts)
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
        "meetings_checked": counts["checked"],
        "meetings_waiting": waiting,
        "requests": getattr(client, "request_count", None),
        # Meetings with no board: add their name, or words in it, to [boardbook_meetings.bodies] if they're a board's.
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
    if not configured(config, "boardbook_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["boardbook_meetings"].get("request_delay", 3.0))
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::The district's BoardBook page could not be read: {e}")
        return 1
    print(json.dumps(status, indent=2))
    for name in status["new_unrecognized"]:
        print(f"::warning::No listed board for {name!r}")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
