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


def system(call) -> str:
    """A call's system prompt: a summary's is text, a translation's is sent cached."""
    return call["system"] if isinstance(call["system"], str) else call["system"][0]["text"]


def translation_calls(client):
    """The calls that translate, not those that review a translation."""
    return [c for c in client.calls if "translate" in system(c) and not system(c).startswith("You check")]


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
    assert call["model"] == "claude-sonnet-5-5" and "Spanish" in system(call)
    assert call["output_config"]["format"]["type"] == "json_schema" and call["output_config"]["effort"] == "low"
    # Each translation's meaning is reviewed by the larger model, English beside Spanish.
    reviews = [c for c in client.calls if "problems" in c["output_config"]["format"]["schema"]["properties"]]
    assert reviews and reviews[0]["model"] == "claude-sonnet-5-5" and reviews[0]["output_config"]["effort"] == "low"
    # The review knows the words the translator was told to use, so it doesn't fail them.
    assert "public hearing = audiencia pública" in system(reviews[0])
    assert "Never guess anyone's gender" in system(call) and "levantar la sesión" in system(call)
    # The system prompts are the same for every request, so they're cached; nothing else is.
    assert all(c["system"][-1]["cache_control"] == {"type": "ephemeral"} for c in [call, reviews[0]])
    assert "cache_control" not in call


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
    month = summarize.recount(tmp_path, summarize.summary_settings(config), "2026-09")
    paid = (sum(r["cost"] + r.get("replaced_cost", 0) for r in translations(tmp_path).values())
            + sum(b["cost"] for b in json.loads(next((tmp_path / "strings").glob("*.json")).read_text()).get("batches", [])))
    assert month["translation_cost"] == pytest.approx(paid, abs=1e-4)
    # The log has every request: the first tries' too, and both reviews.
    row = json.loads((tmp_path / summarize.LEDGER).read_text())["2026-09"]
    assert row["translation_cost"] == pytest.approx(paid, abs=1e-4)


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
    assert ", en inglés)." in page and ">Copia guardada<" in page
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


class ReviewDown(FakeAnthropic):
    """The stand-in, with every review request failing (an API error)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        create = self.messages.create

        def failing(**request):
            if "problems" in request["output_config"]["format"]["schema"]["properties"]:
                self.calls.append(request)
                raise RuntimeError("overloaded")
            return create(**request)
        self.messages.create = failing


def review_calls(client):
    """The reviews of summaries' translations, not of the town's own text."""
    return [c for c in client.calls if system(c).startswith("You check") and '"id": "headline"' in c["messages"][0]["content"]]


def unreviewed(tmp_path, **changes):
    """Every saved translation as if its review couldn't be made."""
    for sha, record in translations(tmp_path).items():
        translate.path(tmp_path, "es", sha).write_text(json.dumps({**record, "review": "not reviewed: overloaded", **changes}))


def test_a_review_that_couldnt_be_made_is_asked_again_by_itself(tmp_path):
    """The translation is kept and only its review is sent again; once it passes, it's shown. Its
    tries (attempts) don't count it, and its cost is recorded once in its file and the log."""
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    unreviewed(tmp_path)
    before = translations(tmp_path)
    sha, record = next(iter(before.items()))
    english = json.loads((tmp_path / "summaries" / f"{sha}.json").read_text())
    assert translate.shown(tmp_path, "es", sha, english, record["kind"]) is None
    assert translate.current(tmp_path, "es", sha, english, record["kind"]) is None
    client = FakeAnthropic()
    assert summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(before)
    assert translation_calls(client) == [] and len(review_calls(client)) == len(before)
    for sha, after in translations(tmp_path).items():
        assert after["review"] == "ok" and after["review_retries"] == 1 and after["attempts"] == before[sha]["attempts"]
        assert {f: after[f] for f in translate.FIELDS[after["kind"]]} == {f: before[sha][f] for f in translate.FIELDS[after["kind"]]}
        assert after["cost"] == pytest.approx(REVIEW) and after["replaced_cost"] == pytest.approx(before[sha]["cost"])
    assert translate.shown(tmp_path, "es", sha, json.loads((tmp_path / "summaries" / f"{sha}.json").read_text()), after["kind"])
    assert summarize.ledger_differences(tmp_path, summarize.summary_settings(config)) == []
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0


