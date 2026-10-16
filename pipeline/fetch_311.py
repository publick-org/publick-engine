"""Collect public 311 requests for the town from SeeClickFix.

Each run reads only what changed on SeeClickFix since the last one:

  1. Sync (all or nothing): list the requests submitted since the last sync on
     the organization's Open311 list, which with its list of services names
     any request type the town hasn't had yet; then list every request of the
     town's request types updated since the last sync (APIv2), each with its
     status and its exact acknowledged, closed, and reopened times, and save
     them. Each request is open or closed by its own status.
  2. Once a week, list the open requests (APIv2), and look up one at a time
     any saved as open that the list leaves out: closed, deleted, made
     private, or moved to another organization's request types.
  3. Read the history still to be read, a month of submissions at a time,
     within the time budget and at most [seeclickfix] catch_up_requests
     requests a run (a month started is read whole), and go on from there
     next run: a new town's history since [seeclickfix] history_since (and
     older requests still open), or, for requests saved before the sync, the
     months with requests whose exact times are missing or out of date, and
     those where APIv2 counts more or fewer requests than are saved (one
     request a month). The sync and the open list aren't counted.
  4. Look up, one at a time, the few requests the listings didn't have.
  5. Tag each request with its ward and precinct.

If SeeClickFix refuses the APIv2 listing (a 403, or a 429 after every
retry), the run goes on without it: every town on the machine leaves the
listing alone for [seeclickfix] listing_refused_hours (pipeline/http.py,
Lane), the sync falls back to Open311's list of requests submitted since the
cursor and looks each new one up by itself, steps 2 and 3 wait for the next
run, and the cursor stays where it was, so the updates since then are read
once the listing answers again. status.json says the listing was refused.
APIv2 listing requests are spaced [seeclickfix] listing_delay seconds apart,
across every process on the machine.

A listing longer than SeeClickFix serves (APIv2: 25 pages of 100) is read in
narrower windows of time, and one whose count changes while it's read is read
again, so none is ever cut short.

Where the sync has got to (its cursor: every update before it is saved), the
history still to be read, and the lookups waiting are in data/311/sync.json,
written after the requests it describes, so a run stopped at any point
leaves it behind them, never ahead. A town's first run on this code takes its
cursor from its saved requests and goes on from there.

Backfill: python -m pipeline.fetch_311 --backfill 2024-01 adds the history
since that month to what's read (with --catch-up, more of it a run).

Writes data/311/requests.json, data/311/sync.json, and data/311/status.json.
For a town with a [storage] table, requests.json is kept in its bucket, not
git (pipeline/bucket_data.py): read from there first, and saved back before
sync.json is written.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from pipeline import bucket_data, seeclickfix
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.files import write_atomic
from pipeline.geo import PrecinctLookup
from pipeline.http import FetchError, HostLimiter, Lane, PoliteClient, Refused

PAGE_SIZE = 100
# APIv2 serves no page past the 25th ("page cannot be greater than 25"): a window listing more is split.
V2_PAGES = 25
# Open311 states no limit of its own; a window that fills this many pages is split too.
OPEN311_PAGES = 60
# A 403 is a request made private, or SeeClickFix refusing us altogether. This many in a
# row is taken as the second: the step stops, and none of them is marked removed.
REFUSED_IN_A_ROW = 3
# Each sync starts this long before where the last one ended, so an update SeeClickFix lists a little
# late, or stamps by a clock a little behind ours, is still read. Reading one twice changes nothing.
OVERLAP = timedelta(hours=1)
# Before SeeClickFix's first request: where a listing with no other start begins.
EPOCH = datetime(2008, 1, 1, tzinfo=timezone.utc)
# Days between listings of every open request (step 2).
RECONCILE_DAYS = 7
# Seconds between saves while history is read, so a stopped run keeps most of what it read.
CHECKPOINT_SECONDS = 300
# Reads of a window whose count changed while it was read, before it's split into narrower ones.
LISTING_ATTEMPTS = 2
# Requests a run spends on step 3 (history: counts, and Open311 and APIv2 pages), unless
# [seeclickfix] catch_up_requests says otherwise. The sync and the weekly list aren't counted.
CATCH_UP_REQUESTS = 20
# Seconds between two APIv2 listing requests from any process on the machine ([seeclickfix] listing_delay).
LISTING_DELAY = 8.0
# Hours every process leaves the APIv2 listing alone once it's refused ([seeclickfix] listing_refused_hours).
LISTING_REFUSED_HOURS = 6
# Days the cursor may stay behind while the listing is refused before the step fails: the runs in
# between save new requests, but not what changed on older ones (a request closed), so the 311
# pages would go quietly out of date.
LISTING_STALE_DAYS = 3
OPEN = "open,acknowledged"
EVERY = "open,acknowledged,closed,archived"


def store_dir(data_dir: Path) -> Path:
    return data_dir / "311"


def load_store(data_dir: Path) -> dict:
    path = store_dir(data_dir) / "requests.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_state(data_dir: Path) -> dict | None:
    path = store_dir(data_dir) / "sync.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_json(path: Path, data) -> None:
    write_atomic(path, json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def zulu(dt: datetime) -> str:
    """A time as SeeClickFix's listings take it: UTC, to the second."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def when(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def halves(span: tuple[datetime, datetime]) -> list[tuple[datetime, datetime]] | None:
    """A span split in two at a whole second, or None for one a second long. SeeClickFix's bounds
    are from (inclusive) and before (exclusive), so the halves meet without a gap or an overlap."""
    start, end = span
    middle = (start + (end - start) / 2).replace(microsecond=0)
    return [(start, middle), (middle, end)] if start < middle < end else None


