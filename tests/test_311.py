"""Tests for the 311 pipeline, run offline against saved SeeClickFix responses."""

import html
import json
import re
import shutil
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import FETCHED_AT, REAL_DATA_DIR
from fakes import FakeSeeClickFix, real_issues, saved_issues
from pipeline import compute_311, fetch_311
from pipeline.config import load_config
from pipeline.geo import PrecinctLookup


@pytest.fixture
def config():
    config = load_config("gloucester")
    # The fixture's history is small: read in one run, unless a test caps it.
    config["seeclickfix"]["catch_up_requests"] = 1000
    return config


@pytest.fixture
def data(tmp_path):
    shutil.copytree(REAL_DATA_DIR / "static", tmp_path / "static")
    return tmp_path


# Just after the real APIv2 page (tests/fixtures/scf_v2_issues.json) was read.
NOW = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)


def load(data_dir):
    return json.loads((data_dir / "311" / "requests.json").read_text())


def test_precinct_lookup():
    lookup = PrecinctLookup(REAL_DATA_DIR / "static" / "gloucester-precincts-2022.geojson")
    city_hall = lookup.find(42.6146, -70.6634)  # 9 Dale Ave
    assert city_hall is not None and city_hall["ward"] in {"1", "2", "3", "4", "5"}
    assert lookup.find(42.3601, -71.0589) is None  # Boston
    assert lookup.find(None, None) is None


def test_run_records_requests_without_personal_details(config, data):
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=0)
    store = load(data)
    assert len(store) == 114
    record = next(iter(store.values()))
    assert {"id", "category", "created_at", "status", "lat", "lng", "ward", "precinct"} <= record.keys()
    assert "description" not in record and "media_url" not in record
    assert all(r["ward"] for r in store.values()), "every fixture request is inside the city"


def test_listings_carry_the_exact_times(config, data):
    """APIv2's listings give each request's acknowledged, closed and reopened times: none is looked
    up one at a time."""
    client = FakeSeeClickFix()
    summary = fetch_311.run(config, client, data, now=FETCHED_AT, detail_limit=0)
    assert summary["details_missing"] == 0 and summary["details_fetched"] == 0
    assert not any("/issues/" in u for u in client.urls)
    store = load(data)
    acknowledged = next(r for r in store.values() if r["detail"]["status"] == "acknowledged")
    assert acknowledged["status"] == "open" and acknowledged["detail"]["acknowledged_at"]
    archived = next(r for r in store.values() if r["detail"]["status"] == "archived")
    assert archived["status"] == "closed" and archived["detail"]["closed_at"]


def test_a_listed_request_reads_as_open311_lists_it():
    """APIv2 and Open311 give the same request the same category, place, times and open or closed;
    APIv2's own status says acknowledged, archived, or reopened."""
    from pipeline import seeclickfix
    issues = {str(i["id"]): i for i in real_issues()}
    for issue in issues.values():
        assert seeclickfix.parse_listed(issue) == seeclickfix.parse_open311(FakeSeeClickFix.open311(issue))
    assert seeclickfix.parse_listed(issues["23023545"])["status"] == "open"  # Acknowledged
    assert seeclickfix.parse_listed(issues["22960687"])["status"] == "closed"  # Archived
    # Closed, then reopened: open, with the close it had.
    reopened = {**seeclickfix.parse_listed(issues["22933218"]), "detail": seeclickfix.parse_issue(issues["22933218"])}
    assert reopened["status"] == "open" and reopened["detail"]["reopened_at"] and reopened["detail"]["closed_at"]
    assert compute_311.closed_time(reopened) is None


def later(days=0, hours=0):
    return FETCHED_AT + timedelta(days=days, hours=hours)


def updated(issue, at, **change):
    """The issue after the city changed it at a time."""
    return {**issue, **change, "updated_at": at.isoformat(timespec="seconds")}


def test_a_sync_reads_only_what_changed_since_the_last(config, data):
    world = real_issues()
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW)
    sync = json.loads((data / "311" / "sync.json").read_text())
    assert sync["cursor"] == "2026-10-15T18:00:00Z" and not sync["pending"]
    # A day later: one request acknowledged; nothing else changed.
    target = world[1]
    world[1] = updated(target, NOW + timedelta(hours=3), status="Acknowledged", acknowledged_at=(NOW + timedelta(hours=3)).isoformat())
    client = FakeSeeClickFix(issues=world)
    summary = fetch_311.run(config, client, data, now=NOW + timedelta(days=1))
    assert summary["updated_listed"] == 1 and summary["changed"] == 1 and summary["new"] == 0
    # The organization's list of request types, and two listings: what was submitted (Open311), and what
    # was updated (APIv2), since an hour before the cursor.
    assert client.request_count == 3
    assert "updated_at_after=2026-10-15T17%3A00%3A00Z" in client.urls[2]
    assert load(data)[str(target["id"])]["detail"]["acknowledged_at"]


def test_a_request_closed_between_runs_is_closed_by_its_own_status(config, data):
    world = real_issues()
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW)
    i = next(n for n, issue in enumerate(world) if issue["status"] == "Open")
    closed_at = NOW + timedelta(hours=5)
    world[i] = updated(world[i], closed_at, status="Closed", closed_at=closed_at.isoformat())
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW + timedelta(days=1))
    record = load(data)[str(world[i]["id"])]
    assert record["status"] == "closed" and record["detail"]["closed_at"] == closed_at.isoformat()


def test_a_failed_sync_leaves_the_requests_and_cursor_as_they_were(config, data):
    world = real_issues()
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW)
    saved = {name: (data / "311" / name).read_text() for name in ("requests.json", "sync.json", "status.json")}
    world[0] = updated(world[0], NOW + timedelta(hours=2), status="Archived")
    for failing in ("open311", "v2"):
        with pytest.raises(fetch_311.FetchError, match="HTTP 503"):
            fetch_311.run(config, FakeSeeClickFix(issues=world, failing=failing), data, now=NOW + timedelta(days=1))
        assert {name: (data / "311" / name).read_text() for name in saved} == saved
    # The next run that works picks up where the last good one ended.
    summary = fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW + timedelta(days=2))
    assert summary["synced_since"] == "2026-10-15T13:00:00-04:00" and load(data)[str(world[0]["id"])]["status"] == "closed"


