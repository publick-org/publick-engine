"""Each step of a summary run on its own (pipeline/summarize.py's SummaryRun): choosing the documents,
summarizing one, translating, laying out text, sending the batch, and transcribing scans."""

import json

import pytest

from conftest import FETCHED_AT
from fakes import FakeAnthropic
from test_summarize import COUNCIL, REQUEST, minutes_town, setup, with_council, with_minutes
from test_translate import spanish_town

from pipeline import summarize
from pipeline.config import load_config


def steps(tmp_path, config, client=None, **kwargs) -> summarize.SummaryRun:
    """A run of the town's summaries, its cost log open, before any step."""
    run = summarize.SummaryRun(config, client or FakeAnthropic(), tmp_path, now=FETCHED_AT, **kwargs)
    run.start()
    return run


def unfetchable(run):
    """The run's documents can't be fetched."""
    def get(folder, name):
        raise summarize.FetchError("404 Not Found")
    run.storage.get = get


def test_new_documents_are_chosen_first_with_the_translations_after_them(tmp_path):
    run = steps(tmp_path, with_minutes(tmp_path))
    run.choose(limit=50, batch=False, collecting=False)
    # An upcoming agenda and 11 recent minutes are new, 4 older minutes are backlog.
    assert run.work.index(summarize.TRANSLATE) == 12 and len(run.work) == 17
    assert run.waiting_count == 16 and len(run.older) == 4
    assert all(summarize.doc_key(item) in run.older for item in run.work[13:])
    assert not run.batching and not run.batching_translations
    run.result()


def test_the_limit_cuts_the_backlog_first(tmp_path):
    run = steps(tmp_path, with_minutes(tmp_path))
    run.choose(limit=12, batch=True, collecting=False)
    assert summarize.TRANSLATE == run.work[-1] and len(run.work) == 13
    assert run.waiting_count == 16 and run.batching and run.batching_translations
    run.result()


def test_a_town_without_prices_chooses_nothing(tmp_path):
    config = with_minutes(tmp_path)
    del config["summaries"]["input_price"]
    run = steps(tmp_path, config)
    assert run.collect(collecting=False, wait=True)
    run.choose(limit=50, batch=False, collecting=False)
    assert run.work == [] and run.errors == [f"{run.unpriced}; nothing summarized"]
    run.result()


def test_one_document_is_summarized_alone(tmp_path):
    client = FakeAnthropic()
    run = steps(tmp_path, with_minutes(tmp_path), client)
    run.choose(limit=50, batch=False, collecting=False)
    assert run.summarize(*run.work[0])
    assert run.done == 1 and len(client.calls) == 1 and run.spent == pytest.approx(REQUEST)
    assert run.result()["summarized"] == 1


def test_a_document_in_the_batch_is_counted_at_half_its_estimate(tmp_path):
    client = FakeAnthropic()
    run = steps(tmp_path, with_minutes(tmp_path), client)
    run.choose(limit=50, batch=True, collecting=False)
    kind, meeting, doc = run.work[0]
    assert run.summarize(kind, meeting, doc)
    assert client.calls == [] and len(run.queued) == 1 and run.done == 0
    estimate = summarize.estimated_cost(kind, 1, run.settings) * summarize.BATCH_PRICE
    assert run.spent == pytest.approx(estimate) == run.queued_cost[doc["sha256"]]
    run.result()


def test_summarizing_stops_at_the_towns_limit_for_one_run(tmp_path):
    client = FakeAnthropic()
    run = steps(tmp_path, with_minutes(tmp_path), client)
    run.choose(limit=50, batch=False, collecting=False)
    run.spent = run.settings["max_cost_per_run"]
    assert not run.summarize(*run.work[0])
    assert client.calls == [] and "max_cost_per_run" in run.errors[0]
    run.result()


def test_a_document_that_cant_be_fetched_fails_and_the_run_goes_on(tmp_path):
    client = FakeAnthropic()
    run = steps(tmp_path, with_minutes(tmp_path), client)
    run.choose(limit=50, batch=False, collecting=False)
    unfetchable(run)
    assert run.summarize(*run.work[0])
    assert client.calls == [] and run.failed == 1 and "404 Not Found" in run.errors[0]
    run.result()


def test_the_backlogs_share_bounds_older_documents_only(tmp_path):
    run = steps(tmp_path, load_config("gloucester"), allowance=1.0, backlog_allowance=0.25)
    run.spent, run.spent_backlog = 0.5, 0.25
    assert run.over_budget(older=True) and not run.over_budget(older=False)
    assert run.room(older=True) == 0.0 and run.room(older=False) == 0.5
    run.spent = 1.0
    assert run.over_budget(older=False)
    run.result()


