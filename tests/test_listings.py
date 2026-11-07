"""A CivicPlus city calendar read month by month, and one meeting listed in more
than one place shown as one (pipeline/listings.py). Run offline against Malden's
saved calendar (tests/fixtures/civicplus_list_2026-10.html) and Agenda Center."""

import copy
import itertools
import json
from datetime import date, datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse
from test_agendacenter import COMMITTEES, PAGE as AGENDA_PAGE

from pipeline import build_site, civicplus, fetch_meetings, identity, listings, network, summarize
from pipeline.fetch_meetings import load_store
from pipeline.http import FetchError

BASE = "https://www.cityofmalden.org"
LIST_PAGE = (FIXTURES / "civicplus_list_2026-10.html").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 2, 7, 0, tzinfo=TZ)


# ---- The calendar's list view ------------------------------------------------

def test_list_url():
    assert civicplus.list_url(BASE, date(2026, 11, 1)) == f"{BASE}/Calendar.aspx?CID=0&view=list&month=11&year=2026"


def test_list_events():
    events = civicplus.parse_list(LIST_PAGE, BASE)
    assert len(events) == 33
    assert {e["calendar"] for e in events} == {"Boards and Commissions Meetings", "Main Calendar", "Teen Center"}
    school = next(e for e in events if e["id"] == "6396")
    assert school == {
        "id": "6396", "calendar_id": "26", "calendar": "Boards and Commissions Meetings",
        "source_url": f"{BASE}/Calendar.aspx?EID=6396", "raw_title": "School Committee Meeting",
        "date": "2026-10-05", "start_time": "18:00", "end_time": None,
        "location_name": "Herbert L. Jackson Council Chamber Room 106", "address": "215 Pleasant St., Malden, MA 02148",
        "title": "School Committee Meeting", "body": "School Committee", "status": "scheduled", "special": False,
    }


def test_a_note_in_the_location_box_is_not_a_place():
    """Malden's Affordable Housing Trust gives its Teams link where the place would be."""
    trust = next(e for e in civicplus.parse_list(LIST_PAGE, BASE) if e["body"] == "Affordable Housing Trust Fund")
    assert (trust["start_time"], trust["end_time"]) == ("17:00", "18:00")
    assert trust["location_name"] == "" and trust["address"] == ""


@pytest.mark.parametrize("title, body, shown", [
    ("CITY COUNCIL REGULAR MEETING", "City Council", "City Council Regular Meeting"),
    ("PLANNING BOARD - PUBLIC HEARING", "Planning Board", "Planning Board - Public Hearing"),
    ("LAWRENCE SCHOOL COMMITTEE", "Lawrence School Committee", "Lawrence School Committee"),
    ("Regular City Council Meeting", "City Council", "Regular City Council Meeting"),
    ("Community Preservation Committee (CPC)", "Community Preservation Committee", "Community Preservation Committee (CPC)"),
    ("Beverly License Board Meeting - Agenda - October 1, 2026", "Beverly License Board",
     "Beverly License Board Meeting - Agenda - October 1, 2026"),
    ("CANCELLED---Budget/Managment Analyst Preliminary Screening Committee Meeting",
     "Budget/Managment Analyst Preliminary Screening Committee", "Budget/Managment Analyst Preliminary Screening Committee Meeting"),
    # A committee of the board named after it is the committee's meeting, not the board's (Lawrence).
    ("City Council - Ordinance Committee", "City Council Ordinance Committee", "City Council - Ordinance Committee"),
    ("City Council - Committee of the Whole", "City Council Committee of the Whole",
     "City Council - Committee of the Whole"),
    ("City Council - Public Safety and Emergency Management Committee",
     "City Council Public Safety and Emergency Management Committee",
     "City Council - Public Safety and Emergency Management Committee"),
    ("School Committee - Finance Subcommittee Meeting", "School Committee Finance Subcommittee",
     "School Committee - Finance Subcommittee Meeting"),
])
def test_calendar_titles(title, body, shown):
    parsed = civicplus.parse_title(title)
    assert (parsed["body"], parsed["title"]) == (body, shown)


def test_tidy_case_keeps_short_abbreviations():
    assert civicplus.tidy_case("LAWRENCE HOUSING AUTHORITY [LHA] BOARD OF COMMISSIONERS") == \
        "Lawrence Housing Authority [LHA] Board of Commissioners"


# ---- Reading it ---------------------------------------------------------------

class FakeMalden:
    """Malden's calendar for October (other months empty), an empty page for each event, its
    Agenda Center, and a PDF for any agenda."""

    def __init__(self, agenda_page: str = "", list_page: str = LIST_PAGE, event_pages: dict | None = None):
        self.agenda_page = agenda_page
        self.list_page = list_page
        self.event_pages = event_pages or {}
        self.pdf = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if "Calendar.aspx?CID=0&view=list" in url:
            return FakeResponse((self.list_page if "month=10&" in url else "<html></html>").encode())
        if "Calendar.aspx?EID=" in url:
            return FakeResponse(self.event_pages.get(url.rsplit("=", 1)[1], "<html></html>").encode())
        if "/AgendaCenter/Search/" in url:
            return FakeResponse(self.agenda_page.encode())
        if "/AgendaCenter/ViewFile/" in url:
            return FakeResponse(self.pdf + f"\n% {url}\n".encode())
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture
def malden(config):
    town = copy.deepcopy(config)
    for table in ("archive", "drive_meetings"):
        town.pop(table, None)
    town["meetings"] = {
        "base_url": BASE,
        "civicplus": {"calendars": ["Boards and Commissions Meetings"]},
        "aliases": {"Rules & Ordinance Committee": "City Council Rules and Ordinance Committee"},
    }
    return town


