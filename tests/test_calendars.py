"""Meetings from calendars other than CivicPlus: a CivicClerk portal and a
DotNetNuke city calendar, as Manchester, New Hampshire has. Run offline
against saved pages (tests/fixtures/civicclerk_*, dnn_*)."""

import copy
import json
from datetime import date, datetime

import pytest
from conftest import BUILT_AT, FETCHED_AT
from fakes import FIXTURES, FakeManchester
from test_site import parse
from test_site import test_internal_links_resolve as check_links

from pipeline import build_site, civicclerk, dnn, fetch_meetings, meeting_names
from pipeline.config import load_config
from pipeline.meeting_names import parse_name

CALENDAR = "https://www.manchesternh.gov/Government/City-Calendars"
PORTAL = "https://manchesternh.portal.civicclerk.com"
BOARDS = ["Board of Mayor and Aldermen", "Committee on Lands and Buildings", "Committee on Finance",
          "Special Committee on Baseball/Civic Center", "Planning Board", "Zoning Board of Adjustment",
          "Board of Health", "Arts Commission", "Manchester Development Corporation"]
ALIASES = {"ZBA": "Zoning Board of Adjustment", "Zoning Board": "Zoning Board of Adjustment",
           "Health Department": "Board of Health"}


def manchester(config: dict) -> dict:
    """The tests' town with Manchester's meeting calendars in place of Gloucester's."""
    town = copy.deepcopy(config)
    for table in ("archive", "drive_meetings", "summaries", "participation", "glossary"):
        town.pop(table, None)
    town["town"].update(name="Manchester", state="New Hampshire", state_abbr="NH")
    town["meetings"] = {
        "calendar_url": CALENDAR,
        "archive_url": "https://www.manchesternh.gov/Departments/City-Clerk/Meeting-Minutes-and-Agendas",
        "archive_name": "city's Meeting Minutes and Agendas page",
        "governing_body": "Board of Mayor and Aldermen",
        "documents": False,
        "boards": BOARDS,
        "aliases": ALIASES,
        "civicclerk": {"api_url": "https://manchesternh.api.civicclerk.com/v1", "portal_url": PORTAL, "since": "2026-08-01"},
        "dnn": {"calendar_url": CALENDAR, "module_id": 3737, "since": "2026-09-01", "months_ahead": 1,
                "exclude_pattern": r"\b(Aldermen|Committee on|Board of Registrars)\b"},
    }
    return town


@pytest.fixture
def config():
    return manchester(load_config("gloucester"))


def load(data_dir):
    return json.loads((data_dir / "meetings" / "meetings.json").read_text())


# ---- Names ------------------------------------------------------------------

@pytest.mark.parametrize("name, body, special, status", [
    ("Board of Mayor and Aldermen", "Board of Mayor and Aldermen", False, "scheduled"),
    ("PH-1 Board of Mayor and Aldermen", "Board of Mayor and Aldermen", False, "scheduled"),
    ("Special Meeting-Board of Mayor and Aldermen", "Board of Mayor and Aldermen", True, "scheduled"),
    ("Special Meeting of the Board of Mayor and Aldermen (Mayor's FY27 Budget)", "Board of Mayor and Aldermen", True, "scheduled"),
    ("Special Meeting of the Committee on Lands & Buildings", "Committee on Lands and Buildings", True, "scheduled"),
    ("CANCELED - Committee on Finance", "Committee on Finance", False, "cancelled"),
    # A board whose own name starts with "Special" is not a special meeting of it.
    ("Special Committee on Baseball/Civic Center", "Special Committee on Baseball/Civic Center", False, "scheduled"),
    ("Planning Board Public Hearings", "Planning Board", False, "scheduled"),
    ("ZBA Public Hearing", "Zoning Board of Adjustment", False, "scheduled"),
    ("Manchester Arts Commission", "Arts Commission", False, "scheduled"),
    # Not listed: cleaned up by rule.
    ("Board of Water Commissioners Meeting", "Board of Water Commissioners", False, "scheduled"),
    ("Central Business Service District (CBSD) Advisory Board", "Central Business Service District Advisory Board", False, "scheduled"),
    ("Special Meeting of the Heritage Commission - Site Walk", "Heritage Commission", True, "scheduled"),
    # Not listed, with the meeting's date in its name.
    ("City Council Regular Meeting June 11, 2026", "City Council", False, "scheduled"),
    ("Airport Commission 9-2-26", "Airport Commission", False, "scheduled"),
    ("Mayor's Veterans Council Meeting, 03/09/26", "Mayor's Veterans Council", False, "scheduled"),
])
def test_parse_name(name, body, special, status):
    parsed = parse_name(name, BOARDS, ALIASES)
    assert (parsed["body"], parsed["special"], parsed["status"]) == (body, special, status)


