"""Structural checks on the built HTML: every page is well-formed for assistive
technology and every internal link resolves."""

import json
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlparse

import pytest

from pipeline import build_site, i18n, structured


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []
        self.links = []
        self.ids = set()
        self.html_lang = None
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        self.attrs.append((tag, attrs))
        if tag == "html":
            self.html_lang = attrs.get("lang")
        if tag == "title":
            self._in_title = True
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])

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


def test_expected_pages_exist(site_dir, config):
    assert (site_dir / "index.html").exists()
    assert (site_dir / "404.html").exists()
    for section in config["sections"]:
        assert (site_dir / section["slug"] / "index.html").exists(), section["slug"]
    assert (site_dir / "about" / "accessibility" / "index.html").exists()


def test_support_files(site_dir, config):
    assert (site_dir / "CNAME").read_text().strip() == config["site"]["domain"]
    assert (site_dir / ".nojekyll").exists()
    sitemap = (site_dir / "sitemap.xml").read_text()
    assert f"https://{config['site']['domain']}/311/" in sitemap
    assert "404" not in sitemap
    assert "sitemap.xml" in (site_dir / "robots.txt").read_text()
    # The icon at the root, where crawlers ask for it, and one sized for search results.
    assert (site_dir / "favicon.ico").read_bytes() == (site_dir / "static" / "favicon.ico").read_bytes()
    assert (site_dir / "favicon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert '<link rel="icon" href="/static/favicon-96.png" type="image/png" sizes="96x96">' in (site_dir / "index.html").read_text()
    assert (site_dir / "static" / "favicon-96.png").exists()


def test_footer_names_the_network(page_files, config):
    network = config["site"]["network"]
    for page in page_files:
        html = page.read_text()
        assert f"Part of {network}, a network of independent sites" in html, page


def test_share_image(site_dir, config):
    image = f"https://{config['site']['domain']}/static/share/{config['slug']}.png"
    assert f'<meta property="og:image" content="{image}?v=' in (site_dir / "311" / "index.html").read_text()
    assert (site_dir / "static" / "share" / f"{config['slug']}.png").exists()


def test_every_page_has_accessible_structure(page_files):
    titles = set()
    for path in page_files:
        p = parse(path)
        name = path.name if path.name != "index.html" else str(path.parent.name or "/")
        assert p.html_lang == "en", name
        assert p.title.strip(), f"{name}: missing <title>"
        assert p.title not in titles, f"{name}: duplicate <title> {p.title!r}"
        titles.add(p.title)
        assert p.tags.count("h1") == 1, f"{name}: needs exactly one <h1>"
        assert p.tags.count("main") == 1, f"{name}: needs one <main>"
        assert "header" in p.tags and "footer" in p.tags and "nav" in p.tags, name
        # Skip link is the first focusable element and targets <main>.
        first_link = next(a for t, a in p.attrs if t == "a")
        assert first_link.get("href") == "#main", f"{name}: skip link must come first"
        assert "main" in p.ids
        # Every <img> needs an alt attribute (empty is fine for decoration).
        for tag, attrs in p.attrs:
            if tag == "img":
                assert "alt" in attrs, f"{name}: <img> without alt"


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


@pytest.mark.parametrize("rel", ["about/index.html", "about/accessibility/index.html"])
def test_contact_route_present(site_dir, rel):
    html = (site_dir / rel).read_text()
    assert "/issues" in html
    assert 'href="mailto:gloucester-ma@publick.org"' in html


def test_report_buttons_open_an_email(site_dir):
    page = (site_dir / "meetings" / "decisions" / "index.html").read_text()
    assert 'class="report-link" href="mailto:gloucester-ma@publick.org?subject=Correction' in page


def test_meeting_pages(site_dir):
    page = (site_dir / "meetings" / "2026-09-28-licensing-board" / "index.html").read_text()
    assert "Cancelled" in page
    assert "Marked cancelled on the city calendar." in page
    hrc = (site_dir / "meetings" / "2026-09-28-human-rights-commission" / "index.html").read_text()
    assert "/meetings/agendas/20124.pdf" in hrc
    assert "scanned image" in hrc
    assert (site_dir / "meetings" / "agendas" / "20124.pdf").exists()
    arts = (site_dir / "meetings" / "2026-09-29-committee-for-the-arts" / "index.html").read_text()
    assert "Removed from the city calendar." in arts


def test_home_lists_this_weeks_meetings(site_dir):
    home = (site_dir / "index.html").read_text()
    assert "Coming up" in home and "Recently decided" in home
    for label in ("Open 311 requests", "Typical time for the city to acknowledge", "Average single-family tax bill", "Unemployment rate"):
        assert label in home
    assert "↓ 0.5 pts from July 2025" in home and "↑ 3.0% from last year" in home
    assert "/meetings/2026-10-05-city-council-ordinances-and-administration-committee/" in home


def test_outbound_and_pdf_links_open_in_new_tab_with_warning(page_files, config):
    """Visitors keep their place on the site; screen readers hear that a new tab opens."""
    import re
    own = {config["site"]["domain"], "www." + config["site"]["domain"]}
    checked = 0
    for path in page_files:
        html = path.read_text()
        for attrs, text in re.findall(r"<a\b([^>]*)>(.*?)</a>", html, re.S):
            href = re.search(r'href="([^"]*)"', attrs).group(1)
            url = urlparse(href)
            outbound = url.scheme in ("http", "https") and url.hostname not in own
            if outbound or url.path.endswith(".pdf"):
                checked += 1
                assert 'target="_blank"' in attrs and 'rel="noopener"' in attrs, f"{path}: {href}"
                assert "(opens in new tab)" in text, f"{path}: {href} missing new-tab warning"
            else:
                assert "target=" not in attrs, f"{path}: internal link {href} should open in place"
    assert checked > 0


def test_ai_preview_is_labeled_and_linked(site_dir):
    page = (site_dir / "meetings" / "2026-09-28-human-rights-commission" / "index.html").read_text()
    assert "Summary written by AI" in page
    assert "Read the full agenda" in page
    assert "https://www.gloucester-ma.gov/Archive.aspx?ADID=20124" in page
    assert "<script" not in page.split("transcript-body")[1][:2000]
    # The fixture agenda is a scan, so its full text is a labeled AI transcription.
    assert "Transcribed by AI from the city's PDF. Check the original before relying on it." in page


@pytest.mark.parametrize("record, shown, hidden", [
    ({"transcript": "# Minutes\n\nApproved.", "transcript_source": "pdf"},
     "From the city's PDF, word for word.", "Transcribed by AI"),
    ({"transcript": "# Minutes\n\nApproved.", "transcript_source": "ai"},
     "Transcribed by AI from the city's PDF.", "word for word"),
    # Saved before the source was recorded: those were all transcribed by AI.
    ({"transcript": "# Minutes\n\nApproved."}, "Transcribed by AI from the city's PDF.", "word for word"),
    # A scan, waiting for its transcription.
    ({"plain_text": "", "needs_transcript": True}, "The minutes are a scanned document. A readable version of the full text will be added here",
     "Read the full minutes"),
    # A PDF with text a screen reader can read: it's the full text.
    ({"plain_text": "Approved.", "needs_transcript": False}, "The full text is in the city's PDF, linked below.",
     "Read the full minutes"),
])
def test_full_text_says_where_it_came_from(record, shown, hidden):
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(build_site.SITE_DIR / "templates")), autoescape=True,
                      extensions=["jinja2.ext.i18n"])
    env.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True, pgettext=i18n.pgettext)
    env.policies["ext.i18n.trimmed"] = True
    env.filters.update(markdown=build_site.render_markdown, date=build_site.format_date, time=build_site.format_time,
                       timestamp=build_site.format_timestamp, filesize=build_site.format_bytes)
    html = env.from_string('{% from "macros.html" import full_text %}{{ full_text("minutes", r) }}').render(r=record)
    assert shown in html and hidden not in html


