"""Summaries translated for a town's pages in another language (pipeline/translate.py),
with a stand-in model client (no API calls)."""

import json

import pytest

from conftest import BUILT_AT, FETCHED_AT
from fakes import FakeAnthropic, FakeCityClient
from pipeline import build_site, fetch_meetings, fetch_minutes, i18n, summarize, translate
from pipeline.config import load_config

# What one FakeAnthropic translation costs at Claude Haiku 4.5's prices: 600 tokens in at $1, 300 out at $5 a million.
TRANSLATION = 0.0021


def spanish_town(tmp_path, languages=("en", "es")):
    """The fixture calendar and minutes, for a town in English and Spanish: an upcoming
    agenda and 11 recent minutes are new, 4 older minutes are backlog."""
    config = load_config("gloucester")
    config["site"]["languages"] = list(languages)
    config["strings"] = {"es": {"City Council": "Concejo Municipal"}}
    fetch_meetings.run(config, FakeCityClient(), tmp_path, now=FETCHED_AT)
    fetch_minutes.run(config, FakeCityClient(), tmp_path, now=FETCHED_AT)
    return config


def translations(tmp_path):
    folder = tmp_path / "summaries" / "es"
    return {f.stem: json.loads(f.read_text()) for f in folder.glob("*.json")} if folder.is_dir() else {}


def translation_calls(client):
    return [c for c in client.calls if "translate" in c["system"]]


def test_numbers_are_checked_without_ai():
    source = {"headline": "Approved $1,500 for 12 Main St, 5-0.", "summary": "On October 22, 2026 at 7:00 PM.",
              "decisions": ["Approved 3 permits", "Denied 1"]}
    good = {"headline": "Se aprobaron $1,500 para 12 Main St, 5-0.", "summary": "El 22 de octubre de 2026 a las 7:00 p. m.",
            "decisions": ["Se aprobaron 3 permisos", "Se negó 1"]}
    assert translate.check(source, good, "minutes") == "ok"
    assert translate.check(source, {**good, "headline": "Se aprobaron $1.500 para 12 Main St, 5-0."}, "minutes") \
        == "headline: 1,500 not in the translation"
    assert translate.check(source, {**good, "decisions": ["Se aprobaron 3 permisos"]}, "minutes") == "decisions: 1 entries for 2"
    assert translate.check(source, {**good, "decisions": ["Se aprobaron tres permisos", "Se negó 1"]}, "minutes") \
        == "decisions 1: 3 not in the translation"
    assert translate.check(source, {**good, "summary": ""}, "minutes") == "summary: missing"


def test_dates_in_figures_may_be_written_out():
    """Agendas write "Tabled on 9/23/2026"; the Spanish writes "23 de septiembre de 2026"."""
    source = {"headline": "Two licenses.", "summary": "Hearing.", "items": ["Tabled on 9/23/2026 - License #LL26-000039"]}
    def items(text):
        return translate.check(source, {"headline": "Dos licencias.", "summary": "Audiencia.", "items": [text]}, "agenda")
    assert items("Pospuesto el 23 de septiembre de 2026 - Licencia #LL26-000039") == "ok"
    assert items("Pospuesto el 23/9/2026 - Licencia #LL26-000039") == "ok"
    assert items("Pospuesto el 9/23/26 - Licencia #LL26-000039") == "ok"
    # The wrong day, month, or year, or a lost number elsewhere, still fails.
    assert items("Pospuesto el 24 de septiembre de 2026 - Licencia #LL26-000039") == "items 1: 23, 9 not in the translation"
    assert items("Pospuesto el 23 de agosto de 2026 - Licencia #LL26-000039").startswith("items 1:")
    assert items("Pospuesto el 23 de septiembre de 2025 - Licencia #LL26-000039").startswith("items 1:")
    assert items("Pospuesto el 23 de septiembre de 2026 - Licencia #LL26-000038") == "items 1: 000039 not in the translation"


def test_every_summary_is_translated_and_checked(tmp_path):
    config = spanish_town(tmp_path)
    client = FakeAnthropic()
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    assert result["summarized"] == 16 and result["translated"] == 16 and not result["errors"]
    saved = translations(tmp_path)
    assert len(saved) == 16
    record = next(r for r in saved.values() if r["kind"] == "minutes")
    assert record["headline"].startswith("ES ") and record["decisions"] == ["ES Approved the site plan for 12 Main St, 5-0"]
    assert record["check"] == "ok" and record["model"] == "claude-haiku-4-5-20251001"
    assert record["cost"] == pytest.approx(TRANSLATION)
    call = translation_calls(client)[0]
    assert call["model"] == "claude-haiku-4-5-20251001" and "Spanish" in call["system"]
    assert call["output_config"]["format"]["type"] == "json_schema"


