"""A board's minutes from a document library on a school district's SharpSchool website, as
Springfield Public Schools posts its School Committee's. Run offline against the library's page,
trimmed to its script, and saved answers of its content service (tests/fixtures/sharpschool_*)."""

import copy
import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeJSONResponse, FakeResponse

from pipeline import absences, build_site, fetch_sharpschool_meetings, identity, sharpschool
from pipeline.config import load_config
from pipeline.http import FetchError

PAGE = "https://www.springfieldpublicschools.com/school_committee/school_committee_meeting-_minutes_archive"
SERVICE = "https://www.springfieldpublicschools.com/portal/svc/ContentItemSvc.asmx/GetItemList"
SETTINGS = {
    "page_url": PAGE,
    "source_name": "Springfield Public Schools",
    "since": "2025-12-01",
    "body": "School Committee",
}
NOW = datetime(2026, 10, 27, 7, 0, tzinfo=TZ)
# The library's folders: its top one, and its years.
FOLDERS = {"1099587": "root", "228103892": "2026", "207624674": "2025"}


def answer(name: str) -> dict:
    return json.loads((FIXTURES / f"sharpschool_{name}.json").read_text())


class FakeLibrary:
    """Serves the library's page, its folders' items (none for a folder not saved), and each file as
    a distinct PDF."""

    def __init__(self, folders: dict | None = None):
        self.folders = {k: answer(v) for k, v in FOLDERS.items()} if folders is None else folders
        self.pdf = (FIXTURES / "legistar_minutes.pdf").read_bytes()
        self.urls, self.posted = [], []
        self.request_count = 0

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        if url == PAGE:
            return FakeResponse((FIXTURES / "sharpschool_page.html").read_bytes())
        if "/common/pages/GetFile.ashx?key=" in url:
            return FakeResponse(self.pdf + f"\n% {url}\n".encode())
        raise AssertionError(f"unexpected URL {url}")

    def post_json(self, url, body):
        assert url == SERVICE and json.loads(body["Params"])["InstanceId"] == "124670"
        self.posted.append(body["parentId"])
        self.request_count += 1
        return FakeJSONResponse(copy.deepcopy(self.folders.get(body["parentId"], {"d": {"DataObject": []}})))


def springfield(config: dict) -> dict:
    town = copy.deepcopy(config)
    for table in ("archive", "drive_meetings", "summaries", "participation", "glossary"):
        town.pop(table, None)
    town["town"].update(name="Springfield")
    town["meetings"] = {"governing_body": "City Council", "boards": ["City Council", "School Committee"],
                        "calendar_url": "https://springfieldma.portal.civicclerk.com",
                        "civicclerk": {"api_url": "https://springfieldma.api.civicclerk.com/v1",
                                       "portal_url": "https://springfieldma.portal.civicclerk.com", "since": "2026-01-01"}}
    town["sharpschool_meetings"] = copy.deepcopy(SETTINGS)
    return town


@pytest.fixture
def config():
    return springfield(load_config("gloucester"))


def load(data_dir):
    return json.loads((data_dir / "meetings" / "meetings.json").read_text())


def items(name: str) -> list[dict]:
    return sharpschool.items(answer(name))


# ---- Reading the library -------------------------------------------------------

def test_the_library_from_its_page():
    folder, settings = sharpschool.library((FIXTURES / "sharpschool_page.html").read_text())
    assert folder == "1099587" and json.loads(settings)["RawUrl"] == "/school_committee/school_committee_meeting-_minutes_archive"
    assert sharpschool.library("<html>No library here</html>") is None
    assert sharpschool.service_url(PAGE) == SERVICE


def test_folders_of_years_from_since():
    names = [f["Name"] for f in sharpschool.year_folders(items("root"), "2025-12-01")]
    assert names == ["2026 Meeting Minutes", "2025 Meeting Minutes"]
    assert sharpschool.year_folders([{"Type": "content_folder", "Name": "2025-2026 School Year"}], "2026-07-01")