def test_scorecard_page(site_dir):
    page = (site_dir / "311" / "index.html").read_text()
    assert "Requests, past 12 months" in page
    assert "Ward 1" in page
    assert "CC BY-NC-SA 3.0" in page
    assert (site_dir / "311" / "methodology" / "index.html").exists()


def test_meeting_known_from_minutes(site_dir, data_dir):
    store = json.loads((data_dir / "meetings" / "meetings.json").read_text())
    archived = next(m for m in store.values() if m.get("source") == "archive")
    page = (site_dir / "meetings" / archived["slug"] / "index.html").read_text()
    assert "What was decided" in page
    assert "Decisions recorded" in page
    assert "Archive Center" in page
    assert f"/meetings/minutes/{archived['minutes'][0]['file']}" in page


def test_311_ward_and_category_pages_and_csv(site_dir):
    assert (site_dir / "311" / "ward" / "1" / "index.html").exists()
    categories = list((site_dir / "311" / "category").iterdir())
    assert categories
    header = (site_dir / "311" / "data" / "by-category.csv").read_text().splitlines()[0]
    assert header.startswith("category,requests,closed,still_open")


def test_311_monthly_charts_have_tables(site_dir):
    """The Accessibility page says every chart has a table with the same numbers."""
    for page in (site_dir / "311" / "ward" / "1" / "index.html", next((site_dir / "311" / "category").iterdir()) / "index.html"):
        html = page.read_text()
        chart = html.index('<div class="columns" role="img"')
        table = html[chart:html.index('<h2', chart)]
        assert "See as a table" in table and "<th scope=\"col\">Month</th>" in table
        first_row = re.search(r'<tr><th scope="row">([^<]+)</th><td class="num">([\d,]+)</td></tr>', table)
        assert first_row, page


def test_city_report_button_on_311_and_street_pages(site_dir, config):
    # The build marks it as off-site (class "external", a new tab, hidden text saying so).
    button = re.compile(r'<a class="[^"]*\bbutton\b[^"]*" href="' + re.escape(config["seeclickfix"]["report_url"])
                        + r'"[^>]*>Report a problem to the city<')
    ward = next((site_dir / "311" / "ward").iterdir())
    category = next((site_dir / "311" / "category").iterdir())
    for page in (site_dir / "311", site_dir / "streets", ward, category):
        assert len(button.findall((page / "index.html").read_text())) == 1, page


