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
