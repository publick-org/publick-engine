"""Collect a board's minutes from a document library on a school district's SharpSchool website.

Springfield Public Schools posts its School Committee's minutes in a library of a folder a year
(see pipeline/sharpschool.py). Meetings are recorded alongside the town's under data/meetings/,
as posted by [sharpschool_meetings] source_name, each of the board named in `body`, so a
meeting also on the city's calendar or meeting portal is shown once (pipeline/identity.py), with
these minutes.

Each run reads the library's page for its settings, the library's top folder, and the folders
of years from `since` on. Each file of minutes is saved when first posted, and again, as a new
version with the change in the meeting's history, when the file is changed and its content
differs. Newest meetings first, until a run has saved max_documents_per_run (default 60).

Files that aren't any meeting's minutes are listed in the run's summary, not guessed at.

Usage:
    python -m pipeline.fetch_sharpschool_meetings [--town springfield]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import sharpschool
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import load_store, meetings_dir, merge, pdf_has_text, record_change, save_json, save_store, slugify, unique_slug
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient

STATUS_FILE = "sharpschool_status.json"


def meeting_id(found: dict) -> str:
    """A meeting's record, by its date and kind as the file names it, so a file posted again,
    under a new item, is a new version of the same minutes."""
    return f"{sharpschool.SOURCE}-{slugify(' '.join(filter(None, (found['date'], found['kind'], found['start_time']))))}"


def new_meeting(store: dict, found: dict, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": meeting_id(found),
        "source": sharpschool.SOURCE,
        "source_url": settings["page_url"],
        "source_name": settings["source_name"],
        "first_seen": stamp,
        "slug": unique_slug(f"{found['date']}-{slugify(settings['body'])}", store),
        "date": found["date"],
        "body": settings["body"],
        "start_time": found["start_time"],
        "end_time": None,
        "status": "scheduled",
    }
    store[meeting["id"]] = meeting
    return meeting


def read_library(client, settings: dict) -> list[dict]:
    """Every item in the library's top folder and its folders of years from `since` on."""
    page = client.get(settings["page_url"]).text
    found = sharpschool.library(page)
    if not found:
        raise FetchError(f"{settings['page_url']}: no document library on the page")
    folder, library_settings = found
    url = sharpschool.service_url(settings["page_url"])

    def folder_items(folder_id) -> list[dict]:
        try:
            answer = client.post_json(url, sharpschool.request_body(str(folder_id), library_settings)).json()
        except ValueError as e:
            raise FetchError(f"{url}: not JSON ({e})") from None
        return sharpschool.items(answer)

    top = folder_items(folder)
    every = list(top)
    for year in sharpschool.year_folders(top, str(settings["since"])):
        every += folder_items(year["ItemId"])
    return every


def save_minutes(client, meeting: dict, found: dict, storage, settings: dict, stamp: str, counts: dict) -> None:
    """Save the meeting's minutes if the file is new or changed, adding to `counts`."""
    docs = meeting.get("minutes", [])
    latest = docs[-1] if docs else None
    if latest and latest["source_url"] == found["url"] and latest.get("posted") == found["changed"]:
        return
    content = client.get(found["url"]).content
    if not content.startswith(b"%PDF"):
        raise FetchError(f"{found['url']} ({found['name']}): not a PDF")
    sha = hashlib.sha256(content).hexdigest()
    if latest and latest["sha256"] == sha:
        # Changed in the library, or posted again, with the same content: remember it, so it isn't downloaded again.
        latest.update(source_url=found["url"], posted=found["changed"])
        return
    doc_id = f"{sharpschool.SOURCE}-{sharpschool.posted_stamp(found['changed']) or 'undated'}-{sha[:8]}"
    storage.put("minutes", f"{doc_id}.pdf", content)
    doc = {
        "id": doc_id,
        "title": found["name"],
        "source_url": found["url"],
        "file": f"{doc_id}.pdf",
        "original_filename": f"{found['name']}.{found['extension'] or 'pdf'}",
        "sha256": sha,
        "bytes": len(content),
        "has_text": pdf_has_text(content),
        "posted": found["changed"],
        "posted_by": settings["source_name"],
        "posted_on": f"the {settings['source_name']} website",
        "fetched_at": stamp,
    }
    if latest:
        record_change(meeting, "minutes", latest["id"], doc_id, stamp)
        counts["changed"] += 1
    else:
        counts["added"] += 1
    meeting.setdefault("minutes", []).append(doc)


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    timezone_name = config["site"]["timezone"]
    now = now or datetime.now(ZoneInfo(timezone_name))
    stamp = now.isoformat(timespec="seconds")
    settings = config["sharpschool_meetings"]
    since = str(settings["since"])
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)

    found, unrecognized = sharpschool.files(read_library(client, settings), settings, timezone_name)
    found = [f for f in found if since <= f["date"] <= now.date().isoformat()]

    created, listed = 0, {}
    for item in found:
        meeting = store.get(meeting_id(item))
        if meeting is None:
            meeting = new_meeting(store, item, settings, stamp)
            created += 1
        fields = {"date": item["date"], "body": settings["body"], "special": item["special"]}
        if item["start_time"]:
            fields["start_time"] = item["start_time"]
        merge(meeting, {**fields, "title": title_for(settings["body"], item["special"]), "posted_title": item["kind"],
                        "listed": True}, stamp, track=True)
        meeting["last_seen"] = stamp
        listed[meeting["id"]] = item

    errors, counts = [], {"added": 0, "changed": 0}
    limit, waiting = settings.get("max_documents_per_run", 60), 0
    for meeting in sorted((store[i] for i in listed), key=lambda m: (m["date"], m["id"]), reverse=True):
        if counts["added"] + counts["changed"] >= limit:
            waiting += 1
            continue
        try:
            save_minutes(client, meeting, listed[meeting["id"]], storage, settings, stamp, counts)
        except FetchError as e:
            errors.append(str(e))

    save_store(data_dir, store, config)
    status_path = meetings_dir(data_dir) / STATUS_FILE
    reported = json.loads(status_path.read_text(encoding="utf-8")).get("unrecognized", []) if status_path.exists() else []
    status = {
        "updated_at": stamp,
        "files_listed": len(found),
        "meetings_created": created,
        "documents_added": counts["added"],
        "documents_changed": counts["changed"],
        "documents_waiting": waiting,
        "requests": getattr(client, "request_count", None),
        # Files that aren't a meeting's minutes by their names: a date first, and "Minutes".
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
    if not configured(config, "sharpschool_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["sharpschool_meetings"].get("request_delay", 2.0))
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::The district's library of minutes could not be read: {e}")
        return 1
    print(json.dumps(status, indent=2))
    for name in status["new_unrecognized"]:
        print(f"::warning::Not a meeting's minutes by its name: {name!r}")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
