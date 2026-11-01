"""Which meeting each listing is, kept from run to run (pipeline/identity.py): the rules that place a
listing, the meeting's address, and what happens when a listing changes after it's placed."""

import copy
import itertools
import json

import pytest

from pipeline import identity, listings

DAY = "2026-10-06"


def record(id, body="Planning Board", date=DAY, time=None, source="calendar", title=None, seen="2026-10-01T07:00:00", **fields):
    return {"id": id, "slug": f"{date}-own-{id}", "date": date, "body": body, "start_time": time, "first_seen": seen,
            "title": title or f"{body} Meeting", "status": "scheduled", "source": source, **fields}


def store_of(*records):
    return {m["id"]: copy.deepcopy(m) for m in records}


def grouping(ledger: dict) -> dict:
    """Each meeting's address, to its listings."""
    return {mid: sorted(e["listings"]) for mid, e in ledger["meetings"].items() if e["listings"]}


def arrive(*batches, town=""):
    """Records recorded run by run, each run keeping its identities, as the daily runs do (identity.update)."""
    store, ledger = {}, {"meetings": {}}
    for batch in batches:
        store.update(store_of(*batch))
        ledger = identity.assign(store, ledger, town)
    return store, ledger


CALENDAR = record("101", time="19:00", seen="2026-10-01T07:00:00")
AGENDA = record("agendacenter-5", source="agendacenter", title="Planning Board Agenda", seen="2026-10-02T07:00:00")
DISTRICT = record("page-2026-10-06-meeting", source="page", title="Meeting", seen="2026-10-03T07:00:00")


# ---- The address ---------------------------------------------------------------

def test_the_address_is_the_board_and_day_whichever_source_lists_it_first():
    for first in (CALENDAR, AGENDA, DISTRICT):
        others = [r for r in (CALENDAR, AGENDA, DISTRICT) if r is not first]
        _, ledger = arrive([first], others)
        assert grouping(ledger) == {"2026-10-06-planning-board": ["101", "agendacenter-5", "page-2026-10-06-meeting"]}


def test_the_same_meetings_whatever_order_the_listings_arrive_in():
    """Every order of three runs, each recording one listing, gives the meeting recorded all at once."""
    _, at_once = arrive([CALENDAR, AGENDA, DISTRICT])
    for order in itertools.permutations([CALENDAR, AGENDA, DISTRICT]):
        _, ledger = arrive(*[[r] for r in order])
        assert grouping(ledger) == grouping(at_once)


def test_the_same_identities_from_run_to_run():
    store, ledger = arrive([CALENDAR, AGENDA, DISTRICT])
    again = identity.assign(store, json.loads(json.dumps(ledger)))
    assert again == ledger
    # And whatever order the store lists its records in.
    assert identity.assign(dict(reversed(list(store.items()))), ledger) == ledger


def test_a_listing_recorded_later_joins_the_meeting_and_its_address_stays():
    store, ledger = arrive([CALENDAR])
    assert grouping(ledger) == {"2026-10-06-planning-board": ["101"]}
    store, ledger = arrive([CALENDAR], [AGENDA])
    assert grouping(ledger) == {"2026-10-06-planning-board": ["101", "agendacenter-5"]}


def test_a_board_meeting_twice_in_a_day_is_told_apart_by_its_time_or_a_number():
    _, ledger = arrive([record("1", time="17:00"), record("2", time="19:00"),
                        record("agendacenter-3", source="agendacenter", title="Planning Board Site Walk")])
    assert grouping(ledger) == {"2026-10-06-planning-board": ["1"], "2026-10-06-planning-board-1900": ["2"],
                                "2026-10-06-planning-board-2": ["agendacenter-3"]}


def test_a_second_meeting_found_later_leaves_the_first_its_address():
    _, ledger = arrive([record("2", time="19:00")], [record("1", time="17:00", seen="2026-10-02T07:00:00")])
    assert grouping(ledger) == {"2026-10-06-planning-board": ["2"], "2026-10-06-planning-board-1700": ["1"]}


