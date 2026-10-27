"""Tests for 311 requests from a city's Socrata open data portal ([socrata_311]), run offline
against real rows of Somerville's dataset (tests/fixtures/socrata_311_rows.json)."""

import copy
import json
import re
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import BUILT_AT, DATA_DIR
from fakes import FakeSocrata, socrata_rows
from pipeline import build_site, compute_311, fetch_socrata_311, socrata, source_311, update
from pipeline.config import load_config
from pipeline.http import FetchError

TZ = ZoneInfo("America/New_York")
# Just after the saved rows were read: the newest was created on October 26, 2026.
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=TZ)
SERVICE = [r for r in socrata_rows() if r["classification"] == "Service"]
WARDS_FILE = "test-wards.geojson"


def portal_config(config: dict) -> dict:
    """Gloucester's config with Somerville's portal for its 311 requests instead of SeeClickFix."""
    config = copy.deepcopy(config)
    del config["seeclickfix"]
    config["socrata_311"] = {"domain": "data.somervillema.gov", "dataset": "4pyi-uqq6", "since": "2025-01",
                             "publisher": "City of Somerville", "classes": ["Service"], "wards_file": WARDS_FILE,
                             "license": "ODbL", "license_url": "https://opendatacommons.org/licenses/odbl/1-0/"}
    socrata.check(config)
    return config


@pytest.fixture
def config():
    return portal_config(load_config("gloucester"))


@pytest.fixture
def data(tmp_path):
    # Seven wards with their 2020 populations, as a ward file gives them (no shapes: nothing is looked up in it).
    static = tmp_path / "static"
    static.mkdir()
    features = [{"type": "Feature", "geometry": None, "properties": {"ward": str(w), "precinct": "1", "district": f"{w}-1",
                                                                       "population_2020": 11000 + 100 * w}} for w in range(1, 8)]
    (static / WARDS_FILE).write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return tmp_path


def load(data_dir):
    return json.loads((data_dir / "311" / "requests.json").read_text())


def test_service_requests_are_saved_as_requests(config, data):
    summary = fetch_socrata_311.run(config, FakeSocrata(), data, now=NOW)
    store = load(data)
    # Only service requests, not calls for information or feedback.
    assert len(store) == len(SERVICE) == summary["requests_total"] == summary["new"]
    assert summary["caught_up"] and summary["pages"] == 1
    row = next(r for r in SERVICE if r["most_recent_status"] == "Closed")
    record = store[row["id"]]
    assert record["status"] == "closed" and record["status_detail"] == "Closed"
    # The city's local times, with the town's offset; a closed request's status date is when it closed.
    assert record["created_at"] == row["date_created"].replace(" ", "T") + record["created_at"][19:]
    assert record["created_at"][19:] in ("-04:00", "-05:00")
    assert record["updated_at"].startswith(row["most_recent_status_date"].replace(" ", "T"))
    assert compute_311.closed_time(record).isoformat() == record["updated_at"]
    assert record["category"] == row["category"].strip() and record["type"] == row["type"] and record["ward"] == row["ward"]
    # In Progress and On Hold are open.
    assert {store[r["id"]]["status"] for r in SERVICE if r["most_recent_status"] in ("In Progress", "On Hold")} == {"open"}
    # The city records no ward for some: none is looked up.
    assert any(r["ward"] is None for r in store.values())
    # Nothing the portal doesn't have, and no census block.
    assert not any({"lat", "lng", "address", "detail", "block_code"} & set(r) for r in store.values())
    status = json.loads((data / "311" / "status.json").read_text())
    assert status["portal_updated_at"] == max(r[":updated_at"] for r in SERVICE)


def test_the_query_reads_the_towns_classes_since_its_first_month(config, data):
    client = FakeSocrata()
    fetch_socrata_311.run(config, client, data, now=NOW)
    url = client.urls[0]
    assert url.startswith("https://data.somervillema.gov/resource/4pyi-uqq6.json?")
    assert "date_created+%3E%3D+%272025-01-01%27" in url and "classification+in+%28%27Service%27%29" in url
    assert "%24order=%3Aupdated_at%2C+id" in url


