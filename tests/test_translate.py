"""Summaries translated for a town's pages in another language (pipeline/translate.py),
with a stand-in model client (no API calls)."""

import json

import pytest

from conftest import BUILT_AT, FETCHED_AT
from fakes import FakeAnthropic, FakeCityClient
from pipeline import build_site, fetch_meetings, fetch_minutes, i18n, summarize, translate
from pipeline.config import load_config

# What one FakeAnthropic translation costs at Claude Sonnet 5.5's prices: 600 tokens in at $2, 300 out at $10 a million.
TRANSLATION = 0.0042
# And its review at Claude Sonnet 5.5's: 500 tokens in at $2, 50 out at $10 a million.
REVIEW = 0.0015


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
    """The calls that translate, not those that review a translation."""
    return [c for c in client.calls if "translate" in c["system"] and not c["system"].startswith("You check")]


def test_numbers_are_checked_without_ai():
    source = {"headline": "Approved $1,500 for 12 Main St, 5-0.", "summary": "On October 22, 2026 at 7:00 PM.",
              "decisions": ["Approved 3 permits", "Denied 1"]}
    good = {"headline": "Se aprobaron $1,500 para 12 Main St, 5-0.", "summary": "El 22 de octubre de 2026 a las 7:00 p. m.",
            "decisions": ["Se aprobaron 3 permisos", "Se negó 1"]}
    assert translate.check(source, good, "minutes") == "ok"
    # Spanish number formats are the same amounts.
    assert translate.check(source, {**good, "headline": "Se aprobaron $1.500 para 12 Main St, 5-0."}, "minutes") == "ok"
    assert translate.check(source, {**good, "headline": "Se aprobaron $1,600 para 12 Main St, 5-0."}, "minutes") \
        == "headline: 1500 not in the translation"
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
    assert record["headline"].startswith("ES ") and record["decisions"] == ["ES aprobó the site plan for 12 Main St, 5-0"]
    assert record["check"] == "ok" and record["review"] == "ok" and record["model"] == "claude-sonnet-5-5"
    assert record["cost"] == pytest.approx(TRANSLATION + REVIEW) and record["prompt_version"] == translate.VERSION
    call = translation_calls(client)[0]
    assert call["model"] == "claude-sonnet-5-5" and "Spanish" in call["system"]
    assert call["output_config"]["format"]["type"] == "json_schema" and call["output_config"]["effort"] == "low"
    # Each translation's meaning is reviewed by the larger model, English beside Spanish.
    reviews = [c for c in client.calls if "problems" in c["output_config"]["format"]["schema"]["properties"]]
    assert reviews and reviews[0]["model"] == "claude-sonnet-5-5" and reviews[0]["output_config"]["effort"] == "low"
    # The review knows the words the translator was told to use, so it doesn't fail them.
    assert "public hearing = audiencia pública" in reviews[0]["system"]
    assert "Never guess anyone's gender" in call["system"] and "levantar la sesión" in call["system"]


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


def test_older_summaries_are_translated_before_older_documents_are_summarized(tmp_path):
    """Older summaries already on the site get their translations before the backlog's English,
    which costs ten times as much: a run that spends its backlog budget on English summaries
    would otherwise never translate them."""
    config = spanish_town(tmp_path, languages=("en",))
    # 12 new documents and 2 of the 4 older ones summarized, in English only.
    summarize.run(config, FakeAnthropic(), tmp_path, limit=14, now=FETCHED_AT)
    config["site"]["languages"] = ["en", "es"]
    # Enough for older documents to pay for two translations, not one more summary.
    result = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT,
                           backlog_allowance=2 * (TRANSLATION + REVIEW))
    assert result["translated"] == 14 and result["summarized"] == 0 and result["remaining"] == 2
    assert "older documents wait" in result["stopped"]


