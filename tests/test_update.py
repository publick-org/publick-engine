"""The daily update's list of sources, and how a run handles a failing step."""

import json
import re
import sys
from pathlib import Path

import pytest

from pipeline import update

TOWN_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "town.yml"


def workflow_steps() -> list[dict]:
    """The update job's steps in town.yml that run a pipeline command, in order."""
    text = TOWN_WORKFLOW.read_text()
    job = text.split("\n  update:\n", 1)[1].split("\n  build:\n", 1)[0]
    steps = []
    for block in re.split(r"\n      - ", job)[1:]:
        run = re.search(r"run: python -m (pipeline\.\w+)((?: \w+)*) --town \"\$TOWN\"", block)
        if not run:
            continue
        name = re.search(r"name: (.+)", block)
        condition = re.search(r"if: (.+)", block)
        steps.append({
            "name": name.group(1).strip() if name else None,
            "module": run.group(1),
            "args": tuple(run.group(2).split()),
            "if": condition.group(1).strip() if condition else None,
            "continue_on_error": "continue-on-error: true" in block,
            "secrets": tuple(sorted(re.findall(r"(\w+): \$\{\{ secrets\.", block))),
        })
    return steps


def test_town_workflow_runs_the_same_sources():
    """town.yml runs each source as its own step; pipeline.update runs the same list."""
    # town.yml offers all, meetings, and 311; the figures run under meetings, as they always have.
    group_condition = {"meetings": "inputs.sources != '311'", "figures": "inputs.sources != '311'",
                       "311": "inputs.sources != 'meetings'"}
    steps = [s for s in workflow_steps() if s["module"] != "pipeline.freshness"]
    expected = [{"name": s.name, "module": s.module, "args": s.args, "if": group_condition[s.group],
                 "continue_on_error": not s.required, "secrets": tuple(sorted(s.secrets))}
                for s in update.SOURCES]
    assert steps == expected


@pytest.fixture
def fake_steps(monkeypatch, tmp_path):
    """Replace each step's command with a small script: 'ok', 'fail', 'hang', or 'env' (records its environment)."""
    behaviour = {}

    def command(source, town):
        kind = behaviour.get(source.name, "ok")
        script = {
            "ok": "pass",
            "fail": "import sys; sys.exit(3)",
            "hang": "import time; time.sleep(30)",
            "env": f"import json, os; open({str(tmp_path / 'env.json')!r}, 'w').write(json.dumps(dict(os.environ)))",
        }[kind]
        return [sys.executable, "-c", script]

    monkeypatch.setattr(update, "command", command)
    return behaviour


def test_all_steps_run_and_pass(fake_steps):
    result = update.run("gloucester")
    assert result["ok"] and [s["name"] for s in result["steps"]] == [s.name for s in update.SOURCES]


def test_failing_source_does_not_stop_the_rest(fake_steps):
    fake_steps["Fetch meetings"] = "fail"
    result = update.run("gloucester")
    assert result["ok"]
    assert len(result["steps"]) == len(update.SOURCES)
    assert result["steps"][0] == {**result["steps"][0], "ok": False, "error": "exited with status 3"}


def test_failing_required_step_fails_the_run(fake_steps, capsys):
    fake_steps["Compute 311 scorecard"] = "fail"
    assert not update.run("gloucester")["ok"]
    assert "::error::gloucester: Compute 311 scorecard" in capsys.readouterr().out


def test_hung_step_is_stopped(fake_steps):
    fake_steps["Fetch minutes"] = "hang"
    result = update.run("gloucester", timeout=1)
    hung = next(s for s in result["steps"] if s["name"] == "Fetch minutes")
    assert hung["error"] == "stopped after 1 seconds" and hung["seconds"] < 10
    assert result["ok"] and len(result["steps"]) == len(update.SOURCES)


def test_a_town_without_a_source_skips_its_steps_without_starting_them(fake_steps, monkeypatch):
    started = []
    real = update.run_step
    monkeypatch.setattr(update, "run_step", lambda source, town, timeout: started.append(source.name) or real(source, town, timeout))
    from pipeline.config import load_config
    config = load_config("gloucester")
    for table in ("archive", "drive_meetings", "permits", "seeclickfix"):
        del config[table]
    result = update.run("gloucester", config=config)
    skipped = {s["name"]: s["skipped"] for s in result["steps"] if s.get("skipped")}
    assert skipped == {"Fetch School Committee documents": "no [drive_meetings] in the config",
                       "Fetch building permits": "no [permits] in the config",
                       "Fetch 311 requests": "no [seeclickfix] in the config",
                       "Compute 311 scorecard": "no [seeclickfix] in the config"}
    assert result["ok"] and not set(skipped) & set(started)
    assert "Move saved documents to storage" in started
    # Minutes linked from Agenda Center and CivicClerk meetings need no [archive].
    assert "Fetch minutes" in started


def test_sources_pick_a_group(fake_steps):
    only_311 = update.run("gloucester", sources="311")["steps"]
    assert [s["name"] for s in only_311] == ["Fetch 311 requests", "Compute 311 scorecard"]
    meetings = update.run("gloucester", sources="meetings")["steps"]
    assert len(meetings) + len(only_311) == len(update.SOURCES)
    figures = [s["name"] for s in update.run("gloucester", sources="figures")["steps"]]
    assert figures == ["Fetch tax bill", "Fetch unemployment", "Fetch school figures", "Fetch budget figures",
                       "Fetch housing figures", "Fetch building permits"]
    assert set(figures) < {s["name"] for s in meetings}, "meetings is still everything but 311"


def test_each_key_goes_only_to_its_step(fake_steps, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("BLS_API_KEY", "b")
    monkeypatch.setenv("STORAGE_ACCESS_KEY_ID", "c")
    fake_steps["Fetch meetings"] = "env"
    update.run("gloucester", sources="meetings")
    env = json.loads((tmp_path / "env.json").read_text())
    assert "ANTHROPIC_API_KEY" not in env and "BLS_API_KEY" not in env and env["STORAGE_ACCESS_KEY_ID"] == "c"
    fake_steps.clear()
    fake_steps["Summarize agendas"] = "env"
    update.run("gloucester", sources="meetings")
    env = json.loads((tmp_path / "env.json").read_text())
    assert env["ANTHROPIC_API_KEY"] == "a" and "BLS_API_KEY" not in env


def test_command_runs_the_module_for_the_town():
    upload = next(s for s in update.SOURCES if s.module == "pipeline.documents")
    assert update.command(upload, "gloucester")[1:] == ["-m", "pipeline.documents", "upload", "--town", "gloucester"]
