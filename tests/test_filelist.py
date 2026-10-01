"""Meetings, agendas and minutes from a town website with one documents page for
every board and a meetings calendar by month (Wallingford, Connecticut's, saved
and trimmed: tests/fixtures/filelist_*)."""

import copy
import re
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse

from pipeline import fetch_meetings, fetch_minutes, filelist
from pipeline.fetch_meetings import load_store

BASE = "https://www.wallingfordct.gov"
DOCUMENTS_URL = f"{BASE}/minutes-and-agendas/"
CALENDAR_URL = f"{BASE}/events/meetings/"
DOCUMENTS = (FIXTURES / "filelist_documents.html").read_text(encoding="utf-8")
OCTOBER = (FIXTURES / "filelist_calendar_2026-10.html").read_text(encoding="utf-8")
NOVEMBER = (FIXTURES / "filelist_calendar_2026-11.html").read_text(encoding="utf-8")
# A month with nothing on the calendar.
EMPTY_MONTH = re.sub(r"<section>.*?</section>", "", OCTOBER, flags=re.S)
NOW = datetime(2026, 10, 1, 7, 0, tzinfo=TZ)
BOARDS = ["Town Council", "Board of Assessment Appeals", "Conservation Commission",
          "Inland Wetlands & Watercourses Commission", "Ordinance Committee", "Parks & Recreation Commission",
          "Personnel and Pension Appeals Board", "Planning & Zoning Commission", "Public Utilities Commission",
          "Quinnipiac River Linear Trail Advisory Committee", "Zoning Board of Appeals", "Library Board of Managers"]
ALIASES = {"Inland Wetland and Watercourse Commission": "Inland Wetlands & Watercourses Commission",
           "Ordinance Committee (TC)": "Ordinance Committee",
           "Quinnipiac River Linear Trail": "Quinnipiac River Linear Trail Advisory Committee"}


def file_link(file_id: int, title: str) -> str:
    """One more file on the documents page, as the city lists them."""
    return (f'\n<li>\n<a href="/minutes-and-agendas/DownloadFile.aspx?FileID={file_id}" class="pdf" target="_blank" '
            f'title="file{file_id}.pdf"><strong>{title}</strong></a></li>')


def with_files(board: str, *links: str) -> str:
    """The documents page with files added to a board's folder."""
    head = f'<a href="#" class="dir"><strong>{board}</strong></a><ul>'
    return DOCUMENTS.replace(head, head + "".join(links), 1)


class FakeTownSite:
    """Serves the saved documents page and calendar months (a month not saved has no
    meetings), the saved event page for every meeting, and a distinct PDF for every file."""

    def __init__(self, documents: str = DOCUMENTS, months: dict | None = None):
        self.documents = documents
        self.months = {"2026/October": OCTOBER, "2026/November": NOVEMBER, **(months or {})}
        self.event_page = (FIXTURES / "filelist_event.html").read_text(encoding="utf-8")
        self.pdf = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        if url == DOCUMENTS_URL:
            return FakeResponse(self.documents.encode())
        if url.startswith(CALENDAR_URL):
            return FakeResponse(self.months.get(url[len(CALENDAR_URL):].strip("/"), EMPTY_MONTH).encode())
        if url.startswith(f"{BASE}/events/20"):
            return FakeResponse(self.event_page.encode())
        if "DownloadFile.aspx" in url or url.endswith(".pdf"):
            return FakeResponse(self.pdf + f"\n% {url}\n".encode())
        raise AssertionError(f"unexpected URL {url}")


def wallingford_meetings() -> dict:
    """[meetings] as Wallingford's config has it."""
    return {"calendar_url": CALENDAR_URL, "archive_url": DOCUMENTS_URL, "archive_name": "town's Minutes & Agendas page",
            "governing_body": "Town Council", "boards": BOARDS, "aliases": ALIASES,
            "file_list": {"documents_url": DOCUMENTS_URL, "calendar_url": CALENDAR_URL, "since": "2026-01-01"}}


@pytest.fixture
def wallingford(config):
    town = copy.deepcopy(config)
    town.pop("archive", None)
    town["meetings"] = wallingford_meetings()
    return town


