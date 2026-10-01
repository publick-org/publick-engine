"""Running many towns from one repository: planning batches, running a town, and the run's report."""

import json
import subprocess
from pathlib import Path

import pytest

from pipeline import network


def make_root(tmp_path, towns=("gloucester-ma", "manchester-nh", "salem-ma")):
    for name in towns:
        (tmp_path / "towns" / name / "config").mkdir(parents=True)
        (tmp_path / "towns" / name / "config" / f"{name.split('-')[0]}.toml").write_text("")
    return tmp_path


def batches(result):
    return [b["towns"].split() for b in result["include"]]


def test_finds_towns_and_their_config_names(tmp_path):
    root = make_root(tmp_path)
    (root / "towns" / "not-a-town").mkdir()
    assert network.town_dirs(root) == ["gloucester-ma", "manchester-nh", "salem-ma"]
    assert network.slug(root, "gloucester-ma") == "gloucester"


def test_plan_batches_every_town(tmp_path):
    root = make_root(tmp_path)
    assert batches(network.plan(root, batch_size=2)) == [["gloucester-ma", "manchester-nh"], ["salem-ma"]]
    assert network.plan(root, batch_size=2)["include"][0]["name"] == "gloucester-ma +1"
    assert network.plan(make_root(tmp_path / "empty", ()))["include"] == []


def test_slots_split_towns_stably(tmp_path):
    towns = [f"town{i}-ma" for i in range(40)]
    root = make_root(tmp_path, towns)
    runs = [sum(batches(network.plan(root, slots=4, run_slot=k, batch_size=100)), []) for k in range(4)]
    assert sorted(sum(runs, [])) == sorted(towns)
    assert all(runs), "each of the day's runs has some towns"
    # A town's slot depends only on its own name, so adding towns never moves it.
    assert network.slot("gloucester-ma", 4) == network.slot("gloucester-ma", 4)
    assert all(network.slot(t, 4) == k for k, run in enumerate(runs) for t in run)


def test_plan_only_named_towns(tmp_path):
    root = make_root(tmp_path)
    assert batches(network.plan(root, only=["salem-ma"])) == [["salem-ma"]]
    with pytest.raises(SystemExit, match="nowhere-ma"):
        network.plan(root, only=["nowhere-ma"])


def test_changes_rebuild_the_towns_they_touch(tmp_path):
    towns = ["gloucester-ma", "manchester-nh", "salem-ma"]
    assert network.changed_towns(["towns/salem-ma/config/salem.toml"], towns) == ["salem-ma"]
    assert network.changed_towns(["towns/salem-ma/site/static/share/salem.png", "README.md"], towns) == ["salem-ma"]
    assert network.changed_towns(["towns/salem-ma/data/311/requests.json"], towns) == []
    assert network.changed_towns(["engine-version"], towns) == towns
    assert network.changed_towns([".github/workflows/network.yml"], towns) == towns
    assert network.changed_towns(["home/index.html"], towns) == []
    root = make_root(tmp_path)
    assert batches(network.plan(root, changed=["towns/salem-ma/config/salem.toml"])) == [["salem-ma"]]


@pytest.fixture
def steps(monkeypatch):
    """Record each step instead of running it; a step named in `failing` fails."""
    calls, failing = [], set()

    def fake(name, cmd, env, cwd, timeout):
        calls.append({"name": name, "cmd": cmd, "env": env, "cwd": cwd})
        ok = name not in failing
        return {"name": name, "ok": ok, "error": None if ok else "exited with status 1", "seconds": 0.0}

    monkeypatch.setattr(network, "step", fake)
    return calls, failing


def test_run_builds_checks_and_publishes_a_town(tmp_path, steps):
    calls, _ = steps
    root = make_root(tmp_path)
    result = network.run_town(root, "gloucester-ma", fetch=True, deploy=True, reports=tmp_path / "reports")
    assert [c["name"] for c in calls] == ["Fetch new data", "Check data freshness", "Build site", "Check site",
                                          "Publish site"]
    assert result["ok"] and result["deployed"] and not result["stale"]
    env = calls[0]["env"]
    assert env["TOWN"] == "gloucester" and env["PUBLICK_TOWN_DIR"] == str(root / "towns" / "gloucester-ma")
    assert str(network.ENGINE_DIR) in env["PYTHONPATH"].split(":")
    assert "GITHUB_STEP_SUMMARY" not in env
    assert calls[3]["env"]["PUBLICK_SITE_DIR"] == str(root / "towns" / "gloucester-ma" / "_site")
    assert json.loads((tmp_path / "reports" / "gloucester-ma.json").read_text()) == result
    assert calls[0]["cmd"][calls[0]["cmd"].index("--sources") + 1] == "all"
    assert calls[3]["cmd"][calls[3]["cmd"].index("-n") + 1] == "auto", "the browser checks run on every core"


