"""The Officials page's address box: the Census Bureau's address ranges, each given its ward
(pipeline/fetch_addresses.py), and what the page makes of an address."""

import json
import shutil
from datetime import timedelta

import pytest
from conftest import FETCHED_AT
from fakes import FIXTURES, FakeCensus

from pipeline import fetch_addresses as fa
from pipeline.config import load_config
from pipeline.http import FetchError

PLACES = ("Gloucester", "MA", "Massachusetts")


@pytest.fixture
def town(tmp_path, monkeypatch, data_dir):
    """A data folder with the tests' ward file, and the county downloads kept apart."""
    monkeypatch.setattr(fa, "DOWNLOADS", tmp_path / "downloads")
    shutil.copytree(data_dir / "static", tmp_path / "data" / "static")
    return load_config("gloucester"), tmp_path / "data"


@pytest.fixture(scope="module")
def ranges(data_dir):
    return json.loads((data_dir / fa.FILE).read_text())


def test_reads_the_census_shapefile():
    records = fa.read_shapefile((FIXTURES / "addrfeat_gloucester.zip").read_bytes())
    assert len(records) == 202
    row, line = next((r, line) for r, line in records if r["FULLNAME"] == "Rowe Sq")
    assert {"LFROMHN", "LTOHN", "RFROMHN", "RTOHN", "PARITYL", "PARITYR"} <= set(row)
    assert len(line) >= 2 and all(-70.8 < x < -70.5 and 42.5 < y < 42.7 for x, y in line)


def test_street_names_are_written_one_way():
    assert fa.street_key("North Main Street") == fa.street_key("N. Main St") == "N MAIN ST"
    assert fa.street_key("Mt Vernon Street") == fa.street_key("Mount Vernon St") == "MT VERNON ST"
    assert fa.street_key("St. Louis Avenue") == fa.street_key("Saint Louis Ave") == "ST LOUIS AVE"
    assert fa.street_key("First Street") == fa.street_key("1st St") == "1ST ST"
    assert fa.base_key("N MAIN ST") == "MAIN" and fa.base_key("N ST") == "N" and fa.base_key("MAIN") == "MAIN"


@pytest.mark.parametrize("text, expected", [
    ("12 Main St", (12, "MAIN ST")),
    ("12A Main St., Apt 3, Gloucester, MA 01930", (12, "MAIN ST")),
    ("12 Main Street Gloucester Massachusetts 01930 USA", (12, "MAIN ST")),
    ("12-14 Main St #2", (12, "MAIN ST")),
    ("12 1/2 Main St", (12, "MAIN ST")),
    # A single letter after the number is a street's name, not part of the number.
    ("70 S Main St", (70, "S MAIN ST")),
    ("65 A St", (65, "A ST")),
    ("Main Street", (None, "MAIN ST")),
    # "Floral" and "Sterling" aren't a floor or a suite.
    ("29 Floral Ave", (29, "FLORAL AVE")),
    ("8 Sterling St", (8, "STERLING ST")),
    ("12 Elm St Manchester New Hampshire", (12, "ELM ST")),
])
def test_reads_an_address(text, expected):
    assert fa.parse(text, PLACES + ("Manchester", "NH", "New Hampshire")) == expected


