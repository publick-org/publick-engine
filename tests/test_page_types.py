"""Each page type built on its own (build_site's page functions), from a LanguagePages holding only what
its pages use. The templates are stand-ins: these tests are of which pages are written, where, and how the
sitemap and redirects list them, not of what the pages say (test_site.py builds the whole site for that)."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import DictLoader, Environment

from pipeline import build_site

BUILT_AT = datetime(2026, 10, 2, 7, 0, tzinfo=ZoneInfo("America/New_York"))


def meeting(day: str) -> dict:
    """A meeting with only what its page's date for the sitemap is read from (build_site.meeting_lastmod)."""
    return {"date": day, "first_seen": "2026-09-01T07:00:00-04:00", "agendas": [], "minutes": [], "history": [],
            "preview": None, "minutes_summary": None, "correction": None}


def language_pages(out_dir: Path, lang: str, templates: dict[str, str], meetings: dict | None = None,
                   **common) -> build_site.LanguagePages:
    return build_site.LanguagePages(
        config={"site": {"domain": "example.org"}, "sections": [{"slug": "meetings"}, {"slug": "311"}]},
        lang=lang, langs=["en", "es"], out_dir=out_dir, data_dir=out_dir / "data", built_at=BUILT_AT,
        env=Environment(loader=DictLoader(templates), autoescape=True),
        common={"meetings": meetings or {"all": [], "boards": []}, "data_status": [],
                "site_nav": lambda url: {"menu": [], "tabs": []}, **common},
        terms=[], built_folders={"meetings", "311"}, documents=True, redirects={})


def written(out_dir: Path) -> list[str]:
    return sorted(p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*") if p.is_file())


def test_board_pages_alone(tmp_path):
    """Each board's page, under its language's prefix and dated by its newest meeting for the sitemap, and a
    page at its old address sending readers to it, listed as a redirect and not in the sitemap."""
    older, newer = meeting("2026-09-01"), meeting("2026-09-15")
    board = {"name": "Concejo Municipal", "url": "/meetings/boards/city-council/",
             "also_urls": ["/meetings/boards/council/"], "meetings": [older, newer]}
    pages = language_pages(tmp_path, "es", {"board.html": "<h1>{{ board.name }}</h1>", "moved.html": "{{ moved_to }}"},
                           {"all": [older, newer], "boards": [board]})
    build_site.board_pages(pages)
    assert pages.urls == {"/es/meetings/boards/city-council/": "2026-09-15"}
    assert pages.redirects == {"/es/meetings/boards/council/": "/es/meetings/boards/city-council/"}
    assert written(tmp_path) == ["es/meetings/boards/city-council/index.html", "es/meetings/boards/council/index.html"]
    assert "<h1>Concejo Municipal</h1>" in (tmp_path / "es/meetings/boards/city-council/index.html").read_text()


def test_311_pages_alone(tmp_path):
    """A page for each ward with its population, but none for requests outside the wards, and one for each
    category; both dated the day of the build."""
    scorecard = {"by_ward": [{"ward": "1", "population": 5000}],
                 "wards": [{"ward": "1", "requests": 3}, {"ward": "outside", "requests": 1}],
                 "categories": [{"slug": "potholes", "name": "Potholes"}]}
    pages = language_pages(tmp_path, "en", {"ward.html": "<p>Ward {{ ward.ward }}: {{ ward.population }}</p>",
                                            "category.html": "<p>{{ category.name }}</p>"}, scorecard=scorecard)
    build_site.ward_pages(pages)
    build_site.category_pages(pages)
    assert pages.urls == {"/311/ward/1/": "2026-10-02", "/311/category/potholes/": "2026-10-02"}
    assert written(tmp_path) == ["311/category/potholes/index.html", "311/ward/1/index.html"]
    assert "<p>Ward 1: 5000</p>" in (tmp_path / "311/ward/1/index.html").read_text()


def test_meeting_pages_alone(tmp_path):
    """Each meeting's page, dated for the sitemap; a page at each listing shown as part of it, numbered when it
    has more than one, sending readers there; and each decision a result of the site's search, in its order."""
    council = {**meeting("2026-09-15"), "url": "/meetings/2026-09-15-city-council/", "body": "City Council",
               "start_time": "19:00", "glossary": [], "also_urls": ["/meetings/2026-09-15-city-council-2/", "/meetings/2026-09-15-council/"],
               "decisions": {"decided": ["Approved the budget", "Approved the budget"], "recommended": ["Recommended a study"]}}
    templates = {"meeting.html": "<h1>{{ meeting.body }}</h1>", "moved.html": "{{ moved_to }} {{ part }}"}
    pages = language_pages(tmp_path, "en", templates, {"all": [council], "boards": []})
    records: list[dict] = []
    build_site.meeting_pages(pages, records)
    assert pages.urls == {"/meetings/2026-09-15-city-council/": "2026-09-15"}
    assert pages.redirects == {"/meetings/2026-09-15-city-council-2/": "/meetings/2026-09-15-city-council/",
                               "/meetings/2026-09-15-council/": "/meetings/2026-09-15-city-council/"}
    assert (tmp_path / "meetings/2026-09-15-council/index.html").read_text().endswith(" 2")
    assert [r["content"] for r in records] == ["Approved the budget", "Approved the budget", "Recommended a study"]
    # The same decision twice still leads to two addresses, each its own result.
    assert len({r["url"] for r in records}) == 3
    assert all(r["url"].startswith("/meetings/2026-09-15-city-council/#minutes") for r in records)