def test_reads_this_month_and_two_more(malden, tmp_path):
    client = FakeMalden()
    status = fetch_meetings.run(malden, client, tmp_path, now=NOW)
    months = [u.split("month=")[1] for u in client.urls if "view=list" in u]
    assert months == ["10&year=2026", "11&year=2026", "12&year=2026"]
    # Without an Agenda Center, each upcoming meeting's page is read for its online link and agenda.
    assert len([u for u in client.urls if "EID=" in u]) == 10
    assert status["calendars"]["city calendar"]["listed"] == 11
    store = load_store(tmp_path)
    assert {m["body"] for m in store.values()} >= {"School Committee", "City Council", "Malden Housing Authority",
                                                    "City Council Rules and Ordinance Committee"}
    # Only the calendar named in [meetings.civicplus] calendars: no Teen Center, no Main Calendar.
    assert not any("Vaccine" in m["title"] for m in store.values())
    council = store["6395"]
    assert (council["source_url"], council["start_time"], council["listed"]) == (f"{BASE}/Calendar.aspx?EID=6395", "19:00", True)


def test_patterns_pick_meetings_from_every_calendar(malden, tmp_path):
    malden["meetings"]["civicplus"] = {"include_pattern": r"\b(council|committee)\b", "exclude_pattern": r"\bcultural\b"}
    fetch_meetings.run(malden, FakeMalden(), tmp_path, now=NOW)
    bodies = {m["body"] for m in load_store(tmp_path).values()}
    assert "Food Policy Council" in bodies          # on the Main Calendar
    assert "Malden Cultural Council" not in bodies
    assert "Malden Housing Authority" not in bodies


def read_pages(client):
    return sorted(u.rsplit("=", 1)[1] for u in client.urls if "EID=" in u)


def test_a_town_with_an_agenda_center_reads_event_pages_only_for_their_agenda_link(malden, tmp_path):
    """Only the next two weeks' meetings (fetch_meetings.AGENDA_LINK_DAYS), and not one whose board the
    Agenda Center lists that day by the same name: the City Council's of October 6 is there."""
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    client = FakeMalden(AGENDA_PAGE.replace("09222026-4441", "10062026-4441"))
    fetch_meetings.run(malden, client, tmp_path, now=NOW)
    # And yesterday's (6392, October 1), past now: read once for its link (PAST_LINK_DAYS).
    assert read_pages(client) == ["6309", "6392", "6393", "6394", "6396"]
    # A page with no "Download Agenda" link links no listing.
    store = load_store(tmp_path)
    assert all("agenda_listing" not in m for m in store.values())


def download_agenda(href: str) -> str:
    return f'<a id="ctl00_ctl00_MainContent_ModuleContent_ctl00_ctl04_lnkDownloadAgenda" href="{href}">Download Agenda</a>'


CLIMATE = "Climate Action &amp; Sustainability Commission Meeting"


def test_a_calendar_entry_and_the_agenda_its_page_links_are_one_meeting_whatever_their_names(malden, tmp_path):
    """The calendar calls Malden's climate commission "Climate Action & Sustainability Commission"; the
    Agenda Center files its agendas as "Commission on Climate Action and Sustainability". The event's
    "Download Agenda" link names the Agenda Center's listing, so the meeting is listed once."""
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    agendas = AGENDA_PAGE.replace("09092026-4408", "10132026-4408")
    calendar = LIST_PAGE.replace("<span>Malden Housing Authority </span>", f"<span>{CLIMATE}</span>", 1)
    page = download_agenda("/AgendaCenter/Commission-on-Climate-Action-and-Sustain-59/#_10132026-4408")
    client = FakeMalden(agendas, calendar, {"6309": page})
    status = fetch_meetings.run(malden, client, tmp_path, now=NOW)

    store = load_store(tmp_path)
    assert store["6309"]["agenda_listing"] == "agendacenter-4408"
    assert store["6309"]["body"] == "Commission on Climate Action and Sustainability"
    assert status["renamed"] == 1
    built = build_site.load_meetings(tmp_path, NOW.date())
    [m] = [m for m in built["all"] if m["date"] == "2026-10-13" and "Climate" in m["body"]]
    assert {x["id"] for x in m["listings"]} == {"6309", "agendacenter-4408"}
    assert (m["start_time"], m["agenda"]["source_url"]) == ("08:30", f"{BASE}/AgendaCenter/ViewFile/Agenda/_10132026-4408")

    # The link is known: the next run reads no page for it, and keeps the meeting together.
    again = FakeMalden(agendas, calendar, {"6309": page})
    fetch_meetings.run(malden, again, tmp_path, now=NOW)
    assert "6309" not in read_pages(again)
    assert load_store(tmp_path)["6309"]["body"] == "Commission on Climate Action and Sustainability"


def test_a_calendar_entry_linking_another_boards_agenda_keeps_its_name(malden, tmp_path):
    """The Housing Authority's entry of October 13 links the climate commission's agenda of that day (a
    clerk's slip): the names share no word but kinds of body, so they stay two meetings."""
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    agendas = AGENDA_PAGE.replace("09092026-4408", "10132026-4408")
    page = download_agenda("/AgendaCenter/Commission-on-Climate-Action-and-Sustain-59/#_10132026-4408")
    status = fetch_meetings.run(malden, FakeMalden(agendas, event_pages={"6309": page}), tmp_path, now=NOW)
    store = load_store(tmp_path)
    assert store["6309"]["agenda_listing"] == "agendacenter-4408"
    assert store["6309"]["body"] == "Malden Housing Authority"
    assert status["renamed"] == 0


