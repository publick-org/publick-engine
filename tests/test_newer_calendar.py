"""CivicPlus's newer calendar (/m/calendar), which a city's Calendar.aspx addresses redirect to: its list of
a month and its event pages read as the older ones are (pipeline/civicplus.py). Run offline against Malden's
saved list for November 2026 (tests/fixtures/civicplus_day_list_2026-11.html) and one event's page
(civicplus_detail.html)."""

import copy
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse
from test_agendacenter import COMMITTEES

from pipeline import civicplus, fetch_meetings, new_town
from pipeline.fetch_meetings import load_store, save_store

BASE = "https://www.cityofmalden.org"
DAY_LIST = (FIXTURES / "civicplus_day_list_2026-11.html").read_text(encoding="utf-8")
DETAIL = (FIXTURES / "civicplus_detail.html").read_text(encoding="utf-8")
NOW = datetime(2026, 11, 2, 7, 0, tzinfo=TZ)
STAMP = NOW.isoformat(timespec="seconds")
CITY_HALL = "215 Pleasant St., Malden, MA 02148"


# ---- The list ----------------------------------------------------------------

def test_day_list_events():
    events = civicplus.parse_list(DAY_LIST, BASE)
    assert len(events) == 52 and len({e["id"] for e in events}) == 52
    assert {e["calendar"] for e in events} == {"Boards and Commissions Meetings", "Main Calendar", "Teen Center",
                                               "Malden Retirement Board", "Cemetery Board of Trustees Meeting"}
    appeal = next(e for e in events if e["id"] == "6441")
    # The room, without the building the list names it in ("Malden City Hall > ..."), and no address.
    assert appeal == {
        "id": "6441", "calendar_id": "26", "calendar": "Boards and Commissions Meetings",
        "source_url": f"{BASE}/m/calendar/event/detail/6441", "raw_title": "Board of Appeal Public Meeting",
        "date": "2026-11-18", "start_time": "18:30", "end_time": "20:00",
        "location_name": "Herbert L. Jackson Council Chamber Room 106",
        "title": "Board of Appeal Public Meeting", "body": "Board of Appeal Public", "status": "scheduled", "special": False,
    }


def test_day_list_times_places_and_status():
    events = {e["id"]: e for e in civicplus.parse_list(DAY_LIST, BASE)}
    # "6:00 PM - No set end time"
    assert (events["6452"]["start_time"], events["6452"]["end_time"]) == ("18:00", None)
    # A place that isn't in a building the city keeps.
    assert events["6455"]["location_name"] == "Virtual/Online"
    # A note typed in the location box, one paragraph or several, isn't a place.
    assert events["6447"]["location_name"] == "" and events["6301"]["location_name"] == ""
    assert events["6459"]["status"] == "cancelled"
    all_day = DAY_LIST.replace("6:00 PM - No set end time", "All Day")
    assert next(e for e in civicplus.parse_list(all_day, BASE) if e["id"] == "6452")["start_time"] is None


def test_an_event_listed_on_two_days_is_its_first_days():
    again = DAY_LIST.replace('<li id="event-6474"', '<li id="event-6441"')
    events = [e for e in civicplus.parse_list(again, BASE) if e["id"] == "6441"]
    assert len(events) == 1 and events[0]["raw_title"] == "Board of Appeal Public Meeting"


def test_a_page_of_neither_calendar_lists_nothing():
    assert civicplus.parse_list("<html><body>Calendar</body></html>", BASE) == []


def test_calendar_names_on_the_newer_page():
    names = new_town.calendar_names(DAY_LIST)
    assert names["26"] == "Boards and Commissions Meetings" and names["24"] == "Teen Center"


# ---- An event's page ---------------------------------------------------------

def test_detail_page():
    """The address is the one its map link searches for, as the older list gave it; the link's text adds the
    floor ("215 Pleasant St., 1st Floor, Malden MA 02148"). The Teams link isn't kept."""
    assert civicplus.parse_event_page(DETAIL, BASE) == {
        "location_name": "Meeting Room 3 #113", "address": CITY_HALL,
        "agenda_url": f"{BASE}/AgendaCenter/Commission-on-Climate-Action-and-Sustain-59/#_11182026-4537",
        "agenda_id": None, "agenda_center_id": "4537",
    }


