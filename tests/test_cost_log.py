"""The cost log (pipeline/cost_log.py): each request paid for is a line as it's answered, counted once,
and each month's ledger is made from the lines, with a stand-in model client (no API calls)."""

import hashlib
import json

import pytest

from conftest import FETCHED_AT
from fakes import FakeAnthropic
from test_summarize import REQUEST, Killed, with_minutes
from test_summarize_batch import collect, first
from test_translate import REVIEW, TRANSLATION, spanish_town

from pipeline import cost_log, network, summarize
from pipeline.config import load_config


def lines(tmp_path, month="2026-09"):
    """The log's lines as written, each one read."""
    return [json.loads(line) for line in cost_log.path(tmp_path, month).read_text().splitlines()]


def ledger(tmp_path):
    return json.loads((tmp_path / summarize.LEDGER).read_text())


def stopped_after(client, requests: int):
    """The client, made to stop the run (a step's timeout) when it's asked for one more summary than this."""
    stream = client.messages.stream

    def counted(**kwargs):
        if len(client.calls) == requests:
            raise Killed()
        return stream(**kwargs)
    client.messages.stream = counted
    return client


@pytest.mark.parametrize("requests", [1, 4])
def test_a_run_stopped_after_n_requests_has_logged_exactly_n(tmp_path, requests):
    config = with_minutes(tmp_path)
    with pytest.raises(Killed):
        summarize.run(config, stopped_after(FakeAnthropic(), requests), tmp_path, limit=50, now=FETCHED_AT)
    logged = lines(tmp_path)
    assert len(logged) == requests and {e["kind"] for e in logged} == {"summary"}
    assert len({e["key"] for e in logged}) == requests and all(e["cost"] == REQUEST for e in logged)
    assert ledger(tmp_path)["2026-09"] == {"cost": round(requests * REQUEST, 4), "documents": requests}
    # The next run goes on from there.
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert ledger(tmp_path)["2026-09"] == {"cost": round((requests + 1) * REQUEST, 4), "documents": requests + 1}


