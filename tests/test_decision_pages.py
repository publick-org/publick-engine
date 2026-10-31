"""Each decision's page in its minutes: found by the fact check where it found the decision's quote
(factcheck.check()), kept beside the decisions shown (factcheck.shown()), and cited after each decision
("p. 3", linking the PDF at that page) on its meeting's page, the decisions page, and the digest."""

import json
import re
import shutil
from datetime import datetime
from urllib.parse import unquote

import pytest
from conftest import TZ

from pipeline import build_site, factcheck
from pipeline.config import load_config

FILLER = "The board heard public comment on several items not on the agenda this evening.\n" * 2
PAGES = [
    "CITY OF EXAMPLE PLANNING BOARD\nMinutes of June 4, 2026\nPresent: Chair Houseman, Members Squibb, Bertoni.\n" + FILLER,
    "1. Minutes of May 21: motion to approve the minutes of May 21, 2026. Motion carried 3-0.\n" + FILLER,
    "2. 12 Elm Street: motion by Squibb to approve the site plan for 12 Elm Street with the conditions in the\n"
    "staff report. Motion carried 3-0.\n" + FILLER + "3. Pond Road: motion to continue the hearing on the\n",
    "subdivision at 40 Pond Road to July 9, 2026. Motion carried 3-0.\n" + FILLER,
]
ELM = "motion by Squibb to approve the site plan for 12 Elm Street with the conditions in the staff report"
POND = "motion to continue the hearing on the subdivision at 40 Pond Road to July 9, 2026"


def record(*entries) -> dict:
    return {"kind": "minutes", "headline": "", "summary": "", "decisions": [d for d, _, _ in entries],
            "decision_evidence": [{"outcome": o, "quote": q} for _, o, q in entries]}


ELM_DECISION = ("Approved the site plan for 12 Elm Street, 3-0.", "approved", ELM)
POND_DECISION = ("Continued the 40 Pond Road hearing to July 9, 2026.", "continued", POND)


def test_a_located_quote_gets_its_page():
    fc = factcheck.check(record(ELM_DECISION), "minutes", PAGES)
    assert fc["source"] == "pdf" and fc["result"] == "ok"
    assert fc["pages"] == [3]


def test_a_quote_over_two_pages_gets_the_first():
    fc = factcheck.check(record(POND_DECISION), "minutes", PAGES)
    assert fc["result"] == "ok", fc["problems"]
    assert fc["pages"] == [3]


def test_a_quote_that_isnt_found_gets_none():
    missing = ("Approved the budget, 3-0.", "approved", "motion to approve the budget for fiscal year 2027")
    fc = factcheck.check(record(missing, ELM_DECISION), "minutes", PAGES)
    assert [p["kind"] for p in fc["problems"]] == ["quote"]
    assert fc["pages"] == [None, 3]
    # No quote at all.
    fc = factcheck.check(record(("Approved the minutes.", "approved", "")), "minutes", PAGES)
    assert fc["pages"] == [None]


def test_the_same_words_on_two_pages_get_none():
    both = ("Approved the minutes of May 21.", "approved", "motion to approve the minutes of May 21")
    pages = PAGES[:3] + [PAGES[3] + PAGES[1]]
    assert factcheck.check(record(both), "minutes", PAGES)["pages"] == [2]
    assert factcheck.check(record(both), "minutes", pages)["pages"] == [None]


def test_scanned_pages_count_where_the_rest_has_text():
    pages = [PAGES[0], "", *PAGES[1:]]
    fc = factcheck.check(record(ELM_DECISION), "minutes", pages)
    assert fc["source"] == "partial" and fc["pages"] == [4]


def test_a_scan_gets_a_page_only_where_its_transcription_says():
    """A scan is checked against the model's transcription, a few pages a request: a quote's page is known
    only where the request it's in transcribed one page alone."""
    transcript = "\n\n".join(["Minutes of June 4, 2026", PAGES[1] + PAGES[2], PAGES[3]])
    base = {**record(ELM_DECISION), "transcript": transcript, "transcript_source": "ai"}
    scan = [""] * 4
    # Saved before requests' pages were kept.
    fc = factcheck.check(base, "minutes", scan)
    assert fc["source"] == "ai" and fc["pages"] == [None]
    # Pages 2 and 3 in one request.
    second = len("Minutes of June 4, 2026") + 2
    third = second + len(PAGES[1] + PAGES[2]) + 2
    spans = [[0, 1, 1], [second, 2, 3], [third, 4, 4]]
    assert factcheck.check({**base, "transcript_pages": spans}, "minutes", scan)["pages"] == [None]
    # Each page alone.
    one = [[0, 1, 1], [second, 2, 2], [second + len(PAGES[1]), 3, 3], [third, 4, 4]]
    assert factcheck.check({**base, "transcript_pages": one}, "minutes", scan)["pages"] == [3]
    # A scan with nothing to check against: no check, no pages.
    assert "pages" not in factcheck.check(record(ELM_DECISION), "minutes", scan)


