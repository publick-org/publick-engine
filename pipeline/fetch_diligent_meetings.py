"""Collect a board's meetings, agendas and minutes from its Diligent Community portal.

Burlington School District's School Board and Portland Public Schools' Board
of Public Education post their meetings on Diligent Community portals (see
pipeline/diligent.py). Meetings are recorded alongside the town's under
data/meetings/, as the district's own: the boards are named in
[diligent_meetings] bodies and body.

Each run reads the portal's list of meetings from `since` to days_ahead
(default 90) days ahead, once. A meeting's documents are read when it is new,
upcoming, or recent (recheck_days, default 60) and published: an agenda can be
revised up to the meeting, and minutes are posted as a draft and adopted
later. The agenda is saved as a PDF of the agenda alone, the minutes as the
portal's PDF. A document is downloaded again only when the portal marks it
modified, and saved as a new version, with the change in the meeting's
history, only when its content changed. Newest meetings first, until a run
has saved max_documents_per_run (default 60): the first run of a long year
reads the rest on later runs. A cancelled meeting's documents aren't read.
An upcoming meeting that leaves the portal is marked as no longer listed.

Meetings with no board are listed in the run's summary, not guessed at.

Usage:
    python -m pipeline.fetch_diligent_meetings [--town burlington]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import diligent
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import load_store, meetings_dir, merge, pdf_has_text, record_change, save_json, save_store, slugify, unique_slug
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient

STATUS_FILE = "diligent_status.json"
POSTED_ON = "Diligent Community"


def new_meeting(store: dict, found: dict, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": f"{diligent.SOURCE}-{found['id']}",
        "source": diligent.SOURCE,
        "source_url": diligent.meeting_page(settings, found["id"]),
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


def wants_documents(meeting: dict, today: str, recent: str) -> bool:
    """Whether to read a meeting's documents this run: never read yet, or still able to change
    (an upcoming meeting's agenda; a recent meeting's minutes, draft then adopted)."""
    if meeting["status"] == "cancelled" or not meeting.get("published"):
        return False
    return "documents_checked" not in meeting or meeting["date"] >= recent or meeting["date"] >= today


def original_filename(disposition: str) -> str | None:
    found = re.search(r'filename="?([^";]+)', disposition or "")
    return found.group(1).strip() if found else None


def save_document(client, meeting: dict, kind: str, picked: dict, storage, settings: dict, stamp: str,
                  counts: dict) -> None:
    """Save the agenda or minutes the portal lists if it's new or changed, adding to `counts`."""
    field = "agendas" if kind == "agenda" else "minutes"
    docs = meeting.get(field, [])
    latest = docs[-1] if docs else None
    if latest and latest["source_url"] == picked["url"] and latest.get("last_modified") == picked["last_modified"]:
        return
    response = client.get(picked["url"])
    content = response.content
    if not content.startswith(b"%PDF"):
        raise FetchError(f"{picked['url']}: not a PDF")
    sha = hashlib.sha256(content).hexdigest()
    if latest and latest["sha256"] == sha:
        # Marked modified with the same content: remember the mark, so it isn't downloaded again.
        latest["last_modified"] = picked["last_modified"]
        return
    doc_id = f"{diligent.SOURCE}-{picked['id']}-{sha[:8]}"
    storage.put(field, f"{doc_id}.pdf", content)
    doc = {
        "id": doc_id,
        "source_url": picked["url"],
        "posted_by": settings["source_name"],
        "posted_on": POSTED_ON,
        "file": f"{doc_id}.pdf",
        "original_filename": original_filename(response.headers.get("content-disposition", "")),
        "sha256": sha,
        "bytes": len(content),
        "has_text": pdf_has_text(content),
        "last_modified": picked["last_modified"],
        "fetched_at": stamp,
    }
    if latest:
        record_change(meeting, kind, latest["id"], doc_id, stamp)
        counts["changed" if latest["source_url"] == picked["url"] else "added"] += 1
    else:
        counts["added"] += 1
    meeting.setdefault(field, []).append(doc)


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["diligent_meetings"]
    since = str(settings["since"])
    recent = (now.date() - timedelta(days=settings.get("recheck_days", 60))).isoformat()
    until = (now.date() + timedelta(days=settings.get("days_ahead", 90))).isoformat()
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)

    try:
        entries = json.loads(client.get(diligent.listing_url(settings, since, until)).text)
    except ValueError as e:
        raise FetchError(f"{settings['page_url']}: the meetings list isn't JSON ({e})") from None
    if not isinstance(entries, list):
        raise FetchError(f"{settings['page_url']}: the meetings list isn't a list")
    found, unrecognized = diligent.parse_listing(entries, settings)

    created, listed = 0, set()
    for item in found:
        meeting = store.get(f"{diligent.SOURCE}-{item['id']}")
        if meeting is None:
            meeting = new_meeting(store, item, settings, stamp)
            created += 1
        fields = {k: item[k] for k in ("date", "body", "status", "special", "published", "location", "remote_url")}
        if item["start_time"]:
            fields["start_time"] = item["start_time"]
        merge(meeting, {**fields, "title": title_for(item["body"], item["special"]), "posted_title": item["name"],
                        "listed": True}, stamp, track=True)
        meeting["last_seen"] = stamp
        listed.add(meeting["id"])

    errors, counts = [], {"added": 0, "changed": 0, "checked": 0}
    limit, waiting = settings.get("max_documents_per_run", 60), 0
    for meeting in sorted(store.values(), key=lambda m: (m["date"], m["id"]), reverse=True):
        if meeting.get("source") != diligent.SOURCE or meeting["date"] < since:
            continue
        # An upcoming meeting no longer listed was most likely called off or moved.
        if meeting["id"] not in listed and meeting["date"] >= today and meeting.get("listed", True):
            merge(meeting, {"listed": False}, stamp, track=True)
        if meeting["id"] not in listed or not wants_documents(meeting, today, recent):
            continue
        if counts["added"] + counts["changed"] >= limit:
            waiting += 1
            continue
        try:
            listing = json.loads(client.get(diligent.documents_url(settings, meeting["id"].split("-", 1)[1])).text)
            picked = diligent.pick_documents(listing.get("Documents") or [], settings)
        except (FetchError, ValueError, AttributeError) as e:
            errors.append(f"{meeting['source_url']}: {e}")
            continue
        counts["checked"] += 1
        failed = False
        for kind in ("agenda", "minutes"):
            # A board that posts its minutes elsewhere (minutes = false): none are read here.
            if kind in picked and (kind == "agenda" or settings.get("minutes", True)):
                try:
                    save_document(client, meeting, kind, picked[kind], storage, settings, stamp, counts)
                except FetchError as e:
                    errors.append(str(e))
                    failed = True
        if not failed:
            meeting["documents_checked"] = stamp

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
        # Meetings with no board: add their name or type to [diligent_meetings.bodies] if they're a board's.
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
    if not configured(config, "diligent_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["diligent_meetings"].get("request_delay", 2.0))
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::The Diligent Community portal could not be read: {e}")
        return 1
    print(json.dumps(status, indent=2))
    for name in status["new_unrecognized"]:
        print(f"::warning::No listed board for {name!r}")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