def past_listing(eid, day, body, title):
    return {"id": eid, "slug": f"{day}-{eid}", "date": day, "body": body, "title": title,
            "source_url": f"{BASE}/Calendar.aspx?EID={eid}", "start_time": "17:30", "status": "scheduled",
            "first_seen": "2026-08-01T07:00:00-04:00", "last_seen": f"{day}T07:00:00-04:00", "listed": True}


class GonePages(FakeMalden):
    """Malden's site, with some event pages gone (404)."""

    def __init__(self, *args, gone=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.gone = set(gone)

    def get(self, url):
        if "Calendar.aspx?EID=" in url and url.rsplit("=", 1)[1] in self.gone:
            self.urls.append(url)
            raise FetchError(f"{url}: HTTP 404", 404)
        return super().get(url)


def test_a_past_listings_page_is_read_once_for_the_agenda_it_links(malden, tmp_path):
    """The calendar no longer lists September's meetings, so their pages weren't read for the agenda they
    link. Within fetch_meetings.PAST_LINK_DAYS they are, once each: the climate commission's of September 9
    is put together with its Agenda Center listing; a page that's gone isn't asked for again; one older than
    the window isn't read."""
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-06-01", "committees": COMMITTEES}
    (tmp_path / "meetings").mkdir()
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps({
        "6200": past_listing("6200", "2026-09-09", "Climate Action & Sustainability Commission", CLIMATE.replace("&amp;", "&")),
        "6201": past_listing("6201", "2026-09-10", "Ward 4 Meeting", "Ward 4 Meeting"),
        "6100": past_listing("6100", "2026-06-10", "Ward 4 Meeting", "Ward 4 Meeting"),
    }))
    page = download_agenda("/AgendaCenter/Commission-on-Climate-Action-and-Sustain-59/#_09092026-4408")
    client = GonePages(AGENDA_PAGE, event_pages={"6200": page}, gone={"6201"})
    status = fetch_meetings.run(malden, client, tmp_path, now=NOW)
    assert {"6200", "6201"} <= set(read_pages(client)) and "6100" not in read_pages(client)
    store = load_store(tmp_path)
    assert store["6200"]["agenda_listing"] == "agendacenter-4408"
    assert store["6200"]["body"] == "Commission on Climate Action and Sustainability"
    assert "agenda_link_checked" in store["6201"] and "agenda_listing" not in store["6201"]
    assert any("HTTP 404" in e for e in status["errors"])
    # Read once: the next run reads neither page again.
    again = GonePages(AGENDA_PAGE, event_pages={"6200": page}, gone={"6201"})
    fetch_meetings.run(malden, again, tmp_path, now=NOW)
    assert not {"6200", "6201"} & set(read_pages(again))


def test_past_pages_share_the_runs_event_pages(malden, tmp_path):
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    malden["meetings"]["max_event_pages"] = 7
    (tmp_path / "meetings").mkdir()
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps(
        {str(6100 + i): past_listing(str(6100 + i), f"2026-09-{i + 1:02d}", "Ward 4 Meeting", "Ward 4 Meeting")
         for i in range(5)}))
    client = FakeMalden(AGENDA_PAGE)
    fetch_meetings.run(malden, client, tmp_path, now=NOW)
    # The upcoming meetings' pages first (five), then past listings', newest first: yesterday's (6392) and
    # September 5's, and no more.
    pages = read_pages(client)
    assert len(pages) == 7 and {"6392", "6104"} <= set(pages) and "6103" not in pages


def test_a_subcommittees_agenda_takes_the_calendars_name():
    """Beverly's calendar names the Airport Commission's subcommittee of October 13; its Agenda Center files
    the agenda under the commission, as "Airport Commission Subcommittee" (agendacenter.body_for). The
    calendar's listing links it: the agenda takes the calendar's name, which says which subcommittee."""
    sub = "Beverly Regional Airport Commission Subcommittee on Mission"
    store = {
        "3934": {"id": "3934", "date": "2026-10-13", "body": sub, "agenda_listing": "agendacenter-2943"},
        "agendacenter-2943": {"id": "agendacenter-2943", "source": "agendacenter", "date": "2026-10-13",
                              "body": "Airport Commission Subcommittee", "subcommittee_of": "Airport Commission"},
    }
    calendar = fetch_meetings.Calendar("city calendar", lambda *a: [], lambda m: m["id"].isdigit())
    assert fetch_meetings.rename(store, [calendar], {}, "Beverly", {}) == 1
    assert store["agendacenter-2943"]["body"] == store["3934"]["body"] == sub
    # Linked from two calendar listings, it keeps its own name, and they take it, as any other.
    store["agendacenter-2943"]["body"] = "Airport Commission Subcommittee"
    store["3935"] = {**store["3934"], "id": "3935", "body": "Beverly Regional Airport Commission Subcommittee on Access"}
    fetch_meetings.rename(store, [calendar], {}, "Beverly", {})
    assert store["agendacenter-2943"]["body"] == "Airport Commission Subcommittee"
    # A calendar name that names no subcommittee takes the Agenda Center's, as before.
    store = {
        "3943": {"id": "3943", "date": "2026-10-16", "body": "Beverly Retirement Board", "agenda_listing": "agendacenter-2955"},
        "agendacenter-2955": {"id": "agendacenter-2955", "source": "agendacenter", "date": "2026-10-16",
                              "body": "Contributory Retirement Board", "subcommittee_of": None},
    }
    fetch_meetings.rename(store, [calendar], {}, "Beverly", {})
    assert store["3943"]["body"] == "Contributory Retirement Board"