def test_a_time_change_keeps_the_meeting_and_its_address():
    store, ledger = arrive([record("1", time="17:00"), record("2", time="19:00")])
    before = grouping(ledger)
    # The evening meeting moves to 5:30, the afternoon one to 7:00: neither takes the other's address.
    store["1"]["start_time"], store["2"]["start_time"] = "19:00", "17:30"
    assert grouping(identity.assign(store, ledger)) == before


def test_a_cancelled_meeting_keeps_its_listings_and_address():
    """A cancellation is a listing's status, or a notice posted as a new listing: neither makes another meeting."""
    store, ledger = arrive([CALENDAR, AGENDA])
    store["101"]["status"] = "cancelled"
    notice = record("agendacenter-6", source="agendacenter", title="Planning Board Agenda - CANCELLED", status="cancelled",
                    seen="2026-10-05T07:00:00")
    store.update(store_of(notice))
    assert grouping(identity.assign(store, ledger)) == {"2026-10-06-planning-board": ["101", "agendacenter-5", "agendacenter-6"]}


def test_a_rescheduled_meeting_keeps_its_address():
    store, ledger = arrive([CALENDAR, AGENDA])
    for m in store.values():
        m["date"] = "2026-10-08"
    after = identity.assign(store, ledger)
    assert grouping(after) == {"2026-10-06-planning-board": ["101", "agendacenter-5"]}
    assert after["meetings"]["2026-10-06-planning-board"]["date"] == "2026-10-08"


def test_a_listing_moved_to_another_day_leaves_the_meeting():
    """The calendar moves the meeting to Thursday, where the Agenda Center has posted its agenda;
    the Agenda Center's listing for Tuesday stays where it was."""
    thursday = record("agendacenter-9", date="2026-10-08", source="agendacenter", title="Planning Board Agenda")
    store, ledger = arrive([CALENDAR, AGENDA, thursday])
    store["101"]["date"] = "2026-10-08"
    after = identity.assign(store, ledger)
    assert grouping(after) == {"2026-10-06-planning-board": ["agendacenter-5"],
                               "2026-10-08-planning-board": ["101", "agendacenter-9"]}


def test_a_listing_given_a_new_id_goes_back_to_its_meeting():
    """A district's Drive meeting taken over by the city calendar (fetch_meetings.adopt_drive_meeting)
    keeps its record and own address under the calendar's id, and so its meeting."""
    drive = record("drive-sc-1", "School Committee", source="drive", title="School Committee")
    store = store_of(drive)
    ledger = identity.assign(store, None)
    taken_over = store.pop("drive-sc-1")
    store["205"] = {**taken_over, "id": "205", "source": "calendar", "start_time": "18:00"}
    assert grouping(identity.assign(store, ledger)) == {drive["slug"]: ["205"]}


def test_published_meetings_keep_their_addresses():
    """Without kept identities, as when a town's meetings were published before they were kept, each meeting
    keeps the address of its first recorded listing, and its other listings' addresses send readers to it."""
    store = store_of(CALENDAR, AGENDA)
    ledger = identity.assign(store, None)
    assert ledger["meetings"] == {CALENDAR["slug"]: {"date": DAY, "board": "planning board", "listings": ["101", "agendacenter-5"],
                                                     "addresses": [AGENDA["slug"]]}}
    combined, _ = listings.combined(store)
    assert combined["101"]["slug"] == CALENDAR["slug"] and combined["101"]["addresses"] == [AGENDA["slug"]]


def test_a_first_run_identifies_the_published_meetings_before_its_new_listings(tmp_path):
    (tmp_path / "meetings").mkdir()
    published = store_of(AGENDA)
    store = {**published, **store_of(CALENDAR)}
    identity.update(tmp_path, store, "", previous=published)
    assert grouping(identity.load(tmp_path)) == {AGENDA["slug"]: ["101", "agendacenter-5"]}


