"""The glossary (pipeline/glossary.py): a town's terms, its state's and the network's ([terms] given by
language), found in English and in Spanish, their page in each language, and the first use of each on a
page linked to it."""

import re
import tempfile
from pathlib import Path

import pytest
from markupsafe import Markup

from conftest import BUILT_AT
from pipeline import build_site, glossary, i18n
from pipeline.config import load_config

TERMS = {
    "special permit": {"en": {"definition": "Permission from a city board for a use the zoning allows only with it."},
                       "es": {"term": "permiso especial", "definition": "Permiso de una junta para un uso que la "
                              "zonificación permite solo con él."}},
    "Select Board": {"en": {"definition": "The board of three or five elected residents that runs a town.",
                            "also": ["Board of Selectmen", "Selectboard"]},
                     "es": {"term": "Junta de Gobierno (Select Board)", "definition": "La junta que gobierna el pueblo."}},
    "ADA": {"en": {"definition": "Americans with Disabilities Act."},
            "es": {"term": "Ley de Estadounidenses con Discapacidades (ADA)",
                   "definition": "La ley federal sobre el acceso de las personas con discapacidades."}},
    "levy": {"en": {"definition": "The property tax a town raises in a year.",
                    "pattern": r"\b(?:tax\s+)?lev(?:y|ies)\b(?!\s+(?:a|an|the)\b)"},
             "es": {"term": "recaudación de impuestos (levy)", "definition": "El impuesto a la propiedad que "
                    "recauda el pueblo en un año.", "also": ["recaudación"]}},
    # Given only in English: there for a [[glossary]] entry to take, not in every town's glossary.
    "M.G.L.": "Massachusetts General Laws.",
}


def config_with(own=(), terms=None):
    return {"glossary": list(own), "terms": TERMS if terms is None else terms}


def test_a_towns_glossary_is_its_own_entries_then_the_terms_given_by_language():
    own = [{"term": "ADA", "definition": "The town's own wording."}, {"term": "COA", "definition": "d", "bodies": ["HDC"]}]
    entries = glossary.entries(config_with(own))
    assert [e["term"] for e in entries] == ["ADA", "COA", "levy", "Select Board", "special permit"]
    # The town's own definition wins over a term of the same name.
    assert entries[0]["definition"] == "The town's own wording." and entries[0]["languages"] == {}
    assert entries[1]["bodies"] == ["HDC"]
    assert entries[3]["slug"] == "select-board"


@pytest.mark.parametrize("text, term", [
    ("Granted two special permits for decks.", "special permit"),
    ("The Board of Selectmen voted 3-0.", "Select Board"),
    ("The selectboard met.", "Select Board"),
    ("ADA compliance", "ADA"),
    ("Set the tax levy for FY27.", "levy"),
])
def test_terms_are_found_in_english_their_plural_and_other_ways_of_writing_them(text, term):
    entry = next(e for e in glossary.entries(config_with()) if e["term"] == term)
    assert glossary.found(entry, text)


@pytest.mark.parametrize("text, term", [
    ("The board's adapted plan.", "ADA"),  # an acronym only as written
    ("Voted to levy a fine.", "levy"),  # the term's own pattern leaves the verb out
    ("A special permitting process.", "special permit"),
])
def test_terms_are_not_found_inside_other_words_or_where_their_pattern_says_not(text, term):
    entry = next(e for e in glossary.entries(config_with()) if e["term"] == term)
    assert not glossary.found(entry, text)


@pytest.mark.parametrize("text, term", [
    ("Otorgó dos permisos especiales.", "special permit"),
    ("La junta de gobierno aprobó el contrato.", "Select Board"),
    ("La JUNTA DE GOBIERNO (Select Board) votó.", "Select Board"),
    ("Cumplimiento de la ADA.", "ADA"),
    ("La recaudacion del año fiscal 2027.", "levy"),
])
def test_terms_are_found_in_spanish_in_any_case_without_accents_in_the_plural(text, term):
    entry = next(e for e in glossary.entries(config_with()) if e["term"] == term)
    assert glossary.found(entry, text, "es")


def test_a_language_without_the_terms_wording_finds_nothing():
    entry = {"term": "COA", "slug": "coa", "definition": "d", "languages": {}}
    assert glossary.found(entry, "COA", "es") is None
    assert glossary.wording(entry, "es") is None and glossary.wording(entry, "en") == "COA"


def test_a_language_shows_each_terms_own_wording_else_its_english():
    own = [{"term": "COA", "definition": "Certificate of Appropriateness."}]
    shown = {e["term_en"]: e for e in glossary.shown(config_with(own), "es", lambda t: {"Certificate of Appropriateness.":
                                                                                         "Certificado."}.get(t, t))}
    assert shown["Select Board"]["term"] == "Junta de Gobierno (Select Board)" and shown["Select Board"]["translated"]
    assert shown["COA"]["term"] == "COA" and not shown["COA"]["translated"]
    assert shown["COA"]["definition"] == "Certificado." and not shown["COA"]["definition_english"]
    english = glossary.shown(config_with(own), "en")
    assert all(e["translated"] and not e["definition_english"] for e in english)
    # The page's order is its language's alphabet, accents aside.
    assert [e["term"] for e in glossary.sorted_for_page(list(shown.values()))][:2] == ["COA", "Junta de Gobierno (Select Board)"]