def test_older_summaries_and_agendas_have_no_pages():
    old = {"kind": "minutes", "headline": "", "summary": "", "decisions": ["Approved the site plan."]}
    assert "pages" not in factcheck.check(old, "minutes", PAGES)
    agenda = {"kind": "agenda", "headline": "", "summary": "", "items": ["Site plan, 12 Elm Street"]}
    assert "pages" not in factcheck.check(agenda, "agenda", PAGES)


def test_whats_shown_keeps_each_decisions_page():
    wrong = ("Approved the site plan for 14 Elm Street, 3-0.", "approved", ELM)
    rec = record(wrong, POND_DECISION)
    rec["fact_check"] = factcheck.check(rec, "minutes", PAGES)
    assert rec["fact_check"]["result"] == "failed"
    out = factcheck.shown(rec, rec, "minutes")
    # The first isn't shown (14 isn't in the minutes), nor its page.
    assert out["decisions"] == [POND_DECISION[0]]
    assert out["decision_pages"] == [[3, POND]]
    # A translation, entry by entry, gets the same.
    es = {**rec, "decisions": ["Aprobó el plan del 14 de Elm Street.", "Aplazó la audiencia al 9 de julio de 2026."],
          "headline": "otro"}
    assert factcheck.shown(es, rec, "minutes")["decision_pages"] == [[3, POND]]
    # A check from before pages: nothing to cite.
    rec["fact_check"].pop("pages")
    assert "decision_pages" not in factcheck.shown(rec, rec, "minutes")


def document_url(folder, name):
    return f"https://files.publick.org/example/{folder}/{name}"


CITY = {"source_url": "https://www.example-ma.gov/AgendaCenter/ViewFile/Minutes/_06042026-101", "file": "agendacenter-101.pdf"}


def test_the_link_goes_to_the_citys_pdf_at_the_page_with_the_quote():
    url = build_site.page_link(CITY, build_site.Decision("Approved.", 3, ELM + "... Motion carried"), document_url)
    pdf, fragment = url.split("#")
    assert pdf == CITY["source_url"]
    assert fragment.startswith("page=3&search=")
    # The quote's first words, to the first "...", as one phrase.
    assert unquote(fragment) == "page=3&search=motion by Squibb to approve the site plan&phrase=true"
    assert build_site.page_link(CITY, build_site.Decision("Approved.", 3), document_url) == CITY["source_url"] + "#page=3"


@pytest.mark.parametrize("doc", [
    # A viewer's page, which doesn't open the PDF at a page.
    {"source_url": "https://drive.google.com/file/d/1abc/view?usp=sharing", "file": "1abc-7b79cfb7.pdf"},
    {"source_url": "https://docs.google.com/document/d/1abc/edit?usp=sharing", "file": "1abc-48ae5841.pdf"},
    # A Word document, saved as a PDF.
    {**CITY, "converted_from": "docx"},
])
def test_the_link_goes_to_the_saved_copy_where_the_citys_isnt_the_pdf(doc):
    d = build_site.Decision("Approved.", 2)
    assert build_site.page_link(doc, d, document_url) == f"https://files.publick.org/example/minutes/{doc['file']}#page=2"
    # A copy saved with the site, read from the digest's email: in full.
    assert build_site.page_link(doc, d, lambda folder, name: f"/{folder}/{name}", "https://example.publick.org") == (
        f"https://example.publick.org/minutes/{doc['file']}#page=2")


# ---- On the site, in both languages --------------------------------------------------------

@pytest.fixture(scope="module")
def cited_site(tmp_path_factory, data_dir):
    """The fixture site in English and Spanish, with each minutes summary's decision found on page 3 (the
    fixture's are scans, transcribed a few pages a request, so none of theirs is known), and one set of
    minutes collected on Wednesday, September 30, for the digest built on Sunday, October 4."""
    base = tmp_path_factory.mktemp("cited")
    data = base / "data"
    shutil.copytree(data_dir, data)
    path = data / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8"))
    store["archive-20079"]["minutes"][-1]["fetched_at"] = "2026-09-30T06:00:00-04:00"
    path.write_text(json.dumps(store), encoding="utf-8")
    cited = []
    for path in sorted((data / "summaries").glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("decision_evidence"):
            rec["decision_evidence"] = [{**e, "quote": "quote of the minutes"} for e in rec["decision_evidence"]]
            rec["fact_check"]["pages"] = [3] * len(rec["decisions"])
            path.write_text(json.dumps(rec), encoding="utf-8")
            cited.append(rec)
    assert cited
    config = load_config("gloucester")
    config["site"]["languages"] = ["en", "es"]   # as every town is
    site = base / "site"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: config)
        build_site.build("gloucester", site, data_dir=data, now=datetime(2026, 10, 4, 7, 0, tzinfo=TZ))
    return site