def test_a_new_towns_meetings_have_their_own_addresses_from_the_start(tmp_path):
    """With no meetings published before its first run, a town's meetings are at their board-and-day
    addresses, not their first listings' own (which no page ever had), and none says it moved."""
    (tmp_path / "meetings").mkdir()
    store = store_of(AGENDA, CALENDAR)
    identity.update(tmp_path, store, "", previous={})
    assert identity.load(tmp_path)["meetings"] == {
        "2026-10-06-planning-board": {"date": DAY, "board": "planning board", "listings": ["101", "agendacenter-5"]}}


def test_a_board_is_shown_under_its_name_as_first_recorded():
    """The calendar's 'Waltham Historical Commission' is the Agenda Center's 'Historical Commission', recorded
    first: one board, with one name, whichever listing a meeting has; every name the board's listings have
    given is kept, for each of its meetings (a board page published under any of them stays reachable)."""
    first = record("agendacenter-1", "Historical Commission", date="2026-09-01", source="agendacenter", seen="2026-09-01T07:00:00")
    later = record("2", "Waltham Historical Commission", time="19:00")
    found = identity.meetings(store_of(first, later), {}, "Waltham")
    assert [(m["id"], m["body"], m["body_names"]) for m in found] == [
        ("2026-09-01-historical-commission", "Historical Commission", ["Historical Commission", "Waltham Historical Commission"]),
        ("2026-10-06-historical-commission", "Historical Commission", ["Historical Commission", "Waltham Historical Commission"])]


def test_a_board_name_no_listing_has_any_more_is_kept():
    """A name the ledger has for a board stays in its meetings' names after the listings are renamed, and a
    new name is added; none is dropped."""
    ledger = identity.assign(store_of(record("1", "Waltham Historical Commission")), None, "Waltham")
    assert ledger["boards"] == {"historical commission": ["Waltham Historical Commission"]}
    store = store_of(record("1", "Historical Commission"))
    assert identity.assign(store, ledger, "Waltham")["boards"] == {
        "historical commission": ["Historical Commission", "Waltham Historical Commission"]}
    assert [m["body_names"] for m in identity.meetings(store, ledger, "Waltham")] == [
        ["Historical Commission", "Waltham Historical Commission"]]


def test_an_address_is_never_given_to_another_meeting():
    """A meeting already published at 2026-10-06-planning-board-2 (a listing's own address) keeps it; a
    new meeting of the board that day takes the next free number."""
    store = store_of(record("1", time="17:00"))
    store["1"]["slug"] = "2026-10-06-planning-board-2"
    ledger = identity.assign(store, None)
    store.update(store_of(record("2", time="19:00", seen="2026-10-02T07:00:00"),
                          record("agendacenter-3", source="agendacenter", title="Planning Board Workshop", seen="2026-10-02T07:00:00")))
    assert grouping(identity.assign(store, ledger)) == {
        "2026-10-06-planning-board-2": ["1"], "2026-10-06-planning-board": ["2"], "2026-10-06-planning-board-3": ["agendacenter-3"]}


# ---- The rules -----------------------------------------------------------------

def meetings_of(*records, town=""):
    return sorted((sorted(r["id"] for r in m["records"]), m["unplaced"]) for m in identity.meetings(store_of(*records), {}, town))


def test_an_untimed_listing_joins_the_meeting_with_a_listing_of_its_kind():
    """Two meetings at different times, one a hearing: the Agenda Center's hearing agenda is the hearing's."""
    assert meetings_of(record("1", time="18:00", title="Planning Board Public Hearing"), record("2", time="19:00"),
                       record("agendacenter-5", source="agendacenter", title="Planning Board Public Hearing Agenda")) == [
        (["1", "agendacenter-5"], False), (["2"], False)]


