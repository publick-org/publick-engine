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
