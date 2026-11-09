"""The one list of meeting sources (pipeline/meeting_sources.py), and the places that read it."""

import importlib
import re
from pathlib import Path

from pipeline import absences, build_site, config, listings, meeting_sources, update

PIPELINE = Path(meeting_sources.__file__).parent
DOCS = PIPELINE.parent / "docs" / "configuration.md"


def test_every_meeting_reader_is_in_the_list():
    """A reader module of its own (fetch_<source>_meetings.py) can't be left out of the list, and so out of
    the daily update."""
    modules = {f"pipeline.{p.stem}" for p in PIPELINE.glob("fetch_*_meetings.py")}
    assert modules == {s.module for s in meeting_sources.READERS}
    for source in meeting_sources.READERS:
        module = importlib.import_module(source.module)
        assert callable(module.run) and callable(module.main), source.module
        assert source.module == f"pipeline.fetch_{source.source}_meetings"


def test_each_sources_table_is_checked_and_documented():
    documented = DOCS.read_text()
    for source in meeting_sources.SOURCES:
        assert source.table in config.TABLES
        assert f"`[{source.table}]`" in documented, f"{source.table} isn't in docs/configuration.md's table of tables"
        if source.checker:
            assert callable(importlib.import_module(source.checker).check), source.checker


def test_the_update_runs_one_step_per_reader_in_order():
    steps = [s for s in update.SOURCES if s.table in meeting_sources.TABLES]
    assert [(s.name, s.module, s.table) for s in steps] == [(r.step, r.module, r.table) for r in meeting_sources.READERS]
    assert all(set(s.secrets) == set(update.STORAGE) for s in steps)


def test_the_orders_follow_the_list():
    documents = [s.source for s in meeting_sources.SOURCES if s.documents]
    assert list(absences.SCHOOL_SOURCES) == documents
    assert set(absences.NO_DOCUMENTS) == {"ical", "schedule"}
    # The board's own sources rank above the city's, and sources that only date a meeting below both.
    order = listings.LISTING_ORDER
    assert list(order[-len(documents):]) == documents
    assert order.index("ical") < order.index("agendacenter") < order.index(documents[0])
    assert listings.DOCUMENT_SOURCES == ("agendacenter", *documents, "archive")


def test_each_source_says_where_it_lists_a_meeting():
    """Each source's own wording, never the city calendar's (a Drive folder's listings once read "on the city
    calendar"), and translated."""
    from pipeline import i18n
    for source in meeting_sources.SOURCES:
        assert source.listing and source.listing != build_site.CALENDAR
        assert build_site.LISTINGS[source.source] == source.listing
        with i18n.use("es"):
            assert i18n._(source.listing) != source.listing, f"{source.listing!r} has no Spanish in site/strings/es.po"


def test_who_posts_the_meetings_in_the_order_they_rank():
    town = {"page_meetings": {"source_name": "Nashua School District", "page_url": "https://nashua.example/board"},
            "schedule_meetings": {"source_name": "Malden Public Schools", "url": "https://malden.example/dates"},
            "opengov_meetings": {"source_name": "Rhode Island Open Meetings", "page_url": "https://opengov.example"}}
    assert meeting_sources.posted_by(town) == [
        {"name": "Nashua School District", "url": "https://nashua.example/board"},
        {"name": "Rhode Island Open Meetings", "url": "https://opengov.example"},
        {"name": "Malden Public Schools", "url": "https://malden.example/dates"}]
    assert [s["name"] for s in meeting_sources.posted_by(town, leave_out=("opengov",))] == [
        "Nashua School District", "Malden Public Schools"]


def test_no_list_of_sources_is_kept_by_hand_outside_the_list():
    """The places that read the list don't keep lists of its sources' tables of their own. A module may
    name a source or two for what only that source does (Rhode Island's portal, a page's own pages)."""
    for module in (absences, build_site, config, listings, update):
        text = Path(module.__file__).read_text()
        named = set(re.findall(r'"(\w+_meetings)"', text)) & set(meeting_sources.TABLES)
        assert len(named) <= 2, f"{module.__name__} names {sorted(named)}: read them from pipeline/meeting_sources.py"
