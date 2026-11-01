"""A board's meetings from its organization's page on BoardBook Premier: Bristol Public Schools'
page (tests/fixtures/boardbook_bristol.html), an agenda page (boardbook_agenda.html) and the
minutes viewer (boardbook_minutes_viewer.html), saved and trimmed, and agenda packages laid out
as BoardBook's are (fakes.boardbook_package)."""

import copy
import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeBoardBook, boardbook_package

from pipeline import absences, boardbook, fetch_boardbook_meetings
from pipeline.fetch_meetings import load_store
from pipeline.http import FetchError

NOW = datetime(2026, 11, 1, 7, 0, tzinfo=TZ)
BASE = FakeBoardBook.BASE


def bristol() -> dict:
    """[boardbook_meetings] as Bristol's config has it, from 2023 on to take in older rows of the fixture."""
    return {"page_url": FakeBoardBook.PAGE, "source_name": "Bristol Public Schools", "since": "2023-01-01",
            "body": "Board of Education",
            "bodies": {"Student Achievement": "Board of Education Student Achievement Committee",
                       "Policy Committee": "Board of Education Policy Committee"}}


@pytest.fixture
def district(config):
    town = copy.deepcopy(config)
    town["boardbook_meetings"] = bristol()
    return town


def run(town, tmp_path, client=None, now=NOW):
    client = client or FakeBoardBook()
    status = fetch_boardbook_meetings.run(town, client, tmp_path, now=now)
    return status, load_store(tmp_path), client


def listing(settings=None):
    return boardbook.parse_listing((FIXTURES / "boardbook_bristol.html").read_text(), settings or bristol())


# ---- The page of meetings ---------------------------------------------------------------

def test_listing():
    found, unrecognized = listing()
    by_id = {m["id"]: m for m in found}
    # Every row but the 2008 meeting, before since.
    assert set(by_id) == {772970, 772304, 773033, 723306, 610578, 711426, 766802}
    board = by_id[772970]
    assert board["date"] == "2026-11-04" and board["start_time"] == "18:30" and board["body"] == "Board of Education"
    assert board["name"] == "Board of Education Meeting" and board["type"] == "Regular" and not board["special"]
    assert board["location"] == "BOE Auditorium and via Zoom Meeting Platform, 129 Church Street, Bristol, CT 06010"
    assert board["remote_url"].startswith("https://zoom.us/j/97752601419")
    assert board["agenda_page"] == f"{BASE}/Public/Agenda/bristol?meeting=772970" and board["minutes_page"] is None
    special = by_id[772304]
    assert special["special"] and special["body"] == "Board of Education" and special["type"] == "Special"
    committee = by_id[773033]
    assert committee["body"] == "Board of Education Student Achievement Committee"
    assert committee["links"] == [{"url": f"{BASE}/Meeting/Supplementals/bristol?meeting=773033",
                                   "text": "Supplemental Resources"}]
    assert by_id[723306]["minutes_page"] == f"{BASE}/Public/Minutes/bristol?meeting=723306"
    assert unrecognized == []


def test_cancelled_recorded_and_revised():
    found, _ = listing()
    cancelled = next(m for m in found if m["name"].startswith("CANCELLED"))
    assert cancelled["status"] == "cancelled" and cancelled["body"] == "Board of Education Student Achievement Committee"
    recorded = next(m for m in found if m.get("video_id"))
    assert recorded["date"] == "2025-10-01" and recorded["body"] == "Board of Education"
    # A recording elsewhere (Zoom, Drive) is kept as a link.
    zoom = next(m for m in found if m["date"] == "2026-09-16")
    assert [link["text"] for link in zoom["links"]] == ["Recorded Meeting URL"]
    revised = boardbook.parse_listing(
        (FIXTURES / "boardbook_bristol.html").read_text().replace("Special Board of Education Meeting",
                                                                   "*Revised* Board of Education Meeting"), bristol())[0]
    assert next(m for m in revised if m["id"] == 772304)["body"] == "Board of Education"


def test_a_meeting_with_no_board_is_reported_and_exclude_pattern():
    settings = {**bristol(), "body": None}
    found, unrecognized = listing(settings)
    assert "Board of Education Meeting" in unrecognized and all(m["body"] for m in found)
    found, _ = listing({**bristol(), "exclude_pattern": "^Special"})
    assert 772304 not in {m["id"] for m in found}


# ---- The agenda package and the minutes ---------------------------------------------------

def test_the_agenda_alone_is_the_packages_pages_before_its_attachments():
    package = boardbook_package("Board of Education Meeting", agenda_pages=3, attachments=("Minutes", "Slides", "Report"))
    content, pages, total = boardbook.agenda_alone(package)
    assert (pages, total) == (3, 6)
    from pdfplumber import open as pdf_open
    import io
    with pdf_open(io.BytesIO(content)) as pdf:
        assert len(pdf.pages) == 3 and "Attachment" not in "".join(p.extract_text() for p in pdf.pages)
    # The same package, the same agenda.
    assert boardbook.agenda_alone(package)[0] == content