def test_a_new_towns_history_is_read_a_month_at_a_time_and_resumed(config, data, monkeypatch):
    """A new town's history since history_since is read a month of submissions at a time, newest
    first, within the time budget; a run that stops part way saves the months it read whole, and
    the next run goes on from there."""
    summary = fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, time_budget=0)
    pending = json.loads((data / "311" / "sync.json").read_text())["pending"]
    # Older requests still open, then each month from January 2024 through September 2026, newest first.
    assert pending[0] == {"start": "2008-01-01T00:00:00+00:00", "end": "2024-01-01T00:00:00-05:00", "open": True}
    assert [w["start"][:7] for w in pending[1:3]] == ["2026-09", "2026-08"] and pending[-1]["start"][:7] == "2024-01"
    assert summary["history_windows_left"] == len(pending) == 34
    # The second window fails: the first is kept, and the run says what stopped it.
    calls = []
    original = fetch_311.read_window

    def read_two(*args):
        calls.append(args[2]["start"])
        if len(calls) == 2:
            raise fetch_311.FetchError("HTTP 503", 503)
        return original(*args)
    monkeypatch.setattr(fetch_311, "read_window", read_two)
    summary = fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT)
    assert summary["history_windows_read"] == 1 and summary["errors"] == ["history from 2026-09-01: HTTP 503"]
    assert json.loads((data / "311" / "sync.json").read_text())["pending"] == pending[1:]
    monkeypatch.setattr(fetch_311, "read_window", original)
    summary = fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT)
    assert summary["history_windows_left"] == 0 and summary["requests_total"] == 114


def test_backfill_adds_months_to_what_is_still_to_read(config, data):
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, time_budget=0)
    pending = json.loads((data / "311" / "sync.json").read_text())["pending"]
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, time_budget=0, backfill_from=date(2023, 6, 1))
    sync = json.loads((data / "311" / "sync.json").read_text())
    assert sync["pending"][:len(pending)] == pending
    assert [w["start"][:7] for w in sync["pending"][len(pending):]] == [f"2023-{m:02d}" for m in range(12, 5, -1)]
    assert sync["history_start"] == "2023-06-01T00:00:00-04:00"


def test_a_window_too_long_for_the_pages_served_is_split(config, data, monkeypatch):
    """No listing is cut short: a window with more requests than SeeClickFix serves is read as
    narrower ones until each fits, and every request is read."""
    monkeypatch.setattr(fetch_311, "V2_PAGES", 1)
    monkeypatch.setattr(fetch_311, "PAGE_SIZE", 5)
    world = real_issues()
    client = FakeSeeClickFix(issues=world)
    fetch_311.run(config, client, data, now=NOW + timedelta(days=1), backfill_from=date(2026, 10, 1))
    assert set(load(data)) == {str(i["id"]) for i in world}
    listings = [u for u in client.urls if "/issues?" in u]
    assert not any("page=2" in u for u in listings)
    assert len({re.search(r"after=([^&]*)", u).group(1) for u in listings if "updated_at" not in u}) > 3
    # Six submitted in one second: there's no narrower window to read them in. The month stays to be
    # read, and the run says why; none of the six is saved from a part of the listing.
    burst = [dict(world[0], id=90000000 + n) for n in range(6)]
    summary = fetch_311.run(config, FakeSeeClickFix(issues=burst), data, now=NOW + timedelta(days=2),
                            backfill_from=date(2026, 10, 1))
    assert "more than 5 requests in one second" in summary["errors"][0] and summary["history_windows_left"] == 1
    assert not any(r.startswith("9000000") for r in load(data))
    # In the sync, it stops the step, saving nothing.
    burst = [dict(b, updated_at=(NOW + timedelta(days=2, hours=1)).isoformat()) for b in burst]
    with pytest.raises(fetch_311.FetchError, match="more than 5 requests in one second"):
        fetch_311.run(config, FakeSeeClickFix(issues=burst), data, now=NOW + timedelta(days=3))


def test_a_window_that_changes_while_read_is_read_again(config, data, monkeypatch):
    """A request updated between two pages moves the ones after it: a window whose count changes as
    it's read is read again, so none is missed."""
    monkeypatch.setattr(fetch_311, "PAGE_SIZE", 5)
    world = real_issues()
    client = FakeSeeClickFix(issues=world)
    get = client.get

    def changing(url):
        response = get(url)
        if "/issues?" in url and "page=2" in url and len(client.issues) == len(world):
            # Closed and archived just now: it leaves this window.
            client.issues = [updated(world[0], NOW + timedelta(days=2), status="Archived")] + world[1:]
        return response
    client.get = changing
    fetch_311.run(config, client, data, now=NOW + timedelta(days=1), backfill_from=date(2026, 10, 1))
    assert set(load(data)) == {str(i["id"]) for i in world}


