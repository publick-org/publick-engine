"""Meetings, agendas and minutes from a PrimeGov portal's public API, as Worcester posts its City
Council and boards there. Run offline against saved lists (tests/fixtures/primegov_*.json)."""

import copy
import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeJSONResponse, FakeResponse

from pipeline import absences, build_site, fetch_meetings, fetch_minutes, listings, primegov
from pipeline.config import load_config

CLIENT = "worcesterma"
BASE = f"https://{CLIENT}.primegov.com"
# Before the meetings of October 27 and 28, which are upcoming.
BEFORE = datetime(2026, 10, 26, 7, 0, tzinfo=TZ)


def archived() -> list[dict]:
    return json.loads((FIXTURES / "primegov_archived_2026.json").read_text())


def upcoming() -> list[dict]:
    return json.loads((FIXTURES / "primegov_upcoming.json").read_text())


class FakePrimeGov:
    """Serves the saved lists (the archive for 2026, none for other years), and every compiled
    document as a distinct PDF."""

    def __init__(self, past: list[dict] | None = None, ahead: list[dict] | None = None):
        self.past = archived() if past is None else past
        self.ahead = upcoming() if ahead is None else ahead
        self.pdf = (FIXTURES / "legistar_minutes.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        if "ListArchivedMeetings?year=" in url:
            return FakeJSONResponse(copy.deepcopy(self.past) if url.endswith("2026") else [])
        if url.endswith("ListUpcomingMeetings"):
            return FakeJSONResponse(copy.deepcopy(self.ahead))
        if "/Public/CompiledDocument?" in url:
            return FakeResponse(self.pdf + f"\n% {url}\n".encode(), {"content-disposition": "attachment; filename=x.pdf"})
        raise AssertionError(f"unexpected URL {url}")


def worcester(config: dict) -> dict:
    """The tests' town with Worcester's PrimeGov portal as its only meeting calendar."""
    town = copy.deepcopy(config)
    for table in ("archive", "drive_meetings", "summaries", "participation", "glossary"):
        town.pop(table, None)
    town["town"].update(name="Worcester")
    town["meetings"] = {
        "governing_body": "City Council",
        "boards": ["City Council", "Zoning Board of Appeals", "Board of Health"],
        "primegov": {"client": CLIENT, "since": "2026-01-01"},
    }
    return town


@pytest.fixture
def config():
    return worcester(load_config("gloucester"))


def load(data_dir):
    return json.loads((data_dir / "meetings" / "meetings.json").read_text())


def by_id(**kwargs) -> dict:
    return {m["id"]: m for m in primegov.parse_meetings(archived() + upcoming(), CLIENT, **kwargs)}


# ---- Reading the API ---------------------------------------------------------

def test_urls():
    assert primegov.archived_url(CLIENT, 2026) == f"{BASE}/api/v2/PublicPortal/ListArchivedMeetings?year=2026"
    assert primegov.upcoming_url(CLIENT) == f"{BASE}/api/v2/PublicPortal/ListUpcomingMeetings"
    assert primegov.portal_url(CLIENT) == f"{BASE}/public/portal"
    assert primegov.document_url(CLIENT, 76972) == f"{BASE}/Public/CompiledDocument?meetingTemplateId=76972&compileOutputType=1"
    assert primegov.document_url(CLIENT, 75801, primegov.HTML) == f"{BASE}/Portal/Meeting?meetingTemplateId=75801"


def test_a_meeting():
    council = by_id()["primegov-12852"]
    assert council["source"] == "primegov" and council["body"] == "City Council" and council["title"] == "City Council"
    assert council["date"] == "2026-10-20" and council["start_time"] == "18:30"
    assert council["location_name"] == "Esther Howland (South) Chamber"
    assert council["status"] == "scheduled" and not council["special"]
    assert council["video_id"] == "00BDhr5e5uU"
    # Its page is its agenda's PDF (it has no HTML template); a meeting with one links that page.
    assert council["source_url"] == f"{BASE}/Public/CompiledDocument?meetingTemplateId=76969&compileOutputType=1"
    assert by_id()["primegov-12673"]["source_url"] == f"{BASE}/Portal/Meeting?meetingTemplateId=75801"


def test_files_are_the_agenda_and_minutes_pdfs_named_for_their_publication():
    council = by_id()["primegov-12852"]
    assert council["agenda_id"] == "primegov-22600-20261016182334"
    assert council["agenda_url"].endswith("meetingTemplateId=76969&compileOutputType=1")
    assert council["minutes_id"] == "primegov-22638-20261023182656"
    assert council["minutes_url"].endswith("meetingTemplateId=76972&compileOutputType=1")
    # Not a packet, an HTML copy, or a legal advertisement.
    zba = next(m for m in archived() if m["id"] == 12806)
    agenda = next(d for d in zba["documentList"] if d["templateName"] == "Agenda")
    assert by_id()["primegov-12806"]["agenda_url"] == primegov.document_url(CLIENT, agenda["templateId"])
    assert "minutes_id" not in by_id()["primegov-12806"]
    html_only = by_id()["primegov-12673"]  # January's agenda is only an HTML page
    assert "agenda_id" not in html_only and html_only["minutes_id"].startswith("primegov-")


def test_a_town_names_its_own_templates():
    found = by_id(agenda=r"^legal advertisement$")["primegov-12806"]
    assert found["agenda_url"] == primegov.document_url(
        CLIENT, next(d["templateId"] for m in archived() if m["id"] == 12806 for d in m["documentList"]
                     if d["templateName"] == "Legal Advertisement"))


def test_a_cancelled_meeting():
    cancelled = by_id()["primegov-5931"]
    assert cancelled["status"] == "cancelled" and cancelled["body"] == "Board of Health"
    assert "agenda_id" not in cancelled  # a cancellation notice isn't an agenda


def test_a_joint_meeting_keeps_its_name():
    joint = by_id()["primegov-12596"]
    assert joint["title"] == "Standing Committee on Public Works (Meeting Jointly with Standing Committee on Traffic and Parking)"
    assert joint["body"] == "Standing Committee on Public Works"


def test_without_documents_no_files_are_named():
    found = primegov.parse_meetings(archived(), CLIENT, with_documents=False)
    assert not any("agenda_id" in m or "minutes_id" in m for m in found)


def test_a_meeting_without_a_date_or_title_is_skipped():
    assert primegov.parse_meetings([{"id": 1, "dateTime": None, "title": "City Council"},
                                    {"id": 2, "dateTime": "2026-10-01T18:00:00", "title": " "}], CLIENT) == []


# ---- Collecting ---------------------------------------------------------------

def test_run_collects_past_and_upcoming_meetings(config, tmp_path):
    client = FakePrimeGov()
    status = fetch_meetings.run(config, client, tmp_path, now=BEFORE)
    assert status["errors"] == [] and status["failed_calendars"] == []
    assert status["calendars"]["PrimeGov"]["listed"] == len(archived()) + len(upcoming())
    store = load(tmp_path)
    assert store["primegov-12852"]["slug"] == "2026-10-20-city-council"
    assert client.urls[:2] == [primegov.archived_url(CLIENT, 2026), primegov.upcoming_url(CLIENT)]
    # Upcoming meetings' agendas are saved; past ones' aren't fetched.
    assert store["primegov-12820"]["agendas"][0]["id"].startswith("primegov-")
    assert "agendas" not in store["primegov-12852"]


def test_the_first_run_reads_every_year_since(config, tmp_path):
    config["meetings"]["primegov"]["since"] = "2025-07-01"
    client = FakePrimeGov()
    fetch_meetings.run(config, client, tmp_path, now=BEFORE)
    assert primegov.archived_url(CLIENT, 2025) in client.urls and primegov.archived_url(CLIENT, 2026) in client.urls
    later = FakePrimeGov()
    fetch_meetings.run(config, later, tmp_path, now=BEFORE.replace(day=27))
    assert primegov.archived_url(CLIENT, 2025) not in later.urls


def test_include_pattern_keeps_the_chosen_boards(config, tmp_path):
    config["meetings"]["primegov"]["include_pattern"] = r"^(City Council|Standing Committee)\b"
    fetch_meetings.run(config, FakePrimeGov(), tmp_path, now=BEFORE)
    bodies = {m["body"] for m in load(tmp_path).values()}
    assert "Zoning Board of Appeals" not in bodies and "Advisory Commission on Youth" not in bodies
    assert {"City Council", "Standing Committee on Traffic and Parking"} <= bodies


def test_documents_off_saves_nothing(config, tmp_path):
    config["meetings"]["documents"] = False
    client = FakePrimeGov()
    fetch_meetings.run(config, client, tmp_path, now=BEFORE)
    assert not any("CompiledDocument" in u for u in client.urls)


def test_minutes_are_saved_and_a_new_publication_replaces_them(config, tmp_path):
    fetch_meetings.run(config, FakePrimeGov(), tmp_path, now=BEFORE)
    assert fetch_minutes.run_linked(config, FakePrimeGov(), tmp_path, now=BEFORE)["errors"] == []
    council = load(tmp_path)["primegov-12852"]
    assert [d["id"] for d in council["minutes"]] == ["primegov-22638-20261023182656"]
    past = archived()
    doc = next(d for m in past if m["id"] == 12852 for d in m["documentList"] if d["templateName"] == "Minutes")
    doc["publishDate"] = "2026-11-03T09:00:00"
    fetch_meetings.run(config, FakePrimeGov(past), tmp_path, now=BEFORE.replace(day=27))
    fetch_minutes.run_linked(config, FakePrimeGov(past), tmp_path, now=BEFORE.replace(day=27))
    council = load(tmp_path)["primegov-12852"]
    assert council["minutes"][-1]["id"] == "primegov-22638-20261103090000"
    assert council["history"][-1]["field"] == "minutes"


def test_minutes_are_awaited_on_the_city_portal(config, tmp_path):
    fetch_meetings.run(config, FakePrimeGov(), tmp_path, now=BEFORE)
    meeting = load(tmp_path)["primegov-12806"]
    assert absences.minutes_source(config, meeting) == ("primegov", "2026-01-01")
    assert build_site.meeting_links(config)["portal"] == f"{BASE}/public/portal"
    assert "primegov" in listings.LISTING_ORDER


# ---- Site ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def worcester_site(tmp_path_factory):
    town = worcester(load_config("gloucester"))
    town["sections"] = [s for s in town["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "storage"):
        town.pop(table, None)
    data_dir = tmp_path_factory.mktemp("worcester-data")
    fetch_meetings.run(town, FakePrimeGov(), data_dir, now=BEFORE)
    fetch_minutes.run_linked(town, FakePrimeGov(), data_dir, now=BEFORE)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: town)
        out = tmp_path_factory.mktemp("worcester") / "site"
        build_site.build("worcester", out, data_dir=data_dir, now=BEFORE)
    return out


def test_site_names_the_city_portal(worcester_site):
    about = (worcester_site / "about" / "index.html").read_text()
    assert "(PrimeGov)" in about and "January 2026" in about and "Archive Center" not in about
    index = (worcester_site / "meetings" / "index.html").read_text()
    assert f'href="{BASE}/public/portal"' in index


def test_a_meeting_page_waits_for_minutes_on_the_portal(worcester_site):
    page = (worcester_site / "meetings" / "2026-10-19-zoning-board-of-appeals" / "index.html").read_text()
    assert "Minutes haven't been posted on the" in page and "city's meeting portal" in page
    council = (worcester_site / "meetings" / "2026-10-20-city-council" / "index.html").read_text()
    assert "youtube" in council.lower()
