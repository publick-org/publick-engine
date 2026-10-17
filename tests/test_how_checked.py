"""How each summary was checked against its document (factcheck.how_checked), and where that's said: in its
credit on its meeting's page, in the digest, the feed, and the open data export; a list of meetings warns of a
summary not checked."""

import json
import re
import xml.etree.ElementTree as ET

import pytest
from jinja2 import Environment, FileSystemLoader

from pipeline import build_site, factcheck, i18n


def fc(source, result="ok"):
    return {"version": factcheck.VERSION, "source": source, "result": result, "problems": []}


AGENDA = {"kind": "agenda", "prompt_version": 5, "headline": "H", "summary": "S", "items": ["Item"]}
MINUTES = {"kind": "minutes", "prompt_version": 4, "headline": "H", "summary": "S", "decisions": ["Approved X."],
           "decision_evidence": [{"outcome": "approved", "quote": "Approved X"}]}
# Minutes summarized before the prompt asked for each decision's quote and outcome.
MINUTES_V2 = {"kind": "minutes", "prompt_version": 2, "headline": "H", "summary": "S", "decisions": ["Approved X."]}

STATES = [
    # The city's own PDF text, in full: checked, whatever the check found (what failed isn't shown).
    ({**AGENDA, "fact_check": fc("pdf")}, "agenda", "pdf"),
    ({**AGENDA, "fact_check": fc("pdf", "failed")}, "agenda", "pdf"),
    ({**MINUTES, "fact_check": fc("pdf")}, "minutes", "pdf"),
    # Scanned pages in a typed document: only the pages with text.
    ({**MINUTES, "fact_check": fc("partial", "weak")}, "minutes", "partial"),
    # A scan, checked against the model's transcription: "weak" hides nothing, and it says so.
    ({**AGENDA, "fact_check": fc("ai")}, "agenda", "ai"),
    ({**MINUTES, "fact_check": fc("ai", "weak")}, "minutes", "ai"),
    # Nothing to check against, no check saved, or minutes with no quote or outcome to anchor.
    ({**AGENDA, "fact_check": fc("none", "unchecked")}, "agenda", "none"),
    (AGENDA, "agenda", "none"),
    ({**MINUTES_V2, "fact_check": fc("pdf")}, "minutes", "none"),
    ({**MINUTES_V2, "fact_check": fc("ai")}, "minutes", "none"),
    ({**MINUTES_V2, "decisions": [], "fact_check": fc("pdf")}, "minutes", "none"),
]


@pytest.mark.parametrize("record, kind, how", STATES)
def test_how_checked(record, kind, how):
    assert factcheck.how_checked(record, kind) == how
    # Its kind, when not given, is the record's.
    assert factcheck.how_checked(record) == how


def test_how_checked_minutes_from_version_3():
    """Minutes from the prompt version with quotes on are checked as their source says, even with no
    decision to quote (split_decisions() then saves no decision_evidence)."""
    record = {"kind": "minutes", "prompt_version": factcheck.ANCHORED_SINCE, "headline": "H", "summary": "S",
              "decisions": [], "fact_check": fc("pdf")}
    assert factcheck.how_checked(record, "minutes") == "pdf"
    assert factcheck.how_checked(None, "minutes") is None
    assert factcheck.how_checked({}, "agenda") is None
    # A document filed as minutes that isn't any: no summary is shown, so nothing is said of its check.
    assert factcheck.how_checked({**MINUTES, "is_minutes": False}, "minutes") is None


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(str(build_site.SITE_DIR / "templates")), autoescape=True,
                    trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.i18n"])
    e.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True, pgettext=i18n.pgettext, npgettext=i18n.npgettext)
    e.policies["ext.i18n.trimmed"] = True
    e.filters.update(date=build_site.format_date, time=build_site.format_time)
    e.globals["how_checked"] = factcheck.how_checked
    return e


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


LINES = {
    "pdf": "Checked against the city's PDF.",
    "partial": "Partly checked against the city's PDF.",
    "ai": "Checked against an AI transcription.",
    "none": "Not checked.",
}


@pytest.mark.parametrize("record, kind, how", STATES)
def test_credit_on_the_meeting_page(record, kind, how):
    """The summary's credit says how it was checked, in words, and links how summaries are checked; a summary not
    checked says so first."""
    html = env().from_string('{% from "macros.html" import ai_credit %}{{ ai_credit(kind, "https://city.example/a.pdf", '
                             '"/report", record=r) }}').render(r=record, kind=kind)
    credit = re.search(r'<div class="credit" data-checked="(\w+)">\s*<p>(.*?)</p>', html, re.S)
    assert credit and credit.group(1) == how
    said = text(credit.group(2))
    assert LINES[how] in said and said.count("Summary written by AI") == 1
    assert said.startswith("Not checked.") == (how == "none")
    assert '<a href="/about/#checks">How summaries are checked</a>' in credit.group(2)
    assert 'href="/report"' in html
    if how == "ai":
        assert "share a mistake" in said
    if how == "none" and record.get("prompt_version") == 2 and record.get("fact_check"):
        assert "older than the check that matches each decision to the minutes' own words" in said
    elif how == "none":
        assert "hasn't been compared with the city's document" in said