def files():
    return filelist.parse_documents(DOCUMENTS, DOCUMENTS_URL)


def found(since="2026-01-01"):
    return {(m["body"], m["date"], m["special"]): m for m in filelist.meeting_documents(files(), BOARDS, ALIASES, since)}


# ---- The documents page ----------------------------------------------------------

def test_every_file_with_its_folders():
    listed = files()
    assert len(listed) == 54
    amended = next(f for f in listed if f["file_id"] == 11786)
    assert amended == {"folders": ["Town Council"], "file_id": 11786,
                       "url": f"{DOCUMENTS_URL}DownloadFile.aspx?FileID=11786",
                       "title": "Amended Agenda of Regular Meeting- January 27, 2026",
                       "filename": "AmendedTCAgenda1.27.26.pdf", "video_id": "IFXv8q-ipoM", "video_start": 66}
    archived = next(f for f in listed if f["file_id"] == 11713)
    assert archived["folders"] == ["Town Council", "2025 Town Council Archive", "2025 Town Council Archive Archive"]
    assert archived["video_id"] == "UqHjdZvFyAQ" and "video_start" not in archived
    boards = {f["folders"][0] for f in listed}
    assert {"Town Council Archive (1984 - 2022)", "Planning & Zoning Commission", "Wallingford Celebrates America 250"} <= boards


@pytest.mark.parametrize("title, filename, expected", [
    ("Agenda of Regular Meeting - October 6, 2026", "", ("agenda", "2026-10-06", False, False)),
    ("Amended Agenda of Regular Meeting- January 27, 2026", "", ("agenda", "2026-01-27", False, False)),
    ("Corrected Agenda of Regular Meeting- April 28,2026", "", ("agenda", "2026-04-28", False, False)),
    ("Agenda and Backup of Regular Meeting - September 22, 2026", "", ("agenda", "2026-09-22", False, True)),
    ("Agenda with backup of Regular Meeting-May 12, 2026", "", ("agenda", "2026-05-12", False, True)),
    ("Agenda Packet of Regular Meeting - January 16, 2026", "", ("agenda", "2026-01-16", False, True)),
    ("Special Meeting Agenda Budget Workshop- April 14, 2026", "", ("agenda", "2026-04-14", True, False)),
    # A special meeting's notice lists its business: it is the agenda.
    ("Notice of Special Meeting - August 18, 2026", "", ("agenda", "2026-08-18", True, False)),
    ("Minutes of Regular Meeting - September 22, 2026", "", ("minutes", "2026-09-22", False, False)),
    ("Special Meeting Minutes-April 13, 2026", "", ("minutes", "2026-04-13", True, False)),
    ("Minutes of Swearing in Ceremony- January 5, 2026", "", ("minutes", "2026-01-05", False, False)),
    ("Corrected Minutes of Regular Meeting - August 10, 2026", "", ("minutes", "2026-08-10", False, False)),
    ("Cancellation of Regular Meeting-August 11, 2026", "", ("cancellation", "2026-08-11", False, False)),
    ("Cancellation Notice of Special Meeting - May 21, 2026", "", ("cancellation", "2026-05-21", True, False)),
    # A typo in the month: the file name has the date.
    ("Minutes of Regular Meeting- Match 11, 2026", "BOAAminutes3.11.26.pdf", ("minutes", "2026-03-11", False, False)),
])
def test_title_variants(title, filename, expected):
    info = filelist.parse_title(title, filename)
    assert (info["kind"], info["date"], info["special"], info["backup"]) == expected


@pytest.mark.parametrize("title", [
    "Addendum to Regular Meeting Agenda - August 15, 2023",
    "Additional Backup of Regular Meeting - September 8, 2026",
    "Text Amendment #505-26 for Regular Meeting - August 10, 2026",
    "Application for Text Amendment #505-26 for Regular Meeting - August 10, 2026",
    "Director’s Report Feb 11, 2026",
    "Annual Report - March 2026",
    "Agenda of Special Meeting - January 5",  # no year, and none in the file name
])
def test_other_documents_are_left_out(title):
    assert filelist.parse_title(title, "TCSwearingIn01.05.pdf") is None