def test_parse_name_keeps_the_calendar_title():
    assert parse_name("CANCELED - Planning Board Business Meeting", BOARDS)["title"] == "Planning Board Business Meeting"


@pytest.mark.parametrize("name, status", [
    ("Policy Committee (Cancelled)", "cancelled"),
    ("Canceled due to weather: Policy Committee", "cancelled"),
    ("1/22 CPC Meeting CANCELLED", "cancelled"),
    ("Public Art Commission Meeting Agenda - June 16, 2026 Meeting Canceled due to technical issue", "cancelled"),
    ("Agenda for Salem Housing Authority Regular Board of Directors Meeting, August 12, 2026. Meeting Cancelled.",
     "cancelled"),
    ("Licensing Board Agenda POSTPONED to February 26, 2026", "postponed"),
    ("Licensing Board Meeting Agenda 5/28/2026 - Rescheduled from 5/26/26.", "rescheduled"),
    # A status word telling of another meeting: this one was held (Salem, 2026-10-15).
    ("Salem Harbor Port Authority Meeting - Special Meeting. This meeting is the rescheduling of the October 1, 2026 "
     "regular meeting that was canceled due to early closing of the City of Salem for the Haunted Happenings Parade.",
     "scheduled"),
])
def test_parse_name_status_labels_the_meeting(name, status):
    assert parse_name(name)["status"] == status


def test_parse_name_keeps_a_status_word_that_doesnt_label_the_meeting():
    parsed = parse_name("Port Authority Meeting - Special Meeting. Held in place of the regular meeting that was canceled.")
    assert parsed["status"] == "scheduled" and parsed["title"].endswith("the regular meeting that was canceled.")


@pytest.mark.parametrize("name, body, special, kind, more", [
    ("Planning Board Meeting", "Planning Board", False, None, False),
    ("Ordinances & Administration Meeting", "City Council Ordinances & Administration Committee", False, None, False),
    ("Remote Rent Board Meeting (Special)", "Rent Board", True, "special", False),
    ("Gloucester School Committee Meeting", "School Committee", False, None, False),
    ("Technical Review Committee (TRC)", "Technical Review Committee", False, None, False),
    ("Planning Board Public Hearings", "Planning Board", False, "public_hearing", False),
    ("ZBA Public Hearing", "Zoning Board of Adjustment", False, "public_hearing", True),
    ("Planning Board - Work Session", "Planning Board", False, "work_session", False),
    ("Board of Assessor Hearings May 19, 2026", "Board of Assessors", False, "hearing", False),
    # A board's own name isn't a kind of meeting, nor a workshop held with a regular meeting.
    ("City Council Workshop Meeting", "City Council Workshop", False, None, False),
    ("City Council Workshop & Meeting", "City Council", False, None, True),
    # What the name says besides: a topic, or the board it meets with.
    ("Special Meeting of the Board of Mayor and Aldermen: Public Hearing on the FY27 Budget", "Board of Mayor and Aldermen",
     True, "public_hearing", True),
    ("Joint Meeting of the Board of Mayor and Aldermen & Board of School Committee", "Board of Mayor and Aldermen",
     False, None, True),
])
def test_kind_of_meeting(name, body, special, kind, more):
    assert meeting_names.kind(name, body, special) == kind
    assert meeting_names.says_more(name, body, "Gloucester") == more


