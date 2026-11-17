"""A city's meetings from its WordPress calendar (The Events Calendar, pipeline/tribe.py) and their agendas and
minutes from its document library (pipeline/wpdocs.py), read by pipeline/fetch_wordpress_meetings.py. Run offline
against Brockton's REST API as it answered in November 2026, trimmed to a few events and documents
(tests/fixtures/wordpress/): the events' notices, images and tags, and the long documents' text past its first
pages, are left out, and the residents named in a License Commission's minutes are replaced."""

import copy
import json
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import TZ
from fakes import FIXTURES, FakeResponse
from test_wordfile import docx, p

from pipeline import fetch_wordpress_meetings as fetch
from pipeline import htmlfile, meeting_sources, pdftext, proudcity, tribe, wpdocs
from pipeline.config import load_config
from pipeline.http import FetchError, is_browser_check

SAVED = FIXTURES / "wordpress"
SITE = "https://brockton.gov"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=TZ)
SETTINGS = {
    "site_url": SITE, "source_name": "City of Brockton", "since": "2026-07-01",
    "categories": {"City Council": "City Council", "Finance Committee": "City Council Finance Committee",
                   "Planning": "Planning Board", "Conservation Commission": "Conservation Commission",
                   "School Committee": "School Committee", "Library": "Library Board of Trustees",
                   "License Commission": "License Commission", "Brockton Housing Authority": "Brockton Housing Authority",
                   "Council on Aging": "Council on Aging", "Board of Health": "Board of Health",
                   "Public Safety": "City Council Public Safety Committee", "Women's Issues": "Women's Commission",
                   "Redevelopment Authority": "Brockton Redevelopment Authority"},
    "exclude_categories": ["Community Event", "Police"],
    "exclude_pattern": "School Council|Plymouth Norfolk",
    "bodies": {"School Policy Subcommittee": "School Committee Policy Subcommittee",
               "BHS School Building Committee": "BHS School Building Committee"},
    "documents": {"agenda_category": "Meeting Agendas", "minutes_category": "Meeting Minutes"},
}


def saved(name: str):
    return json.loads((SAVED / f"{name}.json").read_text())


def fields(items: list[dict], query: dict) -> list[dict]:
    wanted = query.get("_fields", [""])[0]
    return [{k: v for k, v in i.items() if k in wanted.split(",")} for i in items] if wanted else items


