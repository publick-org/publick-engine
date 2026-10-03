"""A CivicPlus city calendar read month by month, and one meeting listed in more
than one place shown as one (pipeline/listings.py). Run offline against Malden's
saved calendar (tests/fixtures/civicplus_list_2026-10.html) and Agenda Center."""

import copy
import json
from datetime import date, datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse
from test_agendacenter import COMMITTEES, PAGE as AGENDA_PAGE

from pipeline import build_site, civicplus, fetch_meetings, listings, network, summarize
from pipeline.fetch_meetings import load_store

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

    def __init__(self, agenda_page: str = ""):
        self.agenda_page = agenda_page
        self.pdf = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if "Calendar.aspx?CID=0&view=list" in url:
            return FakeResponse((LIST_PAGE if "month=10&" in url else "<html></html>").encode())
        if "Calendar.aspx?EID=" in url:
            return FakeResponse(b"<html></html>")
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


def test_a_town_with_an_agenda_center_reads_no_event_pages(malden, tmp_path):
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    client = FakeMalden(AGENDA_PAGE)
    fetch_meetings.run(malden, client, tmp_path, now=NOW)
    assert not [u for u in client.urls if "EID=" in u]


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


def ids(store):
    return sorted(([m["id"] for m in ms] for ms, _ in listings.groups(store)))


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


@pytest.mark.parametrize("name, expected", [
    ("Open Space and Recreation Committee", "Open Space & Recreation Committee"),
    ("Malden Cultural Council", "Cultural Council"),
    ("Malden Housing Authority", "Malden Housing Authority"),     # its own name
    ("Board of Health", "Board of Health"),                       # not recorded yet
])
def test_a_board_keeps_the_name_first_recorded(name, expected):
    from pipeline.meeting_names import words
    known = {words(b): b for b in ("Open Space & Recreation Committee", "Cultural Council", "Malden Housing Authority")}
    assert fetch_meetings.known_body(name, known, "Malden") == expected


def test_a_meeting_posted_three_times_has_two_moved_pages(malden, tmp_path):
    """Each earlier listing's page says where the meeting is now, under a title of its own."""
    from conftest import BUILT_AT
    from test_site import parse
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    page = AGENDA_PAGE.replace("09222026-4441", "10062026-4441").replace("09222026-4442", "10062026-4442")
    page = page.replace("Finance Committee Agenda - September 22, 2026", "City Council Agenda - REVISED")
    fetch_meetings.run(malden, FakeMalden(page), tmp_path, now=NOW)
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