# ---- CivicClerk ---------------------------------------------------------------

def test_civicclerk_events():
    data = json.loads((FIXTURES / "civicclerk_events_2.json").read_text())
    events = {e["id"]: e for e in civicclerk.parse_events(data, PORTAL, BOARDS, ALIASES, {"New Hampshire": "NH"})}
    bma = events["civicclerk-1969"]
    # The API's "Z" times are local: the board meets at 7:00 PM.
    assert (bma["date"], bma["start_time"]) == ("2026-09-01", "19:00")
    assert bma["body"] == "Board of Mayor and Aldermen" and bma["source"] == "civicclerk"
    assert bma["source_url"] == f"{PORTAL}/event/1969/files"
    assert bma["address"] == "One City Hall Plaza, Manchester, NH 03101"
    assert bma["documents_url"] == bma["source_url"]
    assert events["civicclerk-2065"]["status"] == "cancelled"
    # An upcoming meeting with no files published yet links to none.
    assert events["civicclerk-1970"]["documents_url"] is None
    assert civicclerk.next_page(data) is None
    assert "skiptoken" in civicclerk.next_page(json.loads((FIXTURES / "civicclerk_events_1.json").read_text()))


def test_civicclerk_event_name_without_its_recurrence():
    data = {"value": [{"id": 7, "startDateTime": "2026-09-09T18:30:00Z", "publishedFiles": [],
                       "eventName": "One Time EventJoint Public Hearing with the Planning Board September 9, 2026"}]}
    event = civicclerk.parse_events(data, PORTAL)[0]
    assert event["title"] == "Joint Public Hearing with the Planning Board September 9, 2026"
    assert event["body"] == "Joint Public Hearing with the Planning Board"


def test_civicclerk_event_named_only_for_its_recurrence():
    """An event named "One Time Event" and nothing else is its category's board."""
    data = {"value": [{"id": 9764, "startDateTime": "2026-09-23T18:00:00Z", "publishedFiles": [],
                       "eventName": "One Time Event", "categoryName": "Peaks Island Council"}]}
    event = civicclerk.parse_events(data, PORTAL)[0]
    assert (event["title"], event["body"]) == ("Peaks Island Council", "Peaks Island Council")


def test_civicclerk_event_named_for_an_item_goes_under_its_category_when_the_town_says_so():
    """Springfield's portal names some events for their item, not their board ("EVERSOURCE - 41 Oakland St."
    under Board of Public Works); a name that names a listed board keeps it, whatever the category."""
    def event(id_, name, category):
        return {"id": id_, "startDateTime": "2026-09-09T18:30:00Z", "publishedFiles": [], "eventName": name,
                "categoryName": category}
    data = {"value": [event(1, "EVERSOURCE - 41 Oakland St.", "Board of Public Works"),
                      event(2, "License Commission Hearing - October 28, 2026", "Traffic Commission"),
                      event(3, "Special Meeting - Setting the FY27 Tax Rate", "City Council"),
                      event(4, "EVERSOURCE GAS - King St.", "General"),
                      event(5, "Finance Subcommittee", "City Council")]}
    boards = ["Board of Public Works", "City Council", "License Commission", "Traffic Commission"]
    filed = [e["body"] for e in civicclerk.parse_events(data, PORTAL, boards, {}, by_category=True)]
    assert filed == ["Board of Public Works", "License Commission", "City Council", "EVERSOURCE GAS",
                     "Finance Subcommittee"]
    # Off, as for every other town: the name alone.
    assert civicclerk.parse_events(data, PORTAL, boards, {})[0]["body"] == "EVERSOURCE"


def test_civicclerk_events_url():
    assert (civicclerk.events_url("https://x.api.civicclerk.com/v1/", date(2026, 1, 1))
            == "https://x.api.civicclerk.com/v1/Events?$filter=startDateTime%20ge%202026-01-01&$orderby=startDateTime")


# ---- DotNetNuke calendar ------------------------------------------------------

