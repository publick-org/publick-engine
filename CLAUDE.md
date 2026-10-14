# Working in this repository

This repository is public. So is everything attached to it: commit messages,
pull request titles and descriptions, issues, comments, and the logs of its
workflow runs. Write all of them for a stranger reading the code.

## What stays out

Never write any of these here, whether in a file, a commit message, a pull
request, an issue, or a comment:

- **Money:** budgets, what anything costs or has cost the project, the prices
  of accounts or plans, funding.
- **Plans and decisions:** what's planned next, which towns may come later,
  priorities, decisions and when they were made, open questions.
- **Conversations:** anything from your conversation with the person you're
  working with: what they asked, said, decided, or prefer.
- **Who runs Publick and how:** who owns or can reach which accounts, how
  many people there are, how much of the work is done by AI.
- **Security details beyond what the code shows:** which plan an account is
  on and its limits, protections not yet in place, what a token or key can do
  and when it runs out, where unredacted data is kept, what monitoring misses.

Code, its documentation, and test results are fine. Public facts about a town
(its budget, tax rate, meetings, officials) are the sites' content and are
fine.

## Planning and operations notes

They're kept outside this repository. Don't add a plan, a list of priorities,
status notes, an operations guide, or a log of decisions here. If a change
needs one, say so in the conversation, and the person you're working with
will put it where it belongs.

## Pull requests and commits

Describe the change: what it does, why in technical terms, and how it was
tested. No asides to the reader, nothing addressed to the people who run
Publick, and nothing about your own working environment.

The **Public check** workflow (`.github/workflows/public-check.yml`) fails a
pull request whose title, description, commit messages, or added Markdown
lines read like internal notes. Fix the wording rather than working around
the check. If it's a false positive, whoever merges can add the `public-ok`
label.