def test_an_untimed_listing_joins_the_only_meeting_listed_elsewhere():
    """Waltham's calendar lists "Waltham School Committee Special Meeting", the district's page the
    meeting of that day by its date alone: one meeting, without an exclude_pattern or an alias."""
    calendar = record("1", "School Committee", time="18:00", title="Waltham School Committee Special Meeting", special=True)
    page = record("page-1", "School Committee", source="page", title="School Committee Meeting")
    assert meetings_of(calendar, page, town="Waltham") == [(["1", "page-1"], False)]
    m = next(iter(listings.combined(store_of(calendar, page), ledger={}, town="Waltham")[0].values()))
    assert m["same_as"] == "only_meeting"


@pytest.mark.parametrize("title", ["Council on Aging Grant Committee Meeting Agenda", "Council on Aging Site Walk",
                                   "Council on Aging Workshop Agenda", "Council on Aging Executive Session"])
def test_a_listing_of_another_kind_isnt_the_only_meeting_listed_elsewhere(title):
    """Rule 3 joins a listing to the board's only meeting when they differ only in saying it's special or a
    hearing: a committee's meeting, a site walk, a workshop or an executive session is a meeting of its own."""
    calendar = record("1", "Council on Aging", time="10:00")
    agenda = record("agendacenter-2", "Council on Aging", source="agendacenter", title=title)
    assert meetings_of(calendar, agenda) == [(["1"], False), (["agendacenter-2"], False)]


def test_a_meeting_that_could_now_be_either_of_two_says_so_whatever_the_order():
    """An untimed listing identified on its own becomes unplaced when two meetings it could be are found,
    as it is when all three are recorded at once."""
    untimed = record("agendacenter-5", source="agendacenter", title="Planning Board Agenda")
    timed = [record("1", time="17:00", seen="2026-10-02T07:00:00"), record("2", time="19:00", seen="2026-10-02T07:00:00")]
    for batches in ([[untimed], timed], [[untimed, *timed]], [timed, [untimed]]):
        _, ledger = arrive(*batches)
        unplaced = {mid: e.get("unplaced", False) for mid, e in ledger["meetings"].items()}
        assert [unplaced[mid] for mid, e in ledger["meetings"].items() if e["listings"] == ["agendacenter-5"]] == [True]
        assert sum(unplaced.values()) == 1


def test_one_sources_listings_of_different_kinds_are_different_meetings():
    """Lowell's Agenda Center posts the School Committee's meeting and a special meeting for one day."""
    assert meetings_of(record("agendacenter-1", "School Committee", source="agendacenter", title="School Committee Meeting"),
                       record("agendacenter-2", "School Committee", source="agendacenter", special=True,
                              title="Special Meeting/Executive Session Meeting Agenda")) == [
        (["agendacenter-1"], False), (["agendacenter-2"], False)]


def test_an_untimed_listing_that_could_be_either_of_two_meetings_is_shown_on_its_own():
    reposted = [record(f"agendacenter-{n}", source="agendacenter", title="Planning Board Agenda") for n in (5, 6)]
    assert meetings_of(record("1", time="17:00"), record("2", time="19:00"), *reposted) == [
        (["1"], False), (["2"], False), (["agendacenter-5", "agendacenter-6"], True)]
    # A third timed meeting found later doesn't place it.
    store, ledger = arrive([record("1", time="17:00"), record("2", time="19:00"), *reposted])
    store.update(store_of(record("3", time="20:00", seen="2026-10-05T07:00:00")))
    after = identity.assign(store, ledger)
    assert after["meetings"]["2026-10-06-planning-board-2"] == {
        "date": DAY, "board": "planning board", "listings": ["agendacenter-5", "agendacenter-6"], "unplaced": True}


def test_an_unplaced_listing_says_so():
    m = next(m for m in listings.combined(store_of(record("1", time="17:00"), record("2", time="19:00"), AGENDA), ledger={})[0].values()
             if m["id"] == "agendacenter-5")
    assert m["unplaced"] is True