def test_a_run_can_fetch_only_some_sources(tmp_path, steps, monkeypatch):
    calls, _ = steps
    root = make_root(tmp_path)
    monkeypatch.setattr("sys.argv", ["network", "run", "--root", str(root), "--towns", "salem-ma", "--fetch",
                                     "--sources", "figures"])
    network.main()
    fetch = calls[0]["cmd"]
    assert fetch[fetch.index("--sources") + 1] == "figures"


def test_a_fetching_run_records_its_result_for_the_status_page(tmp_path, steps, monkeypatch):
    calls, failing = steps
    rows = [{"label": "311 requests", "updated_at": "2026-09-28T23:45:55-04:00", "max_days": 2, "stale": False},
            {"label": "Meeting summaries", "updated_at": None, "max_days": 2, "stale": True,
             "waiting": [f"agenda {i}" for i in range(25)]}]
    fake = network.step

    def step(name, cmd, env, cwd, timeout):
        if name == "Check data freshness":
            Path(cmd[cmd.index("--report") + 1]).write_text(json.dumps(rows))
        return fake(name, cmd, env, cwd, timeout)

    monkeypatch.setattr(network, "step", step)
    failing.update({"Check data freshness", "Publish site"})
    root = make_root(tmp_path)
    (root / "engine-version").write_text("v1.5.0\n")
    town = root / "towns" / "gloucester-ma"
    (town / "data").mkdir()
    result = network.run_town(root, "gloucester-ma", fetch=True, deploy=True, reports=None)
    record = json.loads((town / "data" / network.RUN_RECORD).read_text())
    assert record == result
    assert record["engine"] == "v1.5.0" and record["started_at"] <= record["finished_at"]
    assert record["stale"] and not record["ok"] and not record["deployed"]
    summaries = record["sources"][1]
    assert len(summaries["waiting"]) == network.WAITING_KEPT and summaries["waiting_count"] == 25
    assert record["sources"][0] == rows[0]
    assert not (town / ".freshness-report.json").exists()


def test_a_run_without_fetching_records_nothing(tmp_path, steps):
    root = make_root(tmp_path)
    (root / "towns" / "gloucester-ma" / "data").mkdir()
    result = network.run_town(root, "gloucester-ma", fetch=False, deploy=False, reports=None)
    assert result["engine"] is None and result["sources"] is None
    assert not (root / "towns" / "gloucester-ma" / "data" / network.RUN_RECORD).exists()


def test_a_town_that_fails_its_checks_is_not_published(tmp_path, steps):
    calls, failing = steps
    failing.add("Check site")
    result = network.run_town(make_root(tmp_path), "gloucester-ma", fetch=False, deploy=True, reports=None)
    assert [c["name"] for c in calls] == ["Build site", "Check site"]
    assert not result["ok"] and not result["deployed"]


def test_a_failed_build_is_not_checked(tmp_path, steps):
    calls, failing = steps
    failing.add("Build site")
    network.run_town(make_root(tmp_path), "gloucester-ma", fetch=False, deploy=True, reports=None)
    assert [c["name"] for c in calls] == ["Build site"]


def test_stale_data_is_reported_but_still_published(tmp_path, steps):
    _, failing = steps
    failing.add("Check data freshness")
    result = network.run_town(make_root(tmp_path), "gloucester-ma", fetch=True, deploy=True, reports=None)
    assert result["ok"] and result["stale"] and result["deployed"]


def test_failed_update_leaves_311_data_as_committed(tmp_path, steps):
    _, failing = steps
    root = make_root(tmp_path)
    data = root / "towns" / "gloucester-ma" / "data" / "311"
    data.mkdir(parents=True)
    (data / "requests.json").write_text("committed")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run([*git, "commit", "-qm", "data"], cwd=root, check=True)
    (data / "requests.json").write_text("half-updated")
    (data / "new.json").write_text("partial")

    failing.add("Fetch new data")
    result = network.run_town(root, "gloucester-ma", fetch=True, deploy=False, reports=None)
    assert not result["ok"]
    assert (data / "requests.json").read_text() == "committed" and not (data / "new.json").exists()


def test_run_continues_after_a_failing_town(tmp_path, steps, monkeypatch):
    calls, failing = steps
    failing.add("Build site")
    root = make_root(tmp_path)
    monkeypatch.setattr("sys.argv", ["network", "run", "--root", str(root), "--towns", "gloucester-ma,salem-ma"])
    assert network.main() == 1
    assert [c["env"]["TOWN"] for c in calls] == ["gloucester", "salem"]


