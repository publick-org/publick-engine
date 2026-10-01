"""A school board's meetings, agendas and minutes from its Finalsite page (Wallingford
Public Schools' Board of Education, saved and trimmed: tests/fixtures/finalsite_*)."""

import copy
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeFinalsite

from pipeline import fetch_finalsite_meetings, fetch_meetings, fetch_minutes, finalsite
from pipeline.fetch_meetings import load_store

PAGE_URL = FakeFinalsite.PAGE_URL
NOW = datetime(2026, 10, 1, 7, 0, tzinfo=TZ)
BODIES = {"Board of Education": "Board of Education",
          "Operations Committee": "Board of Education Operations Committee",
          "Instructional Committee": "Board of Education Instructional Committee",
          "Policy Committee": "Board of Education Policy Committee"}
MINUTES_DOC = "1eCn55rIhzAZxKDbjcrS7dwkf9mn04t46akSJUxGBamo"
AGENDA_1709 = "1NKRJDk9dlUiZD-NOdlJ4MuAo5mPJGQZV8HGq_lKqv6U"


def finalsite_meetings() -> dict:
    """[finalsite_meetings] as Wallingford's config has it, from mid-July."""
    return {"page_url": PAGE_URL, "source_name": "Wallingford Public Schools", "since": "2026-07-15",
            "bodies": dict(BODIES)}


@pytest.fixture
def district(config):
    town = copy.deepcopy(config)
    town["finalsite_meetings"] = finalsite_meetings()
    return town


def post(post_id: str) -> dict:
    return finalsite.parse_post((FIXTURES / f"finalsite_post_{post_id}.html").read_text(encoding="utf-8"), PAGE_URL)


def run(town, tmp_path, client=None, now=NOW):
    client = client or FakeFinalsite()
    status = fetch_finalsite_meetings.run(town, client, tmp_path, now=now)
    return status, load_store(tmp_path), client


# ---- The page and its posts -----------------------------------------------------------

def test_posts_on_the_board_page():
    page = (FIXTURES / "finalsite_board.html").read_text(encoding="utf-8")
    posts = finalsite.parse_board(page)
    assert [p["post_id"] for p in posts] == ["1715", "1709", "1707", "1698", "1684", "1664", "1658"]
    assert posts[1] == {"element": "23192", "post_id": "1709", "title": "September 28, 2026 - Board of Education Meeting",
                        "slug": "board-of-education-meetings/post/september-28-2026-board-of-education-meeting"}
    assert finalsite.site_name(page) == "Wallingford Public Schools"
    assert not finalsite.more_pages(page)
    assert finalsite.post_url(PAGE_URL, "23192", "1709") == (
        "https://www.wallingford.k12.ct.us/fs/elements/23192?is_popup=true&post_id=1709&show_post=true&is_draft=false")


@pytest.mark.parametrize("title, expected", [
    ("September 28, 2026 - Board of Education Meeting", ("2026-09-28", "Board of Education", "scheduled", False)),
    ("September 30, 2026 - Special Board of Education Meeting", ("2026-09-30", "Board of Education", "scheduled", True)),
    ("Canceled - September 22, 2026 - Special Board of Education Meeting",
     ("2026-09-22", "Board of Education", "cancelled", True)),
    ("Canceled - September 3, 2026 - Special Board of Education Meeting Agenda",
     ("2026-09-03", "Board of Education", "cancelled", True)),
    ("September 14, 2026 - Operations Committee Meeting",
     ("2026-09-14", "Board of Education Operations Committee", "scheduled", False)),
    ("September 14, 2026 - Instructional Committee Meeting",
     ("2026-09-14", "Board of Education Instructional Committee", "scheduled", False)),
    ("August 11, 2026 - Policy Committee Meeting", ("2026-08-11", "Board of Education Policy Committee", "scheduled", False)),
    # The date last, or written short, as other districts might.
    ("Board of Education Meeting - October 26, 2026", ("2026-10-26", "Board of Education", "scheduled", False)),
    ("Postponed - Oct. 19, 2026 - Operations Committee",
     ("2026-10-19", "Board of Education Operations Committee", "postponed", False)),
])
def test_titles(title, expected):
    info = finalsite.parse_title(title, BODIES)
    assert (info["date"], info["body"], info["status"], info["special"]) == expected


@pytest.mark.parametrize("title", [
    "September 21, 2026 - Superintendent Search Community Forum",  # no listed board
    "Board of Education Meeting",  # no date
    "Board of Education Meeting Schedule 2026-2027",
])
def test_titles_not_read(title):
    assert finalsite.parse_title(title, BODIES) is None


