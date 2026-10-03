"""The minutes prompt's test set and its report (pipeline/evaluate.py), without the model."""

import json
import re

from test_factcheck import COUNCIL, RIGHT

from pipeline import evaluate, factcheck


def test_the_set_is_well_formed():
    documents = json.loads(evaluate.SET.read_text())["documents"]
    assert len(documents) >= 15 and len({d["name"] for d in documents}) == len(documents)
    for d in documents:
        assert d["url"].startswith("https://files.publick.org/") and re.fullmatch(r"[0-9a-f]{64}", d["sha256"])
        assert d["decisions"]
        for e in d["decisions"]:
            re.compile(e["match"])
            re.compile(e.get("unless") or "")
            outcomes = e["outcome"] if isinstance(e["outcome"], list) else [e["outcome"]]
            assert set(outcomes) <= set(factcheck.OUTCOMES), e
            assert e["minutes"]
    # What the set is for: failed motions, and a scan.
    outcomes = [e["outcome"] for d in documents for e in d["decisions"]]
    assert outcomes.count("denied") >= 10 and any(d.get("scan") for d in documents)


def test_a_decision_written_down_is_right_wrong_or_missing():
    decisions = ["Failed a motion to table Order 200-26, 3-8.", "Approved Order 200-26 on an executive session, 7-4."]
    evidence = [{"outcome": "denied"}, {"outcome": "approved"}]
    row = evaluate.compare({"match": "tabl", "outcome": "denied"}, decisions, evidence)
    assert row["status"] == "right" and row["entries"] == [1]
    row = evaluate.compare({"match": "executive session", "unless": "tabl", "outcome": "approved"}, decisions, evidence)
    assert row["status"] == "right" and row["entries"] == [2]
    assert evaluate.compare({"match": "tabl", "outcome": "tabled"}, decisions, evidence)["status"] == "wrong"
    assert evaluate.compare({"match": "Moose", "outcome": "referred"}, decisions, evidence)["status"] == "missing"
    # Either of two outcomes, where the minutes allow both.
    assert evaluate.compare({"match": "executive", "unless": "tabl", "outcome": ["withdrawn", "approved"]},
                            decisions, evidence)["status"] == "right"


def test_errors_planted_in_decisions_that_passed_are_caught():
    record = {"kind": "minutes", "headline": "", "summary": "",
              "decisions": [text for text, _, _ in RIGHT],
              "decision_evidence": [{"outcome": outcome, "quote": quote} for _, outcome, quote in RIGHT]}
    passed = list(range(1, len(RIGHT) + 1))
    count, caught = evaluate.planted(record, [COUNCIL], passed)
    # Every approved and denied decision, turned round; referrals have no opposite.
    assert count == sum(o in ("approved", "denied") for _, o, _ in RIGHT) and caught == count
    assert evaluate.turned_round("Did not approve $8,000 for the roof, 4-7.", "denied")[1] == "approved"
    assert evaluate.turned_round("Referred it.", "referred") is None


def test_the_report_fails_only_on_a_wrong_outcome_shown():
    result = {"name": "example", "source": "pdf", "cost": 0.05, "decisions": ["Approved $8,000 for the roof."],
              "problems": [], "held_back": [], "planted": 1, "caught": 1,
              "expected": [{"match": "roof", "expected": ["denied"], "entries": [1], "given": ["approved"],
                            "status": "wrong", "held_back": []}]}
    text, shown_wrong = evaluate.report([result])
    assert shown_wrong and "**shown**" in text and "1 wrong (0 caught)" in text
    caught = {**result, "held_back": [1], "expected": [{**result["expected"][0], "held_back": [1]}]}
    assert evaluate.report([caught])[1] is False


def test_a_summary_cut_off_is_reported_and_the_set_goes_on():
    import hashlib

    from pipeline import summarize

    class CutOff:
        class messages:
            @staticmethod
            def stream(**kwargs):
                raise summarize.StoppedEarly("max_tokens", {"input_tokens": 1000, "output_tokens": 16000},
                                             '{"decisions": [{"decision": "Approved')

    pdf = b"%PDF-1.4 not really"
    document = {"name": "long", "url": "https://files.publick.org/x.pdf", "sha256": hashlib.sha256(pdf).hexdigest(),
                "title": "City Council", "date": "2026-03-16", "decisions": []}
    result = evaluate.evaluate(document, CutOff(), "claude-test", {"input_price": 2.0, "output_price": 10.0},
                               get=lambda url: pdf)
    assert result["stopped"] == "stopped early: max_tokens" and result["cost"] == 0.162
    text, failed = evaluate.report([result])
    assert failed and "cut off: 1" in text and "Approved" in text
