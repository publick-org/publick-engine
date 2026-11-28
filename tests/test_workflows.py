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
SDK_PIN = block(LABEL, "SDK_PIN")
SCRIPT = block(LABEL, "run")


EVALUATE = text("evaluate.yml")
# The pinned requirements: evaluated when a pull request is labeled for it, but held for a label only when the SDK's
# pin changes (SDK_PIN), not by the file's name.
UNLABELED = {"requirements.txt"}
REQUIREMENTS = "anthropic==1.11.0\nboto3==1.44.6\n"


def test_the_label_check_names_the_files_evaluate_runs_for():
    paths = {line[2:] for line in block(EVALUATE, "paths").splitlines()}
    assert len(paths) == 14 and all(re.fullmatch(r"[a-z_/.]+", p) for p in paths), paths
    files = {p.relative_to(ROOT).as_posix() for folder in ("pipeline", "evals") for p in (ROOT / folder).rglob("*") if p.is_file()}
    files |= {p.name for p in ROOT.glob("requirements*.txt")}
    assert paths <= files, "evaluate.yml names a file that isn't there"
    assert UNLABELED <= paths
    assert {f for f in files if re.search(EVALUATED, f)} == paths - UNLABELED


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
    for name in ("pipeline/summarize.py", "pipeline/dates.py", "pipeline/translate.py", "evals/minutes.json", "README.md",
                 "requirements.txt"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text(REQUIREMENTS if name == "requirements.txt" else "1\n")
    git("add", "."), git("commit", "-qm", "Base")
    base = git("rev-parse", "HEAD")

    def run(changed, labels="", from_fork="false", base_sha=None, script=SCRIPT):
        """changed: the files to change, each a name (written "2") or (name, its new text)."""
        git("checkout", "-q", "--detach", base)
        for name, new in (c if isinstance(c, tuple) else (c, "2\n") for c in changed):
            (tmp_path / name).write_text(new)
        if changed:
            git("commit", "-qam", "Change")
        output = tmp_path.parent / "github_output"
        output.write_text("")
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "BASE_SHA": base_sha or base, "HEAD_SHA": git("rev-parse", "HEAD"),
               "LABELS": labels, "EVALUATED": EVALUATED, "SDK_PIN": SDK_PIN, "FROM_FORK": from_fork,
               "GITHUB_OUTPUT": str(output)}
        # As Actions runs a step: bash, stopping at the first command that fails.
        result = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True)
        result.output = output.read_text()
        return result
    return run


@pytest.mark.parametrize("changed, labels, passes", [
    ([], "", True),
    (["pipeline/dates.py", "README.md"], "", True),
    (["pipeline/summarize.py"], "", False),
    (["evals/minutes.json"], "public-ok", False),
    (["pipeline/summarize.py"], "evaluate", True),
    (["pipeline/summarize.py", "pipeline/dates.py"], "public-ok,no evaluate", True),
    (["pipeline/summarize.py"], "evaluated,no evaluate yet", False),
    # A dependency update can be evaluated, but is held for a label only when it moves the SDK.
    ([("requirements.txt", "anthropic==1.11.0\nboto3==1.44.7\n")], "", True),
    ([("requirements.txt", "anthropic==2.10.0\nboto3==1.44.6\n")], "", False),
    ([("requirements.txt", "anthropic[aiohttp]==1.11.0\nboto3==1.44.6\n")], "", False),
    ([("requirements.txt", "Anthropic==2.10.0\nboto3==1.44.6\n")], "", False),
    ([("requirements.txt", "anthropic==1.11.0\nanthropic-tools==1.0\nboto3==1.44.6\n")], "", True),
    ([("requirements.txt", "boto3==1.44.6\n")], "", False),
    ([("requirements.txt", "anthropic==2.10.0\nboto3==1.44.6\n")], "evaluate", True),
    ([("requirements.txt", "anthropic==2.10.0\nboto3==1.44.6\n")], "no evaluate", True),
    (["requirements.txt", "pipeline/summarize.py"], "", False),
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


@pytest.mark.parametrize("changed, sets", [
    (["pipeline/summarize.py"], "minutes agendas"),
    (["pipeline/translate.py"], "translations"),
    (["pipeline/dates.py"], ""),
    # The SDK and the other pinned requirements are under every set.
    (["requirements.txt"], "minutes agendas translations digest"),
    ([("requirements.txt", "anthropic==2.10.0\nboto3==1.44.6\n")], "minutes agendas translations digest"),
    (["requirements.txt", "pipeline/summarize.py"], "minutes agendas translations digest"),
])
def test_evaluate_runs_every_set_when_the_requirements_change(pull_request, changed, sets):
    result = pull_request(changed, script=block(EVALUATE, "run"))
    assert result.returncode == 0, result.stdout + result.stderr
    key, _, value = result.output.strip("\n").partition("=")
    assert key == "sets" and value.split() == sets.split(), result.output


def test_the_label_check_names_the_sdk_s_pin_it_held(pull_request):
    result = pull_request([("requirements.txt", "anthropic==2.10.0\nboto3==1.44.6\n")], "")
    assert result.returncode == 1 and "requirements.txt (the anthropic SDK's pin)" in result.stdout, result.stdout
    result = pull_request(["pipeline/summarize.py", ("requirements.txt", "anthropic==2.10.0\nboto3==1.44.6\n")], "")
    assert result.returncode == 1 and "pipeline/summarize.py\nrequirements.txt (the anthropic SDK's pin)\n" in result.stdout


def test_evaluate_says_why_a_push_by_dependabot_doesn_t_run():
    """Dependabot's pushes get no repository secrets: the run says to add the label again, not to set the secret."""
    run = block(EVALUATE[EVALUATE.index("Run the sets against the model"):], "run")
    for actor, says in (("dependabot[bot]", "take the 'evaluate' label off and add it again"),
                        ("someone", "Set the repository secret ANTHROPIC_API_KEY")):
        result = subprocess.run(["bash", "-e", "-c", run], env={"PATH": "/usr/bin:/bin", "ANTHROPIC_API_KEY": "",
                                                                "ACTOR": actor, "SETS": "minutes"},
                                capture_output=True, text=True)
        assert result.returncode == 1 and says in result.stdout, result.stdout
