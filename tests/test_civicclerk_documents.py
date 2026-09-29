"""Agendas and minutes from a CivicClerk portal and agendas from a DotNetNuke city calendar
(Manchester's, saved), for a town that collects documents."""

import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeManchester, FakeResponse
from test_calendars import manchester

from pipeline import civicclerk, fetch_meetings, fetch_minutes
from pipeline.config import load_config

API = "https://manchesternh.api.civicclerk.com/v1"
PORTAL = "https://manchesternh.portal.civicclerk.com"
# Before the August 4 meetings, so they are upcoming.
BEFORE = datetime(2026, 8, 1, 7, 0, tzinfo=TZ)
# After them, when their minutes are posted.
AFTER = datetime(2026, 9, 26, 12, 0, tzinfo=TZ)


class FakeManchesterFiles(FakeManchester):
    """Also serves every CivicClerk file as a distinct PDF."""

    def __init__(self):
        super().__init__()
        self.pdf = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()

    def get(self, url):
        if "GetMeetingFileStream" in url or "manchesternh.gov/Portals/" in url:
            self.urls.append(url)
            return FakeResponse(self.pdf + f"\n% {url}\n".encode(), {"content-disposition": "attachment; filename=1.pdf"})
        return super().get(url)


@pytest.fixture
def config():
    town = manchester(load_config("gloucester"))
    town["meetings"]["documents"] = True
    return town


def events():
    return json.loads((FIXTURES / "civicclerk_events_1.json").read_text())


def test_events_name_their_agenda_and_minutes_files():
    found = {e["id"]: e for e in civicclerk.parse_events(events(), PORTAL, api_url=API)}
    accounts = found["civicclerk-2047"]
    assert accounts["agenda_id"] == "civicclerk-5174"  # the agenda, not the agenda packet (5175)
    assert accounts["agenda_url"] == f"{API}/Meetings/GetMeetingFileStream(fileId=5174,plainText=false)"
    assert accounts["minutes_id"] == "civicclerk-5199"
    assert accounts["minutes_url"] == f"{API}/Meetings/GetMeetingFileStream(fileId=5199,plainText=false)"
    registrars = found["civicclerk-2063"]  # an agenda, no minutes yet
    assert registrars["agenda_id"] == "civicclerk-5202" and "minutes_id" not in registrars


def test_without_documents_no_files_are_named():
    assert not any("agenda_id" in e for e in civicclerk.parse_events(events(), PORTAL))


def test_upcoming_agendas_are_saved(config, tmp_path):
    client = FakeManchesterFiles()
    status = fetch_meetings.run(config, client, tmp_path, now=BEFORE)
    assert not [e for e in status["errors"] if "civicclerk" in e.lower()]
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    board = store["civicclerk-1968"]
    assert board["agendas"][0]["id"] == "civicclerk-5184"
    assert (tmp_path / "meetings" / "agendas" / "civicclerk-5184.pdf").exists()
    assert not any("fileId=5185" in u for u in client.urls)  # never the packet


def test_documents_off_saves_nothing(config, tmp_path):
    config["meetings"]["documents"] = False
    client = FakeManchesterFiles()
    fetch_meetings.run(config, client, tmp_path, now=BEFORE)
    assert not any("GetMeetingFileStream" in u for u in client.urls)
    assert not (tmp_path / "meetings" / "agendas").exists()


