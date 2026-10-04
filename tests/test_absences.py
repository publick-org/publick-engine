"""Why a meeting's minutes, agenda or summary isn't shown, and whether the meetings
calendar is behind (pipeline/absences.py), and how the pages say it."""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta

import pytest
from conftest import BUILT_AT, TZ

from pipeline import absences

TODAY = date(2026, 10, 4)
AGENDA_CENTER = {"meetings": {"agenda_center": {"base_url": "https://example.gov", "since": "2026-01-01"}},
                 "summaries": {"model": "m"}}
# Manchester: aldermanic boards from CivicClerk, every other board from the city calendar, which links no minutes.
CIVICCLERK_AND_CALENDAR = {"meetings": {"civicclerk": {"since": "2026-01-01"}, "dnn": {"since": "2026-01-01"}}}
ARCHIVE = {"meetings": {}, "archive": {"minutes_since": "2026-06-28"}}


def meeting(**fields):
    return {"date": "2026-08-01", "body": "Planning Board", "status": "scheduled", "listed": True,
            "source": "agendacenter", "minutes": [], **fields}


@pytest.mark.parametrize("m", [
    meeting(status="cancelled"),
    meeting(status="postponed"),
    meeting(listed=False),
    meeting(date="2026-10-04"),
    meeting(minutes=[{"id": "x"}]),
], ids=["cancelled", "postponed", "no longer listed", "today", "has minutes"])
def test_no_minutes_gap_to_explain(m):
    assert absences.minutes_gap(AGENDA_CENTER, m, TODAY) is None


def test_minutes_not_posted_says_for_how_long():
    assert absences.minutes_gap(AGENDA_CENTER, meeting(), TODAY) == {"reason": "not_posted", "days": 64, "source": "agendacenter"}


def test_minutes_the_city_links_but_not_saved():
    gap = absences.minutes_gap(AGENDA_CENTER, meeting(minutes_url="https://example.gov/m.pdf"), TODAY)
    assert gap == {"reason": "linked", "url": "https://example.gov/m.pdf"}


def test_minutes_before_collection_starts():
    gap = absences.minutes_gap(ARCHIVE, meeting(source="calendar", date="2026-06-01"), TODAY)
    assert gap == {"reason": "before", "since": "2026-06-28"}


def test_calendar_board_whose_minutes_no_source_reads():
    """A Manchester calendar board's meeting doesn't say its minutes are coming."""
    assert absences.minutes_gap(CIVICCLERK_AND_CALENDAR, meeting(source="calendar"), TODAY) == {"reason": "not_collected"}
    gap = absences.minutes_gap(CIVICCLERK_AND_CALENDAR, meeting(source="civicclerk"), TODAY)
    assert gap["reason"] == "not_posted" and gap["source"] == "civicclerk"


def test_calendar_meeting_gets_minutes_from_the_agenda_center_or_archive():
    assert absences.minutes_gap(AGENDA_CENTER, meeting(source="calendar"), TODAY)["source"] == "agendacenter"
    assert absences.minutes_gap(ARCHIVE, meeting(source="calendar"), TODAY)["source"] == "archive"


def test_school_board_sources():
    finalsite = {"finalsite_meetings": {"since": "2026-07-01"}}
    assert absences.minutes_gap(finalsite, meeting(source="finalsite"), TODAY)["source"] == "finalsite"
    # A district calendar feed carries no documents, but the same meeting from its Drive folders does.
    drive = {"drive_meetings": {"since": "2026-06-28"}, "ical_meetings": {"since": "2026-06-28"}}
    assert absences.minutes_gap(drive, meeting(source="ical"), TODAY) == {"reason": "not_collected"}
    combined = meeting(source="ical", listings=[{"id": "a", "source": "ical"}, {"id": "b", "source": "drive"}])
    assert absences.minutes_gap(drive, combined, TODAY)["source"] == "drive"


def test_agenda_gap():
    assert absences.agenda_gap(meeting(source="calendar"), TODAY) == {"reason": "none_linked"}
    assert absences.agenda_gap(meeting(status="cancelled"), TODAY) == {"reason": "not_held"}
    for fields in ({"agendas": [{"file": "a.pdf"}]}, {"agenda_url": "u"}, {"documents_url": "u"}, {"date": "2026-10-05"}):
        assert absences.agenda_gap(meeting(**fields), TODAY) is None