def test_dnn_month():
    events = {e["id"]: e for e in dnn.parse_month((FIXTURES / "dnn_calendar_2026-09.html").read_text(), CALENDAR, BOARDS, ALIASES)}
    assert len(events) == 17
    pb = events["dnn-106727"]
    assert (pb["date"], pb["start_time"], pb["end_time"]) == ("2026-09-03", "18:00", "22:00")
    assert pb["body"] == "Planning Board" and pb["title"] == "Planning Board Public Hearings"
    assert pb["source_url"] == f"{CALENDAR}/ModuleID/3737/ItemID/106727/mctl/EventDetails"
    assert pb["links"][0]["url"].startswith("https://www.manchesternh.gov/Portals/2/Departments/pcd/")
    # An end time equal to the start means none was given.
    assert events["dnn-105370"]["end_time"] is None
    assert events["dnn-106925"]["status"] == "cancelled"
    assert events["dnn-106834"]["body"] == "Zoning Board of Adjustment"


def test_dnn_generic_name_takes_the_board_from_the_description():
    page = (FIXTURES / "dnn_calendar_2026-09.html").read_text().replace(
        "Board of Water Commissioners Meeting - 9/24", "Regular Meeting - 9/24").replace(
        "&lt;p>Enter event description", "&lt;p>Manchester Health Department, 1528 Elm Street")
    water = next(e for e in dnn.parse_month(page, CALENDAR, BOARDS, ALIASES) if e["id"] == "dnn-105832")
    assert (water["title"], water["body"]) == ("Regular Meeting", "Board of Health")


def test_dnn_event_page():
    assert dnn.parse_event_page((FIXTURES / "dnn_event.html").read_text()) == {"location_name": "Public Works Building"}
    assert dnn.parse_event_page("<html></html>") == {}


def test_dnn_month_url():
    assert dnn.month_url(CALENDAR, 3737, date(2026, 10, 17)) == f"{CALENDAR}/ModuleID/3737/mctl/EventMonth/selecteddate/10-01-2026"


# ---- Collecting ---------------------------------------------------------------

def test_run_collects_both_calendars(config, tmp_path):
    client = FakeManchester()
    status = fetch_meetings.run(config, client, tmp_path, now=FETCHED_AT)
    store = load(tmp_path)
    assert status["errors"] == [] and status["failed_calendars"] == []
    # September and October on the city calendar, less the aldermanic meetings.
    assert {name: c["listed"] for name, c in status["calendars"].items()} == {"CivicClerk": 24, "city calendar": 15}
    assert status["updated_at"] == FETCHED_AT.isoformat()
    assert len(store) == 39
    # Aldermanic meetings come from CivicClerk only, not again from the city calendar.
    assert not any(m["id"].startswith("dnn-") and "Aldermen" in m["body"] for m in store.values())
    assert store["civicclerk-1969"]["body"] == "Board of Mayor and Aldermen"
    planning = store["dnn-106728"]
    assert planning["body"] == "Planning Board" and planning["documents_url"].endswith(".PDF?ver=2026-09-21-111637-000")
    # Upcoming city calendar meetings get their location from their event page; past ones aren't re-read.
    assert planning["location_name"] == "Public Works Building"
    assert "location_name" not in store["dnn-106727"]
    assert not any("ItemID/106727/" in u for u in client.urls)
    # The first run reads every month since `since`; CivicClerk from its `since`.
    assert f"{CALENDAR}/ModuleID/3737/mctl/EventMonth/selecteddate/09-01-2026" in client.urls
    assert any("startDateTime%20ge%202026-08-01" in u for u in client.urls)


def test_later_runs_recheck_recent_weeks_only(config, tmp_path):
    fetch_meetings.run(config, FakeManchester(), tmp_path, now=FETCHED_AT)
    later = FakeManchester()
    fetch_meetings.run(config, later, tmp_path, now=datetime(2026, 11, 20, 12, tzinfo=FETCHED_AT.tzinfo))
    assert any("startDateTime%20ge%202026-09-21" in u for u in later.urls)
    months = [u.rsplit("/", 1)[1] for u in later.urls if "selecteddate" in u]
    assert months == ["11-01-2026", "12-01-2026"]


