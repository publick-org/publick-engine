"""The cost log (pipeline/cost_log.py): each request paid for is a line as it's answered, counted once,
and each month's ledger is made from the lines, with a stand-in model client (no API calls). Kept in the
town's own folder (out of git) without the private store's keys, and in the private store (a stand-in
bucket) with them."""

import hashlib
import json
import shutil
import subprocess
import sys

import pytest

from conftest import FETCHED_AT, ROOT
from fakes import FakeAnthropic, FakePrivateBucket
from test_summarize import REQUEST, Killed, with_minutes
from test_summarize_batch import collect, first
from test_translate import REVIEW, TRANSLATION, spanish_town

from pipeline import cost_log, network, summarize, update
from pipeline.config import load_config

MONTH = "2026-09"


def local(tmp_path):
    return cost_log.LocalStore(tmp_path)


def lines(tmp_path, month=MONTH):
    """The month's lines as written, from each run's file, each one read."""
    return [json.loads(line) for text in local(tmp_path).logs(month) for line in text.splitlines()]


def run_files(tmp_path, month=MONTH):
    return sorted((tmp_path / "costs" / month).glob("*.jsonl"))


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
    assert ledger(tmp_path)[MONTH] == {"cost": round(requests * REQUEST, 4), "documents": requests}
    # The next run goes on from there, in a file of its own.
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert ledger(tmp_path)[MONTH] == {"cost": round((requests + 1) * REQUEST, 4), "documents": requests + 1}
    assert len(run_files(tmp_path)) == 2


def test_the_towns_own_log_is_kept_out_of_git(tmp_path):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=2, now=FETCHED_AT)
    assert (tmp_path / "costs" / ".gitignore").read_text() == "*\n"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    shutil.copytree(tmp_path / "costs", repo / "data" / "costs")
    shutil.copy(tmp_path / summarize.LEDGER, repo / "data" / summarize.LEDGER)
    added = subprocess.run(["git", "-C", str(repo), "add", "--dry-run", "data"], capture_output=True, text=True,
                           check=True).stdout
    # Without the private store the ledger is where it always was; the lines never go in.
    assert "summary-costs.json" in added and "costs/" not in added


def test_a_summary_logged_but_stopped_before_its_file_is_saved_is_counted_when_made_again(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    save = summarize.save_record

    def stopped(*args):
        raise Killed()
    monkeypatch.setattr(summarize, "save_record", stopped)
    with pytest.raises(Killed):
        summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert ledger(tmp_path)[MONTH] == {"cost": REQUEST, "documents": 1}
    assert not (tmp_path / "summaries").exists()
    # Not saved, so it's made again: both paid for, the first as replaced.
    monkeypatch.setattr(summarize, "save_record", save)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert ledger(tmp_path)[MONTH] == {"cost": REQUEST, "documents": 1, "replaced_cost": REQUEST}


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
    assert ledger(tmp_path)[MONTH]["transcript_cost"] == round(2 * REQUEST, 4)


def test_a_batch_collected_twice_is_counted_once(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    first(config, client, tmp_path)
    # What the run had spent before the batch is kept out of git, not in the batch's file.
    assert "spent_before" not in json.loads((tmp_path / summarize.BATCH_FILE).read_text())
    assert (tmp_path / "costs" / summarize.BATCH_SPENT).exists()
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
    row = ledger(tmp_path)[MONTH]
    assert row["cost"] == round(16 * REQUEST / 2, 4) and row["documents"] == 16 and "replaced_cost" not in row
    transcribed = sum(e["cost"] for e in lines(tmp_path) if e["kind"] == "transcript")
    assert result["estimated_cost"] == round(12 * REQUEST / 2 + transcribed, 2)


def test_a_line_already_logged_is_not_counted_again(tmp_path):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(stop_reason="max_tokens"), tmp_path, limit=2, now=FETCHED_AT)
    row = ledger(tmp_path)[MONTH]
    assert row == {"cost": 0.0, "documents": 0, "failed_cost": round(2 * REQUEST, 4)}
    log = summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT)
    # A write tried again, by its key.
    key = lines(tmp_path)[0]["key"]
    assert log.add("failed", key, REQUEST) is False and len(lines(tmp_path)) == 2
    # The same lines in a second run's file (a copy uploaded twice under two names) are read once.
    (file,) = run_files(tmp_path)
    shutil.copy(file, file.with_name("99999999T999999Z-copy.jsonl"))
    assert len(lines(tmp_path)) == 4
    assert cost_log.totals(cost_log.read_month(local(tmp_path), MONTH)) == row
    # Opening the log again changes nothing.
    summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT)
    assert ledger(tmp_path)[MONTH] == row


