"""Automated WCAG 2.2 AA checks with axe-core in a real browser.

Each page is checked at desktop and phone widths, in light mode: every page
declares color-scheme: light and has no dark styles (site_checks/test_town_site.py
checks the declaration), so dark mode shows the same page. If the sites ever get
dark styles, add "dark" back to SCHEMES. Axe
finds a subset of accessibility problems; manual keyboard and screen reader
checks are still needed when new sections are added.
"""

import time

import pytest
from conftest import PAGE_PATHS, serve
from test_sample_towns import malden_site, manchester_site, wallingford_site  # noqa: F401 (fixtures)
from test_site_check_pages import HAND_WRITTEN
from test_spanish_site import built  # noqa: F401 (fixture)

from pipeline import build_site
from site_checks.pages import sample

playwright_api = pytest.importorskip("playwright.sync_api")
axe_module = pytest.importorskip("axe_playwright_python.sync_playwright")

WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
VIEWPORTS = {"desktop": {"width": 1280, "height": 900}, "phone": {"width": 320, "height": 640}}
SCHEMES = ["light"]
PATHS = PAGE_PATHS + ["/no-such-page/"]


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def axe():
    return axe_module.Axe()


def wait_until(page, js, timeout=2.0):
    """Poll a condition. page.wait_for_function evaluates strings with eval, which
    the site's Content Security Policy blocks."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if page.evaluate(js):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {js}")


def format_violations(results):
    lines = []
    for v in results.response["violations"]:
        targets = ", ".join(str(n["target"]) for n in v["nodes"][:5])
        lines.append(f"- [{v['impact']}] {v['id']}: {v['help']} ({targets})")
    return "\n".join(lines)


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("path", PATHS)
def test_axe_no_violations(browser, axe, server_url, path, viewport, scheme):
    context = browser.new_context(viewport=VIEWPORTS[viewport], color_scheme=scheme)
    page = context.new_page()
    page.goto(server_url + path)
    results = axe.run(page, options={"runOnly": {"type": "tag", "values": WCAG_TAGS}})
    context.close()
    assert results.violations_count == 0, f"{path} ({viewport}, {scheme}):\n{format_violations(results)}"


@pytest.mark.parametrize("path", PATHS)
def test_reflows_without_horizontal_scroll(browser, server_url, path):
    """WCAG 1.4.10: content fits a 320px-wide viewport without sideways scrolling."""
    context = browser.new_context(viewport={"width": 320, "height": 640})
    page = context.new_page()
    page.goto(server_url + path)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    context.close()
    assert overflow <= 0, f"{path} scrolls horizontally by {overflow}px at 320px"


TABLES = """[...document.querySelectorAll('.table-wrap')].filter(w => w.offsetParent).map(w => ({
    scrolls: w.scrollWidth > w.clientWidth + 1, tabindex: w.getAttribute('tabindex'),
    role: w.getAttribute('role'), name: w.getAttribute('aria-label') || w.getAttribute('aria-labelledby')}))"""


@pytest.mark.parametrize("path", [p for p in PATHS if p.startswith(("/311/", "/budget/", "/schools/", "/housing/", "/officials/", "/about/"))])
def test_a_table_that_scrolls_is_reachable_by_keyboard(browser, server_url, path):
    """WCAG 2.1.1: a table wider than the screen can be scrolled from the keyboard. Its box takes
    focus and has a name; one that fits doesn't add a tab stop."""
    # At 200% text (WCAG 1.4.4) every table scrolls on a phone.
    for viewport, text in ((VIEWPORTS["desktop"], 1), (VIEWPORTS["phone"], 1), (VIEWPORTS["phone"], 2)):
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.goto(server_url + path)
        if text > 1:
            page.add_style_tag(content=f"html {{ font-size: {text * 100}% !important; }}")
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        page.wait_for_timeout(100)
        for t in page.evaluate(TABLES):
            if t["scrolls"]:
                assert t["tabindex"] == "0" and t["role"] == "region" and t["name"], (path, viewport, text, t)
            elif t["role"] is None:
                assert t["tabindex"] is None, (path, viewport, text, t)
        context.close()