def test_translations_count_in_the_budget(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)
    row = json.loads((tmp_path / summarize.LEDGER).read_text())["2026-09"]
    # Five summaries, and one batch of the town's own text and names drafted first.
    assert row["documents"] == 5 and row["translations"] == 6
    assert row["translation_cost"] == round(6 * (TRANSLATION + REVIEW), 4)
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
    # Made again once; failing again, it's kept and not paid for again.
    again = FakeAnthropic(translation_drops_numbers=True)
    assert summarize.run(config, again, tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(failed)
    assert translations(tmp_path)[sha]["attempts"] == 2 and translate.failed(tmp_path, "es", sha, english, record["kind"])
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0
    # The first try, made the same month and replaced by the second, is still counted.
    second = translations(tmp_path)[sha]
    assert second["replaced_cost"] == record["cost"]
    month = translate.month_counts(tmp_path)["2026-09"]
    assert month["cost"] == pytest.approx(sum(r["cost"] + r.get("replaced_cost", 0) for r in translations(tmp_path).values())
                                          + sum(b["cost"] for b in json.loads(next((tmp_path / "strings").glob("*.json"))
                                                                              .read_text()).get("batches", [])))


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
    minutes = [p for p in (out / "es" / "meetings").glob("20*/index.html") if "ES The board aprobó" in p.read_text()]
    assert minutes
    page = minutes[0].read_text()
    assert "<p>ES The board aprobó a site plan for 12 Main St.</p>" in page
    assert "hasn't been translated" not in page
    # Said plainly: translated by AI, with the English it came from one link away.
    path = "/" + str(minutes[0].relative_to(out / "es").parent) + "/"
    assert f'Traducido automáticamente con IA del <a href="{path}" hreflang="en">resumen en inglés</a>' in page
    assert "(PDF, en inglés)" in page and "</a> (en inglés)" in page
    english = (out / minutes[0].relative_to(out / "es")).read_text()
    assert "Translated automatically" not in english and "en inglés" not in english
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
    # Made twice and failed: the page says so, rather than that it's waiting.
    summarize.run(config, FakeAnthropic(translation_drops_numbers=True), tmp_path, limit=50, now=FETCHED_AT)
    build_site.build("gloucester", out, data_dir=tmp_path, now=BUILT_AT)
    page = next(p for p in (out / "es" / "meetings").glob("20*/index.html")
                if "a site plan for 12 Main St." in p.read_text()).read_text()
    with i18n.use("es"):
        assert i18n.gettext("This summary is shown in English: its automatic translation didn't pass our checks.") in page


def test_the_check_catches_what_turns_a_translation_round():
    """The wrong translations the October 2026 review found passing the old check (numbers only)."""
    english = ("The council voted not to approve the $3 million budget at 7:00 pm; it failed 3-4. "
               "Maria Rodriguez of 12 Essex Street spoke. Tabled unanimously.")
    good = ("El concejo votó no aprobar el presupuesto de $3 millones a las 7:00 p. m.; fue rechazado 3-4. "
            "Maria Rodriguez, de 12 Essex Street, habló. Se pospuso por unanimidad.")
    def check(text):
        return translate.check({"headline": "", "summary": english, "decisions": []},
                               {"headline": "", "summary": text, "decisions": []}, "minutes")
    assert check(good) == "ok"
    wrong = {
        "votó para aprobar": "a \"not\", denied, or failed",
        "fue aprobado 4-3": "a \"not\", denied, or failed",
        "Mario Rodrigues": "Rodriguez not in the translation",
        "12 Calle Elm": "Essex not in the translation",
        "$3 mil millones": "scale differs",
        "7:00 a. m.": "a.m. or p.m.",
        "Se aprobó por unanimidad": "tabled in the English",
        "Se pospuso por mayoría": "unanimous",
    }
    replaced = {"votó para aprobar": "votó no aprobar", "fue aprobado 4-3": "fue rechazado 3-4", "Mario Rodrigues": "Maria Rodriguez",
                "12 Calle Elm": "12 Essex Street", "$3 mil millones": "$3 millones", "7:00 a. m.": "7:00 p. m.",
                "Se aprobó por unanimidad": "Se pospuso por unanimidad", "Se pospuso por mayoría": "Se pospuso por unanimidad"}
    for text, problem in wrong.items():
        assert problem in check(good.replace(replaced[text], text)), text
    assert "added" in check(good + " El 5.")
    # The other way round: denied as approved, approved as denied, an invented "no".
    def headline(en, es):
        return translate.check({"headline": en, "summary": "", "decisions": []}, {"headline": es, "summary": "", "decisions": []}, "minutes")
    assert headline("Denied the permit.", "Aprobó el permiso.") != "ok"
    assert headline("Approved the permit.", "Negó el permiso.") != "ok"
    assert headline("Approved the permit.", "No aprobó el permiso.") != "ok"
    assert headline("Approved purchases not to exceed $8,000.", "Aprobó compras sin exceder $8,000.") == "ok"
    assert headline("Granted a variance for a nonconforming garage.", "Otorgó una variación para un garaje no conforme.") == "ok"
    # Spanish number formats, and English ordinals written as words.
    assert headline("Approved $1,500, $5.8 million, and $2,500.", "Aprobó $1.500, $5,8 millones y $2500.") == "ok"
    assert headline("Renewed a 2nd hand dealer's license.", "Renovó una licencia de comerciante de segunda mano.") == "ok"


def test_a_second_try_corrects_the_first(tmp_path):
    """A translation that fails is made again with the first translation and what was wrong with
    it, so the model fixes it rather than trying its luck again."""
    config = spanish_town(tmp_path)
    flagged = FakeAnthropic(review_problems=[{"id": "headline", "problem": "\"la presidenta\" guesses the Chair's gender"}])
    summarize.run(config, flagged, tmp_path, limit=50, now=FETCHED_AT)
    sha, first = next(iter(translations(tmp_path).items()))
    again = FakeAnthropic()
    summarize.run(config, again, tmp_path, limit=50, now=FETCHED_AT)
    prompt = next(c for c in translation_calls(again) if first["headline"] in c["messages"][0]["content"])["messages"][0]["content"]
    assert "didn't pass a check" in prompt and "headline: \"la presidenta\" guesses the Chair's gender" in prompt
    assert translations(tmp_path)[sha]["attempts"] == 2 and translations(tmp_path)[sha]["review"] == "ok"
    # A first try, or one whose review couldn't be made, has nothing to correct.
    assert all("didn't pass a check" not in c["messages"][0]["content"] for c in translation_calls(flagged))
    assert translate.what_failed(tmp_path, {**first, "review": "not reviewed: overloaded"},
                                 json.loads((tmp_path / "summaries" / f"{sha}.json").read_text()), first["kind"], "es") is None


def test_a_review_that_finds_nothing_wrong_doesnt_fail_it(tmp_path):
    """The review sometimes writes an entry that ends "No error": it's not a problem."""
    config = spanish_town(tmp_path)
    reviewed = FakeAnthropic(review_problems=[{"id": "summary", "problem": "This is faithful. No error.", "is_error": False}])
    summarize.run(config, reviewed, tmp_path, limit=50, now=FETCHED_AT)
    assert all(r["review"] == "ok" for r in translations(tmp_path).values())


def test_a_model_without_effort_gets_none(tmp_path):
    """Claude Haiku 4.5 refuses an effort, so a town that goes back to it sends none."""
    config = spanish_town(tmp_path)
    config["summaries"].update(translation_model="claude-haiku-4-5-20251001", translation_input_price=1.0,
                               translation_output_price=5.0)
    client = FakeAnthropic()
    summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    calls = translation_calls(client)
    assert calls and all(c["model"] == "claude-haiku-4-5-20251001" and "effort" not in c["output_config"] for c in calls)
    config["summaries"]["translation_effort"] = "medium"
    assert translate.output_config(config, {})["effort"] == "medium"


def test_a_translation_the_review_flags_isnt_shown(tmp_path):
    config = spanish_town(tmp_path)
    flagged = FakeAnthropic(review_problems=[{"id": "headline", "problem": "\"adjourned\" as \"se disolvió\""}])
    summarize.run(config, flagged, tmp_path, limit=50, now=FETCHED_AT)
    sha, record = next(iter(translations(tmp_path).items()))
    assert record["check"] == "ok" and record["review"] == 'headline: "adjourned" as "se disolvió"'
    english = json.loads((tmp_path / "summaries" / f"{sha}.json").read_text())
    assert translate.shown(tmp_path, "es", sha, english, record["kind"]) is None
    # Made again once, and shown when the review passes.
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 16
    assert translate.shown(tmp_path, "es", sha, english, record["kind"])


def test_a_translation_from_an_older_prompt_isnt_shown(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    sha, record = next(iter(translations(tmp_path).items()))
    english = json.loads((tmp_path / "summaries" / f"{sha}.json").read_text())
    assert translate.shown(tmp_path, "es", sha, english, record["kind"])
    translate.path(tmp_path, "es", sha).write_text(json.dumps({**record, "prompt_version": translate.VERSION - 1}))
    assert translate.shown(tmp_path, "es", sha, english, record["kind"]) is None
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 1


def test_a_run_drafts_the_towns_own_text_and_the_site_shows_it(tmp_path, monkeypatch):
    """A town with no [strings.es] at all: its run drafts the config's text and the boards' names,
    so its Spanish pages build, with nothing in English, before anyone translates them."""
    config = spanish_town(tmp_path)
    config["strings"] = {}
    result = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    assert result["drafted_texts"] > 10 and not result["errors"]
    saved = translate.saved_drafts(tmp_path, "es")
    assert all(d["check"] == "ok" and d["text"].startswith("ES ") for d in saved["drafts"].values())
    assert config["site"]["tagline"] in saved["drafts"] and "City Council" not in saved["drafts"]  # the engine has it
    monkeypatch.setattr(build_site, "load_config", lambda town: config)
    missing: dict = {}
    build_site.build("gloucester", tmp_path / "site", data_dir=tmp_path, now=BUILT_AT, missing=missing)
    assert not missing["es"]["config"] and not missing["es"]["data"] and missing["es"]["drafted"]
    assert "ES " + config["site"]["tagline"] in (tmp_path / "site" / "es" / "index.html").read_text()
    # Nothing left to draft: the next run asks for none.
    client = FakeAnthropic()
    assert summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["drafted_texts"] == 0
    assert not [c for c in client.calls if "translations" in c["output_config"]["format"]["schema"]["properties"]]


def test_a_draft_that_loses_a_number_isnt_shown(tmp_path):
    config = spanish_town(tmp_path)
    config["strings"] = {}
    summarize.run(config, FakeAnthropic(translation_drops_numbers=True), tmp_path, limit=50, now=FETCHED_AT)
    saved = translate.saved_drafts(tmp_path, "es")["drafts"]
    failed = {en for en, d in saved.items() if d["check"] != "ok"}
    assert failed and all(any(c.isdigit() for c in en) for en in failed)
    assert not failed & set(translate.drafts(tmp_path, "es"))


def test_a_draft_the_review_flags_isnt_shown_or_drafted_forever(tmp_path):
    """The review catches "Mayor" as "Gobernador"; the text stays in English, and after a second
    try isn't drafted again every run."""
    config = spanish_town(tmp_path)
    config["strings"] = {}
    flagged = FakeAnthropic(review_problems=[{"id": "0", "problem": "a wrong title"}])
    summarize.run(config, flagged, tmp_path, limit=50, now=FETCHED_AT)
    saved = translate.saved_drafts(tmp_path, "es")["drafts"]
    wrong = [en for en, d in saved.items() if d["check"] == "review: a wrong title"]
    assert len(wrong) == 1 and wrong[0] not in translate.drafts(tmp_path, "es")
    again = FakeAnthropic(review_problems=[{"id": "0", "problem": "a wrong title"}])
    summarize.run(config, again, tmp_path, limit=50, now=FETCHED_AT)
    assert translate.saved_drafts(tmp_path, "es")["drafts"][wrong[0]]["attempts"] == 2
    third = FakeAnthropic()
    summarize.run(config, third, tmp_path, limit=50, now=FETCHED_AT)
    assert not [c for c in third.calls if "translations" in c["output_config"]["format"]["schema"]["properties"]]


def test_drafts_review_as_strings_lines(tmp_path):
    import tomllib
    config = spanish_town(tmp_path)
    config["strings"] = {"es": {"Board of Assessors Hearing": "Audiencia de la Junta de Tasadores"}}
    file = translate.strings_path(tmp_path, "es")
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"batches": [], "drafts": {
        "Fish Pier Committee": {"text": "Comité del Muelle", "check": "ok", "prompt_version": translate.NAMES_VERSION},
        "Board of Assessors Hearing": {"text": "Audiencia", "check": "ok", "prompt_version": translate.NAMES_VERSION},
        "Ward 9 \"North\"": {"text": "Distrito \"Norte\"", "check": "9 not in the translation", "prompt_version": 1}}}))
    out = translate.review(config, tmp_path, "es")
    # Ready to paste into [strings.es]: the town's own translations aren't repeated, failed drafts are commented out.
    assert tomllib.loads(out) == {"Fish Pier Committee": "Comité del Muelle"}
    assert '# "Ward 9 \\"North\\"" = "Distrito \\"Norte\\""  # 9 not in the translation' in out


