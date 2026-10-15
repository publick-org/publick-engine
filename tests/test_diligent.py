"""A board's meetings from its Diligent Community portal: Burlington School District's list of
meetings and a meeting's documents (tests/fixtures/diligent_burlington.json,
diligent_documents.json), and Portland Public Schools' names (diligent_portland.json), saved
and trimmed."""

import copy
import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeDiligent

from pipeline import absences, diligent, fetch_diligent_meetings
from pipeline.fetch_meetings import load_store
from pipeline.http import FetchError

NOW = datetime(2026, 10, 15, 7, 0, tzinfo=TZ)
BASE = FakeDiligent.BASE


def burlington() -> dict:
    """[diligent_meetings] as Burlington's config would have it."""
    return {"page_url": f"{BASE}/Portal/", "source_name": "Burlington School District", "since": "2026-01-01",
            "bodies": {"School Board Meeting": "School Board",
                       "Finance & Facilities Committee": "School Board Finance and Facilities Committee",
                       "Diversity, Equity & Inclusion Committee": "School Board Diversity, Equity and Inclusion Committee",
                       "Policy & Governance Committee": "School Board Policy and Governance Committee"}}


def portland() -> dict:
    """[diligent_meetings] for Portland's portal, whose meeting types aren't always the board."""
    return {"page_url": "https://portlandschools.community.highbond.com/Portal/", "source_name": "Portland Public Schools",
            "since": "2026-01-01", "body": "Board of Public Education",
            "bodies": {"Policy Committee": "Board of Public Education Policy Committee",
                       "Ad-hoc Committee on the District's Commitment to Equity": "Ad-hoc Committee on Equity and Belonging",
                       "Joint Meeting of the City and School District Finance Committees":
                           "Joint City and School Finance Committees"}}


@pytest.fixture
def district(config):
    town = copy.deepcopy(config)
    town["diligent_meetings"] = burlington()
    return town


def run(town, tmp_path, client=None, now=NOW):
    client = client or FakeDiligent()
    status = fetch_diligent_meetings.run(town, client, tmp_path, now=now)
    return status, load_store(tmp_path), client


# ---- The list of meetings -------------------------------------------------------------------

def test_listing_burlington():
    found, unrecognized = diligent.parse_listing(json.loads((FIXTURES / "diligent_burlington.json").read_text()),
                                                 burlington())
    by_id = {m["id"]: m for m in found}
    board = by_id[1218]
    assert board["date"] == "2026-10-06" and board["start_time"] == "18:00" and board["body"] == "School Board"
    assert board["name"] == "BSD School Board Meeting" and board["published"] and board["status"] == "scheduled"
    assert board["location"] == "Hunt Middle School - Library 1364 North Avenue" and board["remote_url"] is None
    assert by_id[1219]["body"] == "School Board Finance and Facilities Committee"
    cancelled = by_id[1186]
    assert cancelled["status"] == "cancelled" and not cancelled["published"] and cancelled["location"] == ""
    assert cancelled["body"] == "School Board Finance and Facilities Committee"
    assert unrecognized == []


def test_listing_portland_names_before_types():
    """Portland files an ad-hoc committee and a joint meeting under other types: the name wins."""
    found, unrecognized = diligent.parse_listing(json.loads((FIXTURES / "diligent_portland.json").read_text()),
                                                 portland())
    by_name = {m["name"]: m for m in found}
    adhoc = next(m for name, m in by_name.items() if name.startswith("Ad-hoc"))
    assert adhoc["body"] == "Ad-hoc Committee on Equity and Belonging"
    assert by_name["Joint Meeting of the City and School District Finance Committees"]["body"] == \
        "Joint City and School Finance Committees"
    special = by_name["Special Business Meeting of the Portland Board of Public Education"]
    assert special["special"] and special["body"] == "Board of Public Education"
    weather = by_name["Canceled due to weather: Policy Committee"]
    assert weather["status"] == "cancelled" and weather["body"] == "Board of Public Education Policy Committee"
    later = by_name["Policy Committee (Cancelled)"]
    assert later["status"] == "cancelled" and later["date"] == "2026-06-08"
    upcoming = by_name["Business Meeting"]
    assert not upcoming["published"] and upcoming["body"] == "Board of Public Education"
    assert unrecognized == []