def test_a_table_at_phone_width_scrolls_and_is_named(browser, server_url):
    """The School Committee's table on the Officials page is wider than a phone: its box is a tab
    stop named by its heading, and the arrow keys scroll it."""
    context = browser.new_context(viewport=VIEWPORTS["phone"])
    page = context.new_page()
    page.goto(server_url + "/officials/")
    page.wait_for_timeout(100)
    assert {"scrolls": True, "tabindex": "0", "role": "region", "name": "School Committee"} in page.evaluate(TABLES)
    box = page.locator('.table-wrap[aria-label="School Committee"]')
    box.focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(100)
    assert box.evaluate("w => w.scrollLeft") > 0
    context.close()


def test_text_resize_200_percent(browser, server_url):
    """WCAG 1.4.4: text can be enlarged to 200% without loss of content."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.goto(server_url + "/")
    page.add_style_tag(content="html { font-size: 200% !important; }")
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    context.close()
    assert overflow <= 0


def test_skip_link_is_first_tab_stop_and_moves_focus(browser, server_url):
    context = browser.new_context()
    page = context.new_page()
    page.goto(server_url + "/")
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement.textContent.trim()")
    assert focused == "Skip to main content"
    assert page.evaluate("document.activeElement.getBoundingClientRect().top") >= 0, "skip link should be visible on focus"
    page.keyboard.press("Enter")
    # Focus moves to <main> once the browser finishes the in-page jump.
    wait_until(page, "document.activeElement && document.activeElement.id === 'main'")
    context.close()


def test_unknown_path_serves_404_page(browser, server_url):
    context = browser.new_context()
    page = context.new_page()
    response = page.goto(server_url + "/no-such-page/")
    assert response.status == 404
    assert page.locator("h1").inner_text() == "Page not found"
    context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_meeting_search_results(browser, axe, server_url, viewport):
    context = browser.new_context(viewport=VIEWPORTS[viewport])
    page = context.new_page()
    page.goto(server_url + "/meetings/search/?q=ada+compliance")
    page.wait_for_selector("#search-results > li")
    assert "match" in page.text_content("#search-status")
    assert page.locator("#search-results mark").first.text_content().lower() in ("ada", "compliance")
    assert page.input_value("#search-q") == "ada compliance"
    results = axe.run(page, options={"runOnly": {"type": "tag", "values": WCAG_TAGS}})
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    page.goto(server_url + "/meetings/search/?q=zzzqqq")
    wait_until(page, "document.getElementById('search-status').textContent.startsWith('No meetings')", timeout=10)
    context.close()
    assert results.violations_count == 0, format_violations(results)
    assert overflow <= 0


# The address box (site/static/js/address.js) reads an address as pipeline/fetch_addresses.py's find()
# does (tests/test_addresses.py): the same cases, as the page says them.
ADDRESSES = [
    ("12 Main Street, Gloucester, MA 01930", "12 Main St is in Ward 2."),
    ("401 main st", "401 Main St is in Ward 1."),
    ("5 Main St", "5 Main St is close to the line between Ward 2 and Ward 3, so it could be in either."),
    ("3 Rowe Sq", "3 Rowe Sq is in Ward 2."),
    ("4 Rowe Sq", "4 Rowe Sq is in Ward 1."),
    ("12 Mt Vernon Street", "12 Mount Vernon St is in Ward 2."),
    ("12 St. Louis Avenue", "12 Saint Louis Ave is in Ward 1."),
    ("Pleasant Street", "Pleasant St is in Ward 2."),
    ("Washington St", "Washington St runs through Ward 2, Ward 3 and Ward 4."),
    ("5000 Washington St", "The Census Bureau's address list doesn't have 5000 Washington St."),
    ("12 Kilby St North", "12 N Kilby St is in Ward 4."),
    ("12 Elm St Rockport", "12 Elm St is in Ward 2."),
    ("12 Elm Ave", "12 Elm Ave isn't in the address list. Did you mean one of these?"),
    ("15 Harriet Road", "15 Harriet Road isn't in the address list. Did you mean one of these?"),
    ("12 Nowhere Lane", "12 Nowhere Lane isn't in the address list."),
]


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_address_box(browser, axe, server_url, viewport):
    context = browser.new_context(viewport=VIEWPORTS[viewport])
    page = context.new_page()
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.goto(server_url + "/officials/")
    page.wait_for_selector("#address:not([hidden])")
    assert not any("addresses.json" in u for u in requests), "loaded before the box is used"
    page.fill("#address-q", "12 Main St")
    page.keyboard.press("Enter")
    wait_until(page, "document.getElementById('address-result').textContent.indexOf('is in Ward') >= 0", timeout=10)
    result = page.text_content("#address-result")
    # Ward 2's own members, then those elected citywide, from the page's lists.
    assert result.startswith("12 Main St is in Ward 2.") and "Blake Example" in result and "Indy Example" in result
    assert "Finley Example" in result and "Avery Example" not in result
    assert page.get_attribute("#ward-2", "class") == "is-selected" or page.locator("#ward-map").is_hidden()
    results = axe.run(page, options={"runOnly": {"type": "tag", "values": WCAG_TAGS}})
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    for text, said in ADDRESSES:
        page.fill("#address-q", text)
        page.click("#address-form button[type=submit]")
        wait_until(page, "document.querySelector('#address-result p') && "
                         "!document.querySelector('#address-result p').textContent.startsWith('Looking')", timeout=5)
        assert page.text_content("#address-result p").startswith(said), text
    # A near match is offered as a button, chosen with the keyboard.
    page.fill("#address-q", "12 Kilby St")
    page.keyboard.press("Enter")
    page.wait_for_selector("#address-result .choices button")
    assert page.locator("#address-result .choices button").all_text_contents() == ["12 N Kilby St", "12 S Kilby St"]
    page.focus("#address-result .choices button >> nth=1")
    page.keyboard.press("Enter")
    wait_until(page, "document.getElementById('address-result').textContent.startsWith('12 S Kilby St is in Ward 4.')")
    # The address goes nowhere: every request is to the site itself, or for the map's images.
    assert all(u.startswith(server_url) or u.startswith("https://tile.openstreetmap.org/") for u in requests)
    assert not any("Kilby" in u or "Main" in u for u in requests)
    context.close()
    assert results.violations_count == 0, format_violations(results)
    assert overflow <= 0


def test_address_from_the_street_lookup(browser, server_url):
    """The street lookup's result links its street's ward: the street after "#", never sent."""
    page = browser.new_page()
    page.goto(server_url + "/officials/#address=Pleasant%20St")
    wait_until(page, "document.getElementById('address-result').textContent.startsWith('Pleasant St is in Ward 2.')",
               timeout=10)
    page.close()


# The sample towns' own pages (tests/test_sample_towns.py): the home page and meetings,
# from meeting systems other than Gloucester's, and Manchester's New Hampshire budget and
# schools pages. The rest are Gloucester's data, checked above. As on a daily run
# (site_checks/pages.py), every hand-written page and the first and largest of each record
# template, in light mode only, as site_checks/ checks a town.
OWN_PAGES = {"manchester": ("/meetings/", "/budget/", "/schools/"), "malden": ("/meetings/",),
             "wallingford": ("/meetings/",)}


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("town", OWN_PAGES)
def test_sample_town_pages(browser, axe, town, viewport, request):
    site = request.getfixturevalue(f"{town}_site").dir
    paths = [build_site.url_for(p.relative_to(site)) for p in site.rglob("*.html") if p.name != "404.html"]
    paths = [p for p in paths if p == "/" or p.startswith(OWN_PAGES[town])]

    def size(path):
        return (site / path.lstrip("/") / "index.html").stat().st_size

    with serve(site) as url:
        for path in sample(paths, size, HAND_WRITTEN):
            test_axe_no_violations(browser, axe, url, path, viewport, "light")
            if viewport == "phone":
                test_reflows_without_horizontal_scroll(browser, url, path)


# The weekly digest's signup where it's placed (site/templates/digest_line.html): under the home page's heading, at
# the end of a meeting's page, and in the footer of the others.
SIGNUP_PAGES = {"/": "home-signup", "/meetings/2026-08-25-city-council/": "meeting-signup", "/about/": "footer-signup"}


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_the_signup_in_spanish(browser, axe, built, viewport):  # noqa: F811
    """The signup on the Spanish pages, where axe's other checks (the English pages) don't look."""
    out, _, _ = built
    with serve(out) as url:
        for path in SIGNUP_PAGES:
            test_axe_no_violations(browser, axe, url, "/es" + path, viewport, "light")
            if viewport == "phone":
                test_reflows_without_horizontal_scroll(browser, url, "/es" + path)


# Every layout shift as the page loaded, and what each moved. Measured with the web fonts blocked (fallback fonts
# only), so a font swapping in, which moves text on every page alike, isn't counted: what's left is what the
# page's own content did.
LAYOUT_SHIFTS = """new Promise((done) => {
  const shifts = [];
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      shifts.push({ value: e.value, moved: e.sources.map((s) => s.node ? (s.node.id || s.node.className || s.node.nodeName) : "?") });
    }
  }).observe({ type: "layout-shift", buffered: true });
  setTimeout(() => done(shifts), 500);
})"""


@pytest.mark.parametrize("path, form_id", SIGNUP_PAGES.items())
def test_the_signup_moves_nothing_on_a_phone(browser, server_url, path, form_id):
    """The signup is in the page as it's served, so nothing shifts as it loads; its field is named by its label;
    on a meeting's page it's after the meeting's own content, so it never moves that down."""
    context = browser.new_context(viewport=VIEWPORTS["phone"])
    page = context.new_page()
    page.route("**/*.woff2", lambda route: route.abort())
    page.goto(server_url + path)
    page.wait_for_load_state("load")
    shifts = page.evaluate(LAYOUT_SHIFTS)
    box = page.locator(f"#{form_id}").bounding_box()
    agenda = page.locator("#agenda").bounding_box() if form_id == "meeting-signup" else None
    field = page.locator(f"#{form_id}").get_by_label("Get the week ahead by email")
    named = field.count() == 1 and field.get_attribute("id") == f"{form_id}-email"
    context.close()
    assert not shifts, f"{path}: layout shifts {shifts}"
    assert box and box["width"] <= VIEWPORTS["phone"]["width"]
    assert named
    if agenda:
        assert box["y"] > agenda["y"]


def test_the_signup_works_without_javascript(browser, server_url):
    """A plain form: with scripts off, it posts the address and the page's language to /digest/subscribe, and the
    browser follows the Worker's answer (stood in for here) to the "check your email" page."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    posted = []

    def worker(route):
        posted.append((route.request.method, route.request.post_data))
        route.fulfill(status=303, headers={"Location": server_url + "/digest/thanks/"})
    page.route("**/digest/subscribe", worker)
    page.goto(server_url + "/meetings/2026-08-25-city-council/")
    page.fill("#meeting-signup-email", "reader@example.org")
    page.click("#meeting-signup button[type=submit]")
    page.wait_for_url("**/digest/thanks/")
    heading = page.locator("h1").inner_text()
    context.close()
    assert heading == "Check your email"
    [(method, body)] = posted
    assert method == "POST" and "email=reader%40example.org" in body and "lang=en" in body and "website=" in body
