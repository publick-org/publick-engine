"""A city's meetings from its ProudCity website (pipeline/proudcity.py, pipeline/fetch_proudcity_meetings.py),
and the WordPress REST plumbing it reads them through (pipeline/wordpress.py). Run offline against Holyoke's
REST listing and meeting pages, saved in November 2026 and trimmed to each page's main content
(tests/fixtures/proudcity/)."""

import copy
import json
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse
from test_wordfile import docx, p

from pipeline import build_site, fetch_proudcity_meetings, pdftext, proudcity, wordfile, wordpress
from pipeline.config import load_config
from pipeline.http import FetchError

SAVED = FIXTURES / "proudcity"
SITE = "https://www.holyoke.org"
NOW = datetime(2026, 11, 13, 7, 0, tzinfo=TZ)
SETTINGS = {
    "site_url": SITE, "source_name": "City of Holyoke", "since": "2026-06-01",
    "exclude_pattern": "Executive Session|Walkthrough|Retreat|Site Council",
    "bodies": {"City Council": "City Council", "Finance Committee": "City Council Finance Committee",
               "Ordinance Committee": "City Council Ordinance Committee", "School Committee": "School Committee",
               "Policy and Governance": "School Committee Policy and Governance Subcommittee",
               "Parent and Community Engagement": "School Committee Parent and Community Engagement Subcommittee",
               "Planning Board": "Planning Board", "Public Works Commission": "Public Works Commission",
               "License Board": "License Board", "Local Cultural Council": "Cultural Council"},
}

# The saved pages' posts as the REST API lists them: id, page, title as WordPress renders it, and when changed.
POSTS = {
    "council-2026-09-15": ("49960", "holyoke-city-council-meeting-september-15-2026",
                           "Holyoke City Council Meeting September 15, 2026", "2026-10-14T09:12:00"),
    "school-committee-2026-10-13": ("50696", "school-committee-meeting-october-13-2026",
                                    "School Committee Meeting, October 13, 2026", "2026-10-07T15:20:11"),
    "school-committee-2026-09-14": ("50412", "school-committee-meeting-september-14-2026",
                                    "School Committee Meeting, September 14, 2026", "2026-09-09T10:01:40"),
    "policy-governance-2026-06-25": ("49299", "school-committee-policy-and-governance-subcommittee-meeting-june-25-2026",
                                     "School Committee Policy and Governance Subcommittee Meeting, June 25, 2026",
                                     "2026-06-24T12:00:00"),
    "engagement-2026-10-05": ("50593", "school-committee-parent-and-community-engagement-committee-october-5-2025",
                              "School Committee Parent and Community Engagement Committee, October 5, 2025",
                              "2026-09-30T11:00:00"),
    "finance-2026-11-10": ("49999", "finance-committee-meeting-november-10-2026",
                           "Finance Committee Meeting November 10, 2026", "2026-11-06T16:25:59"),
    "cultural-council-cancelled": ("51022", "holyoke-local-cultural-council-meeting-november-10-2026",
                                   "CANCELLED Holyoke Local Cultural Council Meeting November 10, 2026",
                                   "2026-11-10T12:00:13"),
    "ordinance-cancelled": ("49424", "ordinance-committee-meeting-september-29-2026",
                            "Ordinance Committee Meeting September 29, 2026 &#8211; Cancelled due to technical issues",
                            "2026-09-29T17:02:00"),
    "planning-2026-11-10": ("51010", "planning-board-meeting-november-10-2026",
                            "Planning Board Meeting November 10, 2026", "2026-11-10T10:21:59"),
    "public-works-2026-11-16": ("51242", "public-works-commission-meeting-november-16-2026",
                                "Public Works Commission Meeting November 16, 2026", "2026-11-12T16:08:30"),
    "post-election-tabulation": ("51221", "post-election-tabulation", "Post Election Tabulation", "2026-11-10T08:29:06"),
    "license-board-2026-09-02": ("50299", "holyoke-license-board-meeting-september-2-2026",
                                 "Holyoke License Board Meeting September 2, 2026", "2026-11-03T13:38:09"),
}


def page(name: str) -> str:
    return (SAVED / f"{name}.html").read_text()


def page_url(name: str) -> str:
    return f"{SITE}/meetings/{POSTS[name][1]}/"


def listed(name: str, modified: str | None = None, title: str | None = None) -> dict:
    """A post as the listing gives it, published when it was last changed (as the saved posts were, near enough)."""
    post_id, _, saved_title, changed = POSTS[name]
    return {"id": int(post_id), "link": page_url(name), "title": {"rendered": title or saved_title},
            "date": changed, "modified": modified or changed}