def test_a_meeting_with_no_board_is_reported():
    settings = {**portland(), "body": None}
    found, unrecognized = diligent.parse_listing(json.loads((FIXTURES / "diligent_portland.json").read_text()), settings)
    assert "Business Meeting" in unrecognized
    assert all(m["name"] != "Business Meeting" for m in found)


def test_exclude_pattern():
    settings = {**portland(), "exclude_pattern": "^Joint"}
    found, _ = diligent.parse_listing(json.loads((FIXTURES / "diligent_portland.json").read_text()), settings)
    assert not any(m["name"].startswith("Joint") for m in found)


@pytest.mark.parametrize("text, place, link", [
    ("Remote Only [https://us06web.zoom.us/j/84060258850]", "Remote Only", "https://us06web.zoom.us/j/84060258850"),
    ("Hybrid Business Meeting In-person at Casco Bay High School and via Zoom (https://tinyurl.com/3c3tayaj)",
     "Hybrid Business Meeting In-person at Casco Bay High School and via Zoom", None),
    ("In person at Casco Bay High School and via Zoom: https://us06web.zoom.us/j/858?pwd=x https://us06web.zoom.us/j/858?pwd=x",
     "In person at Casco Bay High School and via Zoom", "https://us06web.zoom.us/j/858?pwd=x"),
    ("Remote Only Hosted By City of Portland https://portlandme.portal.civicclerk.com/event/9527/files",
     "Remote Only Hosted By City of Portland", None),
    ("*", "", None),
])
def test_location(text, place, link):
    assert diligent.location(text) == (place, link)


# ---- A meeting's documents ------------------------------------------------------------------

def test_documents_agenda_alone_and_minutes():
    picked = diligent.pick_documents(json.loads((FIXTURES / "diligent_documents.json").read_text())["Documents"],
                                     burlington())
    assert picked["agenda"]["url"] == f"{BASE}/document/16621/?printPdf=true"
    assert picked["minutes"]["url"] == f"{BASE}/document/16657"
    assert picked["agenda"]["last_modified"] == 639238878541230000


def test_documents_adopted_minutes_first_and_package_for_an_html_agenda():
    docs = [{"DocumentType": 1, "Format": "html", "Id": 1}, {"DocumentType": 4, "Format": "pdf", "Id": 4},
            {"DocumentType": 10, "Format": "pdf", "Id": 10}, {"DocumentType": 53, "Format": "pdf", "Id": 53}]
    picked = diligent.pick_documents(docs, burlington())
    assert picked["agenda"]["url"] == f"{BASE}/document/4"
    assert picked["minutes"]["url"] == f"{BASE}/document/53"
    assert diligent.pick_documents([{"DocumentType": 1, "Format": "html", "Id": 1}], burlington()) == {}


# ---- A run ----------------------------------------------------------------------------------

def test_run_records_meetings_and_saves_documents(district, tmp_path):
    status, store, client = run(district, tmp_path)
    assert status["meetings_listed"] == 5 and status["meetings_created"] == 5
    board = store["diligent-1218"]
    assert board["source"] == "diligent" and board["body"] == "School Board"
    assert board["title"] == "School Board Meeting" and board["source_name"] == "Burlington School District"
    assert board["source_url"] == f"{BASE}/Portal/MeetingInformation.aspx?Id=1218"
    assert board["slug"] == "2026-10-06-school-board" and board["start_time"] == "18:00"
    # What the first listing says isn't a change.
    assert not [h for h in board.get("history", []) if h["field"] not in ("agenda", "minutes")]
    agenda, minutes = board["agendas"][-1], board["minutes"][-1]
    assert agenda["source_url"] == f"{BASE}/document/121801/?printPdf=true" and agenda["posted_on"] == "Diligent Community"
    assert minutes["source_url"] == f"{BASE}/document/121810"
    assert (tmp_path / "meetings" / "agendas" / agenda["file"]).read_bytes().startswith(b"%PDF")
    assert (tmp_path / "meetings" / "minutes" / minutes["file"]).exists()
    # A cancelled meeting, not published, has no documents read.
    cancelled = store["diligent-1186"]
    assert cancelled["status"] == "cancelled" and "agendas" not in cancelled
    assert not any("/meetings/1186/" in url for url in client.urls)
    assert status["documents_added"] == 8 and status["errors"] == []
    saved = json.loads((tmp_path / "meetings" / "diligent_status.json").read_text())
    assert saved["meetings_checked"] == 4