class SeeClickFix:
    def __init__(self, config: dict, client):
        self.source = config["seeclickfix"]
        self.client = client
        # Requests made through this source, to keep step 3 within its share.
        self.requests = 0
        self._codes: set[str] | None = None
        self._services: list[dict] | None = None

    def get(self, url: str):
        self.requests += 1
        return self.client.get(url)

    def base_url(self) -> str:
        return f"{self.source['open311_base'].rstrip('/')}/{self.source['organization_id']}"

    def services(self) -> list[dict]:
        """The organization's own list of services (request types), on Open311."""
        if self._services is None:
            self._services = self.get(f"{self.base_url()}/services.json").json()
        return self._services

    def service_codes(self) -> set[str] | None:
        """The request types of the departments listed in [seeclickfix] departments,
        from the organization's own list of services; None when the town takes
        every department's requests."""
        departments = self.source.get("departments")
        if not departments:
            return None
        if self._codes is None:
            self._codes = {str(s["service_code"]) for s in self.services() if s.get("organization") in departments}
            if not self._codes:
                raise FetchError(f"SeeClickFix lists no request types for {', '.join(departments)}")
        return self._codes

    def takes(self, code) -> bool:
        codes = self.service_codes()
        return codes is None or str(code) in codes

    def submitted(self, start: datetime, end: datetime, status: str | None = None) -> list[dict]:
        """The town's requests submitted in [start, end) on the organization's Open311 list
        (only those open, with status="open"), as seeclickfix.parse_open311 reads them."""
        found, windows = {}, [(start, end)]
        while windows:
            window = windows.pop()
            items = self._open311_window(*window, status)
            if items is None:
                parts = halves(window)
                if not parts:
                    raise FetchError(f"SeeClickFix's Open311 list has more than {OPEN311_PAGES * PAGE_SIZE} requests "
                                     f"submitted in the second from {zulu(window[0])}; stopped rather than read part of it")
                windows.extend(parts)
                continue
            found.update((i["id"], i) for i in items)
        return [i for i in found.values() if self.takes(i["service_code"])]

    def _open311_window(self, start: datetime, end: datetime, status: str | None) -> list[dict] | None:
        """One window's requests, or None when every page read was full: there may be more."""
        params = {"start_date": zulu(start), "end_date": zulu(end), **({"status": status} if status else {})}
        items = []
        for page in range(1, OPEN311_PAGES + 1):
            query = urlencode({"page_size": PAGE_SIZE, "page": page, **params})
            batch = self.get(f"{self.base_url()}/requests.json?{query}").json()
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                return [seeclickfix.parse_open311(i) for i in items]
        return None

    def issues(self, codes: set[str], status: str, created: tuple[datetime, datetime] | None = None,
               updated: tuple[datetime, datetime] | None = None) -> list[dict]:
        """Every APIv2 request of these request types and statuses submitted in created and last
        updated in updated ([start, end) spans, either left out for any time). A window that lists
        more than SeeClickFix serves, or whose count changed as it was read, is read again as
        narrower ones: by update time, then by submission time."""
        if not codes:
            # No request types: the listing would be every organization's.
            return []
        found, windows = {}, [(created, updated)]
        while windows:
            created, updated = windows.pop()
            issues = self._v2_window(codes, status, created, updated)
            if issues is not None:
                found.update((str(i["id"]), i) for i in issues)
                continue
            if updated and (parts := halves(updated)):
                windows.extend((created, part) for part in parts)
            elif parts := halves(created or (EPOCH, updated[1])):
                windows.extend((part, updated) for part in parts)
            else:
                raise FetchError(f"SeeClickFix lists more than {V2_PAGES * PAGE_SIZE} requests in one second, or a "
                                 "listing that changes each time it's read; stopped rather than read part of it")
        return list(found.values())

    def _v2_url(self, codes: set[str], status: str, created, updated, per_page: int, page: int) -> str:
        params = {"request_types": ",".join(sorted(codes)), "status": status, "per_page": per_page}
        if created:
            params.update(after=zulu(created[0]), before=zulu(created[1]))
        if updated:
            params.update(updated_at_after=zulu(updated[0]), updated_at_before=zulu(updated[1]))
        return f"{self.source['api_base'].rstrip('/')}/issues?{urlencode({**params, 'page': page})}"

    def count(self, codes: set[str], status: str, created: tuple[datetime, datetime]) -> int:
        """How many APIv2 requests of these request types and statuses were submitted in created."""
        if not codes:
            return 0
        data = self.get(self._v2_url(codes, status, created, None, 1, 1)).json()
        return data["metadata"]["pagination"]["entries"]

    def _v2_window(self, codes: set[str], status: str, created, updated) -> list[dict] | None:
        """One window's requests, or None when it lists more than V2_PAGES pages, or its count
        changed while it was read (a request updated, closed or removed between pages, which moves
        the ones after it to another page) in every attempt."""
        for _ in range(LISTING_ATTEMPTS):
            issues, entries = [], None
            for page in range(1, V2_PAGES + 1):
                data = self.get(self._v2_url(codes, status, created, updated, PAGE_SIZE, page)).json()
                listed = data["metadata"]["pagination"]["entries"]
                if entries is None:
                    entries = listed
                    if entries > V2_PAGES * PAGE_SIZE:
                        return None
                if listed != entries:
                    break
                issues.extend(data["issues"])
                if len(issues) >= entries or not data["issues"]:
                    break
            if len(issues) == entries == len({i["id"] for i in issues}):
                # Never past the last page served: what's returned is the whole window.
                assert entries <= V2_PAGES * PAGE_SIZE
                return issues
        return None

    def issue(self, issue_id: str) -> dict:
        url = f"{self.source['api_base'].rstrip('/')}/issues/{issue_id}"
        return seeclickfix.parse_issue(self.get(url).json())


