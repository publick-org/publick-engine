"""Running many towns from one repository: planning batches, running a town, and the run's report."""

import json
import os
import subprocess
import sys
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


def test_loads_with_the_standard_library_only():
    """The network workflow's plan and home jobs run this module without installing the engine's
    requirements, so importing it mustn't need any of them (v1.31.0 broke both jobs so)."""
    names = [line.split("==")[0].strip().replace("-", "_").lower()
             for line in (network.ENGINE_DIR / "requirements.txt").read_text().splitlines() if line.strip()]
    blocker = ("import sys\n"
               f"BLOCKED = {set(names)!r}\n"
               "class Block:\n"
               "    def find_spec(self, name, path=None, target=None):\n"
               "        if name.split('.')[0].lower() in BLOCKED:\n"
               "            raise ModuleNotFoundError(name + ' is not installed in this job')\n"
               "sys.meta_path.insert(0, Block())\n"
               "import pipeline.network\n")
    result = subprocess.run([sys.executable, "-c", blocker], capture_output=True, text=True,
                            cwd=network.ENGINE_DIR, env={**os.environ, "PYTHONPATH": str(network.ENGINE_DIR)})
    assert result.returncode == 0, result.stderr


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
                                          "Publish site", "Check live site"]
    assert result["ok"] and result["deployed"] and not result["stale"]
    env = calls[0]["env"]
    assert env["TOWN"] == "gloucester" and env["PUBLICK_TOWN_DIR"] == str(root / "towns" / "gloucester-ma")
    assert str(network.ENGINE_DIR) in env["PYTHONPATH"].split(":")
    assert "GITHUB_STEP_SUMMARY" not in env
    assert calls[3]["env"]["PUBLICK_SITE_DIR"] == str(root / "towns" / "gloucester-ma" / "_site")
    assert json.loads((tmp_path / "reports" / "gloucester-ma.json").read_text()) == result
    assert calls[0]["cmd"][calls[0]["cmd"].index("--sources") + 1] == "all"
    assert calls[3]["cmd"][calls[3]["cmd"].index("-n") + 1] == "auto", "the browser checks run on every core"


def test_each_step_gets_only_the_keys_it_uses(tmp_path, steps, monkeypatch):
    calls, _ = steps
    for k in network.FETCH_KEYS + network.PUBLISH_KEYS:
        monkeypatch.setenv(k, "x")
    network.run_town(make_root(tmp_path), "gloucester-ma", fetch=True, deploy=True, reports=None)
    given = {c["name"]: {k for k in network.FETCH_KEYS + network.PUBLISH_KEYS if k in c["env"]} for c in calls}
    assert given == {"Fetch new data": set(network.FETCH_KEYS), "Check data freshness": set(), "Build site": set(),
                     "Check site": set(), "Publish site": set(network.PUBLISH_KEYS), "Check live site": set()}
    # Everything else is passed on.
    assert all(c["env"]["TOWN"] and c["env"]["PATH"] for c in calls)


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
    assert record["fact_checks"]["summaries"] == 0
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


def test_a_site_the_worker_doesnt_serve_isnt_counted_as_published(tmp_path, steps):
    calls, failing = steps
    failing.add("Check live site")
    result = network.run_town(make_root(tmp_path), "gloucester-ma", fetch=False, deploy=True, reports=None)
    assert calls[-1]["cmd"][2:4] == ["pipeline.deploy", "check"]
    assert not result["ok"] and not result["deployed"]
    failing.clear()
    failing.add("Publish site")
    calls.clear()
    network.run_town(make_root(tmp_path / "again"), "gloucester-ma", fetch=False, deploy=True, reports=None)
    assert "Check live site" not in [c["name"] for c in calls], "nothing published, nothing to check"


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
    published = {"town": "lynn", "folder": "lynn-ma", "ok": True, "stale": False, "deployed": True,
                 "fetched": True, "update": {"steps": []}, "steps": [{"name": "Publish site", "ok": True}]}
    (reports / "salem-ma.json").write_text(json.dumps(fetched))
    (reports / "lynn-ma.json").write_text(json.dumps(published))
    table, ok = network.report(reports)
    assert ok and "| salem-ma | **failed** | **stale** | Build site |" in table
    assert "**1 not published**: salem-ma" in table


def test_a_fetching_run_that_published_no_town_fails(tmp_path):
    """The 2026-10-02 daily run finished green with 3 of 3 towns not published."""
    reports = tmp_path / "reports"
    reports.mkdir()
    for town in ("gloucester-ma", "malden-ma"):
        (reports / f"{town}.json").write_text(json.dumps({
            "town": town, "folder": town, "ok": False, "stale": False, "deployed": False, "fetched": True,
            "update": {"steps": []}, "steps": [{"name": "Check site", "ok": False}]}))
    table, ok = network.report(reports)
    assert not ok and "**2 not published**" in table and "No town in this run was published" in table


