# Roadmap: from a few towns to a thousand

Publick's towns started as one repository each: a config file, its data, and a
short workflow that calls the engine at a pinned version, deployed to GitHub
Pages. That works for a handful of towns. At hundreds it means hundreds of
repositories to create, schedule, pin, and watch.

**Decision, now done: towns live in one repository.** Publick runs the towns
itself, so there is no need for each town to have a repository of its own. The
network repository, [publick-org/publick.org](https://github.com/publick-org/publick.org),
holds every town's config and data (Gloucester, Malden, and Manchester so far),
one workflow runs them in batches, and Cloudflare serves every site. The engine
stays its own repository with its own tests and releases, and a single-town
repository calling `town.yml` keeps working, so a town that wants to run its
own site later can.

The first section describes the network repository as built. The numbered
items are what still has to change, each with what breaks, the plan, what's
done, and when it matters. Items are numbered for reference, not order; the
suggested order is at the end.

## The network repository

**Layout.**

```
publick-org/publick.org
  engine-version              the engine release every town runs, e.g. v1.5.0
  towns/gloucester-ma/
    config/gloucester.toml
    data/                     including run.json, the last daily run's result
    site/static/share/gloucester.png
  towns/malden-ma/, towns/manchester-nh/
  home/                       the publick.org homepage
  scripts/                    build_home.py (its town lists), build_status.py (publick.org/status/)
  .github/workflows/network.yml
```

The engine reads a town's config, data, and static files from
`PUBLICK_TOWN_DIR`, so each town's commands run unchanged with it set to the
town's folder, each town in its own process (`pipeline/network.py`).

**The daily run.** `network.yml` runs four times across the early morning.

1. A first job gives each town one of the four runs, from a stable hash of its
   folder name, and splits the run's towns into batches.
2. Town jobs run in a matrix, four towns per job, each checking out only its
   towns' folders. Python packages and Playwright are installed once per job.
   `max-parallel` keeps the run inside the organization's limit on concurrent
   jobs.
3. Each town fetches new data, is built and checked, and is published on its
   own if its checks pass; a town that fails keeps its last good site. Each
   job commits its towns' data, retrying against the other jobs' pushes.
4. A report job writes one table of every town in the run and fails the run
   once if any town needs attention, so GitHub sends one email per run.
5. The homepage and the status page are rebuilt from `main`, with the run's
   data, and published.

A pull request that changes a town's folder builds and checks only that town.
Adding a town is a pull request that adds its folder.

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

**Secrets.** One set, at the organization or repository level: the Anthropic
key, the storage keys, the sites bucket keys, the BLS key. Nothing is set per
town.

**What this replaced.** Staggered cron lines in each town's workflow, a
command to create repositories and Pages settings, warnings for towns pinned
to odd engine versions, and re-enabling town workflows that GitHub turned off
after 60 quiet days. None of those are needed with one repository, one pin,
and one schedule that commits every day.

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

**Plan.** A shared job in the network run fetches each statewide or national
file once and passes it to the town jobs. Town fetchers use the shared copy
when it's there and go to the source only if it isn't. Town-specific sources
(meetings, 311, a city's permits) stay per town, spread across the morning
by the schedule.

*Done so far: New Hampshire's statewide files (tax rates, school figures)
are saved once a year into the engine (`pipeline/states/nh/figures/`), because
the state's websites refuse automated requests; every New Hampshire town reads
its rows from there. The other shared sources are still fetched per town.*

**Matters at:** tens of towns in one state.

## 3. Monitoring by email

**What breaks.** When a source stops updating, the run fails and GitHub
emails the owner. With every town in one workflow, a run with any failing
town fails, several times a morning, with no overview of which towns are
behind or why.

**Plan and progress.**
- A town's failure doesn't fail the run. Each town job records what happened
  (every source's last update, the engine version, build and deploy result).
  *Done: a fetching run writes it to the town's `data/run.json`. Not done:
  a failing town still fails its run.*
- The final job of each run writes a network status page on publick.org: one
  table of towns behind, towns whose runs failed, and totals, plus the
  repository size (item 1) and summary spending (item 4). *Done, without the
  size and spending: [publick.org/status/](https://publick.org/status/), built by
  the network repository's `scripts/build_status.py` after each run. It's public,
  so it says in plain words which data on a site may be out of date and leaves
  the run's internals to the run's summary.*
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

*Done: the default per-run limit ($5, and 50 documents). Not done: the
ledger, monthly limits, and the Batches API.*

**Matters at:** the rest as soon as several towns have summaries turned on.

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
- Releasing is automatic: when the engine tests pass on `main` after a merge,
  that commit is released, so no one has to click through GitHub's release
  page.
- A few canary towns in the network repository run the newest release;
  `engine-version` moves for everyone else after a day with the canaries
  green. Moving it is a one-line pull request, which a bot can open.
- Rolling back is moving `engine-version` back and pointing sites at their
  previous builds.

*Done: automatic releases (a pull request's label picks a patch, major, or no
release). The engine's tests build Gloucester (Massachusetts, CivicPlus) and a
New Hampshire site. Not done: whole-site builds for CivicClerk, DotNetNuke,
and Agenda Center towns, and canary towns.*

**Matters at:** as soon as more than one town uses a reader.

## 6. Who can change what

**What breaks.** In one repository, anyone with write access can change every
town. That's fine while Publick runs every town itself.

**Plan.** `CODEOWNERS` names who reviews each town's folder, and branch
protection on `main` requires that review. If a town ever wants to run its
own site, it gets its own repository calling `town.yml`, as towns do today.

**Matters at:** the first editor from outside Publick.

## Suggested order

Done: the default summary spending limit (item 4); automatic releases (item 5);
the network repository, with all three towns moved in and a `[storage]` table
each; the status page (item 3).

Next:

1. The rest of item 3: a town's failure doesn't fail the run, one email a day
   when a town is behind, and the outside check that the status page is still
   being updated.
2. Whole-site test builds for the other meeting systems (item 5).
3. Canary towns on the newest release (item 5).
4. The repository's size and growth on the status page, then moving
   fast-growing files to the bucket as that shows the need (item 1).
5. The shared statewide fetch, once a state has more than a few towns
   (item 2).
6. The cost ledger and monthly limits, once more towns have summaries
   (item 4).
7. `CODEOWNERS` and branch protection, before the first editor from outside
   Publick (item 6).
