"""The minutes prompt as a batch, by effort (pipeline/evaluate_batch.py), with a stand-in for the Batches API."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from test_factcheck import COUNCIL, RIGHT

from pipeline import evaluate, evaluate_batch, factcheck

DOCUMENTS = [
    {"name": "council", "title": "City Council", "date": "2026-04-07",
     "decisions": [{"match": "table", "outcome": "denied"}, {"match": "roof", "outcome": "denied"}]},
    {"name": "board", "title": "Zoning Board", "date": "2026-04-08", "decisions": [{"match": "storage", "outcome": "referred"}]},
]
PDFS = [b"%PDF council", b"%PDF board"]
SENT = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


def minutes(decisions):
    return {"headline": "The council met.", "summary": "The council met.", "is_minutes": True,
            "decisions": [{"decision": text, "outcome": outcome, "quote": quote} for text, outcome, quote in decisions]}


def message(payload, stop_reason="end_turn", output_tokens=400):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(payload))],
                           usage=SimpleNamespace(input_tokens=10_000, output_tokens=output_tokens))


class Batches:
    """Each effort's batch ends after its number of polls (None: never, until cancelled), with the results given."""

    def __init__(self, polls: dict, results: dict):
        self.polls, self.outcomes = polls, results
        self.sent, self.cancelled, self.retrieved = [], [], 0

    def create(self, requests):
        effort = requests[0]["params"]["output_config"]["effort"]
        self.sent.append((effort, requests))
        return SimpleNamespace(id=f"batch-{effort}")

    def retrieve(self, batch_id):
        self.retrieved += 1
        effort = batch_id.removeprefix("batch-")
        polls = self.polls[effort]
        ended = batch_id in self.cancelled or (polls is not None and self.retrieved >= polls)
        return SimpleNamespace(processing_status="ended" if ended else "in_progress", created_at=SENT,
                               ended_at=SENT + timedelta(minutes=4, seconds=5))

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)

    def results(self, batch_id):
        effort = batch_id.removeprefix("batch-")
        # In any order, as the API gives them.
        return reversed([SimpleNamespace(custom_id=cid, result=r) for cid, r in self.outcomes[effort].items()])


def client(polls, results):
    return SimpleNamespace(messages=SimpleNamespace(batches=Batches(polls, results)))


def succeeded(payload, **kwargs):
    return SimpleNamespace(type="succeeded", message=message(payload, **kwargs))


@pytest.fixture(autouse=True)
def council_text(monkeypatch):
    monkeypatch.setattr(factcheck, "pages", lambda pdf: [COUNCIL])


def test_each_effort_is_one_batch_of_the_documents_as_a_run_sends_them():
    fake = client({"low": 1}, {"low": {}})
    evaluate_batch.send_and_wait(fake, DOCUMENTS, PDFS, "claude-sonnet-5", ["low"], 600, sleep=lambda s: None)
    (effort, requests), = fake.messages.batches.sent
    assert effort == "low" and [r["custom_id"] for r in requests] == ["doc-0", "doc-1"]
    params = requests[1]["params"]
    assert params["model"] == "claude-sonnet-5" and params["output_config"]["effort"] == "low"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["messages"][0]["content"][0]["type"] == "document"
    assert "Zoning Board" in params["messages"][0]["content"][1]["text"]


def test_results_are_matched_by_id_checked_and_priced_at_half():
    right = minutes([RIGHT[0], RIGHT[3], RIGHT[4]])
    # The roof's outcome turned round: wrong, and held back by the check, so not shown.
    wrong_roof = minutes([RIGHT[0], ("Approved $8,000 for the Senior Center roof (203-26).", "approved", RIGHT[3][2])])
    fake = client({"high": 2, "low": 3}, {
        "high": {"doc-0": succeeded(right), "doc-1": succeeded(right)},
        "low": {"doc-0": succeeded(wrong_roof, output_tokens=100),
                "doc-1": SimpleNamespace(type="errored", error=SimpleNamespace(error=SimpleNamespace(message="overloaded")))},
    })
    batches = evaluate_batch.send_and_wait(fake, DOCUMENTS, PDFS, "claude-sonnet-5", ["high", "low"], 600,
                                           sleep=lambda s: None)
    assert batches["high"]["seconds"] == 245 and not batches["high"]["cancelled"]
    prices = {"input_price": 2.0, "output_price": 10.0}
    runs = evaluate_batch.collect(fake, DOCUMENTS, PDFS, batches, prices)
    high, low = (evaluate_batch.totals(r["results"]) for r in runs)
    assert high["right"] == 3 and high["wrong"] == high["missing"] == 0
    assert low["right"] == 1 and low["wrong"] == 1 and low["shown"] == 0 and low["held"] == 1 and low["failed"] == 1
    # 10,000 tokens in and 400 out at $2 and $10 a million is $0.024; at the batch price, $0.012.
    assert runs[0]["results"][0]["cost"] == 0.012
    text = evaluate_batch.report(runs, DOCUMENTS, "claude-sonnet-5", 600)
    assert "| high | 4 min 05 s | 3 | 0 (0) | 0 |" in text and "board: errored: overloaded" in text


def test_a_cut_off_summary_is_counted_and_paid_for():
    fake = client({"low": 1}, {"low": {"doc-0": succeeded(minutes(RIGHT[:1]), stop_reason="max_tokens"),
                                       "doc-1": succeeded(minutes(RIGHT[4:5]))}})
    batches = evaluate_batch.send_and_wait(fake, DOCUMENTS, PDFS, "claude-sonnet-5", ["low"], 600, sleep=lambda s: None)
    t = evaluate_batch.totals(evaluate_batch.collect(fake, DOCUMENTS, PDFS, batches, evaluate.DEFAULT_PRICES)[0]["results"])
    assert t["stopped"] == 1 and t["cost"] > 0


def test_a_batch_not_ended_in_time_is_cancelled_and_what_it_finished_counted():
    clock = iter(range(0, 10_000, 60))
    fake = client({"low": None}, {"low": {"doc-0": succeeded(minutes(RIGHT[:1])),
                                          "doc-1": SimpleNamespace(type="canceled")}})
    batches = evaluate_batch.send_and_wait(fake, DOCUMENTS, PDFS, "claude-sonnet-5", ["low"], 300,
                                           clock=lambda: next(clock), sleep=lambda s: None)
    assert fake.messages.batches.cancelled == ["batch-low"]
    assert batches["low"] == {"id": "batch-low", "seconds": None, "cancelled": True}
    runs = evaluate_batch.collect(fake, DOCUMENTS, PDFS, batches, evaluate.DEFAULT_PRICES)
    t = evaluate_batch.totals(runs[0]["results"])
    assert t["failed"] == 1 and t["cost"] > 0
    assert "not ended after 5 min (cancelled)" in evaluate_batch.report(runs, DOCUMENTS, "claude-sonnet-5", 300)


def test_efforts_are_checked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    for efforts in ("", "low,low", "low,huge"):
        with pytest.raises(SystemExit):
            evaluate_batch.main(["--efforts", efforts])
