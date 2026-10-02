"""A town's site in English and Spanish ([site] languages): the Spanish pages under
/es/, their links, the town's own text from [strings.es], and English pages the same
as a site in English only."""

import json
import re
import tempfile
from pathlib import Path

import pytest

from conftest import BUILT_AT
from pipeline import build_site, i18n
from pipeline.config import load_config

TOWN_STRINGS = {"City Council": "Concejo Municipal", "Meetings": "Reuniones", "Pothole": "Bache"}
ENGINE_STRINGS = {(None, "This week"): "Esta semana", ("month", "October"): "octubre"}


def bilingual_config(town):
    config = load_config(town)
    config["site"]["languages"] = ["en", "es"]
    config["strings"] = {"es": TOWN_STRINGS}
    return config


@pytest.fixture(scope="module")
def built(data_dir):
    """The fixture town built in English and Spanish, with a few Spanish strings."""
    out = Path(tempfile.mkdtemp(prefix="publick-es-")) / "site"
    missing: dict = {}
    real_load, real_strings = build_site.load_config, i18n.strings
    build_site.load_config = bilingual_config
    i18n.strings = lambda lang: ENGINE_STRINGS if lang == "es" else {}
    try:
        urls = build_site.build("gloucester", out, data_dir=data_dir, now=BUILT_AT, missing=missing)
    finally:
        build_site.load_config, i18n.strings = real_load, real_strings
    return out, urls, missing


def page(site: Path, url: str) -> str:
    return (site / url.lstrip("/") / "index.html").read_text(encoding="utf-8")


def test_every_page_in_both_languages(built, site_dir):
    out, urls, _ = built
    english = sorted(p.relative_to(site_dir) for p in site_dir.rglob("*.html"))
    assert english
    for rel in english:
        assert (out / rel).exists(), rel
        assert (out / "es" / rel).exists(), f"es/{rel}"
    assert "/es/" in urls and "/es/meetings/" in urls
    assert "https://gloucester-ma.publick.org/es/meetings/" in (out / "sitemap.xml").read_text()


def test_english_pages_are_unchanged(built, site_dir):
    """A town's English pages gain only the links to their Spanish versions."""
    out, _, _ = built
    extra = re.compile(r'\s*<link rel="alternate" hreflang="[^"]+" href="[^"]+">'
                       r'|\s*<a class="language-switch"[^>]*>[^<]*</a>')
    for path in sorted(site_dir.rglob("*.html")):
        rel = path.relative_to(site_dir)
        assert extra.sub("", (out / rel).read_text(encoding="utf-8")) == path.read_text(encoding="utf-8"), rel


def test_language_attributes_and_switch(built):
    out, _, _ = built
    es, en = page(out, "/es/meetings/"), page(out, "/meetings/")
    assert '<html lang="es">' in es and '<html lang="en">' in en
    for html in (es, en):
        assert '<link rel="alternate" hreflang="en" href="https://gloucester-ma.publick.org/meetings/">' in html
        assert '<link rel="alternate" hreflang="es" href="https://gloucester-ma.publick.org/es/meetings/">' in html
        assert '<link rel="alternate" hreflang="x-default" href="https://gloucester-ma.publick.org/meetings/">' in html
    assert '<a class="language-switch" href="/es/meetings/?lang=es" hreflang="es" lang="es">Español</a>' in en
    assert '<a class="language-switch" href="/meetings/?lang=en" hreflang="en" lang="en">English</a>' in es
    assert '<link rel="canonical" href="https://gloucester-ma.publick.org/es/meetings/">' in es


def test_spanish_pages_link_spanish_pages(built):
    """Links to the site's pages stay in Spanish; downloads, PDFs, static files, and the feed are shared."""
    out, _, _ = built
    for path in sorted((out / "es").rglob("*.html")):
        html = path.read_text(encoding="utf-8")
        for tag in re.findall(r"<(?:a|form)\b[^>]*>", html):
            target = re.search(r'(?:href|action)="(/[^"]*)"', tag)
            if not target or target.group(1).startswith("//") or "hreflang=" in tag:
                continue
            url = target.group(1).split("#")[0].split("?")[0]
            if url.endswith("/"):
                assert url.startswith("/es/"), f"{path.relative_to(out)}: {tag}"
                assert (out / url.lstrip("/") / "index.html").exists(), f"{path.relative_to(out)}: {url}"
            else:
                assert not url.startswith("/es/") and (out / url.lstrip("/")).exists(), f"{path.relative_to(out)}: {url}"