class FakeSite:
    """Brockton's site: The Events Calendar's listing of the saved events (at most 50 a page, `per_page` to test
    paging), the document library (its categories, its documents changed after a time or by id, the most recently
    changed first, and their files' addresses), and the files themselves. `refuse` answers addresses containing one
    of its strings with an HTTP 403."""

    def __init__(self, events=None, documents=None, per_page: int = 50, refuse: tuple = ()):
        self.events = saved("events") if events is None else events
        self.documents = saved("documents") if documents is None else documents
        self.per_page, self.refuse = per_page, refuse
        self.pdf = (FIXTURES / "legistar_minutes.pdf").read_bytes()
        self.urls, self.request_count = [], 0

    def page_of(self, items: list, query: dict, size: int, tec: bool) -> FakeResponse:
        n = int(query.get("page", ["1"])[0])
        pages = -(-len(items) // size)
        if n > max(pages, 1):
            raise FetchError("past the last page", 404 if tec else 400)
        chunk = items[(n - 1) * size:n * size]
        if tec:
            return FakeResponse(json.dumps({"events": chunk, "total": len(items), "total_pages": pages}).encode(),
                                {"X-TEC-Total": str(len(items)), "X-TEC-TotalPages": str(pages)})
        return FakeResponse(json.dumps(fields(chunk, query)).encode(), {"X-WP-TotalPages": str(pages)})

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        if any(r in url for r in self.refuse):
            raise FetchError(f"{url}: HTTP 403", 403)
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if parts.path == "/wp-json/tribe/events/v1/events":
            start, end = query["start_date"][0], query["end_date"][0]
            found = [e for e in self.events if start <= e["start_date"] <= end]
            return self.page_of(found, query, min(int(query["per_page"][0]), 50, self.per_page), tec=True)
        if parts.path == "/wp-json/wp/v2/document-categories":
            return self.page_of(saved("document_categories"), query, 100, tec=False)
        if parts.path == "/wp-json/wp/v2/public-documents":
            docs = sorted(self.documents, key=lambda d: d["modified"], reverse=True)
            if "include" in query:
                docs = [d for d in docs if str(d["id"]) in query["include"][0].split(",")]
            else:
                terms = {int(t) for t in query["document-categories"][0].split(",")}
                docs = [d for d in docs if d["modified"] > query["modified_after"][0]
                        and terms & set(d["document-categories"])]
            return self.page_of(docs, query, 100, tec=False)
        if parts.path == "/wp-json/wp/v2/media":
            ids = query["include"][0].split(",")
            return self.page_of([m for m in saved("media") if str(m["id"]) in ids], query, 100, tec=False)
        if parts.path.startswith("/wp-content/uploads/"):
            if url.endswith(".docx"):
                return FakeResponse(docx(p("Library Board of Trustees") + p("Minutes of July 14, 2026")))
            return FakeResponse(self.pdf + f"\n% {url}\n".encode())
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture
def config():
    town = copy.deepcopy(load_config("gloucester"))
    for table in ("archive", "drive_meetings", "summaries", "participation", "glossary"):
        town.pop(table, None)
    town["town"].update(name="Brockton")
    town["wordpress_meetings"] = copy.deepcopy(SETTINGS)
    return town


def load(data_dir):
    return json.loads((data_dir / "meetings" / "meetings.json").read_text())


def status_of(data_dir):
    return json.loads((data_dir / "meetings" / "wordpress_status.json").read_text())


def event(event_id: int) -> dict:
    return tribe.parse_event(next(e for e in saved("events") if e["id"] == event_id))


def document(post_id: int) -> dict:
    return wpdocs.parse_document(next(d for d in saved("documents") if d["id"] == post_id), {15: "agenda", 17: "minutes"})


# ---- The Events Calendar ------------------------------------------------------------------

def test_an_event_as_the_listing_gives_it():
    found = event(89073)
    assert found == {"id": "89073", "url": "https://brockton.gov/event/planning-board-meeting-92/",
                     "title": "Planning Board Meeting", "date": "2026-09-01", "start_time": "18:00", "end_time": "19:00",
                     "location_name": "Online", "address": "", "remote_url": "https://us02web.zoom.us/j/89542653181",
                     "categories": ["Planning"], "modified": found["modified"]}
    # A place with an address; a link that isn't a Zoom meeting's isn't kept.
    library = event(89174)
    assert library["location_name"] == "Brockton Main Library" and library["address"].startswith("304 Main St")
    other_link = dict(next(e for e in saved("events") if e["id"] == 89159), website="https://www.uwgpc.org/frc")
    assert tribe.parse_event(other_link)["remote_url"] is None
    # A recurring event's occurrence, with no place (an empty list).
    forum = event(10008352)
    assert forum["id"] == "10008352" and forum["location_name"] == "" and forum["categories"] == ["Local"]


def test_an_all_day_event_has_no_time():
    item = dict(next(e for e in saved("events") if e["id"] == 89159), all_day=True,
                start_date="2026-09-14 00:00:00", end_date="2026-09-14 23:59:59")
    assert tribe.parse_event(item)["start_time"] is None and tribe.parse_event(item)["end_time"] is None


@pytest.mark.parametrize("event_id, body, status, special", [
    (89159, "City Council", "scheduled", False),
    (89073, "Planning Board", "scheduled", False),                        # its category's board
    (89087, "School Committee Policy Subcommittee", "scheduled", False),  # bodies, in a board's category
    (89174, "Library Board of Trustees", "cancelled", False),             # "Library Meeting – Canceled"
    (90361, "Brockton Redevelopment Authority", "rescheduled", False),
    (90538, "Brockton Housing Authority", "scheduled", True),             # "Special BHA Meeting"
    (90512, "City Council", "scheduled", True),                           # "City Council Special Meeting"
    (88622, "License Commission", "scheduled", True),
])
def test_a_meetings_board_and_status(event_id, body, status, special):
    info = tribe.meeting_info(event(event_id), SETTINGS)
    assert (info["body"], info["status"], info["special"]) == (body, status, special)


@pytest.mark.parametrize("event_id", [
    10008352,   # "Local": a category not listed
    89183,      # no category
    89467,      # "Public Hearing", "Police", "Public Safety": Police is in exclude_categories
    89392,      # Board of Health's category, but exclude_pattern leaves it out ("Plymouth Norfolk 5 Meeting")
])
def test_events_that_arent_a_listed_boards_meeting(event_id):
    assert tribe.meeting_info(event(event_id), SETTINGS) is None


def test_a_community_event_filed_under_a_boards_category_too_isnt_its_meeting():
    found = next(e for e in saved("events") if [c["name"] for c in e["categories"]][:2] == ["Community Event", "Planning"])
    assert tribe.meeting_info(tribe.parse_event(found), SETTINGS) is None


def test_names_filed_by_category_alone():
    assert tribe.says_board("Finance Committee Meeting", "Finance Committee")
    assert not tribe.says_board("COA Meeting", "Council on Aging")
    assert tribe.meeting_info(event(88888), SETTINGS)["by_category"]
    assert not tribe.meeting_info(event(89087), SETTINGS)["by_category"]


# ---- The document library ----------------------------------------------------------------

def test_the_agenda_and_minutes_categories_by_name():
    assert wpdocs.kinds(saved("document_categories"), SETTINGS["documents"]) == {15: "agenda", 17: "minutes"}
    with pytest.raises(ValueError, match="'Agendas' \\(agenda_category\\)"):
        wpdocs.kinds(saved("document_categories"), {"agenda_category": "Agendas", "minutes_category": "Meeting Minutes"})


def test_a_document_as_the_listing_gives_it():
    found = document(90607)
    assert {k: v for k, v in found.items() if k != "html"} == {
        "id": "90607", "link": "https://brockton.gov/public-documents/2026-11-17-school-committee-meeting-meeting-agenda/",
        "title": "2026-11-17 – School Committee Meeting – Meeting Agenda", "modified": "2026-11-13T17:45:28",
        "kind": "agenda", "event_id": "90605", "date": "2026-11-17", "media_id": "90609"}
    # Posted as text: no file.
    assert document(90656)["media_id"] is None and "Regular Meeting Agenda" in document(90656)["html"]
    # No event linked: the ACF fields empty, or not filled in at all (null).
    assert document(89821)["event_id"] is None and document(89821)["date"] == "2026-09-16"
    assert document(90111)["event_id"] is None and document(90111)["media_id"] is None
    assert document(89545)["kind"] == "minutes"


def test_a_documents_date_is_its_titles_not_a_stale_snapshots():
    """The event was moved from 2026-09-17 to 2026-10-15 after the document was saved; the snapshot kept the old date."""
    found = document(90028)
    assert found["event_id"] == "90026" and found["date"] == "2026-10-15"


@pytest.mark.parametrize("value, expected", [
    ([90659], "90659"), (90659, "90659"), ("90659", "90659"), ("", None), (None, None), ([], None),
    ({"id": 7}, "7"), (False, None), ("abc", None),
])
def test_one_id_of_an_acf_relationship(value, expected):
    assert wpdocs.one_id(value) == expected


def test_a_documents_board_from_its_title():
    assert wpdocs.board_of("2026-09-16 – Women’s Commission Meeting – Meeting Minutes", SETTINGS) == "Women's Commission"
    assert wpdocs.board_of("2026-08-04 – Special License Commission Meeting – Meeting Minutes", SETTINGS) == "License Commission"
    assert wpdocs.board_of("2026-09-03 – School Policy Subcommittee Meeting – Meeting Agenda", SETTINGS) == \
        "School Committee Policy Subcommittee"
    assert wpdocs.board_of("2025‐03‐10 – Election Results", SETTINGS) is None


def test_a_document_posted_as_text_is_saved_as_a_pdf_of_its_text():
    html = document(90656)["html"]
    pdf = htmlfile.to_pdf(html)
    text = pdftext.plain_text(pdf)
    assert "BROCKTON WOMEN'S COMMISSION" in text and "Event Planning Subcommittee Charter Vote" in text
    assert htmlfile.to_pdf(html) == pdf
    # A table's cells, one to a line.
    assert "Conservation Commission" in htmlfile.paragraphs(document(90585)["html"])


def test_proudcity_reads_its_agenda_text_through_the_shared_module():
    assert proudcity.htmlfile is htmlfile


# ---- A run --------------------------------------------------------------------------------

def run(config, tmp_path, site=None, now=NOW):
    site = site or FakeSite()
    status = fetch.run(config, site, tmp_path, now=now)
    return status, load(tmp_path), site


def test_the_first_run_records_each_boards_meetings_and_their_documents(config, tmp_path):
    status, store, site = run(config, tmp_path, FakeSite(per_page=10))
    council = store["wordpress-89159"]
    assert {k: council[k] for k in ("source", "date", "start_time", "body", "title", "location_name", "address",
                                    "documents_url", "status", "source_name")} == {
        "source": "wordpress", "date": "2026-09-14", "start_time": "19:00", "body": "City Council",
        "title": "City Council Meeting", "location_name": "Brockton City Hall", "address": "",
        "documents_url": "https://brockton.gov/event/city-council-meeting-170/", "status": "scheduled",
        "source_name": "City of Brockton"}
    assert store["wordpress-89174"]["status"] == "cancelled"
    assert store["wordpress-89073"]["remote_url"] == "https://us02web.zoom.us/j/89542653181"
    assert not any(k in store for k in ("wordpress-10008352", "wordpress-89183", "wordpress-89467", "wordpress-89392"))
    assert status["meetings_created"] == status["meetings_listed"] == len(store)
    # The calendar's events from `since` to 90 days ahead, 10 a page here: read to the count the first page gives.
    events = [u for u in site.urls if "/tribe/events/v1/events" in u]
    assert len(events) == 3 and "start_date=2026-07-01+00%3A00%3A00" in events[0] and "2027-02-15+23%3A59%3A59" in events[0]
    # The events exclude_categories leaves out aren't counted under their other categories.
    assert status["unlisted_categories"] == {"Local": 1}
    assert status["filed_by_category"]["COA Meeting"] == "Council on Aging"

    # Documents joined by the event they link: the file where there is one, else the text as a PDF.
    agenda = store["wordpress-90605"]["agendas"][0]
    assert agenda["source_url"] == "https://brockton.gov/wp-content/uploads/2026/11/School_Agenda_111726.pdf"
    assert agenda["original_filename"] == "School_Agenda_111726.pdf" and "converted_from" not in agenda
    assert agenda["post_id"] == "90607" and agenda["joined"] == "event"
    text = store["wordpress-90654"]["agendas"][0]
    assert text["converted_from"] == "html" and text["source_url"].endswith("/2026-11-18-womens-commission-meeting-meeting-agenda/")
    assert text["has_text"]
    # A post filed as text that has a file too: the file.
    assert store["wordpress-90583"]["agendas"][0]["source_url"].endswith("/Conservation_Agenda_111826.pdf")
    # The event moved after the document was saved: joined by the event, not the snapshot's date.
    assert store["wordpress-90026"]["agendas"][0]["post_id"] == "90028"
    assert store["wordpress-89159"]["minutes"][0]["converted_from"] == "html"
    assert store["wordpress-88283"]["minutes"][0]["source_url"].endswith("/Library_Minutes_071426.pdf")

    # No event linked: joined by the title's date and board when one meeting is that board's that day.
    assert store["wordpress-88622"]["minutes"][0]["post_id"] == "89545"
    assert store["wordpress-88622"]["minutes"][0]["joined"] == "title"
    # Two posts of one agenda, one linking the event: that one's kept, the other stands aside.
    assert [d["post_id"] for d in store["wordpress-90109"]["agendas"]] == ["90113"]
    # Not joined: an event exclude_pattern leaves out, and minutes of a meeting the calendar doesn't have.
    assert status["unmatched"] == {
        "90661": {"title": "2026-11-19 – Plymouth Norfolk 5 Meeting – Meeting Agenda",
                  "link": "https://brockton.gov/public-documents/2026-11-19-plymouth-norfolk-5-meeting-meeting-agenda/",
                  "kind": "agenda", "date": "2026-11-19", "event_id": "90659", "why": "no meeting", "listed_on": "2026-11-17"},
        "89821": {"title": "2026-09-16 – Women’s Commission Meeting – Meeting Minutes",
                  "link": "https://brockton.gov/public-documents/2026-09-16-womens-commission-meeting-meeting-minutes/",
                  "kind": "minutes", "date": "2026-09-16", "event_id": None, "why": "no meeting", "listed_on": "2026-11-17"}}
    assert status["documents_listed"] == 11 and status["documents_without_event"] == 3
    assert status["documents_added"] == 8 and status["errors"] == [] and status["to_read"] == {}
    # The documents listed from 90 days before `since`; the files' addresses in one request.
    listing = next(u for u in site.urls if "/wp/v2/public-documents?" in u)
    assert "modified_after=2026-04-02T00%3A00%3A00" in listing and "document-categories=15%2C17" in listing
    assert sum("/wp/v2/media" in u for u in site.urls) == 1
    # Three pages of events, the categories, the documents, their files' addresses, and three files.
    assert status["requests"] == site.request_count == 3 + 1 + 1 + 1 + 3


def test_the_next_run_reads_only_what_changed(config, tmp_path):
    run(config, tmp_path)
    status, store, site = run(config, tmp_path, now=NOW.replace(day=18))
    # The events from recheck_days (60) ago; the documents changed since the last listing, less two days.
    events = next(u for u in site.urls if "/tribe/events/v1/events" in u)
    assert "start_date=2026-09-19" in events
    listing = next(u for u in site.urls if "/wp/v2/public-documents?" in u)
    assert "modified_after=2026-11-15T12%3A00%3A00" in listing
    # Those listed again are saved already: nothing downloaded.
    assert status["documents_added"] == status["documents_changed"] == 0
    assert not any("/wp-content/" in u or "/wp/v2/media" in u for u in site.urls)
    assert status["meetings_created"] == 0


def test_a_document_changed_is_saved_as_a_new_version_only_when_its_content_did(config, tmp_path):
    run(config, tmp_path)
    docs = saved("documents")
    changed = next(d for d in docs if d["id"] == 90656)
    changed["modified"] = "2026-11-17T15:00:00"
    changed["content"]["rendered"] += "<p>10. Adjournment (amended)</p>"
    same = next(d for d in docs if d["id"] == 90622)
    same["modified"] = "2026-11-17T15:00:00"   # edited, its text not
    status, store, _ = run(config, tmp_path, FakeSite(documents=docs), now=NOW.replace(hour=18))
    women = store["wordpress-90654"]
    assert len(women["agendas"]) == 2 and women["history"][-1]["field"] == "agenda"
    assert women["agendas"][-1]["post_modified"] == "2026-11-17T15:00:00"
    council = store["wordpress-89159"]
    assert len(council["minutes"]) == 1 and council["minutes"][0]["post_modified"] == "2026-11-17T15:00:00"
    assert status["documents_changed"] == 1 and status["documents_added"] == 0


def test_documents_past_the_runs_limit_wait_for_the_next(config, tmp_path):
    config["wordpress_meetings"]["max_documents_per_run"] = 3
    status, store, _ = run(config, tmp_path)
    assert status["documents_added"] == 3 and len(status["to_read"]) == 6 and status["documents_waiting"] == 6
    # The newest meetings first.
    assert {m["date"] for m in store.values() if m.get("agendas")} >= {"2026-11-18"}
    status, store, site = run(config, tmp_path, now=NOW.replace(hour=18))
    # Read again by id, though not changed since.
    assert any("include=" in u and "/public-documents" in u for u in site.urls)
    assert status["documents_added"] == 3 and len(status["to_read"]) == 2
    status, store, _ = run(config, tmp_path, now=NOW.replace(hour=20))
    assert status["to_read"] == {} and status["documents_waiting"] == 0
    # The two posts of one agenda: the one linking the event, alone.
    assert [d["post_id"] for d in store["wordpress-90109"]["agendas"]] == ["90113"]


def test_a_document_is_joined_once_its_meeting_is_recorded(config, tmp_path):
    run(config, tmp_path)
    status, _, site = run(config, tmp_path, now=NOW.replace(day=24))
    assert "90661" in status["unmatched"] and not any("/public-documents?include=" in u for u in site.urls)
    # Plymouth Norfolk 5's meetings taken in: its agenda, no longer in the listing of what changed, is read again
    # by id and joined.
    settings = config["wordpress_meetings"]
    settings["exclude_pattern"] = "School Council"
    settings["bodies"]["Plymouth Norfolk 5"] = "Plymouth Norfolk 5 Board"
    status, store, site = run(config, tmp_path, now=NOW.replace(day=25))
    assert store["wordpress-90659"]["body"] == "Plymouth Norfolk 5 Board"
    assert store["wordpress-90659"]["agendas"][0]["post_id"] == "90661"
    assert "90661" not in status["unmatched"] and "89821" in status["unmatched"]
    # Only it: the minutes of a Women's Commission meeting the calendar doesn't have wait without being asked for.
    assert any(u.endswith("&include=90661&_fields=id%2Clink%2Ctitle%2Cmodified%2Cdocument-categories%2Cacf%2Ccontent")
               for u in site.urls)


def test_documents_that_link_no_event_are_counted(config, tmp_path):
    """A change in the site's fields leaves every document without its event: counted, and joined by title where
    a title names one meeting."""
    docs = saved("documents")
    for d in docs:
        d["acf"] = {"file-public-document": "", "event-public-document": ""}
    status, store, _ = run(config, tmp_path, FakeSite(documents=docs))
    assert status["documents_without_event"] == status["documents_listed"] == 11
    assert store["wordpress-89159"]["minutes"][0]["joined"] == "title"


def test_an_upcoming_meeting_the_calendar_drops_is_no_longer_listed(config, tmp_path):
    run(config, tmp_path)
    events = [e for e in saved("events") if e["id"] != 90654]
    _, store, _ = run(config, tmp_path, FakeSite(events=events), now=NOW.replace(hour=18))
    assert store["wordpress-90654"]["listed"] is False
    assert store["wordpress-90605"]["listed"] is True


def test_minutes_false_leaves_minutes_out(config, tmp_path):
    config["wordpress_meetings"]["minutes"] = False
    _, store, _ = run(config, tmp_path)
    assert not any(m.get("minutes") for m in store.values()) and store["wordpress-90605"]["agendas"]


def test_without_a_documents_table_only_the_calendar_is_read(config, tmp_path):
    del config["wordpress_meetings"]["documents"]
    status, store, site = run(config, tmp_path)
    assert all("/tribe/events/v1/events" in u for u in site.urls) and status["documents_listed"] == 0
    assert store["wordpress-89159"]["body"] == "City Council"


def test_a_failed_download_is_tried_on_the_next_runs(config, tmp_path):
    class Failing(FakeSite):
        def get(self, url):
            if url.endswith("School_Agenda_111726.pdf"):
                raise FetchError(f"{url}: HTTP 500", 500)
            return super().get(url)
    status, _, _ = run(config, tmp_path, Failing())
    assert status["to_read"] == {"90607": {"tries": 1}} and "School_Agenda_111726.pdf: HTTP 500" in status["errors"][0]
    status, store, _ = run(config, tmp_path, now=NOW.replace(hour=18))
    assert status["to_read"] == {} and store["wordpress-90605"]["agendas"]


@pytest.mark.parametrize("refused", ["/wp-json/wp/v2/public-documents", "School_Agenda_111726.pdf",
                                     "/wp-json/tribe/events/v1/events"])
def test_a_refusal_stops_the_run(config, tmp_path, refused):
    with pytest.raises(FetchError, match="HTTP 403"):
        fetch.run(config, FakeSite(refuse=(refused,)), tmp_path, now=NOW)


def test_a_browser_check_stops_the_run():
    class Challenge:
        url, status_code, headers, text = f"{SITE}/wp-json/", 403, {"cf-mitigated": "challenge"}, ""
    assert is_browser_check(Challenge())
    with pytest.raises(fetch.Blocked):
        fetch.stop_at_browser_check(Challenge())
    with pytest.raises(fetch.Blocked):
        fetch.stop_if_refused(fetch.Blocked("x", 403))
    fetch.stop_if_refused(FetchError("x", 500))


def test_the_city_website_is_its_calendar():
    config = {"wordpress_meetings": {"site_url": SITE, "source_name": "City of Brockton"}}
    assert meeting_sources.city_website(config) == f"{SITE}/events/"
    assert "wordpress" in meeting_sources.CITY


@pytest.mark.parametrize("change, message", [
    ({"categories": {}}, "needs categories"),
    ({"since": "July"}, "since must be a date"),
    ({"site_url": "https://brockton.gov/events/"}, "site_url must be"),
    ({"categories": {"Planning": ""}}, "categories must map"),
    ({"exclude_categories": "Local"}, "exclude_categories must be a list"),
    ({"exclude_pattern": "("}, "isn't a regular expression"),
    ({"days_ahead": 0}, "days_ahead must be a whole number"),
    ({"documents": {"agenda_category": "Meeting Agendas"}}, "needs minutes_category"),
    ({"documents": "Meeting Agendas"}, "must be a table"),
])
def test_the_config_is_checked(change, message):
    settings = {**copy.deepcopy(SETTINGS), **change}
    with pytest.raises(SystemExit, match=message):
        tribe.check({"slug": "brockton", "wordpress_meetings": settings})
    tribe.check({"slug": "brockton", "wordpress_meetings": copy.deepcopy(SETTINGS)})