@pytest.mark.parametrize("package, expected", [
    (boardbook_package("No attachments", attachments=()), (2, 2)),
    (boardbook_package("No bookmarks", bookmarks=False), (4, 4)),
])
def test_a_package_without_attachments_or_bookmarks_is_all_agenda(package, expected):
    assert boardbook.agenda_pages(package) == expected
    assert boardbook.agenda_alone(package)[0] == package


def test_agenda_page():
    page = (FIXTURES / "boardbook_agenda.html").read_text()
    url = f"{BASE}/Public/Agenda/bristol?meeting=772970"
    assert boardbook.download_url(page, url) == f"{BASE}/Public/DownloadAgenda/bristol?meeting=772970"
    fingerprint = boardbook.agenda_fingerprint(page)
    assert fingerprint and fingerprint == boardbook.agenda_fingerprint(page.replace("<main", "<main data-x"))
    assert boardbook.agenda_fingerprint(page.replace("STAFF AND STUDENT", "STUDENT")) != fingerprint
    assert boardbook.agenda_fingerprint(page.replace("14617011", "14617012")) != fingerprint
    assert boardbook.agenda_fingerprint("<html></html>") is None


def test_minutes_viewer():
    page = (FIXTURES / "boardbook_minutes_viewer.html").read_text()
    assert boardbook.snapshot_url(page, f"{BASE}/Documents/WebViewer/bristol") == (
        f"{BASE}/Public/DownloadSnapshot?SnapshotUri=Org2447%2FMeeting723306%2FMinutes%2FUnit%2FPublic.pdf")


# ---- A run --------------------------------------------------------------------------------

def test_run_records_meetings_and_saves_agendas_alone(district, tmp_path):
    status, store, client = run(district, tmp_path)
    found, _ = listing()
    assert status["meetings_listed"] == len(found) and status["meetings_created"] == len(found)
    board = store["boardbook-772970"]
    assert board["source"] == "boardbook" and board["title"] == "Board of Education Meeting"
    assert board["source_url"] == f"{BASE}/Public/Agenda/bristol?meeting=772970"
    assert board["slug"] == "2026-11-04-board-of-education" and board["start_time"] == "18:30"
    assert board["remote_url"].startswith("https://zoom.us/j/") and board["meeting_type"] == "Regular"
    assert not [h for h in board.get("history", []) if h["field"] not in ("agenda", "minutes")]
    agenda = board["agendas"][-1]
    assert agenda["posted_on"] == "BoardBook" and agenda["pages"] == 2 and agenda["package_pages"] == 4
    assert agenda["package_url"] == f"{BASE}/Public/DownloadAgenda/bristol?meeting=772970"
    assert (tmp_path / "meetings" / "agendas" / agenda["file"]).read_bytes().startswith(b"%PDF")
    assert store["boardbook-772304"]["title"] == "Board of Education Special Meeting"
    # Minutes, where posted: the viewer's PDF.
    minutes = store["boardbook-723306"]["minutes"][-1]
    assert minutes["source_url"] == f"{BASE}/Public/Minutes/bristol?meeting=723306"
    assert minutes["download_url"].endswith("Meeting723306%2FMinutes%2FUnit%2FPublic.pdf")
    # A cancelled meeting's documents aren't read.
    cancelled = next(m for m in store.values() if m.get("status") == "cancelled")
    assert "agendas" not in cancelled
    assert not any(f"meeting={cancelled['id'].split('-')[1]}" in url for url in client.urls)
    assert store["boardbook-711426"]["video_id"]
    assert status["errors"] == []
    saved = json.loads((tmp_path / "meetings" / "boardbook_status.json").read_text())
    assert saved["documents_added"] == status["documents_added"] > 0


def test_a_second_run_reads_only_upcoming_agenda_pages(district, tmp_path):
    run(district, tmp_path)
    status, store, client = run(district, tmp_path)
    assert status["documents_added"] == 0 and status["documents_changed"] == 0
    # The upcoming meeting's agenda page is read again, its unchanged package isn't downloaded.
    assert f"{BASE}/Public/Agenda/bristol?meeting=772970" in client.urls
    assert not any("DownloadAgenda" in url for url in client.urls)
    assert not any("meeting=772304" in url for url in client.urls)
    assert len(store["boardbook-772970"]["agendas"]) == 1


def test_a_revised_agenda_is_saved_as_a_new_version(district, tmp_path):
    run(district, tmp_path)
    page = (FIXTURES / "boardbook_agenda.html").read_text().replace("STAFF AND STUDENT RECOGNITION", "RECOGNITION")
    # The page changed, the agenda didn't: the package is read, nothing saved.
    status, store, client = run(district, tmp_path, FakeBoardBook(agenda_pages={772970: page}))
    assert any("DownloadAgenda/bristol?meeting=772970" in url for url in client.urls)
    assert status["documents_changed"] == 0 and len(store["boardbook-772970"]["agendas"]) == 1
    status, store, _ = run(district, tmp_path, FakeBoardBook(
        agenda_pages={772970: page.replace("RECOGNITION", "RECOGNITIONS")},
        packages={772970: boardbook_package("Board of Education Meeting, revised")}))
    meeting = store["boardbook-772970"]
    assert status["documents_changed"] == 1 and len(meeting["agendas"]) == 2
    assert meeting["history"][-1]["field"] == "agenda"


