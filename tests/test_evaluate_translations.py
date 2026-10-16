"""The translation prompt's evaluation (pipeline/evaluate_translations.py), against the fake model."""

from fakes import FakeAnthropic
from pipeline import evaluate_translations, translate

SET = {
    "town": "t-ma",
    "config": {"strings": {"es": {"City Council": "Concejo Municipal"}},
               "summaries": {"model": "claude-sonnet-5", "input_price": 2.0, "output_price": 10.0}},
    "drafts": {},
    "words": ["wastewater"],
    "documents": [
        {"name": "2026-09-22 City Council minutes", "kind": "minutes", "title": "City Council", "date": "2026-09-22",
         "body": "City Council", "sha256": "a" * 64,
         "english": {"headline": "Approved 3 permits", "summary": "The council met.", "decisions": ["Approved 3 permits, 5-0"]}},
        {"name": "2026-10-13 City Council agenda", "kind": "agenda", "title": "City Council", "date": "2026-10-13",
         "body": "City Council", "sha256": "b" * 64,
         "english": {"headline": "Wastewater Plan", "summary": "The council will meet.", "items": ["Wastewater Plan"]}},
    ],
}


def test_each_summary_is_translated_checked_and_reviewed_as_a_run_does():
    client = FakeAnthropic()
    results = evaluate_translations.evaluate(SET, client)
    assert [r["passed"] for r in results] == [True, True]
    assert all(len(r["tries"]) == 1 and r["cost"] > 0 for r in results)
    # The meeting's board is given in Spanish, and the review gets the translator's rules.
    prompts = [c["messages"][0]["content"] for c in client.calls if c["system"][0]["text"].startswith("You translate short summaries")]
    assert all('in Spanish, "Concejo Municipal"' in p for p in prompts)
    text = evaluate_translations.report(results)
    assert "Passed the first time: 2 of 2 (100%)" in text


def test_a_failure_is_corrected_once_and_reported():
    results = evaluate_translations.evaluate(SET, FakeAnthropic(translation_drops_numbers=True))
    first = results[0]
    assert not first["passed"] and len(first["tries"]) == translate.ATTEMPTS
    assert first["tries"][0]["check"].endswith("not in the translation")
    text = evaluate_translations.report(results)
    assert "**2026-09-22 City Council minutes** (check)" in text
    assert "Passed after the correction: 1 of 2 (50%)" in text


def test_only_one_summary():
    assert [r["name"] for r in evaluate_translations.evaluate(SET, FakeAnthropic(), "2026-10-13 City Council agenda")] \
        == ["2026-10-13 City Council agenda"]


def options(**given):
    from types import SimpleNamespace
    names = ("model", "effort", "input_price", "output_price", "review_model", "review_effort", "review_input_price",
             "review_output_price")
    return SimpleNamespace(**{name: given.get(name) for name in names})


def is_review(call):
    return "problems" in call["output_config"]["format"]["schema"]["properties"]


def test_by_default_the_models_are_the_towns():
    run = evaluate_translations.runs_with(SET, options())
    assert run["translation"] == {"model": "claude-sonnet-5-5", "effort": "low",
                                  "prices": {"input_price": 2.0, "output_price": 10.0}}
    assert run["review"] == {"model": "claude-sonnet-5-5", "effort": "low",
                             "prices": {"input_price": 2.0, "output_price": 10.0}}
    client = FakeAnthropic()
    evaluate_translations.evaluate(SET, client, None, run["summaries"], run["translation"]["prices"], run["review"]["prices"])
    assert {(c["model"], c["output_config"]["effort"]) for c in client.calls} == {("claude-sonnet-5-5", "low")}


def test_another_translator_and_reviewer_each_with_its_effort():
    run = evaluate_translations.runs_with(SET, options(model="claude-haiku-5-5", review_effort="medium"))
    # A model with no effort given has none sent: the model's own default.
    assert run["translation"]["model"] == "claude-haiku-5-5" and run["translation"]["effort"] is None
    assert run["translation"]["prices"]["input_price"] == 0.1 and run["review"]["effort"] == "medium"
    client = FakeAnthropic()
    results = evaluate_translations.evaluate(SET, client, None, run["summaries"], run["translation"]["prices"],
                                             run["review"]["prices"])
    translations = [c for c in client.calls if not is_review(c)]
    reviews = [c for c in client.calls if is_review(c)]
    assert len(translations) == len(reviews) == 2
    assert all(c["model"] == "claude-haiku-5-5" and "effort" not in c["output_config"] for c in translations)
    assert all(c["model"] == "claude-sonnet-5-5" and c["output_config"]["effort"] == "medium" for c in reviews)
    # Each summary's tokens and cost, by role, each at its model's prices.
    first = results[0]
    assert first["outcome"] == "first_try"
    assert first["spent"]["translation"]["input_tokens"] == 600 and first["spent"]["review"]["input_tokens"] == 500
    assert first["spent"]["translation"]["cost"] == round((600 * 0.1 + 300 * 0.5) / 1e6, 6)
    assert first["spent"]["review"]["cost"] == round((500 * 2 + 50 * 10) / 1e6, 6)
    text = evaluate_translations.report(results, run)
    assert "Translated by claude-haiku-5-5, effort: none sent (the model default)" in text
    assert "Reviewed by claude-sonnet-5-5, effort: medium" in text
    assert "- Translations: 2 requests, 1,200 input" in text and "a summary)" in text
    totals = evaluate_translations.totals(results)
    assert totals["first_try"] == totals["passed"] == 2 and totals["review"]["requests"] == 2
    # The same model for both: told apart by what each request is for.
    run = evaluate_translations.runs_with(SET, options(model="claude-haiku-5-5", effort="low", review_model="claude-haiku-5-5"))
    assert run["summaries"]["translation_review_model"] == "claude-haiku-5-5"
    assert run["summaries"]["translation_review_input_price"] == 0.1
    results = evaluate_translations.evaluate(SET, FakeAnthropic(), None, run["summaries"], run["translation"]["prices"],
                                             run["review"]["prices"])
    assert results[0]["spent"]["review"]["requests"] == 1 and results[0]["spent"]["translation"]["requests"] == 1


