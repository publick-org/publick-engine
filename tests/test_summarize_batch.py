"""A daily run's summaries sent as one batch, at half the price, and collected at the end of the job
(pipeline/summarize.py), with a stand-in for the Message Batches API."""

import json
from datetime import datetime, timedelta, timezone

from conftest import FETCHED_AT
from fakes import FakeAnthropic
from test_summarize import REQUEST, blank_pdf, minutes_town, with_minutes
from test_translate import spanish_town, translations

from pipeline import summarize
from pipeline.config import load_config

SENT = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)


def at(*minutes):
    """A clock that reads SENT plus each of these minutes in turn, then stays at the last."""
    times = [SENT + timedelta(minutes=m) for m in minutes]
    return lambda: times.pop(0) if len(times) > 1 else times[0]


def first(config, client, tmp_path, **kwargs):
    """A daily run's first part: the town's summaries sent as one batch."""
    return summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT, batch=True, clock=at(0),
                         sleep=lambda s: None, **kwargs)


def collect(config, client, tmp_path, clock=None, **kwargs):
    """The end of the job: the batch collected, and what it didn't make sent one at a time."""
    return summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT, collecting=True,
                         clock=clock or at(5), sleep=lambda s: None, **kwargs)


def saved(tmp_path):
    return [json.loads(f.read_text()) for f in sorted((tmp_path / "summaries").glob("*.json"))]


