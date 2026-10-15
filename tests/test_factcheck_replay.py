"""The fact check replayed on real summaries and their documents' text (pipeline/evaluate_factcheck.py).

Each summary of evals/factcheck/ is checked again and compared with what was last accepted. A change
to the check that shows or hides anything fails here, saying what and why, until it's accepted with
python -m pipeline.evaluate_factcheck --update.
"""

import copy
import functools
import json

import pytest

from pipeline import evaluate_factcheck


@functools.lru_cache(maxsize=1)
def sample() -> dict:
    return evaluate_factcheck.load()


@functools.lru_cache(maxsize=1)
def expected() -> dict:
    return json.loads(evaluate_factcheck.EXPECTED.read_text(encoding="utf-8"))


def test_the_sample_and_its_expected_results_match():
    keys = [evaluate_factcheck.key(item) for item in sample()["items"]]
    assert len(keys) == len(set(keys)) and set(keys) == set(expected())
    # What it's for: every kind of summary, and the check's every result.
    results = {r["result"] for r in expected().values()}
    assert {"ok", "failed", "weak"} <= results
    assert {item["kind"] for item in sample()["items"]} == {"agenda", "minutes"}
    assert any(item["record"].get("transcript_source") == "ai" for item in sample()["items"])
    assert sample()["words"]


@pytest.mark.parametrize("name", sorted(json.loads(evaluate_factcheck.EXPECTED.read_text(encoding="utf-8"))))
def test_the_check_is_as_accepted(name):
    item = next(i for i in sample()["items"] if evaluate_factcheck.key(i) == name)
    actual = evaluate_factcheck.replay(item, frozenset(sample()["words"]))
    changed = evaluate_factcheck.differences(item, expected()[name], actual)
    assert not changed, ("The fact check changed what the site shows. If that's intended, accept it with "
                         "python -m pipeline.evaluate_factcheck --update\n" + "\n".join(changed))


def test_a_change_says_what_is_now_shown_or_hidden_and_why():
    # Waltham's City Council: a decision whose outcome the minutes don't bear out.
    item = next(i for i in sample()["items"] if i["town"] == "waltham-ma" and i["sha256"].startswith("04167a80ee"))
    actual = evaluate_factcheck.replay(item, frozenset(sample()["words"]))
    hidden = next(h for h in actual["hidden"] if h.startswith("decisions ")
                  and all(" outcome " in p for p in evaluate_factcheck.why(actual, h)))
    # As if the check had shown the decision before.
    before = copy.deepcopy(actual)
    before["hidden"].remove(hidden)
    before["problems"] = [p for p in before["problems"] if not p.startswith(hidden + ":")]
    lines = evaluate_factcheck.differences(item, before, actual)
    assert lines[0].startswith("waltham-ma 04167a80ee8c, minutes of ")
    assert any(line.startswith(f"  {hidden}: shown -> hidden: ") for line in lines)
    because = [line for line in lines if line.startswith("    because: ")]
    assert because and all(line.startswith(f"    because: {hidden}: outcome ") for line in because)
    assert not any(line.startswith("  problem found: ") for line in lines)
    # And the other way round.
    lines = evaluate_factcheck.differences(item, actual, before)
    assert any(line.startswith(f"  {hidden}: hidden -> shown: ") for line in lines)
    assert any(line.startswith("    no longer: " + hidden) for line in lines)
    assert evaluate_factcheck.differences(item, actual, copy.deepcopy(actual)) == []
