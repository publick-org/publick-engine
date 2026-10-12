"""A board's meetings from a page that links each meeting's documents: Methuen Public Schools'
table (Finalsite) and Waltham Public Schools' list (ParentSquare SmartSites), saved and
trimmed: tests/fixtures/linkpage_*.html."""

import copy
import json
import shutil
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeLinkPage

from pipeline import build_site, fetch_page_meetings, linkpage
from pipeline.fetch_meetings import load_store

NOW = datetime(2026, 10, 12, 7, 0, tzinfo=TZ)
DRIVE = "https://drive.google.com/file/d/{}/view?usp=sharing"


def methuen() -> dict:
    """[page_meetings] as Methuen's config has it."""
    return {"page_url": FakeLinkPage.METHUEN, "source_name": "Methuen Public Schools", "since": "2026-01-01",
            "body": "School Committee",
            "bodies": {"Policy Subcommittee": "School Committee Policy Subcommittee",
                       "Finance Subcommittee": "School Committee Finance Subcommittee",
                       "Joint Meeting - City Council": "Joint City Council and School Committee"}}


def waltham() -> dict:
    """[page_meetings] for Waltham's page: the Spanish agendas left out, and the agendas it names
    after their meetings ("June 10, 2026 Special Meeting") taken as agendas."""
    return {"page_url": FakeLinkPage.WALTHAM, "source_name": "Waltham Public Schools", "since": "2026-01-01",
            "body": "School Committee", "exclude_pattern": "Español",
            "agenda_pattern": r"\bagenda\b|special meeting|workshop|public hearing"}


def parsed(settings: dict) -> tuple[dict, list]:
    page = (FIXTURES / ("linkpage_methuen.html" if "methuen" in settings["page_url"] else "linkpage_waltham.html"))
    found, unrecognized = linkpage.meetings(page.read_text(encoding="utf-8"), settings["page_url"], settings)
    return {m["key"]: m for m in found}, unrecognized


@pytest.fixture
def district(config):
    town = copy.deepcopy(config)
    town["page_meetings"] = methuen()
    return town


def run(town, tmp_path, client=None, now=NOW):
    client = client or FakeLinkPage()
    status = fetch_page_meetings.run(town, client, tmp_path, now=now)
    return status, load_store(tmp_path), client


# ---- The page -------------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("2026-10-13", "2026-10-13"), ("2026-08-17*", "2026-08-17"), ("September 16, 2026 Minutes", "2026-09-16"),
    ("June 10 2026 Special Meeting Minutes", "2026-06-10"), ("Sept. 2, 2026 Agenda", "2026-09-02"),
])
def test_dates(value, expected):
    assert linkpage.date_value(linkpage.find_date(value)) == expected


def test_no_date():
    assert linkpage.find_date("October Additional Resources") is None
    assert linkpage.date_value(linkpage.find_date("2026-02-30")) is None


@pytest.mark.parametrize("value, expected", [
    ("Business Meeting 6:00 PM", "18:00"), ("Workshop, 6 p.m.", "18:00"), ("Policy Subcommittee 9:30am", "09:30"),
    ("Business Meeting", None),
])
def test_times(value, expected):
    assert linkpage.time_of(value) == expected