def test_a_translation_is_made_once_for_its_english(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    again = FakeAnthropic()
    assert summarize.run(config, again, tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0
    assert translation_calls(again) == []
    # A changed English summary is translated again; the others aren't.
    sha, record = next((s, r) for s, r in translations(tmp_path).items() if r["kind"] == "minutes")
    english = tmp_path / "summaries" / f"{sha}.json"
    english.write_text(json.dumps({**json.loads(english.read_text()), "headline": "Approved a site plan for 14 Main St."}))
    third = FakeAnthropic()
    assert summarize.run(config, third, tmp_path, limit=50, now=FETCHED_AT)["translated"] == 1
    assert "14 Main St" in translations(tmp_path)[sha]["headline"]


def test_a_town_in_english_only_gets_no_translations(tmp_path):
    config = spanish_town(tmp_path, languages=("en",))
    client = FakeAnthropic()
    assert summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0
    assert translation_calls(client) == [] and translations(tmp_path) == {}


def test_new_documents_translations_come_before_the_backlog(tmp_path):
    config = spanish_town(tmp_path)
    result = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT, backlog_allowance=0)
    # The 12 new documents are summarized and translated; the 4 older ones wait for both.
    assert result["summarized"] == 12 and result["translated"] == 12 and result["remaining"] == 4
    assert "older documents wait" in result["stopped"]


def test_translations_count_in_the_budget(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)
    row = json.loads((tmp_path / summarize.LEDGER).read_text())["2026-09"]
    assert row["documents"] == 5 and row["translations"] == 5
    assert row["translation_cost"] == round(5 * TRANSLATION, 4)
    assert summarize.month_cost({"2026-09": row}, "2026-09") == pytest.approx(row["cost"] + row["translation_cost"])


def test_a_translation_that_fails_its_check_isnt_shown(tmp_path):
    config = spanish_town(tmp_path)
    result = summarize.run(config, FakeAnthropic(translation_drops_numbers=True), tmp_path, limit=50, now=FETCHED_AT)
    saved = translations(tmp_path)
    assert result["translated"] == 16 and saved
    failed = [r for r in saved.values() if r["check"] != "ok"]
    # Kept, so it isn't paid for again, but never shown.
    assert failed and all("not in the translation" in r["check"] for r in failed)
    sha, record = next((s, r) for s, r in saved.items() if r["check"] != "ok")
    english = json.loads((tmp_path / "summaries" / f"{sha}.json").read_text())
    assert translate.shown(tmp_path, "es", sha, english, record["kind"]) is None
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0


def test_the_check_runs_again_when_shown(tmp_path):
    """A translation saved as failed by an older, stricter check is shown once it passes,
    without paying for it again; one saved as passing that now fails isn't."""
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    english = {sha: json.loads((tmp_path / "summaries" / f"{sha}.json").read_text()) for sha in translations(tmp_path)}
    # One whose English headline has a number, so a translation without it fails.
    sha, record = next((s, r) for s, r in translations(tmp_path).items() if translate.NUMBER.search(english[s]["headline"]))
    file = translate.path(tmp_path, "es", sha)
    file.write_text(json.dumps({**record, "check": "headline: 9 not in the translation"}))
    assert translate.shown(tmp_path, "es", sha, english[sha], record["kind"])
    file.write_text(json.dumps({**record, "headline": "Sin números."}))
    assert translate.shown(tmp_path, "es", sha, english[sha], record["kind"]) is None


def test_spanish_pages_show_the_translation(tmp_path, monkeypatch):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    monkeypatch.setattr(build_site, "load_config", lambda town: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=tmp_path, now=BUILT_AT)
    minutes = [p for p in (out / "es" / "meetings").glob("20*/index.html") if "ES The board approved" in p.read_text()]
    assert minutes
    page = minutes[0].read_text()
    assert "<p>ES The board approved a site plan for 12 Main St.</p>" in page
    assert "hasn't been translated" not in page
    english = (out / minutes[0].relative_to(out / "es")).read_text()
    assert "<p>The board approved a site plan for 12 Main St.</p>" in english and "ES " not in english
    index = json.loads((out / "es" / "meetings" / "search-index.json").read_text())
    assert any(d["text"].startswith("ES ") for row in index for d in row["docs"])


def test_untranslated_summaries_are_shown_in_english(tmp_path, monkeypatch):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(translation_drops_numbers=True), tmp_path, limit=50, now=FETCHED_AT)
    monkeypatch.setattr(build_site, "load_config", lambda town: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=tmp_path, now=BUILT_AT)
    page = next(p for p in (out / "es" / "meetings").glob("20*/index.html")
                if "a site plan for 12 Main St." in p.read_text()).read_text()
    assert '<p lang="en">The board approved a site plan for 12 Main St.</p>' in page
    with i18n.use("es"):
        assert i18n.gettext("This summary hasn't been translated yet, so it's shown in English.") in page
