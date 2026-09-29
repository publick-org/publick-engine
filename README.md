# Publick engine

The shared code behind the Publick network's town sites, such as [Gloucester Publick](https://gloucester-ma.publick.org) and [Manchester Publick](https://manchester-nh.publick.org): independent, read-only sites that publish public data about a town: how the city responds to 311 requests, what is on upcoming meeting agendas, the budget, schools, housing, and more.

Each site is static HTML built by a small Python pipeline and deployed to GitHub Pages by GitHub Actions. There is no server and no database.

This repository holds the code, page templates, styles, tests, and the daily workflow. Each town has its own small repository with only what is specific to it, and calls this engine at a pinned version. A fix made here reaches every town when it moves to the new version.

The code is MIT-licensed, so anyone can run a site like this for their own town, on their own domain and accounts: see [Starting a site for another town](#starting-a-site-for-another-town). The Publick name and "P" icon identify the Publick network's sites, so a site outside the network should use its own. [How Publick runs it](#how-publick-runs-it) lists the network's own setup.

## How a town uses the engine

A town's repository holds:

```
config/<town>.toml            Everything town-specific: name, domain, sources, sections
data/                         Collected data, committed by the daily job
site/static/share/<town>.png  The town's share image (optional; see Share image)
site/static/...               Any other file that replaces or adds to the engine's site/static/, such as its own favicon.svg
.github/workflows/site.yml    Calls the engine's workflow (below)
```

and a workflow that calls this repository's, pinned to a version:

```yaml
name: Update and deploy

on:
  push:
    branches: [main]
  pull_request:
  # Daily data refresh, early morning Eastern time.
  schedule:
    - cron: "23 10 * * *"
  workflow_dispatch:
    inputs:
      sources:
        description: Which data to update
        type: choice
        options: [all, meetings, "311"]
        default: all

# One run at a time, so two data commits never race.
concurrency:
  group: update-${{ github.ref }}
  cancel-in-progress: false

jobs:
  site:
    uses: publick-org/publick-engine/.github/workflows/town.yml@v1
    with:
      town: <town>
      sources: ${{ inputs.sources || 'all' }}
    secrets: inherit
    permissions:
      contents: write
      pages: write
      id-token: write
```

The engine's workflow checks out the engine at the same version as the workflow file itself, so the version is set in one place. It fetches new data and commits it (on the schedule or **Run workflow**), builds the site, checks it (`site_checks/`: every page's structure and links, and the WCAG 2.2 AA checks in light and dark mode at desktop and phone widths), and deploys it from `main`. Pull requests build and check only.

### Versions

Releases are tagged `v1.0.0`, `v1.1.0` and so on, with a `v1` tag that moves to the newest `v1.x` release. A town pinned to `@v1` takes each compatible release on its next run; one pinned to an exact tag moves when its pin is bumped, which Dependabot can do with a `github-actions` entry in the town's `.github/dependabot.yml`. A change that needs every town's config edited gets a new major version.

To release: merge to `main` with the engine tests passing, then publish a release on GitHub (**Releases → Draft a new release**) with a new tag such as `v1.2.0` on `main`. The release workflow (`.github/workflows/release.yml`) then moves `v1` to the same commit.

## Layout