def test_meeting_dropped_from_civicclerk_is_marked_removed(config, tmp_path):
    fetch_meetings.run(config, FakeManchester(), tmp_path, now=FETCHED_AT)
    pages = [json.loads((FIXTURES / f"civicclerk_events_{n}.json").read_text()) for n in (1, 2)]
    pages[1]["value"] = [e for e in pages[1]["value"] if e["id"] != 1971]
    fetch_meetings.run(config, FakeManchester(pages), tmp_path, now=FETCHED_AT.replace(day=27))
    store = load(tmp_path)
    assert store["civicclerk-1971"]["listed"] is False
    assert store["civicclerk-1971"]["history"][-1]["field"] == "listed"
    # Meetings on the other calendar are untouched.
    assert all(m.get("listed", True) for m in store.values() if m["id"].startswith("dnn-"))


def test_civicclerk_lists_meetings_90_days_ahead(config, tmp_path):
    pages = [json.loads((FIXTURES / f"civicclerk_events_{n}.json").read_text()) for n in (1, 2)]
    far = next(e for e in pages[1]["value"] if e["id"] == 1971)
    far["eventDate"] = far["startDateTime"] = "2027-01-05T19:00:00Z"
    fetch_meetings.run(config, FakeManchester(copy.deepcopy(pages)), tmp_path, now=FETCHED_AT)
    assert "civicclerk-1971" not in load(tmp_path)
    # In range by a later run, it's listed then.
    fetch_meetings.run(config, FakeManchester(copy.deepcopy(pages)), tmp_path, now=FETCHED_AT.replace(month=10, day=20))
    assert load(tmp_path)["civicclerk-1971"]["listed"] is True


def test_civicclerk_forgets_placeholders_recorded_beyond_90_days(config, tmp_path):
    fetch_meetings.run(config, FakeManchester(), tmp_path, now=FETCHED_AT)
    path = tmp_path / "meetings" / "meetings.json"
    store = json.loads(path.read_text())
    placeholder = {**store["civicclerk-1971"], "id": "civicclerk-9999", "slug": "2028-01-10-placeholder", "date": "2028-01-10"}
    for f in ("agenda_id", "agenda_url", "agendas", "minutes", "minutes_id", "minutes_url"):
        placeholder.pop(f, None)
    store["civicclerk-9999"] = placeholder
    path.write_text(json.dumps(store))
    fetch_meetings.run(config, FakeManchester(), tmp_path, now=FETCHED_AT.replace(day=27))
    after = load(tmp_path)
    assert "civicclerk-9999" not in after
    assert "civicclerk-1971" in after


def test_one_calendar_failing_keeps_the_other(config, tmp_path):
    from pipeline.http import FetchError

    class CivicClerkDown(FakeManchester):
        def get(self, url):
            if "civicclerk" in url:
                raise FetchError("HTTP 503", 503)
            return super().get(url)

    fetch_meetings.run(config, FakeManchester(), tmp_path, now=FETCHED_AT)
    later = FETCHED_AT.replace(day=28)
    status = fetch_meetings.run(config, CivicClerkDown(), tmp_path, now=later)
    assert status["failed_calendars"] == ["CivicClerk"]
    assert status["calendars"]["city calendar"]["updated_at"] == later.isoformat()
    # The meetings are only as fresh as the calendar that failed, so a stale-data alert still fires.
    assert status["updated_at"] == status["calendars"]["CivicClerk"]["updated_at"] == FETCHED_AT.isoformat()
    assert any(m["id"].startswith("civicclerk-") for m in load(tmp_path).values())


