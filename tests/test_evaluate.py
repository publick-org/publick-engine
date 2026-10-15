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


def test_a_model_with_list_prices_needs_none_and_another_needs_them():
    import pytest
    assert evaluate.prices_for("claude-sonnet-5") == evaluate.DEFAULT_PRICES
    haiku = evaluate.prices_for("claude-haiku-5-5")
    assert haiku["input_price"] == 0.1 and haiku["long_prompt_tokens"] == 100_000 and haiku["long_input_price"] == 0.5
    # Prices given win, and the long-prompt prices stay.
    assert evaluate.prices_for("claude-haiku-5-5", 0.2)["input_price"] == 0.2
    assert evaluate.prices_for("claude-haiku-5-5", 0.2)["long_output_price"] == 2.5
    assert evaluate.prices_for("claude-test", 1.0, 5.0) == {"input_price": 1.0, "output_price": 5.0}
    with pytest.raises(SystemExit, match="give --input-price"):
        evaluate.prices_for("claude-test")


def test_a_long_prompt_is_priced_at_its_own_price_request_by_request():
    import pytest

    from pipeline import summarize
    haiku = evaluate.prices_for("claude-haiku-5-5")
    short = {"input_tokens": 100_000, "output_tokens": 10_000}
    long = {"input_tokens": 100_001, "output_tokens": 10_000}
    assert summarize.cost(short, haiku) == pytest.approx((100_000 * 0.1 + 10_000 * 0.5) / 1e6)
    assert summarize.cost(long, haiku) == pytest.approx((100_001 * 0.5 + 10_000 * 2.5) / 1e6)
    # Cached input counts toward the prompt's length, at its share of the long price.
    cached = {"input_tokens": 1_000, "cache_read_input_tokens": 100_000, "output_tokens": 0}
    assert summarize.cost(cached, haiku) == pytest.approx((1_000 + 0.1 * 100_000) * 0.5 / 1e6)
    # Without long-prompt prices (a town's [summaries]), every request is priced the same.
    assert summarize.cost(long, evaluate.DEFAULT_PRICES) == pytest.approx((100_001 * 2 + 10_000 * 10) / 1e6)
    # The batch price halves every price, not the length.
    batch = summarize.at_batch_price(haiku)
    assert batch["long_prompt_tokens"] == 100_000 and batch["long_input_price"] == 0.25 and batch["input_price"] == 0.05
    assert summarize.at_batch_price(evaluate.DEFAULT_PRICES) == {"input_price": 1.0, "output_price": 5.0}
    # Requests priced one by one: a transcription's short requests stay at the short price.
    requests = [{"usage": long, "seconds": 30.0}, {"usage": short, "seconds": 3.0}]
    used = evaluate.spent(requests, haiku)
    assert used["long_prompts"] == 1 and used["requests"] == 2 and used["seconds"] == 33.0
    assert used["cost"] == pytest.approx(summarize.cost(long, haiku) + summarize.cost(short, haiku), abs=1e-6)


def test_the_recorder_keeps_each_request_and_passes_everything_on():
    from fakes import FakeAnthropic
    ticks = iter([0.0, 2.5, 10.0, 11.0])
    client = FakeAnthropic()
    recorder = evaluate.Recorder(client, clock=lambda: next(ticks), purpose=lambda kwargs: kwargs["model"][-1])
    schema = {"type": "json_schema", "schema": {"properties": {"transcript": {}}}}
    with recorder.messages.stream(model="claude-a", output_config={"effort": "low", "format": schema}) as stream:
        stream.get_final_message()
    recorder.messages.create(model="claude-b", messages=[{"role": "user", "content": "\n{}"}],
                             output_config={"format": {"schema": {"properties": {"problems": {}}}}})
    assert [(r["model"], r["effort"], r["seconds"], r["purpose"]) for r in recorder.requests] == [
        ("claude-a", "low", 2.5, "a"), ("claude-b", None, 1.0, "b")]
    assert recorder.requests[0]["usage"] == {"input_tokens": 1500, "output_tokens": 400}
    assert recorder.requests[0]["stop_reason"] == "end_turn"
    assert recorder.messages.batches is client.messages.batches


