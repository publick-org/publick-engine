"""Collect posted meeting minutes from the city's Archive Center or Agenda Center.

One request to the Archive Center's main page lists the newest documents in
every board's collection. Minutes dated on or after the configured start
date are downloaded, saved under data/meetings/minutes/ (or in the town's
bucket; see pipeline/documents.py), and attached to the
matching meeting in data/meetings/meetings.json. A meeting that was never on
the calendar feed (usually because it predates this site) gets a record
built from its minutes.

A town with an Agenda Center ([meetings.agenda_center]) or a CivicClerk portal
([meetings.civicclerk]) lists each meeting's minutes with its agenda;
pipeline.fetch_meetings records the link, and this downloads the minutes of
meetings since the table's `since` date (a town with [meetings] documents =
false downloads none).

Usage:
    python -m pipeline.fetch_minutes [--town gloucester]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import civicplus
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import (
    load_store, meetings_dir, normalize_body, pdf_has_text, record_change, save_json, slugify, unique_slug,
)
from pipeline.http import FetchError, PoliteClient


def body_for(collection_name: str, config: dict) -> str:
    body, _ = civicplus.collection_body(collection_name)
    body = normalize_body(body, config["archive"].get("aliases", {}))
    return normalize_body(body, config["meetings"].get("aliases", {}))


def find_meeting(store: dict, body: str, date: str, title: str) -> dict | None:
    """The recorded meeting of this body on this date. A subcommittee's minutes
    match a subcommittee meeting when there is one."""
    candidates = [m for m in store.values() if m["date"] == date and slugify(m["body"]) == slugify(body)]
    if len(candidates) > 1:
        sub = "subcommittee" in title.lower()
        preferred = [m for m in candidates if ("subcommittee" in m.get("title", "").lower()) == sub]
        candidates = preferred or candidates
    return candidates[0] if candidates else None


def new_meeting_from_minutes(store: dict, item: dict, body: str, stamp: str) -> dict:
    title = body + (" Subcommittee Meeting" if "subcommittee" in item["title"].lower() else "")
    meeting = {
        "id": f"archive-{item['id']}",
        "source": "archive",
        "first_seen": stamp,
        "last_seen": stamp,
        "slug": unique_slug(f"{item['date']}-{slugify(title)}", store),
        "date": item["date"],
        "body": body,
        "title": title,
        "status": "scheduled",
        "special": "special" in item["title"].lower(),
        "listed": True,
        "start_time": None,
        "end_time": None,
        "location": "",
    }
    store[meeting["id"]] = meeting
    return meeting


def save_document(client, item: dict, storage, stamp: str) -> dict:
    response = client.get(item["url"])
    content = response.content
    if not content.startswith(b"%PDF"):
        raise FetchError(f"{item['url']}: not a PDF")
    disposition = response.headers.get("content-disposition", "")
    filename = re.search(r'filename="?([^";]+)', disposition)
    storage.put("minutes", f"{item['id']}.pdf", content)
    return {
        "id": item["id"],
        "title": item["title"],
        "source_url": item["url"],
        "file": f"{item['id']}.pdf",
        "original_filename": filename.group(1).strip() if filename else None,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "has_text": pdf_has_text(content),
        "fetched_at": stamp,
    }


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    tz = ZoneInfo(config["site"]["timezone"])
    now = now or datetime.now(tz)
    stamp = now.isoformat(timespec="seconds")
    settings = config["archive"]
    base_url = config["meetings"]["base_url"]
    since = settings["minutes_since"]

    page = client.get(base_url.rstrip("/") + "/" + settings["index"].lstrip("/"))
    collections = civicplus.parse_archive_index(page.text, base_url)
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)
    known = {doc["id"] for m in store.values() for doc in m.get("minutes", [])}

    added, created, errors = 0, 0, []
    for collection in collections:
        if civicplus.collection_body(collection["name"])[1] != "minutes":
            continue
        body = body_for(collection["name"], config)
        for item in collection["items"]:
            if not item["date"] or item["date"] < since or item["id"] in known:
                continue
            try:
                doc = save_document(client, item, storage, stamp)
            except FetchError as e:
                errors.append(str(e))
                continue
            meeting = find_meeting(store, body, item["date"], item["title"])
            if meeting is None:
                meeting = new_meeting_from_minutes(store, item, body, stamp)
                created += 1
            minutes = meeting.setdefault("minutes", [])
            if minutes:
                record_change(meeting, "minutes", minutes[-1]["id"], doc["id"], stamp)
            minutes.append(doc)
            known.add(item["id"])
            added += 1

    save_json(meetings_dir(data_dir) / "meetings.json", store)
    return {"minutes_added": added, "meetings_created": created, "errors": errors}


# Meeting sources whose listings link each meeting's minutes, by the [meetings]
# table that configures them. Each table's `since` is where minutes start.
LINKED = {"agendacenter": "agenda_center", "civicclerk": "civicclerk"}


def run_linked(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    """Download the minutes that Agenda Center and CivicClerk meetings link to,
    for meetings since each source's `since`, newest first, up to
    max_minutes_per_run (default 60) per source a run. Minutes republished under
    a new number replace the old ones, and the change is recorded."""
    tz = ZoneInfo(config["site"]["timezone"])
    now = now or datetime.now(tz)
    stamp = now.isoformat(timespec="seconds")
    tables = {source: config["meetings"][table] for source, table in LINKED.items() if table in config["meetings"]}
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)

    def minutes_id(meeting):
        # Agenda Center minutes are named for the meeting's agenda number.
        return meeting.get("minutes_id") or f"agendacenter-{meeting['id'].removeprefix('agendacenter-')}"

    added, waiting_count, errors = 0, 0, []
    for source, settings in tables.items():
        waiting = sorted((m for m in store.values() if m.get("source") == source and m.get("minutes_url")
                          and m["date"] >= settings["since"]
                          and minutes_id(m) not in {d["id"] for d in m.get("minutes", [])}),
                         key=lambda m: m["date"], reverse=True)
        # The first run can find months of minutes; the rest come on later runs.
        batch = waiting[:settings.get("max_minutes_per_run", 60)]
        for meeting in batch:
            item = {"id": minutes_id(meeting), "title": meeting.get("posted_title") or meeting["title"],
                    "url": meeting["minutes_url"]}
            try:
                doc = save_document(client, item, storage, stamp)
            except FetchError as e:
                errors.append(str(e))
                continue
            minutes = meeting.setdefault("minutes", [])
            if minutes:
                record_change(meeting, "minutes", minutes[-1]["id"], doc["id"], stamp)
            minutes.append(doc)
            added += 1
        waiting_count += len(waiting) - len(batch)
    save_json(meetings_dir(data_dir) / "meetings.json", store)
    return {"minutes_added": added, "minutes_waiting": waiting_count, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    config = load_config(args.town)
    meetings = config.get("meetings", {})
    linked = meetings.get("documents", True) and any(table in meetings for table in LINKED.values())
    if not linked and not configured(config, "archive"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["meetings"].get("request_delay", 3.0))
    summaries = []
    if "archive" in config:
        try:
            summaries.append(run(config, client, args.data))
        except FetchError as e:
            print(f"::error::Archive Center could not be fetched: {e}")
            return 1
    if linked:
        summaries.append(run_linked(config, client, args.data))
    for summary in summaries:
        print(json.dumps(summary, indent=2))
        for error in summary["errors"]:
            print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
