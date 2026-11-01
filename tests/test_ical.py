"""A school district's calendar feed (Beverly Public Schools', saved and trimmed:
tests/fixtures/ical_district.ics), read for its board's meetings (pipeline/ical.py); and one whose
events' UIDs are random UUIDs (Westbrook School Department's, on Apptegy)."""

import copy
from datetime import date, datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse

from pipeline import build_site, fetch_meetings, ical, identity
from pipeline.fetch_meetings import load_store

FEED = (FIXTURES / "ical_district.ics").read_text(encoding="utf-8")
ICAL_URL = "https://www.beverlyschools.org/apps/events/ical/?id=0"
PAGE_URL = "https://www.beverlyschools.org/apps/pages/index.jsp?uREC_ID=2083091&type=d"
BODIES = {"School Committee": "School Committee", "Committee of the Whole": "School Committee of the Whole",
          "Policy Subcommittee": "School Committee Policy Subcommittee",
          "Finance & Facilities": "School Committee Finance and Facilities Subcommittee"}
NOW = datetime(2026, 10, 2, 7, 0, tzinfo=TZ)


def test_events():
    events = ical.parse_events(FEED)
    assert len(events) == 24
    # Long lines are folded in the feed; an escaped comma is a comma.
    regular = next(e for e in events if e["uid"].startswith("54210334@"))
    assert regular == {"uid": "54210334@www.beverlyschools.org#20261014", "date": "2026-10-14", "start_time": "18:00",
                       "title": "Regular School Committee Meeting-BMS Library", "location": "",
                       "url": "https://www.beverlyschools.org/apps/events/54210334/", "cancelled": False}
    holiday = next(e for e in events if e["title"].startswith("No School-Columbus"))
    assert holiday["start_time"] is None


@pytest.mark.parametrize("title, body, status", [
    ("Regular School Committee Meeting-BMS Library", "School Committee", "scheduled"),
    ("Additional School Committee Meeting-ZOOM", "School Committee", "scheduled"),
    ("Committee of the Whole Meeting", "School Committee of the Whole", "scheduled"),
    ("CANCELED - Committee of the Whole Meeting", "School Committee of the Whole", "cancelled"),
    ("Policy Subcommittee Meeting-BMS Library", "School Committee Policy Subcommittee", "scheduled"),
])
def test_meetings_named_by_their_boards(title, body, status):
    found = ical.to_meeting({"uid": "1@x", "date": "2026-10-14", "start_time": "18:00", "title": title, "location": "",
                             "url": None, "cancelled": False}, BODIES)
    assert (found["body"], found["status"], found["id"]) == (body, status, "ical-1")


def test_other_events_are_left_out():
    assert ical.to_meeting({"uid": "2@x", "date": "2026-10-12", "start_time": None,
                            "title": "No School-Columbus/Indigenous Peoples' Day", "location": "", "url": None,
                            "cancelled": False}, BODIES) is None


class FakeDistrict:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        assert url == ICAL_URL
        return FakeResponse(FEED.encode())


@pytest.fixture
def beverly(config):
    town = copy.deepcopy(config)
    town.pop("archive", None)
    town.pop("drive_meetings", None)
    town["meetings"] = {"agenda_center": {"base_url": "https://www.beverlyma.gov", "since": "2026-01-01"}}
    town["ical_meetings"] = {"ical_url": ICAL_URL, "page_url": PAGE_URL, "source_name": "Beverly Public Schools",
                             "since": "2026-09-01", "bodies": BODIES}
    return town


def test_fetch_records_the_board_s_meetings(beverly, tmp_path):
    beverly["meetings"].pop("agenda_center")
    fetch_meetings.run(beverly, FakeDistrict(), tmp_path, now=NOW)
    store = load_store(tmp_path)
    upcoming = sorted((m["date"], m["start_time"], m["body"]) for m in store.values())
    assert ("2026-10-14", "18:00", "School Committee") in upcoming
    assert ("2026-10-28", "19:15", "School Committee of the Whole") in upcoming
    # From `since`, and up to 90 days ahead.
    assert min(d for d, _, _ in upcoming) >= "2026-09-01" and max(d for d, _, _ in upcoming) <= "2026-12-31"
    m = next(m for m in store.values() if m["date"] == "2026-10-14")
    assert (m["source"], m["source_name"], m["source_url"]) == (
        "ical", "Beverly Public Schools", "https://www.beverlyschools.org/apps/events/54210334/")


