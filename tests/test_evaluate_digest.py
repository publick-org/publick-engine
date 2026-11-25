"""The digest subject prompt's set (pipeline/evaluate_digest.py), run with a fake model."""

import json

from test_digest_subject import FakeClient

from pipeline import evaluate_digest

ISSUE = {"name": "town 2026-11-22", "town": "Gloucester", "send": "Sunday, November 22, at 7:00 AM", "good": ["A1"],
         "candidates": [{"id": "A1", "kind": "ahead", "board": "City Council", "day": "Tuesday", "date": "2026-11-24",
                         "time": "18:30", "main_board": True, "hearing": True,
                         "line": "Public hearing on FY27 tax rates.", "items": []},
                        {"id": "D1", "kind": "decided", "board": "School Committee", "day": "Wednesday",
                         "date": "2026-11-18", "main_board": True, "source": "minutes", "line": "",
                         "decisions": ["approved: $25,000 Hate Crime Prevention Grant"]}]}
PRICES = {"input_price": 1.0, "output_price": 5.0}


def test_a_good_answer_passes():
    client = FakeClient({"pick": "A1", "subject": "Council hearing Tue on FY27 tax rates",
                         "preheader": "Last week: School Committee approved $25,000 grant"})
    result = evaluate_digest.evaluate(ISSUE, client, "claude-haiku-5-5", PRICES)
    assert (result["good"], result["used"], result["problems"]) == (True, True, [])
    text, failed = evaluate_digest.report([result], {"model": "claude-haiku-5-5"})
    assert not failed and "1 of 1 answers would be used" in text and "| A1 (good) |" in text


def test_a_made_up_figure_or_a_poor_pick_fails_the_set():
    made_up = evaluate_digest.evaluate(ISSUE, FakeClient({"pick": "A1", "subject": "Council hearing Tue on FY27 tax hike of 4%",
                                                          "preheader": ""}), "m", PRICES)
    assert not made_up["used"] and made_up["problems"] == ["its subject's '4' isn't in what the site says of A1"]
    poor = evaluate_digest.evaluate(ISSUE, FakeClient({"pick": "D1", "subject": "School Committee approved $25,000 grant",
                                                       "preheader": ""}), "m", PRICES)
    assert poor["used"] and not poor["good"]
    text, failed = evaluate_digest.report([made_up, poor], {"model": "m"})
    assert failed and "(expected A1)" in text


def test_the_set_is_as_the_prompt_takes_it():
    """Each issue of the set has candidates as digest.candidates() gives them, and good picks among them."""
    issues = json.loads(evaluate_digest.SET.read_text())["issues"]
    assert len(issues) >= 10
    for issue in issues:
        ids = {c["id"] for c in issue["candidates"]}
        assert issue["good"] and set(issue["good"]) <= ids, issue["name"]
        assert all(c["kind"] in ("ahead", "decided") and c["line"] is not None for c in issue["candidates"])