def test_a_summary_made_again_twice_counts_each_once(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    for _ in range(2):
        monkeypatch.setitem(summarize.KINDS["agenda"], "version", summarize.KINDS["agenda"]["version"] + 1)
        summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert ledger(tmp_path)[MONTH] == {"cost": REQUEST, "documents": 1, "replaced_cost": round(2 * REQUEST, 4)}
    assert summarize.ledger_differences(tmp_path, summarize.summary_settings(config)) == []


def test_a_line_cut_short_is_skipped_and_the_next_starts_on_its_own(tmp_path):
    log = cost_log.Log(local(tmp_path), MONTH)
    log.add("failed", "a", 1.0)
    with open(log.copy, "a") as f:
        f.write('{"kind": "failed", "key": "b", "co')
    log = cost_log.Log(local(tmp_path), MONTH)
    assert [e["key"] for e in log.entries] == ["a"]
    log.add("failed", "c", 2.0)
    assert [e["key"] for e in cost_log.read_month(local(tmp_path), MONTH)] == ["a", "c"]
    assert ledger(tmp_path)[MONTH] == {"cost": 0.0, "documents": 0, "failed_cost": 3.0}
    assert (tmp_path / "costs" / ".gitignore").read_text() == "*\n"


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
    row = ledger(tmp_path)[MONTH]
    assert row["translations"] == 6 and row["translation_cost"] == round(6 * (TRANSLATION + REVIEW), 4)
    assert summarize.ledger_differences(tmp_path, summarize.summary_settings(config)) == []


def test_a_translation_cut_off_is_logged_as_failed(tmp_path):
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    before = ledger(tmp_path)[MONTH]
    config["site"]["languages"] = ["en", "es"]
    (tmp_path / "summaries" / "es").rename(tmp_path / "es-before")
    summarize.run(config, FakeAnthropic(stop_reason="max_tokens"), tmp_path, limit=0, now=FETCHED_AT)
    failed = [e for e in lines(tmp_path) if e["kind"] == "failed"]
    assert len(failed) == 1 and failed[0]["lang"] == "es"
    assert ledger(tmp_path)[MONTH]["failed_cost"] == round(TRANSLATION, 4)
    assert ledger(tmp_path)[MONTH]["translation_cost"] == before["translation_cost"]


def old_ledger(tmp_path, month):
    """The town as the engine before the log left it: its summaries, translations, and drafts, and a
    ledger whose row for the month is the recount and what no file records; no costs/ folder."""
    config = spanish_town(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=4, now=FETCHED_AT)
    shutil.rmtree(tmp_path / "costs")
    settings = summarize.summary_settings(config)
    row = {**summarize.recount(tmp_path, settings, month), "failed_cost": 0.25, "transcript_cost": 0.5,
           "replaced_cost": 0.125}
    (tmp_path / summarize.LEDGER).write_text(json.dumps({"2026-08": {"cost": 9.0, "documents": 99}, month: row}))
    return config, row


def test_a_town_from_before_the_log_starts_it_where_its_month_was(tmp_path, monkeypatch):
    config, row = old_ledger(tmp_path, MONTH)
    settings = summarize.summary_settings(config)
    total = summarize.month_cost({MONTH: row}, MONTH)
    log = summarize.open_log(tmp_path, settings, FETCHED_AT)
    # A line for each summary and translation saved this month and for the batch of drafts, and one for the rest.
    kinds = [e["kind"] for e in lines(tmp_path)]
    assert kinds.count("summary") == 4 and kinds.count("translation") == 4 and kinds.count("draft") == 1
    assert kinds[-1] == "seed" and lines(tmp_path)[-1]["totals"] == {"failed_cost": 0.25, "transcript_cost": 0.5,
                                                                      "replaced_cost": 0.125}
    assert ledger(tmp_path)[MONTH] == row and log.month_cost() == pytest.approx(total)
    assert ledger(tmp_path)["2026-08"] == {"cost": 9.0, "documents": 99}
    assert summarize.ledger_differences(tmp_path, settings) == []
    # Once: the next run goes on from the log, without reading the summaries for it.
    monkeypatch.setattr(summarize, "saved_summaries", lambda data_dir: pytest.fail("recounted"))
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    after = ledger(tmp_path)[MONTH]
    assert after["documents"] == row["documents"] + 1
    assert summarize.month_cost(ledger(tmp_path), MONTH) == pytest.approx(total + REQUEST + TRANSLATION + REVIEW,
                                                                         abs=1e-4)


def test_a_summary_from_before_the_log_made_again_counts_as_replaced(tmp_path, monkeypatch):
    config, row = old_ledger(tmp_path, MONTH)
    config["site"]["languages"] = ["en"]
    summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT)
    monkeypatch.setitem(summarize.KINDS["agenda"], "version", summarize.KINDS["agenda"]["version"] + 1)
    # The upcoming meeting's agenda, summarized first before the log, is first again.
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)["summarized"] == 1
    after = ledger(tmp_path)[MONTH]
    assert after["documents"] == row["documents"] and after["cost"] == row["cost"]
    assert after["replaced_cost"] == round(row["replaced_cost"] + REQUEST, 4)


