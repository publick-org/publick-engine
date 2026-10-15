"""Fail a pull request whose title, description, commit messages, or added
lines read like internal notes rather than public documentation, or that
commits what running the project costs.

This repository is public (see CLAUDE.md). The patterns are broad on purpose;
a false positive is cleared by the `public-ok` label.

- The title, description, commit messages, and lines added to Markdown files
  are checked for every pattern in PATTERNS.
- Lines added to any other file are checked for cost figures (COST_PATTERNS):
  a dollar amount next to a word about spending, or a number given to a
  setting named for a budget, a cost, or spending. Public facts are left
  alone: a town's own data (DATA_PATHS), the tests (TEST_PATHS), whose
  amounts are a town's or made up, and a line about a model's list price
  (LIST_PRICE). A line that is fine anyway says so with `public-ok`
  (in a comment, as `# public-ok: why`), and a setting whose number is no
  secret is in ALLOWED_SETTINGS.
- A file added or changed at a path where cost records are kept
  (COST_RECORD_PATHS: summary-costs.json, data/costs/) fails the check: they
  belong in the private store, not in git.

Run by .github/workflows/public-check.yml, with PR_TITLE, PR_BODY, BASE_SHA
and HEAD_SHA set. Standard library only. `python3 .github/public_check.py
--range A..B` checks a range of commits' messages and added lines, as a
pull request's would be.
"""

import os
import re
import subprocess
import sys

MONEY = r"\$\s?\d[\d,.]*\s*[km]?"

PATTERNS = [
    ("money over time", rf"{MONEY}\s*(a|an|per|/|each)\s*(month|mo|day|week|year|run|town)\b"),
    ("spending", rf"\b(spent|spend|spending|budget)\b[^.\n]{{0,40}}{MONEY}|{MONEY}[^.\n]{{0,20}}\b(spent|budget)\b"),
    ("a dated decision", r"\bdecided\s+(on\s+)?\d{4}-\d{2}-\d{2}|\bdecision (was )?recorded\b|\ban open decision\b"),
    ("the maintainer", r"\bthe maintainer\b|\bmaintainer'?s\b"),
    ("an aside to the reader",
     r"\bif you('re| are)? (want|happy|prefer)\b|\bI'll\b|\bper our\b|\byou asked\b|\bas you said\b|\bmy sandbox\b"),
    ("an account plan", r"\b(free|paid)\s+plan\b"),
    ("internal notes", r"\b(roadmap|runbook)\b"),
    ("a credential's lifetime", r"\b(token|key|secret)\b[^.\n]{0,60}\bexpires?\b[^.\n]{0,30}\d{4}"),
    ("staffing", r"\b(one|single|only one) (person|owner)\b|\bsecond owner\b"),
]
COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in PATTERNS]

# Words about what something cost the project. A budget alone is often a town's (its figures are the
# sites' content), so only the project's own: the summary budget, the network's, a monthly one.
SPENDING = (r"(costs?|costing|spent|spend\w*|allowance|billed|invoiced?|paid"
            r"|(?:summary|summaries|network'?s?|monthly|month'?s|ai|our)\s+budget\w*)")
# A number that isn't zero (a setting of 0 says nothing), maybe in dollars.
NONZERO = r"\$?(?!0+(?:\.0+)?(?![\d.]))\d"
# A dollar amount: "$1", "$1.50", "$2,000", "$110k".
AMOUNT = r"\$\s?\d[\d,]*(?:\.\d+)?\s*[km]?\b"
COST_PATTERNS = [
    ("a cost figure", rf"\b{SPENDING}\b[^\n]{{0,40}}{AMOUNT}|{AMOUNT}[^\n]{{0,30}}\b{SPENDING}\b"),
    # SUMMARY_BUDGET: "110", budget = 50, max_spend: 20, SUMMARY_BUDGET_MONTH: "2026-10=200", and with the
    # name in quotes ("budget": 50, QUOTED_SETTING).
    ("a budget or cost setting",
     rf"(?<![\w.])([A-Za-z_]*(?:budget|cost|spend|spent|allowance)[A-Za-z_]*)\s*[:=]\s*[\"']?{NONZERO}"),
]
COST_COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in COST_PATTERNS]
QUOTED_SETTING = re.compile(rf"[\"']([A-Za-z_]*(?:budget|cost|spend|spent|allowance)[A-Za-z_]*)[\"']\s*:\s*{NONZERO}",
                            re.IGNORECASE)