def test_each_outcome_is_counted_and_a_response_cut_off_doesnt_stop_the_set():
    results = evaluate_translations.evaluate(SET, FakeAnthropic(translation_drops_numbers=True))
    assert [r["outcome"] for r in results] == ["failed_check", "first_try"]
    results = evaluate_translations.evaluate(SET, FakeAnthropic(review_problems=[{"id": "headline", "problem": "wrong"}]))
    assert [r["outcome"] for r in results] == ["failed_review", "failed_review"]
    results = evaluate_translations.evaluate(SET, FakeAnthropic(stop_reason="max_tokens"))
    assert [r["outcome"] for r in results] == ["stopped", "stopped"]
    assert results[0]["tries"] == [{"stopped": "stopped early: max_tokens"}] * translate.ATTEMPTS
    assert results[0]["cost"] > 0
    text = evaluate_translations.report(results)
    assert "0 failed the check, 0 failed the review, 2 cut off or refused" in text
    assert "**2026-09-22 City Council minutes** (stopped): stopped early: max_tokens" in text


def test_a_review_model_s_effort_is_the_towns_or_low():
    assert translate.review_settings({})["effort"] == "low"
    assert translate.review_settings({"summaries": {"translation_review_model": "claude-haiku-5-5"}})["effort"] == "low"
    assert translate.review_settings({"summaries": {"translation_review_effort": "high"}})["effort"] == "high"


# A set of two towns in two states, each with its glossary, and the gold terms of its summaries.
VARIANCE = {"en": {"definition": "Permission to build in a way the zoning doesn't allow."},
            "es": {"term": "variación", "definition": "Permiso para construir de una manera que la zonificación no permite."}}
TOWNS_SET = {
    "thresholds": {"passed": 0.75, "gold_terms": 0.95, "stopped": 0},
    "summaries": {"model": "claude-haiku-5-5", "translation_model": "claude-haiku-5-5", "translation_effort": "high",
                  "translation_input_price": 0.1, "translation_output_price": 0.5},
    "words": [],
    "towns": {
        "a-ma": {"strings": {"es": {"Zoning Board of Appeals": "Junta de Apelaciones de Zonificación"}}, "glossary": [],
                 "terms": {"variance": VARIANCE}},
        "b-nh": {"strings": {"es": {}}, "glossary": [], "terms": {}},
    },
    "documents": [
        {"town": "a-ma", "name": "a-ma 2026-09-22 Zoning Board of Appeals minutes", "kind": "minutes",
         "title": "Zoning Board of Appeals", "date": "2026-09-22", "body": "Zoning Board of Appeals", "sha256": "c" * 64,
         "english": {"headline": "Granted a variance", "summary": "The board granted a variance.",
                     "decisions": ["Granted a variance for 12 Main St, 5-0"]},
         "gold": [{"where": "headline", "term": "variance", "es": "variación"},
                  {"where": "summary", "term": "variance", "es": "variación"},
                  {"where": "decisions 1", "term": "variance", "es": "variación"}]},
        {"town": "b-nh", "name": "b-nh 2026-10-13 Planning Board agenda", "kind": "agenda", "title": "Planning Board",
         "date": "2026-10-13", "body": "Planning Board", "sha256": "d" * 64,
         "english": {"headline": "Two site plans", "summary": "The board will meet.", "items": ["Site plan for 4 Elm St"]},
         "gold": []},
    ],
}