def test_the_set_runs_with_another_model_at_an_effort_and_says_what_it_used(tmp_path):
    from fakes import FakeAnthropic

    from pipeline import summarize
    pdf = (__import__("pathlib").Path(__file__).parent / "fixtures" / "civicplus_agenda_scanned.pdf").read_bytes()
    import hashlib
    document = {"name": "scan", "url": "https://files.publick.org/x.pdf", "sha256": hashlib.sha256(pdf).hexdigest(),
                "title": "City Council", "date": "2026-03-16", "scan": True,
                "decisions": [{"match": "site plan", "outcome": "approved"}]}
    client = FakeAnthropic()
    prices = evaluate.prices_for("claude-haiku-5-5")
    result = evaluate.evaluate(document, client, "claude-haiku-5-5", prices, get=lambda url: pdf, effort="high")
    # The summary and the scan's transcription, each at the effort.
    assert [c["model"] for c in client.calls] == ["claude-haiku-5-5"] * 2
    assert [c["output_config"]["effort"] for c in client.calls] == ["high", "high"]
    assert result["spent"]["requests"] == 2 and result["summary"]["requests"] == 1 and result["transcript"]["requests"] == 1
    assert result["cost"] == round(2 * summarize.cost({"input_tokens": 1500, "output_tokens": 400}, prices), 6)
    assert result["pages"] == summarize.page_count(pdf)
    # Without an effort, none is sent: the model's default, as a run sends it.
    client = FakeAnthropic()
    evaluate.evaluate(document, client, "claude-sonnet-5", evaluate.DEFAULT_PRICES, get=lambda url: pdf)
    assert all("effort" not in c["output_config"] for c in client.calls)
    run = {"set": "minutes", "model": "claude-haiku-5-5", "effort": "high", "prices": prices}
    text, _ = evaluate.report([result], run)
    assert "Model: claude-haiku-5-5, effort: high; prices per million tokens: $0.1 input, $0.5 output ($0.5 and $2.5 " \
           "for a prompt over 100,000 tokens)." in text
    assert "2 requests, 3,000 input and 800 output tokens" in text and "(the transcription: $" in text
    totals = evaluate.totals_of([result])
    assert totals["documents"] == 1 and totals["right"] == 1 and totals["input_tokens"] == 3000
    assert totals["cost_per_document"] == round(result["cost"], 4)


def test_a_refusal_is_reported_with_its_category_and_the_set_goes_on():
    import hashlib
    from types import SimpleNamespace

    from fakes import FakeAnthropic
    client = FakeAnthropic(stop_reason="refusal")
    stream = client.messages.stream

    def refused(**kwargs):
        inner = stream(**kwargs)
        message = inner.get_final_message
        inner.get_final_message = lambda: SimpleNamespace(**{**vars(message()),
                                                             "stop_details": SimpleNamespace(category="cyber")})
        return inner
    client.messages.stream = refused
    pdf = b"%PDF-1.4 not really"
    document = {"name": "refused", "url": "https://files.publick.org/x.pdf", "sha256": hashlib.sha256(pdf).hexdigest(),
                "title": "City Council", "date": "2026-03-16", "decisions": []}
    result = evaluate.evaluate(document, client, "claude-haiku-5-5", evaluate.prices_for("claude-haiku-5-5"),
                               get=lambda url: pdf)
    assert result["stopped"] == "stopped early: refusal (cyber)" and result["spent"]["requests"] == 1
    text, failed = evaluate.report([result])
    assert failed and "refusal (cyber)" in text


def test_main_chooses_the_set_and_checks_its_prices_before_the_key(monkeypatch):
    import sys

    import pytest
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for argv, message in ((["--model", "claude-test"], "give --input-price"),
                          (["--model", "claude-haiku-5-5", "--effort", "low"], "Set ANTHROPIC_API_KEY"),
                          (["--set", "shadow", "--model", "claude-test"], "give --input-price"),
                          (["--set", "shadow", "--model", "claude-haiku-5-5", "--effort", "medium"], "Set ANTHROPIC_API_KEY"),
                          (["--set", "translations", "--model", "claude-haiku-5-5", "--review-model", "claude-haiku-5-5"],
                           "Set ANTHROPIC_API_KEY"),
                          (["--set", "translations", "--model", "claude-test"], "give --input-price"),
                          (["--set", "nothing"], "minutes, translations, batch, or shadow")):
        monkeypatch.setattr(sys, "argv", ["evaluate", *argv])
        with pytest.raises(SystemExit, match=message):
            evaluate.main()