def test_a_table_row_a_meeting():
    found, unrecognized = parsed(methuen())
    assert list(found) == [
        "2026-10-13-business-meeting", "2026-10-02-policy-subcommittee", "2026-09-28-workshop-business-session",
        "2026-09-28-executive-session", "2026-09-25-policy-subcommittee", "2026-09-22-finance-subcommittee",
        "2026-08-17-school-committee-special-meeting", "2026-02-25-workshop-business-session",
        "2026-02-25-special-meeting", "2026-02-23-workshop-business-session", "2026-02-23-special-meeting",
        "2026-01-21-joint-meeting-city-council", "2026-01-12-regular-meeting", "2025-12-11-regular-meeting",
        "2025-12-11-special-meeting"]
    # The name, without the empty cells that repeat their column's heading ("Packet", "Minutes").
    upcoming = found["2026-10-13-business-meeting"]
    assert (upcoming["name"], upcoming["body"], upcoming["status"], upcoming["special"]) == (
        "Business Meeting", "School Committee", "scheduled", False)
    assert upcoming["agenda_url"] == DRIVE.format("1TYN0ZAMOqQXosXkcS60BwkoNu1gI5fb1")
    assert upcoming["documents_url"] == upcoming["agenda_url"] and "minutes_url" not in upcoming
    assert upcoming["links"] == [{"url": DRIVE.format("1lcXjEMBEhKp3WUf7GujcMeH3u1WXdE1h").replace("sharing", "drive_link"),
                                  "text": "Posting"}]
    # A subcommittee by its name; its recording on Drive is a link, not a document.
    policy = found["2026-09-25-policy-subcommittee"]
    assert policy["body"] == "School Committee Policy Subcommittee"
    assert policy["minutes_url"] == DRIVE.format("1kvCaa9USinBVyE6k1jm0pja-wOHe26DL")
    assert [link["text"] for link in policy["links"]] == ["Posting", "Video"] and "video_id" not in policy
    assert found["2026-01-21-joint-meeting-city-council"]["body"] == "Joint City Council and School Committee"
    # A YouTube recording is the meeting's.
    assert found["2026-09-28-workshop-business-session"]["video_id"] == "9f7QLsRGf1k"
    # A row without a date is reported, not guessed at.
    assert unrecognized == ["2015- Executive Sessions Posting Agenda Minutes"]


def test_a_rows_status():
    found, _ = parsed(methuen())
    assert found["2026-09-22-finance-subcommittee"]["status"] == "cancelled"
    assert found["2026-02-23-special-meeting"]["status"] == "cancelled"
    # "Rescheduled from 8/10": this is the meeting's new date.
    moved = found["2026-08-17-school-committee-special-meeting"]
    assert (moved["status"], moved["special"], moved["video_id"]) == ("scheduled", True, "Fko-RbI6xmA")


def test_two_rows_for_one_meeting():
    """An amended posting gets a row above the original's: one meeting, the amended agenda its own."""
    regular = parsed(methuen())[0]["2025-12-11-regular-meeting"]
    assert regular["agenda_url"] == DRIVE.format("1YeYuG1I5M49_3POVuuBZQ4rBO0tSFkFc")
    assert regular["minutes_url"] == DRIVE.format("1wapUQp2jEQq6o97JD3heFXhGxpl12OgI")
    assert regular["video_id"] == "Ckq5HNaqTY0"
    assert {"url": DRIVE.format("19EgxwGKGvcmPjFha1zw0rWjOtbhSZGNm"), "text": "Agenda"} in regular["links"]


def test_a_list_of_links_by_meeting():
    found, unrecognized = parsed(waltham())
    assert not unrecognized
    assert list(found)[:4] == ["2026-10-07-meeting", "2026-09-30-meeting", "2026-09-16-meeting", "2026-09-02-meeting"]
    sept = found["2026-09-16-meeting"]
    assert (sept["body"], sept["name"], sept["special"]) == ("School Committee", "", False)
    assert sept["agenda_url"] == DRIVE.format("1Q5nFidhE77sYy-Q3oWZBap_ruZ5UMajw")
    assert sept["minutes_url"] == DRIVE.format("1o7mQRV_hBKLpVmUNVm_DXozMKShJlLAg")
    # The Spanish agenda is left out, and the month's folder of resources names no meeting.
    assert sept["links"] == []
    # A special meeting's agenda is named after it; its minutes, written another way, are the same meeting's.
    special = found["2026-06-10-special-meeting"]
    assert special["special"] and special["agenda_url"] == DRIVE.format("1UfyBINVE8fFy-9CkmjBLgIzJ-fDVuEGF")
    assert special["minutes_url"] == DRIVE.format("1vJGZfIWq3g7dH9jJNlXV8Xlx_xJ2pNn4")
    # A public hearing the same day as a meeting is a meeting of its own.
    hearing = found["2026-05-06-public-hearing-northeast-elementary-school"]
    assert hearing["agenda_url"] == DRIVE.format("1ZdzqB9FaMCOcYUeI-odwapymDHre2hy2")
    assert "minutes_url" in found["2026-05-06-meeting"]
    # Two agendas for one day: the first listed (the newest) is the agenda, the other a link.
    january = found["2026-01-07-meeting"]
    assert january["agenda_url"] == DRIVE.format("1vHWrdQ9WzAhylyc7bOcA9ZGnn7ZObLWV")
    assert january["links"] == [{"url": DRIVE.format("17qE5J4Zaf0Lx39J-sYceHnz6CYvbWFlg"),
                                 "text": "January 7, 2026 Agenda - 1"}]