def test_past_links_reach_back_as_far_as_the_report_of_meetings_listed_twice():
    from pipeline import document_gaps
    assert fetch_meetings.PAST_LINK_DAYS == document_gaps.WINDOW_DAYS


def test_a_listing_no_longer_listed_is_named_as_a_new_one_would_be(malden, tmp_path):
    """A calendar lists only its upcoming meetings, so September's entries aren't read again. An alias
    added since, or a title the parser now reads differently ("City Council - Ordinance Committee" is
    the committee's, since engine v1.80.0), renames them all the same."""
    stored = {
        "6200": {"id": "6200", "slug": "2026-09-09-climate-action-and-sustainability-commission",
                 "date": "2026-09-09", "body": "Climate Action & Sustainability Commission", "title": CLIMATE.replace("&amp;", "&"),
                 "source_url": f"{BASE}/Calendar.aspx?EID=6200", "start_time": "17:30", "status": "scheduled",
                 "first_seen": "2026-09-01T07:00:00-04:00", "last_seen": "2026-09-09T07:00:00-04:00", "listed": True},
        "6201": {"id": "6201", "slug": "2026-09-15-city-council", "date": "2026-09-15", "body": "City Council",
                 "title": "City Council - Ordinance Committee", "source_url": f"{BASE}/Calendar.aspx?EID=6201",
                 "start_time": "18:00", "status": "scheduled", "first_seen": "2026-09-01T07:00:00-04:00",
                 "last_seen": "2026-09-15T07:00:00-04:00", "listed": True},
    }
    (tmp_path / "meetings").mkdir()
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps(stored))
    malden["meetings"]["aliases"]["Climate Action & Sustainability Commission"] = "Commission on Climate Action and Sustainability"
    status = fetch_meetings.run(malden, FakeMalden(), tmp_path, now=NOW)
    store = load_store(tmp_path)
    assert store["6200"]["body"] == "Commission on Climate Action and Sustainability"
    assert store["6201"]["body"] == "City Council Ordinance Committee"
    assert "history" not in store["6200"] and "history" not in store["6201"]
    assert status["renamed"] == 2
    # Named as they are now, they aren't renamed again.
    assert fetch_meetings.run(malden, FakeMalden(), tmp_path, now=NOW)["renamed"] == 0


@pytest.mark.parametrize("href, number", [
    ("/AgendaCenter/Commission-on-Climate-Action-and-Sustain-59/#_10132026-4408", "4408"),
    ("/AgendaCenter/ViewFile/Agenda/_10132026-4408", "4408"),
    ("/Archive.aspx?ADID=20124", None),
])
def test_an_event_pages_agenda_link_names_its_agenda_center_listing(href, number):
    assert civicplus.parse_event_page(download_agenda(href), BASE).get("agenda_center_id") == number


def test_calendar_and_agenda_center_are_one_meeting(malden, tmp_path):
    """Malden's City Council meeting of October 6 is on the calendar, at 7 PM, and its agenda
    in the Agenda Center (here, the saved September 22 agenda moved to October 6)."""
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    page = AGENDA_PAGE.replace("09222026-4441", "10062026-4441")
    fetch_meetings.run(malden, FakeMalden(page), tmp_path, now=NOW)
    store = load_store(tmp_path)
    assert store["6395"]["date"] == store["agendacenter-4441"]["date"] == "2026-10-06"

    built = build_site.load_meetings(tmp_path, NOW.date())
    council = [m for m in built["all"] if m["date"] == "2026-10-06" and m["body"] == "City Council"]
    assert len(council) == 1
    m = council[0]
    assert (m["start_time"], m["location_name"]) == ("19:00", "Herbert L. Jackson Council Chamber Room 106")
    assert m["agenda"]["source_url"] == f"{BASE}/AgendaCenter/ViewFile/Agenda/_10062026-4441"
    assert m["same_as"] == "one_time"
    assert {x["source"] for x in m["listings"]} == {"calendar", "agendacenter"}
    # The time comes from the calendar, so a missing one is "not listed on the city calendar".
    assert m["source"] == "calendar"


# ---- The rule -----------------------------------------------------------------

def record(id, body="Planning Board", date="2026-10-06", time=None, **fields):
    return {"id": id, "slug": f"{date}-{id}", "date": date, "body": body, "start_time": time, "first_seen": "2026-10-01T07:00:00",
            "title": f"{body} Meeting", "status": "scheduled", **fields}


def ids(store, ledger=None):
    """The records of each meeting listed more than once."""
    return sorted([m["id"] for m in g["records"]] for g in identity.meetings(store, ledger) if len(g["records"]) > 1)


def store_of(*records):
    return {m["id"]: m for m in records}


def test_same_board_day_and_time_is_one_meeting():
    store = store_of(record("1", time="19:00", source="calendar"), record("2", time="19:00", source="civicclerk", special=True))
    assert ids(store) == [["1", "2"]]


def test_different_times_are_two_meetings_and_an_untimed_listing_stays_apart():
    store = store_of(record("1", time="18:00"), record("2", time="19:00"), record("agendacenter-5", source="agendacenter"))
    assert ids(store) == []


def test_an_untimed_listing_goes_with_the_one_timed_meeting():
    store = store_of(record("1", time="19:00"), record("agendacenter-5", source="agendacenter"),
                     record("agendacenter-6", source="agendacenter", posted_title="Planning Board Agenda - REVISED"))
    assert ids(store) == [["1", "agendacenter-5", "agendacenter-6"]]