def test_a_joint_meeting_posted_under_each_board_is_one_meeting():
    """Methuen's joint meeting of the City Council and the School Committee, posted in both boards' Agenda
    Center categories and on the district's page, without the town's aliases for it."""
    council = record("agendacenter-1007", "City Council", source="agendacenter", title="School Committee and City Council Joint Meeting (PDF)")
    school = record("agendacenter-1008", "School Committee", source="agendacenter", title="City Council and School Committee Joint Meeting (PDF)")
    page = record("page-joint", "School Committee", source="page", title="Joint Meeting - City Council")
    others = [record("2", "City Council", date="2026-10-01", time="19:00"), record("3", "School Committee", date="2026-10-02", time="19:00")]
    found = identity.meetings(store_of(council, school, page, *others), {})
    joint = next(m for m in found if m["records"][0]["date"] == DAY)
    assert sorted(r["id"] for r in joint["records"]) == ["agendacenter-1007", "agendacenter-1008", "page-joint"]
    assert joint["body"] == "Joint City Council and School Committee"
    assert joint["id"] == "2026-10-06-joint-city-council-and-school-committee"


def test_a_board_the_town_names_as_joint_is_that_board():
    """Beverly's alias names the council's joint committee meeting; its title's other names aren't boards to add."""
    found = identity.meetings(store_of(
        record("1", "City Council Joint Committee", time="18:00", title="Joint Meeting Legal Affairs/Public Services/Committee of the Whole Meeting"),
        record("agendacenter-2", "City Council Joint Committee", source="agendacenter",
               title="Joint Meeting Legal Affairs/Public Services/Committee of the Whole Meeting Agenda (PDF)"),
        record("3", "City Council", date="2026-10-01", time="19:00")), {})
    assert sorted(len(m["records"]) for m in found) == [1, 2]


@pytest.mark.parametrize("name, same", [
    ("Waltham Historical Commission", "Historical Commission"),
    ("Board of Survey & Planning", "Board of Survey and Planning"),
    ("City Council Finance Sub Committee", "City Council Finance Subcommittee"),
    ("City Council Finance Sub-Committee", "City Council Finance Subcommittee"),
    ("Board of Assessor", "Board of Assessors"),
    ("Trustees of the Trust Funds", "Trustees of Trust Funds"),
])
def test_board_names_compared_in_one_form(name, same):
    assert identity.board_key(name, "Waltham") == identity.board_key(same, "Waltham")


@pytest.mark.parametrize("name, other", [
    ("City Council", "City Council Finance Committee"),
    ("Licensing Commission", "License Commission"),     # a town's alias says these are one
])
def test_different_boards_stay_apart(name, other):
    assert identity.board_key(name, "Waltham") != identity.board_key(other, "Waltham")


def test_a_drive_files_variant_beyond_the_boards_name_is_a_kind():
    """Gloucester's Drive file 'School Committee Agenda' gave the variant 'School Committee': the
    board's own name, so the district's schedule and the calendar list the same meeting."""
    drive = record("drive-1", "School Committee", source="drive", title="School Committee: School Committee", variant="School Committee")
    schedule = record("schedule-1", "School Committee", source="drive", title="School Committee", variant="")
    workshop = record("drive-2", "School Committee", source="drive", title="School Committee: Governance Workshop", variant="Governance Workshop")
    assert meetings_of(drive, schedule, workshop) == [(["drive-1", "schedule-1"], False), (["drive-2"], False)]


# ---- An agenda's time ----------------------------------------------------------

NOV_2 = "2026-11-02"
# Derry's Conservation Commission on November 2: the calendar's 7 PM meeting, and the Agenda Center's
# notice of a site walk and agenda for the meeting, neither giving a time; their agendas say 3 PM and 7 PM.
DERRY_CALENDAR = record("659", "Conservation Commission", date=NOV_2, time="19:00", title="Conservation Commission")
SITE_WALK = record("agendacenter-376", "Conservation Commission", date=NOV_2, source="agendacenter",
                   title="Conservation Commission Meeting", posted_title="Conservation Commission Site Walk")