```
pipeline/                   Python package
  config.py                 Finds the town's repository and loads config/<town>.toml
  update.py                 Daily: runs every fetch below for one town, each in its own process (town.yml runs the same list step by step)
  fetch_meetings.py         Daily: city calendars (CivicPlus, CivicClerk, DotNetNuke) -> data/meetings/
  fetch_minutes.py          Daily: Archive Center minutes -> data/meetings/minutes/
  fetch_drive_meetings.py   Daily: School Committee agendas and minutes (Google Drive) -> data/meetings/
  summarize.py              Daily: agenda and minutes PDFs -> readable text + summaries (AI) -> data/summaries/
  fetch_311.py              Daily: SeeClickFix -> data/311/requests.json (also --backfill YYYY-MM)
  compute_311.py            Daily: requests -> data/311/scorecard.json
  fetch_finance.py          Average single-family tax bill (Mass. DLS) -> data/finance/
  fetch_labor.py            Unemployment rate (BLS LAUS) -> data/labor/
  fetch_schools.py          Graduation, absenteeism, MCAS (DESE) -> data/schools/
  fetch_permits.py          Building and demolition permits (city Data Hub) -> data/permits/
  fetch_budget.py, fetch_housing.py   Budget (Mass. DLS) and housing (Census, SHI) -> data/finance/, data/housing/
  documents.py              Where agenda and minutes PDFs live: the town's bucket, or data/meetings/
  make_share_image.py       Draws the share image (and PNG icons for a town with its own icon)
  streets.py                Street-name matching for the street lookup
  freshness.py              Daily: fails the run when a data source stops updating
  civicplus.py, agendacenter.py, civicclerk.py, dnn.py, seeclickfix.py   Source parsers
  meeting_names.py          Which board a calendar entry is for, from its name
  geo.py                    Ward/precinct point-in-polygon lookup
  http.py                   Rate-limited HTTP client with retries
  build_site.py             Renders site/ + the town's data/ into the town's _site/
  deploy.py                 Publishes a built site to the sites bucket, for the Worker to serve; rollback and prune
  network.py                Runs many towns from one repository (towns/<town>/): plan, run a batch, report
site/templates/             Shared layout and per-record templates (meeting, board)
site/pages/                 One folder per section; each index.html becomes /<section>/
site/static/                CSS, icons, and other files copied as-is (a town's own site/static/ is laid on top)
site/static/vendor/leaflet/ Leaflet 1.9.4 map library, self-hosted (BSD-2-Clause)
tests/                      The engine's tests: pipeline, structure, link, and accessibility checks (offline)
tests/fixtures/town/        The tests' town: Gloucester's config, ward file, and share image
site_checks/                Checks for one town's built site, run by the town workflow before deploying
worker/                     The Cloudflare Worker that serves every site published with deploy.py, by hostname
.github/workflows/town.yml  The daily update, build, check, and deploy that town repositories call
.github/workflows/ci.yml    The engine's tests, on every push and pull request
```

## Build and test locally

Requires Python 3.11 or newer. Clone this repository next to a town's repository, then run commands from the town's repository with the engine on `PYTHONPATH`:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r ../publick-engine/requirements.txt -r ../publick-engine/requirements-dev.txt
python -m playwright install chromium
export PYTHONPATH=../publick-engine