def test_meeting_list_preview_is_about_the_business(site_dir):
    home = (site_dir / "index.html").read_text()
    assert "operations director and a draft plan for recruiting a student member" in home
    assert "will meet on" not in home


def test_preview_line_fallbacks():
    from pipeline.build_site import preview_line
    assert preview_line({"preview": {"items": ["Budget transfer", "Grant acceptance."]}}) == "Budget transfer; Grant acceptance."
    assert preview_line({"minutes_summary": {"decisions": ["Approved X, 5-0.", "Denied Y"]}, "preview": {"headline": "H"}}) == "Approved X, 5-0."
    long = "Recommended the City Council approve payment of prior year invoices and obligations from the School CFO's memo dated August 31, 2026, in the amount of $51,317.23"
    clipped = preview_line({"minutes_summary": {"decisions": [long]}})
    assert len(clipped) <= 141 and clipped.endswith("…")
    assert preview_line({"minutes_summary": {"is_minutes": False, "decisions": []}, "preview": {"headline": "H"}}) == "H"
    assert preview_line({}) is None


def test_repeat_locations_page_and_maps(site_dir):
    import csv
    page = (site_dir / "311" / "repeat-locations" / "index.html").read_text()
    assert "<h1>Repeat locations</h1>" in page
    assert "/static/js/map.js?v=" in page
    main = (site_dir / "311" / "index.html").read_text()
    data = json.loads(main.split('id="map-data-recent">')[1].split("</script>")[0])
    assert data and all({"lat", "lng", "title", "url"} <= p.keys() for p in data)
    # The map is an extra: the same requests are listed as text, including any without a map location.
    assert main.count('href="https://seeclickfix.com/issues/') >= len(data)
    rows = list(csv.reader((site_dir / "311" / "data" / "recent-open.csv").open()))
    assert rows[0] == ["id", "submitted", "category", "location", "ward", "url"] and len(rows) >= len(data) + 1
    assert (site_dir / "311" / "data" / "repeat-locations.csv").exists()
    assert (site_dir / "static" / "vendor" / "leaflet" / "leaflet.js").exists()


def test_schools_page(site_dir):
    page = (site_dir / "schools" / "index.html").read_text()
    assert "Graduation rate" in page and "84.7%" in page and "State 89.3%" in page
    assert 'href="https://gloucesterschoolsreport.com"' in page
    assert 'href="/schools/"' in (site_dir / "index.html").read_text()


def test_meeting_search_index(site_dir):
    page = (site_dir / "meetings" / "search" / "index.html").read_text()
    url = re.search(r'data-index="([^"]+)"', page).group(1)
    assert url.startswith("/meetings/search-index.json?v=")
    index = json.loads((site_dir / "meetings" / "search-index.json").read_text())
    assert index and all(m["url"].startswith("/meetings/") and m["board"] and m["date"] for m in index)
    assert [m["date"] for m in index] == sorted((m["date"] for m in index), reverse=True)
    assert any(d["text"] and "**" not in d["text"] for m in index for d in m["docs"])
    assert 'action="/meetings/search/"' in (site_dir / "meetings" / "index.html").read_text()


def test_plain_text():
    assert build_site.plain_text("# Agenda\n\n**1.** Call to order  |  x\n") == "Agenda\n1. Call to order x"


def test_budget_page(site_dir):
    page = (site_dir / "budget" / "index.html").read_text()
    assert "$140.6M" in page and "Fiscal year 2025" in page
    assert "State median $4,297" in page
    assert 'href="/budget/"' in (site_dir / "index.html").read_text()
    spending = (site_dir / "budget" / "data" / "spending.csv").read_text().splitlines()
    assert spending[0].startswith("fiscal_year,total,") and spending[-1].startswith("2025,140559783,")
    reserves = (site_dir / "budget" / "data" / "reserves.csv").read_text().splitlines()
    assert reserves[-1] == "2026,4112161,"


def test_housing_page(site_dir):
    page = (site_dir / "housing" / "index.html").read_text()
    assert "8.04%" in page and "$601K" in page and "±$17,698" in page
    assert "2020–2024" in page and "partly estimated" in page
    permits = (site_dir / "housing" / "data" / "permits.csv").read_text().splitlines()
    assert permits[0].startswith("year,homes,") and permits[-1].startswith("2025,77,")


def test_model_markdown_keeps_only_safe_links():
    html = build_site.render_markdown(
        "[a](javascript:alert(1)) [b](https://example.org) ![c](https://tracker.example/p.png) <script>x</script>")
    assert "javascript:" not in html and "<img" not in html and "<script" not in html
    assert '<a href="https://example.org" rel="nofollow ugc">b</a>' in html and "c" in html


def test_capitalize_first_escapes_plain_text_but_not_safe_html():
    from markupsafe import Markup
    assert build_site.capitalize_first("agenda items & <b>permits</b>") == "Agenda items &amp; &lt;b&gt;permits&lt;/b&gt;"
    assert build_site.capitalize_first(Markup('<a href="mailto:x@y.org">write</a>')) == '<a href="mailto:x@y.org">write</a>'
    assert build_site.capitalize_first(Markup("write to <a>us</a>")) == "Write to <a>us</a>"