def test_the_default_kinds_need_the_words():
    """Without its agenda_pattern, Waltham's special meeting has its minutes and no agenda."""
    settings = {k: v for k, v in waltham().items() if k != "agenda_pattern"}
    special = parsed(settings)[0]["2026-06-10-special-meeting"]
    assert "agenda_url" not in special and special["minutes_url"]


# ---- Collecting -------------------------------------------------------------------------------

def test_first_run_records_meetings_and_saves_documents(district, tmp_path):
    status, store, client = run(district, tmp_path)
    page = [m for m in store.values() if m.get("source") == "page"]
    # Meetings from `since` on: 2025's aren't recorded, and none of their documents is fetched.
    assert len(page) == 13 and min(m["date"] for m in page) == "2026-01-12"
    assert status["meetings_created"] == 13 and not status["errors"]
    meeting = store["page-2026-09-25-policy-subcommittee"]
    assert (meeting["body"], meeting["title"], meeting["posted_title"]) == (
        "School Committee Policy Subcommittee", "School Committee Policy Subcommittee Meeting", "Policy Subcommittee")
    assert (meeting["source_url"], meeting["source_name"], meeting["listed"]) == (
        FakeLinkPage.METHUEN, "Methuen Public Schools", True)
    agenda, minutes = meeting["agendas"][0], meeting["minutes"][0]
    assert agenda["posted_on"] == "Google Drive" and agenda["posted_by"] == "Methuen Public Schools"
    assert minutes["source_url"] == DRIVE.format("1kvCaa9USinBVyE6k1jm0pja-wOHe26DL") and minutes["has_text"]
    assert (tmp_path / "meetings" / "minutes" / minutes["file"]).exists()
    # A cancelled meeting's agenda is linked, not saved.
    assert not store["page-2026-09-22-finance-subcommittee"].get("agendas")
    assert store["page-2026-09-22-finance-subcommittee"]["status"] == "cancelled"
    assert store["page-2026-01-12-regular-meeting"]["video_id"] == "w0LHPKmJbkk"
    assert not any("2025" in u or "uc?export=download&id=1YeYuG1I5M49" in u for u in client.urls)
    assert status["documents_added"] == 14
    assert json.loads((tmp_path / "meetings" / "page_status.json").read_text())["unrecognized"] == [
        "2015- Executive Sessions Posting Agenda Minutes"]


def test_later_runs_fetch_only_whats_new(district, tmp_path):
    run(district, tmp_path)
    status, store, client = run(district, tmp_path)
    assert client.urls == [FakeLinkPage.METHUEN]
    assert status["documents_added"] == 0 and status["meetings_created"] == 0
    assert status["new_unrecognized"] == []


def test_documents_wait_for_later_runs(district, tmp_path):
    district["page_meetings"]["max_documents_per_run"] = 4
    status, store, _ = run(district, tmp_path)
    # The newest meetings first.
    assert status["documents_added"] == 4 and status["documents_waiting"] == 10
    assert store["page-2026-10-13-business-meeting"].get("agendas") and not store["page-2026-01-12-regular-meeting"].get("agendas")
    # A meeting's agenda and minutes are saved together, so a run can go one over.
    status, _, _ = run(district, tmp_path)
    assert status["documents_added"] == 5 and status["documents_waiting"] == 5


