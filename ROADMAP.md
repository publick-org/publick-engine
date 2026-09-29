# Roadmap: from a few towns to a thousand

Today every town is its own repository: a config file, its data, and a short
workflow that calls the engine at a pinned version, deployed to GitHub Pages.
That works for a handful of towns. At hundreds it means hundreds of
repositories to create, schedule, pin, and watch.

**Decision: towns move into one repository.** Publick runs the towns itself,
so there is no need for each town to have a repository of its own. One
network repository holds every town's config and data, one workflow runs them
in batches, and Cloudflare serves every site. The engine stays its own
repository with its own tests and releases, and a single-town repository
calling `town.yml` keeps working, so a town that wants to run its own site
later can.

The first section describes the network repository. The numbered items are
what still has to change inside it, each with what breaks, the plan, and when
it matters. Items are numbered for reference, not order; the suggested order
is at the end.

## The network repository

**Layout.**

```
publick-org/towns
  engine-version              the engine release every town runs, e.g. v1.4.0
  towns/gloucester-ma/
    config/gloucester.toml
    data/
    site/static/share/gloucester.png
  towns/manchester-nh/...
  .github/workflows/network.yml
```

The engine already reads a town's config, data, and static files from
`PUBLICK_TOWN_DIR`, so each town's commands run unchanged with it set to the
town's folder. `TOWN_DIR` is read once at import, so each town runs in its own
process, as the commands already do.

**The daily run.** `network.yml` runs every hour across the early morning.

1. A first job assigns each town an hour from a stable hash of its slug and
   lists the towns due now. (Not Python's `hash()`, which changes between
   runs.)
2. A shared job fetches statewide and national sources once (item 2) and
   hands them to the town jobs.
3. Town jobs run in a matrix, a few towns per job, each checking out only
   its towns' folders. Python packages and Playwright are installed once per
   job, not once per town. `max-parallel` keeps the run inside the
   organization's limit on concurrent jobs.
4. Each job uploads its towns' changed `data/` folders; a final job commits
   them together in one commit. Dozens of jobs each rebasing and pushing to
   `main`, as `commit-data.sh` does for one town, would fight over the branch.
5. Each town that built and passed its site checks is deployed on its own. A
   town that fails keeps its last good site.

A pull request that changes a town's folder builds and checks only that town.
Adding a town is a pull request that adds its folder.

**Hosting.** Every site is already at `<town>-<state>.publick.org`.

- One wildcard DNS record, `*.publick.org`, points at one Cloudflare Worker.
  There is no DNS record, custom domain, or certificate to set up per town.
- Each build is uploaded to R2 under `sites/<town>/<build>/`.
- The Worker finds the town from the hostname, looks up that town's current
  build, and serves the file (`/meetings/` serves `meetings/index.html`).
- A deploy uploads the new build, then points the town at it, so no one sees
  a half-uploaded site. Rolling back points it at the previous build, for one
  town or all of them. Old builds are deleted after a few days.

**Secrets.** One set, at the organization or repository level: the Anthropic
key, the storage keys, the BLS key. Nothing is set per town.

**Moving the existing towns.**

1. Build `network.yml`, the batching script, the R2 deploy step, and the
   Worker.
2. Copy one town's `config/`, `data/`, and `site/static/` into the network
   repository, without its git history; the old repository stays, archived,
   as the record.
3. Run it alongside the old repository for a few days at a test address, then
   add the wildcard record, delete that town's `CNAME` (a specific record
   takes precedence over the wildcard), and turn off the old schedule.
4. Move the other towns the same way, one at a time.

**What this replaces.** Staggered cron lines in each town's workflow, a
command that creates repositories and Pages settings through the GitHub and
Cloudflare APIs, warnings for towns pinned to odd engine versions, and
re-enabling town workflows that GitHub turned off after 60 quiet days. None
of those are needed with one repository, one pin, and one schedule that
commits every day.

## 1. Data grows in git

**What breaks.** Collected data is committed daily. Git stores changes
compactly, so the cost is each day's changes rather than whole files, but
some files change a lot (Manchester's 311 file is 7 MB, and every open
request is rechecked), and in one repository every town's history adds up.
GitHub handles repositories of tens of gigabytes badly, and a full clone gets
slow long before that. Sparse, shallow checkouts keep the daily jobs fast,
but they don't make the repository smaller.

**Plan.** Measure first: the status page (item 3) reports the repository's
size and daily growth, by town. Keep data files line-stable (sorted keys, one
field per line) so daily changes stay small. Files that still grow fast keep
their working copy in the documents bucket, as agenda and minutes PDFs
already do, with git holding a small summary and the site built from the
bucket. Every town should have a `[storage]` table before it moves in.

**Matters at:** before the move, for large cities; within a year or two of
steady growth for the rest. This is the main cost of one repository.