def test_a_run_reads_only_what_changed_since_the_last(config, data):
    rows = socrata_rows()
    client = FakeSocrata(rows)
    fetch_socrata_311.run(config, client, data, now=NOW)
    opened = next(r for r in rows if r["classification"] == "Service" and r["most_recent_status"] == "Open")
    opened.update({"most_recent_status": "Closed", "most_recent_status_date": "2026-10-27 09:15:00",
                   ":updated_at": "2026-10-28T06:04:00.000Z"})
    summary = fetch_socrata_311.run(config, client, data, now=NOW.replace(day=28))
    assert summary["rows_read"] == 1 and summary["changed"] == 1 and summary["new"] == 0
    record = load(data)[opened["id"]]
    assert record["status"] == "closed" and record["updated_at"] == "2026-10-27T09:15:00-04:00"
    # Nothing new: the last page read again is empty.
    assert fetch_socrata_311.run(config, client, data, now=NOW.replace(day=29))["rows_read"] == 0


def test_a_long_history_is_read_over_runs_from_the_cursor(config, data, monkeypatch):
    monkeypatch.setattr(fetch_socrata_311, "PAGE_SIZE", 20)
    client = FakeSocrata()
    first = fetch_socrata_311.run(config, client, data, now=NOW, max_pages=3)
    assert first["rows_read"] == 60 and not first["caught_up"] and first["portal_updated_at"] is None
    sync = json.loads((data / "311" / "sync.json").read_text())
    assert sync["cursor"]["id"] == str(sorted(SERVICE, key=lambda r: (r[":updated_at"], int(r["id"])))[59]["id"])
    second = fetch_socrata_311.run(config, client, data, now=NOW, max_pages=100)
    assert second["caught_up"] and second["new"] == len(SERVICE) - 60 and len(load(data)) == len(SERVICE)


def test_a_row_rewritten_while_the_run_reads_moves_none_out_of_its_page(config, data, monkeypatch):
    """The city's load rewrites a row already read: it's read again at the end, and no other row is
    skipped, as one would be by offset paging."""
    monkeypatch.setattr(fetch_socrata_311, "PAGE_SIZE", 20)
    rows = socrata_rows()
    first = min((r for r in rows if r["classification"] == "Service"), key=lambda r: (r[":updated_at"], int(r["id"])))

    def load_runs(portal):
        if len(portal.urls) == 2:
            first.update({"most_recent_status": "Closed", "most_recent_status_date": "2026-10-27 08:00:00",
                          ":updated_at": "2026-10-27T10:04:00.000Z"})
    fetch_socrata_311.run(config, FakeSocrata(rows, on_page=load_runs), data, now=NOW)
    store = load(data)
    assert len(store) == len(SERVICE) and store[first["id"]]["status"] == "closed"


def test_a_failed_read_keeps_what_was_read_and_fails_the_step(config, data, monkeypatch):
    monkeypatch.setattr(fetch_socrata_311, "PAGE_SIZE", 20)

    def fail_third(portal):
        portal.failing = len(portal.urls) == 3
    with pytest.raises(FetchError, match="503"):
        fetch_socrata_311.run(config, FakeSocrata(on_page=fail_third), data, now=NOW)
    assert len(load(data)) == 40
    status = json.loads((data / "311" / "status.json").read_text())
    assert status["errors"] and not status["caught_up"]
    # The next run goes on from the last row saved.
    assert fetch_socrata_311.run(config, FakeSocrata(), data, now=NOW)["new"] == len(SERVICE) - 40