DERRY_AGENDA = record("agendacenter-383", "Conservation Commission", date=NOV_2, source="agendacenter",
                      title="Conservation Commission Meeting", posted_title="Conservation Committee Meeting")
DERRY_TIMES = {"agendacenter-376": "15:00", "agendacenter-383": "19:00"}


def test_an_untimed_listing_joins_the_meeting_at_its_agendas_time():
    store = store_of(DERRY_CALENDAR, SITE_WALK, DERRY_AGENDA)
    found = identity.meetings(store, {}, times=DERRY_TIMES)
    assert sorted(sorted(r["id"] for r in m["records"]) for m in found) == [["659", "agendacenter-383"], ["agendacenter-376"]]
    # Without the agendas' times, the agenda's "Committee" makes it a meeting of its own, as before.
    assert len(identity.meetings(store, {})) == 3
    # The same meetings whatever order the listings arrive in.
    for order in itertools.permutations([DERRY_CALENDAR, SITE_WALK, DERRY_AGENDA]):
        store, ledger = {}, {"meetings": {}}
        for batch in ([order[0]], order[1:]):
            store.update(store_of(*batch))
            ledger = identity.assign(store, ledger, times=DERRY_TIMES)
        assert sorted(grouping(ledger).values()) == [["659", "agendacenter-383"], ["agendacenter-376"]]


def test_a_meeting_already_recorded_joins_the_meeting_at_its_agendas_time_and_its_address_leads_there():
    """The agendas' summaries are made after their listings are placed: the meeting recorded apart becomes
    one with the calendar's, at the address recorded first, and its own address leads to it."""
    store = store_of(DERRY_CALENDAR, SITE_WALK, DERRY_AGENDA)
    ledger = identity.assign(store, {"meetings": {}})
    assert grouping(ledger) == {"2026-11-02-conservation-commission": ["659"], "2026-11-02-conservation-commission-2": ["agendacenter-376"],
                                "2026-11-02-conservation-commission-3": ["agendacenter-383"]}
    after = identity.assign(store, ledger, times=DERRY_TIMES)
    assert grouping(after) == {"2026-11-02-conservation-commission": ["659", "agendacenter-383"],
                               "2026-11-02-conservation-commission-2": ["agendacenter-376"]}
    assert after["meetings"]["2026-11-02-conservation-commission"]["addresses"] == ["2026-11-02-conservation-commission-3"]
    # Kept from run to run, and never given to another meeting.
    assert identity.assign(store, json.loads(json.dumps(after)), times=DERRY_TIMES) == after
    assert identity.assign(store, after) == after
    combined, _ = listings.combined(store, ledger=after, times=DERRY_TIMES)
    m = combined["659"]
    assert (m["slug"], m["addresses"], m["same_as"]) == ("2026-11-02-conservation-commission", ["2026-11-02-conservation-commission-3"], "agenda_time")
    assert combined["agendacenter-376"]["slug"] == "2026-11-02-conservation-commission-2"


def test_a_meeting_unplaced_is_placed_by_its_agendas_time():
    untimed = record("agendacenter-5", source="agendacenter", title="Planning Board Agenda")
    store = store_of(record("1", time="17:00"), record("2", time="19:00"), untimed)
    ledger = identity.assign(store, {"meetings": {}})
    assert ledger["meetings"]["2026-10-06-planning-board-2"]["unplaced"] is True
    after = identity.assign(store, ledger, times={"agendacenter-5": "19:00"})
    assert after["meetings"]["2026-10-06-planning-board-1900"] == {
        "date": DAY, "board": "planning board", "listings": ["2", "agendacenter-5"], "addresses": ["2026-10-06-planning-board-2"]}
    assert "2026-10-06-planning-board-2" not in after["meetings"]