def test_a_new_month_starts_an_empty_log_without_a_recount(tmp_path, monkeypatch):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    monkeypatch.setattr(summarize, "saved_summaries", lambda data_dir: pytest.fail("recounted"))
    october = FETCHED_AT.replace(month=10, day=2)
    log = summarize.open_log(tmp_path, summarize.summary_settings(config), october)
    assert log.entries == [] and "2026-10" not in ledger(tmp_path)
    assert ledger(tmp_path)[MONTH] == {"cost": REQUEST, "documents": 1}


def network_root(tmp_path, data, config=""):
    """A network with one town whose data is this town's."""
    town = tmp_path / "net" / "towns" / "a-ma"
    (town / "config").mkdir(parents=True)
    (town / "config" / "a.toml").write_text(config)
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
    (file,) = run_files(tmp_path)
    kept = [line for line in file.read_text().splitlines()
            if json.loads(line)["kind"] != "summary" or json.loads(line)["doc"] == summary.stem]
    file.write_text("\n".join(kept) + "\n")
    before = files()
    found = summarize.ledger_differences(tmp_path, settings)
    assert any(summary.stem in d and "one cost in its file and another in the log" in d for d in found)
    assert sum("isn't in the log" in d for d in found) == 2
    assert any(d.startswith(f"{MONTH}: the ledger's row isn't the one the log makes (cost, documents") for d in found)
    assert summarize.ledger(config, tmp_path, check=True) == 1
    out = capsys.readouterr().out
    assert "::warning::2026-09: the summary of" in out
    # The check's output (a public run log) says where, never how much.
    assert "$" not in out and "1.0" not in out
    assert files() == before
    # The ledger made again from the log is the log's, as its check finds; its output names the months only.
    summarize.ledger(config, tmp_path, check=False)
    assert ledger(tmp_path)[MONTH] == cost_log.totals(cost_log.read_month(local(tmp_path), MONTH))
    assert capsys.readouterr().out == f"The ledger's rows made again from the log: {MONTH}\n"