class FakeSite:
    """A ProudCity site: its REST listing of the posts given (each a listed() item, the most recently changed
    first, `per_page` to a page), its meeting pages (`pages`, by post id, defaulting to the saved ones),
    its attached files (a Word file for .docx, else a PDF), and Google Docs exported as PDFs."""

    def __init__(self, posts: list[dict], pages: dict | None = None, per_page: int = 100, gone: tuple = ()):
        self.posts = sorted(posts, key=lambda p: p["modified"], reverse=True)
        self.pages = {POSTS[name][0]: page(name) for name in POSTS} | (pages or {})
        self.per_page, self.gone = per_page, set(gone)
        self.pdf = (FIXTURES / "legistar_minutes.pdf").read_bytes()
        self.urls, self.request_count = [], 0

    def listing(self, query: dict) -> FakeResponse:
        if "include" in query:
            ids = set(query["include"][0].split(","))
            found = [{"id": p["id"]} for p in self.posts if str(p["id"]) in ids and str(p["id"]) not in self.gone]
            return FakeResponse(json.dumps(found).encode(), {"X-WP-Total": str(len(found)), "X-WP-TotalPages": "1"})
        after = query.get("modified_after", [""])[0]
        posts = [p for p in self.posts if p["modified"] > after]
        n = int(query.get("page", ["1"])[0])
        pages = max(1, -(-len(posts) // self.per_page))
        assert n <= pages, "asked past the last page"
        chunk = posts[(n - 1) * self.per_page:n * self.per_page]
        fields = query.get("_fields", [""])[0].split(",")
        chunk = [{k: v for k, v in item.items() if k in fields} for item in chunk]
        return FakeResponse(json.dumps(chunk).encode(), {"X-WP-Total": str(len(posts)), "X-WP-TotalPages": str(pages)})

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        parts = urlsplit(url)
        if parts.path == "/wp-json/wp/v2/meetings":
            return self.listing(parse_qs(parts.query))
        if url.startswith(f"{SITE}/meetings/"):
            post = next((str(p["id"]) for p in self.posts if p["link"] == url), None)
            if post is None or post in self.gone:
                raise FetchError(f"{url}: HTTP 404", 404)
            return FakeResponse(self.pages[post].encode())
        if url.endswith(".docx"):
            return FakeResponse(docx(p("Board of Public Works Agenda") + p("Monday, November 16, 2026")))
        if "storage.googleapis.com/proudcity/" in url or "docs.google.com/document/" in url:
            return FakeResponse(self.pdf + f"\n% {url}\n".encode())
        raise AssertionError(f"unexpected URL {url}")


def every_post() -> list[dict]:
    return [listed(name) for name in POSTS]


@pytest.fixture
def config():
    town = copy.deepcopy(load_config("gloucester"))
    for table in ("archive", "drive_meetings", "summaries", "participation", "glossary"):
        town.pop(table, None)
    town["town"].update(name="Holyoke")
    town["proudcity_meetings"] = copy.deepcopy(SETTINGS)
    return town


def load(data_dir):
    return json.loads((data_dir / "meetings" / "meetings.json").read_text())


def status_of(data_dir):
    return json.loads((data_dir / "meetings" / "proudcity_status.json").read_text())


def meeting(data_dir, name: str) -> dict:
    return load(data_dir)[f"proudcity-{POSTS[name][0]}"]


# ---- The WordPress REST API ---------------------------------------------------------------

def test_api_url():
    assert (wordpress.api_url("https://www.holyoke.org/", "/wp/v2/meetings", {"per_page": 100, "page": None})
            == "https://www.holyoke.org/wp-json/wp/v2/meetings?per_page=100")
    assert wordpress.api_url(SITE, "tribe/events/v1/events") == f"{SITE}/wp-json/tribe/events/v1/events"


def test_total_pages_from_either_header_or_the_body():
    assert wordpress.total_pages({"X-WP-TotalPages": "33"}) == 33
    assert wordpress.total_pages({"x-wp-totalpages": "1"}) == 1
    # The Events Calendar's own header, and the count in its body.
    assert wordpress.total_pages({"X-TEC-TotalPages": "51"}) == 51
    assert wordpress.total_pages({}, {"events": [], "total": 151, "total_pages": 51}) == 51
    assert wordpress.total_pages({}, []) is None


class Pages:
    """A listing of `count` items, `size` to a page, with The Events Calendar's shape (`key`) or a core route's."""

    def __init__(self, count: int, size: int, key: str | None = None, header: str = "X-WP-TotalPages"):
        self.count, self.size, self.key, self.header, self.urls = count, size, key, header, []

    def get(self, url):
        self.urls.append(url)
        n = int(parse_qs(urlsplit(url).query).get("page", ["1"])[0])
        pages = -(-self.count // self.size)
        assert n <= pages, "asked past the last page"
        items = [{"id": i} for i in range((n - 1) * self.size, min(n * self.size, self.count))]
        body = {self.key: items, "total": self.count, "total_pages": pages} if self.key else items
        return FakeResponse(json.dumps(body).encode(), {self.header: str(pages)})


def test_read_all_reads_each_page_once_and_none_past_the_last():
    client = Pages(250, 100)
    assert [i["id"] for i in wordpress.read_all(client, SITE, "wp/v2/meetings", {"orderby": "modified"})] == list(range(250))
    assert len(client.urls) == 3 and "&page=" not in client.urls[0] and client.urls[2].endswith("page=3")
    assert "per_page=100" in client.urls[0]


def test_read_all_reads_the_events_calendars_listing():
    """The Events Calendar's route: its items under "events", its page count in X-TEC-TotalPages."""
    client = Pages(7, 3, key="events", header="X-TEC-TotalPages")
    found = wordpress.read_all(client, SITE, "tribe/events/v1/events", {"per_page": 3, "start_date": "2026-11-01"},
                               key="events")
    assert len(found) == 7 and len(client.urls) == 3
    # Without fields, no _fields: that route answers _fields=events.id with no events.
    assert not any("_fields" in u for u in client.urls)


def test_read_all_asks_for_only_the_fields_named():
    client = Pages(5, 100)
    wordpress.read_all(client, SITE, "wp/v2/meetings", fields=("id", "link"))
    assert "_fields=id%2Clink" in client.urls[0]


def test_a_listing_longer_than_allowed_is_an_error_not_a_part():
    client = Pages(5000, 100)
    with pytest.raises(FetchError, match="50 pages, more than the 10"):
        wordpress.read_all(client, SITE, "wp/v2/meetings", max_pages=10)
    assert len(client.urls) == 1


def test_a_page_where_json_should_be_is_an_error():
    class Off:
        def get(self, url):
            return FakeResponse(b"<html>Forbidden</html>", {})
    with pytest.raises(FetchError, match="not JSON"):
        wordpress.read_all(Off(), SITE, "wp/v2/meetings")


def test_changed_since_the_last_run_less_two_days():
    assert wordpress.changed_since("2026-11-13T07:00:00-05:00") == "2026-11-11T07:00:00"
    assert wordpress.changed_since("2026-11-13T07:00:00") == "2026-11-11T07:00:00"
    assert wordpress.changed_since(None) is None


def test_rendered_titles_are_plain_text():
    assert wordpress.rendered({"rendered": "Holyoke Gas &#038; Electric Commission Meeting &#8211; November 10, 2026"}) \
        == "Holyoke Gas & Electric Commission Meeting – November 10, 2026"
    assert wordpress.rendered("​H​olyoke ​License ​B​oard") == "Holyoke License Board"


# ---- The listing --------------------------------------------------------------------------

def test_parse_listing_of_the_saved_rest_page():
    posts = proudcity.parse_listing(json.loads((SAVED / "listing.json").read_text()))
    assert len(posts) == 51
    by_id = {p["id"]: p for p in posts}
    assert by_id["51242"] == {"id": "51242", "title": "Public Works Commission Meeting November 16, 2026",
                              "link": f"{SITE}/meetings/public-works-commission-meeting-november-16-2026/",
                              "posted": "", "modified": "2026-11-12T16:08:30", "title_date": "2026-11-16"}
    assert by_id["51042"]["title"] == "Holyoke Gas & Electric Commission Meeting – November 10, 2026"
    # Typed in an editor with zero-width spaces between its letters.
    assert by_id["47957"]["title"] == "Holyoke License Board Meeting March 17, 2026"
    assert by_id["51221"]["title_date"] is None


@pytest.mark.parametrize("title_date, posted, before", [
    ("2026-05-20", "2026-05-15", True),     # dated before since, posted that year
    ("2026-06-01", "2026-05-25", False),    # on since
    ("2025-10-05", "2026-09-30", False),    # a year behind the year it was posted: maybe mistyped, so read
    ("2025-12-15", "2025-12-10", True),
    (None, "2026-01-02", False),            # no date in the title: read
])
def test_a_post_before_since_by_its_title(title_date, posted, before):
    assert proudcity.before({"title_date": title_date, "posted": posted}, "2026-06-01") is before


def test_a_titles_name_without_its_date_or_status():
    assert proudcity.name_of("CANCELLED Holyoke Local Cultural Council Meeting November 10, 2026") == \
        "Holyoke Local Cultural Council Meeting"
    assert proudcity.name_of("Holyoke Water Works Commission Meeting January 27,2026") == \
        "Holyoke Water Works Commission Meeting"


def test_the_address_is_the_headers_line_only():
    header = page("planning-2026-11-10").replace('<h6 class="margin-top-smaller">,</h6>', "")
    found = proudcity.parse_meeting(header + '<h3>Other</h3>\n<h6>Somewhere else</h6>', page_url("planning-2026-11-10"))
    assert found["location"] == "Remote by Zoom"


def test_the_listing_asks_for_what_changed_most_recent_first():
    params = proudcity.listing_params("2026-11-11T07:00:00")
    assert params == {"orderby": "modified", "order": "desc", "modified_after": "2026-11-11T07:00:00"}


# ---- A meeting's page ---------------------------------------------------------------------

def test_a_council_meeting_page():
    found = proudcity.parse_meeting(page("council-2026-09-15"), page_url("council-2026-09-15"))
    assert found["title"] == "Holyoke City Council Meeting September 15, 2026"
    assert (found["date"], found["start_time"]) == ("2026-09-15", "18:30")
    assert found["location"] == "City Hall Holyoke, 536 Dwight St, Holyoke 01040"
    assert found["agenda_file"] is None
    assert found["agenda_text"][0] == "Published September 11, 2026, 1:05 p.m."
    assert "AGENDA FOR THE CITY COUNCIL\nTuesday, September 15, 2026\n6:30 PM" in found["agenda_text"]
    assert found["minutes_file"]["file"] == "item-3-september-15-2026-city-council-meeting-minutes-c2437926.pdf"
    assert found["minutes_file"]["url"].startswith("https://storage.googleapis.com/proudcity/holyokema/2026/04/")
    # The English video embedded in the Video tab; the Spanish one linked in the agenda isn't the recording.
    assert found["video"] == {"video_id": "H1kAe-TpdR4"}
    assert found["links"] == [{"url": "https://drive.google.com/drive/folders/1WYe4AV3aWuu_xSxFM9d72zy76nExpEBP"
                                      "?usp=drive_link", "text": "Link to meeting materials"}]
    assert found["linked_minutes"] == []


def test_the_date_is_the_headers_not_the_titles():
    found = proudcity.parse_meeting(page("engagement-2026-10-05"), page_url("engagement-2026-10-05"))
    assert found["title"].endswith("October 5, 2025") and found["date"] == "2026-10-05"
    assert found["location"] == "Remote by Zoom"


def test_minutes_linked_from_a_later_packet():
    found = proudcity.parse_meeting(page("school-committee-2026-10-13"), page_url("school-committee-2026-10-13"))
    assert [(m["date"], m["label"]) for m in found["linked_minutes"]] == [
        ("2026-09-14", "9.14.26 School Committee Meeting Minutes"),
        ("2026-09-28", "9.28.26 School Committee Meeting Minutes"),
        ("2026-06-25", "6.25.26 Policy and Governance Meeting Minutes")]
    assert all(m["url"].startswith("https://docs.google.com/document/d/") for m in found["linked_minutes"])
    # A dated file that isn't minutes ("10.13.26 Warrant Report") is a packet link only.
    assert any(link["text"] == "10.13.26 Warrant Report" for link in found["links"])
    assert len(found["links"]) == 11


@pytest.mark.parametrize("name, file", [
    ("planning-2026-11-10", "stamped-11-10-26-agenda-b51bc933.pdf"),
    ("public-works-2026-11-16", "11.16.2026-pwc-agenda-6b017dc3.docx"),
    ("license-board-2026-09-02", "sept-1-amended-agenda-20f97873.pdf"),
])
def test_an_agendas_attached_file(name, file):
    found = proudcity.parse_meeting(page(name), page_url(name))
    assert found["agenda_file"]["file"] == file
    # The agenda's text is what's before the file's box, not the box's name and size.
    assert not any(file.rsplit(".", 1)[0] in t or t in ("PDF", "Download") for t in found["agenda_text"])


def test_a_page_with_only_a_notice():
    found = proudcity.parse_meeting(page("post-election-tabulation"), page_url("post-election-tabulation"))
    assert found["date"] == "2026-11-12" and found["agenda_text"][0] == "NOTICE OF POST-ELECTION TABULATION"
    assert found["minutes_file"] is None and found["video"] is None and found["links"] == []


# ---- Boards -------------------------------------------------------------------------------

@pytest.mark.parametrize("title, expected", [
    ("Holyoke City Council Meeting September 15, 2026", ("City Council", "scheduled", False)),
    ("Finance Committee Meeting November 10, 2026", ("City Council Finance Committee", "scheduled", False)),
    ("School Committee Policy and Governance Subcommittee Meeting, June 25, 2026",
     ("School Committee Policy and Governance Subcommittee", "scheduled", False)),
    ("CANCELLED Holyoke Local Cultural Council Meeting November 10, 2026", ("Cultural Council", "cancelled", False)),
    ("Ordinance Committee Meeting September 29, 2026 – Cancelled due to technical issues",
     ("City Council Ordinance Committee", "cancelled", False)),
    ("Cancelled-School Committee Parent and Community Engagement Committee, September 3, 2026",
     ("School Committee Parent and Community Engagement Subcommittee", "cancelled", False)),
    ("Special City Council Meeting October 1, 2026", ("City Council", "scheduled", True)),
])
def test_board_status_and_special_from_the_title(title, expected):
    info = proudcity.meeting_info(title, SETTINGS)
    assert (info["body"], info["status"], info["special"]) == expected


def test_titles_left_out_or_with_no_board():
    assert proudcity.meeting_info("School Committee Executive Session Meeting, September 4, 2026", SETTINGS) is None
    assert proudcity.meeting_info("Post Election Tabulation", SETTINGS) is None
    assert proudcity.board_for("Post Election Tabulation", SETTINGS) is None
    assert proudcity.board_for("Post Election Tabulation", {**SETTINGS, "body": "City Clerk"}) == "City Clerk"


def test_the_board_a_linked_minutes_label_names():
    assert proudcity.linked_board("6.25.26 Policy and Governance Meeting Minutes", SETTINGS) == \
        "School Committee Policy and Governance Subcommittee"
    assert proudcity.linked_board("9.14.26 School Committee Meeting Minutes", SETTINGS) == "School Committee"
    assert proudcity.linked_board("Minutes of 9.14.26", SETTINGS) is None


@pytest.mark.parametrize("change, message", [
    ({"site_url": ""}, "needs site_url"),
    ({"site_url": "https://www.holyoke.org/meetings/"}, "site_url must be the website's address"),
    ({"since": "July"}, "since must be a date"),
    ({"bodies": {}}, "needs bodies, body, or both"),
    ({"bodies": {"Council": ""}}, "bodies must map"),
    ({"exclude_pattern": "("}, "isn't a regular expression"),
    ({"max_pages_per_run": 0}, "max_pages_per_run must be a whole number"),
    ({"minutes": "yes"}, "minutes must be true or false"),
])
def test_check(change, message):
    with pytest.raises(SystemExit, match=message):
        proudcity.check({"slug": "holyoke", "proudcity_meetings": {**SETTINGS, **change}})
    proudcity.check({"slug": "holyoke", "proudcity_meetings": SETTINGS})


# ---- An agenda typed into the page ---------------------------------------------------------

def test_the_same_paragraphs_make_the_same_pdf():
    """No creation date in the file, so an agenda read again unchanged isn't saved again."""
    one = wordfile.text_pdf(["AGENDA", "1. Call to order"], "test")
    assert one == wordfile.text_pdf(["AGENDA", "1. Call to order"], "test")
    assert "Call to order" in pdftext.plain_text(one)


# ---- Collecting ---------------------------------------------------------------------------

def test_first_run_records_meetings_and_saves_their_documents(config, tmp_path):
    client = FakeSite(every_post())
    status = fetch_proudcity_meetings.run(config, client, tmp_path, now=NOW)
    assert status["errors"] == []
    # The first run lists what changed from a year before `since` on.
    assert "modified_after=2025-05-31T00%3A00%3A00" in client.urls[0]
    assert "_fields=id%2Clink%2Ctitle%2Cdate%2Cmodified" in client.urls[0]
    council = meeting(tmp_path, "council-2026-09-15")
    assert council["source"] == "proudcity" and council["source_name"] == "City of Holyoke"
    assert (council["body"], council["date"], council["start_time"]) == ("City Council", "2026-09-15", "18:30")
    assert council["location"] == "City Hall Holyoke, 536 Dwight St, Holyoke 01040"
    assert council["documents_url"] == page_url("council-2026-09-15") and council["video_id"] == "H1kAe-TpdR4"
    # The agenda typed into the page, saved as a PDF of its text; the minutes, the attached PDF.
    agenda = council["agendas"][0]
    assert agenda["converted_from"] == "html" and agenda["source_url"] == page_url("council-2026-09-15")
    saved = (tmp_path / "meetings" / "agendas" / agenda["file"]).read_bytes()
    assert "AGENDA FOR THE CITY COUNCIL" in pdftext.plain_text(saved)
    assert council["minutes"][0]["original_filename"].startswith("item-3-september-15-2026-city-council-meeting-minutes")
    # An attached PDF agenda, and a Word one saved as a PDF of its text.
    assert meeting(tmp_path, "planning-2026-11-10")["agendas"][0]["original_filename"] == "stamped-11-10-26-agenda-b51bc933.pdf"
    works = meeting(tmp_path, "public-works-2026-11-16")["agendas"][0]
    assert works["converted_from"] == "docx" and works["has_text"] is True
    # The wrong year in a title doesn't move the meeting.
    assert meeting(tmp_path, "engagement-2026-10-05")["date"] == "2026-10-05"
    # A cancelled meeting is recorded, its agenda not saved.
    cancelled = meeting(tmp_path, "cultural-council-cancelled")
    assert cancelled["status"] == "cancelled" and "agendas" not in cancelled
    assert meeting(tmp_path, "ordinance-cancelled")["status"] == "cancelled"
    # A title with no board is reported, and its page isn't read.
    assert status["unrecognized"] == ["Post Election Tabulation"]
    assert page_url("post-election-tabulation") not in client.urls
    assert status["listed_at"] == NOW.isoformat(timespec="seconds") and status["to_read"] == {}


def test_minutes_linked_from_later_packets_go_to_their_meetings(config, tmp_path):
    client = FakeSite(every_post())
    status = fetch_proudcity_meetings.run(config, client, tmp_path, now=NOW)
    september = meeting(tmp_path, "school-committee-2026-09-14")
    assert september["minutes"][0]["source_url"].startswith("https://docs.google.com/document/d/1_OWokQeN")
    assert september["minutes"][0]["linked_from"] == page_url("school-committee-2026-10-13")
    assert september["minutes"][0]["posted_on"] == "Google Docs"
    assert any(u.endswith("/export?format=pdf") for u in client.urls)
    # A subcommittee's minutes in the School Committee's packet: its label names the subcommittee.
    policy = meeting(tmp_path, "policy-governance-2026-06-25")
    assert policy["minutes"][0]["source_url"].startswith("https://docs.google.com/document/d/1LzHcdnOuPN")
    # Minutes of meetings not recorded are reported, not made into meetings: 9.28 wasn't posted here,
    # and 6.22, 7.13, 6.3 and 8.4 are before the meetings read.
    unmatched = {(m["date"], m["body"]) for m in status["linked_minutes_unmatched"]}
    assert ("2026-09-28", "School Committee") in unmatched
    assert not any(m["date"] == "2026-09-28" for m in load(tmp_path).values())


def test_a_meetings_own_minutes_are_kept_over_linked_ones(config, tmp_path):
    own = page("school-committee-2026-09-14").replace(
        '<div id="tab-agenda-packet"',
        '<div id="tab-minutes" class="tab-pane fade "><a href="https://storage.googleapis.com/proudcity/holyokema/'
        '2026/10/sc-9-14-26-minutes-0a1b2c3d.pdf" class="btn" download="x">Download</a></div>\n<div id="tab-agenda-packet"')
    fetch_proudcity_meetings.run(config, FakeSite(every_post(), {"50412": own}), tmp_path, now=NOW)
    minutes = meeting(tmp_path, "school-committee-2026-09-14")["minutes"]
    assert [m["original_filename"] for m in minutes] == ["sc-9-14-26-minutes-0a1b2c3d.pdf"]


def test_a_later_run_reads_only_what_changed(config, tmp_path):
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    later = NOW.replace(day=14)
    again = FakeSite(every_post())
    status = fetch_proudcity_meetings.run(config, again, tmp_path, now=later)
    # Changed since the last run, less two days: the post changed on the 12th is listed again, but it's
    # unchanged since it was read, so no page is read and nothing downloaded.
    assert "modified_after=2026-11-11T07%3A00%3A00" in again.urls[0]
    assert status["posts_listed"] == 1 and status["pages_read"] == 0
    assert status["documents_added"] == status["documents_changed"] == 0
    assert not any("/meetings/" in u and "wp-json" not in u for u in again.urls)


def test_a_changed_post_is_read_again_and_an_unchanged_agenda_not_saved(config, tmp_path):
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    posts = [p for p in every_post() if p["id"] != 49960] + [listed("council-2026-09-15", "2026-11-13T09:00:00")]
    status = fetch_proudcity_meetings.run(config, FakeSite(posts), tmp_path, now=NOW.replace(day=14))
    assert status["pages_read"] == 1 and status["documents_added"] == status["documents_changed"] == 0
    assert len(meeting(tmp_path, "council-2026-09-15")["agendas"]) == 1


def test_a_changed_agenda_is_saved_as_a_new_version(config, tmp_path):
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    edited = page("finance-2026-11-10").replace("Published November 6, 2026", "Amended November 9, 2026")
    posts = [p for p in every_post() if p["id"] != 49999] + [listed("finance-2026-11-10", "2026-11-13T09:00:00")]
    status = fetch_proudcity_meetings.run(config, FakeSite(posts, {"49999": edited}), tmp_path, now=NOW.replace(day=14))
    assert status["documents_changed"] == 1
    finance = meeting(tmp_path, "finance-2026-11-10")
    assert len(finance["agendas"]) == 2 and finance["history"][-1]["field"] == "agenda"


def test_minutes_added_to_a_post_are_saved(config, tmp_path):
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    with_minutes = page("finance-2026-11-10").replace(
        '<div id="tab-agenda-packet"',
        '<div id="tab-minutes" class="tab-pane fade "><a href="https://storage.googleapis.com/proudcity/holyokema/'
        '2026/12/finance-11-10-26-minutes-9f8e7d6c.pdf" class="btn" download="x">Download</a></div>\n'
        '<div id="tab-agenda-packet"')
    posts = [p for p in every_post() if p["id"] != 49999] + [listed("finance-2026-11-10", "2026-12-20T09:00:00")]
    status = fetch_proudcity_meetings.run(config, FakeSite(posts, {"49999": with_minutes}), tmp_path,
                                          now=NOW.replace(month=12, day=21))
    assert status["documents_added"] == 1
    assert meeting(tmp_path, "finance-2026-11-10")["minutes"][0]["original_filename"] == \
        "finance-11-10-26-minutes-9f8e7d6c.pdf"


def test_an_agenda_typed_in_weeks_ahead_waits_for_the_post_to_be_filled_in(config, tmp_path):
    """A post made weeks ahead holds the meeting's notice; the agenda typed into it is saved once the
    meeting is a week away, when the clerk's edit has the page read again. An attached file is saved."""
    early = NOW.replace(year=2026, month=8, day=1)
    council = [listed("council-2026-09-15", "2026-07-24T10:00:00"), listed("planning-2026-11-10", "2026-07-24T10:00:00")]
    fetch_proudcity_meetings.run(config, FakeSite(council), tmp_path, now=early)
    assert "agendas" not in meeting(tmp_path, "council-2026-09-15")
    assert meeting(tmp_path, "planning-2026-11-10")["agendas"][0]["original_filename"].endswith(".pdf")
    filled = [listed("council-2026-09-15", "2026-09-11T13:05:00"), council[1]]
    fetch_proudcity_meetings.run(config, FakeSite(filled), tmp_path, now=early.replace(month=9, day=11, hour=14))
    assert meeting(tmp_path, "council-2026-09-15")["agendas"][0]["converted_from"] == "html"


def test_a_board_named_later_has_its_posts_read(config, tmp_path):
    """A post with no board isn't read, and is read once bodies names its board, though the city never edits it."""
    bodies = dict(SETTINGS["bodies"])
    del bodies["License Board"]
    config["proudcity_meetings"]["bodies"] = bodies
    status = fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    assert status["unrecognized"] == ["Holyoke License Board Meeting", "Post Election Tabulation"]
    assert f"proudcity-{POSTS['license-board-2026-09-02'][0]}" not in load(tmp_path)
    config["proudcity_meetings"]["bodies"] = SETTINGS["bodies"]
    client = FakeSite(every_post())
    status = fetch_proudcity_meetings.run(config, client, tmp_path, now=NOW.replace(day=14))
    assert status["unrecognized"] == ["Post Election Tabulation"] and status["new_unrecognized"] == []
    assert meeting(tmp_path, "license-board-2026-09-02")["body"] == "License Board"
    assert page_url("license-board-2026-09-02") in client.urls


def test_linked_minutes_of_what_exclude_pattern_leaves_out_are_not_taken(config, tmp_path):
    packet = page("school-committee-2026-10-13").replace("9.14.26 School Committee Meeting Minutes",
                                                          "9.14.26 School Committee Executive Session Minutes")
    fetch_proudcity_meetings.run(config, FakeSite(every_post(), {"50696": packet}), tmp_path, now=NOW)
    assert "minutes" not in meeting(tmp_path, "school-committee-2026-09-14")


def test_a_linked_minutes_download_that_fails_is_tried_again(config, tmp_path):
    class Failing(FakeSite):
        def __init__(self, *args, fail=True, **kwargs):
            super().__init__(*args, **kwargs)
            self.fail = fail

        def get(self, url):
            if self.fail and "1_OWokQeN" in url:
                self.urls.append(url)
                raise FetchError(f"{url}: HTTP 503", 503)
            return super().get(url)
    status = fetch_proudcity_meetings.run(config, Failing(every_post()), tmp_path, now=NOW)
    assert any("HTTP 503" in e for e in status["errors"])
    assert "minutes" not in meeting(tmp_path, "school-committee-2026-09-14")
    assert any(link.get("tries") == 1 for link in status_of(tmp_path)["linked_minutes"].values())
    # The next run, with no page read again, tries it once more.
    fetch_proudcity_meetings.run(config, Failing(every_post(), fail=False), tmp_path, now=NOW.replace(day=14))
    assert meeting(tmp_path, "school-committee-2026-09-14")["minutes"][0]["source_url"].startswith(
        "https://docs.google.com/document/d/1_OWokQeN")


def test_the_document_cap_leaves_the_rest_for_the_next_run(config, tmp_path):
    config["proudcity_meetings"]["max_documents_per_run"] = 2
    status = fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    assert status["documents_added"] + status["documents_changed"] <= 3
    assert status["meetings_waiting"] == len(status["to_read"]) > 0
    # Newest first: the meeting of November 16 was read; what's left is older.
    assert "public-works" not in json.dumps(status["to_read"])
    assert status["waiting_newest"] <= "2026-11-16"
    following = fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW.replace(hour=8))
    assert following["pages_read"] > 0 and len(following["to_read"]) < len(status["to_read"])


def test_the_page_cap(config, tmp_path):
    config["proudcity_meetings"]["max_pages_per_run"] = 1
    status = fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    assert status["pages_read"] == 1 and len(status["to_read"]) == len(POSTS) - 2   # one read, one with no board


def test_an_upcoming_meeting_whose_post_is_gone(config, tmp_path):
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    gone = FakeSite(every_post(), gone=("51242",))
    fetch_proudcity_meetings.run(config, gone, tmp_path, now=NOW.replace(hour=9))
    assert meeting(tmp_path, "public-works-2026-11-16")["listed"] is False
    assert meeting(tmp_path, "finance-2026-11-10").get("listed") is True
    assert any("include=" in u for u in gone.urls)


def test_a_queued_page_that_is_gone_is_dropped(config, tmp_path):
    config["proudcity_meetings"]["max_pages_per_run"] = 1
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    queued = status_of(tmp_path)["to_read"]
    victim = next(iter(queued))
    config["proudcity_meetings"]["max_pages_per_run"] = 100
    status = fetch_proudcity_meetings.run(config, FakeSite(every_post(), gone=(victim,)), tmp_path, now=NOW.replace(hour=9))
    assert victim not in status["to_read"] and any("HTTP 404" in e for e in status["errors"])


def test_meetings_before_since_are_not_recorded(config, tmp_path):
    config["proudcity_meetings"]["since"] = "2026-10-01"
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    dates = {m["date"] for m in load(tmp_path).values()}
    assert min(dates) >= "2026-10-01" and "2026-10-05" in dates   # the title's 2025 is the wrong year


def test_minutes_false_saves_no_minutes(config, tmp_path):
    config["proudcity_meetings"]["minutes"] = False
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path, now=NOW)
    assert not any(m.get("minutes") for m in load(tmp_path).values())


def test_a_site_whose_api_is_off_fails_the_run(config, tmp_path):
    class Off(FakeSite):
        def listing(self, query):
            return FakeResponse(b"<html>Not here</html>", {})
    with pytest.raises(FetchError, match="REST API"):
        fetch_proudcity_meetings.run(config, Off(every_post()), tmp_path, now=NOW)


# ---- Site ---------------------------------------------------------------------------------

def test_site_says_where_the_meetings_come_from(config, tmp_path, monkeypatch):
    config["sections"] = [s for s in config["sections"] if s["slug"] in ("meetings", "about")]
    for table in ("seeclickfix", "permits", "finance", "schools", "housing", "labor", "freshness", "storage"):
        config.pop(table, None)
    fetch_proudcity_meetings.run(config, FakeSite(every_post()), tmp_path / "data", now=NOW)
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("holyoke", out, data_dir=tmp_path / "data", now=NOW)
    page_html = (out / "meetings" / "2026-09-15-city-council" / "index.html").read_text()
    assert "website" in page_html and "H1kAe-TpdR4" in page_html
    # The agenda typed into the page links its saved copy, a PDF.
    assert "/agendas/proudcity-49960-" in page_html