def test_the_rate_limit_is_shared_by_every_town_and_honours_retry_after(tmp_path, monkeypatch):
    """Two towns' fetches, each its own process with its own client, keep one request delay between
    them to the same host, and a Retry-After one is given holds the other back too."""
    from pipeline import http
    clock = [1000.0]
    monkeypatch.setattr(http.time, "time", lambda: clock[0])
    monkeypatch.setattr(http.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    town_a, town_b = http.HostLimiter(tmp_path), http.HostLimiter(tmp_path)
    town_a.wait("seeclickfix.com", 3.2)
    town_b.wait("seeclickfix.com", 3.2)
    assert clock[0] == pytest.approx(1003.2)
    town_b.wait("example.org", 3.2)  # another host isn't held up
    assert clock[0] == pytest.approx(1003.2)
    town_a.hold("seeclickfix.com", 60)
    town_b.wait("seeclickfix.com", 3.2)
    assert clock[0] == pytest.approx(1063.2)

    class Busy:
        def __init__(self):
            self.headers = {"User-Agent": "test"}
            self.calls = 0

        def get(self, url, timeout):
            self.calls += 1
            return type("R", (), {"status_code": 429 if self.calls == 1 else 200, "headers": {"Retry-After": "120"}})()
    client = http.PoliteClient("test", delay=3.2, limiter=town_a)
    client.session = Busy()
    client.get("https://seeclickfix.com/api/v2/issues")
    other = http.HostLimiter(tmp_path)
    start = clock[0]
    other.wait("seeclickfix.com", 3.2)
    assert clock[0] >= start  # not before the 120 seconds asked for
    assert json.loads((tmp_path / "seeclickfix.com.json").read_text())["until"] >= 1063.2 + 120


def old_store(world: list[dict], stamp: str, without_detail: set[str]) -> dict:
    """Requests as fetches before the sync saved them: Open311's fields, and APIv2's times for those looked up."""
    from pipeline import seeclickfix
    store = {}
    for issue in world:
        record = {**seeclickfix.parse_open311(FakeSeeClickFix.open311(issue)), "first_seen": stamp, "last_seen": stamp,
                  "ward": "2", "precinct": "2-1",
                  "detail": None if str(issue["id"]) in without_detail else {**seeclickfix.parse_issue(issue), "checked_at": stamp}}
        store[record["id"]] = record
    return store


def test_saved_requests_go_on_from_where_the_last_fetch_left_them(config, data):
    """A town's first run on the sync takes its cursor from its saved requests, and reads again only
    the months with requests whose exact times are missing, counting the rest: no full download."""
    world = real_issues()
    # Three submitted in August, saved without their exact times.
    for issue in world[-3:]:
        issue["created_at"] = issue["created_at"].replace("2026-10-0", "2026-08-1")
    missing = {str(i["id"]) for i in world[-3:]}
    stamp = "2026-10-15T12:00:00-04:00"
    store = old_store(world, stamp, missing)
    (data / "311").mkdir()
    fetch_311.save_json(data / "311" / "requests.json", store)
    fetch_311.save_json(data / "311" / "status.json", {"updated_at": stamp, "requests_total": len(store)})
    client = FakeSeeClickFix(issues=world)
    summary = fetch_311.run(config, client, data, now=NOW + timedelta(days=1))
    assert summary["synced_since"] == "2026-10-15T11:00:00-04:00"
    assert summary["history_windows_read"] == 1 and summary["details_missing"] == 0 and summary["new"] == 0
    # The sync's list of request types and two listings; August's, its Open311 list and its APIv2 one; and
    # a count of each of the other 33 months since January 2024 and of the older requests still open, each
    # as many as are saved.
    assert summary["history_windows_checked"] == 34
    assert client.request_count == 3 + 2 + 34 and any("after=2026-08-01T04%3A00%3A00Z" in u for u in client.urls)
    assert sum(f"per_page={fetch_311.COUNT_PAGE_SIZE}&" in u for u in client.urls) == 34
    after = load(data)
    synced = {k for k, r in after.items() if r["last_seen"] != stamp} - missing
    assert len(synced) == summary["updated_listed"] == 3
    assert {k: after[k] for k in after.keys() - synced - missing} == {k: store[k] for k in store.keys() - synced - missing}
    assert all(after[k]["detail"]["acknowledged_at"] == store[k]["detail"]["acknowledged_at"] for k in synced)
    assert all(after[k]["detail"]["checked_at"] == (NOW + timedelta(days=1)).isoformat() for k in missing)
    sync = json.loads((data / "311" / "sync.json").read_text())
    assert sync["cursor"] == "2026-10-16T18:00:00Z" and not sync["pending"] and not sync["lookups"]


def test_a_month_the_last_fetch_never_read_is_read_on_the_first_sync(config, data):
    """Saved requests from a history never backfilled, or a backfill cut short: a month where
    APIv2 counts more requests than are saved is read again, and its requests added."""
    world = real_issues()
    store = old_store(world, "2026-10-15T12:00:00-04:00", set())
    # Closed requests the last fetch never listed, last updated before it: one of a request type
    # no other saved request has, which the organization's list of services names.
    unread = ["22960687", "23019380"]
    for k in unread:
        del store[k]
    (data / "311").mkdir()
    fetch_311.save_json(data / "311" / "requests.json", store)
    services = [{"service_code": code, "organization": "Gloucester, MA"} for code in (8924, 52271)]
    summary = fetch_311.run(config, FakeSeeClickFix(issues=world, services=services), data, now=NOW + timedelta(days=1))
    assert summary["history_windows_read"] == 1 and summary["history_windows_checked"] == 34
    assert summary["new"] == 2 and set(load(data)) == {str(i["id"]) for i in world}


def test_requests_missing_from_the_open_list_are_looked_up_each_week(config, data):
    """Once a week every open request is listed. One saved as open that isn't there is looked up:
    deleted, it's kept out; still open (moved to another organization's request type), it stays open."""
    world = real_issues()
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW)
    open_ids = [str(i["id"]) for i in world if i["status"] in ("Open", "Acknowledged")]
    deleted, moved = open_ids[0], open_ids[1]
    world = [i for i in world if str(i["id"]) != deleted]
    world = [dict(i, request_type={**i["request_type"], "id": 1}) if str(i["id"]) == moved else i for i in world]
    client = FakeSeeClickFix(issues=world, missing_ids={deleted})
    summary = fetch_311.run(config, client, data, now=NOW + timedelta(days=3))
    assert not summary["reconciled"] and not any("/issues/" in u for u in client.urls)
    summary = fetch_311.run(config, client, data, now=NOW + timedelta(days=8))
    assert summary["reconciled"] and summary["details_fetched"] == 1
    store = load(data)
    assert store[deleted]["removed"] is True and store[moved]["status"] == "open"


def test_removed_request_is_kept_out(config, data):
    """A request Open311 lists and APIv2 doesn't is looked up: gone, it's kept out of the figures."""
    gone = str(saved_issues()[0]["id"])
    fetch_311.run(config, FakeSeeClickFix(unlisted_ids={gone}, missing_ids={gone}), data, now=FETCHED_AT)
    assert load(data)[gone]["removed"] is True
    scorecard = compute_311.compute(config, data, now=FETCHED_AT)
    assert scorecard["overall"]["received"] == 113


def test_private_request_is_kept_out(config, data):
    """A 403 for one request, with lookups after it working, is a request made private."""
    issues = saved_issues()
    private, other = str(issues[0]["id"]), str(issues[1]["id"])
    fetch_311.run(config, FakeSeeClickFix(unlisted_ids={private, other}, refused_ids={private}), data, now=FETCHED_AT)
    store = load(data)
    assert store[private]["removed"] is True and store[other]["detail"]
    assert sum(1 for r in store.values() if r.get("removed")) == 1


def test_refused_lookups_stop_the_step_and_remove_nothing(config, data):
    """SeeClickFix turning every lookup away is a block, not a city's worth of deleted
    requests: the step fails, nothing is marked removed, and what was listed is kept."""
    unlisted = {str(i["id"]) for i in saved_issues()[:4]}
    with pytest.raises(fetch_311.FetchError, match="refused 3 lookups in a row"):
        fetch_311.run(config, FakeSeeClickFix(unlisted_ids=unlisted, refuse_all=True), data, now=FETCHED_AT)
    store = load(data)
    assert len(store) == 114 and not any(r.get("removed") for r in store.values())
    status = json.loads((data / "311" / "status.json").read_text())
    assert any("HTTP 403" in e for e in status["errors"])
    assert set(json.loads((data / "311" / "sync.json").read_text())["lookups"]) == unlisted
    # The block lifts: the next run looks them up.
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT)
    assert all(r["detail"] for r in load(data).values())


def submitted(world: list[dict], request_id: int, at: datetime) -> dict:
    """A new open request, submitted at a time."""
    template = next(i for i in world if i["status"] == "Open")
    return updated(dict(template, id=request_id, created_at=at.isoformat(timespec="seconds")), at)