def detail(place: str = "", links: str = "") -> str:
    side = f'<div class="col event-detail-sidebar"><ul>{links}</ul></div>' if links else ""
    return (f'<h1 class="calendar-event-title headline mb-3">Planning Board</h1>'
            f'<div class="d-flex gap-2" aria-label="Event location"><div>{place}</div></div></div>{side}'
            '<hr class="my-4" /><div aria-labelledby="calendar-related-events-heading">'
            '<a href="https://example.zoom.us/j/1">Another meeting</a><a href="/Archive.aspx?ADID=1">View Agenda</a></div>')


def link(href: str, text: str) -> str:
    return f'<li><svg></svg><a class="black-link" href="{href}" target="_blank">{text}</a></li>'


def test_detail_page_archive_agenda_and_zoom_link():
    """Gloucester's: the agenda in its Archive Center, and the event's link to its Zoom meeting."""
    zoom = "https://gloucester-ma-gov.zoom.us/j/88102073849"
    page = detail('<a href="http://maps.google.com/maps?q=Gloucester&#x2B;High&#x2B;School&#x2B;Library, Gloucester, MA 01930" '
                  'class="black-link d-block">Gloucester High School Library, 32 Leslie O. Johnson Road , Gloucester MA 01930</a>',
                  link("/Archive.aspx?ADID=20271", "View Agenda") + link(zoom, zoom) + link("tel:(978) 555-0100", "(978) 555-0100"))
    assert civicplus.parse_event_page(page, "https://www.gloucester-ma.gov") == {
        "address": "Gloucester High School Library, Gloucester, MA 01930",
        "agenda_url": "https://www.gloucester-ma.gov/Archive.aspx?ADID=20271", "agenda_id": "20271", "remote_url": zoom,
    }


def test_detail_page_with_a_note_for_a_place_and_no_links():
    """Nothing is taken from the other events the page lists under it."""
    page = detail('<span class="fr-view fw-bold mb-1 d-block"><p>This meeting is virtual.</p></span>')
    assert civicplus.parse_event_page(page, BASE) == {}


# ---- A town's run --------------------------------------------------------------

