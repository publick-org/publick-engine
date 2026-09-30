# Roadmap: from a few towns to a thousand

Publick's towns started as one repository each: a config file, its data, and a
short workflow that calls the engine at a pinned version, deployed to GitHub
Pages. That works for a handful of towns. At hundreds it means hundreds of
repositories to create, schedule, pin, and watch.

**Decision, now done: towns live in one repository.** Publick runs the towns
itself, so there is no need for each town to have a repository of its own. The
network repository, [publick-org/publick.org](https://github.com/publick-org/publick.org),
holds every town's config and data (Gloucester, Malden, and Manchester so far),
one workflow runs them, and Cloudflare serves every site. The engine stays its
own repository with its own tests and releases, and a single-town repository
calling `town.yml` keeps working, so a town that wants to run its own site
later can.

The first section describes the network repository as built. The numbered
items are what still has to change, each with what breaks, the plan, what's
done, and when it matters. Items are numbered for reference, not order; the
stages at the end say what to do when, by the number of towns.

**The shape of the change.** Today the unit of work is a town: each town runs
every source, every day, in the network's scheduled runs. At a thousand towns
the unit of work becomes a source, for a scope, on its own rhythm: statewide
sources fetched once per state, a vendor's sources (SeeClickFix, a meeting
portal) paced across all the towns that use it, and only meetings, agendas,
minutes, and their summaries done town by town. Most items below are steps
toward that.

**Constraints today.** The network repository is public on GitHub's free plan:
Actions minutes are free, but at most 20 jobs run at once, and a run's matrix
is at most 256 jobs. A paid plan raises the concurrent-job limit (the numbers
below say where that matters). Summaries have a network budget of **$50 a
month**.

## The network repository

**Layout.**

```
publick-org/publick.org
  engine-version              the engine release every town runs, e.g. v1.8.0
  towns/gloucester-ma/
    config/gloucester.toml
    data/                     including run.json, the last fetching run's result
    site/static/share/gloucester.png
  towns/malden-ma/, towns/manchester-nh/
  home/                       the publick.org homepage
  scripts/                    build_home.py (its town lists), build_status.py (publick.org/status/)
  .github/workflows/network.yml
```

The engine reads a town's config, data, and static files from
`PUBLICK_TOWN_DIR`, so each town's commands run unchanged with it set to the
town's folder, each town in its own process (`pipeline/network.py`).

**The daily run.** `network.yml` is scheduled four times across the early
morning.

1. A first job gives each town one of the four runs, from a stable hash of its
   folder name, and splits the run's towns into jobs: four towns a job on a
   scheduled run, one town a job otherwise.
2. Town jobs run in a matrix, each checking out only its towns' folders.
   Python packages and Playwright are installed once per job, and the browser
   checks use every core.
3. Each town fetches new data, is built and checked (a sample of pages on a
   daily run, every page on a pull request), and is published on its own if
   its checks pass; a town that fails keeps its last good site. Each job
   commits its towns' data, retrying against the other jobs' pushes.
4. A report job writes one table of every town in the run and fails the run
   once if any town needs attention, so GitHub sends one email per run.
5. The homepage and the status page are rebuilt from `main`, with the run's
   data, and published.

A pull request that changes a town's folder builds and checks only that town;
one that changes `engine-version` or a workflow builds and checks every town.
Adding a town is a pull request that adds its folder. A manual run can take
any towns, with or without fetching, and fetch only some sources (`figures`
takes minutes; 311 is the slow part).

**Hosting.** Every site is at `<town>-<state>.publick.org`.

- One wildcard DNS record, `*.publick.org`, points at one Cloudflare Worker.
  There is no DNS record, custom domain, or certificate to set up per town.
- Each build goes to an R2 bucket, with files stored once by content and
  shared across sites.
- The Worker finds the town from the hostname, looks up that town's current
  build, and serves the file (`/meetings/` serves `meetings/index.html`).
- A deploy uploads the new build, then points the town at it, so no one sees
  a half-uploaded site. Rolling back points it at the previous build. Builds
  beyond the newest ten are deleted daily.
- Agenda and minutes PDFs are in a second bucket, `publick-documents`, under
  each town's prefix, served at files.publick.org.

