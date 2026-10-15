"""Fail a pull request whose title, description, commit messages, or added
Markdown lines read like internal notes rather than public documentation.

This repository is public (see CLAUDE.md). The patterns are broad on purpose;
a false positive is cleared by the `public-ok` label.

Run by .github/workflows/public-check.yml, with PR_TITLE, PR_BODY, BASE_SHA
and HEAD_SHA set. Standard library only.
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

# Files that describe this check, and so quote what it looks for.
SKIP_FILES = {"CLAUDE.md"}


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def added_markdown_lines(base, head):
    """(file, line number, text) for each line the pull request adds to a Markdown file."""
    out = []
    diff = git("diff", "--unified=0", "--no-color", f"{base}...{head}", "--", "*.md")
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


def commit_messages(base, head):
    return git("log", "--format=%B", f"{base}..{head}")


def check(where, text):
    found = []
    for name, rx in COMPILED:
        for m in rx.finditer(text):
            found.append(f"{where}: {name}: \"{m.group(0)}\"")
    return found


def main():
    base, head = os.environ["BASE_SHA"], os.environ["HEAD_SHA"]
    found = []
    found += check("title", os.environ.get("PR_TITLE", ""))
    for i, line in enumerate(os.environ.get("PR_BODY", "").splitlines(), 1):
        found += check(f"description, line {i}", line)
    for line in commit_messages(base, head).splitlines():
        found += check("a commit message", line)
    for path, line_no, text in added_markdown_lines(base, head):
        found += check(f"{path}:{line_no}", text)

    if not found:
        print("Nothing that reads like internal notes.")
        return 0
    print("This repository is public. These read like internal notes (see CLAUDE.md):\n")
    for f in found:
        print(f"  {f}")
    print("\nReword them or take them out. If one is a false positive, add the `public-ok` label.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