class FakeNewer:
    """Malden's site with the newer calendar: the list's address answers with the newer list, as its redirect
    does (November's; other months empty); a page for each event; an empty Agenda Center; a PDF for an agenda."""

    def __init__(self, list_page: str = DAY_LIST, event_pages: dict | None = None, event_page: str = "<html></html>"):
        self.list_page, self.event_pages, self.event_page = list_page, event_pages or {}, event_page
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if "Calendar.aspx?CID=0&view=list" in url:
            return FakeResponse((self.list_page if "month=11&" in url else "<html></html>").encode())
        if "/m/calendar/event/detail/" in url:
            return FakeResponse(self.event_pages.get(url.rsplit("/", 1)[1], self.event_page).encode())
        if "/AgendaCenter/Search/" in url:
            return FakeResponse(b"")
        if "Archive.aspx?ADID=" in url:
            return FakeResponse((FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes())
        raise AssertionError(f"unexpected URL {url}")


def read_pages(client):
    return sorted(u.rsplit("/", 1)[1] for u in client.urls if "/event/detail/" in u)


@pytest.fixture
def malden(config):
    town = copy.deepcopy(config)
    for table in ("archive", "drive_meetings"):
        town.pop(table, None)
    town["meetings"] = {"base_url": BASE, "civicplus": {"calendars": ["Boards and Commissions Meetings"]}}
    return town


@pytest.fixture
def with_agenda_center(malden):
    malden["meetings"]["agenda_center"] = {"base_url": BASE, "since": "2026-08-01", "committees": COMMITTEES}
    return malden


def facility(name: str, address: str = CITY_HALL) -> str:
    return (f'<a href="/Facilities/Facility/Details/Room-1" class="fr-view d-block">{name}</a>'
            f'<a href="http://maps.google.com/maps?q={address.replace(" ", "&#x2B;")}" class="d-block">{address}</a>')


def place(name: str, address: str = CITY_HALL) -> str:
    """An event's page with a place the city keeps and its address, and nothing else."""
    return detail(facility(name, address))


def test_a_run_reads_the_newer_calendar(malden, tmp_path):
    """Without an Agenda Center, each upcoming meeting's page is read: its address, online link and agenda."""
    zoom = "https://cityofmalden.zoom.us/j/123"
    board = detail(facility("Herbert L. Jackson Council Chamber Room 106"),
                   link("/Archive.aspx?ADID=77", "View Agenda") + link(zoom, zoom))
    client = FakeNewer(event_pages={"6441": board})
    status = fetch_meetings.run(malden, client, tmp_path, now=NOW)
    assert status["errors"] == [] and status["calendars"]["city calendar"]["listed"] == 31
    store = load_store(tmp_path)
    assert not any("Cardio" in m["title"] for m in store.values())      # the Teen Center's calendar isn't read
    appeal = store["6441"]
    assert (appeal["source_url"], appeal["date"], appeal["start_time"]) == (f"{BASE}/m/calendar/event/detail/6441", "2026-11-18", "18:30")
    assert (appeal["location_name"], appeal["address"], appeal["remote_url"]) == \
        ("Herbert L. Jackson Council Chamber Room 106", CITY_HALL, zoom)
    assert [a["id"] for a in appeal["agendas"]] == ["77"] and "history" not in appeal
    # The pages are asked for where the list links them.
    assert f"{BASE}/m/calendar/event/detail/6441" in client.urls


def test_the_newer_list_changes_nothing_recorded_from_the_older(with_agenda_center, tmp_path):
    """The day a city's calendar is replaced: a meeting recorded from the older list, with its address, is
    listed the same by the newer one, so nothing of it changed and its address stays."""
    fetch_meetings.run(with_agenda_center, FakeNewer(), tmp_path, now=NOW)
    store = load_store(tmp_path)
    for meeting in store.values():
        meeting.pop("address_checked", None)
        meeting.pop("history", None)
        if meeting.get("location_name"):
            meeting["address"] = CITY_HALL
        meeting["source_url"] = f"{BASE}/Calendar.aspx?EID={meeting['id']}"
    save_store(tmp_path, store, with_agenda_center)
    before = {i: (m["location_name"], m.get("address")) for i, m in store.items()}

    client = FakeNewer()
    status = fetch_meetings.run(with_agenda_center, client, tmp_path, now=NOW)
    after = load_store(tmp_path)
    assert status["errors"] == [] and status["new_meetings"] == 0
    assert {i: (m["location_name"], m.get("address")) for i, m in after.items()} == before
    assert not any(m.get("history") for m in after.values())
    assert after["6441"]["source_url"] == f"{BASE}/m/calendar/event/detail/6441"
    # Past the days a page is read for its agenda's listing (AGENDA_LINK_DAYS), only one with no address
    # recorded has its page read.
    late = [i for i in read_pages(client) if after[i]["date"] > "2026-11-16"]
    assert read_pages(client) and late and not any(before[i][1] for i in late)


def test_a_town_with_an_agenda_center_reads_a_new_meetings_page_once_for_its_address(with_agenda_center, tmp_path):
    """A meeting weeks ahead, past the days its page is read for its agenda's listing: read for its address,
    which the list leaves out, and not again."""
    client = FakeNewer(event_page=place("Meeting Room 1 #105"))
    fetch_meetings.run(with_agenda_center, client, tmp_path, now=NOW)
    store = load_store(tmp_path)
    late = store["6458"]          # November 23, in Meeting Room 1
    assert "6458" in read_pages(client) and late["address"] == CITY_HALL
    assert "history" not in late and "address_checked" not in late
    # One the list shows no place for is somewhere its page may say: the Housing Authority's, November 24.
    assert "6445" in read_pages(client) and store["6445"]["address"] == CITY_HALL

    again = FakeNewer(event_page=place("Meeting Room 1 #105"))
    fetch_meetings.run(with_agenda_center, again, tmp_path, now=NOW)
    assert "6458" not in read_pages(again) and "6445" not in read_pages(again)
    # In the next two weeks, a meeting with no named place has its page read each run: only it says where.
    assert "6447" in read_pages(again)


def test_a_meeting_first_seen_after_its_date_gets_its_pages_address(with_agenda_center, tmp_path):
    """November 2's and 5's, first read on the 6th: their pages are read once, for the agenda they link."""
    client = FakeNewer(event_page=place("Meeting Room 1 #105"))
    fetch_meetings.run(with_agenda_center, client, tmp_path, now=NOW.replace(day=6))
    assert load_store(tmp_path)["6443"]["address"] == CITY_HALL


def test_a_page_without_an_address_is_not_read_for_it_again(with_agenda_center, tmp_path):
    """Lewiston's page for a meeting with no street searches the map for the town alone."""
    no_street = detail('<a href="http://maps.google.com/maps?q=, Malden, MA 02148" class="d-block">Malden MA 02148</a>')
    assert civicplus.parse_event_page(no_street, BASE) == {}
    fetch_meetings.run(with_agenda_center, FakeNewer(event_page=no_street), tmp_path, now=NOW)
    late = load_store(tmp_path)["6458"]
    assert late.get("address") is None and late["address_checked"] == "Meeting Room 1 #105"
    again = FakeNewer()
    fetch_meetings.run(with_agenda_center, again, tmp_path, now=NOW)
    assert "6458" not in read_pages(again)


def moved(config, tmp_path, page: str | None, pages: int = 40):
    """The Board of Appeal's meeting, recorded in Room 2 at another address, as the list now has it in the
    Council Chamber; page is its event page, or None when the run's pages run out before it."""
    fetch_meetings.run(config, FakeNewer(event_page=place("Meeting Room 2 #108")), tmp_path, now=NOW)
    store = load_store(tmp_path)
    store["6441"].update(location_name="Meeting Room 2 #108", address="110 Pleasant St., Malden, MA 02148")
    store["6441"].pop("history", None)
    save_store(tmp_path, store, config)
    config["meetings"]["max_event_pages"] = pages
    client = FakeNewer(event_pages={"6441": page} if page else None)
    fetch_meetings.run(config, client, tmp_path, now=NOW.replace(hour=9))
    return load_store(tmp_path)["6441"], client


def changes(meeting):
    return [(h["field"], h["old"], h["new"]) for h in meeting.get("history", [])]


def test_a_meeting_moved_to_another_building_gets_its_pages_address(with_agenda_center, tmp_path):
    meeting, client = moved(with_agenda_center, tmp_path, place("Herbert L. Jackson Council Chamber Room 106"))
    assert "6441" in read_pages(client)
    assert (meeting["location_name"], meeting["address"]) == ("Herbert L. Jackson Council Chamber Room 106", CITY_HALL)
    assert changes(meeting) == [("location_name", "Meeting Room 2 #108", "Herbert L. Jackson Council Chamber Room 106"),
                                ("address", "110 Pleasant St., Malden, MA 02148", CITY_HALL)]


def test_a_meeting_moved_to_another_room_keeps_its_address(with_agenda_center, tmp_path):
    meeting, _ = moved(with_agenda_center, tmp_path,
                       place("Herbert L. Jackson Council Chamber Room 106", "110 Pleasant St., Malden, MA 02148"))
    assert meeting["address"] == "110 Pleasant St., Malden, MA 02148"
    assert changes(meeting) == [("location_name", "Meeting Room 2 #108", "Herbert L. Jackson Council Chamber Room 106")]


def test_a_moved_meeting_whose_page_is_not_read_has_no_address(with_agenda_center, tmp_path):
    """Its old place's address isn't shown for the new place, and isn't said to have changed, until the page
    is read on a later run: then it is one change, or none."""
    meeting, client = moved(with_agenda_center, tmp_path, None, pages=0)
    room = ("location_name", "Meeting Room 2 #108", "Herbert L. Jackson Council Chamber Room 106")
    assert read_pages(client) == [] and "address" not in meeting and changes(meeting) == [room]

    later = FakeNewer(event_pages={"6441": place("Herbert L. Jackson Council Chamber Room 106")})
    with_agenda_center["meetings"]["max_event_pages"] = 40
    fetch_meetings.run(with_agenda_center, later, tmp_path, now=NOW.replace(hour=11))
    meeting = load_store(tmp_path)["6441"]
    assert "6441" in read_pages(later) and meeting["address"] == CITY_HALL and "address_before" not in meeting
    assert changes(meeting) == [room, ("address", "110 Pleasant St., Malden, MA 02148", CITY_HALL)]


def test_a_moved_meeting_read_later_in_the_same_building_has_no_address_change(with_agenda_center, tmp_path):
    moved(with_agenda_center, tmp_path, None, pages=0)
    same = place("Herbert L. Jackson Council Chamber Room 106", "110 Pleasant St., Malden, MA 02148")
    with_agenda_center["meetings"]["max_event_pages"] = 40
    fetch_meetings.run(with_agenda_center, FakeNewer(event_pages={"6441": same}), tmp_path, now=NOW.replace(hour=11))
    meeting = load_store(tmp_path)["6441"]
    assert meeting["address"] == "110 Pleasant St., Malden, MA 02148"
    assert [c[0] for c in changes(meeting)] == ["location_name"]


def test_a_meeting_moved_to_a_place_whose_page_gives_no_address_loses_its_address(with_agenda_center, tmp_path):
    meeting, _ = moved(with_agenda_center, tmp_path, detail())
    assert meeting["address"] == "" and changes(meeting)[-1] == ("address", "110 Pleasant St., Malden, MA 02148", "")


# ---- A place written out, with no map link --------------------------------------

WRITTEN = '<span class="d-block">\n City Hall -Second Floor Conference Room , 205 Main St. , Biddeford  ME 04005\n </span>'
AS_LISTED = "City Hall -Second Floor Conference Room, Biddeford, ME 04005"


def test_detail_page_with_its_place_written_out():
    """Biddeford's: the older list gave the first line and the town; the page adds the street to it."""
    assert civicplus.parse_event_page(detail(WRITTEN), BASE) == {
        "address": AS_LISTED,
        "address_readings": [AS_LISTED, "City Hall -Second Floor Conference Room, 205 Main St., Biddeford, ME 04005"],
    }
    # A marked-up note, or words without a town, state and ZIP code, isn't an address.
    assert civicplus.parse_event_page(detail('<span class="d-block">Zoom only</span>'), BASE) == {}


def test_a_written_place_recorded_either_way_is_unchanged(malden, tmp_path):
    """A first line with a comma in it can't be told from two lines: a meeting recorded with either reading
    keeps it, and a meeting whose page names another place has that recorded as a change."""
    fetch_meetings.run(malden, FakeNewer(), tmp_path, now=NOW)
    store = load_store(tmp_path)
    store["6441"]["address"] = AS_LISTED
    store["6440"]["address"] = "City Hall -Second Floor Conference Room, 205 Main St., Biddeford, ME 04005"
    store["6474"]["address"] = "Somewhere Else, Biddeford, ME 04005"
    save_store(tmp_path, store, malden)
    fetch_meetings.run(malden, FakeNewer(event_page=detail(WRITTEN)), tmp_path, now=NOW.replace(hour=9))
    store = load_store(tmp_path)
    assert store["6441"]["address"] == AS_LISTED and "history" not in store["6441"]
    assert store["6440"]["address"].count("205 Main St.") == 1 and "history" not in store["6440"]
    assert changes(store["6474"]) == [("address", "Somewhere Else, Biddeford, ME 04005", AS_LISTED)]
