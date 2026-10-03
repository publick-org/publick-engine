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


@pytest.mark.parametrize("category, board", [
    # Beverly's Agenda Center categories.
    ("Health, Board of", "Board of Health"),
    ("Appeals, Zoning Board of", "Zoning Board of Appeals"),
    ("Aging, Council on", "Council on Aging"),
    ("Registrars of Voters, Board of", "Board of Registrars of Voters"),
    ("Trust Funds, Commissioners of", "Commissioners of Trust Funds"),
    ("Disabilities, Commission on", "Commission on Disabilities"),
    # Not written last-name-first: kept as they are.
    ("Water Supply Board, Salem and Beverly", "Water Supply Board, Salem and Beverly"),
    ("Planning Board", "Planning Board"),
    ("Parks & Recreation Commission", "Parks & Recreation Commission"),
])
def test_categories_written_last_name_first_are_turned_round(category, board):
    row = {"category": category, "title": f"{category} Meeting Agenda"}
    assert agendacenter.body_for(row, {}, {}) == board


def test_an_alias_comes_before_turning_a_name_round():
    row = {"category": "Health, Board of", "title": "Agenda"}
    assert agendacenter.body_for(row, {"Health, Board of": "Beverly Board of Health"}, {}) == "Beverly Board of Health"


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


def test_a_calendar_that_goes_empty_counts_as_failed(malden, tmp_path, monkeypatch):
    """A calendar that listed meetings and lists none has most likely broken: the check fails,
    every run until it lists meetings again, and the meetings recorded stay as they were."""
    import dataclasses
    first = fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    before = load_store(tmp_path)
    real = fetch_meetings.calendars
    monkeypatch.setattr(fetch_meetings, "calendars",
                        lambda config: [dataclasses.replace(c, events=lambda *a: []) for c in real(config)])
    for day in (21, 22):
        status = fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW.replace(day=day))
        assert status["failed_calendars"] == ["Agenda Center"]
        assert status["calendars"]["Agenda Center"] == first["calendars"]["Agenda Center"]
        assert "lists no meetings, after 15 last time" in status["errors"][0]
    assert all(m.get("listed", True) == before[k].get("listed", True) for k, m in load_store(tmp_path).items())


def test_excluded_categories_are_skipped(malden, tmp_path):
    malden["meetings"]["agenda_center"]["exclude_categories"] = ["Board of Appeal"]
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    assert not any(m["body"] == "Board of Appeal" for m in load_store(tmp_path).values())


def test_minutes_are_downloaded_for_meetings_since_the_start(malden, tmp_path):
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    client = FakeAgendaCenter()
    summary = fetch_minutes.run_linked(malden, client, tmp_path, now=NOW)
    assert summary == {"minutes_added": 3, "minutes_waiting": 0, "errors": []}
    store = load_store(tmp_path)
    appeal = store["agendacenter-4400"]
    assert appeal["minutes"][0]["id"] == "agendacenter-4400"
    assert appeal["minutes"][0]["source_url"] == f"{BASE}/AgendaCenter/ViewFile/Minutes/_09162026-4400"
    assert (tmp_path / "meetings" / "minutes" / "agendacenter-4400.pdf").exists()
    # Already saved: nothing is downloaded again.
    assert fetch_minutes.run_linked(malden, FakeAgendaCenter(), tmp_path, now=NOW)["minutes_added"] == 0


def test_minutes_before_the_start_are_left(malden, tmp_path):
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    malden["meetings"]["agenda_center"]["since"] = "2026-09-01"
    summary = fetch_minutes.run_linked(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    assert summary["minutes_added"] == 2  # 8/19 is before the start


def test_minutes_per_run_limit(malden, tmp_path):
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    malden["meetings"]["agenda_center"]["max_minutes_per_run"] = 1
    summary = fetch_minutes.run_linked(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    assert summary == {"minutes_added": 1, "minutes_waiting": 2, "errors": []}
    # The newest first.
    assert load_store(tmp_path)["agendacenter-4400"].get("minutes")


def test_an_agenda_posted_twice_is_one_meeting(malden, tmp_path):
    """A clerk posting the same meeting's agenda again, under a new number, lists it twice;
    with no times to tell them apart, the same board on the same day is one meeting, at the
    address of the first, with both agendas."""
    from pipeline import build_site
    page = PAGE.replace("4452", "9452")
    page = page.replace("Joint Finance Rules and Ordinance Agenda", "Finance Committee Agenda (second posting)")
    fetch_meetings.run(malden, FakeAgendaCenter(page), tmp_path, now=NOW)
    built = build_site.load_meetings(tmp_path, NOW.date())
    finance = [m for m in built["all"] if m["date"] == "2026-09-29" and m["body"] == "City Council Finance Committee"]
    assert len(finance) == 1
    m = finance[0]
    assert m["id"] == "agendacenter-4453" and not m["same_day"]
    assert [x["id"] for x in m["listings"]] == ["agendacenter-4453", "agendacenter-9452"]
    assert m["same_as"] == "no_time"
    assert len(m["agendas"]) == 2
    assert m["also_urls"] == [f"/meetings/{load_store(tmp_path)['agendacenter-9452']['slug']}/"]


def test_same_day_meetings_of_different_kinds_are_numbered(malden, tmp_path):
    """A board's hearing and its meeting on one day, with no times, stay two meetings, told apart by order."""
    from pipeline import build_site
    page = PAGE.replace("4452", "9452")
    page = page.replace("Joint Finance Rules and Ordinance Agenda", "Finance Committee Public Hearing Agenda")
    fetch_meetings.run(malden, FakeAgendaCenter(page), tmp_path, now=NOW)
    meetings = build_site.load_meetings(tmp_path, NOW.date())["all"]
    finance = sorted((m for m in meetings if m["date"] == "2026-09-29" and m["body"] == "City Council Finance Committee"),
                     key=lambda m: m["day_part"])
    assert [m["day_part"] for m in finance] == ["1 of 2", "2 of 2"]
    assert [m["id"] for m in finance] == ["agendacenter-4453", "agendacenter-9452"]
    alone = next(m for m in meetings if m["id"] == "agendacenter-4441")
    assert not alone["same_day"] and alone["day_part"] is None