def needs_detail(record: dict) -> bool:
    """A request saved without its exact times, or with times from before its last update: one
    saved before the sync, or that the listings didn't have."""
    detail = record.get("detail")
    if record.get("removed"):
        return False
    if not detail or not detail.get("checked_at"):
        return True
    updated = when(record.get("updated_at"))
    return bool(updated) and updated > max(t for t in (when(detail.get("updated_at")), when(detail["checked_at"])) if t)


def merge(store: dict, issues: list[dict], stamp: str, history_start: datetime) -> tuple[int, int]:
    """Save APIv2 requests. A request not saved yet is added when it was submitted since the history
    starts, or is open. Returns (new, changed)."""
    new = changed = 0
    for issue in issues:
        item = seeclickfix.parse_listed(issue)
        detail = {**seeclickfix.parse_issue(issue), "checked_at": stamp}
        record = store.get(item["id"])
        if record is None:
            if item["status"] == "open" or (item["created_at"] and when(item["created_at"]) >= history_start):
                store[item["id"]] = {**item, "first_seen": stamp, "last_seen": stamp, "detail": detail}
                new += 1
            continue
        saved = record.get("detail") or {}
        if saved.get("updated_at") and when(saved["updated_at"]) > when(detail["updated_at"]):
            continue  # what's saved was read later
        if (record.get("status"), saved.get("status"), saved.get("updated_at")) != (item["status"], detail["status"], detail["updated_at"]):
            changed += 1
        record.update(item)
        record["detail"] = detail
        record["last_seen"] = stamp
        # Listed again: public again.
        record.pop("removed", None)
        record.pop("removed_at", None)
    return new, changed