def summarized_in_english(tmp_path):
    """The Spanish town's summaries, made with no translations, and the town's config."""
    config = spanish_town(tmp_path)
    english = {**config, "site": {**config["site"], "languages": ["en"]}}
    assert summarize.run(english, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["summarized"] == 16
    return config


def test_new_documents_translations_go_first_then_the_rest(tmp_path):
    config = summarized_in_english(tmp_path)
    run = steps(tmp_path, config)
    run.translate_summaries(only_new=True)
    assert run.translated == 12 and run.spent_backlog == 0
    run.translate_summaries(only_new=False)
    assert run.translated == 16 and run.spent_backlog > 0
    run.result()


def test_translations_wait_when_the_budget_is_spent(tmp_path):
    config = summarized_in_english(tmp_path)
    client = FakeAnthropic()
    run = steps(tmp_path, config, client, backlog_allowance=0.0)
    # New documents' translations aren't held to the backlog's share; the others' are.
    run.translate_summaries(only_new=False)
    assert client.calls == [] and run.stopped == "translations wait: this run's budget for them is spent"
    run.translate_summaries(only_new=True)
    assert run.translated == 12
    run.result()


def test_translations_need_their_prices(tmp_path):
    config = summarized_in_english(tmp_path)
    # Another translation model than the default, with no prices given for it.
    config["summaries"] = {**config["summaries"], "translation_model": "another-model"}
    client = FakeAnthropic()
    run = steps(tmp_path, config, client)
    run.translate_summaries(only_new=True)
    assert client.calls == [] and "nothing translated" in run.errors[0]
    run.result()


def test_text_is_laid_out_and_votes_read_without_the_model(tmp_path):
    config = with_council(load_config("gloucester"))
    sha = minutes_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    # The body's members change: its roll calls are read again, for free.
    client = FakeAnthropic()
    run = steps(tmp_path, with_council(config, COUNCIL[:5]), client)
    run.lay_out_texts()
    assert run.laid_out == 1 and client.calls == []
    record = json.loads((tmp_path / "summaries" / f"{sha}.json").read_text())
    assert record["votes_members"] == COUNCIL[:5]
    run.result()


def test_text_that_cant_be_fetched_is_left_for_the_next_run(tmp_path, monkeypatch):
    config = with_council(load_config("gloucester"))
    minutes_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)
    run = steps(tmp_path, with_council(config, COUNCIL[:5]))
    unfetchable(run)
    run.lay_out_texts()
    assert run.laid_out == 0 and run.failed == 0 and "404 Not Found" in run.errors[0]
    run.result()
    # No more than TEXT_PER_RUN a run.
    monkeypatch.setattr(summarize, "TEXT_PER_RUN", 0)
    run = steps(tmp_path, with_council(config, COUNCIL[:5]))
    run.lay_out_texts()
    assert run.laid_out == 0 and run.errors == []
    run.result()


def needing_a_transcript(tmp_path):
    """The fixture town's scanned agenda, summarized and waiting for its transcription."""
    config = setup(tmp_path)
    result = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT, backlog_allowance=0.0)
    assert result["summarized"] == 1 and result["transcribed"] == 0
    return config


def test_a_scan_is_transcribed_once_no_summary_is_waiting(tmp_path):
    config = needing_a_transcript(tmp_path)
    client = FakeAnthropic()
    run = steps(tmp_path, config, client)
    # Before choose(), the run doesn't know what's waiting; afterwards, nothing is.
    run.waiting_count = 1
    run.transcribe_scans()
    assert client.calls == []
    run.choose(limit=50, batch=False, collecting=False)
    run.transcribe_scans()
    assert run.transcribed == 1 and len(client.calls) == 1 and run.spent_backlog == run.spent > 0
    run.result()


def test_a_transcription_waits_when_the_budget_is_spent(tmp_path):
    config = needing_a_transcript(tmp_path)
    client = FakeAnthropic()
    run = steps(tmp_path, config, client)
    run.spent = run.settings["max_cost_per_run"]
    run.transcribe_scans()
    assert client.calls == [] and run.stopped == "transcriptions wait: this run's budget for them is spent"
    run.result()


def test_a_scan_that_cant_be_fetched_fails_and_the_run_goes_on(tmp_path):
    config = needing_a_transcript(tmp_path)
    run = steps(tmp_path, config)
    unfetchable(run)
    run.transcribe_scans()
    assert run.transcribed == 0 and run.failed == 1 and "404 Not Found" in run.errors[0]
    run.result()


def test_a_refused_batch_sends_one_at_a_time_only_what_fits_at_full_price(tmp_path):
    client = FakeAnthropic()

    def refused(requests):
        raise RuntimeError("batches are unavailable")
    client.messages.batches.create = refused
    run = steps(tmp_path, with_minutes(tmp_path), client, allowance=1.0)
    run.choose(limit=50, batch=True, collecting=False)
    run.summaries()
    assert len(run.queued) == 16 and client.calls == []
    # What's left of the network's allowance holds any one of them at half price, none at full price.
    run.allowance = min(run.queued_cost.values())
    run.send_queued()
    assert run.queued == [] and client.calls == [] and run.spent == 0
    assert len(run.too_costly) == 16 and "couldn't be sent" in run.errors[0]
    run.result()


def test_nothing_queued_sends_no_batch(tmp_path):
    client = FakeAnthropic()
    run = steps(tmp_path, with_minutes(tmp_path), client)
    run.send_queued()
    assert client.batches_sent == [] and run.errors == []
    run.result()