def test_spanish_wording(built):
    out, _, _ = built
    es, en = page(out, "/es/"), page(out, "/")
    assert ">Esta semana</a>" in es and ">This week</a>" in en
    # The town's text: section titles, and boards with their English names after.
    assert ">Reuniones</a>" in es
    assert "Concejo Municipal (City Council)" in page(out, "/es/meetings/boards/city-council/")
    assert "Concejo Municipal" not in page(out, "/meetings/boards/city-council/")


def test_town_texts_without_spanish_are_listed(built, config):
    _, _, missing = built
    assert set(missing) == {"es"}
    # The config's own text, and names from the city's data (boards, 311 categories), apart.
    assert config["site"]["tagline"] in missing["es"]["config"]
    assert "Meetings" not in missing["es"]["config"] and "City Council" not in missing["es"]["data"]
    assert "Pothole" not in missing["es"]["data"] and "Board of Health" in missing["es"]["data"]


def test_section_names_are_the_engines_to_translate(config):
    """Every town's sections have the same names, so the engine translates them; a town's own
    [strings.es] still decides."""
    with i18n.use("es"):
        tr = build_site.TownStrings({"strings": {"es": {"Budget": "Las finanzas"}}}, "es")
        assert tr("Housing") == "Vivienda" and tr("Who represents you") == "Quién lo representa"
        assert tr("311") == "311" and tr("Budget") == "Las finanzas"
        assert not tr.missing


def test_a_site_isnt_built_in_spanish_without_the_towns_own_text(monkeypatch, capsys):
    """The config's own text is required in each of the site's languages; names from the city's
    data only warn, since a new board or category can appear any day."""
    def build(town, out, data, missing):
        missing["es"] = {"config": ["Public data on how Gloucester decides."], "data": ["Board of Health"]}
        return ["/"]
    monkeypatch.setattr(build_site, "build", build)
    monkeypatch.setattr("sys.argv", ["build_site", "--town", "gloucester"])
    with pytest.raises(SystemExit):
        build_site.main()
    out = capsys.readouterr().out
    assert "::warning::Español: 'Board of Health' is shown in English" in out
    assert "::error::Español" in out and "'Public data on how Gloucester decides.'" in out
    monkeypatch.setattr(build_site, "build", lambda town, out, data, missing: missing.update(
        {"es": {"config": [], "data": ["Board of Health"]}}) or ["/"])
    build_site.main()


def test_english_text_is_marked_on_spanish_pages(built):
    """AI summaries are in English until translated: marked so, for screen readers and readers."""
    out, _, _ = built
    meetings = [p for p in (out / "es" / "meetings").glob("*/index.html") if '<p lang="en">' in p.read_text()]
    assert meetings
    html = meetings[0].read_text(encoding="utf-8")
    assert "This summary hasn't been translated yet, so it's shown in English." in html
    english = (out / meetings[0].relative_to(out / "es")).read_text(encoding="utf-8")
    assert 'lang="en">' not in english.replace('hreflang="en"', "").replace('<html lang="en">', "")


def test_search_and_street_data_per_language(built):
    out, _, _ = built
    es_index = json.loads((out / "es" / "meetings" / "search-index.json").read_text())
    en_index = json.loads((out / "meetings" / "search-index.json").read_text())
    assert all(row["url"].startswith("/es/meetings/") for row in es_index)
    assert all(row["url"].startswith("/meetings/") for row in en_index)
    assert 'data-index="/es/meetings/search-index.json' in page(out, "/es/meetings/search/")
    assert 'data-index="/es/streets/streets.json' in page(out, "/es/streets/")


def test_street_index_links_the_language_pages():
    meeting = {"url": "/meetings/2026-10-05-city-council/", "date": "2026-10-05", "body": "City Council", "address": "",
               "preview": {"transcript": "1. A permit for 12 Main Street."}, "minutes_summary": None}
    town = {"name": "Gloucester", "state_abbr": "MA"}
    with i18n.use("es"):
        index = build_site.street_index([meeting], [], [], BUILT_AT.date(), town, "/es")
    mentions = [m for s in index["streets"].values() for m in s["meetings"]]
    assert [m["url"] for m in mentions] == ["/es/meetings/2026-10-05-city-council/"]


