"""A school district's calendar feed (Beverly Public Schools', saved and trimmed:
tests/fixtures/ical_district.ics), read for its board's meetings (pipeline/ical.py)."""

import copy
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse

from pipeline import build_site, fetch_meetings, ical
from pipeline.fetch_meetings import load_store

FEED = (FIXTURES / "ical_district.ics").read_text(encoding="utf-8")
ICAL_URL = "https://www.beverlyschools.org/apps/events/ical/?id=0"
PAGE_URL = "https://www.beverlyschools.org/apps/pages/index.jsp?uREC_ID=2083091&type=d"
BODIES = {"School Committee": "School Committee", "Committee of the Whole": "School Committee of the Whole",
          "Policy Subcommittee": "School Committee Policy Subcommittee",
          "Finance & Facilities": "School Committee Finance and Facilities Subcommittee"}
NOW = datetime(2026, 10, 2, 7, 0, tzinfo=TZ)


def test_events():
    events = ical.parse_events(FEED)
    assert len(events) == 24
    # Long lines are folded in the feed; an escaped comma is a comma.
    regular = next(e for e in events if e["uid"].startswith("54210334@"))
    assert regular == {"uid": "54210334@www.beverlyschools.org#20261014", "date": "2026-10-14", "start_time": "18:00",
                       "title": "Regular School Committee Meeting-BMS Library", "location": "",
                       "url": "https://www.beverlyschools.org/apps/events/54210334/", "cancelled": False}
    holiday = next(e for e in events if e["title"].startswith("No School-Columbus"))
    assert holiday["start_time"] is None


@pytest.mark.parametrize("title, body, status", [
    ("Regular School Committee Meeting-BMS Library", "School Committee", "scheduled"),
    ("Additional School Committee Meeting-ZOOM", "School Committee", "scheduled"),
    ("Committee of the Whole Meeting", "School Committee of the Whole", "scheduled"),
    ("CANCELED - Committee of the Whole Meeting", "School Committee of the Whole", "cancelled"),
    ("Policy Subcommittee Meeting-BMS Library", "School Committee Policy Subcommittee", "scheduled"),
])
def test_meetings_named_by_their_boards(title, body, status):
    found = ical.to_meeting({"uid": "1@x", "date": "2026-10-14", "start_time": "18:00", "title": title, "location": "",
                             "url": None, "cancelled": False}, BODIES)
    assert (found["body"], found["status"], found["id"]) == (body, status, "ical-1")


def test_other_events_are_left_out():
    assert ical.to_meeting({"uid": "2@x", "date": "2026-10-12", "start_time": None,
                            "title": "No School-Columbus/Indigenous Peoples' Day", "location": "", "url": None,
                            "cancelled": False}, BODIES) is None


class FakeDistrict:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        assert url == ICAL_URL
        return FakeResponse(FEED.encode())


@pytest.fixture
def beverly(config):
    town = copy.deepcopy(config)
    town.pop("archive", None)
    town.pop("drive_meetings", None)
    town["meetings"] = {"agenda_center": {"base_url": "https://www.beverlyma.gov", "since": "2026-01-01"}}
    town["ical_meetings"] = {"ical_url": ICAL_URL, "page_url": PAGE_URL, "source_name": "Beverly Public Schools",
                             "since": "2026-09-01", "bodies": BODIES}
    return town


def test_fetch_records_the_board_s_meetings(beverly, tmp_path):
    beverly["meetings"].pop("agenda_center")
    fetch_meetings.run(beverly, FakeDistrict(), tmp_path, now=NOW)
    store = load_store(tmp_path)
    upcoming = sorted((m["date"], m["start_time"], m["body"]) for m in store.values())
    assert ("2026-10-14", "18:00", "School Committee") in upcoming
    assert ("2026-10-28", "19:15", "School Committee of the Whole") in upcoming
    # From `since`, and up to 90 days ahead.
    assert min(d for d, _, _ in upcoming) >= "2026-09-01" and max(d for d, _, _ in upcoming) <= "2026-12-31"
    m = next(m for m in store.values() if m["date"] == "2026-10-14")
    assert (m["source"], m["source_name"], m["source_url"]) == (
        "ical", "Beverly Public Schools", "https://www.beverlyschools.org/apps/events/54210334/")


def test_the_meeting_page_says_where_it_s_from(beverly, tmp_path):
    beverly["meetings"].pop("agenda_center")
    fetch_meetings.run(beverly, FakeDistrict(), tmp_path, now=NOW)
    m = next(m for m in build_site.load_meetings(tmp_path, NOW.date())["all"] if m["date"] == "2026-10-14")
    assert m["listing"] == "school district's website"


@pytest.mark.parametrize("change, message", [
    ({"ical_url": None}, "needs ical_url"),
    ({"since": "September"}, "since must be a date"),
    ({"bodies": ["School Committee"]}, "bodies must map"),
])
def test_config_is_checked(change, message):
    settings = {"ical_url": ICAL_URL, "page_url": PAGE_URL, "source_name": "Beverly Public Schools",
                "since": "2026-09-01", "bodies": BODIES}
    settings = {k: v for k, v in {**settings, **change}.items() if v is not None}
    with pytest.raises(SystemExit, match=message):
        ical.check({"slug": "beverly", "ical_meetings": settings})