def test_pages_set_a_content_security_policy(page_files):
    for path in page_files:
        html = path.read_text()
        assert "Content-Security-Policy" in html and "script-src 'self'" in html, path


def test_glossary_matches_terms_and_boards():
    from pipeline.build_site import glossary_for
    entries = [{"term": "PERAC", "definition": "d"}, {"term": "executive session", "definition": "d"},
               {"term": "M.G.L.", "definition": "d"}, {"term": "COA", "definition": "d", "bodies": ["Historic District Commission"]}]
    meeting = {"body": "Council on Aging Board",
               "preview": {"summary": "Enter Executive Session under M.G.L. Ch. 30A.", "items": ["Review of COA budget"], "transcript": ""}}
    assert [e["term"] for e in glossary_for(meeting, entries)] == ["executive session", "M.G.L."]
    meeting = {"body": "Historic District Commission", "preview": {"summary": "A COA for 38 Pleasant St; perac is not an acronym here."}}
    assert [e["term"] for e in glossary_for(meeting, entries)] == ["COA"]
    assert glossary_for({"body": "X"}, entries) == []


def test_decisions_page_and_csv(site_dir):
    import csv
    page = (site_dir / "meetings" / "decisions" / "index.html").read_text()
    assert "<h1>Decisions</h1>" in page
    rows = list(csv.reader((site_dir / "meetings" / "data" / "decisions.csv").open()))
    assert rows[0] == ["meeting_date", "board", "kind", "decision", "meeting_url", "minutes_url"]
    assert {r[2] for r in rows[1:]} <= {"decided", "recommended", "procedural"}
    home = (site_dir / "index.html").read_text()
    assert ('href="/meetings/decisions/"' in home) == (len(rows) > 1)


def test_decisions_are_sorted_for_residents():
    from pipeline.build_site import decision_kind, emphasize_money
    assert decision_kind("The City Council voted 9-0 to approve the new trash fee schedule.") == "decided"
    assert decision_kind("The Budget & Finance Committee voted 3-0 to recommend approving the new trash fee schedule.") == "recommended"
    assert decision_kind("Recommended, 3-0, that the City Council accept a $16,800 grant.") == "recommended"
    assert decision_kind("Approved the site plan as recommended by staff.") == "decided"
    assert decision_kind("Approved the minutes for July 9, 2026 as presented.") == "procedural"
    assert decision_kind("Continued PH2026-011 to the July 28, 2026 City Council meeting.") == "procedural"
    assert decision_kind("The City Council voted 9-0 to refer CC#2026-020 to the O&A Committee.") == "procedural"
    assert decision_kind("Closed the public hearing for NOI 028-3150 10 Dennison Street.") == "procedural"
    assert str(emphasize_money("Paid $3,100, $51,317.23 & $1.2 million <x>")) == (
        "Paid <strong>$3,100</strong>, <strong>$51,317.23</strong> &amp; <strong>$1.2 million</strong> &lt;x&gt;")


def test_summaries_in_lists_say_they_are_ai(site_dir):
    """A summary shown in a list says it's AI-written, after the summary, where the meeting's own
    details are; a row without one doesn't. The feed says so in words."""
    home = (site_dir / "index.html").read_text()
    rows = re.findall(r'<li class="meeting-item">.*?</li>', home, re.S)
    assert rows and all(("meeting-preview" in r) == ("AI summary" in r) for r in rows)
    assert any("AI summary · Agenda posted" in r for r in rows) and any("AI summary · Minutes posted" in r for r in rows)
    for row in rows:
        if "AI summary" in row:
            assert row.index("meeting-preview") < row.index("AI summary")
    feed = (site_dir / "feed.xml").read_text()
    for item in re.findall(r"<description>(.*?)</description>", feed)[1:]:
        assert item in ("Agenda posted.", "Minutes posted.") or item.endswith(
            ("(AI summary of the agenda. Check the original.)", "(AI summary of the minutes. Check the original.)"))


def test_public_hearings_say_their_summary_is_ai(tmp_path):
    """The public hearings box: the date, the summary on its own line, then "AI summary" under it."""
    import shutil
    from conftest import BUILT_AT, DATA_DIR
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    for path in (data / "summaries").glob("*.json"):
        rec = json.loads(path.read_text())
        if rec.get("kind") == "agenda":
            rec["items"] = [*rec.get("items", []), "Public hearing on the Harbor Plan"]
            path.write_text(json.dumps(rec))
    build_site.build("gloucester", tmp_path / "site", data_dir=data, now=BUILT_AT)
    home = (tmp_path / "site" / "index.html").read_text()
    box = home[home.index('class="notice"'):]
    box = box[:box.index("</div>")]
    hearing = re.search(r"<li>.*?</li>", box, re.S).group(0)
    assert re.search(r'<span class="item-detail">[^<]+</span><span class="meeting-meta">AI summary</span></li>$', hearing)
    assert hearing.index("<time") < hearing.index("AI summary")