def test_a_daily_run_sends_its_summaries_as_one_batch_and_saves_them_when_collected(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    result = first(config, client, tmp_path)
    # Sixteen documents in one batch, none sent alone, nothing saved yet: no transcription while they wait.
    assert result["queued"] == 16 and result["summarized"] == 0 and result["transcribed"] == 0
    assert len(client.batches_sent) == 1 and len(client.batches_sent[0]) == 16 and client.calls == []
    pending = json.loads((tmp_path / summarize.BATCH_FILE).read_text())
    assert pending["id"] == "batch-1" and pending["sent_at"] == SENT.isoformat()
    assert {d["doc"]["sha256"] for d in pending["documents"]} == {
        t[2]["sha256"] for t in summarize.pending_documents(tmp_path, FETCHED_AT.date().isoformat(),
                                                             config["summaries"]["model"])}
    # Each request is the one a run would send on its own.
    assert client.batches_sent[0][0]["output_config"]["format"]["type"] == "json_schema"

    result = collect(config, client, tmp_path)
    assert result["collected"] == 16 and result["summarized"] == 16 and result["remaining"] == 0
    assert not (tmp_path / summarize.BATCH_FILE).exists()
    records = saved(tmp_path)
    # At half the price, checked as they're saved, as one sent alone would be.
    assert len(records) == 16 and all(r["cost"] == REQUEST / 2 for r in records)
    assert all("fact_check" in r for r in records if r.get("is_minutes") is not False)
    ledger = json.loads((tmp_path / summarize.LEDGER).read_text())["2026-09"]
    assert ledger["cost"] == round(16 * REQUEST / 2, 4) and ledger["documents"] == 16
    # The scans are transcribed now that no summary is waiting, one request at a time.
    assert result["transcribed"] > 0 and len(client.calls) == result["transcribed"]


def test_the_batch_is_counted_against_the_runs_budget_at_half_the_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(summarize, "estimated_cost", lambda kind, pages, settings: REQUEST)
    config = with_minutes(tmp_path)
    # Room for five at half price, where two would fit one at a time.
    client = FakeAnthropic()
    result = first(config, client, tmp_path, allowance=REQUEST * 2.6)
    assert result["queued"] == 5 and result["estimated_cost"] == round(5 * REQUEST / 2, 2)
    assert "waiting for a run with room for them" in result["stopped"]
    # Collected, the run goes on from what it had spent: the rest still don't fit.
    result = collect(config, client, tmp_path, allowance=REQUEST * 2.6)
    assert result["collected"] == 5 and result["summarized"] == 5 and result["remaining"] == 11
    pending = summarize.pending_documents(tmp_path, FETCHED_AT.date().isoformat(), config["summaries"]["model"])
    assert len(pending) == 11


def test_a_batch_not_done_in_time_is_cancelled_and_what_it_didnt_make_is_sent_one_at_a_time(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic(batch_polls=None)
    first(config, client, tmp_path)
    # Five minutes after it was sent, then past BATCH_WAIT.
    result = collect(config, client, tmp_path, clock=at(5, 31))
    assert client.cancelled == ["batch-1"]
    assert any("cancelled" in e for e in result["errors"])
    # Every document made one at a time, at the full price, in the same run.
    assert result["collected"] == 0 and result["summarized"] == 16 and result["remaining"] == 0
    assert all(r["cost"] == REQUEST for r in saved(tmp_path))
    assert not (tmp_path / summarize.BATCH_FILE).exists()


def test_a_request_the_batch_didnt_make_is_sent_one_at_a_time(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic(batch_results={0: "errored", 3: "expired"})
    first(config, client, tmp_path)
    result = collect(config, client, tmp_path)
    assert result["collected"] == 14 and result["summarized"] == 16
    summary_calls = [c for c in client.calls if "transcript" not in c["output_config"]["format"]["schema"]["properties"]]
    assert len(summary_calls) == 2
    assert sorted(r["cost"] for r in saved(tmp_path)).count(REQUEST) == 2
    assert sum("not made in the batch (errored: overloaded)" in e for e in result["errors"]) == 1


def test_an_uncollected_batch_is_collected_by_the_next_run_then_paid_from_its_budget(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    first(config, client, tmp_path)
    # The job was stopped before it collected the batch. The next day's run collects it first, counts what
    # it cost against its own budget, and has nothing left to send.
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT + timedelta(days=1), batch=True,
                           clock=at(60 * 20), sleep=lambda s: None, allowance=1.0)
    assert result["collected"] == 16 and result["queued"] == 0 and len(client.batches_sent) == 1
    assert result["estimated_cost"] >= round(16 * REQUEST / 2, 2)
    assert not (tmp_path / summarize.BATCH_FILE).exists()


def test_a_batch_that_cant_be_collected_is_kept_and_its_documents_arent_sent_again(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    first(config, client, tmp_path)

    def unreachable(batch_id):
        raise ConnectionError("no route to the API")
    client.messages.batches.retrieve = unreachable
    result = collect(config, client, tmp_path)
    assert any("couldn't be collected" in e for e in result["errors"])
    assert result["summarized"] == 0 and client.calls == []
    assert (tmp_path / summarize.BATCH_FILE).exists()
    # A daily run while it's still out sends nothing new as a batch, and none of its documents alone.
    again = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT, batch=True, clock=at(10),
                          sleep=lambda s: None)
    assert again["queued"] == 0 and again["summarized"] == 0 and len(client.batches_sent) == 1


def test_collecting_with_no_batch_does_nothing(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    result = collect(config, client, tmp_path)
    assert result["summarized"] == 0 and client.calls == [] and not (tmp_path / "summaries").exists()


def test_a_batch_that_cant_be_sent_is_sent_one_at_a_time(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()

    def refused(requests):
        raise RuntimeError("batches are unavailable")
    client.messages.batches.create = refused
    result = first(config, client, tmp_path)
    assert result["queued"] == 0 and result["summarized"] == 16
    assert any("couldn't be sent" in e for e in result["errors"])
    assert not (tmp_path / summarize.BATCH_FILE).exists()


def test_documents_past_the_batchs_size_are_sent_one_at_a_time(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    monkeypatch.setattr(summarize, "BATCH_MAX_BYTES", 1)
    client = FakeAnthropic()
    result = first(config, client, tmp_path)
    assert result["queued"] == 0 and result["summarized"] == 16 and client.batches_sent == []


def test_a_summary_cut_off_in_the_batch_is_paid_for_and_counted_as_a_try(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic(stop_reason="max_tokens")
    first(config, client, tmp_path)
    result = collect(config, client, tmp_path)
    ledger = json.loads((tmp_path / summarize.LEDGER).read_text())["2026-09"]
    # Each paid for at half price in the batch, then tried once more alone (cut off again, at full price).
    assert ledger["failed_cost"] == round(16 * REQUEST / 2 + 16 * REQUEST, 4)
    tries = json.loads((tmp_path / summarize.STOPPED_EARLY).read_text())
    assert all(t["summary"]["tries"] == 2 for t in tries.values())
    assert result["summarized"] == 0


def test_new_summaries_from_the_batch_are_translated_when_collected(tmp_path):
    config = spanish_town(tmp_path)
    client = FakeAnthropic()
    first(config, client, tmp_path)
    assert translations(tmp_path) == {}
    result = collect(config, client, tmp_path)
    assert result["collected"] == 16 and result["translated"] == 16
    assert len(translations(tmp_path)) == 16


def test_a_long_document_is_estimated_at_half_price_in_the_batch(tmp_path):
    config = load_config("gloucester")
    minutes_town(tmp_path, blank_pdf(120))
    # About $1.04 alone, so it waits with $0.60 left; in a batch, about $0.52, so it goes.
    alone = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT, allowance=0.60)
    assert alone["summarized"] == 0 and "waiting for a run with room" in alone["stopped"]
    batched = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT, allowance=0.60,
                            batch=True, clock=at(0), sleep=lambda s: None)
    assert batched["queued"] == 1


def test_a_summary_the_batch_returns_that_cant_be_read_is_paid_for_and_sent_again(tmp_path):
    from types import SimpleNamespace
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    first(config, client, tmp_path)
    results = client.messages.batches.results

    def garbled(batch_id):
        out = list(results(batch_id))
        out[0].result.message = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="{")],
                                                usage=SimpleNamespace(input_tokens=1500, output_tokens=400))
        return out
    client.messages.batches.results = garbled
    result = collect(config, client, tmp_path)
    assert result["collected"] == 15 and result["summarized"] == 16
    assert any("couldn't be read" in e for e in result["errors"])
    ledger = json.loads((tmp_path / summarize.LEDGER).read_text())["2026-09"]
    assert ledger["failed_cost"] == round(REQUEST / 2, 4)
