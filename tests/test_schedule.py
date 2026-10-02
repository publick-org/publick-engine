"""A page listing a board's meeting dates for the year (Malden Public Schools' School
Committee Meetings page, saved and trimmed: tests/fixtures/schedule_page.html), read
for its upcoming dates (pipeline/schedule.py)."""

import copy
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse

from pipeline import build_site, fetch_meetings, schedule
from pipeline.fetch_meetings import load_store

PAGE = (FIXTURES / "schedule_page.html").read_text(encoding="utf-8")
URL = "https://www.maldenps.org/district-information/school-committee/school-committee-meetings"
SETTINGS = {"url": URL, "source_name": "Malden Public Schools", "body": "School Committee"}
NOW = datetime(2026, 10, 2, 7, 0, tzinfo=TZ)


def test_dates():
    found = [day for day, _ in schedule.parse_dates(PAGE)]
    assert found[:6] == ["2026-08-03", "2026-08-31", "2026-10-05", "2026-11-09", "2026-12-07", "2027-01-04"]
    assert "2025-08-07" in found        # an earlier year's list, further down


@pytest.mark.parametrize("text, expected", [
    ("Monday, November 9, 2026", [("2026-11-09", "")]),
    ("Nov. 9th, 2026 - Finance", [("2026-11-09", "Finance")]),
    ("10/26/26 BOE Meeting", [("2026-10-26", "BOE Meeting")]),
])
def test_date_forms(text, expected):
    assert schedule.parse_dates(f"<p>{text}</p>") == expected


def test_only_upcoming_dates_are_meetings():
    found = schedule.meetings(PAGE, SETTINGS, "2026-10-02", "2026-12-01")
    assert [(m["id"], m["date"], m["body"]) for m in found] == [
        ("schedule-school-committee-2026-10-05", "2026-10-05", "School Committee"),
        ("schedule-school-committee-2026-11-09", "2026-11-09", "School Committee")]
    assert (found[0]["source"], found[0]["source_url"], found[0]["start_time"]) == ("schedule", URL, None)


def test_names_after_the_dates_pick_the_board():
    page = "<p>10/19/26 Operations Comm.</p><p>10/26/26 BOE Meeting</p><p>10/27/26 Holiday</p>"
    settings = {"url": URL, "source_name": "Wallingford Public Schools",
                "names": {"BOE": "Board of Education", "Operations": "Board of Education Operations Committee"}}
    found = schedule.meetings(page, settings, "2026-10-02", "2026-12-01")
    assert [(m["date"], m["body"]) for m in found] == [("2026-10-19", "Board of Education Operations Committee"),
                                                       ("2026-10-26", "Board of Education")]


class FakeSchedule:
    def get(self, url):
        assert url == URL
        return FakeResponse(PAGE.encode())


@pytest.fixture
def malden(config):
    town = copy.deepcopy(config)
    for table in ("archive", "drive_meetings"):
        town.pop(table, None)
    town["meetings"] = {"aliases": {}}
    town["schedule_meetings"] = dict(SETTINGS)
    return town


def test_a_scheduled_meeting_and_its_agenda_are_one(malden, tmp_path):
    """The schedule lists November 9; the agenda, posted later, is listed in the Agenda Center."""
    fetch_meetings.run(malden, FakeSchedule(), tmp_path, now=NOW)
    store = load_store(tmp_path)
    assert {m["date"] for m in store.values()} == {"2026-10-05", "2026-11-09"}
    store["agendacenter-4500"] = {**store["schedule-school-committee-2026-11-09"], "id": "agendacenter-4500",
                                  "source": "agendacenter", "slug": "2026-11-09-school-committee-2",
                                  "first_seen": "2026-11-05T07:00:00-05:00", "posted_title": "School Committee Agenda"}
    (tmp_path / "meetings" / "meetings.json").write_text(__import__("json").dumps(store))
    built = build_site.load_meetings(tmp_path, NOW.date())
    november = [m for m in built["all"] if m["date"] == "2026-11-09"]
    assert len(november) == 1 and november[0]["id"] == "schedule-school-committee-2026-11-09"
    assert november[0]["listing"] == "published schedule" or november[0]["source"] == "schedule"


def test_config_is_checked():
    with pytest.raises(SystemExit, match="needs url, source_name, and the board"):
        schedule.check({"slug": "malden", "schedule_meetings": {"url": URL, "source_name": "Malden Public Schools"}})