def test_an_earlier_first_month_reads_the_months_between_once(config, data):
    config["socrata_311"]["since"] = "2026-01"
    client = FakeSocrata()
    fetch_socrata_311.run(config, client, data, now=NOW)
    later = {r["id"] for r in SERVICE if r["date_created"] >= "2026-01-01"}
    assert set(load(data)) == later
    config["socrata_311"]["since"] = "2025-01"
    summary = fetch_socrata_311.run(config, client, data, now=NOW)
    assert summary["new"] == len(SERVICE) - len(later) and len(load(data)) == len(SERVICE)
    assert json.loads((data / "311" / "sync.json").read_text())["backfill"] is None
    backfill = client.urls[-1]
    assert "date_created+%3C+%272026-01-01%27" in backfill and "%24order=id" in backfill
    # Read once: the next run reads only what changed.
    assert fetch_socrata_311.run(config, client, data, now=NOW)["rows_read"] == 0


def test_missing_saved_requests_stop_the_run(config, data):
    fetch_socrata_311.run(config, FakeSocrata(), data, now=NOW)
    (data / "311" / "requests.json").unlink()
    with pytest.raises(FetchError, match="saved 311 requests are missing"):
        fetch_socrata_311.run(config, FakeSocrata(), data, now=NOW)


@pytest.mark.parametrize("change, message", [
    ({"since": "2025"}, "since must be a month"),
    ({"domain": "https://data.somervillema.gov"}, "domain must be the portal's host name"),
    ({"dataset": "311"}, "dataset must be the dataset's id"),
    ({"publisher": ""}, "needs publisher"),
    ({"fields": {"address": "location"}}, "fields names address"),
    ({"fields": {"created": ""}}, "can't leave out created"),
    ({"fields": {"status": "most recent status"}}, "columns SoQL can't read"),
    ({"classes": "Service"}, "classes must be a list"),
    ({"fields": {"class": ""}}, "classes needs the class field"),
    ({"wards_file": None}, "needs wards_file"),
    ({"license_url": None}, "license and license_url go together"),
    ({"closed_statuses": []}, "closed_statuses must be a list"),
])
def test_a_table_that_cant_work_stops_the_load(config, change, message):
    config["socrata_311"].update(change)
    config["socrata_311"] = {k: v for k, v in config["socrata_311"].items() if v is not None}
    with pytest.raises(SystemExit, match=message):
        socrata.check(config)


def test_a_town_has_one_311_source(config):
    config["seeclickfix"] = load_config("gloucester")["seeclickfix"]
    with pytest.raises(SystemExit, match="also has \\[seeclickfix\\]"):
        socrata.check(config)


def test_a_dataset_without_wards(config, data):
    config["socrata_311"]["fields"] = {"ward": ""}
    del config["socrata_311"]["wards_file"]
    socrata.check(config)
    assert not source_311.has_areas(config)
    fetch_socrata_311.run(config, FakeSocrata(), data, now=NOW)
    assert not any("ward" in r for r in load(data).values())
    sc = compute_311.compute(config, data, now=NOW)
    assert sc["by_ward"] == [] and sc["wards"] == []


def test_the_scorecard_leaves_out_what_the_portal_doesnt_record(config, data):
    fetch_socrata_311.run(config, FakeSocrata(), data, now=NOW)
    sc = compute_311.compute(config, data, now=NOW)
    in_year = [r for r in SERVICE if r["date_created"] >= "2025-10-27"]
    assert sc["overall"]["received"] == len(in_year)
    assert sc["overall"]["closed"] == sum(r["most_recent_status"] == "Closed" for r in in_year)
    assert sc["overall"]["time_to_close"]["median"] is not None
    # No acknowledged times, map points, or pages to link.
    assert sc["overall"]["time_to_acknowledge"]["median"] is None and sc["overall"]["checked"] == 0
    assert "repeats" not in sc
    oldest = sc["backlog"]["oldest"][0]
    assert oldest["url"] is None and oldest["address"] == "" and oldest["type"]
    assert all(r["url"] is None and "lat" not in r for r in sc["recent_open"]["requests"])
    # The city's own wards, with those it recorded none for apart, and requests per 1,000 residents.
    wards = {w["ward"]: w for w in sc["by_ward"]}
    assert set(wards) == {*map(str, range(1, 8)), "outside"}
    assert wards["3"]["per_1000_residents"] == round(wards["3"]["received"] / 11300 * 1000, 1)
    assert wards["outside"]["per_1000_residents"] is None
    # An open request whose status hasn't changed in over a year.
    assert sc["backlog"]["no_update"]["count"] == sum(
        1 for r in SERVICE if r["most_recent_status"] != "Closed" and r["most_recent_status_date"] < "2025-10-27")