def test_about_page_says_how_to_reuse(site_dir, config):
    """The data license: CC BY 4.0, the credit line to copy, what keeps its own terms, and the full
    terms in the network repository; every page's footer links to it."""
    about = (site_dir / "about" / "index.html").read_text()
    reuse = about[about.index('id="reuse"'):about.index('id="privacy"')]
    assert "https://creativecommons.org/licenses/by/4.0/" in reuse and "(CC BY 4.0)" in reuse
    # The test town's network has no website yet, so the credit line names it alone.
    assert "Summary by Publick, AI-generated from the city's agenda." in reuse
    assert "CC BY-NC-SA 3.0" in reuse and "public records" in reuse
    assert f'href="{config["site"]["repo_url"]}/blob/main/LICENSE"' in reuse
    home = (site_dir / "index.html").read_text()
    assert 'href="/about/#reuse">free to reuse with credit</a>' in home[home.index("site-footer"):]


def test_311_pages_say_when_seeclickfix_was_last_read(site_dir, data_dir):
    fetched = json.loads((data_dir / "311" / "scorecard.json").read_text())["fetched_at"]
    for page in ("311/index.html", "311/repeat-locations/index.html"):
        assert f'Updated <time datetime="{fetched}">' in (site_dir / page).read_text(), page


def test_feed_is_valid_rss(site_dir):
    import xml.dom.minidom
    feed = xml.dom.minidom.parse(str(site_dir / "feed.xml"))
    assert feed.getElementsByTagName("channel")
    home = (site_dir / "index.html").read_text()
    assert 'type="application/rss+xml"' in home and 'href="/feed.xml"' in home


def test_recommendations_lead_with_the_action():
    from pipeline.build_site import decision_text, tidy_recommendation
    assert tidy_recommendation("Voted 3 in favor, 0 opposed to recommend that the City Council permit National Grid "
                               "(PP#2026-004) to install one pole.") == "Permit National Grid (PP#2026-004) to install one pole. (3–0)"
    assert tidy_recommendation("The Budget & Finance Committee voted 3-0 to recommend approving the new trash fee "
                               "schedule.") == "Approve the new trash fee schedule. (3–0)"
    # A vote against, or wording that isn't standard, is shown exactly as written.
    against = "Voted by roll call 0 in favor, 3 opposed, not to recommend that the City Council amend Chapter 9."
    assert tidy_recommendation(against) == against
    odd = "The committee voted 2-0 (1 absent) to recommend appointing Rosalie Nicastro."
    assert tidy_recommendation(odd) == odd
    assert str(decision_text("Appointed Joseph A. Orlando, TTE 2/14/2029.")) == "Appointed Joseph A. Orlando, term ends 2/14/2029."


def test_report_links_name_the_page(site_dir, config):
    from urllib.parse import unquote
    from pipeline.build_site import report_link
    site = dict(config["site"], contact_email="")
    github = report_link(site, "https://example.org", "/meetings/x/", "Board, June 1")
    assert github.startswith(config["site"]["repo_url"] + "/issues/new?") and "example.org%2Fmeetings%2Fx%2F" in github
    email = report_link(dict(site, contact_email="fix@example.org"), "https://example.org", "/x/", "Page")
    assert email.startswith("mailto:fix@example.org?subject=Correction%3A%20Page") and "https://example.org/x/" in unquote(email)
    page = next((site_dir / "meetings").glob("*/index.html")).read_text()
    assert "Report a problem with this page" in page


# ---- Officials ----------------------------------------------------------------

def test_officials_page(site_dir, config):
    page = (site_dir / "officials" / "index.html").read_text()
    for body in config["officials"]["bodies"]:
        assert f'<h2 id="{body["name"].lower().replace(" ", "-")}">{body["name"]}</h2>' in page
        for m in body["members"]:
            assert m["name"] in page
    # Every ward in the ward file is listed, and a ward seat links to its ward.
    for ward in range(1, 6):
        assert f'<li id="ward-{ward}" data-ward="{ward}">' in page
    assert '<a href="#ward-3">Ward 3</a>' in page and "Council President" in page
    assert '<a href="#city-council-casey-example">Casey Example</a>' in page
    # A district of wards is listed under each of its wards, and links them.
    assert page.count('School Committee (District A): <a href="#school-committee-jamie-example">Jamie Example</a>') == 2
    ward_2 = page.split('<li id="ward-2"')[1].split('<li id="ward-3"')[0]
    assert ward_2.index("Indy Example") < ward_2.index("Jamie Example")
    assert 'District A<span class="cell-note"> (Wards <a href="#ward-1">1</a> and <a href="#ward-2">2</a>)</span>' in page
    # The mayor is on two bodies: a row on each, with different anchors.
    assert 'id="mayor-morgan-example"' in page and 'id="school-committee-morgan-example"' in page
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert "January 2028" in page and 'href="tel:978555-0100"' in page
    # The location note says it never leaves the browser.
    assert "It isn't sent to Publick or anyone else, and it isn't saved." in page
    assert '<a href="/meetings/boards/city-council/">City Council meetings</a>' in page
    wards_url = re.search(r'data-wards="(/officials/wards\.json\?v=\w+)"', page).group(1)
    shapes = json.loads((site_dir / wards_url.split("?")[0].lstrip("/")).read_text())
    assert [w["ward"] for w in shapes] == ["1", "2", "3", "4", "5"]
    assert all(w["polygons"] and w["outline"] for w in shapes)
    about = (site_dir / "about" / "index.html").read_text()
    assert "<strong>Elected officials:</strong> listed by hand" in about
    assert 'If you use "Find my ward," your location is checked on your device.' in about


