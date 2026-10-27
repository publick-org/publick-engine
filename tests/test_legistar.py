"""Meetings, agendas and minutes from a Legistar system's public API, as Somerville's City Council
posts them. Run offline against saved events (tests/fixtures/legistar_events.json)."""

import copy
import json
from datetime import date, datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeJSONResponse, FakeResponse

from pipeline import absences, build_site, fetch_meetings, fetch_minutes, legistar, listings
from pipeline.config import load_config

API = "https://webapi.legistar.com/v1/somervillema"
# Before the meetings of late October, so they are upcoming.
BEFORE = datetime(2026, 10, 24, 7, 0, tzinfo=TZ)
# After them, when more minutes are posted.
AFTER = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)


def saved() -> list[dict]:
    return json.loads((FIXTURES / "legistar_events.json").read_text())


class FakeLegistar:
    """Serves the saved events for any query, and every agenda and minutes file as a distinct PDF."""

    def __init__(self, events: list[dict] | None = None):
        self.events = saved() if events is None else events
        self.pdf = (FIXTURES / "legistar_minutes.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        if url.startswith(f"{API}/events?"):
            return FakeJSONResponse(copy.deepcopy(self.events))
        if "legistar1.com" in url:
            return FakeResponse(self.pdf + f"\n% {url}\n".encode(), {"content-disposition": "inline; filename=x.pdf"})
        raise AssertionError(f"unexpected URL {url}")


def somerville(config: dict) -> dict:
    """The tests' town with Somerville's Legistar system as its only meeting calendar."""
    town = copy.deepcopy(config)
    for table in ("archive", "drive_meetings", "summaries", "participation", "glossary"):
        town.pop(table, None)
    town["town"].update(name="Somerville")
    town["meetings"] = {
        "governing_body": "City Council",
        "boards": ["City Council", "Finance Committee", "Land Use Committee", "Legislative Matters Committee",
                   "Traffic and Parking Committee", "Licenses and Permits Committee"],
        "legistar": {"client": "somervillema", "since": "2026-01-01"},
    }
    return town


@pytest.fixture
def config():
    return somerville(load_config("gloucester"))


def load(data_dir):
    return json.loads((data_dir / "meetings" / "meetings.json").read_text())


def by_id() -> dict:
    return {e["id"]: e for e in legistar.parse_events(saved())}


# ---- Reading the API ---------------------------------------------------------

def test_urls():
    assert legistar.api_url("somervillema") == API
    assert legistar.site_url("somervillema") == "https://somervillema.legistar.com/Calendar.aspx"
    assert (legistar.events_url(API, date(2026, 1, 1))
            == f"{API}/events?$filter=EventDate%20ge%20datetime'2026-01-01'&$orderby=EventDate,EventId&$top=1000")
    assert legistar.events_url(API, date(2026, 1, 1), 1000).endswith("&$top=1000&$skip=1000")


@pytest.mark.parametrize("text, time", [("7:00 PM", "19:00"), ("06:00 PM", "18:00"), ("10:30 AM", "10:30"),
                                        ("12:00 PM", "12:00"), ("12:15 a.m.", "00:15"), ("", None), (None, None),
                                        ("TBD", None)])
def test_clock(text, time):
    assert legistar.clock(text) == time


def test_a_meeting():
    council = by_id()["legistar-4058"]
    assert council["source"] == "legistar" and council["body"] == "City Council" and council["title"] == "City Council"
    assert council["date"] == "2026-10-08" and council["start_time"] == "19:00"
    assert council["location_name"] == "City Council Chamber and Remote Participation"
    assert council["status"] == "scheduled" and not council["special"]
    assert council["source_url"].startswith("https://somervillema.legistar.com/MeetingDetail.aspx?LEGID=4058")
    assert council["documents_url"] == council["source_url"]


def test_the_comment_says_what_the_name_doesnt():
    found = by_id()
    assert found["legistar-3965"]["status"] == "cancelled"
    assert found["legistar-3965"]["title"] == "Traffic and Parking Committee"
    special = found["legistar-3994"]
    assert special["special"] and special["body"] == "City Council"
    assert special["title"] == "City Council - Special Meeting, Joint Meeting with School Committee"
    joint = found["legistar-3995"]
    assert joint["body"] == "Land Use Committee" and not joint["special"]
    assert joint["title"] == "Land Use Committee - Joint meeting with the Planning Board"


def test_the_online_meetings_link_is_kept_apart_from_the_note():
    found = by_id()
    alone = found["legistar-4089"]
    assert alone["remote_url"].startswith("https://us02web.zoom.us/webinar/register/")
    assert alone["title"] == "Legislative Matters Committee"
    both = found["legistar-4091"]
    assert both["remote_url"].startswith("https://us02web.zoom.us/webinar/register/")
    assert both["title"] == "Land Use Committee - Joint Meeting with the Planning Board"
    assert "remote_url" not in found["legistar-4058"]


def test_files_are_named_for_their_publication():
    found = by_id()
    council = found["legistar-4058"]
    assert council["agenda_id"] == "legistar-4058-agenda-20261009141447"
    assert council["minutes_id"] == "legistar-4058-minutes-20261015145133"
    assert council["minutes_url"].endswith("/4058_M_City_Council_26-10-08_Meeting_Minutes.pdf")
    upcoming = found["legistar-4089"]  # an agenda, no minutes yet
    assert "agenda_id" in upcoming and "minutes_id" not in upcoming
    cancelled = found["legistar-3965"]  # no files
    assert "agenda_id" not in cancelled and cancelled["documents_url"] is None


def test_a_file_published_again_is_a_new_file():
    events = saved()
    council = next(e for e in events if e["EventId"] == 4058)
    council["EventMinutesLastPublishedUTC"] = "2026-11-02T09:00:00"
    again = {e["id"]: e for e in legistar.parse_events(events)}["legistar-4058"]
    assert again["minutes_id"] == "legistar-4058-minutes-20261102090000"
    assert again["minutes_url"] == by_id()["legistar-4058"]["minutes_url"]


def test_without_documents_no_files_are_named():
    assert not any("agenda_id" in e or "minutes_id" in e for e in legistar.parse_events(saved(), with_documents=False))


def test_a_meeting_without_a_date_or_body_is_skipped():
    assert legistar.parse_events([{"EventId": 1, "EventDate": None, "EventBodyName": "City Council"},
                                  {"EventId": 2, "EventDate": "2026-10-01T00:00:00", "EventBodyName": ""}]) == []


# ---- Collecting ---------------------------------------------------------------

def test_run_collects_meetings_and_upcoming_agendas(config, tmp_path):
    client = FakeLegistar()
    status = fetch_meetings.run(config, client, tmp_path, now=BEFORE)
    assert status["errors"] == [] and status["failed_calendars"] == []
    assert status["calendars"]["Legistar"]["listed"] == len(saved())
    store = load(tmp_path)
    assert store["legistar-4058"]["slug"] == "2026-10-08-city-council"
    # The first run reads from `since`, one request a page.
    assert f"{API}/events?$filter=EventDate%20ge%20datetime'2026-01-01'" in client.urls[0]
    # Upcoming meetings' agendas are saved; past ones' aren't fetched again.
    upcoming = store["legistar-4098"]
    assert upcoming["agendas"][0]["id"] == "legistar-4098-agenda-20261025152247"
    assert (tmp_path / "meetings" / "agendas" / "legistar-4098-agenda-20261025152247.pdf").exists()
    assert "agendas" not in store["legistar-4058"]


def test_later_runs_recheck_recent_weeks_only(config, tmp_path):
    fetch_meetings.run(config, FakeLegistar(), tmp_path, now=BEFORE)
    later = FakeLegistar()
    fetch_meetings.run(config, later, tmp_path, now=AFTER)
    assert "datetime'2026-09-21'" in later.urls[0]


def test_pages_until_one_isnt_full(config, tmp_path, monkeypatch):
    monkeypatch.setattr(legistar, "PAGE", 10)
    client = FakeLegistar()
    fetch_meetings.run(config, client, tmp_path, now=BEFORE)
    # The fake serves every event to each request: a full page, so the next is read, up to max_pages.
    assert sum("/events?" in u for u in client.urls) == 20
    assert client.urls[1].endswith("&$skip=10")


def test_only_to_days_ahead(config, tmp_path):
    fetch_meetings.run(config, FakeLegistar(), tmp_path, now=datetime(2026, 8, 1, 7, 0, tzinfo=TZ))
    store = load(tmp_path)
    assert "legistar-4058" in store and "legistar-4099" not in store  # November 10 is past 90 days


def test_a_meeting_dropped_from_legistar_is_marked_removed(config, tmp_path):
    fetch_meetings.run(config, FakeLegistar(), tmp_path, now=BEFORE)
    fewer = [e for e in saved() if e["EventId"] != 4098]
    fetch_meetings.run(config, FakeLegistar(fewer), tmp_path, now=BEFORE.replace(day=25))
    store = load(tmp_path)
    assert store["legistar-4098"]["listed"] is False
    assert store["legistar-4098"]["history"][-1]["field"] == "listed"


def test_documents_off_saves_nothing(config, tmp_path):
    config["meetings"]["documents"] = False
    client = FakeLegistar()
    fetch_meetings.run(config, client, tmp_path, now=BEFORE)
    assert not any("legistar1.com" in u for u in client.urls)
    assert not any("agenda_id" in m for m in load(tmp_path).values())


def test_minutes_are_saved_and_a_new_publication_replaces_them(config, tmp_path):
    fetch_meetings.run(config, FakeLegistar(), tmp_path, now=BEFORE)
    result = fetch_minutes.run_linked(config, FakeLegistar(), tmp_path, now=BEFORE)
    assert result["errors"] == []
    store = load(tmp_path)
    council = store["legistar-4058"]
    assert [d["id"] for d in council["minutes"]] == ["legistar-4058-minutes-20261015145133"]
    assert council["minutes"][0]["has_text"] is True
    assert (tmp_path / "meetings" / "minutes" / "legistar-4058-minutes-20261015145133.pdf").exists()
    # Published again (adopted, or corrected): saved, and the change recorded.
    events = saved()
    next(e for e in events if e["EventId"] == 4058)["EventMinutesLastPublishedUTC"] = "2026-11-02T09:00:00"
    fetch_meetings.run(config, FakeLegistar(events), tmp_path, now=AFTER)
    fetch_minutes.run_linked(config, FakeLegistar(events), tmp_path, now=AFTER)
    council = load(tmp_path)["legistar-4058"]
    assert [d["id"] for d in council["minutes"]][-1] == "legistar-4058-minutes-20261102090000"
    assert council["history"][-1]["field"] == "minutes"


def test_minutes_are_awaited_on_the_city_portal(config, tmp_path):
    fetch_meetings.run(config, FakeLegistar(), tmp_path, now=BEFORE)
    meeting = load(tmp_path)["legistar-4059"]  # October 22, no minutes yet
    assert absences.minutes_source(config, meeting) == ("legistar", "2026-01-01")
    assert build_site.LISTINGS["legistar"] == build_site.LISTINGS["civicclerk"]
    assert build_site.meeting_links(config)["portal"] == "https://somervillema.legistar.com/Calendar.aspx"
    assert "legistar" in listings.LISTING_ORDER


# ---- Site ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def somerville_site(tmp_path_factory):
    town = somerville(load_config("gloucester"))
    town["sections"] = [s for s in town["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "storage"):
        town.pop(table, None)
    data_dir = tmp_path_factory.mktemp("somerville-data")
    fetch_meetings.run(town, FakeLegistar(), data_dir, now=BEFORE)
    fetch_minutes.run_linked(town, FakeLegistar(), data_dir, now=BEFORE)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: town)
        out = tmp_path_factory.mktemp("somerville") / "site"
        build_site.build("somerville", out, data_dir=data_dir, now=BEFORE)
    return out


def test_site_names_the_city_portal(somerville_site):
    about = (somerville_site / "about" / "index.html").read_text()
    assert "meeting portal" in about and "(Legistar)" in about and "January 2026" in about
    assert "Archive Center" not in about
    index = (somerville_site / "meetings" / "index.html").read_text()
    assert 'href="https://somervillema.legistar.com/Calendar.aspx"' in index


def test_a_meeting_page_waits_for_minutes_on_the_portal(somerville_site):
    page = (somerville_site / "meetings" / "2026-10-22-city-council" / "index.html").read_text()
    assert "Minutes haven't been posted on the" in page and "city's meeting portal" in page
    special = (somerville_site / "meetings" / "2026-04-14-city-council" / "index.html").read_text()
    assert "Special Meeting, Joint Meeting with School Committee" in special