def test_a_posts_links():
    committee = finalsite.meeting_fields(post("1698"))
    assert committee == {
        "video_id": "gkpJoSPfmhs",
        "agenda_url": "https://docs.google.com/document/d/1Sr_DRweOtGCK8GzBYUPYsa-5RybZSaUIgQeUcnIzMpw/edit?usp=sharing",
        "minutes_url": f"https://docs.google.com/document/d/{MINUTES_DOC}/edit?usp=sharing",
        "documents_url": "https://docs.google.com/document/d/1Sr_DRweOtGCK8GzBYUPYsa-5RybZSaUIgQeUcnIzMpw/edit?usp=sharing",
        "links": [{"url": "https://drive.google.com/drive/folders/1GchPp9WIyjrBcK2eMO8qVXEdd6wBJ-wh?usp=sharing",
                   "text": "Backup materials"}]}
    board = finalsite.meeting_fields(post("1709"))
    # No recording linked yet ("Video" with no link); the backup folder, a presentation and the
    # motions are links only.
    assert "video_id" not in board and "minutes_url" not in board
    assert [link["text"] for link in board["links"]] == [
        "Backup Folder", "ASTE Proposed Education Specifications Presentation - One New Wallingford High School", "Motions"]
    assert post("1707")["title"] == "Canceled - September 22, 2026 - Special Board of Education Meeting"


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/watch?v=gkpJoSPfmhs", {"video_id": "gkpJoSPfmhs"}),
    ("https://youtu.be/lqLMgiHH-C8?t=95", {"video_id": "lqLMgiHH-C8", "video_start": 95}),
    ("https://www.youtube.com/live/lqLMgiHH-C8?si=abc", {"video_id": "lqLMgiHH-C8"}),
    ("https://www.youtube.com/wpsconnections/live", None),  # the district's channel, not a recording
    ("https://docs.google.com/document/d/1Sr_DRweOtGCK8GzBYUPYsa-5RybZSaUIgQeUcnIzMpw/edit", None),
])
def test_recordings(url, expected):
    assert finalsite.recording(url) == expected


@pytest.mark.parametrize("url, label, kind", [
    ("https://docs.google.com/document/d/1Sr_DRweOtGCK8GzBYUPYsa-5RybZSaUIgQeUcnIzMpw/edit", "Revised Agenda", "agenda"),
    ("https://drive.google.com/file/d/1cXsiDs4Em3bpxIU8kaMY40JYoeVZ7h54/view", "Draft Minutes", "minutes"),
    ("https://www.example.k12.ct.us/uploaded/agenda-9-28.pdf", "Agenda", "agenda"),
    ("https://docs.google.com/document/d/1Sr_DRweOtGCK8GzBYUPYsa-5RybZSaUIgQeUcnIzMpw/edit", "Agenda Addendum", None),
    ("https://docs.google.com/document/d/1Sr_DRweOtGCK8GzBYUPYsa-5RybZSaUIgQeUcnIzMpw/edit", "Motions", None),
    # A folder is never a document, whatever its label.
    ("https://drive.google.com/drive/folders/1GchPp9WIyjrBcK2eMO8qVXEdd6wBJ-wh", "Agenda and backup", None),
])
def test_agendas_and_minutes_by_label(url, label, kind):
    fields = finalsite.meeting_fields({"links": [{"url": url, "text": label}]})
    assert {k for k in fields if k in ("agenda_url", "minutes_url")} == ({f"{kind}_url"} if kind else set())


def test_an_agenda_published_to_the_web_is_linked():
    """A Doc "published to the web" has no PDF export: it is the agenda's link, not saved."""
    published = "https://docs.google.com/document/d/e/2PACX-1vTH__qSvlEuZVSPX72n8t-01vO1rtSnLQfo2Zd9LPD82B2cZppxgZ/pub"
    fields = finalsite.meeting_fields({"links": [{"url": published, "text": "Agenda"}]})
    assert "agenda_url" not in fields and fields["documents_url"] == published
    assert fields["links"] == [{"url": published, "text": "Agenda"}]


def test_download_addresses():
    assert finalsite.download_url(f"https://docs.google.com/document/d/{MINUTES_DOC}/edit?usp=sharing") == (
        f"https://docs.google.com/document/d/{MINUTES_DOC}/export?format=pdf")
    assert finalsite.download_url("https://drive.google.com/file/d/1cXsiDs4Em3bpxIU8kaMY40JYoeVZ7h54/view?usp=sharing") == (
        "https://drive.google.com/uc?export=download&id=1cXsiDs4Em3bpxIU8kaMY40JYoeVZ7h54")
    assert finalsite.original_filename("attachment; filename=\"AGENDA.pdf\"; "
                                       "filename*=UTF-8''AGENDA%20SEPTEMBER%2030%2C%202026%20.pdf") == "AGENDA SEPTEMBER 30, 2026 .pdf"