def add_unlisted(store: dict, items: list[dict], stamp: str) -> int:
    """Save Open311 requests APIv2 didn't list, to be looked up. Returns how many are new."""
    new = 0
    for item in items:
        if item["id"] not in store:
            store[item["id"]] = {**item, "first_seen": stamp, "last_seen": stamp, "detail": None}
            new += 1
    return new


def request_types(api: SeeClickFix, store: dict, *listings: list[dict]) -> set[str]:
    """The town's request types: those on Open311's list of services (its departments', if it names
    them), and every one its saved requests and these Open311 listings have. That list of services
    leaves out request types cities use (some public, some their staff's), so the town's own
    requests show them."""
    departments = api.service_codes()
    codes = set(departments) if departments is not None else {str(s["service_code"]) for s in api.services()}
    codes |= {str(r["service_code"]) for r in store.values() if r.get("service_code") is not None and api.takes(r["service_code"])}
    codes |= {str(i["service_code"]) for items in listings for i in items}
    return codes


def look_up(api: SeeClickFix, store: dict, ids: list[str], stamp: str, limit: int,
            deadline: float) -> tuple[int, list[str], bool, list[str]]:
    """Look up requests one at a time, up to limit. Returns (done, errors, refused, left): refused when
    SeeClickFix turned away REFUSED_IN_A_ROW lookups in a row, so the lookups stopped; left, the ids
    still to look up."""
    queue = [i for i in dict.fromkeys(ids) if i in store and not store[i].get("removed")]
    done, errors, refused, left = 0, [], [], []

    def remove(record):
        # Deleted or made private: keep the record but stop checking it.
        record["removed"] = True
        record["removed_at"] = stamp

    for n, request_id in enumerate(queue):
        if n >= limit or time.monotonic() > deadline:
            return done, errors, False, left + refused + queue[n:]
        record = store[request_id]
        try:
            detail = api.issue(request_id)
        except FetchError as e:
            if e.status in (404, 410):
                remove(record)
                continue
            if e.status == 403:
                # Marked removed only once a later lookup works, so a block removes nothing.
                refused.append(request_id)
                if len(refused) >= REFUSED_IN_A_ROW:
                    return done, errors, True, left + refused + queue[n + 1:]
                continue
            errors.append(str(e))
            left.append(request_id)
            if len(errors) >= 5:
                return done, errors, False, left + queue[n + 1:]
            continue
        for r in refused:
            remove(store[r])
        refused = []
        detail["checked_at"] = stamp
        record["detail"] = detail
        # The detailed status is the request's own.
        record["status"] = "open" if detail["status"] in ("open", "acknowledged") else "closed"
        done += 1
    return done, errors, False, left + refused


def tag_wards(store: dict, lookup: PrecinctLookup) -> int:
    tagged = 0
    for record in store.values():
        if "ward" in record:
            continue
        place = lookup.find(record.get("lat"), record.get("lng"))
        record["ward"] = place["ward"] if place else None
        record["precinct"] = place["district"] if place else None
        tagged += 1
    return tagged


def month_windows(start: date, end: date):
    current = start.replace(day=1)
    while current <= end:
        nxt = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield current, nxt
        current = nxt


def history_windows(start: datetime, end: datetime, tz: ZoneInfo) -> list[dict]:
    """A month of submissions each, newest first, from start's month through end's."""
    windows = [{"start": iso(datetime.combine(a, datetime.min.time(), tz)),
                "end": iso(datetime.combine(b, datetime.min.time(), tz))}
               for a, b in month_windows(start.astimezone(tz).date(), end.astimezone(tz).date())]
    return windows[::-1]