def test_a_refused_listing_falls_back_to_open311_and_keeps_the_cursor(config, data, monkeypatch):
    """SeeClickFix refusing APIv2's listing doesn't fail the step: the run saves the requests Open311
    lists as submitted since the cursor, each looked up by itself, leaves the open list and the
    history alone, keeps the cursor where it was, and says so. The next run with the listing reads
    every update since that cursor."""
    world = real_issues()
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW)
    i = next(n for n, issue in enumerate(world) if issue["status"] == "Open")
    world.append(submitted(world, 93000001, NOW + timedelta(hours=3)))
    closed_at = NOW + timedelta(hours=5)
    world[i] = updated(world[i], closed_at, status="Closed", closed_at=closed_at.isoformat())
    client = FakeSeeClickFix(issues=world, refuse_listing=True)
    # A week on: the open list is due too. (A cursor this old fails the step; see below.)
    monkeypatch.setattr(fetch_311, "LISTING_STALE_DAYS", 30)
    summary = fetch_311.run(config, client, data, now=NOW + timedelta(days=8))
    assert summary["listing_refused"] and summary["new"] == 1 and summary["details_fetched"] == 1
    assert not summary["reconciled"] and summary["cursor"] == "2026-10-15T18:00:00Z"
    assert "refused its APIv2 listing" in summary["errors"][0] and "?" not in summary["errors"][0]
    # The refused listing, once; then only Open311 and the lookup.
    assert sum("/issues?" in u for u in client.urls) == 1 and client.urls[-1].endswith("/issues/93000001")
    store = load(data)
    assert store["93000001"]["detail"]["checked_at"] == (NOW + timedelta(days=8)).isoformat()
    assert store[str(world[i]["id"])]["status"] == "open"  # its update waits for the listing
    assert json.loads((data / "311" / "sync.json").read_text())["cursor"] == "2026-10-15T18:00:00Z"
    assert json.loads((data / "311" / "status.json").read_text())["listing_refused"] is True
    summary = fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW + timedelta(days=9))
    assert summary["synced_since"] == "2026-10-15T13:00:00-04:00" and not summary["listing_refused"]
    assert summary["reconciled"] and not summary["errors"]
    assert load(data)[str(world[i]["id"])]["status"] == "closed"
    assert json.loads((data / "311" / "sync.json").read_text())["cursor"] == "2026-10-24T18:00:00Z"


def test_a_listing_refused_for_days_fails_the_step_after_saving(config, data):
    """A refusal that has kept the cursor back more than LISTING_STALE_DAYS still saves what Open311
    lists, then fails the step, so the run says 311 is behind; within that, the step passes."""
    world = real_issues()
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW)
    world.append(submitted(world, 93000002, NOW + timedelta(days=1)))
    within = NOW + timedelta(days=fetch_311.LISTING_STALE_DAYS)
    summary = fetch_311.run(config, FakeSeeClickFix(issues=world, refuse_listing=True), data, now=within)
    assert summary["listing_refused"] and summary["new"] == 1
    world.append(submitted(world, 93000003, within))
    with pytest.raises(fetch_311.FetchError, match="refused its APIv2 listing since the cursor"):
        fetch_311.run(config, FakeSeeClickFix(issues=world, refuse_listing=True), data,
                      now=within + timedelta(hours=2))
    assert "93000003" in load(data)
    assert json.loads((data / "311" / "status.json").read_text())["listing_refused"] is True
    assert json.loads((data / "311" / "sync.json").read_text())["cursor"] == "2026-10-15T18:00:00Z"


def test_a_listing_refused_after_the_sync_leaves_the_history_for_later(config, data):
    """Refused while the history is read, the run keeps the sync it made and the months it read,
    and the rest waits for the next run: the step doesn't fail."""
    summary = fetch_311.run(config, FakeSeeClickFix(refuse_listing=lambda url: "updated_at_after" not in url),
                            data, now=FETCHED_AT)
    # The months before it with no requests were read without the listing.
    assert summary["listing_refused"] and summary["history_windows_left"] == 34 - summary["history_windows_read"] > 30
    assert summary["cursor"] == fetch_311.zulu(FETCHED_AT) and summary["errors"][0].startswith("SeeClickFix refused")
    summary = fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT)
    assert summary["history_windows_left"] == 0 and summary["requests_total"] == 114


def test_the_fallback_failing_fails_the_step(config, data):
    """The fallback's own lookups refused is a block, as before: the step fails, and the requests it
    saved wait to be looked up, none marked removed, the cursor where it was."""
    world = real_issues()
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW)
    fresh = [submitted(world, 93000001 + n, NOW + timedelta(hours=n + 1)) for n in range(3)]
    with pytest.raises(fetch_311.FetchError, match="refused 3 lookups in a row"):
        fetch_311.run(config, FakeSeeClickFix(issues=world + fresh, refuse_listing=True, refuse_all=True),
                      data, now=NOW + timedelta(days=1))
    sync = json.loads((data / "311" / "sync.json").read_text())
    assert sync["cursor"] == "2026-10-15T18:00:00Z" and set(sync["lookups"]) == {str(i["id"]) for i in fresh}
    assert not any(r.get("removed") for r in load(data).values())


def test_the_catch_up_is_spread_over_runs_and_the_sync_isnt_counted(config, data, monkeypatch):
    """A town's first run on the sync checks at most catch_up_requests months' counts, and each run
    after goes on exactly where the last stopped. The daily sync, however many pages it takes,
    always runs whole and isn't counted."""
    world = [updated(i, NOW + timedelta(hours=1)) for i in real_issues()]
    store = old_store(world, "2026-10-15T12:00:00-04:00", set())
    (data / "311").mkdir()
    fetch_311.save_json(data / "311" / "requests.json", store)
    config["seeclickfix"]["catch_up_requests"] = 10
    monkeypatch.setattr(fetch_311, "PAGE_SIZE", 5)
    checked, pending = [], None
    for day in range(1, 5):
        client = FakeSeeClickFix(issues=world)
        summary = fetch_311.run(config, client, data, now=NOW + timedelta(days=day))
        sync = json.loads((data / "311" / "sync.json").read_text())
        if pending is not None:
            assert sync["pending"] == pending[summary["history_windows_checked"]:]
        pending = sync["pending"]
        checked.append(summary["history_windows_checked"])
        assert summary["catch_up_requests"] == summary["history_windows_checked"]
        sync_requests = client.request_count - summary["catch_up_requests"]
        assert sync_requests == len([u for u in client.urls if f"per_page={fetch_311.COUNT_PAGE_SIZE}&" not in u])
        if day == 1:
            # 13 requests updated since the cursor: three pages of APIv2's listing.
            assert summary["updated_listed"] == 13 and sum("updated_at_after" in u for u in client.urls) == 3
    assert checked == [10, 10, 10, 5] and not pending