@pytest.mark.parametrize("name, expected", [
    ("09.10.26 Minutes Regular Meeting", ("2026-09-10", "Regular Meeting", None, False)),
    ("02.26.26 Minutes Chapter 74 Meeting", ("2026-02-26", "Chapter 74 Meeting", None, False)),
    ("03.30.26 Minutes Special Meeting 515", ("2026-03-30", "Special Meeting", "17:15", True)),
    ("05.07.26 Minutes Special Meeting 500", ("2026-05-07", "Special Meeting", "17:00", True)),
    ("06.24.26 Minutes Special Meeting 8:15 AM", ("2026-06-24", "Special Meeting", "08:15", True)),
    ("12.15.25 Minutes Special Meeting (2)", ("2025-12-15", "Special Meeting (2)", None, True)),
    ("1.5.2026 Minutes", ("2026-01-05", "Meeting", None, False)),
])
def test_a_files_meeting_from_its_name(name, expected):
    found = sharpschool.parse_name(name)
    assert (found["date"], found["kind"], found["start_time"], found["special"]) == expected


@pytest.mark.parametrize("name", ["Public Speak Out Guidelines", "09.10.26 Agenda Regular Meeting", "13.45.26 Minutes"])
def test_a_name_without_a_date_or_minutes_is_no_meetings(name):
    assert sharpschool.parse_name(name) is None


def test_files_of_minutes_and_the_rest():
    found, unrecognized = sharpschool.files(
        items("2026") + [{"Type": "content_item", "Name": "Public Speak Out Guidelines", "DownloadLink": "https://x/y"}],
        SETTINGS, "America/New_York")
    assert unrecognized == ["Public Speak Out Guidelines"]
    regular = next(f for f in found if f["name"] == "09.10.26 Minutes Regular Meeting")
    assert regular["url"] == "https://www.springfieldpublicschools.com/common/pages/GetFile.ashx?key=9UzKDsC9"
    # Last changed 2026-10-09 20:22 UTC, in Springfield's time.
    assert regular["changed"] == "2026-10-09T16:22" and regular["extension"] == "pdf"
    assert sharpschool.files(items("2026"), {**SETTINGS, "exclude_pattern": "special"}, "America/New_York")[0][0]["kind"] != "Special Meeting"


@pytest.mark.parametrize("change, message", [
    ({"page_url": ""}, "needs page_url"),
    ({"body": ""}, "needs body"),
    ({"page_url": "springfieldpublicschools.com"}, "page_url must be"),
    ({"since": "January"}, "since must be a date"),
    ({"exclude_pattern": "("}, "isn't a regular expression"),
])
def test_check(change, message):
    with pytest.raises(SystemExit, match=message):
        sharpschool.check({"slug": "springfield", "sharpschool_meetings": {**SETTINGS, **change}})


# ---- Collecting ---------------------------------------------------------------

def test_run_records_meetings_and_saves_their_minutes(config, tmp_path):
    client = FakeLibrary()
    status = fetch_sharpschool_meetings.run(config, client, tmp_path, now=NOW)
    assert status["errors"] == [] and status["unrecognized"] == []
    # The top folder, then 2026 and 2025; not 2024, before `since`.
    assert client.posted == ["1099587", "228103892", "207624674"]
    store = load(tmp_path)
    # From `since` on: December 15 has two special meetings, the second's file named "(2)".
    assert {m["date"] for m in store.values()} == {"2025-12-15", "2025-12-18", "2026-01-05", "2026-03-12", "2026-03-30", "2026-09-10"}
    early = store["sharpschool-2026-03-30-special-meeting-17-15"]
    assert early["source"] == "sharpschool" and early["source_name"] == "Springfield Public Schools"
    assert early["body"] == "School Committee" and early["start_time"] == "17:15" and early["special"] is True
    assert early["posted_title"] == "Special Meeting" and early["title"] == "School Committee Special Meeting"
    minutes = early["minutes"][0]
    assert minutes["original_filename"] == "03.30.26 Minutes Special Meeting 515.pdf" and minutes["has_text"] is True
    assert minutes["posted_on"] == "the Springfield Public Schools website"
    assert minutes["id"].startswith("sharpschool-202605261619-")
    assert (tmp_path / "meetings" / "minutes" / minutes["file"]).exists()
    assert status["documents_added"] == len(store) == 10


def test_a_second_run_downloads_nothing_new(config, tmp_path):
    fetch_sharpschool_meetings.run(config, FakeLibrary(), tmp_path, now=NOW)
    again = FakeLibrary()
    status = fetch_sharpschool_meetings.run(config, again, tmp_path, now=NOW)
    assert status["documents_added"] == status["documents_changed"] == status["meetings_created"] == 0
    assert not any("GetFile" in u for u in again.urls)