def first_state(store: dict, now: datetime, history_start: datetime, tz: ZoneInfo) -> dict:
    """Where a town's first run on this code starts. A new town: its whole history to read, and
    the requests from before it still open. Requests saved before the sync: from the last run that
    saved them (when each was last listed), reading again the months that have requests whose exact
    times are missing or out of date, and only checking the rest: each is read again only when
    APIv2 counts more or fewer requests in it than are saved (a backfill never run, or cut short)."""
    older = {"start": iso(EPOCH), "end": iso(history_start), "open": True}
    if not store:
        return {"cursor": zulu(now), "history_start": iso(history_start), "reconciled_at": zulu(now),
                "pending": [older] + history_windows(history_start, now, tz), "lookups": []}
    last = max((when(r["last_seen"]) for r in store.values() if r.get("last_seen")), default=now)
    stale = [r for r in store.values() if needs_detail(r)]
    months = {when(r["created_at"]).astimezone(tz).strftime("%Y-%m") for r in stale
              if r.get("created_at") and when(r["created_at"]) >= history_start}
    pending = [w if w["start"][:7] in months else {**w, "check": True} for w in history_windows(history_start, now, tz)]
    older_stale = any(r.get("created_at") and when(r["created_at"]) < history_start for r in stale)
    pending.insert(0, older if older_stale else {**older, "check": True})
    return {"cursor": zulu(last), "history_start": iso(history_start), "reconciled_at": zulu(last),
            "pending": pending, "lookups": [r["id"] for r in stale if not r.get("created_at")]}


def saved_in(store: dict, codes: set[str], start: datetime, end: datetime, only_open: bool) -> int:
    """How many saved requests of these request types were submitted in [start, end) (only those
    open, with only_open), as APIv2 would count them."""
    return sum(1 for r in store.values() if not r.get("removed") and r.get("created_at")
               and str(r.get("service_code")) in codes and start <= when(r["created_at"]) < end
               and (not only_open or r.get("status") == "open"))


def read_window(api: SeeClickFix, store: dict, window: dict, codes: set[str], stamp: str,
                history_start: datetime) -> tuple[int, list[str]] | None:
    """Read one window of history: its requests on Open311 (for request types not seen yet), then
    on APIv2. Returns (new, ids to look up): those Open311 lists and APIv2 doesn't, and those
    still without up-to-date times. A window to check is read only when APIv2 counts more or
    fewer requests in it than are saved: None when it doesn't."""
    start, end = when(window["start"]), when(window["end"])
    if window.get("check") and api.count(codes, OPEN if window.get("open") else EVERY, (start, end)) == \
            saved_in(store, codes, start, end, bool(window.get("open"))):
        return None
    submitted = api.submitted(start, end, "open" if window.get("open") else None)
    codes |= {str(i["service_code"]) for i in submitted}
    issues = api.issues(codes, OPEN if window.get("open") else EVERY, created=(start, end))
    new, _ = merge(store, issues, stamp, history_start)
    listed = {str(i["id"]) for i in issues}
    unlisted = [i for i in submitted if i["id"] not in listed]
    new += add_unlisted(store, unlisted, stamp)
    stale = [rid for rid, r in store.items() if needs_detail(r) and r.get("created_at") and start <= when(r["created_at"]) < end]
    return new, [i["id"] for i in unlisted] + stale


def save(config: dict, data_dir: Path, store: dict, state: dict) -> None:
    """The requests, then where the sync got to: a stop between the two leaves the cursor behind
    the requests, and the next run reads a little again, never less."""
    save_json(store_dir(data_dir) / "requests.json", store)
    bucket_data.push(config, data_dir, "311/requests.json")
    save_json(store_dir(data_dir) / "sync.json", state)


