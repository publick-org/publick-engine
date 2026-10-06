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


# ---- Sections: behind, and not covered ---------------------------------------------

def test_section_behind_picks_the_sections_own_sources():
    rows = [{"label": "Meetings calendar", "file": "meetings/status.json", "stale": True},
            {"label": "Board of Education meetings", "file": "meetings/finalsite_status.json", "stale": True},
            {"label": "Meeting summaries", "file": "summaries", "stale": True, "waiting": ["agenda"]},
            {"label": "School figures", "file": "schools/schools.json", "stale": True},
            {"label": "Housing figures", "file": "housing/housing.json", "stale": False}]
    # The calendar's own note says when it's behind, where meetings are listed.
    assert [r["label"] for r in absences.section_behind(rows, "meetings")] == ["Board of Education meetings", "Meeting summaries"]
    assert [r["label"] for r in absences.section_behind(rows, "schools")] == ["School figures"]
    assert absences.section_behind(rows, "housing") == [] and absences.section_behind(rows, None) == []


def test_not_covered_says_why_each_section_is_missing():
    from pipeline import states
    config = {"slug": "wallingford", "town": {"state": "Connecticut", "state_abbr": "CT"},
              "sections": [{"slug": s} for s in ("meetings", "schools", "housing", "officials")],
              "absences": {"311": "The town doesn't publish its service requests."}}
    gaps = absences.not_covered(config, states.for_town(config))
    # Connecticut has a budget package, so the town's config just doesn't list the section yet.
    assert gaps == [{"section": "311", "reason": "no_311", "note": "The town doesn't publish its service requests."},
                    {"section": "budget", "reason": "not_added", "note": None}]
    # A state the engine has no package for.
    elsewhere = {**config, "town": {"state": "New York", "state_abbr": "NY"}}
    assert absences.not_covered(elsewhere, states.for_town(elsewhere))[1] == {"section": "budget", "reason": "state", "note": None}
    config["absences"] = {"parks": "No parks."}
    with pytest.raises(SystemExit, match="parks"):
        absences.not_covered(config, states.for_town(config))


def test_officials_at_large_and_dashes(config, data_dir):
    import copy
    from pipeline import officials
    at_large = copy.deepcopy(config)
    for body in at_large["officials"]["bodies"]:
        for m in body["members"]:
            m.pop("ward", None), m.pop("wards", None)
    assert officials.load(at_large, data_dir)["at_large"]
    assert not officials.load(config, data_dir)["at_large"]
    at_large["officials"]["bodies"][0]["members"][0].pop("term_ends", None)
    assert officials.load(at_large, data_dir)["dashes"]
    assert officials.at_large(at_large) and not officials.at_large(config)


def test_officials_seat_column_only_where_seats_differ(config, data_dir):
    import copy
    from pipeline import officials
    same = copy.deepcopy(config)
    for m in same["officials"]["bodies"][1]["members"]:
        m["seat"] = "At-large"
        m.pop("ward", None), m.pop("wards", None)
    assert not officials.load(same, data_dir)["bodies"][1]["show_seat"]
    mayor, council = officials.load(config, data_dir)["bodies"][:2]
    assert not mayor["show_seat"] and council["show_seat"]


def assert_dashes_explained(site):
    """Every page of a built site with a – in a table has a legend for it (site_checks does the same)."""
    import re
    for path in site.rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        if re.search(r"<td[^>]*>\s*–\s*</td>", html):
            assert "dash-legend" in html, path


def test_dashes_are_explained_on_the_fixture_site(site_dir):
    assert_dashes_explained(site_dir)


def test_section_pages_say_what_is_behind(site_dir, data_dir, config):
    from pipeline import freshness
    behind = absences.section_behind(freshness.check(config, data_dir, BUILT_AT), "311")
    page = (site_dir / "311" / "index.html").read_text()
    assert ('id="data-behind"' in page) == bool(behind)
    for r in behind:
        assert r["label"] in page