def test_report_lists_every_town_and_fails_once(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    good = {"town": "gloucester", "folder": "gloucester-ma", "ok": True, "stale": False, "deployed": True,
            "steps": [{"name": "Build site", "ok": True}],
            "update": {"steps": [{"name": "Fetch tax bill", "ok": False}]}}
    bad = {"town": "salem", "folder": "salem-ma", "ok": False, "stale": True, "deployed": False, "update": None,
           "steps": [{"name": "Build site", "ok": False}]}
    (reports / "gloucester-ma.json").write_text(json.dumps(good))
    table, ok = network.report(reports)
    assert ok and "| gloucester-ma | published | fresh | Fetch tax bill (fetch) |" in table
    (reports / "salem-ma.json").write_text(json.dumps(bad))
    table, ok = network.report(reports)
    assert not ok and "2 towns; 1 need attention" in table
    assert "| salem-ma | **failed** | **stale** | Build site |" in table


def test_real_step_reports_exit_status_and_timeout(tmp_path):
    env = {"TOWN": "t"}
    assert network.step("ok", ["true"], env, tmp_path, 10)["ok"]
    assert network.step("fail", ["false"], env, tmp_path, 10)["error"] == "exited with status 1"
    assert network.step("hang", ["sleep", "30"], env, tmp_path, 0.5)["error"] == "stopped after 0.5 seconds"


def test_engine_dir_is_this_checkout():
    assert (network.ENGINE_DIR / "pipeline" / "network.py").exists()
    assert Path(network.__file__).resolve().parent.parent == network.ENGINE_DIR


def test_daily_runs_sample_the_browser_checks(tmp_path, steps, monkeypatch):
    calls, _ = steps
    monkeypatch.setenv("PUBLICK_CHECK_PAGES", "sample")  # left over in the environment: must not sample a full run
    root = make_root(tmp_path)
    network.run_town(root, "gloucester-ma", fetch=False, deploy=False, reports=None)
    network.run_town(root, "salem-ma", fetch=False, deploy=False, reports=None, sample_checks=True)
    checks = [c["env"] for c in calls if c["name"] == "Check site"]
    assert "PUBLICK_CHECK_PAGES" not in checks[0]
    assert checks[1]["PUBLICK_CHECK_PAGES"] == "sample"


def test_a_fetching_run_succeeds_when_a_town_fails(tmp_path, steps, monkeypatch, capsys):
    _, failing = steps
    failing.add("Build site")
    root = make_root(tmp_path)
    monkeypatch.setattr("sys.argv", ["network", "run", "--root", str(root), "--towns", "salem-ma", "--fetch"])
    # The data is still committed, and the daily alert lists the town; the run doesn't fail for it.
    assert network.main() == 0
    assert "::warning::salem-ma: Build site" in capsys.readouterr().out


def test_report_fails_only_for_a_town_that_failed_without_fetching(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    fetched = {"town": "salem", "folder": "salem-ma", "ok": False, "stale": True, "deployed": False,
               "fetched": True, "update": {"steps": []}, "steps": [{"name": "Build site", "ok": False}]}
    (reports / "salem-ma.json").write_text(json.dumps(fetched))
    table, ok = network.report(reports)
    assert ok and "| salem-ma | **failed** | **stale** | Build site |" in table


def test_run_record_keeps_the_last_good_update_and_the_datas_size(tmp_path, steps):
    _, failing = steps
    root = make_root(tmp_path)
    data = root / "towns" / "gloucester-ma" / "data"
    data.mkdir()
    (data / "a.json").write_text("x" * 100)
    first = network.run_town(root, "gloucester-ma", fetch=True, deploy=True, reports=None)
    assert first["last_good_at"] == first["finished_at"]
    assert first["data_bytes"] == 100 and first["data_bytes_added"] == 0
    failing.add("Check site")
    second = network.run_town(root, "gloucester-ma", fetch=True, deploy=True, reports=None)
    assert not second["deployed"] and second["last_good_at"] == first["finished_at"]
    # The first run's record counts as data too; this run's steps (stand-ins) added nothing.
    assert second["data_bytes"] > 100 and second["data_bytes_added"] == 0


def write_record(root, name, **record):
    path = root / "towns" / name / "data" / network.RUN_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))