def test_a_set_of_several_towns_each_with_its_glossary_and_board_names():
    client = FakeAnthropic()
    results = evaluate_translations.evaluate(TOWNS_SET, client)
    assert [r["town"] for r in results] == ["a-ma", "b-nh"]
    systems = [c["system"][0]["text"] for c in client.calls if c["system"][0]["text"].startswith("You translate short summaries")]
    # Each town's translator is given its own glossary: one town's terms aren't another's.
    # (a-ma's summary is translated twice, the second time as a correction; b-nh's once, without it.)
    assert [("- variance = variación" in s) for s in systems] == [True, True, False]
    prompts = [c["messages"][0]["content"] for c in client.calls if c["system"][0]["text"].startswith("You translate short")]
    assert any('in Spanish, "Junta de Apelaciones de Zonificación"' in p for p in prompts)
    # The stand-in keeps the English, so it writes "variance": the glossary's check fails it, and the gold terms are lost.
    first = results[0]
    assert not first["passed"] and first["tries"][0]["check"] == 'headline: "variance" isn\'t written "variación"'
    assert [g["kept"] for g in first["gold"]] == [False, False, False]
    totals = evaluate_translations.totals(results)
    assert totals["gold_terms"] == 3 and totals["gold_terms_kept"] == 0
    assert totals["by_town"] == {"a-ma": {"summaries": 1, "passed": 0}, "b-nh": {"summaries": 1, "passed": 1}}
    text = evaluate_translations.report(results, thresholds=TOWNS_SET["thresholds"])
    assert "- Gold terms kept: 0 of 3 (0%)" in text and "- Passed by town: a-ma 0 of 1; b-nh 1 of 1" in text
    assert "- Thresholds: **missed**: 1 of 2 passed, under 75%; 0 of 3 gold terms kept, under 95%" in text
    assert '- **a-ma 2026-09-22 Zoning Board of Appeals minutes**, headline: "variance" should be "variación"' in text


def test_gold_terms_are_kept_when_the_translation_writes_them():
    terms = translate.glossary.entries(TOWNS_SET["towns"]["a-ma"])
    gold = TOWNS_SET["documents"][0]["gold"]
    translation = {"headline": "Otorgó una variación", "summary": "La junta otorgó variaciones.",
                   "decisions": ["Otorgó una varianza para 12 Main St, 5-0"]}
    assert [g["kept"] for g in evaluate_translations.kept_gold(gold, translation, terms)] == [True, True, False]
    assert [g["kept"] for g in evaluate_translations.kept_gold(gold, None, terms)] == [False, False, False]


def test_the_thresholds():
    t = {"summaries": 4, "passed": 3, "gold_terms": 20, "gold_terms_kept": 19, "stopped": 0}
    assert evaluate_translations.missed(t, {"passed": 0.75, "gold_terms": 0.95, "stopped": 0}) == []
    assert evaluate_translations.missed({**t, "passed": 2}, {"passed": 0.75}) == ["2 of 4 passed, under 75%"]
    assert evaluate_translations.missed({**t, "stopped": 1}, {"stopped": 0}) == ["1 cut off or refused, over 0"]
    # No gold terms: nothing to miss.
    assert evaluate_translations.missed({**t, "gold_terms": 0, "gold_terms_kept": 0}, {"gold_terms": 0.95}) == []


def run_main(monkeypatch, tmp_path, the_set, *argv):
    import anthropic
    path = tmp_path / "translations.json"
    path.write_text(__import__("json").dumps(the_set))
    monkeypatch.setattr(evaluate_translations, "SET", path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", lambda: FakeAnthropic())
    return evaluate_translations.main(list(argv))


def test_main_fails_when_a_threshold_is_missed(monkeypatch, tmp_path, capsys):
    assert run_main(monkeypatch, tmp_path, TOWNS_SET) == 1
    assert "Thresholds: **missed**" in capsys.readouterr().out
    # The same set without the term the stand-in can't write passes.
    passing = {**TOWNS_SET, "towns": {**TOWNS_SET["towns"], "a-ma": {**TOWNS_SET["towns"]["a-ma"], "terms": {}}},
               "documents": [{**d, "gold": []} for d in TOWNS_SET["documents"]]}
    assert run_main(monkeypatch, tmp_path, passing) == 0
    # One summary alone is a look, not a verdict.
    assert run_main(monkeypatch, tmp_path, TOWNS_SET, "--only", TOWNS_SET["documents"][0]["name"]) == 0


def test_the_set_in_the_engine_covers_several_towns_and_states_with_gold_terms():
    import json
    the_set = json.loads(evaluate_translations.SET.read_text(encoding="utf-8"))
    towns = {d["town"] for d in the_set["documents"]}
    assert len(towns) >= 6 and len({t.rsplit("-", 1)[1] for t in towns}) >= 5
    assert sum(len(d["gold"]) for d in the_set["documents"]) >= 100
    assert set(the_set["thresholds"]) == {"passed", "gold_terms", "stopped"}
    # Each gold term is one the town's glossary gives, with its Spanish.
    for d in the_set["documents"]:
        terms = {translate.glossary.english(e): e for e in translate.glossary.entries(the_set["towns"][d["town"]])}
        for g in d["gold"]:
            assert translate.glossary.wording(terms[g["term"]], "es") == g["es"]