**Secrets.** One set, at the organization or repository level: the Anthropic
key, the storage keys, the sites bucket keys, the BLS key. Nothing is set per
town.

**Page views.** Every town counts on one GoatCounter site, `publick` (no
cookies, and never what was searched). Each town's `[analytics]` table sets
`prefix` to its folder name, which goes in front of every page path and event,
so the one dashboard can tell the towns apart and there's nothing to set up
per town but that line. GoatCounter is free for this; at millions of page
views a day it would mean self-hosting it.

**What this replaced.** Staggered cron lines in each town's workflow, a
command to create repositories and Pages settings, warnings for towns pinned
to odd engine versions, and re-enabling town workflows that GitHub turned off
after 60 quiet days. None of those are needed with one repository, one pin,
and one schedule that commits every day.

## 1. Data grows in git

**What breaks.** Collected data is committed daily. A town's data folder is
8 to 13 MB today, most of it 311 requests, and much of it is rewritten on
every run (every open request is rechecked). Git stores each day's changes
rather than whole files, but in one repository every town's history adds up:
at a thousand towns that's about 10 GB of working data, changing by gigabytes
a week. GitHub handles repositories of tens of gigabytes badly, and a full
clone gets slow long before that. A thousand jobs a day pushing to `main`
also means constant push conflicts and retries.

**Plan.** Measure first: the status page (item 3) reports the repository's
size and daily growth, by town. *Done: each fetching run records the size of
the town's data and what the run added (`data/run.json`), and the status page
shows them with the repository's size on GitHub.* Keep data files line-stable (sorted keys, one
field per line) so daily changes stay small. Then move each town's working
data to R2, as agenda and minutes PDFs already are, with git keeping config
and code, and sites built from the bucket. Every town already has a
`[storage]` table.

**Matters at:** 20 to 50 towns, when push conflicts and clone times start to
show; before that for a large city with a big 311 history.

## 2. Shared sources are fetched once per town

**What breaks.** Statewide and national sources (the BLS unemployment files,
Census building permits and housing estimates, Massachusetts DLS and DESE
reports, the Subsidized Housing Inventory) are downloaded separately for
every town in the state, from the same GitHub addresses. About 350
Massachusetts towns would each ask DLS and DESE every day. DLS already refuses
GitHub's addresses some days (an empty HTTP 202 instead of the report, as for
Gloucester and Malden on 2026-09-29), SeeClickFix allows about 20 requests a
minute and has blocked us before, and the keyless BLS API allows a couple of
dozen requests a day per address.