def summary_translation_calls(client):
    """The calls that translate a summary, not the town's own text."""
    return [c for c in translation_calls(client) if system(c).startswith("You translate short summaries")]


def test_a_retried_review_that_flags_the_translation_has_it_made_again(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    unreviewed(tmp_path)
    flagged = FakeAnthropic(review_problems=[{"id": "headline", "problem": "\"adjourned\" as \"se disolvió\""}])
    summarize.run(config, flagged, tmp_path, limit=50, now=FETCHED_AT)
    # Each review asked again by itself; one it flags is then made again as any other, as a correction
    # (an older document's in the same run, as its translations are looked at twice).
    assert len(review_calls(flagged)) == len(translations(tmp_path)) + len(summary_translation_calls(flagged))
    assert all("didn't pass a check" in c["messages"][0]["content"] for c in summary_translation_calls(flagged))
    waiting = {sha for sha, r in translations(tmp_path).items() if r["attempts"] == 1}
    assert waiting and all(r["review"] == 'headline: "adjourned" as "se disolvió"' and r["review_retries"] == 1
                           for sha, r in translations(tmp_path).items() if sha in waiting)
    again = FakeAnthropic()
    assert summarize.run(config, again, tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(waiting)
    assert len(summary_translation_calls(again)) == len(waiting)
    assert all("didn't pass a check" in c["messages"][0]["content"] for c in summary_translation_calls(again))
    assert all(r["review"] == "ok" and r["attempts"] == 2 and "review_retries" not in r
               for sha, r in translations(tmp_path).items() if sha in waiting)


def test_reviews_that_keep_failing_are_asked_again_a_few_times_only(tmp_path):
    """REVIEW_RETRIES review-only tries for each translation made, within its ATTEMPTS: then it's left."""
    config = spanish_town(tmp_path)
    summarize.run(config, ReviewDown(), tmp_path, limit=50, now=FETCHED_AT)
    shas = set(translations(tmp_path))
    assert all(r["review"].startswith(translate.UNREVIEWED) for r in translations(tmp_path).values())
    drafts, reviews = len(shas), len(shas)
    for _ in range(10):
        client = ReviewDown()
        if not summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["translated"]:
            break
        drafts += len(summary_translation_calls(client))
        reviews += len(review_calls(client))
    else:
        raise AssertionError("still sending requests after 10 runs")
    assert drafts == translate.ATTEMPTS * len(shas)
    assert reviews == translate.ATTEMPTS * (1 + translate.REVIEW_RETRIES) * len(shas)
    assert all(r["attempts"] == translate.ATTEMPTS and r["review_retries"] == translate.REVIEW_RETRIES
               for r in translations(tmp_path).values())
    # One that has had its tries still has its review asked again, with no new translation.
    unreviewed(tmp_path, review_retries=0)
    client = FakeAnthropic()
    assert summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(shas)
    assert translation_calls(client) == [] and all(r["review"] == "ok" for r in translations(tmp_path).values())


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


def older(tmp_path, **changes):
    """Every saved translation as if an older prompt had made it, with changes."""
    for sha, record in translations(tmp_path).items():
        translate.path(tmp_path, "es", sha).write_text(json.dumps({**record, "prompt_version": translate.VERSION - 1,
                                                                   **changes}))


def english_of(tmp_path, sha):
    return json.loads((tmp_path / "summaries" / f"{sha}.json").read_text())


class Rendering(FakeAnthropic):
    """The stand-in, with its translations writing one phrase as another (a glossary term as given)."""

    def __init__(self, phrase, wording, **kwargs):
        super().__init__(**kwargs)
        create = self.messages.create

        def rendered(**request):
            response = create(**request)
            for block in response.content:
                block.text = block.text.replace(phrase, wording)
            return response
        self.messages.create = rendered


def test_a_translation_from_an_older_prompt_that_passes_is_shown_and_kept(tmp_path):
    """Raising VERSION makes nothing again and hides nothing: a translation an older prompt made,
    from the English as it is now, that passes the checks as they are now, stays."""
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    older(tmp_path)
    before = translations(tmp_path)
    for sha, record in before.items():
        assert translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), record["kind"]) == record
        assert translate.current(tmp_path, "es", sha, english_of(tmp_path, sha), record["kind"]) == record
    client = FakeAnthropic()
    assert summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0
    assert translation_calls(client) == [] and translations(tmp_path) == before


