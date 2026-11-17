"""Meetings, agendas and minutes from a CivicPlus Agenda Center (Malden's, saved)."""

import copy
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse

from pipeline import agendacenter, fetch_meetings, fetch_minutes, identity
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


def subcommittee_row(category, title):
    return {"category": category, "title": title, "number": "2943", "date": "2026-10-13", "posted_at": None,
            "agenda_url": "https://example.gov/AgendaCenter/ViewFile/Agenda/_10132026-2943", "minutes_url": None}


# An Agenda Center files a board's subcommittees under the board: Beverly's and Bangor's.
@pytest.mark.parametrize("category, title, body, of", [
    ("Airport Commission", "Beverly Regional Airport Subcommittee on Mission Meeting Agenda (PDF)",
     "Airport Commission Subcommittee", "Airport Commission"),
    ("Commission on Cultural Development", "Commission on Cultural Development Sub-Committee Meeting Agenda (PDF)",
     "Commission on Cultural Development Subcommittee", "Commission on Cultural Development"),
    # The board's own meeting, and a category that's a subcommittee already, stay as they are.
    ("Airport Commission", "Beverly Regional Airport Commission Meeting Agenda (PDF)", "Airport Commission", None),
    ("Finance Subcommittee", "Finance Subcommittee Meeting", "Finance Subcommittee", None),
])
def test_a_subcommittees_row_is_the_boards_subcommittee(category, title, body, of):
    event = agendacenter.to_event(subcommittee_row(category, title))
    assert (event["body"], event["subcommittee_of"]) == (body, of)


def test_a_committee_the_town_lists_comes_before_the_subcommittee_rule():
    committees = {"Conservation Commission": {"Rapid Response Subcommittee": "Conservation Commission Rapid Response Subcommittee"}}
    row = subcommittee_row("Conservation Commission", "Conservation Commission: Rapid Response Subcommittee Meeting (PDF)")
    event = agendacenter.to_event(row, {}, committees)
    assert (event["body"], event["subcommittee_of"]) == ("Conservation Commission Rapid Response Subcommittee", None)


def row_of(category, title, number="2978", day="2026-11-09"):
    return {**subcommittee_row(category, title), "number": number, "date": day}


# A committee of the category's board named in a row's title, as a board the town's calendar lists that day:
# November 9, 2026, each also on its city's calendar under the committee's name.
@pytest.mark.parametrize("town, title, known, body", [
    ("Beverly", "City Council Budget Analyst Screening Committee Meeting Agenda (PDF)",
     ["City Council", "City Council Budget Analyst Screening Committee"], "City Council Budget Analyst Screening Committee"),
    ("Salem", "Salem City Council Committee of the Whole",
     ["City Council", "Salem City Council Committee of the Whole"], "Salem City Council Committee of the Whole"),
    ("Methuen", "City Council Workshop Agenda, 6pm", ["City Council", "City Council Workshop"], "City Council Workshop"),
    # A title naming no board stays the category's board's (Salem's Board of Registrars, posting under City Council).
    ("Salem", "For the purpose of counting Federal Write-In Absentee Ballots (FWAB) and ballots mailed by voters from "
              "outside the U.S., postmarked on or before November 3, 2026, and received by 5:00 P.M. on Nov. 13, 2026. "
              "(This meeting will be held only if necessary.)",
     ["City Council", "Salem City Council Committee of the Whole", "Board of Registrars"], "City Council"),
    # Another board named in the title isn't the category's board's committee: a joint meeting stays the council's.
    ("Fitchburg", "Joint Convention City Council and School Committee January 20, 2026",
     ["City Council", "School Committee"], "City Council"),
    ("South Burlington", "City Council Meeting - Joint with Planning Commission", ["City Council", "Planning Commission"],
     "City Council"),
    # Names compared as boards are: the town's name, "Sub Committee", plurals.
    ("Salem", "City Council Committee of the Whole Agenda", ["City Council", "Salem City Council Committee of the Whole"],
     "Salem City Council Committee of the Whole"),
    ("Lowell", "City Council Sub Committee on Ordinances", ["City Council Subcommittee on Ordinances"],
     "City Council Subcommittee on Ordinances"),
    # A committee named beside a longer name of another board is still found.
    ("Methuen", "City Council Workshop on Conservation Commission Appointments",
     ["City Council", "City Council Workshop", "Conservation Commission"], "City Council Workshop"),
    # Nor is a name that adds only words naming no board of their own to the category's board's.
    ("Salem", "Salem City Council Special Meeting", ["City Council", "City Council Special"], "City Council"),
    ("Malden", "Mystic Valley Regional Charter School Board of Trustees Meeting",
     ["Mystic Valley Regional Charter School Board"], None),
])
def test_a_committee_the_town_knows_named_in_a_title(town, title, known, body):
    category = "Mystic Valley Regional Charter School" if town == "Malden" else "City Council"
    event = agendacenter.to_event(row_of(category, title), {}, {}, known, town)
    assert event["body"] == (body or category)
    # Without the day's listings, the category's board's (or its subcommittee's, by the subcommittee rule).
    assert agendacenter.to_event(row_of(category, title))["body"] in (category, f"{category} Subcommittee")


