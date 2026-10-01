"""Tests for the Census building permits a town's housing page shows, offline."""

import pytest

from conftest import FIXTURES
from pipeline import fetch_housing

PLACE_FILE = (FIXTURES / "bps_ne2025a.txt").read_text()


def test_a_city_is_found_by_its_place():
    found = fetch_housing.parse_bps(PLACE_FILE, "33", "45140")
    assert found == {"units": 222, "by_size": {"1 unit": 24, "2 units": 12, "3-4 units": 18, "5+ units": 168},
                     "months_reported": 1}


def test_a_town_is_found_by_its_town_code():
    # Wallingford isn't a Census place: its row has place 00000, like every such town in the state.
    column, code = fetch_housing.bps_town({"housing": {"bps_state": "09", "bps_mcd": "78740"}})
    found = fetch_housing.parse_bps(PLACE_FILE, "09", code, column)
    assert found["units"] == 24 and found["months_reported"] == 0
    # Matching on place 00000 finds the state's first such town instead (Manchester, Connecticut).
    assert fetch_housing.parse_bps(PLACE_FILE, "09", "00000")["units"] == 28


def test_place_00000_is_refused():
    assert fetch_housing.bps_town({"housing": {"bps_place": "26150"}}) == (5, "26150")
    for housing in ({"bps_place": "00000"}, {}):
        with pytest.raises(SystemExit, match="bps_mcd"):
            fetch_housing.bps_town({"housing": housing})
