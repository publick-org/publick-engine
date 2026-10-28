"""Collect a town's 311 requests from its Socrata open data portal ([socrata_311]).

Each run reads the rows the portal changed since the last run: every request
created since [socrata_311] since, of the classes the town keeps (Somerville
keeps service requests, not calls for information or feedback), whose
:updated_at is at or after the cursor, in order of :updated_at and then the
dataset's id. Each page starts after the last row read (keyset paging), not at
an offset, so a row the portal rewrites while the run reads is read again at
the end instead of moving another out of the page it was on. The cursor is the
last row read; a run that stops at [socrata_311] max_pages_per_run (a new
town's history is most of a year's requests) goes on from there next run.

When since moves earlier, the months between are read once by the dataset's id
(backfill), within the same page cap.

A request is open or closed by its most recent status; a closed request's
status date is when it was closed. The ward is the city's own, from the row:
nothing is looked up in a ward file. The portal has no address, map point or
acknowledged time, so the 311 pages leave those out (pipeline/source_311.py).
A request deleted from the portal stays as it was last read. Closed requests
older than the pages show (created before the monthly chart's first month, about
two years back, and closed over 30 days ago) are dropped, so the saved requests
stay about two years' worth however long the town has run (prune).

Writes data/311/requests.json, data/311/sync.json (the cursor and any backfill
left), and data/311/status.json, as pipeline/fetch_311.py does for SeeClickFix,
and keeps requests.json in the town's bucket the same way (pipeline/bucket_data.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pipeline import bucket_data, socrata
from pipeline.compute_311 import chart_start
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.fetch_311 import iso, load_state, load_store, save, save_json, store_dir
from pipeline.http import FetchError, HostLimiter, PoliteClient

# Rows a page. Socrata serves up to 50,000; smaller pages keep each request quick, and a stop cheap.
PAGE_SIZE = 1000
# Pages a run reads, unless [socrata_311] max_pages_per_run says otherwise. The daily sync takes a few.
MAX_PAGES = 60
# Pages between saves while a long read goes on, so a stopped run keeps most of what it read.
CHECKPOINT_PAGES = 20
# Days a closed request is kept after it closed, whenever it was created: the 311 page compares
# today's open requests with a week ago's.
KEEP_CLOSED_DAYS = 30


class Portal:
    def __init__(self, config: dict, client):
        self.settings = config["socrata_311"]
        self.client = client
        self.columns = socrata.fields(self.settings)
        self.pages = 0

    def url(self, params: dict) -> str:
        return f"https://{self.settings['domain']}/resource/{self.settings['dataset']}.json?{urlencode(params)}"

    def page(self, where: list[str], order: str) -> list[dict]:
        self.pages += 1
        select = ",".join([":updated_at", *dict.fromkeys(self.columns.values())])
        return self.client.get(self.url({"$select": select, "$where": " AND ".join(f"({w})" for w in where),
                                         "$order": order, "$limit": PAGE_SIZE})).json()

    def scope(self, since: str, before: str | None = None) -> list[str]:
        """The rows the town keeps: created since a day (and before one), of the classes it keeps."""
        created = self.columns["created"]
        where = [f"{created} >= {socrata.quote(since)}"]
        if before:
            where.append(f"{created} < {socrata.quote(before)}")
        if classes := self.settings.get("classes"):
            where.append(f"{self.columns['class']} in ({', '.join(socrata.quote(c) for c in classes)})")
        return where

    def changed(self, since: str, cursor: dict | None) -> list[dict]:
        """A page of the rows changed at or after the cursor, after its row, oldest change first."""
        where = self.scope(since)
        if cursor:
            ts, rid = socrata.quote(cursor["updated_at"]), socrata.quote(cursor["id"])
            where.append(f":updated_at > {ts} OR (:updated_at = {ts} AND {self.columns['id']} > {rid})")
        return self.page(where, f":updated_at, {self.columns['id']}")

    def created(self, start: str, end: str, after: str | None) -> list[dict]:
        """A page of the rows created in [start, end), by id, after the id given."""
        where = self.scope(start, end)
        if after:
            where.append(f"{self.columns['id']} > {socrata.quote(after)}")
        return self.page(where, self.columns["id"])


def merge(store: dict, rows: list[dict], portal: Portal, tz: ZoneInfo, stamp: str) -> tuple[int, int]:
    """Save rows as requests. Returns (new, changed): changed, a saved request whose status or its date moved."""
    closed = set(portal.settings.get("closed_statuses", socrata.CLOSED_STATUSES))
    new = changed = 0
    for row in rows:
        item = socrata.parse_row(row, portal.columns, closed, tz)
        record = store.get(item["id"])
        if record is None:
            store[item["id"]] = {**item, "first_seen": stamp, "last_seen": stamp}
            new += 1
            continue
        if (record.get("status_detail"), record.get("updated_at")) != (item["status_detail"], item["updated_at"]):
            changed += 1
        record.update(item, last_seen=stamp)
    return new, changed


def prune(store: dict, now: datetime) -> int:
    """Drop the closed requests the pages no longer show: created before the monthly chart's first
    month (compute_311.chart_start, about two years back) and closed more than KEEP_CLOSED_DAYS ago.
    Requests still open are kept however old, for the backlog. A dropped request the city changes
    again is read again, as any changed row is. Returns how many were dropped."""
    created_before = chart_start(now).replace(hour=0, minute=0, second=0, microsecond=0)
    closed_before = now - timedelta(days=KEEP_CLOSED_DAYS)
    old = [rid for rid, r in store.items() if r["status"] == "closed" and r.get("created_at") and r.get("updated_at")
           and datetime.fromisoformat(r["created_at"]) < created_before
           and datetime.fromisoformat(r["updated_at"]) < closed_before]
    for rid in old:
        del store[rid]
    return len(old)


def run(config: dict, client, data_dir: Path, now: datetime | None = None, max_pages: int | None = None) -> dict:
    settings = config["socrata_311"]
    tz = ZoneInfo(config["site"]["timezone"])
    now = now or datetime.now(tz)
    stamp = iso(now)
    started = time.monotonic()
    portal = Portal(config, client)
    cap = max_pages if max_pages is not None else settings.get("max_pages_per_run", MAX_PAGES)
    since = f"{settings['since']}-01"
    # The bucket's copy, if the town keeps one there, is the latest: what the last run saved.
    bucket_data.pull(config, data_dir, refresh=True)
    store = load_store(data_dir)
    status_path = store_dir(data_dir) / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    if not store and status.get("requests_total"):
        # Never start again from nothing, and save that over the town's history.
        raise FetchError(f"the town's {status['requests_total']} saved 311 requests are missing "
                         "(data/311/requests.json, or its bucket's copy)")
    state = load_state(data_dir) or {"cursor": None, "since": since, "backfill": None}
    if since < state["since"]:
        # since moved earlier: the months between, read once by id, with any months still to be read from an
        # earlier move (read again from the start).
        end = (state.get("backfill") or {}).get("end", state["since"])
        state = {**state, "since": since, "backfill": {"start": since, "end": end, "after": None}}

    new = changed = read = 0
    errors = []

    def checkpoint() -> None:
        if portal.pages % CHECKPOINT_PAGES == 0:
            save(config, data_dir, store, state)

    try:
        # 1. Every row changed since the cursor, until the pages run out or the cap is reached.
        while portal.pages < cap:
            rows = portal.changed(state["since"], state["cursor"])
            n, c = merge(store, rows, portal, tz, stamp)
            new, changed, read = new + n, changed + c, read + len(rows)
            if rows:
                last = rows[-1]
                state = {**state, "cursor": {"updated_at": socrata.updated_at(last), "id": str(last[portal.columns["id"]])}}
            if len(rows) < PAGE_SIZE:
                state = {**state, "caught_up": True}
                break
            state = {**state, "caught_up": False}
            checkpoint()
        # 2. A backfill, after the sync, with what's left of the cap.
        while state.get("backfill") and portal.pages < cap:
            fill = state["backfill"]
            rows = portal.created(fill["start"], fill["end"], fill["after"])
            new += merge(store, rows, portal, tz, stamp)[0]
            read += len(rows)
            state = {**state, "backfill": {**fill, "after": str(rows[-1][portal.columns["id"]])}
                     if len(rows) == PAGE_SIZE else None}
            checkpoint()
    except FetchError as e:
        # What was read is saved below, with the cursor at its last row; the step fails.
        errors.append(str(e))
    pruned = prune(store, now)
    save(config, data_dir, store, state)

    caught_up = bool(state.get("caught_up")) and not state.get("backfill") and not errors
    summary = {
        # When the requests were last read without an error (the 311 pages' "updated" time, and the freshness
        # check's): a run that fails keeps the last one's, and says when it ran in checked_at.
        "updated_at": status.get("updated_at") if errors else stamp,
        "checked_at": stamp,
        # Every change the portal made before it is saved.
        "cursor": state["cursor"],
        # When the portal last changed a row the town keeps, once every change is read: what says
        # the portal itself is still being updated.
        "portal_updated_at": state["cursor"]["updated_at"] if caught_up and state["cursor"] else status.get("portal_updated_at"),
        "caught_up": caught_up,
        "requests_total": len(store),
        "rows_read": read,
        "new": new,
        "changed": changed,
        "pruned": pruned,
        "pages": portal.pages,
        "requests_made": getattr(client, "request_count", None),
        "seconds": round(time.monotonic() - started, 1),
        "errors": errors,
    }
    save_json(status_path, summary)
    if errors:
        raise FetchError(errors[-1])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--max-pages", type=int, help=f"max pages of {PAGE_SIZE} rows this run")
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "socrata_311"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["socrata_311"].get("request_delay", 1.0),
                          limiter=HostLimiter())
    try:
        summary = run(config, client, args.data, max_pages=args.max_pages)
    except FetchError as e:
        print(f"::error::311 requests: {e}")
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