def cites(page: str) -> list[str]:
    """Each decision's page on a built page: its link, whole."""
    return re.findall(r'<a class="(?:external )?cite".*?</a>', page)


def said(link: str) -> str:
    """What a screen reader says for a link: its text, but what's hidden from it."""
    return re.sub(r"<[^>]+>", "", re.sub(r'<span aria-hidden="true">.*?</span>', "", link)).strip()


def pages_with_decisions(site, lang=""):
    out = []
    for path in sorted((site / lang / "meetings").glob("*/index.html")):
        html = path.read_text(encoding="utf-8")
        if '<h2 id="minutes">' in html and 'class="decision-rows"' in html:
            out.append(html)
    return out


def test_no_page_is_cited_where_none_is_known(site_dir):
    assert pages_with_decisions(site_dir)
    assert not any(cites(p.read_text(encoding="utf-8")) for p in site_dir.rglob("*.html"))
    assert "#page=" not in (site_dir / "digest" / "feed.xml").read_text(encoding="utf-8")


def test_the_meeting_page_cites_the_page_in_both_languages(cited_site):
    en, es = pages_with_decisions(cited_site), pages_with_decisions(cited_site, "es")
    assert en and len(es) == len(en)
    for page in en:
        found = cites(page)
        assert len(found) == 1, found
        href = re.search(r'href="([^"]+)"', found[0]).group(1)
        assert href.endswith("#page=3&amp;search=quote%20of%20the%20minutes&amp;phrase=true")
        # The PDF the page links as the minutes, opened in a new tab as every PDF is.
        assert 'href="' + href.split("#")[0] + '"' in page
        assert 'target="_blank"' in found[0]
        assert '<span aria-hidden="true">p. 3</span>' in found[0]
        assert said(found[0]) == "page 3 of the minutes (opens in new tab)"
        assert "lang=" not in found[0]
    for page in es:
        found = cites(page)
        assert len(found) == 1
        assert "#page=3&amp;search=quote%20of%20the%20minutes" in found[0]
        assert '<span aria-hidden="true">pág. 3</span>' in found[0]
        assert said(found[0]).startswith("página 3 de las actas")
        # Said in Spanish, inside a list that may be shown in English.
        assert 'lang="es"' in found[0]


def test_the_decisions_page_and_the_digest_cite_it(cited_site):
    for lang, short in (("", "p. 3"), ("es", "pág. 3")):
        page = (cited_site / lang / "meetings" / "decisions" / "index.html").read_text(encoding="utf-8")
        found = cites(page)
        assert found and all(f'<span aria-hidden="true">{short}</span>' in c for c in found)
    # The digest, in English.
    issue = (cited_site / "digest" / "2026-10-05" / "index.html").read_text(encoding="utf-8")
    found = cites(issue[issue.index('id="minutes"'):])
    assert len(found) == 1 and said(found[0]) == "page 3 of the minutes (opens in new tab)"
    # The email, in the digest's feed: the link in full, its label what screen readers say.
    feed = (cited_site / "digest" / "feed.xml").read_text(encoding="utf-8")
    assert "#page=3&amp;amp;search=quote%20of%20the%20minutes&amp;amp;phrase=true" in feed
    assert 'aria-label="page 3 of the minutes"&gt;p. 3&lt;/a&gt;' in feed


CITED_PATHS = ["/meetings/2026-08-25-city-council/", "/es/meetings/2026-08-25-city-council/", "/meetings/decisions/",
               "/es/meetings/decisions/", "/digest/2026-10-05/"]


@pytest.mark.parametrize("path", CITED_PATHS)
def test_cited_pages_pass_the_browser_checks(cited_site, path):
    """WCAG 2.2 AA with axe-core, as tests/test_accessibility.py checks every page, on pages that cite a
    decision's page, at desktop and phone widths: the phone's without sideways scrolling."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    axe_module = pytest.importorskip("axe_playwright_python.sync_playwright")
    from conftest import serve
    from test_accessibility import VIEWPORTS, WCAG_TAGS, format_violations
    assert cites((cited_site / path.strip("/") / "index.html").read_text(encoding="utf-8"))
    with serve(cited_site) as url, playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        for name, viewport in VIEWPORTS.items():
            context = browser.new_context(viewport=viewport, color_scheme="light")
            page = context.new_page()
            page.goto(url + path)
            results = axe_module.Axe().run(page, options={"runOnly": {"type": "tag", "values": WCAG_TAGS}})
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            context.close()
            assert results.violations_count == 0, f"{path} ({name}):\n{format_violations(results)}"
            assert overflow <= 0, f"{path} scrolls sideways by {overflow}px ({name})"
        browser.close()