def test_a_summary_logged_but_stopped_before_its_file_is_saved_is_counted_when_made_again(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    save = summarize.save_record

    def stopped(*args):
        raise Killed()
    monkeypatch.setattr(summarize, "save_record", stopped)
    with pytest.raises(Killed):
        summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert ledger(tmp_path)["2026-09"] == {"cost": REQUEST, "documents": 1}
    assert not (tmp_path / "summaries").exists()
    # Not saved, so it's made again: both paid for, the first as replaced.
    monkeypatch.setattr(summarize, "save_record", save)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert ledger(tmp_path)["2026-09"] == {"cost": REQUEST, "documents": 1, "replaced_cost": REQUEST}


def test_a_transcription_is_logged_request_by_request(tmp_path, monkeypatch):
    from test_summarize import blank_pdf, minutes_town
    config = load_config("gloucester")
    monkeypatch.setitem(summarize.TRANSCRIBE, "pages", 2)
    sha = minutes_town(tmp_path, blank_pdf(5))
    client = FakeAnthropic()
    stream = client.messages.stream

    def third_stops(**kwargs):
        # The summary, then two of the transcription's three requests.
        if len(client.calls) == 3:
            raise Killed()
        return stream(**kwargs)
    client.messages.stream = third_stops
    with pytest.raises(Killed):
        summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    assert [e["kind"] for e in lines(tmp_path)] == ["summary", "transcript", "transcript"]
    assert all(e["doc"] == sha for e in lines(tmp_path))
    assert ledger(tmp_path)["2026-09"]["transcript_cost"] == round(2 * REQUEST, 4)


def test_a_batch_collected_twice_is_counted_once(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    first(config, client, tmp_path)
    # The collecting run is stopped after saving three of the batch's summaries: its file stays, for the next run.
    save, saved = summarize.save_record, []

    def stopped_at_the_fourth(data_dir, sha, record):
        if len(saved) == 3:
            raise Killed()
        saved.append(sha)
        save(data_dir, sha, record)
    monkeypatch.setattr(summarize, "save_record", stopped_at_the_fourth)
    with pytest.raises(Killed):
        collect(config, client, tmp_path)
    assert (tmp_path / summarize.BATCH_FILE).exists()
    # Three saved, and the fourth logged just before it was stopped.
    assert len(lines(tmp_path)) == 4
    monkeypatch.setattr(summarize, "save_record", save)
    # The next run collects the whole batch again: what was logged isn't again, or paid from this run's budget.
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT, clock=lambda: FETCHED_AT,
                           sleep=lambda s: None)
    assert result["collected"] == 16
    summaries = [e for e in lines(tmp_path) if e["kind"] == "summary"]
    assert len(summaries) == 16 and len({e["key"] for e in summaries}) == 16
    assert all(e["key"].startswith("batch-1:doc-") for e in summaries)
    row = ledger(tmp_path)["2026-09"]
    assert row["cost"] == round(16 * REQUEST / 2, 4) and row["documents"] == 16 and "replaced_cost" not in row
    transcribed = sum(e["cost"] for e in lines(tmp_path) if e["kind"] == "transcript")
    assert result["estimated_cost"] == round(12 * REQUEST / 2 + transcribed, 2)


def test_a_line_already_logged_is_not_counted_again(tmp_path):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(stop_reason="max_tokens"), tmp_path, limit=2, now=FETCHED_AT)
    row = ledger(tmp_path)["2026-09"]
    assert row == {"cost": 0.0, "documents": 0, "failed_cost": round(2 * REQUEST, 4)}
    log = summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT)
    # A write tried again, by its key.
    key = lines(tmp_path)[0]["key"]
    assert log.add("failed", key, REQUEST) is False and len(lines(tmp_path)) == 2
    # The same line twice in the file (two runs' commits merged with both) is read once.
    file = cost_log.path(tmp_path, "2026-09")
    file.write_text(file.read_text() + file.read_text().splitlines()[0] + "\n")
    assert cost_log.totals(cost_log.read(file)) == row
    # Opening the log again changes nothing.
    summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT)
    assert ledger(tmp_path)["2026-09"] == row