def test_an_exported_doc_has_a_text_layer():
    assert fetch_meetings.pdf_has_text((FIXTURES / "finalsite_doc.pdf").read_bytes()) is True


# ---- Runs ---------------------------------------------------------------------------------

def test_first_run_records_meetings_since_the_start_date(district, tmp_path):
    status, store, client = run(district, tmp_path)
    assert sorted((m["date"], m["body"], m["status"], m["special"]) for m in store.values()) == [
        ("2026-07-27", "Board of Education", "scheduled", False),
        ("2026-09-03", "Board of Education", "cancelled", True),
        ("2026-09-14", "Board of Education Operations Committee", "scheduled", False),
        ("2026-09-22", "Board of Education", "cancelled", True),
        ("2026-09-28", "Board of Education", "scheduled", False),
        ("2026-09-30", "Board of Education", "scheduled", True),
    ]
    # One request for the page, one for each post since `since`, one for each document.
    assert client.urls[0] == PAGE_URL
    assert sum("/fs/elements/" in u for u in client.urls) == 6 and not any("post_id=1658" in u for u in client.urls)
    assert status["requests"] == 1 + 6 + 5 == len(client.urls)
    assert (status["posts_listed"], status["meetings_created"], status["documents_added"]) == (7, 6, 5)

    committee = store["finalsite-1698"]
    assert committee["slug"] == "2026-09-14-board-of-education-operations-committee"
    assert committee["title"] == "Board of Education Operations Committee Meeting"
    assert committee["posted_title"] == "September 14, 2026 - Operations Committee Meeting"
    assert (committee["source"], committee["source_url"], committee["source_name"]) == (
        "finalsite", PAGE_URL, "Wallingford Public Schools")
    assert committee["video_id"] == "gkpJoSPfmhs" and committee["start_time"] is None
    minutes = committee["minutes"][0]
    assert minutes["source_url"] == f"https://docs.google.com/document/d/{MINUTES_DOC}/edit?usp=sharing"
    assert minutes["id"].startswith(f"{MINUTES_DOC}-") and minutes["has_text"] is True
    assert (minutes["posted_by"], minutes["posted_on"]) == ("Wallingford Public Schools", "Google Docs")
    assert minutes["original_filename"] == "AGENDA SEPTEMBER 30, 2026 .pdf"
    assert (tmp_path / "meetings" / "minutes" / minutes["file"]).exists()
    assert (tmp_path / "meetings" / "agendas" / committee["agendas"][0]["file"]).exists()

    board = store["finalsite-1709"]
    assert [link["text"] for link in board["links"]][-1] == "Motions" and "minutes" not in board
    # A cancelled meeting's agenda is linked, not saved.
    cancelled = store["finalsite-1707"]
    assert "agendas" not in cancelled and cancelled["documents_url"] == cancelled["agenda_url"]
    assert not any(m.get("history") for m in store.values())


def test_later_runs_read_only_new_and_recent_posts(district, tmp_path):
    run(district, tmp_path)
    status, store, client = run(district, tmp_path)
    # The page; the two recent meetings without minutes; the recent minutes, exported again
    # and found unchanged.
    assert client.urls == [PAGE_URL, finalsite.post_url(PAGE_URL, "23192", "1715"),
                           finalsite.post_url(PAGE_URL, "23192", "1709"),
                           f"https://docs.google.com/document/d/{MINUTES_DOC}/export?format=pdf"]
    assert (status["meetings_created"], status["documents_added"], status["documents_changed"]) == (0, 0, 0)
    assert status["documents_rechecked"] == 1
    assert len(store["finalsite-1698"]["minutes"]) == 1 and not any(m.get("history") for m in store.values())


def test_minutes_posted_later(district, tmp_path):
    run(district, tmp_path)
    body = (FIXTURES / "finalsite_post_1709.html").read_text(encoding="utf-8").replace(
        "</div>\n</article>", '<p><a href="https://docs.google.com/document/d/minutes-of-september-28-000/edit">'
        'Minutes</a></p>\n<p><a href="https://www.youtube.com/watch?v=Zx9aB8cD7eF">Video</a></p></div>\n</article>')
    status, store, _ = run(district, tmp_path, FakeFinalsite(posts={"1709": body}))
    board = store["finalsite-1709"]
    assert board["minutes"][0]["id"].startswith("minutes-of-september-28-000-") and board["video_id"] == "Zx9aB8cD7eF"
    assert status["documents_added"] == 1
    # With minutes, its post isn't read again.
    _, _, client = run(district, tmp_path, FakeFinalsite(posts={"1709": body}))
    assert not any("post_id=1709" in u for u in client.urls)