def test_the_update_runs_the_portals_steps(config):
    steps = {s.name: s for s in update.SOURCES}
    fetch = steps["Fetch 311 requests from an open data portal"]
    assert fetch.module == "pipeline.fetch_socrata_311" and fetch.tables() == ("socrata_311",)
    assert "socrata_311" in steps["Compute 311 scorecard"].tables()
    assert source_311.table(config) == "socrata_311"


# Links to this site's own pages and anchors, as site_checks/test_town_site.py checks them.
LINK = re.compile(r'href="(/[^"#?]*)(?:#([^"]*))?"')


def test_a_portal_towns_311_pages(config, data, tmp_path, monkeypatch):
    fetch_socrata_311.run(config, FakeSocrata(), data, now=BUILT_AT)
    site_data = tmp_path / "site-data"
    shutil.copytree(DATA_DIR, site_data)
    shutil.rmtree(site_data / "311")
    shutil.copytree(data / "311", site_data / "311")
    shutil.copy(data / "static" / WARDS_FILE, site_data / "static" / WARDS_FILE)
    compute_311.save_json(site_data / "311" / "scorecard.json", compute_311.compute(config, site_data, now=BUILT_AT))
    config["socrata_311"]["report_url"] = "https://www.somervillema.gov/departments/311"
    config["site"]["languages"] = ["en", "es"]
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=site_data, now=BUILT_AT)

    page = (out / "311" / "index.html").read_text()
    # Without the markup every link off the site has.
    plain = re.sub(r' class="external" target="_blank" rel="noopener"|<span class="visually-hidden"> \(opens in new tab\)</span>', "", page)
    assert "responds to the public service requests it publishes on its open data portal" in page
    assert "How quickly the City of Gloucester closes public service requests" in page
    # Nothing about acknowledging, maps, or repeat locations: the portal doesn't record them.
    assert "cknowledg" not in page and "leaflet" not in page and "map-recent" not in page
    assert 'id="repeats"' not in page and "repeat-locations" not in page and "No street address" not in page
    assert 'id="speed"' in page and "Time to close counts only the" in page
    assert "No ward recorded" in page and "Outside wards" not in page and "Per 1,000" in page
    assert 'href="https://www.somervillema.gov/departments/311"' in page
    assert ("Data from City of Somerville's open data portal, "
            '<a href="https://data.somervillema.gov/d/4pyi-uqq6">data.somervillema.gov</a>, used under the '
            '<a href="https://opendatacommons.org/licenses/odbl/1-0/">ODbL</a> license.') in plain
    assert "seeclickfix" not in page.lower()
    assert '"license":"https://opendatacommons.org/licenses/odbl/1-0/"' in page
    assert not (out / "311" / "repeat-locations").exists() and not (out / "311" / "data" / "repeat-locations.csv").exists()
    assert (out / "311" / "data" / "recent-open.csv").read_text().splitlines()[0] == "id,submitted,category,type,ward"
    assert "acknowledge" not in (out / "311" / "data" / "monthly.csv").read_text().splitlines()[0]

    category = next((out / "311" / "category").glob("*/index.html")).read_text()
    ward = (out / "311" / "ward" / "3" / "index.html").read_text()
    for p in (category, ward):
        assert "cknowledg" not in p and "No street address" not in p and "seeclickfix" not in p.lower()
    assert "Wards as the city records them with each request." in ward

    methodology = (out / "311" / "methodology" / "index.html").read_text()
    assert "Everything comes from the 311 requests City of Somerville publishes on its open data portal" in methodology
    assert "Every request since January 2025, checked daily." in methodology
    assert "no address, map location, or time a request was acknowledged" in methodology
    assert "A request whose status is Closed counts as closed" in methodology
    assert 'id="locations"' not in methodology and "Repeat location" not in methodology
    assert "seeclickfix" not in methodology.lower()

    home = (out / "index.html").read_text()
    assert "Time to close a 311 request" in home and "Time to acknowledge" not in home
    about = (out / "about" / "index.html").read_text()
    assert "<strong>311 requests:</strong> City of Somerville's" in about and "SeeClickFix" not in about
    assert "311 requests keep the city's open data license" in about
    # Not in the street lookup: the portal has no addresses.
    streets = (out / "streets" / "index.html").read_text()
    assert "<strong>311 requests:</strong>" not in streets and "and 311 requests" not in streets

    # Every link to a page of the site, and every anchor, resolves; every – in a table is explained.
    for path in (out / "311").rglob("*.html"):
        html = path.read_text()
        for href, anchor in LINK.findall(html):
            if href.startswith("/static/"):
                continue
            target = out / href.lstrip("/") / ("index.html" if href.endswith("/") else "")
            assert target.exists(), f"{path}: {href}"
            if anchor:
                assert f'id="{anchor}"' in target.read_text(), f"{path}: {href}#{anchor}"
        if '<td class="num">–' in html or "duration_cell" in html:
            assert "dash-legend" in html, path

    spanish = (out / "es" / "311" / "index.html").read_text()
    assert "Sin distrito registrado" in spanish and "Tiempo típico de cierre" in spanish and "confirm" not in spanish