def test_a_second_run_downloads_nothing_unchanged(district, tmp_path):
    run(district, tmp_path)
    status, store, client = run(district, tmp_path)
    assert status["documents_added"] == 0 and status["documents_changed"] == 0
    assert not any("/document/" in url for url in client.urls)
    assert len(store["diligent-1218"]["agendas"]) == 1


def test_revised_minutes_saved_as_a_new_version(district, tmp_path):
    run(district, tmp_path)
    docs = FakeDiligent().documents_for(1218)["Documents"]
    for d in docs:
        if d["DocumentType"] == 10:
            d["LastModified"] += 1
    # Marked modified, same content: not saved again.
    status, store, _ = run(district, tmp_path, FakeDiligent(documents={1218: docs}))
    assert status["documents_changed"] == 0 and len(store["diligent-1218"]["minutes"]) == 1
    # Then adopted: a new document, saved as the meeting's latest minutes with the change recorded.
    docs.append({"DocumentType": 53, "Format": "pdf", "Id": 121853, "LastModified": 1})
    status, store, _ = run(district, tmp_path, FakeDiligent(documents={1218: docs}))
    meeting = store["diligent-1218"]
    assert status["documents_added"] == 1 and len(meeting["minutes"]) == 2
    assert meeting["minutes"][-1]["source_url"] == f"{BASE}/document/121853"
    assert meeting["history"][-1]["field"] == "minutes"


def test_older_meetings_not_read_again(district, tmp_path):
    """A meeting past recheck_days, read once, isn't read again."""
    district["diligent_meetings"]["recheck_days"] = 20
    run(district, tmp_path)
    _, _, client = run(district, tmp_path)
    assert not any("/meetings/1195/" in url for url in client.urls)
    assert any("/meetings/1220/" in url for url in client.urls)


def test_an_upcoming_meeting_that_leaves_the_list(district, tmp_path):
    entries = json.loads((FIXTURES / "diligent_burlington.json").read_text())
    upcoming = {**entries[0], "Id": 1300, "MeetingDate": "2026-10-20", "MeetingDateTime": "2026-10-20 18:00",
                "Name": "BSD School Board Meeting - Oct 20 2026", "Published": False}
    _, store, client = run(district, tmp_path, FakeDiligent(entries=[*entries, upcoming]))
    assert store["diligent-1300"]["listed"] and "agendas" not in store["diligent-1300"]
    assert not any("/meetings/1300/" in url for url in client.urls)
    _, store, _ = run(district, tmp_path, FakeDiligent(entries=entries))
    assert store["diligent-1300"]["listed"] is False


def test_documents_limited_per_run(district, tmp_path):
    district["diligent_meetings"]["max_documents_per_run"] = 3
    status, store, _ = run(district, tmp_path)
    assert status["meetings_waiting"] == 2
    # Newest first: the latest meetings have their documents.
    assert store["diligent-1220"].get("agendas") and not store["diligent-1195"].get("agendas")
    status, store, _ = run(district, tmp_path)
    assert store["diligent-1195"].get("agendas")


def test_a_document_that_isnt_a_pdf(district, tmp_path):
    status, store, _ = run(district, tmp_path, FakeDiligent(content={121810: b"<html>Sign in</html>"}))
    assert any("121810" in e for e in status["errors"])
    assert "minutes" not in store["diligent-1218"] and store["diligent-1218"]["agendas"]
    assert "documents_checked" not in store["diligent-1218"]


def test_a_list_that_isnt_json(district, tmp_path):
    class Broken(FakeDiligent):
        def get(self, url):
            response = super().get(url)
            if "meetings?" in url:
                response.text = "<html>Maintenance</html>"
            return response
    with pytest.raises(FetchError):
        run(district, tmp_path, Broken())


def test_minutes_not_posted_names_the_district(district):
    m = {"date": "2026-09-01", "body": "School Board", "status": "scheduled", "listed": True, "source": "diligent",
         "source_url": f"{BASE}/Portal/MeetingInformation.aspx?Id=1195", "source_name": "Burlington School District",
         "minutes": []}
    gap = absences.minutes_gap(district, m, NOW.date())
    assert gap["reason"] == "not_posted" and gap["source"] == "diligent"
    assert gap["name"] == "Burlington School District" and gap["url"] == m["source_url"]


# ---- The config -----------------------------------------------------------------------------