def test_officials_outline_leaves_out_shared_edges():
    from pipeline.officials import outline
    left = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
    right = [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]
    (line,) = outline([left, right])
    edges = {frozenset((tuple(a), tuple(b))) for a, b in zip(line, line[1:])}
    assert frozenset(((1, 0), (1, 1))) not in edges and len(edges) == 6 and line[0] == line[-1]


@pytest.mark.parametrize("change, error", [
    ({"checked": "soon"}, "checked"),
    ({"members": [{"name": "A", "seat": "Ward 9", "ward": 9}]}, "isn't in the ward file"),
    ({"members": [{"name": "A", "seat": "At-large", "term_ends": "January 2028"}]}, "must be a month"),
    ({"members": [{"name": "A"}]}, "needs a name and a seat"),
    ({"members": [{"name": "A", "seat": "At-large", "district": 2}]}, "unknown district"),
    ({"members": [{"name": "A", "seat": "District C", "wards": [1, 9]}]}, "isn't in the ward file"),
    ({"members": [{"name": "A", "seat": "Ward 1", "ward": 1, "wards": [1]}]}, "both ward and wards"),
])
def test_officials_config_is_checked(config, data_dir, change, error):
    import copy
    from pipeline import officials
    bad = copy.deepcopy(config)
    if "checked" in change:
        bad["officials"]["checked"] = change["checked"]
    else:
        bad["officials"]["bodies"][1]["members"] = change["members"]
    with pytest.raises(SystemExit, match=error):
        officials.load(bad, data_dir)


def test_officials_section_needs_its_table(config, data_dir):
    import copy
    from pipeline import officials
    assert officials.load(config, data_dir)["wards"][0]["members"][0]["name"] == "Avery Example"
    town = copy.deepcopy(config)
    del town["officials"]
    with pytest.raises(SystemExit, match=r"needs an \[officials\] table"):
        officials.load(town, data_dir)


def test_street_lookup_example_is_one_of_the_towns_own_streets(site_dir):
    """Not every town has a Pleasant Street: the example is the street with the most on it."""
    index = json.loads(next((site_dir / "streets").glob("streets.json")).read_text())
    from pipeline.build_site import example_street
    example = example_street(index)
    assert example in {s["name"] for s in index["streets"].values()}
    for page in (site_dir / "index.html", site_dir / "streets" / "index.html"):
        assert f'such as "{example}"' in page.read_text()
    assert "Pleasant Street" not in (site_dir / "streets" / "index.html").read_text()


def test_example_street_picks_the_busiest_and_needs_streets():
    from pipeline.build_site import example_street
    index = {"streets": {"A": {"name": "Elm Street", "meetings_total": 1, "requests_total": 1},
                         "B": {"name": "Water Street", "meetings_total": 5, "permits_total": 2}}}
    assert example_street(index) == "Water Street"
    assert example_street({"streets": {}}) is None


def test_home_shows_the_main_boards_and_what_can_be_read_now():
    """Up to six meetings in full: the main boards' first, then those with something to read now
    (an agenda summary, a public hearing), then the soonest of the rest, in date order. The rest
    one line each, a tap away, a cancelled one last; one left over isn't hidden."""
    from pipeline.build_site import home_meetings, main_boards

    def week(*rows):
        return [{"body_en": body, "preview": {"summary": "s"} if kind == "summary" else None,
                 "public_hearing": kind == "hearing", "status": "cancelled" if kind == "cancelled" else "scheduled"}
                for body, kind in rows]

    main = main_boards({"meetings": {}})
    busy = week(("Waterways Board", ""), ("Conservation Commission", "summary"), ("City Council", ""),
                ("Harbor Plan Committee", ""), ("School Committee", ""), ("Planning Board", "hearing"),
                ("Council on Aging", "summary"), ("Board of Health", "summary"), ("Licensing Board", "cancelled"),
                ("Trust Fund Commissioners", ""))
    home = home_meetings(busy, main)
    assert [m["body_en"] for m in home["full"]] == ["Conservation Commission", "City Council", "School Committee",
                                                    "Planning Board", "Council on Aging", "Board of Health"]
    assert [m["body_en"] for m in home["more"]] == ["Waterways Board", "Harbor Plan Committee", "Trust Fund Commissioners",
                                                    "Licensing Board"]
    assert home["hidden"] and home["count"] == 10
    # A quiet week: the soonest meetings fill the six places, so the page never looks empty.
    quiet = home_meetings(week(("Public Utilities Commission", "summary"), ("Planning & Zoning Commission", ""),
                               ("Economic Development Commission", ""), ("Veterans Memorial Committee", ""),
                               ("Ordinance Committee", ""), ("Personnel Appeals Board", "cancelled"),
                               ("Inland Wetlands Commission", ""), ("Conservation Commission", "")), ["Town Council"])
    assert len(quiet["full"]) == 6 and "Personnel Appeals Board" not in [m["body_en"] for m in quiet["full"]]
    assert [m["body_en"] for m in quiet["more"]] == ["Conservation Commission", "Personnel Appeals Board"]
    # One left over is shown, not hidden behind a tap.
    seven = home_meetings(week(*[(f"Board {i}", "") for i in range(7)]), main)
    assert len(seven["more"]) == 1 and not seven["hidden"]
    # A town's own governing body and main boards.
    assert main_boards({"meetings": {"governing_body": "Town Council", "main_boards": ["Board of Finance"]}}) == [
        "Town Council", "School Committee", "Board of Education", "Board of Finance"]


