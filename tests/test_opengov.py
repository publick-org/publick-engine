"""Public bodies' meetings from Rhode Island's Open Meetings portal: South Kingstown School
Committee's completed meetings' feed and two meeting pages (tests/fixtures/opengov_*), saved
and trimmed; and their videos from a clerk's ClerkBase dockets (a docket of Pawtucket's City
Council, tests/fixtures/clerkbase_docket.htm, trimmed)."""

import copy
import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeOpenGov

from pipeline import absences, clerkbase, fetch_opengov_meetings, opengov, youtube
from pipeline.fetch_meetings import load_store
from pipeline.http import FetchError

NOW = datetime(2026, 10, 15, 7, 0, tzinfo=TZ)


def south_kingstown() -> dict:
    """[opengov_meetings] as South Kingstown's config would have it."""
    return {"page_url": "https://opengov.sos.ri.gov/OpenMeetingsPublic/OpenMeetingDashboard?subtopmenuId=201",
            "source_name": "Rhode Island Open Meetings portal", "since": "2026-01-01",
            "bodies": {"South Kingstown School Committee": "School Committee"}}


@pytest.fixture
def town(config):
    town = copy.deepcopy(config)
    town["opengov_meetings"] = south_kingstown()
    return town


def run(town, tmp_path, client=None, now=NOW):
    client = client or FakeOpenGov()
    status = fetch_opengov_meetings.run(town, client, tmp_path, now=now)
    return status, load_store(tmp_path), client


# ---- The feed and the meeting's page --------------------------------------------------------

def test_feed():
    found = {m["id"]: m for m in opengov.parse_feed((FIXTURES / "opengov_completed.xml").read_text())}
    assert found[1093108] == {"id": 1093108, "date": "2026-09-08", "start_time": "17:00",
                              "location": "South Kingstown High School Cafeteria, 215 Columbia Street, Wakefield, RI, 02879"}
    assert found[764250]["date"] == "2006-06-13"
    assert opengov.parse_feed(FakeOpenGov.feed([])) == []
    with pytest.raises(ValueError):
        opengov.parse_feed("<html>Maintenance</html>")


def test_meeting_page_with_minutes():
    page = opengov.parse_meeting((FIXTURES / "opengov_meeting_minutes.html").read_text())
    assert page["body"] == "South Kingstown School Committee"
    assert not page["annual_calendar"] and not page["announcement"] and not page["cancelled"]
    assert [a["path"] for a in page["agendas"]] == ["\\Notices\\3959\\2026\\565478.pdf"]
    minutes = page["minutes"][0]
    assert minutes["path"] == "\\Minutes\\3959\\2026\\567158.pdf" and minutes["filed"] == "2026-09-22T18:47"
    # Who filed it isn't kept.
    assert minutes["label"] == "SC Approved - 9.22.2026, Minutes filed on Sep 22 2026, 06:47PM"


def test_meeting_page_with_an_amended_agenda():
    page = opengov.parse_meeting((FIXTURES / "opengov_meeting_amended.html").read_text())
    assert [a["number"] for a in page["agendas"]] == ["568407", "568418"]
    assert page["minutes"] == []


def test_file_url():
    assert opengov.file_url("\\Notices\\3959\\2026\\568418.pdf") == \
        "https://opengov.sos.ri.gov/Common/DownloadMeetingFiles?FilePath=%5CNotices%5C3959%5C2026%5C568418.pdf"


# ---- A run ----------------------------------------------------------------------------------

def test_run_records_meetings_and_saves_documents(town, tmp_path):
    status, store, client = run(town, tmp_path)
    # The 2006 meeting is before since; the annual calendar isn't a meeting.
    assert set(store) == {"opengov-1093108", "opengov-1094476", "opengov-1094488"}
    assert status["not_meetings"] == {"1082970": "annual calendar"}
    m = store["opengov-1093108"]
    assert m["source"] == "opengov" and m["body"] == "School Committee" and m["title"] == "School Committee Meeting"
    assert m["date"] == "2026-09-08" and m["start_time"] == "17:00" and m["status"] == "scheduled"
    assert m["source_url"] == "https://opengov.sos.ri.gov/OpenMeetingsPublic/MeetingInformation?MeetingID=1093108"
    assert m["location"].startswith("South Kingstown High School Cafeteria")
    assert not m.get("history")
    agenda, minutes = m["agendas"][-1], m["minutes"][-1]
    assert agenda["posted_on"] == "the Open Meetings portal" and agenda["portal_path"].endswith("565478.pdf")
    assert minutes["filed_at"] == "2026-09-22T18:47"
    assert (tmp_path / "meetings" / "minutes" / minutes["file"]).read_bytes().startswith(b"%PDF")
    # Both agendas, the amended one last, with the change in the meeting's history.
    amended = store["opengov-1094476"]
    assert [a["portal_path"][-10:] for a in amended["agendas"]] == ["568407.pdf", "568418.pdf"]
    assert amended["history"][-1]["field"] == "agenda"
    assert status["documents_added"] == 6 and status["errors"] == []