def test_the_check_command_exits_1_on_a_difference(tmp_path):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=2, now=FETCHED_AT)
    command = [sys.executable, "-m", "pipeline.summarize", "ledger", "--check", "--town", "gloucester", "--data", str(tmp_path)]
    agree = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert agree.returncode == 0 and "0 difference(s)" in agree.stdout
    (file,) = run_files(tmp_path)
    file.write_text(file.read_text().splitlines()[0] + "\n")
    differ = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert differ.returncode == 1 and "isn't in the log" in differ.stdout


# The private store: a stand-in bucket, through the keys.

def private(monkeypatch):
    """The private store's keys set, with a stand-in bucket for them."""
    bucket = FakePrivateBucket()
    for key in cost_log.PRIVATE_KEYS:
        monkeypatch.setenv(key, "private-test")
    monkeypatch.setattr(cost_log, "private_client", lambda: bucket)
    return bucket


@pytest.fixture
def bucket(monkeypatch):
    return private(monkeypatch)


def prefix(config):
    return f"costs/{cost_log.town_prefix(config)}/"


def stored_ledger(bucket, config):
    return json.loads(bucket.objects[prefix(config) + "summary-costs.json"])


def test_with_the_keys_the_costs_go_to_the_private_store_and_nothing_to_git(tmp_path, bucket):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=3, now=FETCHED_AT)
    logged = bucket.lines(prefix(config) + f"{MONTH}/")
    assert len(logged) == 3 and {e["kind"] for e in logged} == {"summary"}
    # One object for the run, named for it, beside the ledger and the mark that the town's log has begun.
    names = sorted(k.removeprefix(prefix(config)) for k in bucket.objects)
    assert len(names) == 3 and names[0].startswith(f"{MONTH}/2") and names[1:] == ["started", "summary-costs.json"]
    assert stored_ledger(bucket, config)[MONTH] == {"cost": round(3 * REQUEST, 4), "documents": 3}
    # Nothing about costs left in the data folder but the .gitignore: the run's copy went once it was uploaded.
    assert not (tmp_path / summarize.LEDGER).exists()
    assert [p.name for p in (tmp_path / "costs").rglob("*") if p.is_file()] == [".gitignore"]
    # Each line was uploaded as it was answered, with the month's row.
    assert bucket.puts.count(prefix(config) + names[0]) >= 3 and bucket.puts.count(prefix(config) + names[2]) >= 3
    # The next run reads the month from the store and goes on from it, in an object of its own.
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert stored_ledger(bucket, config)[MONTH] == {"cost": round(4 * REQUEST, 4), "documents": 4}
    assert len([k for k in bucket.objects if k.startswith(prefix(config) + f"{MONTH}/")]) == 2


def test_a_run_stopped_with_the_private_store_has_uploaded_every_request_but_the_one_in_flight(tmp_path, bucket):
    config = with_minutes(tmp_path)
    with pytest.raises(Killed):
        summarize.run(config, stopped_after(FakeAnthropic(), 4), tmp_path, limit=50, now=FETCHED_AT)
    # Lost with its machine, the run's copy isn't needed: the store has every line.
    assert len(bucket.lines(prefix(config))) == 4
    assert stored_ledger(bucket, config)[MONTH] == {"cost": round(4 * REQUEST, 4), "documents": 4}
    shutil.rmtree(tmp_path / "costs")
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    assert stored_ledger(bucket, config)[MONTH] == {"cost": round(5 * REQUEST, 4), "documents": 5}