def test_side_split_where_it_crosses_into_another_ward(tmp_path):
    """A straight street, 400 meters east, crossing from ward 1 into ward 2 halfway, numbered 1 to 99
    on the left: numbers near the crossing get both wards, the rest their own, and the right side
    of a street that is itself a ward line is in the ward on its side."""
    def square(x0, x1, y0, y1):
        return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}
    lon0, lat0 = -70.70, 42.60
    dx = 400 / (111_320 * 0.7373)
    wards = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"ward": 1}, "geometry": square(lon0 - 0.01, lon0 + dx / 2, lat0, lat0 + 0.01)},
        {"type": "Feature", "properties": {"ward": 2}, "geometry": square(lon0 + dx / 2, lon0 + 0.01, lat0, lat0 + 0.01)},
        {"type": "Feature", "properties": {"ward": 3}, "geometry": square(lon0 - 0.01, lon0 + 0.01, lat0 - 0.01, lat0)},
    ]}
    path = tmp_path / "wards.geojson"
    path.write_text(json.dumps(wards))
    ward_at, _ = fa.ward_finder(path)
    line = [(lon0, lat0), (lon0 + dx, lat0)]
    left = fa.place_side(fa.Side(line, 1), ward_at, 1, 99, 1)
    assert [r[2] for r in left] == [frozenset({"1"}), frozenset({"1", "2"}), frozenset({"2"})]
    assert left[0][:2] == (1, left[0][1]) and left[-1][1] == 99
    # The crossing is at 50; AMBIGUOUS_M (25 meters of 400) either side is about 6 numbers.
    assert 41 <= left[1][0] <= 45 and 55 <= left[1][1] <= 59
    # The street runs along the line between wards 1-2 (north) and 3 (south): its right side is in 3.
    assert fa.place_side(fa.Side(line, -1), ward_at, 2, 98, 2) == [(2, 98, frozenset({"3"}))]
    # Joined: the left side's three runs, in order, wards named.
    joined = fa.join([(1, *r) for r in left])
    assert [r[3] for r in joined] == ["1", ["1", "2"], "2"]


def test_fetch_makes_the_towns_file(town):
    config, data_dir = town
    client = FakeCensus()
    result = fa.run(config, client, data_dir, now=FETCHED_AT)
    assert result["year"] == 2026 and result["counties"] == ["25009"] and result["streets"] == 14
    saved = json.loads((data_dir / fa.FILE).read_text())
    assert saved["wards_file"] == "gloucester-precincts-2022.geojson"
    assert saved["source_urls"] == ["https://www2.census.gov/geo/tiger/TIGER2026/ADDRFEAT/tl_2026_25009_addrfeat.zip"]
    # Rockport's Main St, in the same county file, is left out: none of its numbers are in a ward.
    assert all(r[1] < 1000 for r in saved["streets"]["Main St"])
    assert saved["streets"]["Rowe Sq"] == [[1, 99, 1, "2"], [2, 98, 2, "1"]]
    # Asked once a month at most, then for next year's file only.
    assert "skipped" in fa.run(config, FakeCensus(), data_dir, now=FETCHED_AT + timedelta(days=5))
    later = FakeCensus()
    assert "skipped" in fa.run(config, later, data_dir, now=FETCHED_AT + timedelta(days=40))
    assert not any("ADDRFEAT" in u for u in later.urls)
    next_year = FakeCensus()
    result = fa.run(config, next_year, data_dir, now=FETCHED_AT.replace(year=2027, month=2))
    assert "still the newest" in result["skipped"]
    assert [u for u in next_year.urls if "ADDRFEAT" in u] == [fa.ADDRFEAT_URL.format(year=2027, county="25009")]
    assert json.loads((data_dir / fa.FILE).read_text())["checked_at"].startswith("2027-02")
    published = FakeCensus(years=(2026, 2027))
    assert fa.run(config, published, data_dir, now=FETCHED_AT.replace(year=2027, month=4))["year"] == 2027


def test_no_published_year_is_an_error(town):
    config, data_dir = town
    with pytest.raises(FetchError, match="no address ranges for county 25009"):
        fa.run(config, FakeCensus(years=()), data_dir, now=FETCHED_AT)
    assert not (data_dir / fa.FILE).exists()


def test_a_new_ward_file_makes_it_again_and_the_build_waits(town):
    config, data_dir = town
    fa.run(config, FakeCensus(), data_dir, now=FETCHED_AT)
    assert fa.load(data_dir, config) is not None
    path = data_dir / "static" / "gloucester-precincts-2022.geojson"
    wards = json.loads(path.read_text())
    wards["features"][0]["properties"]["population_2020"] += 1
    path.write_text(json.dumps(wards))
    # Ranges made with another ward file could name the wrong wards: the page leaves the box out.
    assert fa.load(data_dir, config) is None
    assert fa.run(config, FakeCensus(), data_dir, now=FETCHED_AT + timedelta(days=1))["year"] == 2026
    assert fa.load(data_dir, config) is not None


