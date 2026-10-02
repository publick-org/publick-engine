"""One town's built site: every page and link is in place, and every page (or,
on a daily run, a sample of them: see pages.py) passes the automated WCAG 2.2 AA
checks at desktop and phone widths. The sites have only a light theme, and every
page says so (color-scheme: light), so a reader in dark mode sees the same page;
the checks hold every page to that rather than running the browser checks twice. The engine's own tests (tests/) cover the same ground in more depth
against saved Gloucester data."""

import time
from html.parser import HTMLParser
from urllib.parse import urlparse

import pytest

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
    (hreflang), and that page exists."""
    if len(PREFIXES) < 2:
        pytest.skip("The site is in one language.")
    base = f"https://{config['site']['domain']}"
    for path in page_files:
        if path.name == "404.html":
            continue
        alternates = {a["hreflang"]: a["href"] for t, a in parse(path).attrs if t == "link" and a.get("hreflang")}
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
    context.close()
    problems = "\n".join(f"- [{v['impact']}] {v['id']}: {v['help']}" for v in results.response["violations"])
    assert results.violations_count == 0, f"{path} ({viewport}):\n{problems}"
    assert overflow <= 0, f"{path} scrolls sideways by {overflow}px ({viewport})"


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