def test_a_second_run_reads_only_what_can_change(town, tmp_path):
    run(town, tmp_path)
    status, store, client = run(town, tmp_path)
    assert status["documents_added"] == 0
    assert not any("DownloadMeetingFiles" in url for url in client.urls)
    # Meetings with minutes, and the annual calendar, aren't read again; recent ones without minutes are.
    assert not any("MeetingID=1093108" in url or "MeetingID=1082970" in url for url in client.urls)
    assert any("MeetingID=1094476" in url for url in client.urls)


def test_minutes_filed_later(town, tmp_path):
    run(town, tmp_path)
    amended = (FIXTURES / "opengov_meeting_amended.html").read_text()
    with_minutes = amended.replace(
        "No minutes on file for this meeting.",
        "<a onclick=\"DownloadMeetingFiles('\\\\Minutes\\\\3959\\\\2026\\\\569001.pdf')\">"
        "Minutes filed on Oct 27 2026, 07:10PM by the clerk</a>")
    status, store, _ = run(town, tmp_path, FakeOpenGov(pages={1094476: with_minutes}))
    assert status["documents_added"] == 1
    assert store["opengov-1094476"]["minutes"][-1]["portal_path"].endswith("569001.pdf")


def test_upcoming_and_cancelled_meetings(town, tmp_path):
    page = (FIXTURES / "opengov_meeting_minutes.html").read_text().replace("565478", "569100").replace("567158", "569101")
    cancelled_page = page.replace('id="HdnCancelMeetingFlag" name="meetingDetailChange.CancelMeetingFlag" type="hidden" value="0"',
                                  'id="HdnCancelMeetingFlag" name="meetingDetailChange.CancelMeetingFlag" type="hidden" value="1"')
    client = FakeOpenGov(upcoming=[(1095000, "2026-10-27T17:00"), (1095001, "2026-11-10T17:00")],
                         pages={1095000: page, 1095001: cancelled_page})
    _, store, _ = run(town, tmp_path, client)
    assert store["opengov-1095000"]["date"] == "2026-10-27" and store["opengov-1095000"]["agendas"]
    cancelled = store["opengov-1095001"]
    assert cancelled["status"] == "cancelled" and "agendas" not in cancelled
    # The next run, the upcoming meeting is in no feed: no longer listed. The cancelled one stays cancelled.
    _, store, _ = run(town, tmp_path, FakeOpenGov(upcoming=[(1095001, "2026-11-10T17:00")],
                                                  pages={1095001: cancelled_page}))
    assert store["opengov-1095000"]["listed"] is False
    assert store["opengov-1095001"]["status"] == "cancelled"


def test_the_cancelled_feed(town, tmp_path):
    _, store, _ = run(town, tmp_path, FakeOpenGov(cancelled=[(1093108, "2026-09-08T17:00")]))
    assert store["opengov-1093108"]["status"] == "cancelled" and "minutes" not in store["opengov-1093108"]


def test_documents_limited_per_run(town, tmp_path):
    town["opengov_meetings"]["max_documents_per_run"] = 2
    status, store, _ = run(town, tmp_path)
    assert status["documents_added"] == 2 and status["meetings_waiting"] >= 1
    for _ in range(3):
        status, store, _ = run(town, tmp_path)
    assert sum(len(m.get("agendas", [])) + len(m.get("minutes", [])) for m in store.values()) == 6


def test_a_file_that_isnt_a_pdf(town, tmp_path):
    status, store, _ = run(town, tmp_path, FakeOpenGov(content={"567158": b"<html>Error</html>"}))
    assert any("not a PDF" in e for e in status["errors"])
    assert "page_checked" not in store["opengov-1093108"]


def test_a_feed_that_isnt_a_feed(town, tmp_path):
    client = FakeOpenGov()
    client.feeds[3] = "<html>Maintenance</html>"
    with pytest.raises(FetchError):
        run(town, tmp_path, client)


