"""Page view counting (site/static/js/count.js) in a real browser: what it sends
to GoatCounter, and that it never sends what was searched.

The built site is served at its real hostname, since the script counts nothing
on localhost, and GoatCounter's endpoint is stubbed to record each beacon."""

import mimetypes
from urllib.parse import parse_qs, urlsplit

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

SITE = "https://gloucester-ma.publick.org"
COUNTER = "https://publick.goatcounter.com/count"


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def counted(browser, site_dir, visits, prefix=True):
    """Visit each URL in turn (the later ones linked from the first) and return
    the query of every beacon sent, in order."""
    beacons = []

    def serve(route):
        path = urlsplit(route.request.url).path
        file = site_dir / path.lstrip("/")
        if file.is_dir():
            file = file / "index.html"
        body = file.read_bytes()
        if file.suffix == ".html" and not prefix:
            body = body.replace(b' data-prefix="gloucester-ma"', b"")
        route.fulfill(body=body, content_type=mimetypes.guess_type(file.name)[0] or "text/html")

    def count(route):
        query = parse_qs(urlsplit(route.request.url).query, keep_blank_values=True)
        beacons.append({k: v[0] for k, v in query.items()})
        route.fulfill(status=204)

    context = browser.new_context()
    context.route(SITE + "/**", serve)
    context.route(COUNTER + "*", count)
    page = context.new_page()
    for url in visits:
        page.goto(url, referer=page.url if page.url.startswith(SITE) else None)
        page.wait_for_load_state("networkidle")
    context.close()
    return beacons


def test_page_view_names_the_town(browser, site_dir):
    [view] = counted(browser, site_dir, [SITE + "/meetings/"])
    assert view["p"] == "gloucester-ma/meetings/"
    assert view["t"] and view["r"] == "" and "e" not in view


def test_link_from_the_same_site_names_the_town(browser, site_dir):
    views = counted(browser, site_dir, [SITE + "/", SITE + "/meetings/"])
    assert [v["p"] for v in views] == ["gloucester-ma/", "gloucester-ma/meetings/"]
    assert views[1]["r"] == "gloucester-ma/"


def test_search_is_an_event_without_what_was_searched(browser, site_dir):
    view, event = counted(browser, site_dir, [SITE + "/meetings/search/?q=harbor+plan"])
    assert view["p"] == "gloucester-ma/meetings/search/"
    assert event["p"] == "gloucester-ma/meeting-search" and event["e"] == "true"
    assert not any("harbor" in value for beacon in (view, event) for value in beacon.values())


def test_without_a_prefix_paths_are_sent_as_they_are(browser, site_dir):
    view, event = counted(browser, site_dir, [SITE + "/meetings/search/?q=harbor"], prefix=False)
    assert view["p"] == "/meetings/search/" and event["p"] == "meeting-search"