def test_a_town_without_ward_seats_has_no_file(town):
    config, data_dir = town
    for body in config["officials"]["bodies"]:
        for m in body["members"]:
            m.pop("ward", None), m.pop("wards", None)
    client = FakeCensus()
    assert fa.run(config, client, data_dir, now=FETCHED_AT) == {"skipped": "no ward seats"}
    assert not client.urls and not (data_dir / fa.FILE).exists()


@pytest.mark.parametrize("text, street, wards, how", [
    ("12 Main St", "Main St", ["2"], "number"),
    ("12 Main Street, Gloucester, MA 01930", "Main St", ["2"], "number"),
    ("401 main st", "Main St", ["1"], "number"),
    # Odd numbers at the street's start are close to a ward line: both wards, never a guess.
    ("5 Main St", "Main St", ["2", "3"], "number"),
    # Each side of a street that is a ward line is in its own ward.
    ("3 Rowe Sq", "Rowe Sq", ["2"], "number"),
    ("4 Rowe Sq", "Rowe Sq", ["1"], "number"),
    ("12 Mt Vernon Street", "Mount Vernon St", ["2"], "number"),
    ("12 St. Louis Avenue", "Saint Louis Ave", ["1"], "number"),
    # No number: the street's one ward, or the wards it runs through.
    ("Pleasant Street", "Pleasant St", ["2"], "street"),
    ("Washington St", "Washington St", ["2", "3", "4"], "street"),
    # A number the ranges don't have.
    ("5000 Washington St", "Washington St", ["2", "3", "4"], "no number"),
    # The street with other directions, or a place name after it.
    ("12 Kilby St North", "N Kilby St", ["4"], "number"),
    ("12 Elm St Rockport", "Elm St", ["2"], "number"),
])
def test_finds_the_ward(ranges, text, street, wards, how):
    assert fa.find(ranges, text, PLACES) == {"street": street, "wards": wards, "how": how, "matches": []}


@pytest.mark.parametrize("text, matches", [
    # Another type, or two streets of that name: offered, never taken.
    ("12 Elm Ave", ["Elm St"]),
    ("12 Kilby St", ["N Kilby St", "S Kilby St"]),
    ("Main", ["E Main St", "Main St"]),
    # A spelling a letter off.
    ("15 Harriet Road", ["Harriett Rd"]),
])
def test_offers_near_matches(ranges, text, matches):
    assert fa.find(ranges, text, PLACES) == {"street": None, "wards": [], "how": "choose", "matches": matches}


def test_no_such_street(ranges):
    assert fa.find(ranges, "12 Nowhere Lane", PLACES)["how"] == "none"


def test_page_has_the_address_box(site_dir, ranges):
    page = (site_dir / "officials" / "index.html").read_text()
    assert 'id="address"' in page and 'data-index="/officials/addresses.json?v=' in page
    assert "/static/js/address.js?v=" in page
    # Shown by its script: without one, the page is the map and the list.
    assert 'class="address-box" id="address"' in page and "hidden>" in page.split('id="address"', 1)[1].split("\n", 1)[0]
    assert '<label for="address-q">Your street address</label>' in page
    assert 'role="status" aria-live="polite"' in page.split('id="address-result"', 1)[1].split(">", 1)[0]
    # The members elected citywide, for the script to add to a ward's own.
    template = page.split('<template id="citywide-members">', 1)[1].split("</template>", 1)[0]
    assert "Mayor: <a href=\"#mayor-morgan-example\">" in template
    assert "City Council (At-large): <a href=\"#city-council-finley-example\">" in template
    assert "Avery Example" not in template
    data = json.loads((site_dir / "officials" / "addresses.json").read_text())
    assert data["streets"] == ranges["streets"] and data["words"]["STREET"] == "ST" and "AVE" in data["types"]
    about = (site_dir / "about" / "index.html").read_text()
    assert "TIGER/Line address ranges<span" in about and ", 2026, each matched by Publick to the boundaries above" in about
    assert "If you look up your address on Who represents you, it's looked up on your device." in about
    streets = (site_dir / "streets" / "index.html").read_text()
    assert 'data-ward-page="/officials/"' in streets and "Which ward is {street} in?" in streets