# ---- Site ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def manchester_site(tmp_path_factory):
    town = manchester(load_config("gloucester"))
    town["sections"] = [s for s in town["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "storage"):
        town.pop(table, None)
    # The Arts Commission's entry for October 27 most likely won't take place, as found by hand.
    town["meetings"]["corrections"] = [{
        "board": "Arts Commission", "date": "2026-10-27", "doubtful": True, "checked": "2026-10-01",
        "note": "The commission now meets on the fourth Tuesday; this entry is left over from its old schedule.",
        "evidence": "https://www.manchesternh.gov/arts-commission-schedule"}]
    data_dir = tmp_path_factory.mktemp("manchester-data")
    fetch_meetings.run(town, FakeManchester(), data_dir, now=FETCHED_AT)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: town)
        out = tmp_path_factory.mktemp("manchester") / "site"
        build_site.build("manchester", out, data_dir=data_dir, now=BUILT_AT)
    return out


def test_calendar_only_town_leaves_out_document_pages(manchester_site):
    for path in ("meetings/decisions", "meetings/search", "feed.xml", "streets", "meetings/data"):
        assert not (manchester_site / path).exists(), path
    index = (manchester_site / "meetings" / "index.html").read_text()
    assert "/meetings/decisions/" not in index and "search-q" not in index
    assert "Board of Mayor and Aldermen, boards, and committees: when they meet." in index
    assert f'href="{PORTAL}"' in index and f'href="{CALENDAR}"' in index
    for page in ("index.html", "about/index.html", "meetings/index.html"):
        html = (manchester_site / page).read_text()
        assert "list.aspx" not in html and "Archive.aspx" not in html and "feed.xml" not in html, page
    assert "what was decided" not in (manchester_site / "index.html").read_text()


def test_calendar_only_meeting_links_to_the_city_documents(manchester_site):
    bma = (manchester_site / "meetings" / "2026-09-01-board-of-mayor-and-aldermen" / "index.html").read_text()
    assert f'The agenda and minutes are on the <a href="{PORTAL}/event/1969/files"' in bma
    assert "meeting portal<span class=\"visually-hidden\"> (opens in new tab)</span></a> (CivicClerk)" in bma
    assert "No agenda was posted" not in bma
    upcoming = (manchester_site / "meetings" / "2026-10-06-board-of-mayor-and-aldermen" / "index.html").read_text()
    assert "No agenda posted yet." in upcoming
    planning = (manchester_site / "meetings" / "2026-10-01-planning-board" / "index.html").read_text()
    assert "Public Works Building" in planning and "2026-10-01_PB_AGENDA.PDF" in planning
    assert "Not listed on the city calendar" not in planning
    # Lists say which meetings have an agenda posted, on the city's site or here.
    index = (manchester_site / "meetings" / "past" / "index.html").read_text()
    item = index[index.index("/meetings/2026-10-01-planning-board/"):]
    assert item.index("Agenda posted") < item.index("</li>")


def test_calendar_only_town_links_resolve(manchester_site):
    check_links(manchester_site, sorted(manchester_site.rglob("*.html")))
    for path in manchester_site.rglob("*.html"):
        assert parse(path).tags.count("h1") == 1, path


def test_same_day_meetings_of_one_board_are_told_apart(manchester_site):
    """The Board of Mayor and Aldermen holds a hearing and its regular meeting on one evening."""
    titles = [parse(p).title for p in (manchester_site / "meetings").glob("2026-08-04-board-of-mayor-and-aldermen*/index.html")]
    assert len(titles) == len(set(titles)) == 2
    assert any("7:00" in t for t in titles)
    past = (manchester_site / "meetings" / "past" / "index.html").read_text()
    assert "Special Meeting of the Board of Mayor and Aldermen - Public Hearing: CDBG Funds</a>" in past


def test_a_meeting_is_named_for_its_board_and_kind(manchester_site):
    """A meeting's page is named for its board, the kind of meeting, and the day, and says what
    else its listing's name does."""
    pages = [p.read_text() for p in (manchester_site / "meetings").glob("2026-08-04-board-of-mayor-and-aldermen*/index.html")]
    hearing = next(p for p in pages if "CDBG" in p)
    assert "<h1>Board of Mayor and Aldermen public hearing – Aug 4, 2026, 6:00 PM</h1>" in hearing
    assert '<meta property="og:title" content="Board of Mayor and Aldermen public hearing – Aug 4, 2026, 6:00 PM">' in hearing
    assert "<title>Manchester, NH Board of Mayor and Aldermen public hearing – Aug 4, 2026, 6:00 PM</title>" in hearing
    assert "6:00 PM</time> · Special meeting</p>" in hearing
    assert "Listed as “Special Meeting of the Board of Mayor and Aldermen - Public Hearing: CDBG Funds”." in hearing
    regular = next(p for p in pages if "CDBG" not in p)
    assert "<h1>Board of Mayor and Aldermen – Aug 4, 2026, 7:00 PM</h1>" in regular and "Listed as" not in regular


def test_dnn_agenda_file_is_a_pdf_named_for_the_meeting():
    events = dnn.parse_month((FIXTURES / "dnn_calendar_2026-09.html").read_text(), CALENDAR, BOARDS, ALIASES)
    agendas = {e["title"]: dnn.agenda_file(e["links"], e["date"]) for e in events}
    zba = agendas["ZBA Public Hearing"]
    assert zba["agenda_url"].split("?")[0].endswith("/2026-09-10 ZBA Agenda.pdf")
    assert zba["agenda_id"].startswith("dnn-")
    assert agendas["Trustees of Trust Funds"]["agenda_url"].endswith("/2026-09-15_TRUST_AGENDA.PDF")
    # The Development Corporation links its schedule for the year, and the
    # Highway Commission a web page: neither is that meeting's agenda.
    assert agendas["Manchester Development Corporation Board of Directors Meeting"] is None
    assert agendas["Highway Commission"] is None


def test_dnn_agenda_file_needs_the_meeting_date():
    link = [{"url": "https://www.manchesternh.gov/Portals/2/2026-09-17_PB_AGENDA.PDF", "text": "Agenda"}]
    assert dnn.agenda_file(link, "2026-09-17")
    assert dnn.agenda_file(link, "2026-10-01") is None
    underscored = [{"url": "https://www.manchesternh.gov/Portals/2/MDC%202026_08_13%20Special%20Meeting%20Agenda.pdf", "text": "Agenda"}]
    assert dnn.agenda_file(underscored, "2026-08-13")


def test_dnn_revised_agenda_gets_a_new_id():
    first = [{"url": "https://www.manchesternh.gov/Portals/2/2026-09-10 ZBA Agenda.pdf?ver=1", "text": "Agenda"}]
    revised = [{"url": "https://www.manchesternh.gov/Portals/2/2026-09-10 ZBA Agenda.pdf?ver=2", "text": "Agenda"}]
    assert dnn.agenda_file(first, "2026-09-10")["agenda_id"] != dnn.agenda_file(revised, "2026-09-10")["agenda_id"]


def test_a_corrected_listing_is_shown_with_the_correction(manchester_site):
    """A meeting the town's corrections find most likely won't take place stays listed, marked,
    with the reason and how it's known."""
    arts = (manchester_site / "meetings" / "2026-10-27-arts-commission" / "index.html").read_text()
    assert "A correction from" in arts and "left over from its old schedule" in arts
    assert 'href="https://www.manchesternh.gov/arts-commission-schedule"' in arts and "May not take place" in arts
    upcoming = (manchester_site / "meetings" / "index.html").read_text()
    item = upcoming[upcoming.index("/meetings/2026-10-27-arts-commission/"):]
    assert item.index("May not take place") < item.index("</li>")


def test_a_link_for_the_whole_year_is_not_the_meeting_s_agenda(manchester_site):
    """The Development Corporation's calendar entries link its agenda template for 2026."""
    mdc = (manchester_site / "meetings" / "2026-10-08-manchester-development-corporation" / "index.html").read_text()
    assert "No agenda posted yet." in mdc and "the board's documents" in mdc
    upcoming = (manchester_site / "meetings" / "index.html").read_text()
    item = upcoming[upcoming.index("/meetings/2026-10-08-manchester-development-corporation/"):]
    item = item[:item.index("</li>")]
    assert "Agenda posted" not in item and "No agenda posted yet" in item