def test_the_meeting_page_says_where_it_s_from(beverly, tmp_path):
    beverly["meetings"].pop("agenda_center")
    fetch_meetings.run(beverly, FakeDistrict(), tmp_path, now=NOW)
    m = next(m for m in build_site.load_meetings(tmp_path, NOW.date())["all"] if m["date"] == "2026-10-14")
    assert m["listing"] == "school district's website"


# ---- Ids --------------------------------------------------------------------------------------

@pytest.mark.parametrize("uid, expected", [
    ("48810607@www.beverlyschools.org#20261125", "ical-48810607"),         # Beverly (Edlio)
    ("934-1793646000-1793646000@www.somerville.k12.ma.us", "ical-934"),    # Somerville
    ("1@x", "ical-1"), ("1020", "ical-1020"),
    ("300ab2c1-0d4e-4a7b-9c1f-2b3c4d5e6f70", "ical-300ab2c1-0d4e-4a7b-9c1f-2b3c4d5e6f70"),  # Westbrook (Apptegy)
    ("d4c7509e-37df-4e45-8ce0-02a72b99c709", "ical-d4c7509e-37df-4e45-8ce0-02a72b99c709"),
    ("9756723c-cb64-4415-ada8-b08b71349db7", "ical-9756723c-cb64-4415-ada8-b08b71349db7"),
])
def test_a_meetings_id_from_its_uid(uid, expected):
    assert ical.meeting_id(uid) == expected