def test_dates_in_titles():
    assert filelist.date_of("Director’s Report Feb 11, 2026") == "2026-02-11"
    assert filelist.date_of("Corrected Agenda of Regular Meeting- November 10,2025") == "2025-11-10"
    assert filelist.date_of("Agenda of Special Meeting (Ordinance Committee) -April 9, 2026") == "2026-04-09"
    assert filelist.date_of("Minutes of Regular Meeting- Match 11, 2026") is None


@pytest.mark.parametrize("folder, board", [
    ("Town Council", "Town Council"),
    ("Planning & Zoning Commission", "Planning & Zoning Commission"),
    ("Ordinance Committee (TC)", "Ordinance Committee"),       # an alias
    ("Town Council Archive (1984 - 2022)", None),              # folders match whole
    ("Wallingford Celebrates America 250", None),              # not listed
])
def test_folders_are_matched_to_listed_boards(folder, board):
    assert filelist.folder_board(folder, BOARDS, ALIASES) == board


def test_documents_make_meetings_from_the_start_date():
    meetings = found()
    assert all(day >= "2026-01-01" for _, day, _ in meetings)
    assert not any(m["body"] not in BOARDS for m in meetings.values())
    # The newest plain agenda is the agenda; one with backup is never the agenda.
    assert meetings["Town Council", "2026-09-22", False]["agenda"]["file_id"] == 12184
    assert meetings["Town Council", "2026-07-14", False]["agenda"]["file_id"] == 12087
    may = meetings["Town Council", "2026-05-12", False]
    assert may["agenda"] is None and may["packet"]["file_id"] == 11974
    assert meetings["Town Council", "2026-09-22", False]["minutes"]["file_id"] == 12188
    # Corrected minutes replace the first ones; the text amendments aren't documents of the meeting.
    zoning = meetings["Planning & Zoning Commission", "2026-08-10", False]
    assert zoning["minutes"]["file_id"] == 12181 and zoning["agenda"]["file_id"] == 12117
    assert meetings["Board of Assessment Appeals", "2026-03-11", False]["minutes"]["file_id"] == 11881
    assert meetings["Ordinance Committee", "2026-09-01", False]["agenda"]["file_id"] == 12139
    later = found("2026-09-01")
    assert min(day for _, day, _ in later) == "2026-09-01"


def test_regular_and_special_meetings_on_one_day():
    meetings = found()
    # Both kinds of document for both: two meetings.
    regular, special = meetings["Town Council", "2026-01-27", False], meetings["Town Council", "2026-01-27", True]
    assert (regular["agenda"]["file_id"], regular["minutes"]["file_id"]) == (11786, 11807)
    assert (special["agenda"]["file_id"], special["minutes"]["file_id"]) == (11782, 11808)
    assert meetings["Town Council", "2026-04-14", True]["minutes"]["file_id"] == 11949
    assert meetings["Town Council", "2026-04-14", False]["minutes"] is None
    # A regular meeting cancelled for a special one.
    assert meetings["Conservation Commission", "2026-04-09", False]["cancelled"]
    held = meetings["Conservation Commission", "2026-04-09", True]
    assert not held["cancelled"] and held["minutes"]["file_id"] == 11953
    # A special meeting's agenda with minutes labelled regular: one meeting, special by its agenda.
    parks = [k for k in meetings if k[:2] == ("Parks & Recreation Commission", "2026-09-02")]
    assert parks == [("Parks & Recreation Commission", "2026-09-02", True)]
    assert meetings[parks[0]]["minutes"]["file_id"] == 12162
    swearing_in = meetings["Town Council", "2026-01-05", True]
    assert swearing_in["minutes"]["file_id"] == 11809 and ("Town Council", "2026-01-05", False) not in meetings


def test_cancellations_and_recordings():
    meetings = found()
    august = meetings["Town Council", "2026-08-11", False]
    assert august["cancelled"] and august["agenda"] is None
    assert filelist.document_fields(august) == {"special": False, "status": "cancelled"}
    assert meetings["Zoning Board of Appeals", "2026-09-22", False]["cancelled"]
    assert meetings["Personnel and Pension Appeals Board", "2026-10-06", False]["cancelled"]
    fields = filelist.document_fields(meetings["Town Council", "2026-01-27", False])
    agenda, minutes = (f"{DOCUMENTS_URL}DownloadFile.aspx?FileID={n}" for n in (11786, 11807))
    assert fields == {"special": False, "agenda_id": "filelist-11786", "agenda_url": agenda, "documents_url": agenda,
                      "minutes_id": "filelist-11807", "minutes_url": minutes, "video_id": "IFXv8q-ipoM", "video_start": 66}