def test_a_run_that_publishes_without_fetching_updates_the_run_record(tmp_path, steps):
    """A daily run that couldn't publish, then a fix published by a push: the run record says
    the town is published, and when, so the status page and the alert don't wait for the next day."""
    _, failing = steps
    root = make_root(tmp_path)
    (root / "towns" / "gloucester-ma" / "data").mkdir()
    failing.add("Check site")
    daily = network.run_town(root, "gloucester-ma", fetch=True, deploy=True, reports=None)
    record_path = root / "towns" / "gloucester-ma" / "data" / network.RUN_RECORD
    assert not daily["deployed"] and json.loads(record_path.read_text())["last_good_at"] is None
    failing.clear()
    push = network.run_town(root, "gloucester-ma", fetch=False, deploy=True, reports=None)
    record = json.loads(record_path.read_text())
    assert record["deployed"] and record["ok"] and record["published_at"] == push["finished_at"]
    assert record["last_good_at"] == push["finished_at"] and record["finished_at"] == daily["finished_at"]
    assert [s["name"] for s in record["steps"]] == ["Fetch new data", "Build site", "Check site", "Publish site",
                                                    "Check live site"]
    behind = network.behind(root, at=network.datetime.fromisoformat(push["finished_at"]))
    assert "gloucester-ma" not in [r["folder"] for r in behind]


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


def test_the_alert_comments_only_on_towns_newly_behind(tmp_path):
    """The issue is edited after each run, which sends no email; a comment names the towns that
    weren't in it before, and nothing is said about a town already listed or caught up."""
    rows = [{"folder": "manchester-nh", "last_good_at": None, "last_run_at": None, "problems": ["Check site"]},
            {"folder": "salem-ma", "last_good_at": None, "last_run_at": None, "problems": []}]
    before = network.behind_text(rows[:1] + [{**rows[1], "folder": "beverly-ma"}], 30)
    assert network.newly_behind_text(rows, before) == "Newly needing attention:\n\n- **salem-ma**: no run since\n"
    assert network.newly_behind_text(rows, network.behind_text(rows, 30)) == ""
    assert network.newly_behind_text([], before) == ""
    # The command the workflow runs, with the issue's text saved to a file.
    saved = tmp_path / "issue.md"
    saved.write_text(before)
    root = make_root(tmp_path)
    out = subprocess.run([sys.executable, "-m", "pipeline.network", "behind", "--root", str(root), "--new-since", str(saved)],
                         capture_output=True, text=True, check=True).stdout
    assert out.startswith("Newly needing attention:") and "**salem-ma**" in out and "manchester-nh" not in out


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


def test_run_record_counts_what_the_fact_check_holds_back(tmp_path):
    summaries = tmp_path / "summaries"
    (summaries / "es").mkdir(parents=True)

    def check(result, *problems, source="pdf"):
        return {"version": 2, "source": source, "result": result, "problems": list(problems)}

    records = {
        "ok": {"kind": "minutes", "headline": "Approved the budget.", "summary": "", "decisions": ["Approved it, 3-2."],
               "fact_check": check("ok", {"field": "decisions", "entry": 1, "kind": "tally", "what": "3-2"})},
        "failed": {"kind": "minutes", "headline": "Approved a grant.", "summary": "",
                   "decisions": ["Accepted $500.", "Accepted $900.", "Approved the minutes."],
                   "fact_check": check("failed", {"field": "decisions", "entry": 1, "kind": "number", "what": "500"},
                                       {"field": "decisions", "entry": 2, "kind": "number", "what": "900"})},
        "weak": {"kind": "agenda", "headline": "Kowalczyk's permit.", "summary": "", "items": [],
                 "fact_check": check("weak", {"field": "headline", "kind": "name", "what": "Kowalczyk"}, source="ai")},
        "not yet": {"kind": "agenda", "headline": "A hearing.", "summary": "", "items": []},
        "not minutes": {"kind": "minutes", "is_minutes": False, "headline": "", "summary": "", "decisions": []},
    }
    for name, record in records.items():
        (summaries / f"{name}.json").write_text(json.dumps(record))
    # Translations are in their own folder, and aren't counted again.
    (summaries / "es" / "failed.json").write_text(json.dumps({"headline": "Aprobó una subvención."}))
    assert network.fact_checks(tmp_path) == {
        "summaries": 4, "ok": 1, "failed": 1, "weak": 1, "unchecked": 0, "not_yet": 1,
        "held_back": 1, "entries_not_shown": 2, "vote_counts_left_out": 1}
    assert network.fact_checks(tmp_path / "nothing")["summaries"] == 0
