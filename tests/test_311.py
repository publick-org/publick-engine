"""Tests for the 311 pipeline, run offline against saved SeeClickFix responses."""

import json
import shutil
from datetime import datetime

import pytest

from conftest import FETCHED_AT, REAL_DATA_DIR
from fakes import FakeSeeClickFix
from pipeline import compute_311, fetch_311
from pipeline.config import load_config
from pipeline.geo import PrecinctLookup


@pytest.fixture
def config():
    return load_config("gloucester")


@pytest.fixture
def data(tmp_path):
    shutil.copytree(REAL_DATA_DIR / "static", tmp_path / "static")
    return tmp_path


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


def test_run_fills_exact_times_up_to_limit(config, data):
    summary = fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=10)
    assert summary["details_fetched"] == 10
    assert summary["details_missing"] == 104
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=500)
    assert all(r["detail"] for r in load(data).values())


def test_request_leaving_open_list_is_rechecked(config, data):
    first = FakeSeeClickFix()
    fetch_311.run(config, first, data, now=FETCHED_AT, detail_limit=500)
    closed_id = str(first.open_items[0]["service_request_id"])
    # The city closed it: it leaves the open list, and the lookup reports it closed.
    now_closed = dict(first.open_items[0], status="closed")
    later = FakeSeeClickFix(open_items=first.open_items[1:], window_items=first.window_items + [now_closed])
    fetch_311.run(config, later, data, now=FETCHED_AT.replace(day=27), detail_limit=500)
    assert any(u.endswith(f"/issues/{closed_id}") for u in later.urls), "closed request should be looked up again"
    assert load(data)[closed_id]["status"] == "closed"


def test_removed_request_is_kept_out(config, data):
    source = FakeSeeClickFix()
    gone = str(source.open_items[0]["service_request_id"])
    fetch_311.run(config, FakeSeeClickFix(missing_ids={gone}), data, now=FETCHED_AT, detail_limit=500)
    assert load(data)[gone]["removed"] is True
    scorecard = compute_311.compute(config, data, now=FETCHED_AT)
    assert scorecard["overall"]["received"] == 113


def test_private_request_is_kept_out(config, data):
    """A 403 for one request, with lookups after it working, is a request made private."""
    private = str(FakeSeeClickFix().open_items[0]["service_request_id"])
    fetch_311.run(config, FakeSeeClickFix(refused_ids={private}), data, now=FETCHED_AT, detail_limit=500)
    store = load(data)
    assert store[private]["removed"] is True
    assert sum(1 for r in store.values() if r.get("removed")) == 1


def test_refused_lookups_stop_the_step_and_remove_nothing(config, data):
    """SeeClickFix turning every lookup away is a block, not a city's worth of deleted
    requests: the step fails, nothing is marked removed, and what was listed is kept."""
    with pytest.raises(fetch_311.FetchError, match="refused 3 lookups in a row"):
        fetch_311.run(config, FakeSeeClickFix(refuse_all=True), data, now=FETCHED_AT, detail_limit=500)
    store = load(data)
    assert len(store) == 114 and not any(r.get("removed") for r in store.values())
    status = json.loads((data / "311" / "status.json").read_text())
    assert any("HTTP 403" in e for e in status["errors"])
    # The block lifts: the next run fills in the details as usual.
    fetch_311.run(config, FakeSeeClickFix(), data, now=FETCHED_AT, detail_limit=500)
    assert all(r["detail"] for r in load(data).values())


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


def test_311_areas_must_be_wards_or_precincts(config):
    from pipeline import officials
    config["seeclickfix"]["areas"] = "districts"
    with pytest.raises(SystemExit, match='must be "wards" or "precincts"'):
        officials.precincts(config)


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