def test_a_translation_from_an_older_prompt_that_fails_a_glossary_term_is_made_again(tmp_path):
    """An older translation that doesn't write a glossary term as given now is made again, and stays
    hidden (the English shown) until a translation passes."""
    config = spanish_town(tmp_path)
    term = {"en": {"definition": "A drawing of a project."}, "es": {"term": "site plan", "definition": "Un plano."}}
    config["terms"] = {"site plan": term}
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    older(tmp_path, attempts=2)
    before = translations(tmp_path)
    config["terms"] = {"site plan": {**term, "es": {"term": "plano del sitio", "definition": "Un plano."}}}
    terms = translate.glossary.entries(config)
    # Those whose English uses the term ("site plan", "Site Plans") fail now, and aren't shown.
    uses = {sha for sha, r in before.items()
            if translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), r["kind"], terms) is None}
    assert uses and len(uses) < len(before)
    assert all("isn't written" in translate.what_failed(tmp_path, before[sha], english_of(tmp_path, sha),
                                                         before[sha]["kind"], "es", terms) for sha in uses)
    # Made again (still writing "site plan", so failing), and still not shown; the others are kept. (An older
    # document's is tried twice in a run: once among the older summaries, again after the older documents.)
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] >= len(uses)
    after = translations(tmp_path)
    assert all(after[sha] == before[sha] for sha in before if sha not in uses)
    for sha in uses:
        assert after[sha]["prompt_version"] == translate.VERSION and after[sha]["attempts"] in (1, 2)
        assert after[sha]["check"].endswith('"site plan" isn\'t written "plano del sitio"') and "previous" not in after[sha]
        assert translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), after[sha]["kind"], terms) is None
    # A try that writes the term as given is shown; those that have had their tries aren't made again.
    second = {sha for sha in uses if after[sha]["attempts"] == 1}
    assert second
    rendering = Rendering("site plan", "plano del sitio")
    rendering.messages.create = (lambda create: lambda **request: create(**{**request, "messages": [
        {**m, "content": m["content"].replace("Site plan", "site plan")} for m in request["messages"]]}))(
        rendering.messages.create)
    assert summarize.run(config, rendering, tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(second)
    for sha in uses:
        shown = translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), before[sha]["kind"], terms)
        assert bool(shown) == (sha in second)
        assert not shown or ("plano del sitio" in json.dumps(shown, ensure_ascii=False) and shown["attempts"] == 2)


def test_a_changed_english_summary_is_translated_again_whatever_made_its_translation(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    older(tmp_path)
    sha, record = next((s, r) for s, r in translations(tmp_path).items() if r["kind"] == "minutes")
    file = tmp_path / "summaries" / f"{sha}.json"
    file.write_text(json.dumps({**json.loads(file.read_text()), "headline": "Approved a site plan for 14 Main St."}))
    # Not shown: it isn't a translation of this English.
    assert translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), "minutes") is None
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 1
    remade = translations(tmp_path)[sha]
    assert "14 Main St" in remade["headline"] and remade["prompt_version"] == translate.VERSION and remade["attempts"] == 1
    assert translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), "minutes") == remade