@pytest.mark.parametrize("other", [
    "Council on Aging Grant Committee Meeting Agenda (PDF)",
    "Mission Statement Subcommittee of the Council on Aging Meeting Agenda (PDF)",
    "Council on Aging Special Meeting",
    "Council on Aging Public Hearing",
])
def test_a_committee_posted_under_its_board_is_its_own_meeting(other):
    """An Agenda Center lists a board's committees under the board: Beverly's Council on Aging
    posts its Board of Directors' and its Grant Committee's agendas for one day."""
    store = store_of(record("agendacenter-1", "Council on Aging", posted_title="Council on Aging Board of Directors Meeting Agenda (PDF)"),
                     record("agendacenter-2", "Council on Aging", posted_title=other))
    assert ids(store) == []


def test_the_same_agenda_posted_twice_is_one_meeting():
    """Malden's Housing Authority posts each agenda twice, under two titles."""
    store = store_of(record("agendacenter-4422", "Malden Housing Authority", posted_title="Board of Commissioners Meeting"),
                     record("agendacenter-4437", "Malden Housing Authority", posted_title="Malden Housing Authority Meeting"))
    assert ids(store) == [["agendacenter-4422", "agendacenter-4437"]]


def test_board_names_match_whatever_their_case():
    store = store_of(record("1", "CITY COUNCIL", time="19:00"), record("agendacenter-2", "City Council", source="agendacenter"))
    assert ids(store) == [["1", "agendacenter-2"]]


def test_a_variant_is_its_own_meeting():
    store = store_of(record("drive-1", "School Committee", source="drive", variant=""),
                     record("drive-2", "School Committee", source="drive", variant="Governance Workshop"))
    assert ids(store) == []


def test_the_first_recorded_keeps_its_page():
    early = record("agendacenter-9", source="agendacenter", first_seen="2026-09-01T07:00:00")
    store = store_of(record("1", time="19:00"), early)
    combined, moved = listings.combined(store)
    assert list(combined) == ["agendacenter-9"] and moved == {"1": "agendacenter-9"}
    assert combined["agendacenter-9"]["slug"] == early["slug"]
    assert combined["agendacenter-9"]["start_time"] == "19:00"


def test_a_new_agenda_after_a_cancellation_notice_is_a_meeting_back_on():
    """Lawrence posted 'BUDGET AND FINANCE COMMITTEE - CANCELLED' (1922), then a revised agenda (1925)."""
    store = store_of(record("agendacenter-1922", source="agendacenter", status="cancelled"),
                     record("agendacenter-1925", source="agendacenter"))
    assert listings.combined(store)[0]["agendacenter-1922"]["status"] == "scheduled"
    store = store_of(record("agendacenter-1922", source="agendacenter"),
                     record("agendacenter-1925", source="agendacenter", status="cancelled"))
    assert listings.combined(store)[0]["agendacenter-1922"]["status"] == "cancelled"


def test_a_cancellation_in_one_place_stands():
    """A city that cancels a meeting on its calendar can leave its agenda as it was."""
    store = store_of(record("1", time="19:00", status="cancelled"), record("agendacenter-2", source="agendacenter"))
    assert listings.combined(store)[0]["1"]["status"] == "cancelled"


def test_documents_and_history_from_every_listing():
    doc = lambda sha, at: {"sha256": sha, "fetched_at": at, "file": f"{sha}.pdf", "bytes": 1}
    calendar = record("1", time="19:00", history=[{"at": "2026-10-03T07:00:00", "field": "start_time", "old": "18:00", "new": "19:00"}])
    first = record("agendacenter-2", source="agendacenter", agendas=[doc("a", "2026-10-01T07:00:00")])
    second = record("agendacenter-3", source="agendacenter", agendas=[doc("b", "2026-10-02T07:00:00"), doc("a", "2026-10-01T07:00:00")])
    m = listings.combined(store_of(calendar, first, second), lambda r: r.get("source", "calendar"))[0]["1"]
    assert [d["sha256"] for d in m["agendas"]] == ["a", "b"]
    assert [(h["field"], h["listing"]) for h in m["history"]] == [("start_time", "calendar")]
    assert [x["id"] for x in m["listings"]] == ["1", "agendacenter-2", "agendacenter-3"]


def test_the_same_meeting_whatever_order_the_store_has_its_records_in():
    """Two timed listings of one kind each, and an untimed listing of the second's kind: it joins them,
    since one of the meeting's listings is of its kind, whichever was recorded first and whatever the
    store's order."""
    records = [record("1", time="19:00"), record("civicclerk-2", time="19:00", source="civicclerk", special=True),
               record("agendacenter-5", source="agendacenter", special=True, agenda_url="https://city.example/a5")]
    results = set()
    for order in itertools.permutations(records):
        store = store_of(*copy.deepcopy(order))
        combined, moved = listings.combined(store)
        results.add(json.dumps([sorted(combined.items()), sorted(moved.items())], sort_keys=True))
    assert len(results) == 1
    assert ids(store_of(*records)) == [["1", "agendacenter-5", "civicclerk-2"]]