def test_a_doc_edited_in_place_is_a_new_version(district, tmp_path):
    run(district, tmp_path)
    # The draft minutes approved: the same Doc, new content.
    status, store, _ = run(district, tmp_path, FakeFinalsite(edits={MINUTES_DOC: "approved"}))
    minutes = store["finalsite-1698"]["minutes"]
    assert len(minutes) == 2 and minutes[0]["sha256"] != minutes[1]["sha256"]
    assert store["finalsite-1698"]["history"] == [
        {"at": NOW.isoformat(), "field": "minutes", "old": minutes[0]["id"], "new": minutes[1]["id"]}]
    assert status["documents_changed"] == 1
    # Long after the meeting, the minutes aren't exported again.
    _, _, client = run(district, tmp_path, now=NOW.replace(month=12))
    assert not any("export" in u for u in client.urls)


def test_an_upcoming_agenda_is_checked_until_the_meeting(district, tmp_path):
    before = NOW.replace(day=27, month=9)
    run(district, tmp_path, now=before)
    status, store, client = run(district, tmp_path, FakeFinalsite(edits={AGENDA_1709: "amended"}), now=before)
    assert len(store["finalsite-1709"]["agendas"]) == 2 and store["finalsite-1709"]["history"][0]["field"] == "agenda"
    # The two upcoming agendas and the recent minutes; past meetings' agendas aren't exported again.
    assert status["documents_rechecked"] == 3 and status["documents_changed"] == 1


def test_changes_on_the_page(district, tmp_path):
    before = NOW.replace(day=27, month=9)
    run(district, tmp_path, now=before)
    page = (FIXTURES / "finalsite_board.html").read_text(encoding="utf-8")
    start = page.index('<article class="fsStyleAutoclear fsBoard-30" data-post-id="1715"')
    page = page[:start] + page[page.index("</article>", start) + len("</article>"):]
    page = page.replace("September 28, 2026 - Board of Education Meeting", "Canceled - September 28, 2026 - Board of Education Meeting")
    page = page.replace("July 27, 2026 - Board of Education Meeting", "September 21, 2026 - Superintendent Search Forum")
    status, store, _ = run(district, tmp_path, FakeFinalsite(page=page), now=before)
    # Taken off the page before it happened: kept, as no longer listed.
    removed = store["finalsite-1715"]
    assert removed["listed"] is False and removed["history"][-1]["field"] == "listed"
    cancelled = store["finalsite-1709"]
    assert cancelled["status"] == "cancelled" and cancelled["history"][-1]["field"] == "status"
    # A title without a listed board is reported, not guessed at, and warned about once.
    assert status["unrecognized_titles"] == status["new_unrecognized_titles"] == ["September 21, 2026 - Superintendent Search Forum"]
    again, _, _ = run(district, tmp_path, FakeFinalsite(page=page), now=before)
    assert again["new_unrecognized_titles"] == []


def test_a_page_without_posts_fails(district, tmp_path):
    with pytest.raises(fetch_finalsite_meetings.FetchError):
        run(district, tmp_path, FakeFinalsite(page="<html><body>Moved</body></html>"))
    assert not (tmp_path / "meetings" / "meetings.json").exists()


def test_next_to_the_towns_own_meetings(district, tmp_path):
    """The Board of Education's meetings and the town website's (pipeline.filelist) in one store:
    their own boards, ids and page addresses, and neither source changes the other's."""
    from test_filelist import FakeTownSite, wallingford_meetings
    district.pop("archive", None)
    district["meetings"] = wallingford_meetings()
    fetch_meetings.run(district, FakeTownSite(), tmp_path, now=NOW)
    run(district, tmp_path)
    fetch_meetings.run(district, FakeTownSite(), tmp_path, now=NOW)
    assert not fetch_minutes.run_linked(district, FakeTownSite(), tmp_path, now=NOW)["errors"]
    store = load_store(tmp_path)
    schools = [m for m in store.values() if m.get("source") == "finalsite"]
    town = [m for m in store.values() if m.get("source") == "filelist"]
    assert len(schools) == 6 and town
    assert not {m["body"] for m in schools} & {m["body"] for m in town}
    assert len({m["slug"] for m in store.values()}) == len(store)
    assert all(m["listed"] and not m.get("history") for m in schools)


@pytest.mark.parametrize("change, message", [
    ({"page_url": None}, "needs page_url"),
    ({"since": "July 2026"}, "since must be a date"),
    ({"bodies": ["Board of Education"]}, "bodies must map"),
    ({"recheck_days": 0}, "recheck_days must be"),
])
def test_config_is_checked(change, message):
    settings = {k: v for k, v in {**finalsite_meetings(), **change}.items() if v is not None}
    with pytest.raises(SystemExit, match=message):
        finalsite.check({"slug": "wallingford", "finalsite_meetings": settings})
