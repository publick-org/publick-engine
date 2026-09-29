"""Meetings, agendas and minutes from a CivicPlus Agenda Center (Malden's, saved)."""

import copy
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse

from pipeline import agendacenter, fetch_meetings, fetch_minutes
from pipeline.fetch_meetings import load_store

BASE = "https://www.cityofmalden.org"
PAGE = (FIXTURES / "agendacenter_search.html").read_text(encoding="utf-8")
NOW = datetime(2026, 9, 20, 7, 0, tzinfo=TZ)
COMMITTEES = {"City Council": {
    "Finance Committee": "City Council Finance Committee",
    "License Committee": "City Council License Committee",
    "Rules and Ordinance Committee": "City Council Rules and Ordinance Committee",
}}
ALIASES = {"Transportation Commission (formerly Traffic Commission)": "Transportation Commission"}


class FakeAgendaCenter:
    """Serves the saved search page, and a distinct PDF for every agenda or minutes link."""

    def __init__(self, page: str = PAGE):
        self.page = page
        self.pdf = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if "/AgendaCenter/Search/" in url:
            return FakeResponse(self.page.encode())
        if "/AgendaCenter/ViewFile/" in url:
            return FakeResponse(self.pdf + f"\n% {url}\n".encode())
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture
def malden(config):
    town = copy.deepcopy(config)
    town.pop("archive", None)
    town["meetings"] = {"aliases": ALIASES, "agenda_center": {
        "base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}}
    return town


def rows():
    return agendacenter.parse_listing(PAGE, BASE)


def test_search_url_covers_the_dates():
    url = agendacenter.search_url(BASE, datetime(2026, 8, 1).date(), datetime(2026, 11, 19).date())
    assert url == f"{BASE}/AgendaCenter/Search/?term=&CIDs=all&startDate=08/01/2026&endDate=11/19/2026&dateRange=&dateSelector="


def test_listing_rows():
    found = rows()
    assert len(found) == 15
    assert {r["category"] for r in found} == {"Board of Appeal", "City Council", "Commission on Climate Action and Sustainability",
                                              "Transportation Commission (formerly Traffic Commission)"}
    finance = next(r for r in found if r["number"] == "4453")
    assert finance == {"category": "City Council", "category_id": "37", "number": "4453", "date": "2026-09-29",
                       "posted_at": "2026-09-24T12:17", "title": "Finance Committee Agenda",
                       "agenda_url": f"{BASE}/AgendaCenter/ViewFile/Agenda/_09292026-4453", "minutes_url": None}
    with_minutes = [r for r in found if r["minutes_url"]]
    assert [r["number"] for r in with_minutes] == ["4400", "4378", "4410"]
    assert with_minutes[0]["minutes_url"] == f"{BASE}/AgendaCenter/ViewFile/Minutes/_09162026-4400"


def test_committees_and_board_names():
    events = {r["number"]: agendacenter.to_event(r, ALIASES, COMMITTEES) for r in rows()}
    assert events["4453"]["body"] == "City Council Finance Committee"
    assert events["4453"]["title"] == "City Council Finance Committee Meeting"
    assert events["4441"]["body"] == "City Council"            # "City Council Agenda - September 22, 2026"
    assert events["4429"]["body"] == "City Council License Committee"
    traffic = next(e for e in events.values() if e["body"] == "Transportation Commission")
    assert traffic["title"] == "Transportation Commission Meeting"
    assert events["4453"]["posted_title"] == "Finance Committee Agenda"


def test_cancelled_meeting():
    cancelled = agendacenter.to_event(next(r for r in rows() if r["number"] == "4392"))
    assert cancelled["status"] == "cancelled" and cancelled["body"] == "Commission on Climate Action and Sustainability"


def test_revised_agenda_gets_a_new_id():
    row = next(r for r in rows() if r["number"] == "4453")
    first = agendacenter.to_event(row)
    revised = agendacenter.to_event({**row, "posted_at": "2026-09-25T09:00"})
    assert first["id"] == revised["id"] == "agendacenter-4453"
    assert first["agenda_id"] == "4453-202609241217" and revised["agenda_id"] == "4453-202609250900"


def test_fetch_meetings_records_every_board_and_saves_upcoming_agendas(malden, tmp_path):
    client = FakeAgendaCenter()
    status = fetch_meetings.run(malden, client, tmp_path, now=NOW)
    assert status["new_meetings"] == 15 and not status["errors"]
    assert client.urls[0] == agendacenter.search_url(BASE, datetime(2026, 8, 1).date(), datetime(2026, 11, 19).date())
    store = load_store(tmp_path)
    for m in store.values():
        assert bool(m.get("agendas")) == (m["date"] >= "2026-09-20"), m["id"]
    finance = store["agendacenter-4453"]
    assert finance["agendas"][0]["id"] == "4453-202609241217"
    assert finance["slug"] == "2026-09-29-city-council-finance-committee"
    assert (tmp_path / "meetings" / "agendas" / "4453-202609241217.pdf").exists()


def test_later_runs_reread_recent_weeks_and_record_revised_agendas(malden, tmp_path):
    malden["meetings"]["agenda_center"]["since"] = "2026-06-01"
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    revised = PAGE.replace("Sep</abbr> 24, 2026 12:17 PM", "Sep</abbr> 25, 2026 9:00 AM")
    client = FakeAgendaCenter(revised)
    fetch_meetings.run(malden, client, tmp_path, now=NOW.replace(day=21))
    assert "startDate=07/23/2026" in client.urls[0]  # 60 days back, no longer from `since`
    finance = load_store(tmp_path)["agendacenter-4453"]
    assert [a["id"] for a in finance["agendas"]] == ["4453-202609241217", "4453-202609250900"]
    assert finance["history"][-1]["field"] == "agenda"


def test_excluded_categories_are_skipped(malden, tmp_path):
    malden["meetings"]["agenda_center"]["exclude_categories"] = ["Board of Appeal"]
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    assert not any(m["body"] == "Board of Appeal" for m in load_store(tmp_path).values())


def test_minutes_are_downloaded_for_meetings_since_the_start(malden, tmp_path):
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    client = FakeAgendaCenter()
    summary = fetch_minutes.run_agenda_center(malden, client, tmp_path, now=NOW)
    assert summary == {"minutes_added": 3, "minutes_waiting": 0, "errors": []}
    store = load_store(tmp_path)
    appeal = store["agendacenter-4400"]
    assert appeal["minutes"][0]["id"] == "agendacenter-4400"
    assert appeal["minutes"][0]["source_url"] == f"{BASE}/AgendaCenter/ViewFile/Minutes/_09162026-4400"
    assert (tmp_path / "meetings" / "minutes" / "agendacenter-4400.pdf").exists()
    # Already saved: nothing is downloaded again.
    assert fetch_minutes.run_agenda_center(malden, FakeAgendaCenter(), tmp_path, now=NOW)["minutes_added"] == 0


def test_minutes_before_the_start_are_left(malden, tmp_path):
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    malden["meetings"]["agenda_center"]["since"] = "2026-09-01"
    summary = fetch_minutes.run_agenda_center(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    assert summary["minutes_added"] == 2  # 8/19 is before the start


def test_minutes_per_run_limit(malden, tmp_path):
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    malden["meetings"]["agenda_center"]["max_minutes_per_run"] = 1
    summary = fetch_minutes.run_agenda_center(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    assert summary == {"minutes_added": 1, "minutes_waiting": 2, "errors": []}
    # The newest first.
    assert load_store(tmp_path)["agendacenter-4400"].get("minutes")
