# Contributing

This repository is public, and so is everything attached to it: commit
messages, pull request titles and descriptions, issues, comments, and the logs
of its workflow runs. Write all of them for someone reading the code.

## Writing a change

Describe the change: what it does, why in technical terms, and how it was
tested. Code, its documentation, and test results belong here. So do public
facts about a town, such as its budget, tax rate, meetings, and officials:
they are the sites' content.

Keep out anything about the project itself that the code doesn't show: its
finances, its plans, the people and accounts behind it, and its security
setup. Notes like these are kept elsewhere.

## The Public check

The **Public check** workflow (`.github/workflows/public-check.yml`) fails a
pull request whose title, description, commit messages, or added Markdown
lines read like internal notes, or whose added lines give a cost figure. Fix
the wording rather than working around the check. If it's a false positive,
whoever merges can add the `public-ok` label.