def test_a_known_committee_doesnt_take_a_subcommittees_row():
    """A row naming a subcommittee is still its board's subcommittee, not a committee it names without one;
    a subcommittee the town knows takes it."""
    row = row_of("City Council", "City Council Workshop Subcommittee Agenda")
    event = agendacenter.to_event(row, {}, {}, ["City Council Workshop"], "Methuen")
    assert (event["body"], event["subcommittee_of"]) == ("City Council Subcommittee", "City Council")
    known = ["City Council Workshop", "City Council Workshop Subcommittee"]
    event = agendacenter.to_event(row, {}, {}, known, "Methuen")
    assert (event["body"], event["subcommittee_of"]) == ("City Council Workshop Subcommittee", None)


def test_only_a_committee_another_source_lists_that_day_takes_a_row(malden, tmp_path):
    """A committee another source listed on another day ("Leach Library Trustees Non-Public", one evening on the
    calendar) doesn't take the board's rows of other days: they stay the category's board's."""
    page = PAGE.replace("City Council Agenda - September 22, 2026", "City Council Workshop Agenda, 6pm")
    (tmp_path / "meetings").mkdir()
    fetch_meetings.save_json(tmp_path / "meetings" / "meetings.json", {"2121": {
        "id": "2121", "date": "2026-09-23", "body": "City Council Workshop", "title": "City Council Workshop",
        "start_time": "18:00", "first_seen": "2026-09-01T07:00:00-04:00", "slug": "2026-09-23-city-council-workshop",
        "status": "scheduled", "listed": True}})
    fetch_meetings.run(malden, FakeAgendaCenter(page), tmp_path, now=NOW)
    assert load_store(tmp_path)["agendacenter-4441"]["body"] == "City Council"


def test_a_committee_the_town_lists_comes_before_one_it_knows():
    committees = {"City Council": {"Preliminary Screening": "City Council Budget/Management Analyst Screening Committee"}}
    row = row_of("City Council", "City Council Budget Analyst Preliminary Screening Committee Meeting Agenda (PDF)")
    event = agendacenter.to_event(row, {}, committees, ["City Council Budget Analyst Screening Committee"], "Beverly")
    assert event["body"] == "City Council Budget/Management Analyst Screening Committee"


def test_a_row_filed_under_its_category_joins_the_calendars_committee_meeting_on_the_next_run(malden, tmp_path):
    """Methuen's November 9 workshop: its Agenda Center row was filed under City Council, beside the calendar's
    listing of the City Council Workshop, so the meeting had two pages. The next run files the row under the
    workshop, the two are one meeting, and the row's page leads to the calendar's."""
    page = PAGE.replace("City Council Agenda - September 22, 2026", "City Council Workshop Agenda, 6pm")
    fetch_meetings.run(malden, FakeAgendaCenter(page), tmp_path, now=NOW)
    store = load_store(tmp_path)
    row = store["agendacenter-4441"]
    assert row["body"] == "City Council"
    # As recorded before: the calendar's listing, recorded the same run, a meeting of its own.
    store["2121"] = {"id": "2121", "date": "2026-09-22", "body": "City Council Workshop", "title": "City Council Workshop",
                     "start_time": "18:00", "first_seen": row["first_seen"], "slug": "2026-09-22-city-council-workshop",
                     "status": "scheduled", "listed": True}
    fetch_meetings.save_json(tmp_path / "meetings" / "meetings.json", store)
    (tmp_path / "meetings" / "identities.json").unlink()
    identity.update(tmp_path, store, "Gloucester", previous={})
    ours = {"2121", "agendacenter-4441"}
    pages = {m["id"]: [r["id"] for r in m["records"]] for m in identity.meetings(store, identity.load(tmp_path))
             if ours & {r["id"] for r in m["records"]}}
    assert pages == {"2026-09-22-city-council": ["agendacenter-4441"], "2026-09-22-city-council-workshop": ["2121"]}

    fetch_meetings.run(malden, FakeAgendaCenter(page), tmp_path, now=NOW.replace(day=21))
    store = load_store(tmp_path)
    assert store["agendacenter-4441"]["body"] == "City Council Workshop"
    meetings = [m for m in identity.meetings(store, identity.load(tmp_path)) if ours & {r["id"] for r in m["records"]}]
    assert [(m["id"], [r["id"] for r in m["records"]], m["addresses"]) for m in meetings] == [
        ("2026-09-22-city-council-workshop", ["2121", "agendacenter-4441"], ["2026-09-22-city-council"])]