def test_closed_requests_older_than_the_pages_show_are_dropped(config, data):
    """A closed request created before the monthly chart's first month, and closed over 30 days ago,
    is dropped as the requests are saved; the scorecard is the same as from every request."""
    later = datetime(2028, 3, 15, 12, 0, tzinfo=TZ)
    rows = socrata_rows()
    # One old request closed only last week: it was open a week ago, so it's kept for now.
    recent_close = next(r for r in rows if r["classification"] == "Service" and r["date_created"] < "2026-04-01"
                        and r["most_recent_status"] == "Closed")
    recent_close.update(most_recent_status_date="2028-03-10 09:00:00", **{":updated_at": "2028-03-10T10:04:00.000Z"})
    everything = data / "everything"
    shutil.copytree(data, everything)
    fetch_socrata_311.run(config, FakeSocrata(rows), everything, now=later, max_pages=0)
    fetch_socrata_311.run(config, FakeSocrata(rows), data, now=later)
    store, start = load(data), compute_311.chart_start(later).date().isoformat()
    dropped = [r for r in SERVICE if r["id"] not in store]
    assert dropped and json.loads((data / "311" / "status.json").read_text())["pruned"] == len(dropped)
    assert all(r["date_created"] < start and r["most_recent_status"] == "Closed" for r in dropped)
    assert recent_close["id"] in store
    # Old requests still open are kept, however old.
    assert all(r["id"] in store for r in SERVICE if r["most_recent_status"] != "Closed")

    # Every request, read without pruning, against the requests kept: the same scorecard.
    unpruned = {r["id"]: socrata.parse_row(r, socrata.fields(config["socrata_311"]), {"Closed"}, TZ) for r in rows
                if r["classification"] == "Service"}
    compute_311.save_json(everything / "311" / "requests.json", unpruned)
    full = compute_311.compute(config, everything, now=later)
    kept = compute_311.compute(config, data, now=later)
    for card in (full, kept):
        card.pop("requests_recorded"), card.pop("fetched_at")
    assert kept == full and kept["data_since"] == max("2025-01-01", start)