def test_a_month_started_is_read_whole(config, data, monkeypatch):
    """The share is checked between months: a month's pages are never cut short by it."""
    world = real_issues()
    fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW)
    config["seeclickfix"]["catch_up_requests"] = 1
    monkeypatch.setattr(fetch_311, "PAGE_SIZE", 5)
    summary = fetch_311.run(config, FakeSeeClickFix(issues=world), data, now=NOW + timedelta(days=1),
                            backfill_from=date(2026, 9, 1))
    # October's three pages on Open311 and three on APIv2; September waits.
    assert summary["history_windows_read"] == 1 and summary["catch_up_requests"] == 6
    assert [w["start"][:7] for w in json.loads((data / "311" / "sync.json").read_text())["pending"]] == ["2026-09"]


def test_the_listing_is_its_own_lane(config):
    lane = fetch_311.listing_lane(config["seeclickfix"])
    assert lane.path == "/api/v2/issues"
    assert lane.interval == fetch_311.LISTING_DELAY and lane.cooldown == fetch_311.LISTING_REFUSED_HOURS * 3600


def test_scorecard_is_dated_by_the_last_fetch(config, data):
    """The 311 page says when SeeClickFix was last read, not when the numbers were worked out:
    a day the fetch failed doesn't read as updated."""
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=0)
    later = FETCHED_AT.replace(day=FETCHED_AT.day + 2)
    scorecard = compute_311.compute(config, data, now=later)
    assert scorecard["fetched_at"] == FETCHED_AT.isoformat(timespec="seconds")
    assert scorecard["generated_at"] == later.isoformat(timespec="seconds")


def test_close_time_prefers_exact_then_archive_then_update():
    base = {"status": "closed", "updated_at": "2026-09-05T00:00:00-04:00"}
    assert compute_311.closed_time({**base, "detail": {"closed_at": "2026-09-02T00:00:00-04:00", "updated_at": "2026-09-03T00:00:00-04:00"}}).day == 2
    assert compute_311.closed_time({**base, "detail": {"closed_at": None, "updated_at": "2026-09-03T00:00:00-04:00"}}).day == 3
    assert compute_311.closed_time({**base, "detail": None}).day == 5
    assert compute_311.closed_time({**base, "status": "open"}) is None


def test_small_samples_are_suppressed():
    assert compute_311.stats([1.0, 2.0])["median"] is None
    s = compute_311.stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert s["median"] == 3.0 and s["n"] == 5


def test_scorecard(config, data):
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=500)
    sc = compute_311.compute(config, data, now=FETCHED_AT)
    o = sc["overall"]
    assert o["received"] == o["open"] + o["closed"] == 114
    assert o["checked"] == 114
    assert 0 < o["acknowledged"] < o["checked"]
    assert o["time_to_close"]["median"] is not None
    assert sum(b["count"] for b in sc["backlog"]["buckets"]) == sc["backlog"]["open"] == 100
    assert sum(w["received"] for w in sc["by_ward"]) == 114
    assert all(w["per_1000_residents"] for w in sc["by_ward"] if w["ward"] != "outside")
    assert sc["backlog"]["oldest"][0]["url"].startswith("https://seeclickfix.com/issues/")


def test_month_windows_cover_every_month():
    from datetime import date
    windows = list(fetch_311.month_windows(date(2024, 11, 1), date(2025, 2, 10)))
    assert [w[0].isoformat() for w in windows] == ["2024-11-01", "2024-12-01", "2025-01-01", "2025-02-01"]
    assert windows[-1][1].isoformat() == "2025-03-01"


def test_street_address_keeps_what_seeclickfix_shows():
    from pipeline.seeclickfix import street_address
    assert street_address("229 Main St Gloucester, Massachusetts, 01930", "Gloucester") == "229 Main St"
    assert street_address("30 Reservoir Rd Gloucester MA 01930, United States", "Gloucester") == "30 Reservoir Rd"
    assert street_address("470-580 Western Ave", "Gloucester") == "470-580 Western Ave"
    assert street_address("Bray St & Salt Marsh Ln", "Gloucester") == "Bray St & Salt Marsh Ln"
    assert street_address("01930", "Gloucester") == ""


def request(id, created, lat=42.6150, lng=-70.6600, status="closed", closed=None, category="Pothole"):
    return {"id": id, "category": category, "address": "12 Main St", "ward": "2", "status": status,
            "created_at": created, "updated_at": closed or created, "lat": lat, "lng": lng,
            "detail": {"closed_at": closed} if closed else None}


def test_repeat_locations():
    records = [
        request("1", "2026-05-01T09:00:00-04:00", closed="2026-05-03T09:00:00-04:00"),
        # 20 meters away, after the first was closed: a repeat.
        request("2", "2026-05-20T09:00:00-04:00", lat=42.61518, status="open"),
        # Same spot, but a different category.
        request("3", "2026-05-21T09:00:00-04:00", category="Sidewalk Issue", closed="2026-05-22T09:00:00-04:00"),
        # Far away.
        request("4", "2026-05-22T09:00:00-04:00", lat=42.62, closed="2026-05-23T09:00:00-04:00"),
        # Same spot, but more than 60 days after the last request there.
        request("5", "2026-09-01T09:00:00-04:00", closed="2026-09-02T09:00:00-04:00"),
        # Two reports of the same problem before any close: not a repeat.
        request("6", "2026-06-01T09:00:00-04:00", lat=42.63, status="open"),
        request("7", "2026-06-02T09:00:00-04:00", lat=42.63, status="open"),
    ]
    places = compute_311.repeat_locations(records, 50, 60, "https://seeclickfix.com/issues", "Gloucester")
    assert len(places) == 1
    p = places[0]
    assert [r["id"] for r in p["requests"]] == ["1", "2"]
    assert p["reports"] == 2 and p["again_after_close"] == 1 and p["open"] == 1
    assert p["address"] == "12 Main St"
    assert p["requests"][-1]["url"] == "https://seeclickfix.com/issues/2"


def test_every_public_category_is_mapped():
    categories = ("Pothole", "Health Department (Housing) - Internal", "Private Property Issue",
                  "Animal Issues", "Police Department (Non-Emergency)")
    records = [request("1", "2026-09-01T09:00:00-04:00", category=c) for c in categories]
    records.append(request("2", "2026-09-01T09:00:00-04:00", lat=None))
    assert [r["category"] for r in compute_311.mappable(records)] == list(categories)


def test_longest_open_lists_every_category():
    records = [request("1", "2026-01-01T09:00:00-04:00", status="open", category="Health Department (Housing) - Internal"),
               request("2", "2026-02-01T09:00:00-04:00", status="open", lat=None),
               request("3", "2026-03-01T09:00:00-04:00", status="open")]
    oldest = compute_311.oldest_open(records, FETCHED_AT, "https://seeclickfix.com/issues", "Gloucester")
    assert [r["id"] for r in oldest] == ["1", "2", "3"]
    assert {r["address"] for r in oldest} == {"12 Main St"}