def test_a_copy_that_couldnt_be_uploaded_is_uploaded_by_the_next_run(tmp_path, bucket, capsys):
    config = with_minutes(tmp_path)
    # The store opens, then can't be reached for the rest of the run.
    log = summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT, config)
    bucket.failing = None
    assert log.add("summary", "k1", REQUEST, doc="d1") and log.add("summary", "k2", REQUEST, doc="d2")
    log.close()
    out = capsys.readouterr().out
    # Said once, with no amount.
    assert out.count("::warning::cost log: the private store couldn't be written") == 1 and "$" not in out
    # Kept on this machine, on disk.
    (copy,) = (tmp_path / "costs" / "pending" / MONTH).glob("*.jsonl")
    assert [json.loads(line)["key"] for line in copy.read_text().splitlines()] == ["k1", "k2"]
    assert bucket.lines(prefix(config)) == []
    # The next run on this machine (the job's next step) uploads it before it reads the month.
    bucket.failing = 0
    log = summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT, config)
    assert log.row == {"cost": round(2 * REQUEST, 4), "documents": 2} and not copy.exists()
    assert [e["key"] for e in bucket.lines(prefix(config))] == ["k1", "k2"]
    # A key in an uploaded copy isn't counted again.
    assert log.add("summary", "k1", REQUEST, doc="d1") is False


def test_a_failed_upload_is_tried_again_on_the_next_line(tmp_path, bucket):
    config = with_minutes(tmp_path)
    log = summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT, config)
    bucket.failing = 1
    log.add("summary", "k1", REQUEST, doc="d1")
    assert bucket.lines(prefix(config)) == []
    log.add("summary", "k2", REQUEST, doc="d2")
    assert [e["key"] for e in bucket.lines(prefix(config))] == ["k1", "k2"]
    assert stored_ledger(bucket, config)[MONTH]["documents"] == 2


def test_a_batch_collected_twice_with_the_private_store_is_counted_once(tmp_path, bucket, monkeypatch):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    first(config, client, tmp_path)
    save, saved = summarize.save_record, []

    def stopped_at_the_fourth(data_dir, sha, record):
        if len(saved) == 3:
            raise Killed()
        saved.append(sha)
        save(data_dir, sha, record)
    monkeypatch.setattr(summarize, "save_record", stopped_at_the_fourth)
    with pytest.raises(Killed):
        collect(config, client, tmp_path)
    monkeypatch.setattr(summarize, "save_record", save)
    summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT, clock=lambda: FETCHED_AT, sleep=lambda s: None)
    summaries = [e for e in cost_log.read_month(cost_log.open_store(config, tmp_path), MONTH) if e["kind"] == "summary"]
    assert len(summaries) == 16 and stored_ledger(bucket, config)[MONTH]["documents"] == 16


def test_the_ledger_from_git_is_imported_once_and_the_month_goes_on(tmp_path, monkeypatch):
    """The move to the private store: the network's budget step imports each town's summary-costs.json
    before any town runs, the town's first run seeds its log from it and deletes the file (so its data
    commit takes it out of git), and no month's total is lost."""
    data = tmp_path / "data"
    config, row = old_ledger(data, MONTH)
    bucket = private(monkeypatch)
    total = summarize.month_cost({MONTH: row}, MONTH)
    root = network_root(tmp_path, data, '[storage]\nprefix = "a-ma"\n')
    data = root / "towns" / "a-ma" / "data"
    config["storage"] = {**config.get("storage", {}), "prefix": "a-ma"}
    legacy = json.loads((data / summarize.LEDGER).read_text())
    # The budget, before any town runs: the store had nothing, so the file's ledger is imported and counted.
    budget = network.summary_budget(root, 50.0, 1, FETCHED_AT.date())
    assert budget["spent"] == round(total, 2) and stored_ledger(bucket, config) == legacy
    # Imported once: a second read doesn't replace what's in the store.
    bucket.objects[prefix(config) + "summary-costs.json"] = json.dumps({**legacy, "2026-07": {"cost": 1.0}}).encode()
    assert network.summary_budget(root, 50.0, 1, FETCHED_AT.date())["spent"] == round(total, 2)
    # The town's first run with the store: seeded from the imported row, the file deleted, the month carried on.
    summarize.run(config, FakeAnthropic(), data, limit=1, now=FETCHED_AT)
    assert not (data / summarize.LEDGER).exists()
    assert (prefix(config) + f"{MONTH}/seed.jsonl") in bucket.objects and (prefix(config) + "started") in bucket.objects
    after = stored_ledger(bucket, config)
    assert after["2026-08"] == {"cost": 9.0, "documents": 99} and after["2026-07"] == {"cost": 1.0}
    assert summarize.month_cost(after, MONTH) == pytest.approx(total + REQUEST + TRANSLATION + REVIEW, abs=1e-4)
    assert network.summary_budget(root, 50.0, 1, FETCHED_AT.date())["spent"] == round(summarize.month_cost(after, MONTH), 2)
    assert summarize.ledger_differences(data, summarize.summary_settings(config),
                                        store=cost_log.open_store(config, data)) == []
    # Seeded once.
    monkeypatch.setattr(summarize, "saved_summaries", lambda data_dir: pytest.fail("recounted"))
    summarize.run(config, FakeAnthropic(), data, limit=1, now=FETCHED_AT)
    assert stored_ledger(bucket, config)[MONTH]["documents"] == row["documents"] + 2