def test_remake_below_makes_older_translations_again_and_keeps_them_shown_until_then(tmp_path, monkeypatch):
    """REMAKE_BELOW, raised on purpose, makes every translation older than it again, passing or not;
    each stays shown until a new one passes, and a new one that fails has its tries."""
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    older(tmp_path)
    before = translations(tmp_path)
    monkeypatch.setattr(translate, "REMAKE_BELOW", translate.VERSION)
    assert all(translate.current(tmp_path, "es", sha, english_of(tmp_path, sha), r["kind"]) is None for sha, r in before.items())
    flagged = lambda: FakeAnthropic(review_problems=[{"id": "headline", "problem": "a wrong word"}])
    first = flagged()
    # (An older document's is tried twice in a run: once among the older summaries, again after the older documents.)
    made = summarize.run(config, first, tmp_path, limit=50, now=FETCHED_AT)["translated"]
    # Each made again as a first try, not as a correction of the one that passed.
    corrections = [c for c in translation_calls(first) if "didn't pass a check" in c["messages"][0]["content"]]
    assert made > len(before) and len(corrections) == made - len(before)
    once = set()
    for sha, record in translations(tmp_path).items():
        assert record["review"] == "headline: a wrong word" and record["prompt_version"] == translate.VERSION
        once |= {sha} if record["attempts"] == 1 else set()
        # The one it replaced is still shown.
        shown = translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), record["kind"])
        assert shown["prompt_version"] == translate.VERSION - 1 and shown["headline"] == before[sha]["headline"]
        assert not translate.failed(tmp_path, "es", sha, english_of(tmp_path, sha), record["kind"])
    # Its second try fails too: the older one stays shown, and nothing is made a third time.
    assert once and summarize.run(config, flagged(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(once)
    for sha, record in translations(tmp_path).items():
        assert record["attempts"] == 2 and record["previous"]["generated_at"] == before[sha]["generated_at"]
        assert translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), record["kind"])["prompt_version"] \
            == translate.VERSION - 1
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0
    # Once the glossary changes, a new try passes and takes its place.
    config["terms"] = {"M.G.L.": "Massachusetts General Laws."}
    config["terms"]["Select Board"] = SELECT_BOARD[0]["languages"]
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(before)
    terms = translate.glossary.entries(config)
    for sha, record in translations(tmp_path).items():
        assert "previous" not in record and record["review"] == "ok"
        assert translate.shown(tmp_path, "es", sha, english_of(tmp_path, sha), record["kind"], terms) == record