def test_an_agenda_made_in_the_builder_is_read_as_its_pdf():
    """An agenda written in the Agenda Center's builder (Lowell's council, Beverly's Council on
    Aging) is linked as a web page, "?html=true"; the same address without it is the PDF."""
    builder = PAGE.replace('href="/AgendaCenter/ViewFile/Agenda/_09292026-4453" target',
                           'href="/AgendaCenter/ViewFile/Agenda/_09292026-4453?html=true" target', 1)
    assert builder != PAGE
    found = agendacenter.parse_listing(builder, BASE)
    assert len(found) == 15
    finance = next(r for r in found if r["number"] == "4453")
    assert finance["agenda_url"] == f"{BASE}/AgendaCenter/ViewFile/Agenda/_09292026-4453"
    assert finance["title"] == "Finance Committee Agenda"


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


def test_later_runs_reread_from_the_start_and_record_revised_agendas(malden, tmp_path):
    malden["meetings"]["agenda_center"]["since"] = "2026-06-01"
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    revised = PAGE.replace("Sep</abbr> 24, 2026 12:17 PM", "Sep</abbr> 25, 2026 9:00 AM")
    client = FakeAgendaCenter(revised)
    fetch_meetings.run(malden, client, tmp_path, now=NOW.replace(day=21))
    assert "startDate=06/01/2026" in client.urls[0]
    finance = load_store(tmp_path)["agendacenter-4453"]
    assert [a["id"] for a in finance["agendas"]] == ["4453-202609241217", "4453-202609250900"]
    assert finance["history"][-1]["field"] == "agenda"


def test_recheck_days_limits_later_runs(malden, tmp_path):
    malden["meetings"]["agenda_center"].update(since="2026-06-01", recheck_days=60)
    first = FakeAgendaCenter()
    fetch_meetings.run(malden, first, tmp_path, now=NOW)
    assert "startDate=06/01/2026" in first.urls[0]
    client = FakeAgendaCenter()
    fetch_meetings.run(malden, client, tmp_path, now=NOW.replace(day=21))
    assert "startDate=07/23/2026" in client.urls[0]


def test_minutes_posted_months_late_are_found(malden, tmp_path):
    """Minutes a board posts long after its meeting are linked on a later run, and downloaded."""
    malden["meetings"]["agenda_center"]["since"] = "2026-06-01"
    unposted = PAGE.replace("/AgendaCenter/ViewFile/Minutes/_08192026", "/AgendaCenter/ViewFile/Nothing/_08192026")
    fetch_meetings.run(malden, FakeAgendaCenter(unposted), tmp_path, now=NOW)
    late = next(m for m in load_store(tmp_path).values() if m["date"] == "2026-08-19" and m.get("source") == "agendacenter")
    assert not late.get("minutes_url")
    later = NOW.replace(month=12)
    fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=later)
    fetch_minutes.run_linked(malden, FakeAgendaCenter(), tmp_path, now=later)
    assert load_store(tmp_path)[late["id"]]["minutes"]


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


def test_a_calendar_that_cant_be_parsed_doesnt_stop_the_others(malden, tmp_path, monkeypatch):
    """A layout change that breaks one calendar's parser (an error, not a failed request) counts that
    calendar as failed; the others' meetings are still saved."""
    real = fetch_meetings.calendars

    def broken(*args):
        raise ValueError("time data '7:00 PM EST' does not match format")
    monkeypatch.setattr(fetch_meetings, "calendars", lambda config: [
        fetch_meetings.Calendar("Broken", broken, lambda m: False), *real(config)])
    status = fetch_meetings.run(malden, FakeAgendaCenter(), tmp_path, now=NOW)
    assert status["failed_calendars"] == ["Broken"] and "ValueError" in status["errors"][0]
    assert "agendacenter-4453" in load_store(tmp_path)


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
    board's address for the day (pipeline/identity.py), with both agendas. Both were first recorded
    in this run, so neither listing's own page was ever published, and none says it moved."""
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
    assert m["url"] == "/meetings/2026-09-29-city-council-finance-committee/" and m["also_urls"] == []


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