def test_a_document_that_isnt_a_pdf(district, tmp_path):
    status, store, _ = run(district, tmp_path, FakeLinkPage(missing=("1TYN0ZAMOqQXosXkcS60BwkoNu1gI5fb1",)))
    assert status["errors"] == [f"{DRIVE.format('1TYN0ZAMOqQXosXkcS60BwkoNu1gI5fb1')}: not a PDF"]
    assert not store["page-2026-10-13-business-meeting"].get("agendas")
    # It's tried again on the next run.
    status, store, _ = run(district, tmp_path)
    assert store["page-2026-10-13-business-meeting"]["agendas"]


def test_a_meeting_that_leaves_the_page(district, tmp_path):
    run(district, tmp_path)
    page = (FIXTURES / "linkpage_methuen.html").read_text(encoding="utf-8")
    without = page.replace("2026-10-13", "2026-10-31").replace("Business Meeting<br />", "Workshop<br />", 1)
    status, store, _ = run(district, tmp_path, FakeLinkPage(page=without))
    assert store["page-2026-10-13-business-meeting"]["listed"] is False
    assert store["page-2026-10-31-workshop"]["listed"]
    # A past meeting that leaves the page stays as it was.
    assert all(m["listed"] for m in store.values() if m.get("source") == "page" and m["date"] < "2026-10-12")


def test_a_page_without_meetings_fails(district, tmp_path):
    with pytest.raises(fetch_page_meetings.FetchError):
        run(district, tmp_path, FakeLinkPage(page="<html><body>Moved</body></html>"))
    assert not (tmp_path / "meetings" / "meetings.json").exists()


def test_waltham(config, tmp_path):
    town = copy.deepcopy(config)
    town["page_meetings"] = waltham()
    status, store, client = run(town, tmp_path)
    page = [m for m in store.values() if m.get("source") == "page"]
    assert status["meetings_created"] == 16 and not status["errors"] and not status["unrecognized"]
    assert sum(len(m.get("agendas", [])) for m in page) == 16 and sum(len(m.get("minutes", [])) for m in page) == 10
    assert not any("1TeZPpVRgFpSu-QaCNBdG20a5ph96THrp" in u for u in client.urls)  # the Spanish agenda


@pytest.mark.parametrize("change, message", [
    ({"page_url": None}, "needs page_url"),
    ({"body": None}, "needs body"),
    ({"since": "July 2026"}, "since must be a date"),
    ({"bodies": ["School Committee"]}, "bodies must map"),
    ({"agenda_pattern": "(agenda"}, "agenda_pattern isn't a regular expression"),
    ({"max_documents_per_run": 0}, "max_documents_per_run must be"),
])
def test_config_is_checked(change, message):
    settings = {k: v for k, v in {**methuen(), **change}.items() if v is not None}
    with pytest.raises(SystemExit, match=message):
        linkpage.check({"slug": "methuen", "page_meetings": settings})


# ---- On the site -------------------------------------------------------------------------------

def test_site_shows_the_districts_meetings(config, data_dir, tmp_path, monkeypatch):
    from conftest import BUILT_AT
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    town = copy.deepcopy(config)  # the session's config, shared with other tests
    town["page_meetings"] = {**methuen(), "since": "2026-09-01"}
    fetch_page_meetings.run(town, FakeLinkPage(), data, now=NOW)
    monkeypatch.setattr(build_site, "load_config", lambda slug: town)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    shown = {m["id"]: m for m in build_site.load_meetings(data, BUILT_AT.date())["all"]}

    page = (out / "meetings" / shown["page-2026-09-25-policy-subcommittee"]["slug"] / "index.html").read_text()
    assert "Minutes on Google Drive" in page and "Agenda on Google Drive" in page
    assert "known from the <a" in page and "Methuen Public Schools website" in page
    assert "Not listed on the city calendar" not in page
    about = (out / "about" / "index.html").read_text()
    assert ("<strong>School Committee meetings, agendas, and minutes:</strong> Methuen Public Schools, "
            f'from the documents linked on its <a href="{FakeLinkPage.METHUEN}"') in about
    board = (out / "meetings" / "boards" / "school-committee-policy-subcommittee" / "index.html").read_text()
    assert "Methuen Public Schools website" in board
