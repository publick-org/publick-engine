"""A board's meetings, agendas and minutes from a city's listing of its events' documents, as
Somerville posts its School Committee's. Run offline against two saved pages, trimmed to a few
meetings (tests/fixtures/meetingdocs_page*.html)."""

import copy
import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse
from test_wordfile import docx, p

from pipeline import absences, build_site, fetch_meetingdocs_meetings, meetingdocs
from pipeline.config import load_config

PAGE = "https://www.somervillema.gov/meetingdocs?title_1=School+Committee"
SETTINGS = {
    "page_url": PAGE, "source_name": "City of Somerville", "since": "2026-07-01", "not_agenda_pattern": r"\bcall\b",
    "bodies": {"Regular Meeting": "School Committee",
               "Rules Management": "School Committee Rules Management Subcommittee",
               "Educational Programs": "School Committee Educational Programs and Instruction Subcommittee",
               "School Building Facilities and Maintenance Subcommittee":
                   "School Committee School Building Facilities and Maintenance Subcommittee"},
}
# After the October 19 meetings, before December's.
NOW = datetime(2026, 10, 27, 7, 0, tzinfo=TZ)


def pages() -> list[str]:
    return [(FIXTURES / f"meetingdocs_page{n}.html").read_text() for n in (0, 1)]