def test_minutes_not_posted_names_the_portal(town):
    m = {"date": "2026-10-13", "body": "School Committee", "status": "scheduled", "listed": True, "source": "opengov",
         "source_url": opengov.meeting_url(1094476), "source_name": "Rhode Island Open Meetings portal", "minutes": []}
    gap = absences.minutes_gap(town, m, NOW.date())
    assert gap["reason"] == "not_posted" and gap["source"] == "opengov" and gap["url"] == m["source_url"]


# ---- Videos from ClerkBase dockets ----------------------------------------------------------

FOLDER = "https://clerkshq.com/Content/Pawtucket-ri/council"
DOCKET = (FIXTURES / "clerkbase_docket.htm").read_text(encoding="utf-8")


def test_docket_url():
    assert clerkbase.docket_url(FOLDER, "2026-10-21") == f"{FOLDER}/2026/oct21_26dk.htm"
    assert clerkbase.docket_url(FOLDER + "/", "2026-02-05") == f"{FOLDER}/2026/feb05_26dk.htm"


def test_docket_video():
    # Its whole-meeting link plays January 7's meeting, left from that docket; its items play its own.
    assert "SCkRAP6hz1c" in DOCKET
    assert clerkbase.video(DOCKET) == "y_CuxXXyn2E"
    # Before the video is indexed, the docket links none.
    assert clerkbase.video(DOCKET.replace("youtube.com", "example.com")) is None
    # Two videos linked as often: neither is known to be the meeting's.
    assert clerkbase.video('<a href="https://youtu.be/aaaaaaaaaaa">1</a><a href="https://www.youtube.com/watch?v=bbbbbbbbbbb">2</a>') is None


def with_dockets(town, dockets):
    town["opengov_meetings"]["dockets"] = {"School Committee": FOLDER}
    return FakeOpenGov(dockets=dockets)


def test_a_docket_gives_its_meeting_a_video(town, tmp_path):
    client = with_dockets(town, {f"{FOLDER}/2026/sep08_26dk.htm": DOCKET})
    status, store, _ = run(town, tmp_path, client)
    assert store["opengov-1093108"]["video_id"] == "y_CuxXXyn2E"
    assert status["videos_linked"] == 1 and status["errors"] == []
    # October 13's two meetings, at 5 and 6 p.m., share a docket: neither is given its video, nor is it read.
    assert not any("video_id" in store[i] for i in ("opengov-1094476", "opengov-1094488"))
    assert not any("oct13" in url for url in client.urls)
    # A YouTube source without a channel takes it, matched to the meeting that links it.
    found = youtube.linked({"platform": "youtube", "boards": ["School Committee"]}, store, "2026-09-01")
    assert [(f["video_id"], f["listing"], f["date"]) for f in found] == [("y_CuxXXyn2E", "opengov-1093108", "2026-09-08")]
    # A meeting with its video isn't read again.
    _, _, client = run(town, tmp_path, with_dockets(town, {}))
    assert not any("clerkshq.com" in url for url in client.urls)


def test_a_docket_is_read_until_it_links_a_video(town, tmp_path):
    url = f"{FOLDER}/2026/sep08_26dk.htm"
    _, store, client = run(town, tmp_path, with_dockets(town, {}))
    assert url in client.urls and "video_id" not in store["opengov-1093108"]
    _, store, _ = run(town, tmp_path, with_dockets(town, {url: DOCKET.replace("youtube.com", "example.com")}))
    assert "video_id" not in store["opengov-1093108"]
    _, store, _ = run(town, tmp_path, with_dockets(town, {url: DOCKET}))
    assert store["opengov-1093108"]["video_id"] == "y_CuxXXyn2E"


def test_dockets_not_read(town, tmp_path):
    # Upcoming and cancelled meetings, and meetings older than recheck_days, aren't read.
    client = FakeOpenGov(upcoming=[(1095000, "2026-10-27T17:00")], cancelled=[(1093108, "2026-09-08T17:00")],
                         pages={1095000: (FIXTURES / "opengov_meeting_amended.html").read_text()})
    town["opengov_meetings"]["dockets"] = {"School Committee": FOLDER}
    _, _, client = run(town, tmp_path, client)
    assert not any("clerkshq.com" in url for url in client.urls)
    town["opengov_meetings"]["recheck_days"] = 30
    _, _, client = run(town, tmp_path, with_dockets(town, {}))
    assert not any("clerkshq.com" in url for url in client.urls)


