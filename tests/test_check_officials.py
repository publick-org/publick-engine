"""Each body's members looked for on its own page (pipeline/check_officials.py), and the network's
issue of what to look at (pipeline/network.py officials). Offline: pages are made up here."""

import copy
import json
from datetime import datetime, timedelta

import pytest
from conftest import TZ
from fakes import FakeResponse

from pipeline import check_officials, network
from pipeline.http import FetchError

NOW = datetime(2026, 11, 9, 7, 0, tzinfo=TZ)
COUNCIL = "https://city.example/council"
SCHOOL = "https://schools.example/committee"


def town(**school):
    return {"slug": "example", "site": {"user_agent": "test", "timezone": "America/New_York"},
            "officials": {"checked": "2026-11-01", "next_election": "2027-11-02", "bodies": [
                {"name": "Mayor", "url": COUNCIL, "members": [{"name": "Morgan Example", "seat": "Mayor"}]},
                {"name": "City Council", "url": COUNCIL, "members": [
                    {"name": "Jacqueline \"Nikki\" O'Toole", "seat": "Ward 1"},
                    {"name": "David P. “D.J.” Beauregard, Jr.", "seat": "Ward 2"},
                    {"name": "Bryce Kaw-uh", "seat": "Ward 3"},
                    {"name": "José Núñez", "seat": "At-large"}]},
                {"name": "School Committee", "url": SCHOOL, "members": [
                    {"name": "Riley Example", "seat": "At-large"}, {"name": "Casey Sample", "seat": "At-large"}],
                 **school},
                {"name": "Board of Assessors", "members": [{"name": "Not Checked", "seat": "Appointed"}]}]}}


class Pages:
    def __init__(self, pages: dict):
        self.pages = pages
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return FakeResponse(page.encode())


COUNCIL_PAGE = """<html><head><script>var names = ["Somebody Else"];</script></head><body>
<h1>City Council</h1><p>Mayor Morgan Example</p><ul><li>Nikki O&rsquo;Toole, Ward 1</li>
<li>D.J. Beauregard, Ward 2</li><li>Bryce Kaw-uh, Ward 3</li><li>Jose Nunez, At-large</li></ul></body></html>"""


def no_browser(url, user_agent):
    return None


@pytest.mark.parametrize("name, expected", [
    ("Jacqueline \"Nikki\" O'Toole", "o'toole"),
    ("David P. “D.J.” Beauregard, Jr.", "beauregard"),
    ("Laura Sanchez-Parkinson", "sanchez-parkinson"),
    ("Domenic J. Sarno", "sarno"),
    ("Edward Sapienza III", "sapienza"),
])
def test_a_member_is_looked_for_by_surname(name, expected):
    assert check_officials.surname(name) == expected


def test_a_surname_counts_only_as_a_word_of_its_own():
    text = check_officials.page_text("<p>Councilor Boyerson and Mr. Dexter-Smith</p>")
    assert not check_officials.named("Dan Boyer", text) and not check_officials.named("Dana Dexter", text)
    assert check_officials.named("Dana Dexter-Smith", text)


def test_every_member_named_is_no_lead(tmp_path):
    client = Pages({COUNCIL: COUNCIL_PAGE, SCHOOL: "<p>Riley Example (chair) and Casey Sample</p>"})
    result = check_officials.run(town(), client, tmp_path, NOW, browser=no_browser)
    assert [b["name"] for b in result["bodies"]] == ["Mayor", "City Council", "School Committee"]
    assert check_officials.leads(result) == []
    # The council's page, shared with the mayor, is read once; a body without a url isn't checked.
    assert client.urls == [COUNCIL, SCHOOL]
    saved = json.loads((tmp_path / check_officials.RESULT_FILE).read_text())
    assert saved["checked_at"] == "2026-11-09T07:00:00-05:00" and saved["bodies"][1]["missing"] == []


def test_a_member_the_page_doesnt_name_a_page_that_names_none_and_one_that_cant_be_read(tmp_path):
    client = Pages({COUNCIL: COUNCIL_PAGE.replace("Bryce Kaw-uh", "Avery Newcomer"),
                    SCHOOL: "<p>Our school committee meets monthly.</p>"})
    result = check_officials.run(town(), client, tmp_path, NOW, browser=no_browser)
    assert check_officials.leads(result) == [
        {"name": "City Council", "url": COUNCIL, "missing": ["Bryce Kaw-uh"]},
        {"name": "School Committee", "url": SCHOOL, "none": True}]
    client = Pages({COUNCIL: FetchError(f"{COUNCIL}: HTTP 403", 403), SCHOOL: "<p>Riley Example, Casey Sample</p>"})
    result = check_officials.run(town(), client, tmp_path, NOW, force=True, browser=no_browser)
    assert check_officials.leads(result) == [
        {"name": "Mayor", "url": COUNCIL, "error": f"{COUNCIL}: HTTP 403"},
        {"name": "City Council", "url": COUNCIL, "error": f"{COUNCIL}: HTTP 403"}]


def test_a_page_whose_names_come_from_its_scripts_is_read_in_a_browser(tmp_path):
    client = Pages({COUNCIL: COUNCIL_PAGE, SCHOOL: '<div id="app"></div><script src="members.js"></script>'})
    asked = []

    def browser(url, user_agent):
        asked.append(url)
        return check_officials.plain("School Committee: Riley Example, Casey Sample")

    result = check_officials.run(town(), client, tmp_path, NOW, browser=browser)
    assert asked == [SCHOOL] and check_officials.leads(result) == []
    assert [b["read"] for b in result["bodies"]] == ["page", "page", "browser"]