def test_a_town_run_with_the_keys_before_the_import_seeds_from_its_own_file(tmp_path, monkeypatch):
    config, row = old_ledger(tmp_path, MONTH)
    bucket = private(monkeypatch)
    summarize.open_log(tmp_path, summarize.summary_settings(config), FETCHED_AT, config)
    assert stored_ledger(bucket, config)[MONTH] == row and stored_ledger(bucket, config)["2026-08"]["documents"] == 99
    assert not (tmp_path / summarize.LEDGER).exists()


def test_the_store_cant_be_read_and_nothing_is_spent(tmp_path, bucket, monkeypatch):
    config = with_minutes(tmp_path)

    def unreachable(**kwargs):
        raise ConnectionError("no route")
    monkeypatch.setattr(bucket, "list_objects_v2", unreachable)
    client = FakeAnthropic()
    with pytest.raises(cost_log.StoreError, match="couldn't be listed"):
        summarize.run(config, client, tmp_path, limit=5, now=FETCHED_AT)
    assert client.calls == []



def test_a_store_that_cant_be_read_fails_the_step_without_its_details(tmp_path, bucket, monkeypatch, capsys):
    with_minutes(tmp_path)

    def unreachable(**kwargs):
        raise ConnectionError("https://account.example/private-bucket unreachable")
    monkeypatch.setattr(bucket, "list_objects_v2", unreachable)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(sys, "argv", ["summarize", "--data", str(tmp_path)])
    assert summarize.main() == 1
    out = capsys.readouterr().out
    assert "::error::the private store couldn't be listed (ConnectionError); nothing summarized" in out
    assert "account.example" not in out


# The budget, as the network's run log shows it: shares and fractions, never an amount.

def test_the_month_has_its_own_budget_when_one_is_named():
    env = {"SUMMARY_BUDGET": "40", "SUMMARY_BUDGET_MONTH": "2026-09=70, 2026-11=55"}
    assert cost_log.monthly_budget("2026-09", env) == 70.0 and cost_log.monthly_budget("2026-11", env) == 55.0
    assert cost_log.monthly_budget("2026-10", env) == 40.0 and cost_log.monthly_budget("2026-10", {}) is None


def test_a_towns_share_is_made_dollars_with_the_budget(monkeypatch):
    monkeypatch.setenv("SUMMARY_BUDGET", "40")
    monkeypatch.setenv("SUMMARY_BUDGET_MONTH", "2026-09=70")
    monkeypatch.setenv(summarize.ALLOWANCE_SHARE_ENV, str(round(1.23 / 70, 10)))
    monkeypatch.setenv(summarize.BACKLOG_SHARE_ENV, str(round(0.4 / 70, 10)))
    assert summarize.allowances(MONTH) == (1.23, 0.4, None)
    assert summarize.allowances("2026-10") == (0.7, 0.22, None)
    # A share without the budget: nothing but the town's own text is drafted, and the log says why.
    monkeypatch.delenv("SUMMARY_BUDGET")
    monkeypatch.delenv("SUMMARY_BUDGET_MONTH")
    allowance, backlog, warning = summarize.allowances(MONTH)
    assert (allowance, backlog) == (0.0, 0.0) and "SUMMARY_BUDGET" in warning
    # Without shares, the dollars, as a run on its own may give them.
    monkeypatch.delenv(summarize.ALLOWANCE_SHARE_ENV)
    monkeypatch.delenv(summarize.BACKLOG_SHARE_ENV)
    monkeypatch.setenv(summarize.ALLOWANCE_ENV, "2.5")
    assert summarize.allowances(MONTH) == (2.5, None, None)