@pytest.mark.parametrize("settings, message", [
    ({"source_name": "X", "since": "2026-01-01", "body": "B"}, "needs page_url"),
    ({**burlington(), "page_url": "bsdvt.community.highbond.com"}, "page_url must be"),
    ({**burlington(), "since": "January"}, "since must be a date"),
    ({**burlington(), "bodies": {}}, "needs bodies, body, or both"),
    ({**burlington(), "bodies": {"X": ""}}, "bodies must map"),
    ({**burlington(), "exclude_pattern": "("}, "exclude_pattern isn't"),
    ({**burlington(), "max_documents_per_run": 0}, "max_documents_per_run must be"),
])
def test_config_check(settings, message):
    with pytest.raises(SystemExit, match=message):
        diligent.check({"slug": "burlington", "diligent_meetings": settings})


def test_config_check_passes():
    diligent.check({"slug": "burlington", "diligent_meetings": burlington()})
    diligent.check({"slug": "portland", "diligent_meetings": portland()})


# ---- The site -------------------------------------------------------------------------------

def test_site_shows_the_districts_meetings(config, data_dir, tmp_path, monkeypatch):
    import shutil

    from conftest import BUILT_AT

    from pipeline import build_site
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    town = copy.deepcopy(config)  # the session's config, shared with other tests
    town["diligent_meetings"] = burlington()
    fetch_diligent_meetings.run(town, FakeDiligent(), data, now=BUILT_AT)
    monkeypatch.setattr(build_site, "load_config", lambda slug: town)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    shown = {m["id"]: m for m in build_site.load_meetings(data, BUILT_AT.date())["all"]}

    page = (out / "meetings" / shown["diligent-1195"]["slug"] / "index.html").read_text()
    assert "Minutes on Diligent Community" in page and "Agenda on Diligent Community" in page
    assert "This meeting on the Burlington School District meeting portal" in page and "</a> (Diligent Community)." in page
    assert "Hunt Middle School - Library 1364 North Avenue" in page
    assert "Not listed on the city calendar" not in page
    about = (out / "about" / "index.html").read_text()
    assert ("<strong>School Board meetings, agendas, and minutes:</strong> Burlington School District, "
            f'from its <a href="{BASE}/Portal/"') in about
    board = (out / "meetings" / "boards" / "school-board" / "index.html").read_text()
    assert f'Earlier meetings are on the <a href="{BASE}/Portal/"' in board and "Burlington School District meeting portal" in board


def test_a_portal_without_the_boards_minutes(district, tmp_path):
    """Portland's board approves its minutes in a later meeting's packet: none are on the portal."""
    district["diligent_meetings"]["minutes"] = False
    status, store, _ = run(district, tmp_path)
    assert store["diligent-1218"]["agendas"] and "minutes" not in store["diligent-1218"]
    m = {**store["diligent-1195"], "minutes": []}
    gap = absences.minutes_gap(district, m, NOW.date())
    assert gap == {"reason": "not_collected", "source": "diligent", "url": m["source_url"],
                   "name": "Burlington School District"}
    with pytest.raises(SystemExit, match="minutes must be true or false"):
        diligent.check({"slug": "burlington", "diligent_meetings": {**burlington(), "minutes": "no"}})


@pytest.mark.parametrize("minutes, says", [
    (True, "Minutes haven't been posted on the <a"),
    (False, "This site doesn't collect this board's minutes. They may be on the <a"),
])
def test_site_says_where_minutes_not_here_may_be(config, data_dir, tmp_path, monkeypatch, minutes, says):
    import shutil

    from conftest import BUILT_AT

    from pipeline import build_site
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    town = copy.deepcopy(config)
    town["diligent_meetings"] = {**burlington(), "minutes": minutes}
    agenda_only = [d for d in FakeDiligent().documents_for(1195)["Documents"] if d["DocumentType"] in (1, 4)]
    fetch_diligent_meetings.run(town, FakeDiligent(documents={1195: agenda_only}), data, now=BUILT_AT)
    monkeypatch.setattr(build_site, "load_config", lambda slug: town)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    page = (out / "meetings" / "2026-09-01-school-board" / "index.html").read_text()
    assert says in page
    # The district's portal, where the meeting is, not the city's Archive Center.
    assert f'href="{BASE}/Portal/MeetingInformation.aspx?Id=1195"' in page.split(says)[1][:200]
    assert "Burlington School District meeting portal" in page.split(says)[1][:400]
