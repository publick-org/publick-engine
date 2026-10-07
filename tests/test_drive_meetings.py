"""School Committee agendas and minutes from the school district's Google Drive."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeDrive, FakeResponse

from pipeline import fetch_drive_meetings
from pipeline.config import load_config
from pipeline.fetch_drive_meetings import find_date, list_folder, parse_name, start_time
from pipeline.fetch_meetings import save_json

NOW = datetime(2026, 9, 27, 7, 0, tzinfo=ZoneInfo("America/New_York"))
SHORT = load_config("gloucester")["drive_meetings"]["abbreviations"]


@pytest.mark.parametrize("name, date, variant, revised, special", [
    ("SC Agenda 9_9_26.pdf", "2026-09-09", "", False, False),
    ("SC Agenda 8_12_26", "2026-08-12", "", False, False),
    ("SC Agenda 10_08_25.pdf", "2025-10-08", "", False, False),
    ("SC Agenda REVISED Agenda 6_10_26.pdf", "2026-06-10", "", True, False),
    ("Special SC Agenda 7_20_26", "2026-07-20", "", False, True),
    ("SC Agenda Online 3_9_26.pdf", "2026-03-09", "", False, False),
    ("SC Governance Workshop Agenda 4_8_26.pdf", "2026-04-08", "Governance Workshop", False, False),
    ("B&F Subcommittee Agenda 9_16_26.pdf", "2026-09-16", "", False, False),
    ("B & F Minutes 11_12_25 .pdf", "2025-11-12", "", False, False),
    ("Program Agenda  10_16_25.pdf", "2025-10-16", "", False, False),
    ("Revised Personnel Agenda (Food Service Workers) 3_23_26.pdf", "2026-03-23", "Food Service Workers", True, False),
    ("Amended SC Minutes 6_24_20.pdf", "2020-06-24", "", True, False),
])
def test_file_names(name, date, variant, revised, special):
    info = parse_name(name, SHORT)
    assert (info["date"], info["variant"], info["revised"], info["special"]) == (date, variant, revised, special)
    assert not info["left_out"]


def test_names_without_a_date_or_left_out():
    assert parse_name("B & F Agenda 6_17_2.pdf", SHORT) is None
    assert parse_name("SC Agenda 13_40_26.pdf", SHORT) is None
    assert parse_name("ES SC Minutes 10_28_20.pdf", SHORT)["left_out"]
    assert parse_name("Joint CC & SC Minutes 9_15_20.pdf", SHORT)["left_out"]


def test_real_folder_page():
    entries = list_folder(FakeDrive().get("https://drive.google.com/embeddedfolderview?id=1uDibz6g_fSFrl7MwqyU_bt__Sb2C_iPB").text)
    assert [e["name"] for e in entries] == [
        "Ad Hoc Communications Subcommittee Minutes", "Building & Finance Subcommittee Minutes",
        "Personnel Subcommittee Minutes", "Program Subcommittee Minutes", "School Committee Minutes"]
    assert all(e["folder"] for e in entries)


def test_collects_documents_from_the_saved_folders(tmp_path):
    config = load_config("gloucester")
    status = fetch_drive_meetings.run(config, FakeDrive(), tmp_path, now=NOW)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    got = sorted((m["date"], m["body"], len(m.get("agendas", [])), len(m.get("minutes", []))) for m in store.values())
    assert got == [
        ("2026-08-12", "School Committee", 1, 1),
        ("2026-08-26", "School Committee", 1, 1),
        ("2026-09-09", "School Committee", 1, 1),
        ("2026-09-16", "School Committee Building & Finance Subcommittee", 1, 0),
        # From the district's schedule, up to 45 days ahead; documents attach when posted.
        ("2026-09-23", "School Committee", 0, 0),
        ("2026-10-07", "School Committee Building & Finance Subcommittee", 0, 0),
        ("2026-10-14", "School Committee", 0, 0),
        ("2026-10-28", "School Committee", 0, 0),
        ("2026-11-04", "School Committee", 0, 0),
        ("2026-11-09", "School Committee Building & Finance Subcommittee", 0, 0),
    ]
    assert status["scheduled_meetings_added"] == 8
    assert status["unreadable_names"] == ["B & F Agenda 6_17_2.pdf"]
    meeting = next(m for m in store.values() if m["date"] == "2026-09-09")
    assert meeting["id"] == "schedule-school-committee-2026-09-09" and meeting["source"] == "drive"
    minutes = meeting["minutes"][0]
    assert minutes["original_filename"] == "SC Minutes 9_9_26.pdf"
    assert minutes["source_url"] == f"https://drive.google.com/file/d/{minutes['id']}/view"
    assert (tmp_path / "meetings" / "minutes" / minutes["file"]).exists()

    # A second run adds nothing and doesn't warn again about the same file name.
    again = fetch_drive_meetings.run(config, FakeDrive(), tmp_path, now=NOW)
    assert again["documents_added"] == 0 and again["new_unreadable_names"] == []


def entry(file_id: str, name: str, folder: bool = False) -> str:
    kind = "drive/folders" if folder else "file/d"
    return (f'<div class="flip-entry" id="entry-{file_id}" tabindex="0" role="link"><div class="flip-entry-info">'
            f'<a href="https://drive.google.com/{kind}/{file_id}" target="_blank"><div class="flip-entry-title">{name}</div>'
            f'</a></div><div class="flip-entry-last-modified"><div>Sep 15</div></div></div>')


class MadeUpDrive(FakeDrive):
    """Folders written by the test, in the markup Drive serves."""
    def __init__(self, folders: dict):
        super().__init__()
        self.folders = folders

    def get(self, url):
        if "embeddedfolderview?id=" in url:
            entries = self.folders.get(url.rsplit("=", 1)[1], [])
            return FakeResponse(f'<div class="flip-entries">{"".join(entry(*e) for e in entries)}</div>'.encode())
        return super().get(url)


def test_versions_variants_and_calendar_meetings(tmp_path):
    config = load_config("gloucester")
    settings = config["drive_meetings"]
    del settings["schedule_url"]
    save_json(tmp_path / "meetings" / "meetings.json", {"12999": {
        "id": "12999", "date": "2026-10-14", "body": "School Committee", "title": "School Committee Meeting",
        "slug": "2026-10-14-school-committee", "first_seen": "2026-10-01T08:00:00-04:00"}})
    drive = MadeUpDrive({
        settings["agendas_folder"]: [("sca", "School Committee Agendas", True), ("pa", "Personnel Subcommittee Agendas", True),
                                     ("xa", "Superintendent Search Agendas", True)],
        settings["minutes_folder"]: [("scm", "School Committee Minutes", True)],
        "sca": [("rev", "SC Agenda REVISED 10_14_26.pdf"), ("orig", "SC Agenda 10_14_26.pdf"),
                ("ws", "SC Governance Workshop Agenda 10_14_26.pdf"), ("old", "SC Agenda 6_10_26.pdf"),
                ("y", "2026-2027 School Year", True), ("y0", "2024-2025 School Year", True)],
        "y": [("in-year", "Special SC Agenda 10_20_26.pdf")],
        "y0": [("too-old", "SC Agenda 10_9_24.pdf")],
        "pa": [("food", "Personnel Agenda (Food Service Workers) 10_5_26.pdf"),
               ("bus", "Personnel Agenda (Transportation Workers) 10_5_26.pdf")],
        "xa": [("other", "Search Agenda 10_1_26.pdf")],
        "scm": [("es", "ES SC Minutes 10_14_26.pdf")],
    })
    status = fetch_drive_meetings.run(config, drive, tmp_path, now=NOW)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())

    # Attached to the meeting on the city calendar, original first and the revision last.
    calendar = store["12999"]
    assert [a["id"] for a in calendar["agendas"]] == ["orig", "rev"]
    assert calendar["history"][0]["field"] == "agenda"
    titles = sorted(m["title"] for m in store.values() if m["id"] != "12999")
    assert titles == ["School Committee", "School Committee Personnel Subcommittee: Food Service Workers",
                      "School Committee Personnel Subcommittee: Transportation Workers", "School Committee: Governance Workshop"]
    special = next(m for m in store.values() if m["date"] == "2026-10-20")
    assert special["special"] and special["title"] == "School Committee"
    assert status["left_out"] == ["ES SC Minutes 10_14_26.pdf"]
    # Files before `since`, in older school-year folders, and in folders not listed in the config are skipped.
    assert not any(u.endswith(("=old", "=too-old", "=other", "=y0")) for u in drive.urls)
    assert status["documents_added"] == 6


def test_site_shows_school_committee_documents(site_dir):
    pages = {p.parent.name: p.read_text() for p in (site_dir / "meetings").glob("2026-09-09-school-committee*/index.html")}
    page = pages["2026-09-09-school-committee"]
    assert "Minutes on the School Committee&#39;s Google Drive" in page
    assert "Agenda on the School Committee&#39;s Google Drive" in page
    assert "Gloucester Public Schools posted this as a scanned image." in page
    assert "known from the <a" in page and "Gloucester Public Schools website" in page
    assert "minutes posted by Gloucester Public Schools<span" in page and "city&#39;s minutes" not in page
    assert "Not listed on the city calendar" not in page
    about = (site_dir / "about" / "index.html").read_text()
    assert "School Committee agendas and minutes:" in about
    board = (site_dir / "meetings" / "boards" / "school-committee" / "index.html").read_text()
    assert "Gloucester Public Schools website" in board


def test_calendar_takes_over_a_meeting_first_recorded_from_drive(tmp_path):
    from fakes import FIXTURES, FakeCityClient

    from pipeline import civicplus, fetch_meetings
    config = load_config("gloucester")
    feed = (FIXTURES / "civicplus_calendar.xml").read_text().replace("6:00 PM Human Rights Commission", "6:00 PM School Committee")
    event = next(e for e in civicplus.parse_calendar_feed(feed.encode(), config["meetings"]["base_url"])
                 if e["body"] == "School Committee")
    settings = config["drive_meetings"]
    del settings["schedule_url"]
    drive = MadeUpDrive({settings["agendas_folder"]: [("sca", "School Committee Agendas", True)],
                         "sca": [("early", f"SC Agenda {int(event['date'][5:7])}_{int(event['date'][8:])}_26.pdf")]})
    fetch_drive_meetings.run(config, drive, tmp_path, now=NOW)
    first = next(iter(json.loads((tmp_path / "meetings" / "meetings.json").read_text()).values()))
    assert first["source"] == "drive"

    fetch_meetings.run(config, FakeCityClient(feed=feed.encode()), tmp_path, now=NOW)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    same_day = [m for m in store.values() if m["date"] == event["date"] and m["body"] == "School Committee"]
    assert len(same_day) == 1
    meeting = same_day[0]
    assert meeting["id"] == event["id"] and meeting["slug"] == first["slug"]
    assert "source" not in meeting and meeting["agendas"][0]["id"] == "early"
    assert meeting["start_time"] == "18:00"
    assert not any(h["field"] in ("title", "start_time") for h in meeting.get("history", []))


def test_schedule_page():
    from fakes import FIXTURES

    from pipeline.fetch_drive_meetings import parse_schedule
    dates = parse_schedule((FIXTURES / "drive" / "schedule.html").read_text())
    school = [d for name, d in dates if name == "School Committee"]
    finance = [d for name, d in dates if name == "Building and Finance Subcommittee"]
    # "September 9t h and 2 3rd , 202 6" as the page writes it.
    assert school[:4] == ["2026-09-09", "2026-09-23", "2026-10-14", "2026-10-28"]
    assert school[-1] == "2027-06-23" and len(school) == 19
    # "February 3rd, 4 th, 10th, 2027".
    assert ["2027-02-03", "2027-02-04", "2027-02-10"] == [d for d in finance if d.startswith("2027-02")]
    assert len(finance) == 12


def test_schedule_changes(tmp_path):
    from pipeline.fetch_drive_meetings import update_schedule
    settings = load_config("gloucester")["drive_meetings"]
    page = "<p>School Committee - October 14th and 28th, 2026</p><p>Gloucester Public Schools | 2 Blackburn Drive</p>"
    store = {}
    today = NOW.date()
    assert update_schedule(store, page, settings, today, "t1") == 2
    assert update_schedule(store, page, settings, today, "t2") == 0
    # October 28 moves to October 29: the old date is marked as no longer listed, not deleted.
    moved = page.replace("28th", "29th")
    assert update_schedule(store, moved, settings, today, "t3") == 1
    old = store["schedule-school-committee-2026-10-28"]
    assert old["listed"] is False and old["history"][-1] == {"at": "t3", "field": "listed", "old": True, "new": False}
    assert store["schedule-school-committee-2026-10-29"]["listed"] is True
    # Back on the schedule: listed again.
    update_schedule(store, page, settings, today, "t4")
    assert old["listed"] is True
    # Dates past schedule_days_ahead aren't added yet.
    assert update_schedule({}, "<p>School Committee - March 10th, 2027 |</p>", settings, today, "t5") == 0


def test_site_lists_upcoming_school_committee_meetings(site_dir):
    upcoming = (site_dir / "meetings" / "index.html").read_text()
    assert "/meetings/2026-10-14-school-committee/" in upcoming
    page = (site_dir / "meetings" / "2026-10-14-school-committee" / "index.html").read_text()
    assert "No agenda posted yet." in page
    assert "known from the <a" in page


# Lewiston's School Committee: a folder a school year, and in it a folder a meeting.
LEWISTON = {
    "source_name": "Lewiston Public Schools",
    "posted_on": "the School Committee's Google Drive",
    "page_url": "https://www.lewistonpublicschools.org/en-US/school-committee-bc9f3846",
    "meetings_folder": "0ByV5LOTYl0ObfnowOTlLRDZpankyc1hZcnF0TUV4U3hnTEljNGdvNDE3Z2p0b0VSS2lfekE",
    "resource_key": "0-kGE8IAFzu2Zp_mSIJG0OQg",
    "body": "School Committee",
    "since": "2026-08-01",
}
NOW_OCTOBER = datetime(2026, 10, 6, 7, 0, tzinfo=ZoneInfo("America/New_York"))


@pytest.mark.parametrize("name, day", [
    ("04 10-5-26", "2026-10-05"),
    ("01 8-24-2026", "2026-08-24"),
    ("01 September 8, 2025", "2025-09-08"),
    ("02 September 22, 2025 (Dingley Bldg)", "2025-09-22"),
    ("21 CANCELED MEETING 3/23/26", "2026-03-23"),
    ("03b 7-29-26 Minutes SD.pdf", "2026-07-29"),
    ("06 LHS School Committee Report 9.21.26.pdf", None),
    ("10a BDA.pdf", None),
])
def test_dates_in_folder_and_file_names(name, day):
    assert find_date(name) == day


def test_time_from_the_agenda():
    assert start_time("1. Call meeting to order on or about 5:30 pm until approximately 8:30.") == "17:30"
    assert start_time("The meeting was called to order at 6 p.m. by the chair.") == "18:00"
    assert start_time("in order to approve 3 items") is None


def test_a_folder_for_each_meeting(tmp_path):
    config = load_config("gloucester")
    config["drive_meetings"] = LEWISTON
    drive = FakeDrive()
    status = fetch_drive_meetings.run(config, drive, tmp_path, now=NOW_OCTOBER)
    assert any("resourcekey=0-kGE8IAFzu2Zp_mSIJG0OQg" in url for url in drive.urls)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    school = {m["date"]: m for m in store.values() if m["body"] == "School Committee"}
    # A meeting folder each from August 24 on; August 3 and 17 are known from their minutes alone.
    assert sorted(school) == ["2026-08-03", "2026-08-17", "2026-08-24", "2026-09-14", "2026-09-21", "2026-10-05"]
    assert "agendas" not in school["2026-08-03"]
    # Each meeting's agenda, never a personnel item's cover; the amended agenda where that's all there is.
    assert [d["original_filename"] for d in school["2026-08-24"]["agendas"]] == ["00 8-24-26 Agenda.pdf"]
    assert [d["original_filename"] for d in school["2026-10-05"]["agendas"]] == ["00 10-5-26 Amended Agenda.pdf"]
    # Minutes by their own date, from a later meeting's packet; earlier ones (before since) left out.
    assert [d["original_filename"] for d in school["2026-08-24"]["minutes"]] == ["03b 8-24-26 Minutes.pdf"]
    assert [d["original_filename"] for d in school["2026-09-21"]["minutes"]] == ["03a 9-21-26 Minutes.pdf"]
    assert "minutes" not in school["2026-10-05"]
    assert all(m["source"] == "drive" and m["status"] == "scheduled" for m in school.values())
    assert status["errors"] == [] and status["unreadable_names"] == []
    # Minutes of meetings before since (in the 9-14 packet) are not meetings of their own.
    assert not any(m["date"] < "2026-08-01" for m in school.values())
    again = fetch_drive_meetings.run(config, FakeDrive(), tmp_path, now=NOW_OCTOBER)
    assert again["documents_added"] == 0 and again["meetings_created"] == 0


def test_cancelled_meetings_notices_and_copies(tmp_path):
    """A CANCELED folder cancels the day's meeting; a NOTICE folder isn't a meeting; the same minutes
    in two packets are attached once, and not again on the next run."""
    def entry(fid, name, folder=False):
        kind = "drive/folders" if folder else "file/d"
        return (f'<div class="flip-entry" id="entry-{fid}"><div class="flip-entry-info">'
                f'<a href="https://drive.google.com/{kind}/{fid}"><div class="flip-entry-title">{name}</div></a></div></div>')
    pages = {
        "root": [entry("year", "2025-2026 School Committee", True), entry("old", "2014-15 School Committee Meetings", True)],
        "year": [entry("m1", "20 March 23, 2026", True), entry("m2", "21 CANCELED MEETING 3/23/26", True),
                 entry("m3", "30 May 4, 2026 NOTICE", True), entry("m4", "31 May 11, 2026", True),
                 entry("m5", "32 May 18, 2026", True)],
        "m1": [entry("a1", "00 3-23-26 Agenda.pdf")],
        "m2": [entry("n1", "00 3-23-26 CANCELED MEETING NOTICE.pdf")],
        "m3": [entry("n2", "00 5-4-26 Notice Retreat.pdf")],
        "m4": [entry("a4", "00 5-11-26 Agenda.pdf"), entry("x4", "03a 3-23-26 Minutes.pdf"),
               entry("e4", "03c 5-11-26 ES Minutes.pdf")],
        "m5": [entry("a5", "00 5-18-26 Agenda.pdf"), entry("y5", "03a 5-11-26 Minutes.pdf"),
               entry("z5", "03b 3-23-26 Minutes.pdf")],
    }

    class Folders(FakeDrive):
        def get(self, url):
            if "embeddedfolderview?id=" in url:
                fid = url.split("id=", 1)[1].split("&", 1)[0]
                return FakeResponse(("<html><body>" + "".join(pages.get(fid, [])) + "</body></html>").encode())
            return super().get(url)

    config = load_config("gloucester")
    config["drive_meetings"] = {**LEWISTON, "meetings_folder": "root", "resource_key": None, "since": "2026-03-01"}
    status = fetch_drive_meetings.run(config, Folders(), tmp_path, now=NOW_OCTOBER)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    school = {m["date"]: m for m in store.values() if m["body"] == "School Committee"}
    assert sorted(school) == ["2026-03-23", "2026-05-11", "2026-05-18"]
    assert school["2026-03-23"]["status"] == "cancelled"
    assert [d["original_filename"] for d in school["2026-03-23"]["minutes"]] == ["03b 3-23-26 Minutes.pdf"]
    assert [d["original_filename"] for d in school["2026-05-11"]["minutes"]] == ["03a 5-11-26 Minutes.pdf"]
    assert status["left_out"] == ["03c 5-11-26 ES Minutes.pdf", "30 May 4, 2026 NOTICE"]
    again = fetch_drive_meetings.run(config, Folders(), tmp_path, now=NOW_OCTOBER)
    assert again["documents_added"] == 0