# A model's list price, per million tokens: a public fact the engine's code and tests use.
LIST_PRICE = re.compile(r"\bprices?\b|_price\b|per million|a million", re.IGNORECASE)
# Settings whose numbers are no secret: a town's limit on one run, the engine's default for it, and the
# test suite's prices.
ALLOWED_SETTINGS = {"max_cost_per_run", "default_max_cost_per_run", "same_cost"}
# A budget of something other than money (time_budget_seconds, a token budget), or a switch (HIDE_COSTS: 1).
NOT_MONEY = re.compile(r"time|seconds|minutes|hours|years?\b|_year|tokens|pages|bytes|hide|show|skip|enable|disable",
                       re.IGNORECASE)
# A line marked as fine.
MARKED = re.compile(r"\bpublic-ok\b")

# Public data: a town's or state's collected records, test fixtures, and evaluation sets, which quote
# budgets, contracts, and fees as the documents give them.
DATA_PATHS = re.compile(r"^(towns/[^/]+/data/|states/|pipeline/states/[^/]+/figures/|tests/fixtures/|evals/"
                        r"|site_checks/fixtures/)")
TEST_PATHS = re.compile(r"^tests/")
# Where cost records are kept, which never belong in git.
COST_RECORD_PATHS = re.compile(r"(^|/)summary-costs\.json$|(^|/)data/costs/")

# Files that describe this check, and so quote what it looks for.
SKIP_FILES = {"CLAUDE.md", "public_check.py", "test_public_check.py"}


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def added_lines(base, head, pathspec=()):
    """(file, line number, text) for each line the change adds, in the files the pathspec names (all)."""
    out = []
    diff = git("diff", "--unified=0", "--no-color", "--no-ext-diff", f"{base}...{head}", "--", *pathspec)
    path, line_no = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
        elif line.startswith("+") and path and os.path.basename(path) not in SKIP_FILES:
            out.append((path, line_no, line[1:]))
            line_no += 1
    return out


def added_markdown_lines(base, head):
    return added_lines(base, head, ("*.md",))


def changed_files(base, head):
    """The files the change adds or changes (not those it deletes)."""
    return git("diff", "--name-only", "--no-renames", "--diff-filter=AMT", f"{base}...{head}").splitlines()


def commit_messages(base, head):
    return git("log", "--format=%B", f"{base}..{head}")


def check(where, text):
    found = []
    for name, rx in COMPILED:
        for m in rx.finditer(text):
            found.append(f"{where}: {name}: \"{m.group(0)}\"")
    return found


def check_costs(path, line_no, text):
    """A line added to a file that isn't Markdown, checked for cost figures."""
    if DATA_PATHS.match(path) or MARKED.search(text) or LIST_PRICE.search(text):
        return []
    found = []
    # A test's numbers are made up for it, and its dollar amounts are a town's.
    if TEST_PATHS.match(path):
        return []
    for name, rx in COST_COMPILED:
        for m in rx.finditer(text):
            if m.lastindex and m.group(1) and (m.group(1).lower() in ALLOWED_SETTINGS or NOT_MONEY.search(m.group(1))):
                continue
            found.append(f"{path}:{line_no}: {name}: \"{m.group(0).strip()}\"")
    for m in QUOTED_SETTING.finditer(text):
        if m.group(1).lower() not in ALLOWED_SETTINGS and not NOT_MONEY.search(m.group(1)):
            found.append(f"{path}:{line_no}: a budget or cost setting: \"{m.group(0)}\"")
    return found


def check_change(base, head):
    """Everything a change between two commits adds: its commit messages, its added lines, and its files."""
    found = []
    for line in commit_messages(base, head).splitlines():
        found += check("a commit message", line)
    for path, line_no, text in added_lines(base, head):
        if path.endswith(".md"):
            found += check(f"{path}:{line_no}", text)
        else:
            found += check_costs(path, line_no, text)
    for path in changed_files(base, head):
        if COST_RECORD_PATHS.search(path):
            found.append(f"{path}: a cost record committed (it belongs in the private store, not in git)")
    return found


def report(found):
    if not found:
        print("Nothing that reads like internal notes.")
        return 0
    print("This repository is public. These read like internal notes (see CLAUDE.md):\n")
    for f in found:
        print(f"  {f}")
    print("\nReword them or take them out. If one is a false positive, add the `public-ok` label "
          "(or, for a line of code that is fine, a `public-ok` comment on it).")
    return 1


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--range":
        base, _, head = sys.argv[2].partition("..")
        return report(check_change(base, head))
    base, head = os.environ["BASE_SHA"], os.environ["HEAD_SHA"]
    found = []
    found += check("title", os.environ.get("PR_TITLE", ""))
    for i, line in enumerate(os.environ.get("PR_BODY", "").splitlines(), 1):
        found += check(f"description, line {i}", line)
    found += check_change(base, head)
    return report(found)


if __name__ == "__main__":
    sys.exit(main())