def test_minutes_are_saved_and_a_republished_file_replaces_them(config, tmp_path):
    fetch_meetings.run(config, FakeManchesterFiles(), tmp_path, now=AFTER)
    summary = fetch_minutes.run_linked(config, FakeManchesterFiles(), tmp_path, now=AFTER)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    with_minutes = [m for m in store.values() if m.get("minutes")]
    assert summary["minutes_added"] == len(with_minutes) > 0 and not summary["errors"]
    board = store["civicclerk-1968"]
    assert board["minutes"][0]["id"] == "civicclerk-5246"

    # The clerk republishes the minutes as a new file.
    pages = [events(), json.loads((FIXTURES / "civicclerk_events_2.json").read_text())]
    for e in pages[0]["value"]:
        for f in e.get("publishedFiles") or []:
            if f.get("fileId") == 5246:
                f["fileId"] = 9246
    client = FakeManchesterFiles()
    client.pages = pages
    fetch_meetings.run(config, client, tmp_path, now=AFTER.replace(day=27))
    assert fetch_minutes.run_linked(config, FakeManchesterFiles(), tmp_path, now=AFTER.replace(day=27))["minutes_added"] == 1
    board = json.loads((tmp_path / "meetings" / "meetings.json").read_text())["civicclerk-1968"]
    assert [d["id"] for d in board["minutes"]] == ["civicclerk-5246", "civicclerk-9246"]
    assert board["history"][-1]["field"] == "minutes"


def test_minutes_before_since_are_left(config, tmp_path):
    fetch_meetings.run(config, FakeManchesterFiles(), tmp_path, now=AFTER)
    config["meetings"]["civicclerk"]["since"] = "2026-09-01"
    summary = fetch_minutes.run_linked(config, FakeManchesterFiles(), tmp_path, now=AFTER)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    assert all(m["date"] >= "2026-09-01" for m in store.values() if m.get("minutes"))
    assert summary["minutes_added"] == sum(1 for m in store.values() if m.get("minutes"))


def test_turning_documents_on_rereads_from_since(config, tmp_path):
    """A town that already records meetings reads its earlier meetings' files once, when documents are turned on."""
    config["meetings"]["civicclerk"]["since"] = "2026-06-01"
    config["meetings"]["documents"] = False
    fetch_meetings.run(config, FakeManchesterFiles(), tmp_path, now=AFTER)
    config["meetings"]["documents"] = True
    client = FakeManchesterFiles()
    fetch_meetings.run(config, client, tmp_path, now=AFTER.replace(day=27))
    assert "2026-06-01" in client.urls[0]  # from `since`, not the last 60 days
    client = FakeManchesterFiles()
    fetch_meetings.run(config, client, tmp_path, now=AFTER.replace(day=28))
    assert "2026-07-30" in client.urls[0]  # then back to 60 days


# Early September: the city calendar's September meetings are upcoming.
SEPTEMBER = datetime(2026, 9, 5, 7, 0, tzinfo=TZ)


def test_city_calendar_agendas_are_saved(config, tmp_path):
    config["meetings"]["max_event_pages"] = 200
    client = FakeManchesterFiles()
    status = fetch_meetings.run(config, client, tmp_path, now=SEPTEMBER)
    assert not [e for e in status["errors"] if "manchesternh.gov" in e]
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    city = {(m["title"], m["date"]): m for m in store.values() if m["id"].startswith("dnn-")}
    zba = city["ZBA Public Hearing", "2026-09-10"]
    assert zba["agendas"][0]["source_url"].split("?")[0].endswith("/2026-09-10 ZBA Agenda.pdf")
    assert (tmp_path / "meetings" / "agendas" / zba["agendas"][0]["file"]).exists()
    assert city["Trustees of Trust Funds", "2026-09-15"]["agendas"]
    # A link to the year's schedule or a board's page is not saved as an agenda.
    assert "agendas" not in city["Manchester Development Corporation Board of Directors Meeting", "2026-09-10"]
    assert "agendas" not in city["Highway Commission", "2026-09-14"]
    assert not any("MDC 2026 Website" in u for u in client.urls)


def test_city_calendar_documents_off_saves_nothing(config, tmp_path):
    config["meetings"]["documents"] = False
    client = FakeManchesterFiles()
    fetch_meetings.run(config, client, tmp_path, now=SEPTEMBER)
    assert not any("/Portals/" in u for u in client.urls)
