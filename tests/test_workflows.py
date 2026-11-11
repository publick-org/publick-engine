"""The workflows that only run in this repository, where one has to agree with another."""

import re
import subprocess

import pytest
import yaml

from conftest import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"


def workflow(name):
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data, data[True]  # PyYAML reads the key "on" as True


def label_step():
    data, _ = workflow("evaluate-label.yml")
    return data["jobs"]["label"]["steps"][-1]


def test_the_label_check_names_the_files_evaluate_runs_for():
    _, on = workflow("evaluate.yml")
    paths = set(on["pull_request"]["paths"])
    evaluated = re.compile(label_step()["env"]["EVALUATED"])
    files = {p.relative_to(ROOT).as_posix() for folder in ("pipeline", "evals") for p in (ROOT / folder).rglob("*") if p.is_file()}
    assert paths <= files, "evaluate.yml names a file that isn't there"
    assert {f for f in files if evaluated.search(f)} == paths


def test_the_release_leaves_out_both_evaluation_workflows():
    data, _ = workflow("release.yml")
    unreleased = re.compile(data["jobs"]["release"]["steps"][1]["env"]["UNRELEASED"])
    for name in ("evaluate.yml", "evaluate-label.yml", "ci.yml", "public-check.yml", "release.yml"):
        assert unreleased.search(f".github/workflows/{name}"), name
    assert not unreleased.search(".github/workflows/town.yml")


@pytest.fixture
def pull_request(tmp_path):
    """A scratch repository with a base commit, and a function making a pull request's head from files to change."""
    def git(*args):
        return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
                               *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    for name in ("pipeline/summarize.py", "pipeline/dates.py", "evals/minutes.json", "README.md"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("1\n")
    git("add", "."), git("commit", "-qm", "Base")
    base = git("rev-parse", "HEAD")

    def run(changed, labels):
        git("checkout", "-q", "--detach", base)
        for name in changed:
            (tmp_path / name).write_text("2\n")
        if changed:
            git("commit", "-qam", "Change")
        step = label_step()
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "BASE_SHA": base, "HEAD_SHA": git("rev-parse", "HEAD"),
               "LABELS": labels, "EVALUATED": step["env"]["EVALUATED"]}
        return subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True, text=True)
    return run


@pytest.mark.parametrize("changed, labels, passes", [
    ([], "", True),
    (["pipeline/dates.py", "README.md"], "", True),
    (["pipeline/summarize.py"], "", False),
    (["evals/minutes.json"], "public-ok", False),
    (["pipeline/summarize.py"], "evaluate", True),
    (["pipeline/summarize.py", "pipeline/dates.py"], "public-ok,no evaluate", True),
    (["pipeline/summarize.py"], "evaluated,no evaluate yet", False),
])
def test_the_label_check_asks_for_a_label_only_when_the_evaluated_files_change(pull_request, changed, labels, passes):
    result = pull_request(changed, labels)
    assert (result.returncode == 0) is passes, result.stdout + result.stderr
    if not passes:
        assert "::error::" in result.stdout and "'evaluate'" in result.stdout and "'no evaluate'" in result.stdout