def test_no_check_said_without_a_summary():
    html = env().from_string('{% from "macros.html" import ai_credit %}{{ ai_credit("agenda", "/a.pdf", "/report") }}').render()
    assert "data-checked" not in html and "Checked" not in html and "Not checked" not in html


@pytest.mark.parametrize("how, label", [
    ("pdf", "AI summary"),
    ("partial", "AI summary"),
    ("ai", "AI summary"),
    ("none", "AI summary, not checked"),
    (None, "AI summary"),
])
def test_label_in_lists(how, label):
    """A meeting's line in a list (the home page, a board's page) says its summary is AI's, and warns when it wasn't
    checked; the meeting's page says how it was."""
    e = env()
    e.globals["today"] = "2026-10-14"
    m = {"date": "2026-10-07", "start_time": None, "url": "/meetings/x/", "body": "Planning Board", "title": "Planning Board",
         "preview_line": "Approved a permit.", "preview_checked": how, "minutes_doc": None, "agenda": {"file": "a.pdf"},
         "status": "scheduled", "remote_url": None, "special": False, "listed": True}
    html = e.from_string('{% from "macros.html" import meeting_item %}{{ meeting_item(m) }}').render(m=m)
    meta = text(re.search(r'<span class="meeting-meta">(.*?)</span>', html, re.S).group(1))
    assert meta.split(" · ")[0] == label


@pytest.mark.parametrize("how", ["pdf", "partial", "ai", "none"])
def test_in_spanish(how):
    """Each state has its Spanish, on the meeting page and in the digest's lists."""
    record = next(r for r, kind, h in STATES if h == how and kind == "minutes")
    e = env()
    with i18n.use("es"):
        credit = e.from_string('{% from "macros.html" import ai_credit %}{{ ai_credit("minutes", "/a.pdf", "/report", record=r) }}').render(r=record)
        label = e.from_string('{% from "macros.html" import ai_summary_label %}{{ ai_summary_label(how) }}').render(how=how)
        short = build_site.checked_short(how)
    assert {"pdf": "Verificado con el PDF de la ciudad.", "partial": "Verificado en parte con el PDF de la ciudad.",
            "ai": "Verificado con una transcripción hecha con IA.", "none": "Sin verificar."}[how] in text(credit)
    assert "Cómo se verifican los resúmenes" in credit
    words = {"pdf": "verificado con el PDF de la ciudad", "partial": "verificado en parte con el PDF de la ciudad",
             "ai": "verificado con una transcripción hecha con IA", "none": "sin verificar"}[how]
    assert label == "Resumen hecho con IA, " + words
    assert short == words[0].upper() + words[1:]


def test_preview_source():
    """The summary a meeting's list line comes from, whose check is said beside it."""
    minutes, agenda = {"headline": "M"}, {"headline": "A"}
    assert build_site.preview_source({"minutes_summary": minutes, "preview": agenda}) == (minutes, "minutes")
    assert build_site.preview_source({"minutes_summary": {"is_minutes": False, "headline": "X"}, "preview": agenda}) == (agenda, "agenda")
    assert build_site.preview_source({"minutes_summary": {"headline": "", "decisions": []}, "preview": agenda}) == (agenda, "agenda")
    assert build_site.preview_source({"preview": {"items": ["X"]}}) == ({"items": ["X"]}, "agenda")
    assert build_site.preview_source({}) == (None, None)


def test_built_site(site_dir):
    """In the fixture town's build: each summary's credit says how it was checked, as the export does, and
    the feed and the digest say so too."""
    export = json.loads((site_dir / "data" / "meetings.json").read_text())
    states = {}
    for row in export["meetings"]:
        page = None
        for kind in ("agenda", "minutes"):
            summary = (row[kind] or {}).get("summary")
            if not summary:
                continue
            assert summary["checked"] in factcheck.HOW_CHECKED
            states[summary["checked"]] = states.get(summary["checked"], 0) + 1
            page = page or (site_dir / row["url"].split("/", 3)[3] / "index.html").read_text()
            assert f'data-checked="{summary["checked"]}"' in page
        if page:
            # Each summary's credit says how it was checked.
            assert page.count('<div class="credit" data-checked="') >= 1
    assert states
    feed = ET.parse(site_dir / "feed.xml").getroot()
    said = [d.text for d in feed.iter("description") if d.text and "(AI summary of the" in d.text]
    assert said and all(re.search(r"Check the original\.\) (Checked against the city's PDF|Partly checked against the city's PDF|"
                                  r"Checked against an AI transcription|Not checked)\.$", d) for d in said)
    issues = sorted((site_dir / "digest").glob("20*/index.html"))
    with_minutes = [p.read_text() for p in issues if 'id="minutes"' in p.read_text()]
    for page in with_minutes:
        assert page.count('class="small checked"') == len(re.findall(r'<li class="decision-meeting">\s*<p><a[^>]*>[^<]*</a>, <time[^>]*>[^<]*</time></p>\s*<ul>', page))