def test_scorecard_has_recent_open_and_repeats(config, data):
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=500)
    sc = compute_311.compute(config, data, now=FETCHED_AT)
    recent = sc["recent_open"]["requests"]
    assert recent and all(r["created_at"] >= "2026-08-27" for r in recent)
    assert [r["created_at"] for r in recent] == sorted((r["created_at"] for r in recent), reverse=True)
    assert "places" in sc["repeats"]


def test_open_requests_with_no_update_in_a_year():
    def req(rid, created, updated=None, detail_updated=None, category="Sidewalk Issue", ward="1"):
        r = {"id": rid, "category": category, "ward": ward, "status": "open", "created_at": created, "updated_at": updated}
        if detail_updated:
            r["detail"] = {"updated_at": detail_updated}
        return r
    records = [
        req("a", "2024-05-01T09:00:00-04:00", "2024-06-01T09:00:00-04:00"),           # quiet since June 2024
        req("b", "2024-05-01T09:00:00-04:00", "2026-08-01T09:00:00-04:00"),           # updated last month
        req("c", "2024-05-01T09:00:00-04:00", None, "2026-09-01T09:00:00-04:00"),     # a recent status change seen in detail
        req("d", "2025-02-01T09:00:00-05:00", category="Other", ward=None),           # never updated, 20 months old
        req("e", "2026-03-01T09:00:00-05:00"),                                        # never updated, 7 months old
    ]
    quiet = compute_311.no_update(records, FETCHED_AT)
    assert quiet["count"] == 2
    assert quiet["by_category"] == [{"name": "Other", "count": 1}, {"name": "Sidewalk Issue", "count": 1}]
    assert quiet["by_ward"] == [{"name": "1", "count": 1}, {"name": "outside", "count": 1}]


def test_site_explains_requests_with_no_update(site_dir, data_dir, tmp_path):
    # The saved requests are all recent, so the section stays hidden.
    assert 'id="no-update"' not in (site_dir / "311" / "index.html").read_text()
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    card = json.loads((data / "311" / "scorecard.json").read_text())
    card["backlog"]["no_update"] = {"days": 365, "count": 298, "by_category": [{"name": "Sidewalk Issue", "count": 298}],
                                    "by_ward": [{"name": "1", "count": 200}, {"name": "outside", "count": 98}]}
    (data / "311" / "scorecard.json").write_text(json.dumps(card))
    from conftest import BUILT_AT
    from pipeline import build_site
    build_site.build("gloucester", tmp_path / "site", data_dir=data, now=BUILT_AT)
    page = (tmp_path / "site" / "311" / "index.html").read_text()
    assert '<a href="#no-update">298 with no update in over a year</a>' in page
    assert "298 of the" in page and "fixed and never closed, or never handled" in page
    assert "Outside wards" in page
    assert "298 with no update in over a year" in (tmp_path / "site" / "index.html").read_text()
    assert "No update in over a year" in (tmp_path / "site" / "311" / "methodology" / "index.html").read_text()


def test_departments_limit_requests_to_their_request_types(config, data):
    """A town that lists departments keeps only their request types, from the
    organization's own list of services."""
    config["seeclickfix"]["departments"] = ["Public Works"]
    services = [{"service_code": 8928, "service_name": "Trash or Recycling", "organization": "Public Works"},
                {"service_code": 8929, "service_name": "Pothole", "organization": "Public Works"},
                {"service_code": 8936, "service_name": "Other", "organization": "Police"}]
    client = FakeSeeClickFix(services=services)
    fetch_311.run(config, client, data, now=FETCHED_AT, detail_limit=0)
    categories = {r["category"] for r in load(data).values()}
    assert categories == {"Trash or Recycling", "Pothole"}
    assert sum(u.endswith("/547/services.json") for u in client.urls) == 1


def test_departments_with_no_request_types_stop_the_fetch(config, data):
    from pipeline.http import FetchError
    config["seeclickfix"]["departments"] = ["No Such Department"]
    with pytest.raises(FetchError, match="no request types for No Such Department"):
        fetch_311.run(config, FakeSeeClickFix(services=[]), data, now=FETCHED_AT, detail_limit=0)
    assert not (data / "311" / "requests.json").exists()


def test_ward_source_and_scope_note_are_shown(config, data, tmp_path, monkeypatch):
    from conftest import BUILT_AT, DATA_DIR
    from pipeline import build_site
    config["seeclickfix"].update(wards_publisher="NH GRANIT", wards_year=2022, wards_url="https://example.org/wards",
                                 scope_note="Public Works requests only.")
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=DATA_DIR, now=BUILT_AT)
    ward = next((out / "311" / "ward").glob("*/index.html")).read_text()
    assert "Wards use the 2022 boundaries from NH GRANIT." in ward
    methodology = (out / "311" / "methodology" / "index.html").read_text()
    assert 'href="https://example.org/wards"' in methodology and "2022 NH GRANIT ward boundaries" in methodology
    assert "<li>Public Works requests only.</li>" in methodology
    assert "<p>Public Works requests only.</p>" in (out / "311" / "index.html").read_text()
    about = (out / "about" / "index.html").read_text()
    assert 'href="https://example.org/wards"' in about and ", 2022 boundaries." in about and "MassGIS" not in about


def test_a_town_without_wards_places_requests_in_precincts(config, data, tmp_path, monkeypatch):
    # South Kingstown elects every seat townwide: its 311 areas are voting precincts, named as such,
    # and the Officials page has no ward finder.
    from conftest import BUILT_AT, DATA_DIR
    from pipeline import build_site, officials
    config["seeclickfix"].update(areas="precincts", wards_publisher="RIGIS", wards_year=2022)
    assert officials.wards_file(config) is None
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=DATA_DIR, now=BUILT_AT)
    scorecard = (out / "311" / "index.html").read_text()
    assert "Requests per 1,000 residents, by voting precinct" in scorecard and "Precinct 1" in scorecard
    ward = (out / "311" / "ward" / "1" / "index.html").read_text()
    assert "<h1>Precinct 1</h1>" in ward and "Voting precincts use the 2022 boundaries from RIGIS." in ward
    assert "Ward 1" not in ward
    methodology = (out / "311" / "methodology" / "index.html").read_text()
    assert "2022 RIGIS voting precinct boundaries" in methodology and "ward boundaries" not in methodology
    assert "<strong>Voting precinct maps:</strong>" in (out / "about" / "index.html").read_text()
    assert "Find your ward" not in (out / "officials" / "index.html").read_text()
    spanish = (out / "es" / "311" / "ward" / "1" / "index.html")
    if spanish.exists():
        assert "Precinto 1" in spanish.read_text()


