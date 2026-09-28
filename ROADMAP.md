# Roadmap: from a few towns to a thousand

The engine already does the important part: every town is a config file, its
data, and a short workflow that calls the engine at a pinned version, so a fix
made here reaches every town. What follows is what has to change for that to
hold at hundreds or a thousand towns. None of it is needed for a handful.

Each item says what breaks, the plan, and when it matters. Items are numbered
for reference, not order; the suggested order is at the end.

## 1. Every town runs at the same minute

**What breaks.** Each town's workflow runs at 10:23 UTC. A thousand runs would
start together, and GitHub limits how many jobs an organization runs at once,
so most would sit in a queue for hours.

**Plan.** Give each town its own time slot, derived from its name, spread
across the early morning. The new-town setup (item 4) writes it into the
town's workflow; existing towns get theirs once.

**Matters at:** a few dozen towns.

## 2. Shared sources are fetched once per town

**What breaks.** Statewide and national sources (the BLS unemployment files,
Census building permits and housing estimates, Massachusetts DLS and DESE
reports, the Subsidized Housing Inventory) are downloaded separately by every
town in the state, from the same GitHub addresses. SeeClickFix already
blocked us twice in one day for one town; the keyless BLS API allows a couple
of dozen requests a day per address.

**Plan.** One shared daily job fetches each statewide or national file once
and publishes it to the documents bucket (e.g. `files.publick.org/shared/`).
Town fetchers read the shared copy first and go to the source only if it is
missing or stale. Town-specific sources (meetings, 311, a city's permits)
stay per town, spread out by item 1.

**Matters at:** tens of towns in one state.

## 3. A bad release reaches every site at once

**What breaks.** `@v1` moves for every town the moment a release is published,
and the engine's tests use only Gloucester's saved data, so a bug in a reader
Gloucester doesn't use (CivicClerk, DotNetNuke, New Hampshire sources) can pass
tests and break every town that does use it the next morning.

**Plan.**
- Engine tests build a small set of sample towns, one for each supported
  system (CivicPlus, CivicClerk and DotNetNuke meetings, SeeClickFix with and
  without departments, Massachusetts and non-Massachusetts sources), from
  saved data, and run the site checks on each.
- Releases go first to `@next`, used by a few canary towns, and move to `@v1`
  after a day with the canaries' runs green.
- Releasing becomes automatic: a merge that bumps a `VERSION` file creates
  the release, so no one has to click through GitHub's release page.
- Rolling back is one step: move `@v1` to the previous release.

**Matters at:** as soon as more than one town uses a reader.

## 4. Setting up a town by hand

**What breaks.** Each town needs a repository, its Pages settings, its
deployment-branch rule, a DNS record, the custom domain, HTTPS, and an email
route: about fifteen minutes of clicking in GitHub and Cloudflare, and easy
to get wrong (Manchester's first deploy failed on the deployment-branch rule).

**Plan.** A new-town command that takes a filled-in config and does the rest
through the GitHub and Cloudflare APIs: create the repository with its
workflow, README, and share image; set Pages to GitHub Actions and allow
`main`; add the DNS record and custom domain; turn on HTTPS once the
certificate is issued; add the email route. It runs with a dry-run mode that
prints each step first.

**Matters at:** about ten towns.

## 5. Towns drift onto their own pins

**What breaks.** A town pinned to a specific engine commit (Manchester is
today, until meetings are released) stops receiving releases. One exception
is harmless; hundreds of towns each pinned somewhere different can't be
maintained.

**Plan.** Towns pin only `@v1` (or `@next` for canaries). The engine's
workflow warns on any other pin, and the network status page (item 6) lists
towns that aren't on `@v1` or `@next`. Automatic releases (item 3) remove the
reason to pin a commit: a needed feature is one merge away from a release.

**Matters at:** now; it's cheap to hold the line early.

## 6. Monitoring by email

**What breaks.** When a source stops updating, the daily run fails and GitHub
emails the owner. At a thousand towns that is a flood of email with no
overview.

**Plan.** Each site publishes a small `/status.json` (every source's last
update, the engine version, when it was built). A network status page on
publick.org reads them all daily and shows one table: towns behind, towns
whose runs failed, towns on an old engine or an unusual pin, and totals.
Email goes to the owner once a day as a digest, not once per failure.

**Matters at:** about twenty towns.

## 7. Data grows in git

**What breaks.** Collected data is committed daily. Git stores changes
compactly, so the cost is the size of each day's changes rather than the
whole file, but some files change a lot (Manchester's 311 file is 7 MB and
every open request's record is rechecked), and a thousand repositories with
years of history add up.

**Plan.** Measure first: the status page reports each town's repository size
and daily growth. Keep data files line-stable (sorted keys, one field per
line) so daily changes stay small. For files that still grow fast, keep the
working copy in the documents bucket, as agenda and minutes PDFs already are,
with git holding a small summary and the site built from the bucket.

**Matters at:** a few years in, or sooner for large cities.

## 8. AI summary costs

**What breaks.** Each run's spending limit is per town ($5 a run), so the
network-wide worst case grows with the number of towns: $5,000 a day at a
thousand.

**Plan.** Each town records what its summaries cost in a small ledger in its
data. A monthly limit per town replaces the per-run limit as the main control,
and the status page totals spending across the network against a network
budget, so one town's backlog (a first backfill of years of minutes) can't
spend everyone's month.

**Matters at:** as soon as several towns have summaries turned on.

## 9. Quiet towns stop running

**What breaks.** GitHub turns off scheduled workflows in public repositories
after 60 days without activity. The daily data commits normally count as
activity, but a town whose sources all go quiet (or break) stops updating,
and nothing says so.

**Plan.** The status page (item 6) flags any town whose last run is more than
two days old. A daily network job re-enables disabled town workflows through
the GitHub API and reports which it had to wake.

**Matters at:** any number of towns; it's rare but silent.

## The bigger choice, at a few hundred towns

Towns have separate repositories because GitHub Pages serves one custom
domain per repository. At a few hundred towns, one repository holding every
town's config and data, with one workflow that runs towns in batches and
publishes each to its own address on Cloudflare (Pages, or R2 and a Worker),
may be simpler than hundreds of repositories: nothing to create per town, no
pins to drift, one place to look. The engine's code stays the same; only how
towns are stored, run, and hosted changes. Items 1, 4, 5, and 9 largely go
away in that model; 2, 3, 6, 7, and 8 still apply.

This is worth deciding before the town count makes moving expensive,
somewhere around fifty to a hundred towns.

## Suggested order

1. Automatic releases with canaries, and sample towns in the engine's tests (item 3).
2. Staggered schedules (item 1) and holding towns to `@v1`/`@next` (item 5).
3. The new-town command (item 4).
4. Per-site `status.json` and the network status page, with the idle-town check (items 6 and 9).
5. The cost ledger and monthly limits, once more towns have summaries (item 8).
6. The shared statewide fetch, once a state has more than a few towns (item 2).
7. Data size tracking, then moving fast-growing files to the bucket if needed (item 7).
8. The monorepo decision, before a hundred towns.