def test_a_decision_that_fails_its_fact_check_isnt_shown_in_either_language(tmp_path, monkeypatch):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    sha, record = next((s, r) for s, r in ((f.stem, json.loads(f.read_text())) for f in (tmp_path / "summaries").glob("*.json"))
                       if r.get("kind") == "minutes")
    record["fact_check"] = {"version": 1, "source": "pdf", "result": "failed",
                            "problems": [{"field": "decisions", "entry": 1, "kind": "number", "what": "12"}]}
    (tmp_path / "summaries" / f"{sha}.json").write_text(json.dumps(record))
    monkeypatch.setattr(build_site, "load_config", lambda town: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=tmp_path, now=BUILT_AT)
    english = next(p for p in (out / "meetings").glob("20*/index.html") if "1 decision isn't shown" in p.read_text())
    page = english.read_text()
    assert "1 decision isn't shown: it couldn't be matched to the minutes." in page
    assert "Approved the site plan for 12 Main St" not in page
    spanish = (out / "es" / english.relative_to(out)).read_text()
    assert "1 decisión no aparece: no se pudo comprobar con las actas." in spanish


def test_what_the_english_writes_short_may_be_written_out():
    """False alarms from the 2026-10-07 audit of prompt 4's translations: each a right translation."""
    def headline(en, es):
        return translate.check({"headline": en, "summary": "", "decisions": []}, {"headline": es, "summary": "", "decisions": []}, "minutes")
    # A fiscal year, written out, with the English kept after it, or with two figures.
    assert headline("Accepted the FY27 grant.", "Aceptó la subvención del año fiscal 2027.") == "ok"
    assert headline("Reviewed FY26 balances.", "Revisó los saldos del año fiscal 2026 (FY26).") == "ok"
    assert headline("Reviewed the FY 27 budget.", "Revisó el presupuesto del año fiscal 27.") == "ok"
    assert headline("Accepted the FY27 grant.", "Aceptó la subvención del año fiscal 2028.") != "ok"
    # An abbreviation written out; a name at the end of a sentence still counts.
    assert headline("Amended Ch. 22 Sec. 22-270.", "Enmendó el capítulo 22, sección 22-270.") == "ok"
    assert headline("Disclosure by Jennifer Duran, Asst City Clerk.", "Divulgación de Jennifer Duran, subsecretaria municipal.") == "ok"
    assert "Cruz" in headline("Seconded by Councilor Cruz.", "Secundado por el concejal.")
    # A name the Spanish writes with its accent.
    assert headline("Celebrates America's 250th.", "Celebra los 250 años de América.") == "ok"
    # A cap, not a "not"; a time range's a.m.; a date with dots.
    assert headline("Awarded a contract not to exceed $15,545.", "Adjudicó un contrato por un máximo de $15,545.") == "ok"
    assert headline("Interviews 9:00-11:00 A.M.", "Entrevistas de 9:00 a. m. a 11:00 a. m.") == "ok"
    assert headline("Capital Plan - 1.28.26", "Plan de capital - 28 de enero de 2026") == "ok"
    assert headline("Capital Plan - 1.28.26", "Plan de capital - 27 de enero de 2026") != "ok"
    # Failed and declined, in either language's words.
    assert headline("Failed motion to reconsider.", "Fracasa la moción para reconsiderar.") == "ok"
    assert headline("Declined to sell the land.", "Rechazó vender el terreno.") == "ok"
    assert headline("Declined to sell the land.", "Aprobó vender el terreno.") != "ok"


def test_a_town_s_ordinary_words_are_whole_words_from_every_town(tmp_path):
    """"Bryan" doesn't make "ryan" an ordinary word, so a Ryan is still checked; a word another town
    of the network writes in lowercase is ordinary everywhere."""
    for town, text in (("a-ma", "Bryan Lee spoke about wastewater."), ("b-ma", "Ryan spoke.")):
        folder = tmp_path / "towns" / town / "data" / "summaries"
        folder.mkdir(parents=True)
        (folder / "x.json").write_text(json.dumps({"summary": text}))
    words = translate.town_words(tmp_path / "towns" / "b-ma" / "data")
    assert "wastewater" in words and "ryan" not in words
    assert translate.names("Ryan reviewed the Wastewater Plan.", words) == {"Ryan"}