def test_a_docket_that_cant_be_read(town, tmp_path):
    status, store, _ = run(town, tmp_path, with_dockets(town, {f"{FOLDER}/2026/sep08_26dk.htm": 403}))
    assert any("sep08_26dk.htm: HTTP 403" in e for e in status["errors"])
    assert "video_id" not in store["opengov-1093108"] and store["opengov-1093108"]["minutes"]


# ---- The config -----------------------------------------------------------------------------

@pytest.mark.parametrize("settings, message", [
    ({**south_kingstown(), "bodies": {}}, "needs bodies"),
    ({**south_kingstown(), "since": "2026"}, "since must be a date"),
    ({**south_kingstown(), "page_url": "opengov.sos.ri.gov"}, "page_url must be"),
    ({**south_kingstown(), "bodies": {"South Kingstown School Committee": ""}}, "bodies must map"),
    ({**south_kingstown(), "recheck_days": -1}, "recheck_days must be"),
    ({**south_kingstown(), "dockets": {"School Committee": "https://clerkshq.com/Pawtucket-ri"}}, "dockets must map"),
    ({**south_kingstown(), "dockets": ["School Committee"]}, "dockets must map"),
])
def test_config_check(settings, message):
    with pytest.raises(SystemExit, match=message):
        opengov.check({"slug": "south-kingstown", "opengov_meetings": settings})


def test_config_with_dockets():
    opengov.check({"slug": "pawtucket", "opengov_meetings": {**south_kingstown(), "dockets": {"School Committee": FOLDER}}})


# ---- The site -------------------------------------------------------------------------------

def test_site_shows_the_portals_meetings(config, data_dir, tmp_path, monkeypatch):
    import shutil

    from conftest import BUILT_AT

    from pipeline import build_site
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    town = copy.deepcopy(config)  # the session's config, shared with other tests
    town["opengov_meetings"] = south_kingstown()
    fetch_opengov_meetings.run(town, FakeOpenGov(), data, now=BUILT_AT)
    monkeypatch.setattr(build_site, "load_config", lambda slug: town)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    shown = {m["id"]: m for m in build_site.load_meetings(data, BUILT_AT.date())["all"]}

    page = (out / "meetings" / shown["opengov-1093108"]["slug"] / "index.html").read_text()
    assert "Minutes posted on the Open Meetings portal" in page and "Agenda posted on the Open Meetings portal" in page
    assert "This meeting on the state's Open Meetings portal" in page
    about = (out / "about" / "index.html").read_text()
    assert "<strong>School Committee meetings, agendas, and minutes:</strong> the Rhode Island Secretary of State" in about
    board = (out / "meetings" / "boards" / "school-committee" / "index.html").read_text()
    assert "Earlier meetings are on the" in board and "Open Meetings portal" in board
    assert json.loads((data / "meetings" / "opengov_status.json").read_text())["errors"] == []


def test_site_with_every_meeting_from_the_portal(config, data_dir, tmp_path, monkeypatch):
    """A town with no meeting reader of the city's own and no [archive] (Pawtucket, Central
    Falls): the About and Meetings pages name the portal as the source of every meeting."""
    import shutil

    from conftest import BUILT_AT

    from pipeline import build_site
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    town = copy.deepcopy(config)
    town.pop("archive", None)
    town["meetings"] = {k: v for k, v in town["meetings"].items() if k not in build_site.CITY_MEETING_READERS}
    town["opengov_meetings"] = south_kingstown() | {"bodies": {"South Kingstown Town Council": "Town Council",
                                                               "South Kingstown School Committee": "School Committee"}}
    links = build_site.meeting_links(town)
    assert links["opengov_only"] and "Rhode Island Open Meetings portal" not in [s["name"] for s in links["schools"]]
    assert not build_site.meeting_links(config)["opengov_only"]
    fetch_opengov_meetings.run(town, FakeOpenGov(), data, now=BUILT_AT)
    monkeypatch.setattr(build_site, "load_config", lambda slug: town)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)

    about = (out / "about" / "index.html").read_text()
    assert "<strong>Meetings, agendas, and minutes:</strong> the Rhode Island Secretary of State" in about
    assert "Archive Center" not in about and "Town Council, School Committee meetings" not in about
    meetings = (out / "meetings" / "index.html").read_text()
    assert "From the Rhode Island Secretary of State" in meetings
    assert "School board meetings from the <a href=\"https://opengov" not in meetings
