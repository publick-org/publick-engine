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
    assert {item["kind"] for item in sample()["items"]} == {"agenda", "minutes", "recording"}
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


def test_a_decision_shown_again_says_why():
    # Nashua's Budget Review Committee: decision 1's problems are only noted ("away"), in a summary that failed.
    item = next(i for i in sample()["items"] if i["town"] == "nashua-nh" and i["sha256"].startswith("07672028416c"))
    actual = evaluate_factcheck.replay(item, frozenset(sample()["words"]))
    assert actual["result"] == "failed" and "decisions 1" not in actual["hidden"]
    # As if it had been hidden before, with the same problems.
    before = {**copy.deepcopy(actual), "hidden": ["decisions 1", *actual["hidden"]]}
    lines = evaluate_factcheck.differences(item, before, actual)
    assert "  decisions 1: hidden -> shown: " in "\n".join(lines)
    assert "    its problems are still found, but are only noted (factcheck.NOTED)" in lines
    # Problems that aren't only noted, still found, aren't said to be.
    hard = next(h for h in actual["hidden"] if h.startswith("decisions "))
    before = {**copy.deepcopy(actual), "hidden": [h for h in actual["hidden"] if h != hard]}
    lines = evaluate_factcheck.differences(item, actual, before)
    assert "    its problems are still found" in lines


def test_add_takes_each_summary_as_a_town_and_its_sha256s_start(capsys):
    with pytest.raises(SystemExit):
        evaluate_factcheck.main(["add", "network", "lawrence-ma"])
    assert "lawrence-ma:cb192cd509" in capsys.readouterr().err


def test_a_new_sample_keeps_only_the_hard_cases_recordings():
    def candidate(town, kind, sha):
        return {"town": town, "kind": kind, "platform": "calendar", "date": "2026-11-02", "sha256": sha, "bytes": 1000,
                "record": {"decisions": ["a", "b", "c"], "fact_check": {"result": "ok", "source": kind}}}
    town, start = evaluate_factcheck.HARD_CASES[-1]
    hard = candidate(town, "recording", start + "0" * (64 - len(start)))
    other = candidate("salem-ma", "recording", "f" * 64)
    chosen = evaluate_factcheck.choose([hard, other, candidate("salem-ma", "minutes", "e" * 64)])
    assert hard in chosen and other not in chosen