# ---- The calendar -----------------------------------------------------------------------

def test_month_url():
    assert filelist.month_url(CALENDAR_URL, datetime(2026, 10, 1).date()) == f"{CALENDAR_URL}2026/October/"


def test_calendar_month():
    events = {e["id"]: e for e in filelist.parse_month(OCTOBER, CALENDAR_URL, BOARDS, ALIASES)}
    assert len(events) == 12  # the calendar view below the list repeats them
    council = events["filelist-town-council-47"]
    assert council["source_url"] == f"{BASE}/events/2026/10/13/town-council-47/"
    assert (council["date"], council["start_time"], council["end_time"]) == ("2026-10-13", "18:30", None)
    # The page's datetime attributes say 07:00 for 7:00 PM; the text is right.
    assert events["filelist-quinnipiac-river-linear-trail-10"]["start_time"] == "19:00"
    assert events["filelist-quinnipiac-river-linear-trail-10"]["body"] == "Quinnipiac River Linear Trail Advisory Committee"
    assert events["filelist-inland-wetland-and-watercourse-commission-6"]["body"] == "Inland Wetlands & Watercourses Commission"
    assert events["filelist-ordinance-committee-18"]["body"] == "Ordinance Committee"
    scrcog = events["filelist-scrog-board-committee-8"]
    assert (scrcog["body"], scrcog["start_time"], scrcog["end_time"]) == ("SCRCOG Board & Executive Committee", "09:00", "11:00")
    library = events["filelist-library-board-of-managers-11"]
    pdf = f"{BASE}/Customer-Content/www/events/PDFs/Wallingford_Public_Library_Special_Meeting_Agenda_October_5__2026.pdf"
    assert library["links"] == [{"url": pdf, "text": "Special Agenda"}]
    assert filelist.calendar_documents(library)["special"]
    cancelled = events["filelist-personnel-and-pension-appeals-board-5"]
    assert filelist.calendar_documents(cancelled) == {"status": "cancelled"}


def test_event_page_location():
    page = (FIXTURES / "filelist_event.html").read_text(encoding="utf-8")
    assert filelist.parse_event_page(page) == {
        "location_name": "Wallingford Town Hall, Robert F. Parisi Council Chambers",
        "address": "45 South Main Street, Wallingford, CT 06492"}
    assert filelist.parse_event_page("<time>Tuesday</time><p></p>") == {"location_name": "", "address": ""}


# ---- Collected ------------------------------------------------------------------------------

def test_one_request_per_listing_and_only_upcoming_agendas(wallingford, tmp_path):
    client = FakeTownSite()
    status = fetch_meetings.run(wallingford, client, tmp_path, now=NOW)
    assert not status["errors"] and not status["failed_calendars"], status
    listings = [u for u in client.urls if u == DOCUMENTS_URL or u.startswith(CALENDAR_URL)]
    assert listings == [f"{CALENDAR_URL}2026/October/", f"{CALENDAR_URL}2026/November/", DOCUMENTS_URL]
    downloads = sorted(u.rsplit("/", 1)[-1] for u in client.urls if "DownloadFile" in u or u.endswith(".pdf"))
    # Upcoming meetings' agendas only: the trail committee's and the Public Utilities Commission's from the
    # documents page, the Library Board's (no folder there) from the calendar. Nothing before the start date.
    assert downloads == ["DownloadFile.aspx?FileID=12190", "DownloadFile.aspx?FileID=12194",
                         "Wallingford_Public_Library_Special_Meeting_Agenda_October_5__2026.pdf"]
    store = load_store(tmp_path)
    assert all(m["date"] >= "2026-01-01" for m in store.values())
    assert not any(m["body"] in ("SCRCOG Board & Executive Committee", "Wallingford Celebrates America 250")
                   for m in store.values())
    # A second run reads the listings again, and nothing else it already has.
    again = FakeTownSite()
    fetch_meetings.run(wallingford, again, tmp_path, now=NOW)
    assert again.urls == listings