python -m pipeline.fetch_meetings         # update meetings from the city website
python -m pipeline.fetch_311              # update 311 requests from SeeClickFix
python -m pipeline.compute_311            # recompute the 311 scorecard
ANTHROPIC_API_KEY=... python -m pipeline.summarize   # agenda text and previews (optional)
python -m pipeline.build_site             # writes _site/
python -m http.server -d _site 8000       # browse at http://localhost:8000
python -m pytest ../publick-engine/site_checks       # the checks the workflow runs before deploying
```

Commands use the town repository's one config file, or `--town <town>` or `TOWN` when there are several. To run from elsewhere, set `PUBLICK_TOWN_DIR` to the town's repository.

A town with a `[storage]` table keeps agenda and minutes PDFs in a bucket (see [Document storage](#document-storage)). A local fetch then needs the bucket's keys (`STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`), or set `DOCUMENTS_LOCAL=1` to keep PDFs under `data/meetings/` instead. Building the site needs no keys.

The engine's own tests run from this repository, offline, against saved Gloucester data in `tests/fixtures/`:

```sh
python -m pytest
```

## Starting a site for another town

Each town gets its own repository, with its own `config/<town>.toml`, its own `data/`, and its own site address. The code stays here.

1. **Create the town's repository** with the layout under [How a town uses the engine](#how-a-town-uses-the-engine): an empty `data/`, and the workflow file with `town` set.
2. **Write `config/<town>.toml`**, starting from a copy of [`tests/fixtures/town/config/gloucester.toml`](tests/fixtures/town/config/gloucester.toml). `[site]`, `[town]` and `[[sections]]` are required. Every other table is one data source. Leave out a table the town doesn't have and the command that fetches it does nothing:

   | Table | Source | Works for |
   |---|---|---|
   | `[meetings]`, `[archive]` | CivicPlus calendar and Archive Center | Towns whose website runs on CivicPlus. Without them the site has no meetings section or RSS feed |
   | `[meetings.civicclerk]`, `[meetings.dnn]` | A CivicClerk meeting portal, and a DotNetNuke (DNN Events) city calendar | Towns whose meetings are on either or both, like Manchester. See [Meetings from other calendars](#meetings-from-other-calendars) |
   | `[drive_meetings]` | Agendas and minutes in public Google Drive folders (Gloucester's School Committee) | Any board whose folders are laid out one per committee, with dates in file names |
   | `[seeclickfix]` | SeeClickFix 311 requests | Towns on SeeClickFix. `organization_id` is the town's SeeClickFix organization (its Open311 address, `seeclickfix.com/open311/v2/<id>/services.json`, lists its request types). `departments` (optional) keeps only the request types of the listed departments, by the `organization` names in that list; `scope_note` then says so on the 311 pages. Needs a ward boundary file in `data/static/` whose features carry `ward`, `district` (the precinct, e.g. `1-1`) and `population_2020`; `wards_publisher`, `wards_year` and `wards_url` credit its source on the 311 and About pages |
   | `[finance]` | Tax bill and budget (Mass. DLS) | Massachusetts |
   | `[schools]` | DESE | Massachusetts districts |
   | `[housing]` | Census and the Subsidized Housing Inventory | Anywhere for the Census parts; leave out `shi_url` outside Massachusetts. Parcel counts need `[finance]` |
   | `[labor]` | BLS unemployment | Anywhere BLS publishes a local series; set `bulk_file` to the state's file (defaults to Massachusetts's) |
   | `[permits]` | The city's permit spreadsheet | Gloucester's Data Hub layout only |
   | `[summaries]` | AI summaries of agendas and minutes | Anywhere, with `ANTHROPIC_API_KEY`. `model`, `input_price` and `output_price` (dollars per million tokens) are required; nothing is sent without prices. `max_per_run` (documents) and `max_cost_per_run` (dollars) default to 50 and $5 |
   | `[freshness]` | Stale-data alerts | List only the sources the town has |
   | `[storage]` | Keeps agenda and minutes PDFs in a bucket instead of git | Recommended for every town; see [Document storage](#document-storage) |

   Rewrite the hand-written content for the new town from its own sources: `[meetings.aliases]`, `[archive.aliases]`, `[participation.*]`, `[[glossary]]` and `[[seeclickfix.annotations]]`.
3. **List only the town's sections** in `[[sections]]`. Page folders under `site/pages/` for sections that aren't listed are not built, and their data is ignored.
4. **Fetch and build locally**, from the town's repository, to see what the town's sources return:

   ```sh
   export PYTHONPATH=../publick-engine
   python -m pipeline.fetch_meetings && python -m pipeline.fetch_minutes
   python -m pipeline.fetch_311 --backfill 2024-01 && python -m pipeline.compute_311
   python -m pipeline.build_site && python -m http.server -d _site 8000
   ```

5. **Name and brand the site** in `[site]`:
   - `name`, plus `name_prefix` (shown in dark ink) and `name_suffix` (in the accent color). Include a trailing space in `name_prefix` for two words, e.g. `name_prefix = "Gloucester "`.
   - `domain`: the site's address.
   - `network` (optional): the family of sites it belongs to, named in every footer. Leave it out for a stand-alone site.
   - `contact_email`: shown on the About and Accessibility pages and used by the "Report an error" buttons.
   - `[site.colors]` (optional): the town's own colors, as `"#rrggbb"`. `primary` (links, buttons, map markers), `primary_dark` (headings, rules, the masthead), `primary_soft` (light backgrounds), and `accent` (the current page in the menu, flags, notices). Take them from the city's own website and check each against white for WCAG AA contrast (4.5:1). `network` is the network's name in the masthead and share image; Publick's is slate `#2c4a63`, the same for every town. Unset colors keep the defaults (Gloucester's navy and maroon).

   The engine's icon is the Publick "P". A site outside the network draws its own in the town's `site/static/favicon.svg`. Then run `python -m pipeline.make_share_image` for the share image (and PNG icons, for a town with its own icon).