def test_a_summary_made_again_twice_counts_each_once(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    for _ in range(2):
        monkeypatch.setitem(summarize.KINDS["agenda"], "version", summarize.KINDS["agenda"]["version"] + 1)
        summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert ledger(tmp_path)["2026-09"] == {"cost": REQUEST, "documents": 1, "replaced_cost": round(2 * REQUEST, 4)}
    assert summarize.ledger_differences(tmp_path, summarize.summary_settings(config)) == []


def test_a_line_cut_short_is_skipped_and_the_next_starts_on_its_own(tmp_path):
    file = cost_log.path(tmp_path, "2026-09")
    cost_log.append(file, [{"kind": "failed", "key": "a", "cost": 1.0}])
    with open(file, "a") as f:
        f.write('{"kind": "failed", "key": "b", "co')
    log = cost_log.Log(tmp_path, "2026-09")
    assert [e["key"] for e in log.entries] == ["a"]
    log.add("failed", "c", 2.0)
    assert [e["key"] for e in cost_log.read(file)] == ["a", "c"]
    assert ledger(tmp_path)["2026-09"] == {"cost": 0.0, "documents": 0, "failed_cost": 3.0}
    assert (tmp_path / "costs" / ".gitattributes").read_text() == "*.jsonl merge=union\n"


def test_translations_drafts_and_reviews_are_logged(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)
    logged = lines(tmp_path)
    kinds = [e["kind"] for e in logged]
    # The town's own text drafted first (and reviewed), then five summaries, each translated and reviewed.
    assert kinds[:2] == ["draft", "review"] and kinds.count("summary") == 5
    assert kinds.count("translation") == 5 and kinds.count("review") == 6
    assert all(e["lang"] == "es" for e in logged if e["kind"] in cost_log.TRANSLATING)
    assert all(e["cost"] == TRANSLATION for e in logged if e["kind"] in ("draft", "translation"))
    row = ledger(tmp_path)["2026-09"]
    assert row["translations"] == 6 and row["translation_cost"] == round(6 * (TRANSLATION + REVIEW), 4)
    assert summarize.ledger_differences(tmp_path, summarize.summary_settings(config)) == []


def test_a_translation_cut_off_is_logged_as_failed(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    before = ledger(tmp_path)["2026-09"]
    config["site"]["languages"] = ["en", "es"]
    (tmp_path / "summaries" / "es").rename(tmp_path / "es-before")
    summarize.run(config, FakeAnthropic(stop_reason="max_tokens"), tmp_path, limit=0, now=FETCHED_AT)
    failed = [e for e in lines(tmp_path) if e["kind"] == "failed"]
    assert len(failed) == 1 and failed[0]["lang"] == "es"
    assert ledger(tmp_path)["2026-09"]["failed_cost"] == round(TRANSLATION, 4)
    assert ledger(tmp_path)["2026-09"]["translation_cost"] == before["translation_cost"]


def old_ledger(tmp_path, month):
    """The town as the engine before the log left it: its summaries, translations, and drafts, and a
    ledger whose row for the month is the recount and what no file records; no costs/ folder."""
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=4, now=FETCHED_AT)
    for path in (tmp_path / "costs").iterdir():
        path.unlink()
    (tmp_path / "costs").rmdir()
    settings = summarize.summary_settings(config)
    row = {**summarize.recount(tmp_path, settings, month), "failed_cost": 0.25, "transcript_cost": 0.5,
           "replaced_cost": 0.125}
    (tmp_path / summarize.LEDGER).write_text(json.dumps({"2026-08": {"cost": 9.0, "documents": 99}, month: row}))
    return config, row


def test_a_town_from_before_the_log_starts_it_where_its_month_was(tmp_path, monkeypatch):
    config, row = old_ledger(tmp_path, "2026-09")
    settings = summarize.summary_settings(config)
    total = summarize.month_cost({"2026-09": row}, "2026-09")
    log = summarize.open_log(tmp_path, settings, FETCHED_AT)
    # A line for each summary and translation saved this month and for the batch of drafts, and one for the rest.
    kinds = [e["kind"] for e in lines(tmp_path)]
    assert kinds.count("summary") == 4 and kinds.count("translation") == 4 and kinds.count("draft") == 1
    assert kinds[-1] == "seed" and lines(tmp_path)[-1]["totals"] == {"failed_cost": 0.25, "transcript_cost": 0.5,
                                                                      "replaced_cost": 0.125}
    assert ledger(tmp_path)["2026-09"] == row and log.month_cost() == pytest.approx(total)
    assert ledger(tmp_path)["2026-08"] == {"cost": 9.0, "documents": 99}
    assert summarize.ledger_differences(tmp_path, settings) == []
    # Once: the next run goes on from the log, without reading the summaries for it.
    monkeypatch.setattr(summarize, "saved_summaries", lambda data_dir: pytest.fail("recounted"))
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    after = ledger(tmp_path)["2026-09"]
    assert after["documents"] == row["documents"] + 1
    assert summarize.month_cost(ledger(tmp_path), "2026-09") == pytest.approx(total + REQUEST + TRANSLATION + REVIEW,
                                                                             abs=1e-4)


def test_a_summary_from_before_the_log_made_again_counts_as_replaced(tmp_path, monkeypatch):
    config, row = old_ledger(tmp_path, "2026-09")
    config["site"]["languages"] = ["en"]
    summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT)
    monkeypatch.setitem(summarize.KINDS["agenda"], "version", summarize.KINDS["agenda"]["version"] + 1)
    # The upcoming meeting's agenda, summarized first before the log, is first again.
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)["summarized"] == 1
    after = ledger(tmp_path)["2026-09"]
    assert after["documents"] == row["documents"] and after["cost"] == row["cost"]
    assert after["replaced_cost"] == round(row["replaced_cost"] + REQUEST, 4)