def test_a_town_with_no_areas(config, data, tmp_path, monkeypatch):
    # Bangor has neither wards nor voting precincts: [seeclickfix] has no precincts_file, requests
    # aren't placed in any area, and the pages leave areas out instead of explaining their absence.
    from conftest import BUILT_AT, DATA_DIR
    from pipeline import build_site
    from pipeline.fetch_meetings import save_json
    del config["seeclickfix"]["precincts_file"]
    for body in config["officials"]["bodies"]:
        for m in body["members"]:
            m.pop("ward", None), m.pop("wards", None)
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=500)
    assert not any(r.get("ward") for r in load(data).values())
    sc = compute_311.compute(config, data, now=FETCHED_AT)
    assert sc["overall"]["received"] == 114
    assert sc["by_ward"] == [] and sc["wards"] == [] and sc["backlog"]["no_update"]["by_ward"] == []
    assert all(c["by_ward"] == [] for c in sc["categories"])

    site_data = tmp_path / "site-data"
    shutil.copytree(DATA_DIR, site_data)
    save_json(site_data / "311" / "scorecard.json", sc)
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=site_data, now=BUILT_AT)
    scorecard = (out / "311" / "index.html").read_text()
    assert "by category.</" not in scorecard and "open, by category." in scorecard
    assert 'id="wards"' not in scorecard and "by-ward.csv" not in scorecard and "Precinct" not in scorecard
    assert not (out / "311" / "ward").exists() and not (out / "311" / "data" / "by-ward.csv").exists()
    category = next((out / "311" / "category").glob("*/index.html")).read_text()
    assert 'id="wards"' not in category and "By ward" not in category
    methodology = (out / "311" / "methodology" / "index.html").read_text()
    assert "ward boundaries" not in methodology and "Ward differences" not in methodology
    assert "Ward maps:" not in (out / "about" / "index.html").read_text()
    officials_page = (out / "officials" / "index.html").read_text()
    assert "Find your ward" not in officials_page and "data-gap=" not in officials_page


def test_a_town_names_its_areas(config):
    # [town] areas names them; [seeclickfix] areas = "precincts", where towns without wards first said so, still does.
    from pipeline import officials
    assert officials.areas(config) == "wards"
    config["town"]["areas"] = "districts"
    assert officials.areas(config) == "districts"
    config["town"]["areas"] = "neighborhoods"
    with pytest.raises(SystemExit, match='must be "wards", "precincts" or "districts"'):
        officials.areas(config)
    del config["town"]["areas"]
    config["seeclickfix"]["areas"] = "precincts"
    assert officials.areas(config) == "precincts"
    config["town"]["areas"] = "precincts"
    assert officials.areas(config) == "precincts"
    config["town"]["areas"] = "districts"
    with pytest.raises(SystemExit, match="differ"):
        officials.areas(config)
    del config["town"]["areas"]
    config["seeclickfix"]["areas"] = "districts"
    with pytest.raises(SystemExit, match=r'must be "wards" or "precincts", not \'districts\' \(districts are \[town\] areas\)'):
        officials.areas(config)


def reader_text(page: str) -> str:
    """What a reader sees or hears of a page: its text, title, description, labels and the ward map's
    messages, without its addresses, ids and class names, which say "ward" whatever a town calls its areas."""
    said = [page.split("<body", 1)[1], *re.findall(r'<title>(.*?)</title>', page),
            *re.findall(r'<meta (?:name="description"|property="og:(?:title|description)") content="([^"]*)"', page),
            *re.findall(r'aria-label="([^"]*)"', page)]
    said += [re.sub(r"\{\w+\}", "", text) for strings in re.findall(r"data-strings='([^']*)'", page)
             for text in json.loads(html.unescape(strings)).values()]
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", " ".join(said))))


def test_a_town_whose_areas_are_districts(config, tmp_path, monkeypatch):
    # Portland, Maine calls its council districts districts ([town] areas): its 311, About and Officials
    # pages say "District 3", in English and Spanish, and the Officials page finds a reader's district from
    # the same file. Only the words change: the data, the pages' addresses and the downloads still say ward.
    from conftest import BUILT_AT, DATA_DIR
    from pipeline import build_site, officials
    config["town"]["areas"] = "districts"
    config["site"]["languages"] = ["en", "es"]
    # The town's own text says districts too, as Portland's does, with its Spanish ([strings.es]).
    for section in config["sections"]:
        section["summary"] = section["summary"].replace("ward", "district")
    for body in config["officials"]["bodies"]:
        body["note"] = body.get("note", "").replace("by ward", "by district")
        for m in body["members"]:
            m["seat"] = m["seat"].replace("Ward", "District")
    config["strings"] = {"es": {
        "Five councilors elected by district and four at-large.": "Cinco concejales elegidos por distrito y cuatro por toda la ciudad.",
        "How fast the city responds to service requests, by category and district.":
            "Qué tan rápido responde la ciudad a las solicitudes de servicio, por categoría y distrito.",
        "The mayor, City Council, and School Committee, by office and by district, and how to reach them.":
            "El alcalde, el Concejo Municipal y el Comité Escolar, por cargo y por distrito, y cómo comunicarse con ellos.",
    }}
    assert officials.wards_file(config) == config["seeclickfix"]["precincts_file"]
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=DATA_DIR, now=BUILT_AT)

    def page(url):
        return (out / url.lstrip("/") / "index.html").read_text(encoding="utf-8")

    ward = page("/311/ward/1/")
    assert "<h1>District 1</h1>" in ward and "<title>Gloucester, MA 311 requests in District 1</title>" in ward
    assert "Districts use the 2022 boundaries from MassGIS." in ward and 'href="/311/data/ward-1.csv"' in ward
    scorecard = page("/311/")
    assert "Requests per 1,000 residents, by district" in scorecard and '<a href="/311/ward/1/">District 1</a>' in scorecard
    assert "for requests outside the districts" in scorecard and "by category and district." in scorecard
    assert (out / "311" / "data" / "by-ward.csv").exists()
    methodology = page("/311/methodology/")
    assert "2022 MassGIS district boundaries" in methodology and "District differences reflect" in methodology
    about = page("/about/")
    assert "<strong>District maps:</strong>" in about and 'If you use "Find my district,"' in about
    officials_page = page("/officials/")
    assert "<h2 id=\"wards\">Find your district</h2>" in officials_page and "<h3>District 1</h3>" in officials_page
    assert '"ward": "District {ward}"' in officials_page and "Find my district using my location" in officials_page
    assert 'aria-label="Map of Gloucester\'s districts"' in officials_page
    assert '(Districts <a href="#ward-1">1</a> and <a href="#ward-2">2</a>)' in officials_page
    for url in ("/311/ward/1/", "/311/", "/311/methodology/", "/about/", "/officials/"):
        said = reader_text(page(url))
        assert "District" in said and not re.search(r"\b(?:wards?|precincts?)\b", said, re.I), url

    # In Spanish, a district is a distrito: never the English, nor a ward's or a precinct's other words.
    ward = page("/es/311/ward/1/")
    assert "<h1>Distrito 1</h1>" in ward and "Los distritos usan los límites de 2022 de MassGIS." in ward
    assert '<a href="/es/311/ward/1/">Distrito 1</a>' in page("/es/311/")
    assert "límites de distritos de 2022 de MassGIS" in page("/es/311/methodology/")
    assert "<strong>Mapas de distritos:</strong>" in page("/es/about/") and "Buscar mi distrito" in page("/es/about/")
    officials_page = page("/es/officials/")
    assert "Busque su distrito" in officials_page and "<h3>Distrito 1</h3>" in officials_page
    assert '"ward": "Distrito {ward}"' in officials_page
    assert '(Distritos <a href="#ward-1">1</a> y <a href="#ward-2">2</a>)' in officials_page
    for url in ("/es/311/ward/1/", "/es/311/", "/es/311/methodology/", "/es/about/", "/es/officials/"):
        said = reader_text(page(url))
        assert "distrito" in said.lower() and not re.search(r"\b(?:wards?|districts?|precincts?|precintos?|barrios?)\b", said, re.I), url