6. **Deploy** as under [Deploying](#deploying), and set up [Document storage](#document-storage) and the [secrets](#secrets).

Page text is written for a Massachusetts city. A town (rather than a city), or a town outside Massachusetts, needs a read through the page wording.

## Meetings from other calendars

A town whose website isn't on CivicPlus lists its calendars as tables inside `[meetings]`, instead of `base_url` and `calendar_feed`. It can have both; Manchester's are below.

```toml
[meetings]
calendar_url = "https://www.manchesternh.gov/Government/City-Calendars"   # linked as "the city calendar"
archive_url = "https://www.manchesternh.gov/Departments/City-Clerk/Meeting-Minutes-and-Agendas"
archive_name = "city's Meeting Minutes and Agendas page"   # where earlier agendas and minutes are
governing_body = "Board of Mayor and Aldermen"             # "City Council" if left out
# notify_url = "..."                                       # the city's meeting alerts, if it has them
documents = false   # agendas and minutes aren't collected yet: see below
boards = ["Board of Mayor and Aldermen", "Planning Board", ...]

[meetings.aliases]
"ZBA" = "Zoning Board of Adjustment"

# The city's CivicClerk portal (the address its agenda links go to), through its public API.
[meetings.civicclerk]
api_url = "https://manchesternh.api.civicclerk.com/v1"
portal_url = "https://manchesternh.portal.civicclerk.com"
since = "2026-01-01"   # the first run collects meetings from here; later runs re-read the last 60 days (recheck_days) and ahead

# A DotNetNuke city calendar: the month view's address, and the module number in its event links.
[meetings.dnn]
calendar_url = "https://www.manchesternh.gov/Government/City-Calendars"
module_id = 3737
since = "2026-01-01"   # the first run reads each month from here; later runs this month and months_ahead (default 1)
exclude_pattern = '...' # entries to skip; include_pattern keeps only matching ones
```

- **Board names.** Calendar names that aren't uniform ("PH-1 Board of Mayor and Aldermen", "Special Meeting-Board of Mayor and Aldermen") are matched to the longest name in `boards` (or key in `[meetings.aliases]`) that they contain, ignoring case, punctuation and "&"/"and". A name that matches none is cleaned up by rule: status words, "Special Meeting of the", and endings such as "Meeting" or "Public Hearings" are removed. A board whose own name starts with "Special" ("Special Committee on Airport Activities") should be listed, or it reads as a special meeting of another committee.
- **Both calendars.** Meetings the DNN calendar links to the CivicClerk portal are collected from CivicClerk only. Use `exclude_pattern` for the rest of those boards' entries.
- **CivicClerk times** are local, although the API marks them UTC.
- **CivicPlus Agenda Center.** A CivicPlus town that posts its boards' agendas and minutes in the Agenda Center rather than on the calendar (Malden) uses `[meetings.agenda_center]`. One request to its search page lists every board's meetings for a date range; upcoming meetings' agendas are saved, and a revised agenda (same number, new posted time) is recorded in the meeting's history. `fetch_minutes` downloads the minutes linked from each meeting since `since`, up to `max_minutes_per_run` (default 60) a run. The board is the Agenda Center category (renamed by `[meetings.aliases]` if listed); `committees` names the committees whose meetings are posted under another board's category, matched in the row's title as `boards` are. There are no times or places in the listing, so meeting pages show the date only.

  ```toml
  [meetings.agenda_center]
  base_url = "https://www.cityofmalden.org"
  since = "2026-01-01"     # the first run lists meetings from here; later runs re-read the last 60 days (recheck_days) and 60 ahead (days_ahead)
  exclude_categories = ["Community Outreach"]

  [meetings.agenda_center.committees."City Council"]
  "Finance Committee" = "City Council Finance Committee"
  ```
- **`documents = false`** is for a town with a calendar but no agendas and minutes collected yet. Each meeting page links to its agenda (and minutes) where the city posts them, and the pages that need the documents are left out: decisions, search, the RSS feed, and agenda items in the street lookup.

## Adding a section

1. Add a `[[sections]]` entry to `config/<town>.toml`. It appears in the main navigation.
2. Create `site/pages/<slug>/index.html` extending `base.html`. It is served at `/<slug>/`.
3. Sub-pages go in sub-folders: `site/pages/<slug>/<name>/index.html` is served at `/<slug>/<name>/`.
4. Pages generated from data (one per record) use a template in `site/templates/` and are added in `pipeline/build_site.py`.

New pages are picked up by the tests and the site checks automatically. A section belongs to the engine, so every town that lists it gets it.

## Data collection

- The workflow runs every morning, fetches new data, commits any changes under `data/`, then tests, builds, and deploys.
- Requests identify the site in the User-Agent, wait between calls, and back off on errors. The city website rate-limits bursts of requests.
- Records are never deleted. When a source changes something after posting it, the change is recorded in the record's `history`.
- Meeting page URLs are fixed when a meeting is first recorded, so links keep working if the city renames or reschedules it.

## Accessibility

The site targets [WCAG 2.2](https://www.w3.org/TR/WCAG22/) Level AA. Every build runs axe-core against each page at desktop and 320px widths, and checks reflow, text resizing, and keyboard access. A failing check blocks deployment. The sites have only a light theme and every page declares `color-scheme: light`, so a reader in dark mode sees the same page; a town's checks hold every page to that, and the engine's own tests also run axe in dark mode.

A network's daily runs set `PUBLICK_CHECK_PAGES=sample` to run the axe checks on a sample of each town's pages instead: every hand-written page, and the first and largest page of each record template (a meeting, a board, a ward, a 311 category). The same templates render every page of a kind, so the sample covers each template, and the largest page is the likeliest to hold data that breaks a layout. Structure and link checks still cover every page, and any change to the engine or a town's config gets the full run (`site_checks/pages.py`).

Rules for new pages:

- One `<h1>` per page, with headings in order.
- Every chart has a data table with the same figures. Every map has a list of the same locations, and the map is not the only way to reach any information.
- Don't use color alone to carry meaning. Pair it with text, a pattern, or a shape.
- All controls work with a keyboard and have a visible focus style.
- Link text makes sense on its own (no "click here").
- Pages work without JavaScript wherever possible.
- Check each new section by hand with a keyboard and a screen reader before launch.

## Share image

The picture shown when a page is shared is the town's `site/static/share/<town>.png`. Redraw it after changing the site name or tagline, from the town's repository:

```
python -m pipeline.make_share_image
```

## Deploying

Pushes to the town repository's `main` build, check, and deploy. Pull requests build and check only. The daily schedule and the **Run workflow** button also fetch new data first.

One-time setup, with `<domain>` the site's address (the `domain` in its config) and `<owner>` the GitHub account or organization that owns the repository:

1. **Verify the domain** so no other GitHub account can claim it: **Settings → Pages → Add a domain** on the account or organization that owns the repository, then add the TXT record it gives you at your DNS provider. Verifying a parent domain (e.g. `example.org`) also covers its subdomains, which suits a family of sites like `<town>.example.org`.
2. **Repository settings → Pages → Source:** GitHub Actions.
3. **DNS records** at your DNS provider:
   - For a subdomain such as `gloucester.example.org`: a `CNAME` from it to `<owner>.github.io`.
   - For a bare domain such as `example.org`: `A` records `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`, and a `www` `CNAME` to `<owner>.github.io`.
   - On Cloudflare, set these records to **DNS only** (grey cloud). Proxied records stop GitHub from issuing the site's certificate.
4. **Repository settings → Pages → Custom domain:** enter `<domain>`. Once the certificate is issued, turn on **Enforce HTTPS**.

### Moving a site to a new domain

GitHub Pages serves one custom domain per repository, so when a site moves, its old domain stops working unless it is redirected. One way, with Cloudflare:

1. Create the new domain's DNS records (step 3 above) and wait for them to resolve.
2. Change `domain` in the town's config and merge; then set the same domain under **Repository settings → Pages → Custom domain** straight away. Until then, pages name the new address while being served from the old one.
3. Add the old domain to Cloudflare (**Add a domain**, Free plan) and switch its nameservers at the registrar to the two Cloudflare gives. Before switching, turn off DNSSEC at the registrar if it's on.
4. In the old domain's **DNS → Records**, replace the GitHub records with one `A` record for `@` and one for `www`, both pointing to `192.0.2.1` with **Proxied** (orange cloud). The address is never reached; Cloudflare answers first.
5. **Rules → Redirect Rules → Create rule:** match all incoming requests, with a **Dynamic** redirect to `concat("https://<new domain>", http.request.uri.path)`, status **301**, and **Preserve query string** on. Old links, including deep ones like `/meetings/…`, land on the same page at the new address.

If nobody relies on the old domain, deleting its GitHub records is enough; don't leave them pointing at GitHub Pages with no repository claiming the domain.

### Serving many sites from one bucket

GitHub Pages serves one custom domain per repository. A network that runs many towns from one repository publishes each built site to a Cloudflare R2 bucket instead, and one Cloudflare Worker (`worker/index.js`) serves them all, choosing the site by hostname:

```sh
python -m pipeline.deploy publish [--town <town>] [--site _site]   # the site goes live at its config's domain
python -m pipeline.deploy rollback [--town <town>] [--build <build>] # back to the previous build, or a named one
python -m pipeline.deploy prune [--keep 10] [--dry-run]              # delete old builds and unused files
```

Files are stored once by content and shared across sites, so a daily publish uploads only what changed. A site goes live with one write, after all its files are uploaded. See `pipeline/deploy.py` for the bucket layout.

Setup, once for the network:

1. **Create a bucket** for sites (**R2 object storage → Create bucket**), separate from the documents bucket and with no public address: only the Worker reads it.
2. **Create an API token** with *Object Read & Write* on that bucket only. Set the secrets `SITES_ACCESS_KEY_ID` and `SITES_SECRET_ACCESS_KEY`, and `SITES_ENDPOINT` (`https://<account id>.r2.cloudflarestorage.com`) and `SITES_BUCKET`.
3. **Deploy the Worker** from `worker/index.js` with an R2 binding named `SITES` to that bucket.
4. **Route the sites to it:** a proxied (orange cloud) DNS record for each site's hostname, or one wildcard such as `*.example.org`, and a Worker route such as `*.example.org/*`. A site's specific DNS record takes precedence over the wildcard, so delete a town's GitHub Pages `CNAME` to move it to the Worker.

## Document storage

Agenda and minutes PDFs average well over a megabyte, git keeps every version forever, and a GitHub Pages site may be at most 1 GB. Without a `[storage]` table they're committed under `data/meetings/` and copied into the site, which works for a small or short-lived town. With one, they go to an S3-compatible bucket and pages link to the bucket's public address. Git keeps each document's text, summary and SHA-256 hash, so the site is still rebuilt entirely from the repository.

One bucket can serve several towns, each under its own `prefix`. Cloudflare R2 is the suggested host: no charge for downloads, and the first 10 GB are free. Steps 1–3 are done once per bucket; each town then needs step 4, and step 5 if it already has PDFs in git.

1. **Create the bucket** in Cloudflare: **R2 object storage → Create bucket**.
2. **Give it a public address:** the bucket's **Settings → Custom Domains → Add**, e.g. `files.example.org`. The domain's DNS must be on Cloudflare, in the same account. The bucket's `r2.dev` address is rate-limited and meant only for testing.
3. **Create an API token:** **R2 object storage → API Tokens → Manage → Create Account API token**, with *Object Read & Write* on that bucket only. Save its access key ID and secret as the secrets `STORAGE_ACCESS_KEY_ID` and `STORAGE_SECRET_ACCESS_KEY`.
4. **Add the table** to `config/<town>.toml`, with the account ID from the R2 overview page:

   ```toml
   [storage]
   endpoint = "https://<account id>.r2.cloudflarestorage.com"
   bucket = "<bucket name>"
   public_url = "https://files.example.org"
   prefix = "<town>"   # optional; defaults to the config file's name
   ```

5. **Run the workflow.** New PDFs go straight to the bucket. The **Move saved documents to storage** step uploads the ones already in `data/meetings/`, checks each copy, and commits their removal. Until a file is moved, the site keeps linking to its copy in the repository.

The files remain in the repository's git history. Shrinking the history means rewriting it, which is a separate decision.

## Data and licenses

The code is under the [MIT License](LICENSE). Data keeps the terms of its source; each town's `data/README.md` lists its sources. 311 data comes from [SeeClickFix](https://seeclickfix.com) under [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/). Other sources are listed on the site's [About page](https://gloucester-ma.publick.org/about/).

## Email

`contact_email` can be any address. A forwarding address on the site's own domain keeps a personal inbox private and lets each town's mail be sorted or handed to someone else later. Cloudflare **Email Routing** does this for free: **Email Routing → Routing rules → Create address**, action **Send to an email**. It only receives mail; replies go out from the destination inbox.

## Secrets

Set these under the repository's **Settings → Secrets and variables → Actions**. With several town repositories in one GitHub organization, set them once as **organization** secrets instead, so every town's workflow gets them; a repository secret of the same name overrides it.

- `ANTHROPIC_API_KEY` (optional): enables agenda and minutes text and summaries. Without it the step is skipped.
- `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY` (needed with `[storage]`): an R2 API token for the documents bucket. See [Document storage](#document-storage).
- `SITES_ENDPOINT`, `SITES_BUCKET`, `SITES_ACCESS_KEY_ID`, `SITES_SECRET_ACCESS_KEY` (needed to publish to the sites bucket): see [Serving many sites from one bucket](#serving-many-sites-from-one-bucket).
- `BLS_API_KEY` (optional): free key from bls.gov/developers for the unemployment rate. Without it the job uses BLS's keyless limit, then falls back to the bulk data file.

## How Publick runs it

The Publick network's own setup, for reference. Everything here belongs to Publick; another operator uses their own.

| What | Publick's value |
|---|---|
| GitHub organization | `publick-org`: this engine, and one repository per town named `<town>-<state>` (e.g. `gloucester-ma`) |
| Site addresses | `<town>-<state>.publick.org`, a `CNAME` to `publick-org.github.io`; `publick.org` is verified for the organization |
| DNS and registrar | Cloudflare |
| Site names | `<Town> Publick` (`name_prefix = "<Town> "`, `name_suffix = "Publick"`, `network = "Publick"`), sharing the Publick "P" icon |
| Documents bucket | R2 bucket `publick-documents` at `https://files.publick.org`, `prefix = "<town>-<state>"` |
| Email | `<town>-<state>@publick.org` for each town and `hello@publick.org`, forwarded by Cloudflare Email Routing |
| Secrets | `ANTHROPIC_API_KEY`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`, `BLS_API_KEY` |

Adding a town to the network: a new `publick-org/<town>-<state>` repository ([Starting a site for another town](#starting-a-site-for-another-town)), a `CNAME` for `<town>-<state>` in publick.org's DNS, a `[storage]` table with the town's prefix, and a `<town>-<state>@publick.org` routing rule.