def test_links_the_sources_disagree_on_follow_the_listing_order():
    """A district's calendar feed (ical-3) and its Finalsite page (finalsite-1) both link documents for one
    meeting: the board's own posting is linked, and the page lists the feed's listing first. Ids from
    different sources aren't compared (by id, ical-3 would be the newer)."""
    feed = record("ical-3", "Board of Education", time="19:00", source="ical", documents_url="https://schools.example/feed")
    site = record("finalsite-1", "Board of Education", time="19:00", source="finalsite", documents_url="https://schools.example/docs")
    for store in (store_of(feed, site), store_of(site, feed)):
        m = next(iter(listings.combined(copy.deepcopy(store))[0].values()))
        assert m["documents_url"] == "https://schools.example/docs"
        assert [x["id"] for x in m["listings"]] == ["ical-3", "finalsite-1"]


def test_removed_only_when_every_listing_is():
    store = store_of(record("1", time="19:00", listed=False), record("agendacenter-2", source="agendacenter"))
    assert listings.combined(store)[0]["1"]["listed"] is True
    store["agendacenter-2"]["listed"] = False
    assert listings.combined(store)[0]["1"]["listed"] is False


def test_counted_and_summarized_once(tmp_path):
    doc = {"sha256": "a" * 64, "fetched_at": "2026-10-01T07:00:00", "file": "a.pdf", "bytes": 1}
    store = store_of(record("1", time="19:00"), record("agendacenter-2", source="agendacenter", agendas=[doc]),
                     record("agendacenter-3", source="agendacenter", agendas=[{**doc, "sha256": "b" * 64}]))
    (tmp_path / "meetings").mkdir()
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps(store))
    assert network.activity(tmp_path, today=date(2026, 10, 1))["meetings_by_date"] == {"2026-10-06": 1}
    pending = summarize.pending_documents(tmp_path, "2026-10-01", "a-model")
    assert [d["sha256"] for _, _, d in pending] == ["b" * 64]


def save_store(data_dir, store):
    (data_dir / "meetings").mkdir()
    (data_dir / "meetings" / "meetings.json").write_text(json.dumps(store))


def test_an_agenda_posted_again_as_the_minutes_is_the_agenda(tmp_path):
    """Lowell's Agenda Center gave its Board of Parks agenda of 2026-10-28 as the meeting's minutes too: the
    meeting has no minutes yet, and the file is summarized once, as its agenda. Its real minutes come in as
    any do."""
    doc = lambda sha: {"sha256": sha * 64, "fetched_at": "2026-10-29T07:00:00", "file": f"{sha}.pdf", "bytes": 1}
    store = store_of(record("agendacenter-4077", source="agendacenter", date="2026-10-28",
                            agendas=[doc("a")], minutes=[doc("a")]))
    assert listings.combined(store)[0]["agendacenter-4077"]["minutes"] == []
    save_store(tmp_path, store)
    assert [(k, d["sha256"]) for k, _, d in summarize.pending_documents(tmp_path, "2026-11-06", "a-model")] == [("agenda", "a" * 64)]

    store["agendacenter-4077"]["minutes"].append(doc("b"))
    assert [d["sha256"] for d in listings.combined(store)[0]["agendacenter-4077"]["minutes"]] == ["b" * 64]


@pytest.mark.parametrize("name, held, kept", [
    ("10-9-24 Minutes - Reg. meeting.pdf", "2026-10-28", False),                 # Waltham's Budget Workshop
    ("Regular Planning Board Meeting January 13 2025", "2026-01-13", True),      # a year mistyped: Beverly's
    ("Regular Planning Board Meeting January 13 2025", "2026-10-28", False),
    ("Minutes 10-28-26.pdf", "2026-10-28", True),
    ("Board of Commissioners Meeting Recalled from 10-8-26", "2026-10-28", True),  # weeks off: kept
    ("Approved minutes.pdf", "2026-10-28", True),                                # no date: kept
])
def test_minutes_whose_own_name_dates_them_to_another_meeting_are_not_its_minutes(name, held, kept):
    doc = {"sha256": "m" * 64, "fetched_at": "2026-10-29T07:00:00", "file": "m.pdf", "bytes": 1, "original_filename": name}
    agenda = {**doc, "sha256": "a" * 64, "original_filename": "Budget Workshop.pdf"}
    store = store_of(record("page-1", "School Committee", date=held, source="page", agendas=[agenda], minutes=[doc]))
    assert bool(listings.combined(store)[0]["page-1"]["minutes"]) is kept
    # A meeting known only from its minutes keeps them, whatever their name says.
    del store["page-1"]["agendas"]
    assert listings.combined(store)[0]["page-1"]["minutes"] == [doc]


def test_a_minutes_title_dates_them_when_their_file_has_no_name():
    doc = {"sha256": "m" * 64, "fetched_at": "2026-01-14T07:00:00", "file": "m.pdf", "bytes": 1,
           "title": "Board of Assessors Regular Meeting 01/13/2025"}
    agenda = {**doc, "sha256": "a" * 64, "title": "Board of Assessors agenda"}
    store = store_of(record("agendacenter-1", "Board of Assessors", date="2026-01-13", agendas=[agenda], minutes=[doc]))
    assert listings.combined(store)[0]["agendacenter-1"]["minutes"] == [doc]
    store["agendacenter-1"]["minutes"][0] = {**doc, "title": "Board of Assessors Regular Meeting 06/02/2025"}
    assert listings.combined(store)[0]["agendacenter-1"]["minutes"] == []


def test_a_file_one_meeting_has_as_its_agenda_and_another_as_its_minutes_is_summarized_once(tmp_path):
    """A summary is saved by its file's hash: made as an agenda and as minutes, each would replace the other
    every run. It's made as the agenda."""
    doc = {"sha256": "a" * 64, "fetched_at": "2026-10-29T07:00:00", "file": "a.pdf", "bytes": 1}
    save_store(tmp_path, store_of(record("1", date="2026-10-28", minutes=[doc]),
                                  record("2", body="Conservation Commission", date="2026-11-04", agendas=[doc])))
    assert [(k, m["id"]) for k, m, _ in summarize.pending_documents(tmp_path, "2026-11-06", "a-model")] == [("agenda", "2")]