def test_a_new_month_starts_an_empty_log_without_a_recount(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    monkeypatch.setattr(summarize, "saved_summaries", lambda data_dir: pytest.fail("recounted"))
    october = FETCHED_AT.replace(month=10, day=2)
    log = summarize.open_log(tmp_path, summarize.summary_settings(config), october)
    assert log.entries == [] and "2026-10" not in ledger(tmp_path)
    assert ledger(tmp_path)["2026-09"] == {"cost": REQUEST, "documents": 1}


def network_root(tmp_path, data):
    """A network with one town whose data is this town's."""
    town = tmp_path / "net" / "towns" / "a-ma"
    (town / "config").mkdir(parents=True)
    (town / "config" / "a.toml").write_text("")
    data.rename(town / "data")
    return tmp_path / "net"


def test_the_networks_budget_reads_the_ledger_made_from_the_log(tmp_path):
    data = tmp_path / "data"
    config = spanish_town(data)
    summarize.run(config, FakeAnthropic(stop_reason="max_tokens"), data, limit=1, now=FETCHED_AT)
    summarize.run(config, FakeAnthropic(), data, limit=3, now=FETCHED_AT)
    paid = sum(e["cost"] for e in lines(data))
    root = network_root(tmp_path, data)
    assert network.summary_budget(root, 50.0, 1, FETCHED_AT.date())["spent"] == round(paid, 2)


def test_the_check_finds_a_difference_and_changes_nothing(tmp_path, capsys):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=3, now=FETCHED_AT)
    settings = summarize.summary_settings(config)
    assert summarize.ledger_differences(tmp_path, settings) == []
    assert summarize.ledger(config, tmp_path, check=True) == 0

    def files():
        return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    # A summary whose file records another cost, and one the log lacks.
    summary = next((tmp_path / "summaries").glob("*.json"))
    summary.write_text(json.dumps({**json.loads(summary.read_text()), "cost": 1.0}))
    file = cost_log.path(tmp_path, "2026-09")
    kept = [line for line in file.read_text().splitlines()
            if json.loads(line)["kind"] != "summary" or json.loads(line)["doc"] == summary.stem]
    file.write_text("\n".join(kept) + "\n")
    before = files()
    found = summarize.ledger_differences(tmp_path, settings)
    assert any(summary.stem in d and "$1.000000 in its file" in d for d in found)
    assert sum("isn't in the log" in d for d in found) == 2
    assert any(d.startswith(f"2026-09: {summarize.LEDGER} has") for d in found)
    assert summarize.ledger(config, tmp_path, check=True) == 1
    assert "::warning::2026-09: the summary of" in capsys.readouterr().out
    assert files() == before
    # The ledger made again from the log is the log's, as its check finds.
    summarize.ledger(config, tmp_path, check=False)
    assert ledger(tmp_path)["2026-09"] == cost_log.totals(cost_log.read(file))


def test_the_check_command_exits_1_on_a_difference(tmp_path):
    import subprocess
    import sys
    from conftest import ROOT
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=2, now=FETCHED_AT)
    command = [sys.executable, "-m", "pipeline.summarize", "ledger", "--check", "--town", "gloucester", "--data", str(tmp_path)]
    agree = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert agree.returncode == 0 and "0 difference(s)" in agree.stdout
    file = cost_log.path(tmp_path, "2026-09")
    file.write_text(file.read_text().splitlines()[0] + "\n")
    differ = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert differ.returncode == 1 and "isn't in the log" in differ.stdout
