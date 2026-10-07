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
    prompts = [c["messages"][0]["content"] for c in client.calls if c["system"].startswith("You translate short summaries")]
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