@pytest.mark.parametrize("name, expected", [
    ("Open Space and Recreation Committee", "Open Space & Recreation Committee"),
    ("Malden Cultural Council", "Cultural Council"),
    ("Malden Housing Authority", "Malden Housing Authority"),     # its own name
    ("Board of Health", "Board of Health"),                       # not recorded yet
    ("Board of Assessor", "Board of Assessors"),
    ("City Council Finance Sub Committee", "City Council Finance Subcommittee"),
])
def test_a_board_keeps_the_name_first_recorded(name, expected):
    known = identity.known_boards(store_of(*(record(str(n), b) for n, b in enumerate((
        "Open Space & Recreation Committee", "Cultural Council", "Malden Housing Authority", "Board of Assessors",
        "City Council Finance Subcommittee")))), "Malden")
    assert fetch_meetings.known_body(name, known, "Malden") == expected


def test_a_meeting_posted_three_times_has_two_moved_pages(malden, tmp_path):
    """Each earlier listing's page, published before identities were kept, says where the meeting is now,
    under a title of its own, and the Worker answers its address with a redirect (redirects.json)."""
    from conftest import BUILT_AT
    from test_site import parse
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    page = AGENDA_PAGE.replace("09222026-4441", "10062026-4441").replace("09222026-4442", "10062026-4442")
    page = page.replace("Finance Committee Agenda - September 22, 2026", "City Council Agenda - REVISED")
    fetch_meetings.run(malden, FakeMalden(page), tmp_path, now=NOW)
    # A town whose listings' own pages were published before identities were kept.
    (tmp_path / "meetings" / identity.LEDGER).unlink()
    malden["sections"] = [s for s in malden["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "summaries"):
        malden.pop(table, None)
    out = tmp_path / "site"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: malden)
        build_site.build("malden", out, data_dir=tmp_path, now=BUILT_AT)
    council = next(m for m in build_site.load_meetings(tmp_path, NOW.date())["all"]
                   if m["date"] == "2026-10-06" and m["body"] == "City Council")
    assert len(council["also_urls"]) == 2
    titles = [parse(out / u.strip("/") / "index.html").title for u in council["also_urls"]]
    assert len(set(titles)) == 2 and all("moved (listing" in t for t in titles)
    moved = (out / council["also_urls"][0].strip("/") / "index.html").read_text()
    assert f'url={council["url"]}' in moved and 'name="robots" content="noindex"' in moved
    # A moved page sends the reader on at once, so the browser checks leave it out (site_checks/pages.py);
    # the page it points to stays in.
    from site_checks.pages import redirects
    assert redirects(moved)
    assert not redirects((out / council["url"].strip("/") / "index.html").read_text())
    sitemap = (out / "sitemap.xml").read_text()
    assert council["url"] in sitemap and council["also_urls"][0] not in sitemap
    moves = json.loads((out / build_site.REDIRECTS_FILE).read_text())
    for also in council["also_urls"]:
        assert moves[also] == council["url"]


def test_identities_are_kept_and_every_address_a_meeting_had_still_leads_to_it(malden, tmp_path):
    """A run keeps which meeting each listing is (data/meetings/identities.json). An address a meeting
    had before (a listing's own page, published before identities were kept) sends readers to the
    meeting's page; a listing without a time that could be either of a board's two meetings that day
    is shown on its own, and says so."""
    from conftest import BUILT_AT
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    page = AGENDA_PAGE.replace("09222026-4441", "10062026-4441")
    fetch_meetings.run(malden, FakeMalden(page), tmp_path, now=NOW)
    ledger = identity.load(tmp_path)
    council = next(mid for mid, e in ledger["meetings"].items() if "6395" in e["listings"])
    assert "agendacenter-4441" in ledger["meetings"][council]["listings"]
    ledger["meetings"][council]["addresses"] = ["2026-10-06-city-council-as-first-posted"]
    identity.save(tmp_path, ledger)
    store = load_store(tmp_path)
    seen = "2026-10-02T07:00:00-04:00"
    for id, time, title, source in (("9001", "17:00", "Planning Board Meeting", "calendar"), ("9002", "19:00", "Planning Board Meeting", "calendar"),
                                    ("agendacenter-9003", None, "Planning Board Agenda", "agendacenter")):
        store[id] = {"id": id, "slug": f"2026-10-20-planning-board-{id}", "date": "2026-10-20", "body": "Planning Board",
                     "start_time": time, "title": title, "source": source, "status": "scheduled", "listed": True,
                     "first_seen": seen, "last_seen": seen, "source_url": f"{BASE}/{id}"}
    fetch_meetings.save_store(tmp_path, store, malden)
    kept = identity.load(tmp_path)["meetings"]
    assert kept["2026-10-20-planning-board-2"] == {"date": "2026-10-20", "board": "planning board",
                                                   "listings": ["agendacenter-9003"], "unplaced": True}
    assert kept["2026-10-20-planning-board"]["listings"] == ["9001"] and kept["2026-10-20-planning-board-1900"]["listings"] == ["9002"]

    malden["sections"] = [s for s in malden["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "summaries"):
        malden.pop(table, None)
    out = tmp_path / "site"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: malden)
        build_site.build("malden", out, data_dir=tmp_path, now=BUILT_AT)
    moved = (out / "meetings" / "2026-10-06-city-council-as-first-posted" / "index.html").read_text()
    assert f'url=/meetings/{council}/' in moved and 'name="robots" content="noindex"' in moved
    assert f'<link rel="canonical" href="https://{malden["site"]["domain"]}/meetings/{council}/">' in moved
    sitemap = (out / "sitemap.xml").read_text()
    assert f"/meetings/{council}/" in sitemap and "as-first-posted" not in sitemap
    alone = (out / "meetings" / "2026-10-20-planning-board-2" / "index.html").read_text()
    assert "the board has more than one meeting this day, so it can't be told which one this is" in alone
    assert "can't be told" not in (out / "meetings" / "2026-10-20-planning-board-1900" / "index.html").read_text()


def test_a_board_listed_under_two_names_has_one_page(malden, tmp_path):
    """A board one source names with the town's name in front is one board, under its name as first
    recorded, with one page; the page its other name had sends readers there."""
    from conftest import BUILT_AT
    malden["town"]["name"] = "Malden"
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    fetch_meetings.run(malden, FakeMalden(AGENDA_PAGE), tmp_path, now=NOW)
    store = load_store(tmp_path)
    for id, body, date_, seen in (("9101", "Tree Board", "2026-10-07", "2026-10-01T07:00:00-04:00"),
                                  ("agendacenter-9102", "Malden Tree Board", "2026-10-21", "2026-10-02T07:00:00-04:00")):
        store[id] = {"id": id, "slug": f"{date_}-{fetch_meetings.slugify(body)}", "date": date_, "body": body, "start_time": None,
                     "title": f"{body} Meeting", "source": "agendacenter" if "-" in id else "calendar", "status": "scheduled",
                     "listed": True, "first_seen": seen, "last_seen": seen, "source_url": f"{BASE}/{id}"}
    fetch_meetings.save_store(tmp_path, store, malden)
    built = build_site.load_meetings(tmp_path, NOW.date(), town="Malden")
    tree = [b for b in built["boards"] if "Tree Board" in b["name"]]
    assert [(b["name"], len(b["meetings"]), b["also_urls"]) for b in tree] == [
        ("Tree Board", 2, ["/meetings/boards/malden-tree-board/"])]
    # A later fetch stores the listing under the board's name as first recorded (fetch_meetings.known_body),
    # so "Malden Tree Board" is in no record any more: identities.json keeps the name, and its page.
    store = load_store(tmp_path)
    store["agendacenter-9102"]["body"] = "Tree Board"
    fetch_meetings.save_store(tmp_path, store, malden)
    built = build_site.load_meetings(tmp_path, NOW.date(), town="Malden")
    assert [(b["name"], b["also_urls"]) for b in built["boards"] if "Tree Board" in b["name"]] == [
        ("Tree Board", ["/meetings/boards/malden-tree-board/"])]

    malden["sections"] = [s for s in malden["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "summaries"):
        malden.pop(table, None)
    out = tmp_path / "site"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: malden)
        build_site.build("malden", out, data_dir=tmp_path, now=BUILT_AT)
    moved = (out / "meetings" / "boards" / "malden-tree-board" / "index.html").read_text()
    assert "url=/meetings/boards/tree-board/" in moved and "This board's page has moved" in moved
    assert json.loads((out / build_site.REDIRECTS_FILE).read_text())["/meetings/boards/malden-tree-board/"] == "/meetings/boards/tree-board/"
    assert "/meetings/boards/malden-tree-board/" not in (out / "sitemap.xml").read_text()


@pytest.mark.parametrize("lang", ["en", "es"])
def test_moved_pages_of_two_meetings_on_one_day_have_titles_of_their_own(lang):
    """A board that meets twice in a day, each meeting with a moved page (an earlier listing's address):
    the moved pages' titles tell the meetings apart as the meetings' own pages do, by start time or by
    order, so a site's titles stay unique (site_checks: test_every_page_has_accessible_structure)."""
    from jinja2 import Environment, FileSystemLoader

    from pipeline import i18n
    e = Environment(loader=FileSystemLoader(str(build_site.SITE_DIR / "templates")), autoescape=True,
                    trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.i18n"])
    e.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True, pgettext=i18n.pgettext, npgettext=i18n.npgettext)
    e.policies["ext.i18n.trimmed"] = True
    e.filters.update(date=build_site.format_date, time=build_site.format_time)
    template = e.get_template("moved.html")

    def title(meeting, part=None):
        # The block alone: the template sets m from meeting at its top, which a block call skips.
        context = template.new_context({"meeting": meeting, "m": meeting, "part": part})
        return "".join(template.blocks["title"](context)).strip()

    base = {"body": "Council on Aging", "date": "2026-11-10", "same_day": True, "day_part": None}
    with i18n.use(lang):
        by_time = [title({**base, "start_time": "09:30"}), title({**base, "start_time": "14:00"})]
        by_order = [title({**base, "start_time": None, "day_part": _part}) for _part in ("1 of 2", "2 of 2")]
        alone = title({**base, "same_day": False, "start_time": "09:30"})
        listings = [title({**base, "start_time": "14:00"}, part=n) for n in (1, 2)]
    assert len(set(by_time)) == 2 and len(set(by_order)) == 2 and len(set(listings)) == 2
    assert ("2:00 PM" in by_time[1]) if lang == "en" else ("2:00" in by_time[1])
    assert "(2 of 2)" in by_order[1]
    # A board's only meeting that day keeps the plain title, with no time.
    assert "9:30" not in alone and alone.startswith("Council on Aging")