def test_a_file_changed_in_the_library_is_saved_as_a_new_version(config, tmp_path):
    fetch_sharpschool_meetings.run(config, FakeLibrary(), tmp_path, now=NOW)
    folders = {k: answer(v) for k, v in FOLDERS.items()}
    item = next(i for i in folders["228103892"]["d"]["DataObject"] if i["Name"] == "09.10.26 Minutes Regular Meeting")
    item.update(ModifiedDate="/Date(1793030400000)/", DownloadLink=item["DownloadLink"] + "x")
    status = fetch_sharpschool_meetings.run(config, FakeLibrary(folders), tmp_path, now=NOW)
    assert status["documents_changed"] == 1
    regular = load(tmp_path)["sharpschool-2026-09-10-regular-meeting"]
    assert len(regular["minutes"]) == 2 and regular["history"][-1]["field"] == "minutes"


def test_a_page_without_its_library_fails_the_run(config, tmp_path):
    client = FakeLibrary()
    client.get = lambda url: FakeResponse(b"<html>Moved</html>")
    with pytest.raises(FetchError, match="no document library"):
        fetch_sharpschool_meetings.run(config, client, tmp_path, now=NOW)


def test_minutes_are_awaited_on_the_districts_website(config, tmp_path):
    fetch_sharpschool_meetings.run(config, FakeLibrary(), tmp_path, now=NOW)
    regular = load(tmp_path)["sharpschool-2026-09-10-regular-meeting"]
    # Shown with the portal's listing of the meeting, it awaits minutes in the district's library, not on the portal.
    meeting = {"id": "civicclerk-1", "source": "civicclerk", "date": "2026-09-10", "body": "School Committee",
               "status": "scheduled", "listings": [regular]}
    assert absences.minutes_source(config, meeting) == ("sharpschool", "2025-12-01")


def test_each_file_joins_the_citys_listing_of_its_meeting(config, tmp_path):
    fetch_sharpschool_meetings.run(config, FakeLibrary(), tmp_path, now=NOW)
    store = load(tmp_path)

    def portal(id, day, time, title, special=False):
        return {"id": f"civicclerk-{id}", "slug": f"{day}-school-committee", "date": day, "body": "School Committee",
                "start_time": time, "first_seen": "2026-01-01T07:00:00", "title": title, "posted_title": title,
                "status": "scheduled", "source": "civicclerk", "special": special}
    store.update({m["id"]: m for m in (
        portal(1, "2026-03-30", "17:15", "Springfield School Committee - Special Meeting", True),
        portal(2, "2026-03-30", "17:30", "Springfield School Committee - Special Meeting", True),
        portal(3, "2026-03-12", "18:00", "Springfield School Committee - Public Speak Out & Regular Meeting"),
        portal(4, "2026-03-12", "17:15", "Springfield School Committee - Special Meeting", True),
    )})
    ledger = identity.assign(store, {"meetings": {}}, "Springfield")
    together = {frozenset(e["listings"]) for e in ledger["meetings"].values()}
    # By time on a day of two special meetings; by kind, without a time, on a day of a regular and a special.
    assert {
        frozenset({"civicclerk-1", "sharpschool-2026-03-30-special-meeting-17-15"}),
        frozenset({"civicclerk-2", "sharpschool-2026-03-30-special-meeting-17-30"}),
        frozenset({"civicclerk-3", "sharpschool-2026-03-12-regular-meeting"}),
        frozenset({"civicclerk-4", "sharpschool-2026-03-12-special-meeting"}),
    } <= together


# ---- Site ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def springfield_site(tmp_path_factory):
    town = springfield(load_config("gloucester"))
    town["sections"] = [s for s in town["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "storage"):
        town.pop(table, None)
    data_dir = tmp_path_factory.mktemp("springfield-data")
    fetch_sharpschool_meetings.run(town, FakeLibrary(), data_dir, now=NOW)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: town)
        out = tmp_path_factory.mktemp("springfield") / "site"
        build_site.build("springfield", out, data_dir=data_dir, now=NOW)
    return out


def test_site_says_where_the_minutes_come_from(springfield_site):
    about = (springfield_site / "about" / "index.html").read_text()
    assert "School Committee minutes:</strong> Springfield Public Schools, from the library of minutes" in about
    page = (springfield_site / "meetings" / "2026-09-10-school-committee" / "index.html").read_text()
    assert "Springfield Public Schools website" in page and "GetFile.ashx?key=9UzKDsC9" in page
    assert "Minutes posted on the Springfield Public Schools website" in page