def test_a_board_whose_minutes_are_elsewhere(district, tmp_path):
    district["boardbook_meetings"]["minutes"] = False
    _, store, client = run(district, tmp_path)
    assert "minutes" not in store["boardbook-723306"] and not any("Minutes" in url for url in client.urls)
    m = {**store["boardbook-723306"], "minutes": []}
    assert absences.minutes_gap(district, m, NOW.date())["reason"] == "not_collected"


def test_documents_limited_per_run(district, tmp_path):
    district["boardbook_meetings"]["max_documents_per_run"] = 2
    status, store, _ = run(district, tmp_path)
    assert status["meetings_waiting"] > 0
    assert store["boardbook-772970"].get("agendas") and not store["boardbook-711426"].get("agendas")
    run(district, tmp_path)
    run(district, tmp_path)
    _, store, _ = run(district, tmp_path)
    assert store["boardbook-711426"].get("agendas")


def test_an_upcoming_meeting_that_leaves_the_page(district, tmp_path):
    run(district, tmp_path)
    page = (FIXTURES / "boardbook_bristol.html").read_text().replace("meeting=772970", "meeting=1")
    _, store, _ = run(district, tmp_path, FakeBoardBook(page=page))
    assert store["boardbook-772970"]["listed"] is False


def test_a_package_that_isnt_a_pdf(district, tmp_path):
    status, store, _ = run(district, tmp_path, FakeBoardBook(packages={772970: b"<html>Maintenance</html>"}))
    assert any("772970" in e for e in status["errors"]) and "agendas" not in store["boardbook-772970"]
    _, store, _ = run(district, tmp_path)
    assert store["boardbook-772970"]["agendas"]


def test_a_page_without_its_table(district, tmp_path):
    with pytest.raises(FetchError):
        run(district, tmp_path, FakeBoardBook(page="<html><body>Nightly maintenance</body></html>"))


# ---- The config ---------------------------------------------------------------------------

@pytest.mark.parametrize("settings, message", [
    ({"source_name": "X", "since": "2026-01-01", "body": "B"}, "needs page_url"),
    ({**bristol(), "page_url": "https://meetings.boardbook.org/Public/Agenda/bristol"}, "page_url must be"),
    ({**bristol(), "since": "January"}, "since must be a date"),
    ({**bristol(), "bodies": {}, "body": None}, "needs bodies, body, or both"),
    ({**bristol(), "bodies": {"X": ""}}, "bodies must map"),
    ({**bristol(), "exclude_pattern": "("}, "exclude_pattern isn't"),
    ({**bristol(), "minutes": "no"}, "minutes must be true or false"),
    ({**bristol(), "max_documents_per_run": 0}, "max_documents_per_run must be"),
])
def test_config_check(settings, message):
    with pytest.raises(SystemExit, match=message):
        boardbook.check({"slug": "bristol", "boardbook_meetings": settings})


def test_config_check_passes():
    boardbook.check({"slug": "bristol", "boardbook_meetings": bristol()})


# ---- The site -----------------------------------------------------------------------------

def test_site_shows_the_districts_meetings(config, data_dir, tmp_path, monkeypatch):
    import shutil

    from conftest import BUILT_AT

    from pipeline import build_site
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    town = copy.deepcopy(config)
    town["boardbook_meetings"] = {**bristol(), "minutes": False}
    fetch_boardbook_meetings.run(town, FakeBoardBook(), data, now=BUILT_AT)
    monkeypatch.setattr(build_site, "load_config", lambda slug: town)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    shown = {m["id"]: m for m in build_site.load_meetings(data, BUILT_AT.date())["all"]}
    page = (out / "meetings" / shown["boardbook-773033"]["slug"] / "index.html").read_text()
    assert "Agenda posted on BoardBook" in page
    assert "This meeting on the Bristol Public Schools meeting portal<" in page and "</a> (BoardBook)." in page
    past = (out / "meetings" / shown["boardbook-766802"]["slug"] / "index.html").read_text()
    assert "This site doesn't collect this board's minutes. They may be on the <a" in past
    about = (out / "about" / "index.html").read_text()
    assert ("<strong>Board of Education meetings and agendas:</strong> Bristol Public Schools, "
            f'from its <a href="{FakeBoardBook.PAGE}"') in about
    board = (out / "meetings" / "boards" / "board-of-education-student-achievement-committee" / "index.html").read_text()
    assert f'Earlier meetings are on the <a href="{FakeBoardBook.PAGE}"' in board