def test_incoming_members_are_looked_for_once_they_take_office(tmp_path):
    incoming = {"incoming": {"takes_office": "2026-11-09", "members": [
        {"name": "Riley Example", "seat": "At-large"}, {"name": "Harper Newcomer", "seat": "At-large"}]}}
    client = Pages({COUNCIL: COUNCIL_PAGE, SCHOOL: "<p>Riley Example, Casey Sample</p>"})
    result = check_officials.run(town(**incoming), client, tmp_path, NOW - timedelta(days=1), browser=no_browser)
    assert check_officials.leads(result) == []
    result = check_officials.run(town(**incoming), client, tmp_path, NOW, browser=no_browser)
    assert check_officials.leads(result) == [{"name": "School Committee", "url": SCHOOL, "missing": ["Harper Newcomer"]}]


def test_checked_weekly_and_again_once_the_list_or_a_page_changes(tmp_path):
    client = Pages({COUNCIL: COUNCIL_PAGE, SCHOOL: "<p>Riley Example</p>"})
    config = town()
    check_officials.run(config, client, tmp_path, NOW, browser=no_browser)
    assert "skipped" in check_officials.run(config, client, tmp_path, NOW + timedelta(days=6), browser=no_browser)
    assert "skipped" not in check_officials.run(config, client, tmp_path, NOW + timedelta(days=7), browser=no_browser)
    # A person records the change: the list is checked on the next run, and the lead goes.
    fixed = copy.deepcopy(config)
    fixed["officials"]["bodies"][2]["members"].pop()
    result = check_officials.run(fixed, client, tmp_path, NOW + timedelta(days=8), browser=no_browser)
    assert "skipped" not in result and check_officials.leads(result) == []
    moved = copy.deepcopy(fixed)
    moved["officials"]["bodies"][2]["url"] = "https://schools.example/members"
    client.pages["https://schools.example/members"] = "<p>Riley Example</p>"
    assert "skipped" not in check_officials.run(moved, client, tmp_path, NOW + timedelta(days=9), browser=no_browser)


def record(root, folder, officials=None, finished="2026-11-09T12:00:00+00:00"):
    data = root / "towns" / folder / "data"
    data.mkdir(parents=True)
    (data.parent / "config").mkdir()
    (data.parent / "config" / f"{folder.split('-')[0]}.toml").write_text("")
    run = {"town": folder.split("-")[0], "folder": folder, "finished_at": finished}
    if officials is not None:
        run["officials"] = officials
    (data / network.RUN_RECORD).write_text(json.dumps(run))


def test_the_network_lists_every_towns_leads_in_one_issue(tmp_path):
    record(tmp_path, "beverly-ma", {"checked_at": "2026-11-09T07:00:00-05:00", "leads": [
        {"name": "School Committee", "url": SCHOOL, "none": True}]})
    record(tmp_path, "manchester-nh", {"checked_at": "2026-11-08T07:00:00-05:00", "leads": [
        {"name": "Board of Mayor and Aldermen", "url": COUNCIL, "missing": ["Bryce Kaw-uh", "Dan Goonan"]},
        {"name": "Board of School Committee", "url": SCHOOL, "missing": [f"Member {i}" for i in range(1, 8)]}]})
    record(tmp_path, "central-falls-ri", {"checked_at": "2026-11-09T07:00:00-05:00", "leads": [
        {"name": "City Council", "url": "https://cf.example", "error": "https://cf.example: HTTP 403"}]})
    record(tmp_path, "gloucester-ma", {"checked_at": "2026-11-09T07:00:00-05:00", "leads": []})
    record(tmp_path, "salem-ma")
    text = network.officials_text(tmp_path)
    assert text.startswith("3 of the network's towns have officials to look at")
    assert f"| manchester-nh | Board of Mayor and Aldermen | Bryce Kaw-uh, Dan Goonan | [page]({COUNCIL}) | 2026-11-08 |" in text
    assert "| Member 1, Member 2, Member 3, Member 4, Member 5, and 2 more |" in text
    assert f"| beverly-ma | School Committee | (names none of its members) | [page]({SCHOOL}) | 2026-11-09 |" in text
    assert "| central-falls-ri | City Council | (couldn't be read: https://cf.example: HTTP 403) |" in text
    assert "Not checked yet: 1 towns: salem-ma." in text
    # Nothing to look at: no text, and the workflow closes the issue.
    for name in ("beverly-ma", "manchester-nh", "central-falls-ri"):
        (tmp_path / "towns" / name / "data" / network.RUN_RECORD).unlink()
    assert network.officials_text(tmp_path) == ""


def test_a_towns_run_record_keeps_its_officials_leads(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    assert network.officials_leads(data) is None
    (data / check_officials.RESULT_FILE).write_text(json.dumps({"checked_at": "2026-11-09T07:00:00-05:00", "bodies": [
        {"name": "School Committee", "url": SCHOOL, "members": ["Riley Example"], "read": "page", "missing": []},
        {"name": "City Council", "url": COUNCIL, "members": ["A B", "C D"], "read": "page", "missing": ["C D"]}]}))
    assert network.officials_leads(data) == {"checked_at": "2026-11-09T07:00:00-05:00",
                                             "leads": [{"name": "City Council", "url": COUNCIL, "missing": ["C D"]}]}