@pytest.mark.parametrize("script, url, root", [
    ("search.js", "/meetings/search/", "search"), ("streets.js", "/streets/", "street"),
    ("map.js", "/311/", "map-recent"), ("wards.js", "/officials/", "ward-map"),
])
def test_scripts_get_all_their_wording(built, script, url, root):
    """Every message a script shows comes from its page (data-strings), in the page's language."""
    out, _, _ = built
    js = (build_site.STATIC_DIR / "js" / script).read_text()
    # t("key"), t(found ? "key" : "other"), count("key") (key_one and key_other), strings.key
    keys = set(re.findall(r'\bt\("(\w+)"', js))
    keys |= {k for pair in re.findall(r'\bt\([^()"]*\? "(\w+)" : "(\w+)"', js) for k in pair}
    keys |= {k + suffix for k in re.findall(r'\bcount\("(\w+)"', js) for suffix in ("_one", "_other")}
    keys |= set(re.findall(r"\bstrings\.(\w+)", js))
    assert keys
    for prefix in ("", "/es"):
        html = page(out, prefix + url)
        tag = re.search(rf'<div[^>]*id="{root}"[^>]*>', html).group(0)
        strings = json.loads(re.search(r"data-strings='([^']*)'", tag).group(1))
        assert keys <= set(strings), sorted(keys - set(strings))


def test_languages_must_start_with_english():
    for langs in (["es"], ["en", "fr"], ["en", "es", "es"], []):
        with pytest.raises(SystemExit):
            build_site.languages({"slug": "x", "site": {"languages": langs}})
    assert build_site.languages({"slug": "x", "site": {}}) == ["en"]


def test_shared_words_and_numbered_seats_come_from_the_engine():
    """What many towns share (common boards, roles, numbered seats) is translated once, in the
    engine; a town's own [strings.es] still wins, and what neither has is noted."""
    with i18n.use("es"):
        tr = build_site.TownStrings({"strings": {"es": {"At-large": "Todo el municipio"}}}, "es")
        assert tr.board("Planning Board") == "Junta de Planificación (Planning Board)"
        assert tr("Ward 7") == "Distrito 7" and tr("District A") == "Distrito A" and tr("Precinct 12") == "Precinto 12"
        assert tr("Chair") == "Presidente" and tr("At-large") == "Todo el municipio"
        assert tr.data("Wards 1 and 2") == "Wards 1 and 2" and tr.missing_data == {"Wards 1 and 2"}
        assert not tr.missing


def test_drafts_are_shown_after_the_towns_own_text():
    with i18n.use("es"):
        tr = build_site.TownStrings({"strings": {"es": {"Harbor Plan": "Plan del Puerto"}}}, "es",
                                    drafts={"Harbor Plan": "Plan Portuario", "Fish Pier Committee": "Comité del Muelle"})
        assert tr("Harbor Plan") == "Plan del Puerto"
        assert tr.board("Fish Pier Committee") == "Comité del Muelle (Fish Pier Committee)"
        assert tr.drafted == {"Fish Pier Committee"} and not tr.missing_data


def test_needed_texts_are_what_the_build_would_miss(data_dir):
    """The run drafts what needed_texts() finds before the build; it must find what the build would."""
    out = Path(tempfile.mkdtemp(prefix="publick-es-")) / "site"
    config = load_config("gloucester")
    config["site"]["languages"] = ["en", "es"]
    missing: dict = {}
    real = build_site.load_config
    build_site.load_config = lambda town: config
    try:
        build_site.build("gloucester", out, data_dir=data_dir, now=BUILT_AT, missing=missing)
    finally:
        build_site.load_config = real
    needed = build_site.needed_texts(load_config("gloucester") | {"site": config["site"]}, data_dir, "es", BUILT_AT)
    assert needed["config"] and needed["data"]
    assert needed == {"config": missing["es"]["config"], "data": missing["es"]["data"]}