def run(config: dict, client, data_dir: Path, now: datetime | None = None,
        backfill_from: date | None = None, detail_limit: int | None = None,
        time_budget: float | None = None, catch_up: int | None = None) -> dict:
    source = config["seeclickfix"]
    tz = ZoneInfo(config["site"]["timezone"])
    now = now or datetime.now(tz)
    stamp = iso(now)
    started = time.monotonic()
    deadline = started + (time_budget if time_budget is not None else source.get("time_budget_seconds", 2400))
    api = SeeClickFix(config, client)
    # The bucket's copy, if the town keeps one there, is the latest: what the last run saved.
    bucket_data.pull(config, data_dir, refresh=True)
    store = load_store(data_dir)
    status_path = store_dir(data_dir) / "status.json"
    had = json.loads(status_path.read_text(encoding="utf-8")).get("requests_total") if status_path.exists() else None
    if not store and had and not backfill_from:
        # Never start again from nothing, and save that over the town's history.
        raise FetchError(f"the town's {had} saved 311 requests are missing (data/311/requests.json, or its bucket's copy)")

    state = load_state(data_dir)
    if state is None:
        since = source.get("history_since")
        start = (datetime(*map(int, since.split("-")[:2]), 1, tzinfo=tz) if since
                 else now - timedelta(days=source.get("recent_days", 21)))
        state = first_state(store, now, start, tz)
    if backfill_from:
        start = datetime.combine(backfill_from, datetime.min.time(), tz)
        state["pending"] += [w for w in history_windows(start, now, tz) if w not in state["pending"]]
        state["history_start"] = iso(min(start, when(state["history_start"])))
    history_start = when(state["history_start"])

    # 1. The sync: all or nothing. Until it's done, nothing is saved.
    since = when(state["cursor"]) - OVERLAP
    submitted = api.submitted(since, now)
    codes = request_types(api, store, submitted)
    errors, refused, listing_refused = [], False, False
    try:
        issues = api.issues(codes, EVERY, updated=(since, now))
    except Refused as e:
        # The listing refused: what Open311 lists as submitted since the cursor, each looked up
        # by itself (new ones first), and the cursor left where it was.
        listing_refused, issues = True, []
        errors.append(f"SeeClickFix refused its APIv2 listing ({e}): synced from Open311 only the requests "
                      f"submitted since {zulu(since)}; the cursor stays at {state['cursor']}")
        new, changed = add_unlisted(store, submitted, stamp), 0
        waiting = [i["id"] for i in submitted if store[i["id"]].get("detail") is None]
        state = {**state, "lookups": waiting + state["lookups"]}
        print(f"{time.monotonic() - started:.0f}s: APIv2 listing refused; {len(submitted)} submitted since "
              f"{zulu(since)} on Open311, {new} new", flush=True)
    else:
        new, changed = merge(store, issues, stamp, history_start)
        listed = {str(i["id"]) for i in issues}
        unlisted = [i for i in submitted if i["id"] not in listed]
        new += add_unlisted(store, unlisted, stamp)
        lookups = state["lookups"] + [i["id"] for i in unlisted]
        state = {**state, "cursor": zulu(now), "lookups": lookups}
        print(f"{time.monotonic() - started:.0f}s: {len(issues)} requests updated since {zulu(since)}, {new} new", flush=True)

    def refusal(e: Refused) -> None:
        nonlocal listing_refused
        listing_refused = True
        errors.append(f"SeeClickFix refused its APIv2 listing ({e}); the rest waits for the next run")

    # 2. The open requests, every RECONCILE_DAYS days.
    reconciled = False
    if not listing_refused and now - when(state["reconciled_at"]) >= timedelta(days=RECONCILE_DAYS):
        try:
            open_issues = api.issues(codes, OPEN, created=(EPOCH, now))
        except Refused as e:
            refusal(e)
        except FetchError as e:
            errors.append(f"open requests: {e}")
            refused = e.status == 403
        else:
            new += merge(store, open_issues, stamp, history_start)[0]
            listed_open = {str(i["id"]) for i in open_issues}
            gone = [rid for rid, r in store.items() if r.get("status") == "open" and not r.get("removed")
                    and rid not in listed_open and r.get("created_at") and when(r["created_at"]) < now]
            state = {**state, "reconciled_at": zulu(now), "lookups": state["lookups"] + gone}
            reconciled = True
            print(f"{time.monotonic() - started:.0f}s: {len(open_issues)} open; {len(gone)} saved as open aren't", flush=True)

    # 3. History, a window at a time, each saved whole, within its share of requests.
    cap = catch_up if catch_up is not None else source.get("catch_up_requests", CATCH_UP_REQUESTS)
    windows_read = windows_checked = 0
    saved_at, before = time.monotonic(), api.requests
    while (state["pending"] and not refused and not listing_refused and time.monotonic() < deadline
           and api.requests - before < cap):
        window = state["pending"][0]
        try:
            result = read_window(api, store, window, codes, stamp, history_start)
        except Refused as e:
            refusal(e)
            break
        except FetchError as e:
            errors.append(f"history from {window['start'][:10]}: {e}")
            refused = e.status == 403
            break
        added, more = result or (0, [])
        new += added
        state = {**state, "pending": state["pending"][1:], "lookups": state["lookups"] + more}
        windows_read += result is not None
        windows_checked += result is None
        print(f"{time.monotonic() - started:.0f}s: {'read' if result else 'checked'} {window['start'][:10]} to "
              f"{window['end'][:10]}{' (open)' if window.get('open') else ''}, {added} new", flush=True)
        if time.monotonic() - saved_at > CHECKPOINT_SECONDS:
            save(config, data_dir, store, state)
            saved_at = time.monotonic()
            print(f"{time.monotonic() - started:.0f}s: saved progress", flush=True)

    catch_up = api.requests - before

    # 4. Lookups, one request at a time.
    limit = detail_limit if detail_limit is not None else source.get("max_details_per_run", 400)
    details_done, left = 0, list(dict.fromkeys(state["lookups"]))
    if not refused:
        details_done, lookup_errors, refused, left = look_up(api, store, left, stamp, limit, deadline)
        errors += lookup_errors
        if refused:
            errors.append(f"SeeClickFix refused {REFUSED_IN_A_ROW} lookups in a row (HTTP 403); stopped, "
                          "and none was marked removed")
    state = {**state, "lookups": left}

    # A town with no wards or precincts to place requests in has no precincts_file.
    tagged = tag_wards(store, PrecinctLookup(data_dir / "static" / source["precincts_file"])) if source.get("precincts_file") else 0
    save(config, data_dir, store, state)
    summary = {
        "updated_at": stamp,
        "synced_since": iso(since.astimezone(tz)),
        # Every update before it is saved.
        "cursor": state["cursor"],
        "listing_refused": listing_refused,
        "requests_total": len(store),
        "updated_listed": len(issues),
        "new": new,
        "changed": changed,
        "reconciled": reconciled,
        "history_windows_read": windows_read,
        "history_windows_checked": windows_checked,
        "history_windows_left": len(state["pending"]),
        "catch_up_requests": catch_up,
        "details_fetched": details_done,
        "lookups_left": len(left),
        "details_missing": sum(1 for r in store.values() if not r.get("detail") and not r.get("removed")),
        "ward_tagged": tagged,
        "requests_made": getattr(client, "request_count", None),
        "seconds": round(time.monotonic() - started, 1),
        "errors": errors,
    }
    save_json(status_path, summary)
    if refused:
        # What was fetched is saved; the step fails, so the run says 311 is behind.
        raise FetchError(errors[-1], 403)
    if listing_refused and now - when(state["cursor"]) > timedelta(days=LISTING_STALE_DAYS):
        # Saved as above; the step fails so the run says 311 is behind, as for any failed fetch.
        raise FetchError(f"SeeClickFix has refused its APIv2 listing since the cursor, {state['cursor']}, more than "
                         f"{LISTING_STALE_DAYS} days ago: changes to requests already saved aren't being read")
    return summary