def test_a_site_walk_and_a_meeting_at_their_agendas_times_stay_two_meetings():
    """Two untimed listings that are two meetings, their agendas at 3 PM and 7 PM, with or without the
    calendar's: Derry's October 19 site walk and the meeting the calendar lists."""
    walk = record("agendacenter-378", "Conservation Commission", source="agendacenter", posted_title="Conservation Commission Site Walk")
    regular = record("agendacenter-375", "Conservation Commission", source="agendacenter", posted_title="Conservation Commission Regular Meeting")
    calendar = record("661", "Conservation Commission", time="19:00", title="Conservation Commission")
    times = {"agendacenter-378": "15:00", "agendacenter-375": "19:00"}
    for records, expected in (([walk, regular], [["agendacenter-375"], ["agendacenter-378"]]),
                              ([calendar, walk, regular], [["661", "agendacenter-375"], ["agendacenter-378"]])):
        for given in ({}, times):
            found = identity.meetings(store_of(*records), {}, times=given)
            assert sorted(sorted(r["id"] for r in m["records"]) for m in found) == expected


def test_an_agendas_time_no_meeting_has_places_the_listing_as_one_without_a_time():
    """A calendar and an agenda can give one meeting's time half an hour apart: the agenda's 6 PM doesn't
    make the calendar's 6:30 meeting two."""
    calendar = record("1", time="18:30")
    store = store_of(calendar, AGENDA)
    assert meetings_of(calendar, AGENDA) == [(["1", "agendacenter-5"], False)]
    found = identity.meetings(store, {}, times={"agendacenter-5": "18:00"})
    assert [sorted(r["id"] for r in m["records"]) for m in found] == [["1", "agendacenter-5"]]
    ledger = identity.assign(store, {"meetings": {}})
    assert identity.assign(store, ledger, times={"agendacenter-5": "18:00"}) == ledger


def test_agenda_times_are_read_from_the_agendas_summaries(tmp_path):
    (tmp_path / "summaries").mkdir()
    for sha, summary in (("a" * 64, {"kind": "agenda", "start_time": "19:00"}), ("b" * 64, {"kind": "agenda", "start_time": ""}),
                         ("c" * 64, {"kind": "agenda", "start_time": "7 PM"}), ("d" * 64, {"kind": "minutes"})):
        (tmp_path / "summaries" / f"{sha}.json").write_text(json.dumps(summary))
    store = store_of(record("agendacenter-1", source="agendacenter", agendas=[{"sha256": "d" * 64}, {"sha256": "a" * 64}]),
                     record("agendacenter-2", source="agendacenter", agendas=[{"sha256": "b" * 64}]),
                     record("agendacenter-3", source="agendacenter", agendas=[{"sha256": "c" * 64}]),
                     record("agendacenter-4", source="agendacenter", agendas=[{"sha256": "e" * 64}]),
                     record("agendacenter-5", source="agendacenter"),
                     record("6", time="18:00", agendas=[{"sha256": "a" * 64}]))
    assert identity.agenda_times(store, tmp_path) == {"agendacenter-1": "19:00"}


def test_a_run_places_listings_by_their_agendas_times(tmp_path):
    """identity.update reads the agendas' times from the town's summaries."""
    (tmp_path / "meetings").mkdir()
    (tmp_path / "summaries").mkdir()
    store = store_of(DERRY_CALENDAR, SITE_WALK, DERRY_AGENDA)
    identity.update(tmp_path, store, "", previous={})
    assert len(identity.load(tmp_path)["meetings"]) == 3
    for m, sha in ((SITE_WALK, "5" * 64), (DERRY_AGENDA, "7" * 64)):
        (tmp_path / "summaries" / f"{sha}.json").write_text(json.dumps({"kind": "agenda", "start_time": DERRY_TIMES[m["id"]]}))
        store[m["id"]]["agendas"] = [{"sha256": sha}]
    identity.update(tmp_path, store, "")
    assert grouping(identity.load(tmp_path)) == {"2026-11-02-conservation-commission": ["659", "agendacenter-383"],
                                                 "2026-11-02-conservation-commission-2": ["agendacenter-376"]}