class FakeCity:
    """Serves the listing's two pages, and each document: a Word file for a .docx, else a PDF."""

    def __init__(self, listing: list[str] | None = None):
        self.listing = pages() if listing is None else listing
        self.pdf = (FIXTURES / "legistar_minutes.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        if url.startswith("https://www.somervillema.gov/meetingdocs?"):
            return FakeResponse(self.listing[1 if url.endswith("&page=1") else 0].encode())
        if url.endswith(".docx"):
            return FakeResponse(docx(p("Somerville School Committee. Agenda. Regular meeting, August 31, 2026.")))
        if "s3.amazonaws.com" in url:
            return FakeResponse(self.pdf + f"\n% {url}\n".encode())
        raise AssertionError(f"unexpected URL {url}")


def somerville(config: dict) -> dict:
    town = copy.deepcopy(config)
    for table in ("archive", "drive_meetings", "summaries", "participation", "glossary"):
        town.pop(table, None)
    town["town"].update(name="Somerville")
    town["meetings"] = {"governing_body": "City Council", "boards": ["City Council"],
                        "legistar": {"client": "somervillema", "since": "2026-01-01"}}
    town["meetingdocs_meetings"] = copy.deepcopy(SETTINGS)
    return town


@pytest.fixture
def config():
    return somerville(load_config("gloucester"))


def load(data_dir):
    return json.loads((data_dir / "meetings" / "meetings.json").read_text())


def events() -> list[dict]:
    return [e for page in pages() for e in meetingdocs.parse_listing(page, PAGE)]


def found() -> dict:
    return {m["id"]: m for m in meetingdocs.to_meetings(events(), SETTINGS)[0]}


# ---- Reading the listing -----------------------------------------------------

def test_listing_url():
    assert (meetingdocs.listing_url(SETTINGS, "2026-07-01")
            == f"{PAGE}&field_event_date_value%5Bmin%5D=2026-07-01")
    assert meetingdocs.listing_url(SETTINGS, "2026-07-01", 2).endswith("&page=2")


@pytest.mark.parametrize("cell, expected", [
    ('<time datetime="2026-10-19T23:00:00Z">Mon, 10/19/2026 - 7:00pm</time>', ("2026-10-19", "19:00")),
    ('<time datetime="2026-10-19T19:00:00Z">Mon, 10/19/2026 - 7:00pm</time>', ("2026-10-19", "19:00")),
    ("Wed, 06/10/2026 - 3:00pm", ("2026-06-10", "15:00")),
    ("Tue, 12/01/2026 - 12:15am", ("2026-12-01", "00:15")),
    ("Mon, 10/19/2026", ("2026-10-19", None)),
    ("no date", None),
])
def test_the_time_is_the_pages_text_not_its_machine_time(cell, expected):
    """An event's repeated rows give different machine times (23:00Z and 19:00Z for one 7:00 PM meeting)."""
    assert meetingdocs.when(cell) == expected


def test_each_event_once_with_its_documents_once():
    listed = events()
    assert [e["id"] for e in listed].count("2026-10-19-school-committee-regular-meeting") == 1
    regular = next(e for e in listed if e["id"] == "2026-10-19-school-committee-regular-meeting")
    assert regular["title"] == "School Committee Regular Meeting"
    assert regular["url"] == "https://www.somervillema.gov/events/2026/10/19/school-committee-regular-meeting"
    assert regular["date"] == "2026-10-19" and regular["start_time"] == "19:00"
    assert len(regular["documents"]) == len({d["url"] for d in regular["documents"]}) == 8
    agenda = next(d for d in regular["documents"] if d["file"] == "Regular SC Agenda 10-19-26_1.pdf")
    assert agenda["type"] == "Agenda" and agenda["posted"] == "2026-10-14T11:53"
    assert agenda["label"].startswith("Agenda for Regular School Committee meeting")


def test_the_agenda_is_the_newest_and_never_the_weeks_call():
    regular = found()["2026-10-19-school-committee-regular-meeting"]
    assert regular["agenda"]["file"] == "Amended Regular SC Agenda 10-19-26_1.pdf"
    assert regular["agenda"]["type"] == "Revised Agenda" and regular["agenda"]["posted"] == "2026-10-19T11:52"
    # A subcommittee whose only "agenda" is the week's call has none here.
    assert "agenda" not in found()["2026-10-19-school-committee-rules-management-subcommittee-meeting"]
    # Without not_agenda_pattern, the week's "Call/ Agenda" would be the subcommittee's agenda.
    plain = {k: v for k, v in SETTINGS.items() if k != "not_agenda_pattern"}
    rules = {m["id"]: m for m in meetingdocs.to_meetings(events(), plain)[0]}[
        "2026-10-19-school-committee-rules-management-subcommittee-meeting"]
    assert rules["agenda"]["file"].startswith("Call for the Week")


def test_minutes_a_word_agenda_and_the_recording():
    meeting = found()["2026-08-31-school-committee-regular-meeting"]
    assert meeting["agenda"]["file"] == "SC Agenda-for-083126.docx"
    assert meeting["minutes"]["file"].startswith("August 31,2026 Regular SC Minutes")
    assert meeting["video"] == {"video_id": "cfMSuzjnEfM"}


def test_boards_status_and_titles_with_none():
    meetings, unrecognized = meetingdocs.to_meetings(events(), SETTINGS)
    by_id = {m["id"]: m for m in meetings}
    assert by_id["2026-10-19-school-committee-rules-management-subcommittee-meeting"]["body"] == \
        "School Committee Rules Management Subcommittee"
    cancelled = by_id["2026-09-22-school-committee-educational-programs-and-instructional-subcommittee-cancelled"]
    assert cancelled["status"] == "cancelled"
    assert unrecognized == ["School Committee Student Assignment Advisory Committee"]
    assert not any("Student Assignment" in m["title"] for m in meetings)


@pytest.mark.parametrize("change, message", [
    ({"page_url": ""}, "needs page_url"),
    ({"since": "July"}, "since must be a date"),
    ({"bodies": {}}, "needs bodies, body, or both"),
    ({"not_agenda_pattern": "("}, "isn't a regular expression"),
])
def test_check(change, message):
    town = {"slug": "somerville", "meetingdocs_meetings": {**SETTINGS, **change}}
    with pytest.raises(SystemExit, match=message):
        meetingdocs.check(town)


# ---- Collecting ---------------------------------------------------------------

def test_run_records_meetings_and_saves_their_documents(config, tmp_path):
    client = FakeCity()
    status = fetch_meetingdocs_meetings.run(config, client, tmp_path, now=NOW)
    assert status["errors"] == [] and status["unrecognized"] == ["School Committee Student Assignment Advisory Committee"]
    store = load(tmp_path)
    regular = store["meetingdocs-2026-10-19-school-committee-regular-meeting"]
    assert regular["source"] == "meetingdocs" and regular["source_name"] == "City of Somerville"
    assert regular["body"] == "School Committee" and regular["start_time"] == "19:00"
    assert regular["posted_title"] == "School Committee Regular Meeting"
    assert regular["agendas"][0]["original_filename"] == "Amended Regular SC Agenda 10-19-26_1.pdf"
    assert regular["agendas"][0]["id"].startswith("meetingdocs-202610191152-")
    august = store["meetingdocs-2026-08-31-school-committee-regular-meeting"]
    assert august["agendas"][0]["converted_from"] == "docx" and august["agendas"][0]["has_text"] is True
    assert august["minutes"][0]["has_text"] is True and august["video_id"] == "cfMSuzjnEfM"
    assert (tmp_path / "meetings" / "minutes" / f"{august['minutes'][0]['id']}.pdf").exists()
    # A cancelled meeting's documents aren't saved.
    cancelled = store["meetingdocs-2026-09-22-school-committee-educational-programs-and-instructional-subcommittee-cancelled"]
    assert cancelled["status"] == "cancelled" and "agendas" not in cancelled
    # Both pages were read: the first has a next page and reaches back no further than `since`.
    assert sum("meetingdocs?" in u for u in client.urls) == 2
    assert status["documents_added"] == 3  # the two agendas and the August minutes


def test_a_second_run_downloads_nothing_new(config, tmp_path):
    fetch_meetingdocs_meetings.run(config, FakeCity(), tmp_path, now=NOW)
    again = FakeCity()
    status = fetch_meetingdocs_meetings.run(config, again, tmp_path, now=NOW)
    assert status["documents_added"] == status["documents_changed"] == 0
    assert not any("s3.amazonaws.com" in u for u in again.urls)


def test_a_newer_agenda_is_saved_as_a_new_version(config, tmp_path):
    fetch_meetingdocs_meetings.run(config, FakeCity(), tmp_path, now=NOW)
    newer = [page.replace("Amended Regular SC Agenda 10-19-26_1.pdf", "Amended Regular SC Agenda 10-19-26_2.pdf")
             .replace("Mon, 10/19/2026 - 11:52am", "Mon, 10/19/2026 - 4:30pm") for page in pages()]
    status = fetch_meetingdocs_meetings.run(config, FakeCity(newer), tmp_path, now=NOW)
    assert status["documents_changed"] == 1
    regular = load(tmp_path)["meetingdocs-2026-10-19-school-committee-regular-meeting"]
    assert [a["original_filename"] for a in regular["agendas"]][-1] == "Amended Regular SC Agenda 10-19-26_2.pdf"
    assert regular["history"][-1]["field"] == "agenda"


def test_an_upcoming_meeting_that_leaves_the_listing(config, tmp_path):
    fetch_meetingdocs_meetings.run(config, FakeCity(), tmp_path, now=NOW)
    first, second = pages()
    gone = first.replace("/events/2026/12/01/school-committee-school-building-facilities-and-maintenance-subcommittee",
                         "/elsewhere")
    fetch_meetingdocs_meetings.run(config, FakeCity([gone, second]), tmp_path, now=NOW)
    meeting = load(tmp_path)["meetingdocs-2026-12-01-school-committee-school-building-facilities-and-maintenance-subcommittee"]
    assert meeting["listed"] is False


def test_minutes_are_awaited_on_the_city_calendar(config, tmp_path):
    fetch_meetingdocs_meetings.run(config, FakeCity(), tmp_path, now=NOW)
    meeting = load(tmp_path)["meetingdocs-2026-10-19-school-committee-regular-meeting"]
    gap = absences.minutes_gap(config, meeting, NOW.date())
    assert gap["reason"] == "not_posted" and gap["source"] == "meetingdocs"
    assert gap["url"] == "https://www.somervillema.gov/events/2026/10/19/school-committee-regular-meeting"


# ---- Site ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def somerville_site(tmp_path_factory):
    town = somerville(load_config("gloucester"))
    town["sections"] = [s for s in town["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "storage"):
        town.pop(table, None)
    data_dir = tmp_path_factory.mktemp("somerville-data")
    fetch_meetingdocs_meetings.run(town, FakeCity(), data_dir, now=NOW)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: town)
        out = tmp_path_factory.mktemp("somerville") / "site"
        build_site.build("somerville", out, data_dir=data_dir, now=NOW)
    return out


def test_site_says_where_the_meetings_come_from(somerville_site):
    about = (somerville_site / "about" / "index.html").read_text()
    assert "School Committee meetings, agendas, and minutes:" in about and "events calendar" in about
    page = (somerville_site / "meetings" / "2026-10-19-school-committee" / "index.html").read_text()
    assert "This meeting on the city&#39;s events calendar" in page or "This meeting on the city's events calendar" in page
    assert "Minutes haven&#39;t been posted on the" in page or "Minutes haven't been posted on the" in page
    august = (somerville_site / "meetings" / "2026-08-31-school-committee" / "index.html").read_text()
    assert "cfMSuzjnEfM" in august