def test_summary_gap():
    assert absences.summary_gap({}, meeting()) == {"reason": "off"}
    since = {"summaries": {"model": "m", "since": "2026-09-01"}}
    assert absences.summary_gap(since, meeting()) == {"reason": "before", "since": "2026-09-01"}
    assert absences.summary_gap(since, meeting(date="2026-09-15")) is None


def test_calendar_behind():
    config = {"meetings": {}, "freshness": {"sources": [{"file": "meetings/status.json", "max_days": 3}]}}
    now = datetime(2026, 10, 4, 8, 0, tzinfo=TZ)
    read = lambda days: {"updated_at": (now - timedelta(days=days)).isoformat()}
    assert absences.calendar_behind(config, read(2.5), now) is None
    assert absences.calendar_behind(config, read(4), now) == {"since": "2026-09-30"}
    assert absences.calendar_behind(config, None, now) == {"since": None}
    # Without a [freshness] row, two days.
    assert absences.calendar_behind({"meetings": {}}, read(2.5), now) == {"since": "2026-10-01"}
    assert absences.calendar_behind({}, None, now) is None


# ---- On the pages (the fixture town, built on BUILT_AT) ---------------------------

def test_a_cancelled_meeting_doesnt_wait_for_minutes(site_dir):
    page = (site_dir / "meetings" / "2026-09-28-licensing-board" / "index.html").read_text()
    assert "Cancelled" in page
    assert "What was decided" not in page and "not been posted" not in page and "haven't been posted" not in page


def test_missing_minutes_say_where_and_for_how_long(site_dir, data_dir):
    store = json.loads((data_dir / "meetings" / "meetings.json").read_text())
    held = [m for m in store.values() if m["date"] < BUILT_AT.date().isoformat() and not m.get("minutes")
            and m.get("status", "scheduled") == "scheduled" and m.get("listed", True)]
    assert held
    for m in held:
        page = (site_dir / "meetings" / m["slug"] / "index.html").read_text()
        days = (BUILT_AT.date() - date.fromisoformat(m["date"])).days
        assert "Minutes haven't been posted on the" in page, m["slug"]
        assert f"{days} day{'s' if days != 1 else ''} after the meeting." in page, m["slug"]


def test_a_calendar_not_read_lately_is_said_not_shown_as_empty(site_dir):
    """The fixture's calendar was last read on September 27, five days before it's built."""
    note = "This site hasn't been able to check the city's meeting listings since September 27, 2026"
    for page in ("index.html", "meetings/index.html"):
        assert note in (site_dir / page).read_text(), page
    assert "Some of this site's data is behind" in (site_dir / "about" / "index.html").read_text()


def test_a_board_page_starts_at_its_own_first_meeting(site_dir, data_dir):
    store = json.loads((data_dir / "meetings" / "meetings.json").read_text())
    first = defaultdict(list)
    for m in store.values():
        first[m["slug"].split("-", 3)[3]].append(m["date"])
    slug, dates = next((s, d) for s, d in sorted(first.items()) if min(d)[:7] > min(m["date"] for m in store.values())[:7])
    page = (site_dir / "meetings" / "boards" / slug / "index.html").read_text()
    month = date.fromisoformat(min(dates)).strftime("%B %Y")
    assert f"Meetings recorded here start in {month}." in page



def test_a_calendar_link_to_the_agenda_is_given(tmp_path):
    """Manchester's calendar links some boards' agendas (documents_url) without a copy saved:
    the page links it, rather than leaving the Agenda heading empty."""
    from pipeline import build_site
    data = tmp_path / "data"
    (data / "meetings").mkdir(parents=True)
    record = {"id": "dnn-1", "body": "Planning Board", "date": "2026-09-17", "slug": "2026-09-17-planning-board",
              "title": "Planning Board", "source": "calendar", "first_seen": "2026-09-01T08:00:00-04:00",
              "documents_url": "https://example.gov/2026-09-17_PB_AGENDA.PDF"}
    (data / "meetings" / "meetings.json").write_text(json.dumps({"dnn-1": record}))
    m = build_site.load_meetings(data, BUILT_AT.date())["all"][0]
    absences.annotate({"all": [m]}, {"meetings": {}}, BUILT_AT.date())
    assert m["agenda_gap"] is None and m["minutes_gap"] == {"reason": "not_collected"}