def test_each_terms_first_use_on_a_page_is_linked_and_only_that():
    link = glossary.Linker(glossary.shown(config_with(), "en"))
    first = link("The Select Board granted a special permit & the Board of Selectmen another special permit.")
    assert str(first) == ('The <a class="term" href="/meetings/glossary/#select-board">Select Board</a> granted a '
                          '<a class="term" href="/meetings/glossary/#special-permit">special permit</a> &amp; the Board '
                          'of Selectmen another special permit.')
    # Later on the page, a term already linked isn't linked again.
    assert 'class="term"' not in str(link("Another special permit."))
    assert 'href="/meetings/glossary/#ada"' in str(link("ADA work."))


def test_linking_keeps_html_and_never_links_inside_a_link():
    link = glossary.Linker(glossary.shown(config_with(), "en"))
    text = Markup('Approved <strong>$5,000</strong> for <a href="/x">the ADA ramp</a> and ADA signs.')
    assert str(link(text)) == ('Approved <strong>$5,000</strong> for <a href="/x">the ADA ramp</a> and '
                               '<a class="term" href="/meetings/glossary/#ada">ADA</a> signs.')


def test_a_spanish_page_links_the_spanish_wording_and_english_shown_in_english():
    link = glossary.Linker(glossary.shown(config_with(), "es"), "es")
    assert str(link("Aprobó dos permisos especiales.")) == (
        'Aprobó dos <a class="term" href="/meetings/glossary/#special-permit">permisos especiales</a>.')
    assert str(link("The Select Board met.", english=True)) == (
        'The <a class="term" href="/meetings/glossary/#select-board">Select Board</a> met.')


def test_a_page_of_many_meetings_links_only_the_terms_each_meeting_uses():
    link = glossary.Linker(glossary.shown(config_with(), "en"))
    meeting = {"glossary": [e for e in link.entries if e["term"] == "ADA"]}
    out = str(link.for_meeting(meeting)("ADA and special permit."))
    assert 'href="/meetings/glossary/#ada"' in out and "#special-permit" not in out
    # What the page linked is remembered across meetings.
    assert 'class="term"' not in str(link.for_meeting(meeting)("ADA again."))
    assert len(link.entries) == 5 - 1  # the [terms] given by language; M.G.L. is a definition alone


def glossary_config(town):
    config = load_config(town)
    config["site"]["languages"] = ["en", "es"]
    config["terms"] = TERMS
    return config


@pytest.fixture(scope="module")
def built(data_dir):
    """The fixture town in English and Spanish, with terms given by language."""
    out = Path(tempfile.mkdtemp(prefix="publick-glossary-")) / "site"
    real_load, real_strings = build_site.load_config, i18n.strings
    build_site.load_config = glossary_config
    i18n.strings = lambda lang: {}
    try:
        urls = build_site.build("gloucester", out, data_dir=data_dir, now=BUILT_AT, missing={})
    finally:
        build_site.load_config, i18n.strings = real_load, real_strings
    return out, urls


def page(site: Path, url: str) -> str:
    return (site / url.lstrip("/") / "index.html").read_text(encoding="utf-8")


def test_the_glossary_page_in_each_language(built):
    out, urls = built
    assert "/meetings/glossary/" in urls and "/es/meetings/glossary/" in urls
    english, spanish = page(out, "/meetings/glossary/"), page(out, "/es/meetings/glossary/")
    terms = re.findall(r'<dt id="([^"]+)">', english)
    # The town's own entries and the terms given by language, each once, alphabetically.
    assert len(terms) == len(set(terms)) and "select-board" in terms and "special-permit" in terms and "coa" in terms
    assert re.findall(r'<dt id="([^"]+)">', spanish) and set(re.findall(r'<dt id="([^"]+)">', spanish)) == set(terms)
    assert '<dt id="select-board">Junta de Gobierno (Select Board)</dt>' in spanish
    assert '<dt id="special-permit">permiso especial <span class="meta" lang="en">(special permit)</span></dt>' in spanish
    # A town entry with no Spanish wording is marked English.
    assert '<dt id="coa"><span lang="en">COA</span>' in spanish
    assert 'href="/meetings/glossary/"' in page(out, "/meetings/") and 'href="/es/meetings/glossary/"' in page(out, "/es/meetings/")


def test_meeting_pages_link_a_terms_first_use_to_the_glossary(built):
    out, _ = built
    html = page(out, "/meetings/2026-09-28-human-rights-commission/")
    assert html.count('<a class="term" href="/meetings/glossary/#ada">') == 1
    assert '<a href="/meetings/glossary/">All the terms in the glossary</a>' in html
    # On the Spanish page, the link goes to the Spanish glossary.
    assert 'href="/es/meetings/glossary/#ada"' in page(out, "/es/meetings/2026-09-28-human-rights-commission/")


def test_a_town_without_terms_has_no_glossary_page(data_dir):
    out = Path(tempfile.mkdtemp(prefix="publick-noglossary-")) / "site"
    real_load = build_site.load_config

    def without(town):
        config = load_config(town)
        config["glossary"], config["terms"] = [], {}
        return config
    build_site.load_config = without
    try:
        urls = build_site.build("gloucester", out, data_dir=data_dir, now=BUILT_AT, missing={})
    finally:
        build_site.load_config = real_load
    assert "/meetings/glossary/" not in urls and not (out / "meetings" / "glossary").exists()
    assert "/meetings/glossary/" not in page(out, "/meetings/")