def test_tries_are_still_counted_for_each_glossary_whatever_the_prompt_version(tmp_path, monkeypatch):
    """An older translation that failed has the tries it had left with this glossary, no more: one
    that's had them isn't made again because VERSION went up (RETRY_BELOW not raised with it)."""
    monkeypatch.setattr(translate, "RETRY_BELOW", translate.VERSION - 1)
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(review_problems=[{"id": "summary", "problem": "a wrong word"}]), tmp_path,
                  limit=50, now=FETCHED_AT)
    shas = sorted(translations(tmp_path))
    older(tmp_path)
    spent = shas[:5]
    for sha in spent:
        file = translate.path(tmp_path, "es", sha)
        file.write_text(json.dumps({**json.loads(file.read_text()), "attempts": translate.ATTEMPTS}))
    flagged = FakeAnthropic(review_problems=[{"id": "summary", "problem": "a wrong word"}])
    assert summarize.run(config, flagged, tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(shas) - len(spent)
    after = translations(tmp_path)
    assert all(after[sha]["attempts"] == 2 and after[sha]["prompt_version"] == translate.VERSION
               for sha in shas if sha not in spent)
    assert all(after[sha]["prompt_version"] == translate.VERSION - 1 for sha in spent)
    assert all(translate.failed(tmp_path, "es", sha, english_of(tmp_path, sha), after[sha]["kind"]) for sha in shas)
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0


def test_a_translation_that_failed_with_a_prompt_older_than_retry_below_has_its_tries_again(tmp_path, monkeypatch):
    """RETRY_BELOW, raised with a prompt that fixes what made translations fail: one that failed for good
    with an older prompt is corrected again, with tries of its own, while one that passes stays."""
    monkeypatch.setattr(translate, "RETRY_BELOW", translate.VERSION)
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    shas = sorted(translations(tmp_path))
    older(tmp_path)
    spent = shas[:5]
    for sha in spent:
        file = translate.path(tmp_path, "es", sha)
        file.write_text(json.dumps({**json.loads(file.read_text()), "attempts": translate.ATTEMPTS,
                                    "review": "summary: a wrong word"}))
    assert all(translate.failed(tmp_path, "es", sha, english_of(tmp_path, sha), translations(tmp_path)[sha]["kind"])
               is False for sha in spent)
    client = FakeAnthropic()
    assert summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["translated"] == len(spent)
    after = translations(tmp_path)
    assert all(after[sha]["attempts"] == 1 and after[sha]["prompt_version"] == translate.VERSION
               and after[sha]["review"] == "ok" for sha in spent)
    assert all(after[sha]["prompt_version"] == translate.VERSION - 1 for sha in shas if sha not in spent)
    # The correction says what was wrong.
    assert any("a wrong word" in c["messages"][0]["content"] for c in client.calls)


def test_the_translator_is_given_the_spanish_of_each_board_the_summary_names(tmp_path):
    """The meeting's board, and every other name of the town's the English has, as the site writes it:
    from [strings.es], the engine's own for boards many towns share, or a draft that passes."""
    config = spanish_town(tmp_path)
    config["strings"] = {"es": {"City Council": "Concejo Municipal"}}
    names = translate.town_names(config, tmp_path, "es")
    assert names["City Council"] == "Concejo Municipal" and names["Conservation Commission"]
    source = {"headline": "Sent the plan to the Conservation Commission",
              "summary": "The City Council and the Planning Board heard it. The Commissioner spoke.", "decisions": []}
    text = translate.board_names(names, "es", "City Council", source)
    assert text.startswith('The board is "City Council"; in Spanish, "Concejo Municipal".\n')
    assert f'"Conservation Commission" = "{names["Conservation Commission"]}"' in text
    assert f'"Planning Board" = "{names["Planning Board"]}"' in text
    # Only names the summary has, whole, and the meeting's board once.
    assert text.count("City Council") == 1 and "Board of Health" not in text
    assert translate.board_names({}, "es", "City Council", source) == ""


def test_drafts_from_an_older_prompt_stay_until_remake_below_says_otherwise(tmp_path, monkeypatch):
    config = spanish_town(tmp_path)
    config["strings"] = {}
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    file = translate.strings_path(tmp_path, "es")
    saved = json.loads(file.read_text())
    saved["drafts"] = {en: {**d, "prompt_version": translate.NAMES_VERSION - 1} for en, d in saved["drafts"].items()}
    file.write_text(json.dumps(saved))
    shown = translate.drafts(tmp_path, "es")
    assert shown and set(shown) == set(saved["drafts"])
    drafting = lambda client: [c for c in client.calls if "translations" in c["output_config"]["format"]["schema"]["properties"]]
    client = FakeAnthropic()
    assert summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["drafted_texts"] == 0 and not drafting(client)
    # Raised on purpose: drafted again; one the review flags leaves the older draft shown.
    monkeypatch.setattr(translate, "NAMES_REMAKE_BELOW", translate.NAMES_VERSION)
    flagged = FakeAnthropic(review_problems=[{"id": "0", "problem": "a wrong title"}])
    assert summarize.run(config, flagged, tmp_path, limit=50, now=FETCHED_AT)["drafted_texts"] == len(shown)
    after = translate.saved_drafts(tmp_path, "es")["drafts"]
    wrong = [en for en, d in after.items() if d["check"] == "review: a wrong title"]
    assert len(wrong) == 1 and after[wrong[0]]["previous"]["prompt_version"] == translate.NAMES_VERSION - 1
    assert translate.drafts(tmp_path, "es") == shown
    assert all(d["prompt_version"] == translate.NAMES_VERSION for d in after.values())
    # It has a second try, and then isn't drafted again; the older draft stays shown.
    again = FakeAnthropic(review_problems=[{"id": "0", "problem": "a wrong title"}])
    assert summarize.run(config, again, tmp_path, limit=50, now=FETCHED_AT)["drafted_texts"] == 1
    assert translate.saved_drafts(tmp_path, "es")["drafts"][wrong[0]]["attempts"] == 2
    assert translate.drafts(tmp_path, "es") == shown
    third = FakeAnthropic()
    assert summarize.run(config, third, tmp_path, limit=50, now=FETCHED_AT)["drafted_texts"] == 0 and not drafting(third)


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


def test_cached_input_is_counted_at_its_own_price():
    """input_tokens leaves out cached input: written at 1.25 times the input price, read at a tenth."""
    prices = {"input_price": 2.0, "output_price": 10.0}
    assert summarize.cost({"input_tokens": 600, "output_tokens": 300}, prices) == pytest.approx(TRANSLATION)
    cached = {"input_tokens": 600, "output_tokens": 300, "cache_creation_input_tokens": 800, "cache_read_input_tokens": 1000}
    assert summarize.cost(cached, prices) == pytest.approx(TRANSLATION + (800 * 1.25 + 1000 * 0.1) * 2.0 / 1e6)


# A state's glossary term, as its layer gives it (pipeline/glossary.py).
SELECT_BOARD = [{"term": "Select Board", "definition": "The elected board that runs a town.", "bodies": [],
                 "languages": {"en": {"definition": "The elected board that runs a town.",
                                      "also": ["Selectboard", "Board of Selectmen"]},
                               "es": {"term": "Junta de Gobierno (Select Board)", "definition": "La junta que gobierna."}}}]


def test_names_with_capitals_inside_hyphens_and_titles():
    names = translate.names
    # Capitals inside, which a capitalized word's pattern missed, or found only the end of ("Brien").
    assert names("McCallum, LeLacheur, DiStefano, MacDonald, O'Brien and O’Neil spoke.") \
        == {"McCallum", "LeLacheur", "DiStefano", "MacDonald", "O'Brien", "O’Neil"}
    assert names("Sanchez-Parkinson and Cooke-Marra spoke on the Mid-Year report.") == {"Sanchez-Parkinson", "Cooke-Marra"}
    # After a title or "moved by", a name even if it's an ordinary word; in capitals only there.
    assert names("Councilor Hall, Mayor Young, Dr. Wood, and Rep. Long spoke; seconded by Rose.") \
        == {"Hall", "Young", "Wood", "Long", "Rose"}
    assert names("MR. CARUSO: SECOND. The ZBA and the MBTA approved the HVAC plan.") == {"CARUSO"}
    # Not another title, a heading, or the end of a sentence: "Mayor Pro Tem", "Chair Report", "the Mayor. Members".
    assert names("Mayor Pro Tem spoke; Chair Report; seconded by Alderman Barry. He thanked the Mayor. Members left.") \
        == {"Barry"}
    # Ordinary words the dictionary lacks, a title, an acronym's plural, and a month's abbreviation.
    assert names("The Councillors heard the Sustainability and Unaudited reports on Nov 3 and Oct 5 for CDs.") == set()
    # The glossary's words are no one's name either.
    assert names("The Selectboard voted.") == {"Selectboard"}
    assert names("The Selectboard voted.", translate.glossary_words(SELECT_BOARD)) == set()
    # A name in quotes.
    assert names("Approved naming the 'Matzeliger Way' and ('Janice and Scott Conant').") >= {"Matzeliger", "Janice", "Conant"}


def test_a_name_is_kept_whichever_apostrophe_it_s_written_with():
    def check(english, spanish):
        return translate.check({"headline": "", "summary": english, "decisions": []},
                               {"headline": "", "summary": spanish, "decisions": []}, "minutes")
    assert check("Councilor O’Brien spoke.", "El concejal O'Brien habló.") == "ok"
    assert check("Named the 'Matzeliger Way'.", "Nombró la 'Matzeliger Way'.") == "ok"
    assert check("Councillor McCallum spoke.", "El concejal McCallum habló.") == "ok"
    assert check("Councilor Hall spoke.", "El concejal Salón habló.") == "summary: Hall not in the translation"
    assert check("McCallum spoke.", "Mccallum habló.") == "summary: McCallum not in the translation"


def test_the_glossary_is_checked_without_ai():
    def check(english, spanish, terms=SELECT_BOARD):
        return translate.check({"headline": "", "summary": english, "decisions": []},
                               {"headline": "", "summary": spanish, "decisions": []}, "minutes", terms=terms)
    assert check("The Select Board met.", "La Junta de Gobierno (Select Board) se reunió.") == "ok"
    # The English in parentheses is for the first time only; plural and case as the sentence needs.
    assert check("The Select Board met.", "La junta de gobierno se reunió.") == "ok"
    assert check("Two Select Boards met.", "Dos Juntas de Gobierno se reunieron.") == "ok"
    assert check("The Board of Selectmen met.", "La Junta de Gobierno se reunió.") == "ok"
    assert check("The Select Board met.", "La Junta de Selectos (Select Board) se reunió.") \
        == 'summary: "Select Board" isn\'t written "Junta de Gobierno (Select Board)"'
    # Without the glossary, or for a term limited to some boards, nothing is asked.
    assert check("The Select Board met.", "La Junta de Selectos se reunió.", terms=()) == "ok"
    assert check("The Select Board met.", "La Junta de Selectos se reunió.",
                 terms=[{**SELECT_BOARD[0], "bodies": ["Retirement Board"]}]) == "ok"


def test_the_translator_and_its_reviewer_are_given_the_glossary(tmp_path):
    config = spanish_town(tmp_path)
    config["terms"] = {"Select Board": SELECT_BOARD[0]["languages"], "M.G.L.": "Massachusetts General Laws."}
    client = FakeAnthropic()
    summarize.run(config, client, tmp_path, limit=3, now=FETCHED_AT)
    call = translation_calls(client)[0]
    assert "Civic terms." in system(call) and "- Select Board = Junta de Gobierno (Select Board)" in system(call)
    # A term given only by its English definition isn't a term for the translator.
    assert "M.G.L." not in system(call)
    reviews = [c for c in client.calls if "problems" in c["output_config"]["format"]["schema"]["properties"]]
    assert "- Select Board = Junta de Gobierno (Select Board)" in system(reviews[0])
    record = next(iter(translations(tmp_path).values()))
    assert record["glossary"] == translate.glossary_key(translate.glossary.entries(config), "es")
    # A town without terms gets the prompt as it was.
    assert translate.glossary_text([], "es") == ""


def test_a_glossary_change_makes_again_only_the_translations_it_fails(tmp_path):
    """The stand-in's translations keep the English, so a term written the same in both passes, and one
    given another wording fails: the translations whose English uses it are made again, with tries of
    their own, and the rest are kept."""
    config = spanish_town(tmp_path)
    term = {"en": {"definition": "A drawing of a project."}, "es": {"term": "site plan", "definition": "Un plano."}}
    config["terms"] = {"site plan": term}
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    before = translations(tmp_path)
    uses = {sha for sha, r in before.items() if "site plan" in json.dumps(r)}
    assert uses and all(r["check"] == "ok" for r in before.values())
    config["terms"] = {"site plan": {**term, "es": {"term": "plano del sitio", "definition": "Un plano."}}}
    again = FakeAnthropic()
    summarize.run(config, again, tmp_path, limit=50, now=FETCHED_AT)
    after = translations(tmp_path)
    for sha in uses:
        # Made again as a correction, its tries counted from the new glossary's first.
        assert after[sha]["generated_at"] and after[sha]["check"].endswith('"site plan" isn\'t written "plano del sitio"')
        assert after[sha]["glossary"] != before[sha]["glossary"] and after[sha]["attempts"] in (1, 2)
    assert all(after[sha] == before[sha] for sha in before if sha not in uses)
    assert any("plano del sitio" in c["messages"][0]["content"] for c in translation_calls(again))
    # Once each has had its tries with this glossary, it's left (and the English shown).
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    assert all(translations(tmp_path)[sha]["attempts"] == 2 for sha in uses)
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["translated"] == 0
