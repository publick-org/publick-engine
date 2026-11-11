"""The Public check (.github/public_check.py): what it flags in a pull request's added lines and files, and
what it leaves alone."""

import importlib.util
import subprocess

import pytest

from conftest import ROOT

spec = importlib.util.spec_from_file_location("public_check", ROOT / ".github" / "public_check.py")
public_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public_check)


@pytest.mark.parametrize("path, line", [
    (".github/workflows/network.yml", '  SUMMARY_BUDGET: "12"'),
    (".github/workflows/network.yml", '  SUMMARY_BUDGET_MONTH: "2026-01=34"'),
    (".github/workflows/evaluate.yml", "# A run costs about $9 on the key."),
    ("pipeline/network.py", "# We spent $42 on summaries this month."),
    ("pipeline/network.py", "MONTHLY_BUDGET = 12"),
    ("pipeline/network.py", "    allowance = 3.5"),
    ("scripts/build_status.py", '    row = {"translation_cost": 1.2345, "documents": 7}'),
    ("wrangler.toml", "# The network's monthly budget, $12."),
])
def test_a_cost_figure_in_any_file_is_flagged(path, line):
    assert public_check.check_costs(path, 1, line)


@pytest.mark.parametrize("path, line", [
    # Settings of nothing, or of no money.
    ("pipeline/summarize.py", '    out = {"done": 0, "failed": 0, "paid": 0.0, "estimated_cost": 0.0}'),
    ("pipeline/summarize.py", "        replaced_cost = 0"),
    ("towns/methuen-ma/config/methuen.toml", "time_budget_seconds = 2400"),
    ("pipeline/states/ct/schools.py", "FIRST_SPENDING_YEAR = 2015"),
    (".github/workflows/evaluate.yml", '          PUBLICK_HIDE_COSTS: "1"'),
    # A town's limit for one run, and a model's list price: no secret.
    ("towns/methuen-ma/config/methuen.toml", "max_cost_per_run = 5.0"),
    ("pipeline/summarize.py", "DEFAULT_MAX_COST_PER_RUN = 5.0"),
    ("pipeline/evaluate.py", "# Dollars per million tokens: $2 input, $10 output, what a summary costs."),
    # A town's budget is the sites' content.
    ("pipeline/build_site.py", '    """140559783 -> "$140.6 million" in the budget section."""'),
    ("towns/gloucester-ma/data/finance/budget.json", '  "budget": 140559783, "cost": 12.5'),
    ("tests/test_nh.py", '    assert "$20.24" in budget and "Less MSD Budget" in page'),
    # Marked as fine.
    ("pipeline/evaluate.py", "DEFAULT_ALLOWANCE = 2.5  # public-ok: a test default, not a budget"),
    # No figure.
    (".github/workflows/network.yml", "  SUMMARY_BUDGET: ${{ secrets.SUMMARY_BUDGET }}"),
    (".github/workflows/network.yml", "# AI summaries share one monthly budget, SUMMARY_BUDGET (a secret)."),
])
def test_public_facts_and_settings_of_nothing_are_left_alone(path, line):
    assert public_check.check_costs(path, 1, line) == []


def git(repo, *args):
    # Without the machine's signing or hooks: the commits are only for the check to read.
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def test_a_change_that_commits_a_cost_record_fails_and_one_that_removes_it_passes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "config", "user.email", "test@example.org")
    git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("A network.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Start")
    base = git(repo, "rev-parse", "HEAD")
    data = repo / "towns" / "a-ma" / "data"
    (data / "costs" / "2026-10").mkdir(parents=True)
    (data / "summary-costs.json").write_text('{"2026-10": {"cost": 0.0}}\n')
    (data / "costs" / "2026-10" / "run.jsonl").write_text('{"kind": "summary"}\n')
    (data / "run.json").write_text("{}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Add a town's data")
    head = git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    found = public_check.check_change(base, head)
    assert sorted(f.split(":")[0] for f in found) == ["towns/a-ma/data/costs/2026-10/run.jsonl",
                                                      "towns/a-ma/data/summary-costs.json"]
    git(repo, "rm", "-q", "--cached", "-r", "towns/a-ma/data/summary-costs.json", "towns/a-ma/data/costs")
    git(repo, "commit", "-qm", "Stop tracking the cost records")
    assert public_check.check_change(head, git(repo, "rev-parse", "HEAD")) == []


@pytest.mark.parametrize("text", [
    "Claude-Session: https://claude.ai/code/session_0123abc",
    "claude-session: abc",
    "Written in https://claude.ai/code/session_0123abc",
])
def test_a_session_link_in_a_commit_message_or_title_is_flagged(text):
    assert public_check.check_session("a commit message", "Fix the page\n\n" + text)


@pytest.mark.parametrize("text", [
    "Co-Authored-By: Claude <noreply@anthropic.com>",
    "Summaries: the session of the council that passed it",
    "See https://claude.com/claude-code",
    "",
])
def test_other_lines_of_a_commit_message_are_left_alone(text):
    assert not public_check.check_session("a commit message", text)


@pytest.mark.parametrize("name, flagged", [
    ("claude/eager-name-abc123", True), ("Claude/x", True),
    ("meeting-page-order", False), ("add-town/londonderry-nh", False), ("engine-version", False),
    ("dependabot/pip/requirements-1", False), ("fix-claude/typo", False), ("", False),
])
def test_a_branch_named_with_a_tool_s_prefix_is_flagged(name, flagged):
    assert bool(public_check.check_branch(name)) is flagged


def test_a_pull_request_s_branch_title_and_commits_are_checked_and_its_description_s_link_is_not(tmp_path, monkeypatch, capsys):
    def git(*args):
        # Without the machine's signing or hooks: the commits are only for the check to read.
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
                        "-c", "core.hooksPath=/dev/null", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", "."), git("commit", "-qm", "First")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    (tmp_path / "a.py").write_text("x = 2\n")
    git("commit", "-qam", "Second\n\nClaude-Session: https://claude.ai/code/session_0123abc")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    monkeypatch.chdir(tmp_path)
    env = {"BASE_SHA": base, "HEAD_SHA": head, "PR_TITLE": "Second", "HEAD_REF": "claude/some-name",
           "PR_BODY": "What it does.\n\nhttps://claude.ai/code/session_0123abc"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["public_check.py"])
    assert public_check.main() == 1
    out = capsys.readouterr().out
    assert "the branch's name" in out and "a commit message: a link to a working session" in out
    assert "description" not in out and "title" not in out
    # In the title, the link is found too.
    monkeypatch.setenv("PR_TITLE", "Second (https://claude.ai/code/session_0123abc)")
    assert public_check.main() == 1
    assert "title: a link to a working session" in capsys.readouterr().out
    monkeypatch.setenv("PR_TITLE", "Second")
    # Named for its change, with the link out of the commit message: nothing found.
    git("commit", "-q", "--amend", "-m", "Second")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("HEAD_SHA", head), monkeypatch.setenv("HEAD_REF", "second-value")
    assert public_check.main() == 0