def listing_lane(source: dict) -> Lane:
    """APIv2's listing (/api/v2/issues; not the lookups under it), as every town on this machine
    that reads SeeClickFix shares it (pipeline/http.py): its own interval, and its refusals."""
    path = urlsplit(source["api_base"]).path.rstrip("/") + "/issues"
    return Lane(path, source.get("listing_delay", LISTING_DELAY),
                source.get("listing_refused_hours", LISTING_REFUSED_HOURS) * 3600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--backfill", metavar="YYYY-MM", help="read every request since this month")
    parser.add_argument("--details", type=int, help="max one-at-a-time lookups this run")
    parser.add_argument("--time-budget", type=float, help="seconds to spend reading history and looking up requests")
    parser.add_argument("--catch-up", type=int, help="max requests this run spends reading history")
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "seeclickfix"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["seeclickfix"].get("request_delay", 3.2),
                          limiter=HostLimiter(), lanes=(listing_lane(config["seeclickfix"]),))
    backfill = date.fromisoformat(args.backfill + "-01") if args.backfill else None
    try:
        summary = run(config, client, args.data, backfill_from=backfill, detail_limit=args.details, time_budget=args.time_budget,
                      catch_up=args.catch_up)
    except FetchError as e:
        print(f"::error::311 requests: {e}")
        return 1
    print(json.dumps(summary, indent=2))
    for error in summary["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