def test_a_town_with_wards_says_ward(site_dir, config):
    # Without [town] areas, a town's areas are wards, as before districts could be named.
    from pipeline import officials
    assert officials.areas(config) == "wards"
    ward = (site_dir / "311" / "ward" / "1" / "index.html").read_text()
    assert "<h1>Ward 1</h1>" in ward and "Wards use the 2022 boundaries from MassGIS." in ward
    officials_page = (site_dir / "officials" / "index.html").read_text()
    assert "Find your ward" in officials_page and '"ward": "Ward {ward}"' in officials_page
    assert "<h3>Ward 1</h3>" in officials_page and "(Wards <a" in officials_page
    about = (site_dir / "about" / "index.html").read_text()
    assert "<strong>Ward maps:</strong>" in about and 'If you use "Find my ward,"' in about
    for path in ("311/ward/1", "311", "311/methodology", "about", "officials"):
        said = reader_text((site_dir / path / "index.html").read_text())
        assert not re.search(r"\b(?:Districts?|Precincts?) (?:\d|\{ward\})|Find (?:your|my) (?:district|precinct)", said), path


def test_wards_are_in_number_order():
    from pipeline.compute_311 import ward_order
    assert sorted(["10", "2", "outside", "1", "12", "3"], key=ward_order) == ["1", "2", "3", "10", "12", "outside"]


def test_block_address_keeps_only_the_hundred_block():
    from pipeline.seeclickfix import block_address
    assert block_address("67 Middle St Gloucester, Massachusetts, 01930", "Gloucester") == "1–99 Middle St"
    assert block_address("262 Main Street Apt 4", "Gloucester") == "200–299 Main Street"
    assert block_address("1895 S Willow St", "Manchester") == "1800–1899 S Willow St"
    assert block_address("144–172 Frontage Rd", "Manchester") == "100–199 Frontage Rd"
    assert block_address("105R Washington St #2", "Gloucester") == "100–199 Washington St"
    assert block_address("25 1/2 Cleveland St", "Gloucester") == "1–99 Cleveland St"
    assert block_address("125½ Cleveland St", "Gloucester") == "100–199 Cleveland St"
    # An intersection or a landmark has no house number to hide.
    assert block_address("Bray St & Salt Marsh Ln", "Gloucester") == "Bray St & Salt Marsh Ln"
    assert block_address("Heritage Trail", "Manchester") == "Heritage Trail"
    assert block_address("01930", "Gloucester") == ""


def test_sensitive_categories():
    from pipeline.seeclickfix import sensitive
    for c in ("Homeless Encampment", "Health Department (Housing) - Internal", "Police Department (Non-Emergency)",
              "Problem Property", "Private Property Issue", "Fire - Smoke Detector Request", "Animal - Lost or Missing Pet",
              "Water - Lead Service Inspection", "Noise/ Business and Construction activity"):
        assert sensitive(c), c
    for c in ("Pothole", "Trash - Missed Pickup", "Street Light Issue", "Animal - Dead Animal"):
        assert not sensitive(c), c
    assert sensitive("Animal Issues", ["Animal Issues"])


def test_sensitive_requests_are_shown_to_the_block():
    """A sensitive category's requests are still listed and mapped, with the address to the block and
    the map point to about 100 meters; other categories keep the address SeeClickFix shows."""
    encampment = {**request("1", "2026-09-01T09:00:00-04:00", lat=42.615432, lng=-70.660876, status="open",
                            category="Homeless Encampment"), "sensitive": True}
    pothole = request("2", "2026-09-01T10:00:00-04:00", lat=42.615432, lng=-70.660876, status="open")
    recent = compute_311.recent_open([encampment, pothole], FETCHED_AT, 30, "https://seeclickfix.com/issues", "Gloucester")
    shown = {r["id"]: r for r in recent}
    assert shown["1"]["address"] == "1–99 Main St" and (shown["1"]["lat"], shown["1"]["lng"]) == (42.615, -70.661)
    assert shown["2"]["address"] == "12 Main St" and (shown["2"]["lat"], shown["2"]["lng"]) == (42.6154, -70.6609)
    oldest = compute_311.oldest_open([encampment], FETCHED_AT, "https://seeclickfix.com/issues", "Gloucester")
    assert oldest[0]["address"] == "1–99 Main St"
    again = {**encampment, "id": "3", "created_at": "2026-05-01T09:00:00-04:00", "status": "closed",
             "updated_at": "2026-05-02T09:00:00-04:00", "detail": {"closed_at": "2026-05-02T09:00:00-04:00"}}
    places = compute_311.repeat_locations([again, {**encampment, "created_at": "2026-05-10T09:00:00-04:00"}], 50, 60,
                                          "https://seeclickfix.com/issues", "Gloucester")
    assert places[0]["address"] == "1–99 Main St" and (places[0]["lat"], places[0]["lng"]) == (42.615, -70.661)


def test_scorecard_cuts_sensitive_addresses_and_keeps_the_store(config, data):
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=500)
    store = load(data)
    config["seeclickfix"]["sensitive_categories"] = sorted({r["category"] for r in store.values()})
    sc = compute_311.compute(config, data, now=FETCHED_AT)
    shown = [r["address"] for r in sc["recent_open"]["requests"] + sc["backlog"]["oldest"] if r["address"]]
    assert shown and all("–" in a or not a[0].isdigit() for a in shown)
    # requests.json keeps what SeeClickFix lists.
    assert load(data) == store