def test_calendar_meetings_get_their_documents(wallingford, tmp_path):
    fetch_meetings.run(wallingford, FakeTownSite(), tmp_path, now=NOW)
    store = load_store(tmp_path)
    utilities = store["filelist-public-utilities-commission-58"]
    assert utilities["title"] == "Public Utilities Commission Meeting" and utilities["start_time"] == "18:00"
    assert [a["id"] for a in utilities["agendas"]] == ["filelist-12194"]  # the documents page's copy, not the calendar's
    assert utilities["location_name"] == "Wallingford Town Hall, Robert F. Parisi Council Chambers"
    assert utilities["address"] == "45 South Main Street, Wallingford, CT 06492"
    library = store["filelist-library-board-of-managers-11"]
    assert library["special"] and library["title"] == "Library Board of Managers Special Meeting"
    assert library["agendas"][0]["source_url"].endswith("Wallingford_Public_Library_Special_Meeting_Agenda_October_5__2026.pdf")
    assert not store["filelist-library-board-of-managers-10"].get("agendas")
    assert store["filelist-personnel-and-pension-appeals-board-5"]["status"] == "cancelled"
    assert store["filelist-inland-wetland-and-watercourse-commission-6"]["body"] == "Inland Wetlands & Watercourses Commission"
    assert store["filelist-ordinance-committee-18"]["body"] == "Ordinance Committee"


def test_meetings_known_only_from_their_documents(wallingford, tmp_path):
    fetch_meetings.run(wallingford, FakeTownSite(), tmp_path, now=NOW)
    store = load_store(tmp_path)
    council = next(m for m in store.values() if m["body"] == "Town Council" and m["date"] == "2026-09-22")
    assert council["id"] == "filelist-file-12176" and council["slug"] == "2026-09-22-town-council"
    assert council["title"] == "Town Council Meeting" and council["source_url"] == DOCUMENTS_URL
    assert council["documents_url"] == f"{DOCUMENTS_URL}DownloadFile.aspx?FileID=12184"
    assert council["minutes_id"] == "filelist-12188" and not council.get("agendas")  # past: linked, not saved
    january = [m for m in store.values() if m["body"] == "Town Council" and m["date"] == "2026-01-27"]
    assert sorted(m["title"] for m in january) == ["Town Council Meeting", "Town Council Special Meeting"]
    assert next(m for m in january if not m["special"])["video_id"] == "IFXv8q-ipoM"
    cancelled = next(m for m in store.values() if m["body"] == "Town Council" and m["date"] == "2026-08-11")
    assert cancelled["status"] == "cancelled"


def test_an_amended_agenda_is_a_new_version(wallingford, tmp_path):
    fetch_meetings.run(wallingford, FakeTownSite(), tmp_path, now=NOW)
    amended = with_files("Public Utilities Commission", file_link(12200, "Amended Agenda of Regular Meeting - October 6, 2026"))
    fetch_meetings.run(wallingford, FakeTownSite(amended), tmp_path, now=NOW.replace(day=2))
    utilities = load_store(tmp_path)["filelist-public-utilities-commission-58"]
    assert [a["id"] for a in utilities["agendas"]] == ["filelist-12194", "filelist-12200"]
    assert utilities["history"][-1]["field"] == "agenda"


def test_a_cancellation_posted_later(wallingford, tmp_path):
    fetch_meetings.run(wallingford, FakeTownSite(), tmp_path, now=NOW)
    notice = with_files("Town Council", file_link(12201, "Cancellation of Regular Meeting - October 13, 2026"))
    fetch_meetings.run(wallingford, FakeTownSite(notice), tmp_path, now=NOW.replace(day=2))
    council = load_store(tmp_path)["filelist-town-council-47"]
    assert council["status"] == "cancelled"
    assert council["history"][-1] | {"at": None} == {"at": None, "field": "status", "old": "scheduled", "new": "cancelled"}


