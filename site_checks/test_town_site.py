"""One town's built site: every page and link is in place, and every page (or,
on a daily run, a sample of them: see pages.py) passes the automated WCAG 2.2 AA
checks at desktop and phone widths. The sites have only a light theme, and every
page says so (color-scheme: light), so a reader in dark mode sees the same page;
the checks hold every page to that rather than running the browser checks twice. The engine's own tests (tests/) cover the same ground in more depth
against saved Gloucester data."""

import re
import time
from html.parser import HTMLParser
from urllib.parse import urlparse

import pytest

from pipeline import build_site
from site_checks.conftest import BROWSER_PATHS, PREFIXES

WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
VIEWPORTS = {"desktop": {"width": 1280, "height": 900}, "phone": {"width": 320, "height": 640}}
# Every page, or a sample on a daily run (site_checks/pages.py).
PATHS = BROWSER_PATHS + [prefix + "/no-such-page/" for prefix in PREFIXES.values()]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs, self.links, self.ids = [], [], [], set()
        self.html_lang, self.title, self._in_title = None, "", False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        self.attrs.append((tag, attrs))
        if tag == "html":
            self.html_lang = attrs.get("lang")
        self._in_title = self._in_title or tag == "title"
        if "id" in attrs:
            self.ids.add(attrs["id"])
        self.links += [attrs[k] for k in ("href", "src") if attrs.get(k)]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def parse(path):
    p = PageParser()
    p.feed(path.read_text(encoding="utf-8"))
    return p


def test_expected_pages_and_support_files(site_dir, config):
    for rel in ("sitemap.xml", "robots.txt", ".nojekyll"):
        assert (site_dir / rel).exists(), rel
    for prefix in PREFIXES.values():
        folder = site_dir / prefix.lstrip("/")
        for rel in ("index.html", "404.html", "about/accessibility/index.html"):
            assert (folder / rel).exists(), prefix + "/" + rel
        for section in config["sections"]:
            assert (folder / section["slug"] / "index.html").exists(), prefix + "/" + section["slug"]
    assert (site_dir / "CNAME").read_text().strip() == config["site"]["domain"]


def page_language(site_dir, path) -> str:
    """The language a page is in, from its folder: /es/... is Spanish, the rest English."""
    first = path.relative_to(site_dir).parts[0]
    return next((lang for lang, prefix in PREFIXES.items() if prefix == "/" + first), "en")


def test_every_page_has_accessible_structure(site_dir, page_files):
    titles = set()
    for path in page_files:
        p = parse(path)
        lang = page_language(site_dir, path)
        assert p.html_lang == lang, path
        # Titles are unique within a language (a title not translated yet is the English one).
        assert p.title.strip() and (lang, p.title) not in titles, f"{path}: missing or duplicate <title>"
        titles.add((lang, p.title))
        assert p.tags.count("h1") == 1 and p.tags.count("main") == 1, f"{path}: needs one <h1> and one <main>"
        assert next(a for t, a in p.attrs if t == "a").get("href") == "#main", f"{path}: skip link must come first"
        assert all("alt" in a for t, a in p.attrs if t == "img"), f"{path}: <img> without alt"
        assert ("meta", {"name": "color-scheme", "content": "light"}) in p.attrs, \
            f"{path}: must declare color-scheme light, or dark mode needs the browser checks again"


def test_internal_links_resolve(site_dir, page_files):
    for path in page_files:
        p = parse(path)
        for link in p.links:
            url = urlparse(link)
            if url.scheme or url.netloc or link.startswith("mailto:"):
                continue
            if link.startswith("#"):
                assert link[1:] in p.ids, f"{path}: broken anchor {link}"
                continue
            target = site_dir / url.path.lstrip("/")
            if url.path.endswith("/"):
                target = target / "index.html"
            assert target.exists(), f"{path.relative_to(site_dir)}: broken link {link}"
            if url.fragment:
                assert url.fragment in parse(target).ids, f"{path}: broken anchor {link}"


def test_every_page_links_its_other_languages(site_dir, page_files, config):
    """On a site in more than one language, every page names each language's version
    (hreflang), and that page exists. A page in English only for now (build_site.ENGLISH_ONLY_FOLDERS)
    names none, and has no other version."""
    if len(PREFIXES) < 2:
        pytest.skip("The site is in one language.")
    base = f"https://{config['site']['domain']}"
    for path in page_files:
        if path.name == "404.html":
            continue
        alternates = {a["hreflang"]: a["href"] for t, a in parse(path).attrs if t == "link" and a.get("hreflang")}
        if path.relative_to(site_dir).parts[0] in build_site.ENGLISH_ONLY_FOLDERS:
            assert not alternates, f"{path}: hreflang links on a page in English only"
            continue
        assert set(alternates) == set(PREFIXES) | {"x-default"}, f"{path}: hreflang links {sorted(alternates)}"
        for lang, url in alternates.items():
            assert url.startswith(base + "/"), f"{path}: {url}"
            target = site_dir / url.removeprefix(base + "/")
            assert (target / "index.html" if url.endswith("/") else target).exists(), f"{path}: {lang} version {url} is missing"