def test_behind_lists_towns_without_a_good_update(tmp_path):
    from datetime import datetime, timezone
    root = make_root(tmp_path)
    at = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    write_record(root, "gloucester-ma", finished_at="2026-10-02T09:00:00+00:00", deployed=True, stale=False,
                 fetched=True, steps=[])
    write_record(root, "manchester-nh", finished_at="2026-10-02T10:00:00+00:00", deployed=False, stale=True,
                 fetched=True, last_good_at="2026-09-30T10:00:00+00:00",
                 steps=[{"name": "Check site", "ok": False}],
                 sources=[{"label": "Meetings", "stale": True}, {"label": "311 requests", "stale": False}])
    rows = network.behind(root, 30, at)
    assert [r["folder"] for r in rows] == ["manchester-nh", "salem-ma"]
    assert rows[0]["problems"] == ["Check site", "Meetings behind"]
    assert rows[1] == {"folder": "salem-ma", "last_good_at": None, "last_run_at": None, "problems": []}
    text = network.behind_text(rows, 30)
    assert "2 of the network's towns need attention" in text and "| salem-ma | never | never | no run since |" in text
    assert network.behind_text([], 30) == ""
    write_record(root, "gloucester-ma", finished_at="2026-10-02T09:00:00+00:00", deployed=True, stale=False,
                 fetched=True, steps=[], failing=["Average tax bill (Mass. DLS)"])
    assert network.behind(root, 30, at)[0]["problems"] == ["Average tax bill (Mass. DLS): checks failing"]


def test_an_older_run_record_counts_as_a_good_update_when_it_was_one(tmp_path):
    record = {"finished_at": "2026-09-29T17:38:19+00:00", "deployed": True, "stale": False, "update": {}}
    assert network.last_good(record) == "2026-09-29T17:38:19+00:00"
    assert network.last_good({**record, "stale": True}) is None


def test_summary_budget_splits_whats_left_of_the_month(tmp_path):
    from datetime import date
    root = make_root(tmp_path)
    for name, cost in (("gloucester-ma", 6.97), ("manchester-nh", 4.5)):
        path = root / "towns" / name / "data" / network.SUMMARY_LEDGER
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"2026-08": {"cost": 30.0}, "2026-10": {"cost": cost, "failed_cost": 0.53}}))
    budget = network.summary_budget(root, 50.0, towns_in_run=2, today=date(2026, 10, 11))
    assert budget["spent"] == 12.53 and budget["left"] == 37.47
    assert budget["allowance"] == 18.73
    # (37.47 - 10 kept for new documents) over 21 days left and 3 towns in the network.
    assert budget["backlog_allowance"] == 0.43
    spent = network.summary_budget(root, 12.0, towns_in_run=1, today=date(2026, 10, 11))
    assert spent["left"] == 0 and spent["allowance"] == 0 and spent["backlog_allowance"] == 0


def test_network_reads_the_ledger_summarize_writes():
    from pipeline import summarize
    assert network.SUMMARY_LEDGER == summarize.LEDGER


def test_a_daily_run_takes_the_towns_that_are_due(tmp_path):
    from datetime import datetime, timezone
    root = make_root(tmp_path)
    at = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    write_record(root, "gloucester-ma", finished_at="2026-10-01T10:00:00+00:00")  # 23 hours ago: due
    write_record(root, "manchester-nh", finished_at="2026-10-02T08:00:00+00:00")  # an hour ago: not due
    # salem-ma has never run: due, and first.
    assert batches(network.plan(root, due_hours=18, at=at)) == [["salem-ma", "gloucester-ma"]]
    # A second start finds nothing more to do once they've run.
    write_record(root, "gloucester-ma", finished_at="2026-10-02T09:30:00+00:00")
    write_record(root, "salem-ma", finished_at="2026-10-02T09:40:00+00:00")
    assert network.plan(root, due_hours=18, at=datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc))["include"] == []
    # Named towns are only taken when due too.
    assert batches(network.plan(root, only=["manchester-nh", "salem-ma"], due_hours=18,
                                at=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc))) == [["manchester-nh", "salem-ma"]]


def test_run_record_counts_boards_and_coming_meetings_for_the_homepage(tmp_path):
    from datetime import date
    meetings = tmp_path / "meetings"
    meetings.mkdir()
    store = {
        "a": {"date": "2026-10-01", "body": "City Council"},
        "b": {"date": "2026-10-01", "body": "Planning Board"},
        "c": {"date": "2026-10-03", "body": "City Council", "status": "cancelled"},
        "d": {"date": "2026-10-05", "body": "Board of Health", "listed": False},
        "e": {"date": "2026-10-14", "body": "Licensing Board"},
        "f": {"date": "2026-10-15", "body": "City Council"},
        "g": {"date": "2026-09-30", "body": "Harbor Commission"},
    }
    (meetings / "meetings.json").write_text(json.dumps(store))
    counts = network.activity(tmp_path, today=date(2026, 10, 1))
    assert counts == {"boards": 5, "meetings_by_date": {"2026-10-01": 2, "2026-10-14": 1}}
    assert network.activity(tmp_path / "nothing") == {"boards": 0, "meetings_by_date": {}}