def uuid_feed(*events: tuple[str, str, str]) -> str:
    """A feed as Westbrook's: (uid, DTSTART, SUMMARY) an event."""
    body = "".join(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{start}\r\nSUMMARY:{title}\r\nEND:VEVENT\r\n"
                   for uid, start, title in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


class FakeFeed:
    """A feed read once a run: `feeds` in turn, the last again after."""
    def __init__(self, *feeds: str):
        self.feeds = list(feeds)

    def get(self, url):
        assert url == ICAL_URL
        return FakeResponse((self.feeds.pop(0) if len(self.feeds) > 1 else self.feeds[0]).encode())


@pytest.fixture
def westbrook(beverly):
    beverly["meetings"].pop("agenda_center")
    beverly["ical_meetings"]["bodies"] = {"School Committee": "School Committee",
                                          "Ed Programs Committee": "School Committee Educational Programs Committee"}
    return beverly


def test_events_whose_uids_start_alike_are_two_meetings(westbrook, tmp_path):
    feed = uuid_feed(("3b111579-0dea-4a20-9339-2f5422f5b1d5", "20261118T190000", "School Committee Meeting"),
                     ("3a37fc5d-957a-407a-ac5a-7203ccd1ecf2", "20261209T190000", "Ed Programs Committee Meeting"))
    fetch_meetings.run(westbrook, FakeFeed(feed), tmp_path, now=NOW)
    assert sorted(load_store(tmp_path)) == ["ical-3a37fc5d-957a-407a-ac5a-7203ccd1ecf2",
                                            "ical-3b111579-0dea-4a20-9339-2f5422f5b1d5"]


# Westbrook's feed read twice: each time, every event has a new UID.
FIRST = uuid_feed(("2dbfcb31-63ed-4214-85a8-c3739c691d12", "20261118T190000", "School Committee Meeting"),
                  ("62206e13-91f1-47f0-ba15-b667b4b0e379", "20261118T170000", "Ed Programs Committee Meeting"),
                  ("77f52502-6bdf-4722-ba6f-507262bb919a", "20261209T190000", "School Committee Meeting"))
SECOND = uuid_feed(("52da6800-de83-4cc5-8416-10731ad54b6d", "20261118T190000", "School Committee Meeting"),
                   ("839e9f3a-be75-4f14-8960-f18be4913d41", "20261118T170000", "Ed Programs Committee Meeting"),
                   ("183ae4ac-4b31-4a28-bb26-6208e34c8acd", "20261209T190000", "School Committee Meeting"))


def test_a_feed_whose_uids_change_is_read_by_date(westbrook, tmp_path):
    westbrook["ical_meetings"]["uid_changes"] = True
    client = FakeFeed(FIRST, SECOND)
    fetch_meetings.run(westbrook, client, tmp_path, now=NOW)
    first = load_store(tmp_path)
    fetch_meetings.run(westbrook, client, tmp_path, now=NOW.replace(day=3))
    store = load_store(tmp_path)
    assert sorted(store) == sorted(first) == ["ical-2026-11-18-school-committee",
                                              "ical-2026-11-18-school-committee-educational-programs-committee",
                                              "ical-2026-12-09-school-committee"]
    assert all(m.get("listed", True) and not m.get("history") for m in store.values())


def test_a_boards_second_meeting_that_day_is_named_by_its_time(westbrook, tmp_path):
    westbrook["ical_meetings"]["uid_changes"] = True
    feed = uuid_feed(("a", "20261118T190000", "School Committee Meeting"), ("b", "20261118T170000", "School Committee Meeting"))
    fetch_meetings.run(westbrook, FakeFeed(feed), tmp_path, now=NOW)
    assert sorted(load_store(tmp_path)) == ["ical-2026-11-18-school-committee", "ical-2026-11-18-school-committee-1700"]


def test_meetings_stored_under_uids_move_to_their_dated_ids(westbrook, tmp_path):
    """A town's meetings recorded before uid_changes was set keep their records and addresses."""
    client = FakeFeed(FIRST, SECOND)
    fetch_meetings.run(westbrook, client, tmp_path, now=NOW)
    before = load_store(tmp_path)
    ledger = identity.load(tmp_path)["meetings"]
    address = {e["listings"][0]: mid for mid, e in ledger.items()}
    westbrook["ical_meetings"]["uid_changes"] = True
    fetch_meetings.run(westbrook, client, tmp_path, now=NOW.replace(day=3))
    store = load_store(tmp_path)
    assert len(store) == 3 and all(i.startswith("ical-2026-") for i in store)
    old = before["ical-77f52502-6bdf-4722-ba6f-507262bb919a"]
    moved = store["ical-2026-12-09-school-committee"]
    assert (moved["slug"], moved["first_seen"], moved.get("history")) == (old["slug"], old["first_seen"], None)
    assert all(m.get("listed", True) for m in store.values())
    ledger = identity.load(tmp_path)["meetings"]
    assert ledger[address["ical-77f52502-6bdf-4722-ba6f-507262bb919a"]]["listings"] == ["ical-2026-12-09-school-committee"]


@pytest.mark.parametrize("change, message", [
    ({"ical_url": None}, "needs ical_url"),
    ({"since": "September"}, "since must be a date"),
    ({"bodies": ["School Committee"]}, "bodies must map"),
    ({"uid_changes": "yes"}, "uid_changes must be true or false"),
])
def test_config_is_checked(change, message):
    settings = {"ical_url": ICAL_URL, "page_url": PAGE_URL, "source_name": "Beverly Public Schools",
                "since": "2026-09-01", "bodies": BODIES}
    settings = {k: v for k, v in {**settings, **change}.items() if v is not None}
    with pytest.raises(SystemExit, match=message):
        ical.check({"slug": "beverly", "ical_meetings": settings})


# ---- A repeating event (Winooski School District's Google calendar, saved and trimmed: tests/fixtures/ical_google.ics)

GOOGLE = (FIXTURES / "ical_google.ics").read_text(encoding="utf-8")
WSD = {"Board Meeting WSD": "School Board", "School Board Meeting": "School Board"}


def board_meetings(feed=GOOGLE, until=date(2026, 12, 31)):
    found = [ical.to_meeting(e, WSD) for e in ical.parse_events(feed, until=until, tz=TZ)]
    return sorted((m for m in found if m), key=lambda m: m["date"])


def test_a_repeating_event_is_each_of_its_meetings():
    found = [m for m in board_meetings() if m["date"] >= "2026-01-01"]
    # The second Wednesday of each month from March 11, 2026, at 6 p.m., but July 8 (EXDATE).
    assert [m["date"] for m in found] == ["2026-03-11", "2026-04-08", "2026-05-13", "2026-06-10", "2026-08-12",
                                         "2026-09-09", "2026-10-14", "2026-11-11", "2026-12-09"]
    assert {m["start_time"] for m in found} == {"18:00"} and {m["status"] for m in found} == {"scheduled"}
    # A meeting changed from the rule (September 9 and October 14 have events of their own) is listed once, and
    # every meeting's id is the series' (without Google's "_R<date>") and its date under the rule.
    assert [m["id"] for m in found if m["date"] in ("2026-10-14", "2026-11-11")] == [
        "ical-1o4b4qef9ngoopgpl46l5qghmc-20261014", "ical-1o4b4qef9ngoopgpl46l5qghmc-20261111"]
    assert len({m["id"] for m in found}) == len(found)


def test_a_repeating_event_is_read_to_until():
    assert board_meetings(until=date(2026, 11, 30))[-1]["date"] == "2026-11-11"
    # Its rule ends in March 2028 (UNTIL, in UTC: the evening of March 8 here).
    assert board_meetings(until=date(2030, 1, 1))[-1]["date"] == "2028-03-08"


def test_a_meeting_moved_from_its_date_keeps_its_id():
    # The 2021-22 series' July 13, 2022 meeting was moved to July 20.
    series = [m for m in board_meetings() if m["posted_title"] == "School Board Meeting - Regular"]
    dates = [m["date"] for m in series]
    assert dates[0] == "2021-10-13" and "2022-07-13" not in dates and dates[-1] == "2022-07-20"
    moved = series[-1]
    assert (moved["id"], moved["start_time"]) == ("ical-k7gnfkae0r34ojh7o450fq8iv4-20220713", "18:30")


def test_a_time_in_utc_is_the_towns_time():
    heart = next(e for e in ical.parse_events(GOOGLE, tz=TZ) if e["title"] == "Heart of Winooski Board Meeting")
    assert (heart["date"], heart["start_time"]) == ("2026-06-16", "10:00")   # DTSTART:20260616T140000Z
    # Not the School Board's: only the names in bodies are taken.
    assert ical.to_meeting(heart, {"Board Meeting WSD": "School Board"}) is None


def test_a_cancelled_meeting_of_a_repeating_event():
    feed = "\r\n".join([
        "BEGIN:VCALENDAR", "BEGIN:VEVENT", "DTSTART;TZID=America/New_York:20261007T190000",
        "RRULE:FREQ=WEEKLY;COUNT=3", "UID:abc@google.com", "SUMMARY:School Board Meeting", "END:VEVENT",
        "BEGIN:VEVENT", "DTSTART;TZID=America/New_York:20261014T190000", "UID:abc@google.com",
        "RECURRENCE-ID;TZID=America/New_York:20261014T190000", "STATUS:CANCELLED", "SUMMARY:School Board Meeting",
        "END:VEVENT", "END:VCALENDAR", ""])
    assert [(m["date"], m["status"], m["id"]) for m in board_meetings(feed)] == [
        ("2026-10-07", "scheduled", "ical-abc-20261007"), ("2026-10-14", "cancelled", "ical-abc-20261014"),
        ("2026-10-21", "scheduled", "ical-abc-20261021")]


def test_other_feeds_ids_are_their_events_numbers():
    assert ical.event_id({"uid": "54210334@www.beverlyschools.org#20261014"}) == "54210334"
    assert ical.event_id({"uid": "934-1793646000-1793646000@www.somerville.k12.ma.us"}) == "934"


def test_fetch_lists_a_repeating_events_meetings(beverly, tmp_path):
    town = beverly
    town["meetings"].pop("agenda_center")
    town["ical_meetings"].update(bodies=WSD, source_name="Winooski School District")

    class Calendar:
        def get(self, url):
            return FakeResponse(GOOGLE.encode())

    fetch_meetings.run(town, Calendar(), tmp_path, now=NOW)
    listed = sorted((m["date"], m["start_time"], m["body"]) for m in load_store(tmp_path).values())
    # From since (September 1) to 90 days ahead.
    assert listed == [("2026-09-09", "18:00", "School Board"), ("2026-10-14", "18:00", "School Board"),
                      ("2026-11-11", "18:00", "School Board"), ("2026-12-09", "18:00", "School Board")]