def test_home_page_layout(site_dir):
    import re
    home = (site_dir / "index.html").read_text()
    week = home[home.index('id="coming-up"'):home.index('id="decided"') if 'id="decided"' in home else home.index('id="numbers"')]
    count = re.search(r"(\d+) public meetings? this week", week)
    assert count
    shown = week.count('class="meeting-item"')
    assert shown == int(count.group(1))
    # Each meeting says its day, since the list isn't grouped by day.
    assert week.count('class="meeting-day"') == shown
    if "more-meetings" in week:
        assert week.count('class="meeting-item"', week.index("more-meetings")) >= 2
        assert re.search(r"Show \d+ more meetings? this week", week)
    decided = home[home.index('id="decided"'):home.index('id="numbers"')] if 'id="decided"' in home else ""
    assert decided.count('class="meeting-item"') <= 3
    # Decisions, then the numbers, then one row of links.
    assert home.index('id="coming-up"') < home.index('id="numbers"') < home.index('id="explore"')
    assert 'class="meeting-item single"' not in home


def structured_data(path) -> list[dict]:
    """A page's JSON-LD items."""
    return [json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', path.read_text())]


def test_meeting_descriptions_lead_with_the_summary_and_say_it_is_ai(site_dir):
    """A search result shows the description on its own, so a summary's headline says it was written by AI."""
    page = (site_dir / "meetings" / "2026-07-14-city-council" / "index.html").read_text()
    assert '<meta name="description" content="AI summary of the minutes: Approved a site plan for 12 Main St.">' in page
    # A meeting with no summary keeps saying what the page has.
    plain = [p.read_text() for p in (site_dir / "meetings").glob("20*/index.html") if "AI summary of the" not in p.read_text()]
    assert plain and all(re.search(r'<meta name="description" content="[^"]+ on \w+day, ', p) for p in plain)


def test_meetings_are_events_for_search_engines(site_dir):
    events = {}
    for path in (site_dir / "meetings").glob("20*/index.html"):
        for item in structured_data(path):
            if item["@type"] == "Event":
                events[path.parent.name] = item
            else:
                # Every meeting page says where it is in the site.
                assert item["@type"] == "BreadcrumbList"
                trail = item["itemListElement"]
                assert [t["item"] for t in trail][0] == "https://gloucester-ma.publick.org/meetings/"
                assert trail[-1]["item"] == f"https://gloucester-ma.publick.org/meetings/{path.parent.name}/"
    assert events
    event = events["2026-09-28-historical-commission"]
    assert event["startDate"] == "2026-09-28T18:30:00-04:00"
    assert event["eventStatus"] == "https://schema.org/EventScheduled"
    assert event["eventAttendanceMode"] == "https://schema.org/MixedEventAttendanceMode"
    assert event["location"][0]["address"]["addressLocality"] == "Gloucester"
    assert event["location"][1]["@type"] == "VirtualLocation"
    assert event["description"].startswith("AI summary of the agenda: ")
    # A meeting is an event only when the page says where it is.
    for slug, item in events.items():
        assert item["location"], slug


def test_event_says_only_what_the_listing_does():
    town = {"name": "Gloucester", "state": "Massachusetts", "state_abbr": "MA"}
    m = {"title": "Planning Board Meeting", "body": "Planning Board", "date": "2026-12-03", "start_time": "19:00",
         "end_time": None, "status": "cancelled", "listed": True, "location_name": "", "address": "", "location": "",
         "remote_url": "https://zoom.us/j/1", "correction": None}
    event = structured.event(m, "https://x/", "A &amp; B\n", town, "America/New_York")
    assert event["startDate"] == "2026-12-03T19:00:00-05:00"
    assert event["eventStatus"] == "https://schema.org/EventCancelled"
    assert event["eventAttendanceMode"] == "https://schema.org/OnlineEventAttendanceMode"
    assert event["description"] == "A & B"
    assert "endDate" not in event
    assert structured.event({**m, "remote_url": None}, "https://x/", "", town, "America/New_York") is None
    assert structured.event({**m, "listed": False}, "https://x/", "", town, "America/New_York") is None
    assert structured.event({**m, "correction": {"doubtful": True}}, "https://x/", "", town, "America/New_York") is None


def test_structured_data_cannot_end_its_script():
    html = structured.script({"@type": "Thing", "name": "</script><b>&"}, None)
    assert html.count("<script") == 1 and "</script><b>" not in html
    assert json.loads(re.search(r">(.*)</script>", html).group(1))["name"] == "</script><b>&"
    assert structured.script(None) == ""


def test_home_and_downloads_for_search_engines(site_dir):
    home = (site_dir / "index.html").read_text()
    assert "<title>Gloucester, MA: city meetings, agendas, and data | Gloucester Publick</title>" in home
    # A meeting's title in search results starts with the town and names the minutes or agenda
    # it has; its own name, on social cards and in breadcrumbs, doesn't.
    meetings = site_dir / "meetings"
    minutes = (meetings / "2026-07-14-city-council" / "index.html").read_text()
    assert "<title>Gloucester, MA City Council minutes, Tuesday, July 14, 2026 | Gloucester Publick</title>" in minutes
    assert '<meta property="og:title" content="City Council, Tuesday, July 14, 2026">' in minutes
    agenda = (meetings / "2026-09-28-historical-commission" / "index.html").read_text()
    assert "<title>Gloucester, MA Historical Commission agenda, Monday, September 28, 2026 | Gloucester Publick</title>" in agenda
    neither = (meetings / "2026-09-23-school-committee" / "index.html").read_text()
    assert "<title>Gloucester, MA School Committee, Wednesday, September 23, 2026 | Gloucester Publick</title>" in neither
    # So does every other section's; its heading and social card keep the short name.
    board = (meetings / "boards" / "school-committee" / "index.html").read_text()
    assert "<title>Gloucester, MA School Committee meetings and agendas | Gloucester Publick</title>" in board
    assert '<meta property="og:title" content="School Committee meetings">' in board
    decided = (meetings / "decisions" / "index.html").read_text()
    assert "<title>Gloucester, MA votes and decisions from meeting minutes | Gloucester Publick</title>" in decided
    assert "<title>Gloucester, MA 311 requests and response times | Gloucester Publick</title>" in (site_dir / "311" / "index.html").read_text()
    [site] = structured_data(site_dir / "index.html")
    assert site["@type"] == "WebSite" and site["name"] == "Gloucester Publick"
    assert site["url"] == "https://gloucester-ma.publick.org/"
    [decisions] = structured_data(site_dir / "meetings" / "decisions" / "index.html")
    assert decisions["@type"] == "Dataset"
    assert decisions["distribution"][0]["contentUrl"] == "https://gloucester-ma.publick.org/meetings/data/decisions.csv"
    assert decisions["license"] == "https://creativecommons.org/licenses/by/4.0/"
    [requests] = structured_data(site_dir / "311" / "index.html")
    assert requests["license"] == "https://creativecommons.org/licenses/by-nc-sa/3.0/"
    # Every file a dataset names is in the site.
    for item in (decisions, requests):
        for d in item["distribution"]:
            assert (site_dir / urlparse(d["contentUrl"]).path.lstrip("/")).exists(), d["contentUrl"]


def test_sitemap_dates_say_when_each_page_changed(site_dir):
    """Not the build's date for every page, which search engines would learn to ignore."""
    sitemap = (site_dir / "sitemap.xml").read_text()
    dates = dict(re.findall(r"<loc>https://gloucester-ma.publick.org([^<]*)</loc>(?:<lastmod>([^<]*)</lastmod>)?", sitemap))
    built = "2026-10-02"
    assert dates["/"] == dates["/meetings/"] == dates["/311/"] == built
    # A meeting's page: when its listing, documents or summaries last changed (the fixture's were fetched on September 26).
    assert dates["/meetings/2026-07-14-city-council/"] == "2026-09-26"
    # A board's page and the lists of past meetings: their newest meeting's.
    assert dates["/meetings/boards/city-council/"] == "2026-09-26"
    assert dates["/meetings/past/"] == max(d for u, d in dates.items() if u.startswith("/meetings/20"))
    # A page that can't say when it changed has no date.
    assert dates["/about/accessibility/"] == "" and dates["/311/methodology/"] == ""
    assert all(d <= built for d in dates.values())


def test_meeting_lastmod():
    m = {"first_seen": "2026-09-01T05:00:00-04:00", "agendas": [{"fetched_at": "2026-09-03T05:00:00-04:00"}], "minutes": [],
         "history": [{"at": "2026-09-05T05:00:00-04:00"}], "preview": {"generated_at": "2026-09-04T06:00:00-04:00"},
         "minutes_summary": None, "correction": None, "date": "2026-09-10"}
    assert build_site.meeting_lastmod(m, date(2026, 9, 8)) == "2026-09-05"
    # Once its day comes, the page speaks of the meeting as past.
    assert build_site.meeting_lastmod(m, date(2026, 9, 12)) == "2026-09-10"
    # A translation that comes later changes that language's page.
    assert build_site.meeting_lastmod({**m, "preview": {"generated_at": "2026-09-04", "translated_at": "2026-09-11T01:00:00"}},
                                      date(2026, 9, 12)) == "2026-09-11"