**Plan.** Each statewide or national source is fetched once per state (or
once for the country) into a shared file, and each town reads its rows, as
New Hampshire's figures already work. Vendor sources shared by many towns
(SeeClickFix, a meeting portal like CivicClerk or CivicPlus) are paced per
vendor across all the towns that use it, not per town. Town-specific sources
(meetings, a city's own permits or budget page) stay per town. Combined with
item 7, the state agencies go from hundreds of requests a day to a handful a
year.

*Done so far: New Hampshire's statewide files (tax rates, school figures)
are saved once a year into the engine (`pipeline/states/nh/figures/`), because
the state's websites refuse automated requests; every New Hampshire town reads
its rows from there. The other shared sources are still fetched per town.*

**Matters at:** tens of towns in one state; DLS matters now.

## 3. Monitoring by email

**What breaks.** When a source stops updating, the run fails and GitHub
emails the owner. With every town in one workflow, a run with any failing
town fails, several times a morning, with no overview of which towns are
behind or why. At a thousand towns, one alert per stale town would flood the
inbox.

**Plan and progress.**
- A town's failure doesn't fail the run. Each town job records what happened
  (every source's last update, the engine version, build and deploy result).
  *Done: a fetching run writes it to the town's `data/run.json`, with when
  the town last had a good update (published, with fresh data), and doesn't
  fail when a town does. A run that only builds, as for a pull request, still
  fails, so a broken site can't be merged.*
- The final job of each run writes a network status page on publick.org: one
  table of towns behind, towns whose runs failed, and totals, plus the
  repository size (item 1) and summary spending (item 4). *Done, without the
  size and spending, now there too: [publick.org/status/](https://publick.org/status/), built by
  the network repository's `scripts/build_status.py` after each run. It's public,
  so it says in plain words which data on a site may be out of date and leaves
  the run's internals to the run's summary.*
- One alert a day, not one per failure: if any town has gone more than about
  30 hours without a successful update, open (or update) one GitHub issue
  listing them, which emails the owner. *Done: `pipeline.network behind`
  lists them, with towns whose figure checks keep failing, and the network's
  daily runs open, update, or close one issue, assigned to the owner.*
- If the network run itself stops, every town stops at once. The scheduled
  Cloudflare Worker that starts the runs (item 8) also checks that the status
  page was updated in the last day, and raises the alert if not.
- At hundreds of towns the status page needs search and filters, and the
  daily alert becomes a digest.

**Matters at:** now.

## 4. AI summary costs

**What broke.** Each run's spending limit was per town (`max_cost_per_run`),
so the network-wide worst case grew with the number of towns: $5,000 a day at
a thousand. The network's budget is $50 a month. At $5 a run each, Gloucester,
Malden, and Manchester alone could spend $15 a day, nine times the budget over
a month. The network budget below is now the main control, and $5 a run stays
as each town's ceiling. Summaries have cost about 2 to 12 cents each so far
(September's 173: $14.05). All towns share one Anthropic key, and its rate
limits apply to the whole network, not to each town.

**Plan.**
- One network budget, not per-town limits, as the main control: each town
  records what its summaries cost in a small ledger in its data, and a run
  summarizes only while the month's network total is under the budget ($50).
- One priority order across the network: upcoming agendas everywhere first,
  then the newest minutes, then older documents. A backlog (a new town's
  first months of minutes) is worked through with what's left of the month.
- The status page shows the month's spending against the budget.
- If rate limits bite, or to cut the cost, summaries move to one network job
  that uses the Batches API, which is cheaper.

*Done:*
- The ledger: `data/summary-costs.json`, each month's cost and documents,
  recounted from the saved summaries (which now record their cost), plus
  what cut-off responses cost, which leave no summary.
- The network budget: each run, the network's plan job adds up the month
  across towns and gives each town in the run an equal share of what's left
  (`pipeline.network budget`). A town stops at its share, or at its own
  per-run limit ($5, and 50 documents, by default), whichever comes first.
- The priority order, within each town: upcoming agendas, then documents
  fetched in the last two weeks for a meeting in the last two months, then
  the backlog. The backlog is paced: it may use what's left beyond a fifth of
  the budget (kept for new documents), spread over the rest of the month and
  every town.
- The month's spending against the budget, on the status page.

*Not done:* one priority order across towns (today each town orders its
own, within its share), and the Batches API.

**Matters at:** now, at $50 a month.

## 5. A bad release reaches every site at once

**What breaks.** The network repository runs every town on one engine
version, so a bad release breaks every site that uses the broken part the
next morning. The engine's tests build only Gloucester's site and a New
Hampshire site from saved data. CivicClerk and DotNetNuke parsing is tested,
but no test builds and checks a whole site for a town that uses them. At a
thousand towns, a pull request that moves `engine-version` builds and checks
every town, which takes hours for each release.

**Plan.**
- Engine tests build a small set of sample towns, one for each supported
  system (CivicPlus, CivicClerk, and DotNetNuke meetings; SeeClickFix with
  and without departments; Massachusetts and non-Massachusetts sources),
  from saved data, and run the site checks on each.
- Releasing is automatic: when the engine tests pass on `main` after a merge,
  that commit is released, so no one has to click through GitHub's release
  page.
- A few canary towns in the network repository run the newest release;
  `engine-version` moves for everyone else after a day with the canaries
  green. Moving it is a one-line pull request, which a bot can open. That
  pull request checks every page of the canaries and a sample of the rest.
- Rolling back is moving `engine-version` back and pointing sites at their
  previous builds.

*Done: automatic releases (a pull request's label picks a patch, major, or no
release). The engine's tests build Gloucester (Massachusetts, CivicPlus) and a
New Hampshire site. Not done: whole-site builds for CivicClerk, DotNetNuke,
and Agenda Center towns, and canary towns.*

**Matters at:** as soon as more than one town uses a reader; the sampled
upgrade checks at about 50 towns.

## 6. Who can change what

**What breaks.** In one repository, anyone with write access can change every
town. That's fine while Publick runs every town itself.

**Plan.** `CODEOWNERS` names who reviews each town's folder, and branch
protection on `main` requires that review. If a town ever wants to run its
own site, it gets its own repository calling `town.yml`, as towns do today.

**Matters at:** the first editor from outside Publick.

## 7. Every source was checked every day

**What breaks.** Every source is fetched and judged the same way: each run
asks every source for new data, and the status page asks "was this checked in
the last N days?". That fits meetings and 311, which change daily. It doesn't
fit a tax bill (certified once a year), a city budget (adopted once a year),
or school figures (once a year). Those are asked for daily or weekly anyway,
and when one of those checks fails, the status page shows the town as behind
even though its figures are the latest published. On 2026-09-29 Gloucester and
Malden showed "Some data delayed" because DLS refused the daily tax bill
check, while both had the latest certified year.

**Plan and progress.** Each figure source has a rhythm (`pipeline/rhythms.py`),
defined once with its fetcher (a state's package for state sources, so every
Massachusetts town shares Massachusetts's), with when each new period usually
appears, from the sources' own release history:

| Rhythm | Sources | Checked | Behind when |
|---|---|---|---|
| Continuous | Meetings, agendas, minutes, School Committee documents, 311, a city's permit file | Every run | A check fails, or the data is over 2 days old (`[freshness]` sources) |
| Monthly | Unemployment rate (BLS), building permits so far this year | Weekly | The next month is two months past its usual date |
| Yearly | Tax rate and bill, city budget, school figures, annual permits, ACS estimates, parcel counts | Monthly; weekly from two months before a new period's usual date | The next period is two months past its usual date (`grace_months`) |

The grace is generous because release dates slip: most yearly sources fill in
town by town over months, and 2026's were delayed by the federal shutdown and
a Census hold.

*Done:*
- Rhythms for every figure source; `pipeline.update` skips a figure step that
  isn't due (`--force` checks everything), and keeps each step's run of
  failures in `data/checks.json`.
- `pipeline.freshness` judges figure sources by period. A figure source whose
  last three checks failed is reported in the run's report (it needs
  attention) but isn't behind.
- The About page's data table shows each source's latest period and when the
  next usually appears.
- DLS: a 202 with nothing is asked again after 30, 60 and 120 seconds, and a
  refusal's error lists the headers that say why. The tax bill asks only for
  years not saved yet (and the newest saved), not six years every check.

*Not done:* the network status page's wording (in the network repository);
statewide sources fetched once per state (item 2). The single-town workflow,
`town.yml`, still runs every step daily; its budget, housing, and permit
fetchers skip within 7 days of the last fetch, as before.

**Matters at:** now: it's what makes the status page trustworthy.

## 8. Scheduled runs are late, and a waiting run can be dropped

**What breaks.** GitHub starts scheduled workflows when it can. On
2026-09-29, the four runs due at 5:17 to 8:17 a.m. Eastern started between
11:47 a.m. and 2:05 p.m. And every scheduled and manual run shares one
concurrency group (`network-update`) in which only one run can wait: a newer
run replaces the waiting one, silently. That day a manual run for Manchester
replaced a waiting scheduled run, so that run's town would have gone a day
without an update if it hadn't been refreshed by hand that morning. The same
day, a manual run queued behind a scheduled run for the same town started
from the commit it was queued at, and its data commit collided with the
first run's and was lost (37 new summaries).

**Plan.**
- **Runs pick towns by need, not by slot.** A run takes the towns whose last
  successful update is oldest, or older than about 20 hours, up to the run's
  size. A late or duplicate run then does useful work or nothing, a missed
  run is made up by the next, and a manual run never costs a town its daily
  update.
- **Towns queue separately.** The one-at-a-time rule moves from the whole
  workflow to each town's job, so runs for different towns don't block or
  replace each other, and two runs for one town wait in line.
- **Cloudflare starts the runs.** A scheduled Cloudflare Worker (a Cron
  Trigger, which fires on time) starts the Network workflow through GitHub's
  API, as "Run workflow" does. One GitHub schedule stays as a backup; with
  towns picked by need, a second start is harmless. This needs a GitHub token
  that can only start workflows in the network repository, stored as a Worker
  secret.
- The same Worker checks the status page (item 3).

*Done: runs don't lose data when two update the same town (engine v1.7.1).
Town jobs check out the latest `main` when they start, not the commit the run
was queued at, and `commit-data.sh` recovers from a collision: it keeps both
runs' changes, and the later run's lines where both changed the same lines.
Not done: towns picked by need, towns queued separately, and the Cloudflare
start.*

**Matters at:** now. Towns picked by need and queued separately first, since
they need nothing set up; then the Cloudflare start.

## 9. Runner capacity

**What breaks.** Every town's daily work runs on GitHub's runners. On the
free plan, at most 20 jobs run at once. Once the 311 and summary backlogs
clear, a town's daily job takes about 15 minutes, so a thousand towns need
about 250 runner-hours a day: about 12½ hours at 20 at a time, with no room
for a slow source or a retry. A new town's first 311 history takes much
longer (about 40 minutes a run for several weeks for a mid-size city).

**Plan.**
- Items 2 and 7 remove most of the work: statewide sources once per state,
  yearly sources checked yearly. What's left per town is meetings, 311, and
  summaries.
- At about 100 towns, the runs become a work queue: the scheduler adds
  whatever is due ("Malden meetings", "Massachusetts tax bills", "Manchester
  311"), and workers take items with per-vendor rate limits (item 2). A
  missed or late run only means a longer queue. The workers can stay GitHub
  jobs, or move to Cloudflare's compute or a small server, whichever is
  cheaper at that size.
- A paid GitHub plan raises the concurrent-job limit (to 60 on Team), which
  buys time without changing the design.
- A new town's 311 history is fetched by its own job over its first weeks, so
  it doesn't slow the daily run.

*Done: one town per job on manual and pull request runs, and browser checks
on every core, which took Manchester's full check from 481 to 168 seconds.*

**Matters at:** about 100 towns on the free plan.

## Stages

Done: the network repository, with all three towns moved in and a `[storage]`
table each; the status page (item 3); the default summary limit (item 4);
automatic releases (item 5); faster runs, with one town per job on manual
runs and checks on every core (item 9); Manchester's summaries, and the
agendas its city calendar links (engine v1.7.0); runs that don't lose data
when two update the same town (item 8, engine v1.7.1); figure sources on
their own rhythm, with the DLS retry (item 7, engine v1.8.0); page views for
every town on one GoatCounter site (v1.9.0); the network summary budget of
$50 a month, with its ledger and priority order (item 4), one daily alert
for towns behind, a town's failure not failing a daily run (item 3), and each
town's data size on the status page (item 1) (v1.10.0).

**Stage 1: now, to about 20 towns.** Everything here is needed at a thousand
towns too.

1. Towns picked by need and queued separately (item 8).
2. Statewide sources fetched once per state (item 2); the rhythms they build
   on are done (item 7).
3. The Cloudflare Worker that starts the runs and checks the status page
   (items 8 and 3), once there's a token for it.
4. Whole-site test builds for the other meeting systems (item 5).

**Stage 2: about 20 to 50 towns.**

1. Town data moved to R2, with git keeping config and code (item 1), when
   the status page's sizes say it's time.
2. Canary towns on the newest release, and sampled checks when
   `engine-version` moves (item 5).
3. `CODEOWNERS` and branch protection, before the first editor from outside
   Publick (item 6).

**Stage 3: about 100 to 1,000 towns.**

1. The work queue, with per-vendor rate limits and a new town's history
   fetched on its own (items 9 and 2).
2. A paid GitHub plan or other workers, as the queue's length shows the need
   (item 9).
3. Summaries through the Batches API, and a budget sized to the network
   (item 4).
4. A status page with search and filters, and a daily digest instead of an
   alert (item 3).