def test_the_shares_make_the_budgets_allowances(tmp_path, monkeypatch):
    data = tmp_path / "data"
    config = with_minutes(data)
    summarize.run(config, FakeAnthropic(), data, limit=3, now=FETCHED_AT)
    root = network_root(tmp_path, data)
    budget = network.summary_budget(root, 7.0, 2, FETCHED_AT.date())
    monkeypatch.setenv("SUMMARY_BUDGET", "7")
    monkeypatch.setenv(summarize.ALLOWANCE_SHARE_ENV, str(budget["allowance_share"]))
    monkeypatch.setenv(summarize.BACKLOG_SHARE_ENV, str(budget["backlog_share"]))
    assert summarize.allowances(MONTH)[:2] == (budget["allowance"], budget["backlog_allowance"])
    assert budget["used_share"] == round(3 * REQUEST / 7, 4) and budget["within_budget"]


def test_the_budget_command_prints_no_amount(tmp_path):
    data = tmp_path / "data"
    config = with_minutes(data)
    summarize.run(config, FakeAnthropic(), data, limit=3, now=FETCHED_AT)
    root = network_root(tmp_path, data)
    env = {k: v for k, v in __import__("os").environ.items() if k not in cost_log.PRIVATE_KEYS}
    command = [sys.executable, "-m", "pipeline.network", "budget", "--root", str(root), "--towns-in-run", "2"]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                          env={**env, "SUMMARY_BUDGET": "137", "SUMMARY_BUDGET_MONTH": ""})
    assert done.returncode == 0, done.stderr
    printed = json.loads(done.stdout)
    assert sorted(printed) == sorted(network.SHARE_FIELDS) and 0 < printed["allowance_share"] < 1
    assert "137" not in done.stdout + done.stderr and "$" not in done.stdout + done.stderr
    assert "of the month's summary budget used" in done.stderr
    # Without a budget, it stops rather than guess.
    unset = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env)
    assert unset.returncode != 0 and "SUMMARY_BUDGET" in unset.stderr


def test_what_a_run_prints_leaves_out_what_it_cost(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(sys, "argv", ["summarize", "--data", str(tmp_path)])
    monkeypatch.setattr(summarize, "run", lambda *a, **k: {
        "summarized": 1, "failed": 0, "errors": [], "stopped": None, "estimated_cost": 1.23, "month_cost": 45.6})
    assert summarize.main() == 0
    out = capsys.readouterr().out
    assert '"summarized": 1' in out and "cost" not in out and "1.23" not in out and "45.6" not in out


def test_only_the_steps_with_costs_get_the_private_store_and_the_budget():
    env = {k: "x" for k in (*cost_log.PRIVATE_KEYS, *cost_log.BUDGET_KEYS, "ANTHROPIC_API_KEY", "SITES_BUCKET")}
    assert set(network.keyed(env)) == set()
    assert set(network.keyed(env, network.FETCH_KEYS)) == set(env) - {"SITES_BUCKET"}
    assert set(network.keyed(env, network.COLLECT_KEYS)) == set(env) - {"SITES_BUCKET"}
    assert set(network.keyed(env, network.PRIVATE_KEYS)) == set(cost_log.PRIVATE_KEYS)
    # Within the fetch, the summaries alone.
    for source in update.SOURCES:
        given = set(cost_log.PRIVATE_KEYS + cost_log.BUDGET_KEYS) & set(source.secrets)
        assert given == (set(cost_log.PRIVATE_KEYS + cost_log.BUDGET_KEYS) if source.module == "pipeline.summarize"
                         else set())
