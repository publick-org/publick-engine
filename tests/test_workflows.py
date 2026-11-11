"""The workflows that only run in this repository, where one has to agree with another."""

import re
import subprocess
import textwrap

import pytest

from conftest import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"


def text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def block(source, key):
    """The lines under `key:` or `key: |` or `key: >-` in a workflow, without their indentation: enough of YAML
    for the values these tests read, so the tests need no YAML library."""
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if re.fullmatch(rf"\s*(- )?{re.escape(key)}:( [|>]-?)?", line))
    indent = len(lines[start]) - len(lines[start].lstrip())
    taken = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        taken.append(line)
    return textwrap.dedent("\n".join(taken)).strip("\n")


LABEL = text("evaluate-label.yml")
EVALUATED = block(LABEL, "EVALUATED")
SCRIPT = block(LABEL, "run")


def test_the_label_check_names_the_files_evaluate_runs_for():
    paths = {line[2:] for line in block(text("evaluate.yml"), "paths").splitlines()}
    assert len(paths) == 8 and all(re.fullmatch(r"[a-z_/.]+", p) for p in paths), paths
    files = {p.relative_to(ROOT).as_posix() for folder in ("pipeline", "evals") for p in (ROOT / folder).rglob("*") if p.is_file()}
    assert paths <= files, "evaluate.yml names a file that isn't there"
    assert {f for f in files if re.search(EVALUATED, f)} == paths


def test_the_label_check_runs_again_when_a_label_is_added_or_taken_off():
    types = re.search(r"^    types: \[(.*)\]$", LABEL, re.MULTILINE).group(1).split(", ")
    assert {"opened", "reopened", "synchronize", "labeled", "unlabeled"} <= set(types)
    # What the script reads comes from the pull request the event is about.
    for name, value in (("BASE_SHA", "github.event.pull_request.base.sha"), ("HEAD_SHA", "github.event.pull_request.head.sha"),
                        ("LABELS", "join(github.event.pull_request.labels.*.name, ',')"),
                        ("FROM_FORK", "github.event.pull_request.head.repo.full_name != github.repository")):
        assert f"          {name}: ${{{{ {value} }}}}\n" in LABEL, name


def test_the_release_leaves_out_both_evaluation_workflows():
    unreleased = block(text("release.yml"), "UNRELEASED")
    for name in ("evaluate.yml", "evaluate-label.yml", "ci.yml", "public-check.yml", "release.yml"):
        assert re.search(unreleased, f".github/workflows/{name}"), name
    assert not re.search(unreleased, ".github/workflows/town.yml")


@pytest.fixture
def pull_request(tmp_path):
    """A scratch repository with a base commit, and a function running the check's script on a pull request made
    from files to change."""
    def git(*args):
        return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
                               "-c", "core.hooksPath=/dev/null", *args], cwd=tmp_path, check=True, capture_output=True,
                              text=True).stdout.strip()
    git("init", "-q")
    for name in ("pipeline/summarize.py", "pipeline/dates.py", "evals/minutes.json", "README.md"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("1\n")
    git("add", "."), git("commit", "-qm", "Base")
    base = git("rev-parse", "HEAD")

    def run(changed, labels, from_fork="false", base_sha=None):
        git("checkout", "-q", "--detach", base)
        for name in changed:
            (tmp_path / name).write_text("2\n")
        if changed:
            git("commit", "-qam", "Change")
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "BASE_SHA": base_sha or base, "HEAD_SHA": git("rev-parse", "HEAD"),
               "LABELS": labels, "EVALUATED": EVALUATED, "FROM_FORK": from_fork}
        # As Actions runs a step: bash, stopping at the first command that fails.
        return subprocess.run(["bash", "-e", "-c", SCRIPT], cwd=tmp_path, env=env, capture_output=True, text=True)
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


def test_the_label_check_fails_when_it_can_t_tell_what_changed(pull_request):
    result = pull_request(["pipeline/summarize.py"], "", base_sha="0" * 40)
    assert result.returncode != 0 and "no label needed" not in result.stdout, result.stdout + result.stderr


def test_evaluate_on_a_pull_request_from_a_fork_doesn_t_pass(pull_request):
    assert pull_request(["pipeline/summarize.py"], "evaluate", from_fork="true").returncode == 1
    assert pull_request(["pipeline/summarize.py"], "no evaluate", from_fork="true").returncode == 0
    assert pull_request(["pipeline/dates.py"], "", from_fork="true").returncode == 0