def test_documents_posted_before_the_calendar_lists_the_meeting(wallingford, tmp_path):
    notice = with_files("Town Council", file_link(12202, "Notice of Special Meeting - October 13, 2026"))
    without = OCTOBER.replace("/events/2026/10/13/town-council-47/", "/events/2026/10/13/elsewhere/").replace(
        "<h1>Town Council</h1>\n\t\t\t\t\t</hgroup>", "<h1>Wallingford Center Inc</h1>\n\t\t\t\t\t</hgroup>", 1)
    fetch_meetings.run(wallingford, FakeTownSite(notice, {"2026/October": without}), tmp_path, now=NOW)
    first = load_store(tmp_path)["filelist-file-12202"]
    assert first["special"] and first["start_time"] is None
    fetch_meetings.run(wallingford, FakeTownSite(notice), tmp_path, now=NOW.replace(day=2))
    store = load_store(tmp_path)
    council = [m for m in store.values() if m["body"] == "Town Council" and m["date"] == "2026-10-13"]
    assert [m["id"] for m in council] == ["filelist-town-council-47"]
    assert council[0]["slug"] == first["slug"] and council[0]["agendas"][0]["id"] == "filelist-12202"
    assert council[0]["start_time"] == "18:30" and not council[0].get("history")


def test_minutes_come_to_a_meeting_after_it_leaves_the_calendar(wallingford, tmp_path):
    fetch_meetings.run(wallingford, FakeTownSite(), tmp_path, now=NOW)
    minutes = with_files("Quinnipiac River Linear Trail Advisory Committee",
                         file_link(12300, "Minutes of Regular Meeting - October 1, 2026"))
    client = FakeTownSite(minutes, {"2026/October": EMPTY_MONTH})
    fetch_meetings.run(wallingford, client, tmp_path, now=datetime(2026, 11, 2, 7, 0, tzinfo=TZ))
    assert f"{CALENDAR_URL}2026/December/" in client.urls
    store = load_store(tmp_path)
    trail = [m for m in store.values() if m["body"] == "Quinnipiac River Linear Trail Advisory Committee"]
    assert [m["id"] for m in trail] == ["filelist-quinnipiac-river-linear-trail-10"]
    assert trail[0]["minutes_id"] == "filelist-12300" and trail[0]["start_time"] == "19:00"


def test_minutes_are_downloaded_once_from_the_start_date(wallingford, tmp_path):
    fetch_meetings.run(wallingford, FakeTownSite(), tmp_path, now=NOW)
    client = FakeTownSite()
    summary = fetch_minutes.run_linked(wallingford, client, tmp_path, now=NOW)
    assert summary == {"minutes_added": 14, "minutes_waiting": 0, "errors": []}
    assert all("DownloadFile.aspx" in u for u in client.urls)
    store = load_store(tmp_path)
    zoning = next(m for m in store.values() if m["body"] == "Planning & Zoning Commission" and m["date"] == "2026-08-10")
    assert [d["id"] for d in zoning["minutes"]] == ["filelist-12181"]
    assert (tmp_path / "meetings" / "minutes" / "filelist-12181.pdf").exists()
    assert fetch_minutes.run_linked(wallingford, FakeTownSite(), tmp_path, now=NOW)["minutes_added"] == 0
    wallingford["meetings"]["file_list"]["since"] = "2026-09-01"
    later = tmp_path / "later"
    fetch_meetings.run(wallingford, FakeTownSite(), later, now=NOW)
    client = FakeTownSite()
    assert fetch_minutes.run_linked(wallingford, client, later, now=NOW)["minutes_added"] == 5
    assert sorted(int(u.rsplit("=", 1)[1]) for u in client.urls) == [12153, 12156, 12162, 12182, 12188]


def test_boards_must_be_listed(wallingford, tmp_path):
    del wallingford["meetings"]["boards"]
    with pytest.raises(SystemExit, match="boards"):
        fetch_meetings.run(wallingford, FakeTownSite(), tmp_path, now=NOW)


def test_a_packet_is_linked_not_saved():
    # A meeting whose only agenda has backup links to it, with nothing to download.
    fields = filelist.document_fields(found()["Town Council", "2026-05-12", False])
    assert "agenda_id" not in fields and "agenda_url" not in fields
    assert fields["documents_url"].endswith("FileID=11974")