playwright_api = pytest.importorskip("playwright.sync_api")
axe_module = pytest.importorskip("axe_playwright_python.sync_playwright")


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def axe():
    return axe_module.Axe()


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("path", PATHS)
def test_axe_no_violations(browser, axe, server_url, path, viewport):
    context = browser.new_context(viewport=VIEWPORTS[viewport])
    page = context.new_page()
    page.goto(server_url + path)
    results = axe.run(page, options={"runOnly": {"type": "tag", "values": WCAG_TAGS}})
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    # A meeting's time (or "Time TBA", longer in Spanish) must fit its column, not run into the name beside it.
    crowded = page.evaluate("""() => [...document.querySelectorAll('.meeting-when')]
        .filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent.trim())""")
    context.close()
    problems = "\n".join(f"- [{v['impact']}] {v['id']}: {v['help']}" for v in results.response["violations"])
    assert results.violations_count == 0, f"{path} ({viewport}):\n{problems}"
    assert overflow <= 0, f"{path} scrolls sideways by {overflow}px ({viewport})"
    assert not crowded, f"{path}: meeting times wider than their column ({viewport}): {crowded[:3]}"


def test_skip_link_and_404(browser, server_url):
    context = browser.new_context()
    page = context.new_page()
    page.goto(server_url + "/")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.textContent.trim()") == "Skip to main content"
    page.keyboard.press("Enter")
    end = time.monotonic() + 2
    while page.evaluate("document.activeElement && document.activeElement.id") != "main" and time.monotonic() < end:
        page.wait_for_timeout(50)
    assert page.evaluate("document.activeElement.id") == "main"
    for prefix in PREFIXES.values():
        assert page.goto(server_url + prefix + "/no-such-page/").status == 404
    context.close()


# ---- Every gap says why (pipeline/absences.py) ---------------------------------------

DASH_CELL = re.compile(r"<td[^>]*>\s*–\s*</td>")
# A section heading of a meeting page, and what follows it up to the next heading.
SECTION = re.compile(r'<h2 id="(minutes|agenda)">.*?</h2>(.*?)(?=<h2|</main>)', re.S)


def test_every_dash_is_explained(site_dir, page_files):
    """A table that shows – in place of a figure says what it means on the same page."""
    for path in page_files:
        html = path.read_text(encoding="utf-8")
        if DASH_CELL.search(html):
            assert "dash-legend" in html, f"{path.relative_to(site_dir)}: a – in a table with nothing saying what it means"


def test_meeting_pages_say_why_something_is_missing(site_dir):
    """No meeting page has an empty Agenda or minutes section, and a meeting that wasn't held
    never says its minutes are still to come."""
    for path in site_dir.glob("**/meetings/*/index.html"):
        html = path.read_text(encoding="utf-8")
        for name, body in SECTION.findall(html):
            assert re.sub(r"<[^>]+>|\s", "", body), f"{path.relative_to(site_dir)}: empty {name} section"
        status = re.search(r'class="page-head" data-status="([a-z]+)"', html)
        if status and status.group(1) != "scheduled":
            assert 'data-gap="not_posted"' not in html, f"{path.relative_to(site_dir)}: says minutes are coming for a meeting not held"


def test_officials_page_says_why_there_is_no_ward_map(site_dir, config):
    """A ward map missing from a town with wards is explained; a town with none says nothing."""
    from pipeline import officials
    if not any(s["slug"] == "officials" for s in config["sections"]):
        pytest.skip("No Officials page.")
    if officials.at_large(config):
        pytest.skip("Every seat is at-large: no wards to map.")
    for prefix in PREFIXES.values():
        html = (site_dir / prefix.lstrip("/") / "officials" / "index.html").read_text(encoding="utf-8")
        assert 'id="wards"' in html or "data-gap=" in html, f"{prefix}/officials/: no ward map, and nothing says why"


def test_about_page_names_the_sections_a_town_lacks(site_dir, config):
    from pipeline import absences, states
    gaps = absences.not_covered(config, states.for_town(config))
    for prefix in PREFIXES.values():
        html = (site_dir / prefix.lstrip("/") / "about" / "index.html").read_text(encoding="utf-8")
        assert ('id="not-covered"' in html) == bool(gaps), f"{prefix}/about/"