## 2. Shared sources are fetched once per town

**What breaks.** Statewide and national sources (the BLS unemployment files,
Census building permits and housing estimates, Massachusetts DLS and DESE
reports, the Subsidized Housing Inventory) are downloaded separately for
every town in the state, from the same GitHub addresses. SeeClickFix already
blocked us twice in one day for one town; the keyless BLS API allows a couple
of dozen requests a day per address.

**Plan.** The network run's shared job fetches each statewide or national
file once and passes it to the town jobs. Town fetchers use the shared copy
when it's there and go to the source only if it isn't. Town-specific sources
(meetings, 311, a city's permits) stay per town, spread across the morning
by the schedule.

**Matters at:** tens of towns in one state.

## 3. Monitoring by email

**What breaks.** When a source stops updating, the run fails and GitHub
emails the owner. With every town in one workflow, a run with any failing
town fails, several times a morning, with no overview of which towns are
behind or why.

**Plan.**
- A town's failure doesn't fail the run. Each town job records what happened
  (every source's last update, the engine version, build and deploy result).
  *Done: a fetching run writes it to the town's `data/run.json`.*
- The final job of each run writes a network status page on publick.org: one
  table of towns behind, towns whose runs failed, and totals, plus the
  repository size (item 1) and summary spending (item 4). *Done, without the
  size and spending: publick.org/status/, built by the network repository's
  `scripts/build_status.py` after each run.*
- Once a day, the run fails if any town is behind, so GitHub sends one email
  a day rather than one per failure.
- If the network run itself stops, every town stops at once. A scheduled
  Cloudflare Worker, outside GitHub, checks that the status page was updated
  in the last day and emails if not.

**Matters at:** as soon as the second town moves in.

## 4. AI summary costs

**What breaks.** Each run's spending limit is per town (`max_cost_per_run`,
$5 in Gloucester's config), so the network-wide worst case grows with the
number of towns: $5,000 a day at a thousand. There's no engine default, so a
town that turns on summaries without setting the limit stops with an error.
All towns share one Anthropic key, and its rate limits apply to the whole
network, not to each town.

**Plan.**
- The engine sets a default per-run limit, so leaving it out is safe.
- Each town records what its summaries cost in a small ledger in its data. A
  monthly limit per town replaces the per-run limit as the main control, and
  the status page totals spending against a network budget, so one town's
  backlog (a first backfill of years of minutes) can't spend everyone's
  month.
- Summaries run in the town jobs, which the schedule already spreads out. If
  rate limits still bite, move summaries to one network job that uses the
  Batches API, which is also cheaper.

**Matters at:** the default, now; the rest as soon as several towns have
summaries turned on.

## 5. A bad release reaches every site at once

**What breaks.** The network repository runs every town on one engine
version, so a bad release breaks every site that uses the broken part the
next morning. The engine's tests build only Gloucester's site from saved
data. CivicClerk and DotNetNuke parsing is tested, but no test builds and
checks a whole site for a town that uses them, or for a New Hampshire town.

**Plan.**
- Engine tests build a small set of sample towns, one for each supported
  system (CivicPlus, CivicClerk, and DotNetNuke meetings; SeeClickFix with
  and without departments; Massachusetts and non-Massachusetts sources),
  from saved data, and run the site checks on each.
- Releasing is automatic: a merge that bumps a `VERSION` file creates the
  release, so no one has to click through GitHub's release page.
- A few canary towns in the network repository run the newest release;
  `engine-version` moves for everyone else after a day with the canaries
  green. Moving it is a one-line pull request, which a bot can open.
- Rolling back is moving `engine-version` back and pointing sites at their
  previous builds.

**Matters at:** as soon as more than one town uses a reader.

## 6. Who can change what

**What breaks.** In one repository, anyone with write access can change every
town. That's fine while Publick runs every town itself.

**Plan.** `CODEOWNERS` names who reviews each town's folder, and branch
protection on `main` requires that review. If a town ever wants to run its
own site, it gets its own repository calling `town.yml`, as towns do today.

**Matters at:** the first editor from outside Publick.

## Suggested order

1. The default summary spending limit (item 4); it's small.
2. Sample towns in the engine's tests and automatic releases (item 5), so the
   move itself is covered by tests.
3. The network repository: `network.yml`, batching, the R2 deploy, and the
   Worker, with one town moved in and run alongside its old repository.
4. The status page and the outside check (item 3), before the second town
   moves in.
5. `[storage]` for every town, then move the rest of the towns one at a time
   (item 1).
6. Canary towns on the newest release (item 5).
7. The shared statewide fetch, once a state has more than a few towns
   (item 2).
8. The cost ledger and monthly limits, once more towns have summaries
   (item 4).
9. Moving fast-growing files to the bucket, as the size tracking shows the
   need (item 1).
