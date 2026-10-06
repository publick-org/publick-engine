# Publick engine

The shared code behind the Publick network's town sites, such as [Gloucester Publick](https://gloucester-ma.publick.org) and [Manchester Publick](https://manchester-nh.publick.org): independent, read-only sites that publish public data about a town: how the city responds to 311 requests, what is on upcoming meeting agendas, the budget, schools, housing, and more.

Each site is static HTML built by a small Python pipeline in GitHub Actions. There is no server and no database.

This repository holds the code, page templates, styles, tests, and the workflows. What's specific to a town (its config, its data, its share image) lives elsewhere, and runs this engine at a pinned version, in one of two ways:

- **A network of towns in one repository**, as Publick runs its own: [publick-org/publick.org](https://github.com/publick-org/publick.org) holds every town's folder, runs them in daily batches (`pipeline/network.py`), and serves every site from a Cloudflare R2 bucket through one Worker. See [Serving many sites from one bucket](#serving-many-sites-from-one-bucket).
- **One repository for one town**, deployed to GitHub Pages by the engine's `town.yml`, described next.

A fix made here reaches every town when it moves to the new version.

The code is MIT-licensed, so anyone can run a site like this for their own town, on their own domain and accounts: see [Starting a site for another town](#starting-a-site-for-another-town). The Publick name and "P" icon identify the Publick network's sites, so a site outside the network should use its own. [How Publick runs it](#how-publick-runs-it) lists the network's own setup.

## A town in its own repository

This is the stand-alone way to run a town. A town in a network repository has the same files in its folder, `towns/<town>-<state>/`, and the network's own workflow runs it instead of `town.yml`: see the [network's README](https://github.com/publick-org/publick.org#readme).

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

The engine's workflow checks out the engine at the same version as the workflow file itself, so the version is set in one place. It fetches new data and commits it (on the schedule or **Run workflow**), builds the site, checks it (`site_checks/`: every page's structure and links, and the WCAG 2.2 AA checks at desktop and phone widths), and deploys it from `main`. Pull requests build and check only.

### Versions

Releases are tagged `v1.0.0`, `v1.1.0` and so on, with a `v1` tag that moves to the newest `v1.x` release. A town pinned to `@v1` takes each compatible release on its next run; one pinned to an exact tag moves when its pin is bumped, which Dependabot can do with a `github-actions` entry in the town's `.github/dependabot.yml`. A change that needs every town's config edited gets a new major version.

A network repository pins every town at once, with an exact tag in one `engine-version` file; publick.org's moves to each new release once every town passes on it (see its [README](https://github.com/publick-org/publick.org#how-it-runs)).

Releasing is automatic, once a day. Each morning at 08:20 UTC, before the network's daily runs, `.github/workflows/release.yml` releases the newest commit on `main` whose engine tests passed, with everything merged since the last release, as the next minor version (`v1.4.0` → `v1.5.0`), and moves `v1` to it. Label a pull request `patch` for a patch version (the release is a patch when every pull request in it is), `major` for a change that needs every town's config edited (`v2.0.0`; towns on `@v1` stay there until they move), or `no release` to leave it out; a pull request that changes only Markdown files is left out without a label. An urgent fix needn't wait: **Actions → Release → Run workflow** releases now. A release published by hand (**Releases → Draft a new release**) also moves its major tag.

## Layout

```
pipeline/                   Python package
  config.py                 Finds the town's repository and loads config/<town>.toml
  update.py                 Daily: runs every fetch below for one town, each in its own process (town.yml runs the same list step by step)
  fetch_meetings.py         Daily: city calendars (CivicPlus, a CivicPlus Agenda Center, CivicClerk, DotNetNuke, a calendar with a documents page),
                            a school district's calendar feed or page of dates -> data/meetings/
  listings.py               One meeting listed in more than one place (a calendar and an Agenda Center, a repost) shown as one;
                            `python -m pipeline.listings` lists the meetings put together, and why
  fetch_minutes.py          Daily: Archive Center minutes -> data/meetings/minutes/
  fetch_drive_meetings.py   Daily: School Committee agendas and minutes (Google Drive) -> data/meetings/
  fetch_finalsite_meetings.py   Daily: a school board's meetings, agendas and minutes (a Finalsite district website, Google Docs) -> data/meetings/
  summarize.py              Daily: agenda and minutes PDFs -> summaries (AI), and full text for scans (AI) -> data/summaries/,
                            new documents first, with each month's cost in data/summary-costs.json
  translate.py              Summaries in the site's other languages, from the English summary (AI), checked without AI and
                            reviewed by AI -> data/summaries/<language>/, and drafts of the town's own text and names
                            -> data/strings/; run by summarize.py within its budget. `python -m pipeline.translate drafts` lists drafts
  factcheck.py              Each summary checked against its PDF's own text (no AI): numbers, amounts, dates, names, vote counts,
                            and each decision's quote from the minutes and its outcome (approved, denied, ...), so a dropped "not"
                            is caught. Kept in data/summaries/; what isn't in the document isn't shown. `python -m pipeline.factcheck` lists it
  evaluate.py               The minutes prompt run against the real model on minutes checked by hand (evals/minutes.json), with the
                            checks, before a prompt change ships: `ANTHROPIC_API_KEY=... python -m pipeline.evaluate` (about $1 to $2)
  pdftext.py                A PDF's own text: laid out as full text for a supported style (the software that made it), checked word for word; plain text for search
  votes.py                  Roll call votes from a supported style's minutes, checked against the body's [officials] members (no AI);
                            read with the minutes' text, kept in data/summaries/, not yet shown. `python -m pipeline.votes` lists them for review
  fetch_311.py              Daily: SeeClickFix -> data/311/requests.json (also --backfill YYYY-MM)
  compute_311.py            Daily: requests -> data/311/scorecard.json
  fetch_finance.py, fetch_budget.py, fetch_schools.py   Tax bill, budget, school figures, from the town's state's source -> data/finance/, data/schools/
  states/                   What differs by state (see States below): states/ma/ is Massachusetts (DLS, DESE),
                            states/nh/ New Hampshire (DRA, Department of Education, NH GRANIT), states/ct/
                            Connecticut (OPM on data.ct.gov, EdSight), states/vt/ Vermont (PVR, VCGI, AOE),
                            states/me/ Maine (MRS, Maine GeoLibrary, ESSA Dashboard), states/ri/ Rhode Island
                            (Division of Municipal Finance, RIDE); states/ma/dls.py can fetch each DLS report once
                            for every town (a network's states/)
  fetch_labor.py            Unemployment rate (BLS LAUS) -> data/labor/
  fetch_place.py            Whether the town is a city or a town by law (Census TIGERweb) -> data/place.json, once;
                            the site's wording says "the city" or "the town" to match (i18n.py)
  fetch_permits.py          Building and demolition permits (city Data Hub) -> data/permits/
  fetch_housing.py          Housing (Census, plus the state's own figures: Massachusetts's SHI and parcels) -> data/housing/
  documents.py              Where agenda and minutes PDFs live: the town's bucket, or data/meetings/
  make_share_image.py       Draws the share image (and PNG icons for a town with its own icon)
  streets.py                Street-name matching for the street lookup
  freshness.py              Daily: whether each data source is still updating (fails a single town's run when one isn't)
  absences.py               Why something isn't shown, for the pages to say: a meeting's minutes, agenda or summary, a calendar
                            or a section's sources behind, a section the town doesn't have
  rhythms.py                How often each figure source publishes: when it's checked, and when it's behind
  civicplus.py, agendacenter.py, civicclerk.py, dnn.py, filelist.py, finalsite.py, ical.py, schedule.py, seeclickfix.py   Source parsers
  meeting_names.py          Which board a calendar entry is for, from its name
  geo.py                    Ward/precinct point-in-polygon lookup
  http.py                   Rate-limited HTTP client with retries
  build_site.py             Renders site/ + the town's data/ into the town's _site/, with a sitemap dating each page by when it last changed
  digest.py                 The weekly digest: each Sunday's issue of the week's meetings and the minutes posted the week
                            before (no AI), built into /digest/ with a feed for email
  structured.py             Structured data (schema.org JSON-LD) for search engines: the site's name, meetings as events,
                            breadcrumbs, and the downloads as datasets
  i18n.py                   The sites' wording in other languages: the language being built, and
                            `python -m pipeline.i18n update` to keep site/strings/ current
  common_strings.py         Boards, roles, seats, and summaries many towns share, translated once in site/strings/
  deploy.py                 Publishes a built site to the sites bucket, for the Worker to serve; rollback and prune
  network.py                Runs many towns from one repository (towns/<town>-<state>/): plan (the towns that are due),
                            run a batch, report, behind (the daily alert), budget (the summary budget's shares),
                            states (statewide sources); a fetching run writes each town's result to its data/run.json,
                            with a few counts for the network homepage (boards followed, meetings in the next 14 days)
site/templates/             Shared layout and per-record templates (meeting, board)
site/pages/                 One folder per section; each index.html becomes /<section>/
site/states/<state>/        Each state's own pages (schools, budget) and page parts (its About page sources)
site/strings/<language>.po  Each language's translation of the English wording marked in templates and code
site/static/                CSS, icons, and other files copied as-is (a town's own site/static/ is laid on top)
site/static/vendor/leaflet/ Leaflet 1.9.4 map library, self-hosted (BSD-2-Clause)
tests/                      The engine's tests: pipeline, structure, link, and accessibility checks (offline)
tests/fixtures/town/        The tests' town: Gloucester's config, ward file, and share image
site_checks/                Checks for one town's built site, run by the town workflow before deploying
worker/                     The Cloudflare Workers: index.js serves every site published with deploy.py, by
                            hostname; scheduler-index.js starts a network's daily runs on time and watches they finish
.github/workflows/town.yml  The daily update, build, check, and deploy that town repositories call
.github/workflows/ci.yml    The engine's tests, on every push and pull request
.github/workflows/release.yml  The daily release (see Versions)
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

In a network repository, clone the engine next to it and run the same commands from the network repository's root, with `PUBLICK_TOWN_DIR` set to the town's folder:

```sh
export PYTHONPATH=../publick-engine PUBLICK_TOWN_DIR=towns/<town>-<state>
python -m pipeline.build_site             # writes towns/<town>-<state>/_site/
```

A town with a `[storage]` table keeps agenda and minutes PDFs in a bucket (see [Document storage](#document-storage)). A local fetch then needs the bucket's keys (`STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`), or set `DOCUMENTS_LOCAL=1` to keep PDFs under `data/meetings/` instead. Building the site needs no keys.

The engine's own tests run from this repository, offline, against saved Gloucester data in `tests/fixtures/`, with whole sites for Manchester, Malden and Wallingford built from their meeting systems' saved pages (`tests/test_sample_towns.py`):

```sh
python -m pytest
```

## Starting a site for another town

Each town gets its own `config/<town>.toml`, its own `data/`, and its own site address, in its own repository or in a folder of a network repository (publick.org's checklist is [ADDING-A-TOWN.md](https://github.com/publick-org/publick.org/blob/main/ADDING-A-TOWN.md)). The code stays here.

1. **Create the town's repository** with the layout under [A town in its own repository](#a-town-in-its-own-repository): an empty `data/`, and the workflow file with `town` set. In a network, add its folder instead.
2. **Write `config/<town>.toml`**, starting from a copy of [`tests/fixtures/town/config/gloucester.toml`](tests/fixtures/town/config/gloucester.toml). `[site]`, `[town]` and `[[sections]]` are required. Every other table is one data source. Leave out a table the town doesn't have and the command that fetches it does nothing:

   | Table | Source | Works for |
   |---|---|---|
   | `[meetings]`, `[archive]` | CivicPlus calendar and Archive Center | Towns whose website runs on CivicPlus. A town with none of this table's meetings sources has no meetings section or RSS feed |
   | `[meetings.civicplus]`, `[meetings.agenda_center]` | A CivicPlus calendar read month by month, and a CivicPlus Agenda Center | CivicPlus towns, like Malden and Beverly (both). See [Meetings from other calendars](#meetings-from-other-calendars) |
   | `[meetings.civicclerk]`, `[meetings.dnn]`, `[meetings.file_list]` | A CivicClerk meeting portal, a DotNetNuke (DNN Events) city calendar, and a meetings calendar with one documents page for every board | Towns whose meetings are on these, like Manchester (the first two) and Wallingford, Connecticut (the third). See [Meetings from other calendars](#meetings-from-other-calendars) |
   | `[drive_meetings]` | Agendas and minutes in public Google Drive folders (Gloucester's and Lewiston's School Committees) | Either of two layouts. One folder per committee, with dates in file names (Gloucester: `agendas_folder`, `minutes_folder`, `bodies`). Or one folder per school year holding one folder per meeting, named by its date, with each meeting's minutes posted later in another meeting's folder (Lewiston: `meetings_folder`, the one `body`, and `resource_key` for an older shared folder whose link carries one); a folder named "CANCELED" cancels that day's meeting, the time comes from the agenda's "call to order", and a later copy of the same minutes isn't attached twice |
   | `[ical_meetings]`, `[schedule_meetings]` | A school district's calendar feed (iCalendar), and a page listing a board's meeting dates | Beverly's School Committee (the first) and Malden's (the second). See [Meetings from other calendars](#meetings-from-other-calendars) |
   | `[finalsite_meetings]` | A school board's meetings posted on its district's Finalsite website, with agendas and minutes as Google Docs (Wallingford's Board of Education) | Any board whose page lists one post a meeting, titled with its date. See [Meetings from other calendars](#meetings-from-other-calendars) |
   | `[seeclickfix]` | SeeClickFix 311 requests | Towns on SeeClickFix. `organization_id` is the town's SeeClickFix organization (its Open311 address, `seeclickfix.com/open311/v2/<id>/services.json`, lists its request types). `departments` (optional) keeps only the request types of the listed departments, by the `organization` names in that list; `scope_note` then says so on the 311 pages. Takes a ward boundary file in `data/static/` (`precincts_file`) whose features carry `ward`, `district` (the precinct, e.g. `1-1`) and `population_2020`; `wards_publisher`, `wards_year` and `wards_url` credit its source on the 311 and About pages. A town without wards, its seats all elected townwide, places requests in its voting precincts instead: `areas = "precincts"` names them so on the pages ("Precinct 3201"), and keeps the file off the Officials page. A town with neither wards nor voting precincts (Bangor) leaves `precincts_file` out: its requests aren't placed in any area, and the 311 pages have no area sections, area pages or area downloads. Requests in categories that point at a person or a household (an encampment, a health or police complaint, noise, a smoke detector or lost pet request: `seeclickfix.SENSITIVE_CATEGORIES`) are shown with their address to the block ("200–299 Main St") and their map point to about 100 meters; `sensitive_categories` adds a town's own category names |
   | `[finance]` | Tax bill and budget, from the state | Every New England state, each with a package in `pipeline/states/`; Rhode Island has budget figures but no tax bill, since the state publishes nothing to calculate one from. Its keys are the state's own; see [States](#states) |
   | `[schools]` | School district figures, from the state | Every New England state. Its keys are the state's own (Connecticut's `edsight_district` is the district's name in EdSight); see [States](#states) |
   | `[housing]` | Census, plus the state's own housing figures | Anywhere for the Census parts. Building permits find the town by its Census place (`bps_place`), or, for a New England town that isn't a Census place (Wallingford), by its town code (`bps_mcd`). In Massachusetts, `shi_url` and `shi_name` add the Subsidized Housing Inventory, and `[finance]` adds parcel counts |
   | `[labor]` | BLS unemployment | Anywhere BLS publishes a local series; set `bulk_file` to the state's file (defaults to Massachusetts's) |
   | `[permits]` | The city's permit spreadsheet | Gloucester's Data Hub layout only |
   | `[summaries]` | AI summaries of agendas and minutes | Anywhere, with `ANTHROPIC_API_KEY`. `model`, `input_price` and `output_price` (dollars per million tokens) are required; nothing is sent without prices. `max_per_run` (documents) and `max_cost_per_run` (dollars) default to 50 and $5. `since` (a date) summarizes only meetings on or after it, so a new town's history doesn't take the month's budget; older meetings keep their records and documents. New documents (upcoming agendas, and those posted in the last two weeks for a recent meeting) go first. In a network, the run also gives each town its share of a monthly budget, keeping back every other town's floor for the rest of the month, so no one town's launch or busy week can spend what the others need (`pipeline/network.py`, `summary_budget`; `pipeline/summarize.py`). Each summary is then checked against its document's own text, without AI (`pipeline/factcheck.py`): a decision, item, headline, or sentence with a number, amount, date, or name the document doesn't have isn't shown, the page says how many weren't, and a vote count the document doesn't give is left out |
   | `[analytics]` | Page view counts, with GoatCounter (no cookies, never what was searched) | Anywhere. `goatcounter` is the account's code; `prefix` (optional) goes in front of every counted path, so towns sharing one GoatCounter site can be told apart; `public_stats` links its public dashboard from the About page |
   | `[freshness]` | Stale-data alerts | List the sources that change daily (meetings, 311, a city's permits); figure sources (tax bill, budget, schools, unemployment, housing) are judged by their rhythms in the engine (`pipeline/rhythms.py`). `grace_months` (default 2) is how long after a new period's usual date before it counts as behind |
   | `[officials]` | Who represents you: the Officials page (`/officials/`), with the section `officials` | Anywhere; kept by hand from the city's website. `checked` is the date the list was last checked against official city and school websites (shown on the page). Each `[[officials.bodies]]` (the mayor, the City Council, the School Committee) has a `name`, `members`, and optionally `url` (its official page, on the city's or the school district's website), `note`, and `board` (the meeting board whose page it links, if not named the same). A body's members are also who its minutes' roll call votes are checked against (`pipeline/votes.py`; collected, not yet shown). Each member has a `name` and `seat` ("Ward 1", "At-large"), and optionally `ward` (the ward the seat is elected by, a ward in the ward file) or `wards` (a district of several wards, like `[1, 2, 3]`; leave both out for a citywide seat), `role`, `term_ends` (`"2028-01"`), `email`, `phone` and `url`. The ward map uses the ward file (`wards_file`, in `data/static/`; defaults to `[seeclickfix]`'s, unless its `areas` are precincts), credited on the About page by `wards_publisher`, `wards_year` and `wards_url` here for a town without `[seeclickfix]`; its "Find my ward" checks a visitor's location in their browser, and never sends or saves it. With no member's seat elected by a ward, the page shows no map and says nothing about wards (readers know their town has none); a body whose members all have the same seat ("At-large") has no Seat column, so its `note` is where to say how it's elected |
   | `[absences]` | Optional: the town's own sentence for a section it doesn't have, on the About page's "What this site doesn't cover" (`pipeline/absences.py`) | Keys are sections (`311`, `budget`, `schools`, `housing`, `officials`, `meetings`), each a sentence such as `311 = "The city takes requests through its own MyBeverly app, which has no public data."`. Without one, the page gives the engine's reason: 311 isn't in a public system the site reads, the state's figures aren't collected yet, or the section isn't on the site yet. Translated as the town's other text |
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
   - `network_url` (optional): the network's homepage, which the network's name in the footer links to.
   - `contact_email`: shown on the About and Accessibility pages and used by the "Report an error" buttons.
   - `[site.colors]` (optional): the town's own colors, as `"#rrggbb"`. `primary` (links, buttons, map markers), `primary_dark` (headings, rules, the masthead), `primary_soft` (light backgrounds), and `accent` (the current page in the menu, flags, notices). Take them from the city's own website and check each against white for WCAG AA contrast (4.5:1). `network` is the color of the network's name in the masthead and share image; Publick's is slate `#2c4a63`, the same for every town. Unset colors keep the defaults (Gloucester's navy and maroon).

   The engine's icon is the Publick "P". A site outside the network draws its own in the town's `site/static/favicon.svg`. Then run `python -m pipeline.make_share_image` for the share image (and PNG icons, for a town with its own icon).
6. **Deploy** as under [Deploying](#deploying), and set up [Document storage](#document-storage) and the [secrets](#secrets).


## States

Tax bills, budgets, and school figures come from each state's own agencies, in
each state's own form, so everything state-specific is in one place per state:

```
pipeline/states/<state>/__init__.py   STATE: its sources, the config keys each needs, its short credits
pipeline/states/<state>/*.py          One module per source: client(config) and run(config, client, data_dir, now, force)
site/states/<state>/schools.html      The state's schools page, budget.html its budget page,
site/states/<state>/about_*.html      and its lines in the About page's list of sources
```

A town's `[town] kind` is `"city"` or `"town"`, for the site's wording ("the town's Agenda Center", "el pueblo"). It needn't be set: `pipeline.fetch_place` records the Census Bureau's word for the place `[housing] census_geo` names, once, in `data/place.json`, and a place with neither is a city.

A town's `[town] state_abbr` picks its state. The fetch commands
(`fetch_finance`, `fetch_budget`, `fetch_schools`) run that state's source and
skip a town whose state has none; the config's `[finance]` and `[schools]`
tables hold the state's own keys, checked when the config is loaded. So a town
in a state that already has a package needs only its config, and adding a state
means adding its package and pages, with no changes elsewhere. A town in a state
without one still gets everything else. Its config lists only sections its
state can fill; the build stops with a message naming what's missing otherwise.
Each state package's docstring lists its config keys.

A figure Publick calculates, rather than takes as published, carries a
`calculated` note in its data saying how, and its page shows that note under
"Calculated by Publick" (the `calculated` macro in `site/templates/macros.html`).

### New Hampshire's yearly figures

New Hampshire's Department of Revenue Administration and Department of
Education publish tax rates, graduation rates, test results, and cost per pupil
as statewide files once a year, and their websites refuse automated requests.
So the files are downloaded by hand and saved into the engine:

```sh
python -m pipeline.states.nh.extract ~/Downloads/*.xlsx ~/Downloads/*.csv   # the list of files is in extract.py
python -m pipeline.states.nh.extract --population                          # Census estimates, fetched directly
```

This writes `pipeline/states/nh/figures/`, every town's and district's rows in
a few small files; commit them and every New Hampshire town reads its own rows
from the next release on. The status page says when a new year is due: each
figure's rhythm (in `tax_bill.py`, `budget.py` and `schools.py`) knows when the
state usually publishes it, and marks the town behind two months after that
if the new year isn't in the saved figures. The average single-family tax
bill is calculated daily from those rates and NH GRANIT's parcel map (which
answers automated requests), and held back after a revaluation until the DRA's
figures for the new year are saved (see `pipeline/states/nh/tax_bill.py`).

### Connecticut's figures

Connecticut's tax bill and budget come from the Office of Policy and
Management's statewide datasets on data.ct.gov, through its Socrata API
(`pipeline/states/ct/opendata.py`), which answers one query for one town or for
all 169: mill rates, tax levies, grand lists, adopted budgets, and the audited
Municipal Fiscal Indicators. The average single-family bill is calculated as New
Hampshire's is: the average assessed value of the town's single-family homes in
the state's yearly Parcel and CAMA file times the mill rate. Each year's parcel
file is found by its name in the portal's catalog, paired with the fiscal year
its grand list is taxed in, and used only when its total for the town is close
to OPM's grand list (see `pipeline/states/ct/tax_bill.py`). Towns code their
single-family homes differently, so `[finance] single_family_use` lists the
town's codes when they aren't "101" or "1010".

The school figures come from EdSight, the State Department of
Education's data portal, through the CSV export each of its reports has
(`pipeline/states/ct/schools.py`). The exports answer without a login as long as
the session keeps the cookies EdSight's redirects set; without them EdSight
answers with its sign-in page, and the step fails rather than saving anything.
A trend export covers the last five school years, so the figures already saved
are kept and the new years added; spending per pupil has an export per school
year, and only years not yet saved are asked for.

### Vermont's yearly figures

Vermont's Department of Taxes (Property Valuation and Review) and Agency of
Education publish tax rates, grand lists, taxes raised, and spending per pupil
as statewide workbooks once a year, under names that change each year. One
command finds the newest on the state's pages, by their links' text, and saves
every town's and district's rows into the engine:

```sh
python -m pipeline.states.vt.extract                # or name workbooks downloaded by hand
python -m pipeline.states.vt.extract --population   # Census estimates, matched to the state's town names
```

Commit `pipeline/states/vt/figures/`, as for New Hampshire. The average
homestead bill is calculated daily from those rates and VCGI's statewide parcel
data, while the parcel data's grand list year has rates in the saved figures and
its homestead values add up to the state's homestead grand list. Graduation
rates, chronic absenteeism, and test results come from data.vermont.gov at each
run; each spring's test results are a dataset of their own, found by name.

### Maine's yearly figures

Maine Revenue Services publishes every municipality's tax rate, commitment, and
valuation once a year in a 150-page PDF, and the Department of Education's ESSA
Dashboard, a Tableau workbook, holds every district's school figures. Neither
suits a daily run, so one command saves both into the engine once a year:

```sh
python -m pipeline.states.me.extract                # the MVR summaries not yet saved
python -m pipeline.states.me.extract --population   # Census estimates, matched to MRS's names
python -m pipeline.states.me.extract --schools      # the dashboard's four measures, every district (about 15 minutes)
```

`--schools` uses the requests the dashboard's own Download button makes, which
Tableau doesn't publish; if they stop working, the four crosstabs downloaded by
hand can be named instead (see `pipeline/states/me/extract.py`). The average
single-family bill is calculated daily from the newest tax rate and the Maine
GeoLibrary's parcel table, which towns send when they choose to: so it's shown
only while the town's parcels add up to about its taxable land and buildings,
and `[finance] single_family_use` lists the town's own single-family codes.

### Rhode Island's yearly figures

Rhode Island's Division of Municipal Finance publishes each fiscal year's tax
rates, net assessed values, and levies by class of property as PDFs, on a site
that refuses automated requests. Download them by hand in a browser once a year
and save them into the engine:

```sh
python -m pipeline.states.ri.extract ~/Downloads/*.pdf
python -m pipeline.states.ri.extract --population
```

Each file is recognized by its own heading. The state publishes no average
bill and no statewide assessed values, so a Rhode Island town has no tax bill;
its budget page has the rates, levy, assessed values, and property tax per
resident. School figures come from RIDE's report card data files and its
assessment data portal at each run.

## Meetings from other calendars

A town whose website isn't on CivicPlus lists its calendars as tables inside `[meetings]`, instead of `base_url` and `calendar_feed`. It can have both; Manchester's are below.

```toml
[meetings]
calendar_url = "https://www.manchesternh.gov/Government/City-Calendars"   # linked as "the city calendar"
archive_url = "https://www.manchesternh.gov/Departments/City-Clerk/Meeting-Minutes-and-Agendas"
archive_name = "city's Meeting Minutes and Agendas page"   # where earlier agendas and minutes are
governing_body = "Board of Mayor and Aldermen"             # "City Council" if left out
# notify_url = "..."                                       # the city's meeting alerts, if it has them
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
- **A CivicPlus calendar, month by month.** `[meetings.civicplus]` reads the calendar's list view (`Calendar.aspx?CID=0&view=list&month=..&year=..`), this month and `months_ahead` more (default 2), one request each, with each event's time, place and address. `calendars` names the city's calendars to read ("City Meetings"; all of them if left out), and `include_pattern` and `exclude_pattern` (here or in `[meetings]`) pick the public meetings. It takes the place of `calendar_feed`, whose RSS feed lists only a fixed number of events (10 to 20), so reaches a week or two ahead. A town without an Agenda Center reads each upcoming meeting's page for its online link and agenda, as from the feed.
- **A meeting listed in more than one place.** A town with both a calendar and an Agenda Center lists most meetings twice, and clerks post an agenda again under a new number (a repost, a revised agenda, a cancellation notice). Each listing stays its own record, and `pipeline/listings.py` shows the records of one meeting as one: the same board (after `[meetings.aliases]`) on the same day, either at the same time, or with one giving no time and their titles agreeing on what kind of meeting it is (committee, subcommittee, special, hearing, joint, workshop: an Agenda Center posts a board's committees under the board). The meeting keeps the page of the listing recorded first; the others' pages say it moved. Its time and place come from the calendar, its agendas from every listing, and the newest posting of each source gives that source's status, a cancellation in any one standing. Its page lists every listing. `python -m pipeline.listings` lists what was put together, and why. A calendar entry's board takes the name already recorded when it's the same words written another way, or with the town's name in front ("Malden Cultural Council" for "Cultural Council"); others need an alias.
- **CivicPlus Agenda Center.** A CivicPlus town that posts its boards' agendas and minutes in the Agenda Center rather than on the calendar (Malden) uses `[meetings.agenda_center]`. One request to its search page lists every board's meetings for a date range; upcoming meetings' agendas are saved, and a revised agenda (same number, new posted time) is recorded in the meeting's history. `fetch_minutes` downloads the minutes linked from each meeting since `since`, up to `max_minutes_per_run` (default 60) a run. The board is the Agenda Center category (renamed by `[meetings.aliases]` if listed); `committees` names the committees whose meetings are posted under another board's category, matched in the row's title as `boards` are. There are no times or places in the listing, so meeting pages show the date only.

  ```toml
  [meetings.agenda_center]
  base_url = "https://www.cityofmalden.org"
  since = "2026-01-01"     # the first run lists meetings from here; later runs re-read the last 60 days (recheck_days) and 60 ahead (days_ahead)
  exclude_categories = ["Community Outreach"]

  [meetings.agenda_center.committees."City Council"]
  "Finance Committee" = "City Council Finance Committee"
  ```
- **A calendar and a documents page.** A town website with a meetings calendar by month and one page listing every board's agendas and minutes in folders (Wallingford, Connecticut's: `/events/meetings/` and `/minutes-and-agendas/`, on a CMS by Web Solutions) uses `[meetings.file_list]`. Each run reads the documents page once and the calendar's months once each; it saves upcoming meetings' agendas, and `fetch_minutes` downloads the minutes of meetings since `since`, up to `max_minutes_per_run` (default 60) a run. Only the boards in `boards` are collected: a documents folder matches a board (or a key in `[meetings.aliases]`) by its whole name, so an archive folder like "Town Council Archive (1984 - 2022)" isn't the Town Council, and calendar names match as other calendars' do. A document's title gives its kind and the meeting's date ("Amended Agenda of Regular Meeting- January 27, 2026"); documents go with the calendar's meeting of the same board and day, or make a meeting of their own. A meeting's agenda is its newest plain agenda; an agenda with backup (often a long scan) is never saved or summarized, and a meeting with no plain agenda links to it instead; a cancellation notice marks it cancelled. Addenda, applications and reports are left out. A meeting's own calendar page gives its location, read once. A board with no folder on the documents page gets the agenda its calendar entry links. The documents page's YouTube links are kept with each meeting (`video_id`, and `video_start` in seconds), not yet shown.

  ```toml
  [meetings.file_list]
  documents_url = "https://www.wallingfordct.gov/minutes-and-agendas/"
  calendar_url = "https://www.wallingfordct.gov/events/meetings/"
  since = "2026-01-01"   # documents of meetings from here on; earlier ones are never downloaded
  # months_ahead = 1     # calendar months read after this one
  ```
- **A school board on Finalsite.** A school district whose board posts each meeting on a Finalsite page (Wallingford's Board of Education: "September 28, 2026 - Board of Education Meeting", with the agenda and minutes as Google Docs) uses its own table, `[finalsite_meetings]`, fetched by `pipeline.fetch_finalsite_meetings` after the town's calendars. Its meetings are recorded alongside the town's, as the district's own (each page says it is known from the district's website), so its boards shouldn't be in `[meetings] boards` too. Each run reads the page once; a post's body (its links) is read when it's new, and again while its meeting is recent (`recheck_days`, default 60) and has no minutes yet. A title's date and board come from the title; `bodies` maps the names in titles to the site's boards, the longest found winning, and a title with no listed board or no date is reported, not guessed at. "Canceled" marks a meeting cancelled, and its agenda is linked, not saved. Agendas and minutes (links labelled so, to a Google Doc, a Drive file or a PDF) are saved as PDFs, a Doc exported by Google with its text; a Doc "published to the web" (a `/pub` page) has no PDF, so it is linked. A Doc is edited in place, so it is exported again while it can still change, an agenda until its meeting and minutes while the meeting is recent, and saved as a new version only if it changed. Backup folders, presentations and other documents (Wallingford's "Motions") are kept with the meeting as `links`, and the YouTube recording as `video_id`, not yet shown. The page lists the current school year; earlier years' archive pages aren't read. A district that posts each meeting only when its agenda is ready may publish the year's dates on a page of their own (Wallingford's "Board of Education Schedule 2026": "10/19/26 Operations Comm.", "10/26/26 BOE Meeting"): `schedule_url` names it and `schedule_names` maps the names it uses to boards (the longest found winning), and its upcoming dates are listed, to `schedule_days_ahead` (default 60) days out, until a post takes over the meeting's record (keeping its page). A date that leaves the schedule before it happens, with no post, is marked no longer listed.

  ```toml
  [finalsite_meetings]
  page_url = "https://www.wallingford.k12.ct.us/board-of-education/board-of-education-meetings"
  source_name = "Wallingford Public Schools"   # credited on its meetings' pages
  since = "2026-07-01"   # meetings from here on; earlier posts are never read
  # recheck_days = 60    # how long after a meeting its post and minutes are checked again

  [finalsite_meetings.bodies]   # names in post titles -> the site's boards
  "Board of Education" = "Board of Education"
  "Operations Committee" = "Board of Education Operations Committee"
  ```
- **A school district's calendar feed.** A district whose website publishes an iCalendar feed (Beverly Public Schools', on Edlio: `/apps/events/ical/?id=0`) gives its board's meetings, with their times, to `[ical_meetings]`: `ical_url`, `page_url` (the board's page, linked from the site), `source_name`, `since`, and `bodies`, mapping names in event titles to boards ("Committee of the Whole" = "School Committee of the Whole"), the longest found winning; other events (holidays, games) are left out. One request a run reads the meetings from `since` to `days_ahead` (default 90) days out, as the district's own (each page says it is known from the district's website); no documents are saved.
- **A page of a board's dates.** A board that publishes its year's meeting dates as a plain list (Malden Public Schools' School Committee Meetings page: "Monday, November 9, 2026"), and posts each agenda elsewhere when it's ready, uses `[schedule_meetings]`: `url`, `source_name`, and the board, `body` (every date is its meeting) or `names` (mapping the words after each date to boards, as "10/26/26 BOE Meeting"). Dates may be written out, short ("Nov. 9, 2026") or in figures. One request a run lists its upcoming dates, to `days_ahead` (default 60) days out; past dates aren't taken for meetings held. A listed date is shown with the board's other listings of that day (its Agenda Center agenda) as one meeting; one that leaves the page before it happens is marked as no longer listed.
- **The home page** shows up to six of the next seven days' meetings in full, the same shape in a quiet week and a busy one, chosen in order: the main boards' (the governing body, `governing_body`; the School Committee or Board of Education; any in `[meetings] main_boards`), then those with something to read now (an agenda summary, a public hearing), then the soonest of the rest; shown in date order. The rest are one line each behind "Show 7 more meetings this week", a cancelled meeting last; one left over is shown, not hidden. Then the latest three decisions, the numbers, and one row of links to the sections.
- **Corrections.** When the city's own listing is wrong (an entry left over from a board's old schedule, a typo in a time), `[[meetings.corrections]]` says so on the meeting's page, with the reason and how it's known, rather than copying the mistake or quietly changing it. Each names its meeting by `board` and `date` (or its record's `meeting` id), and gives a `note` (the town's text, translated as the rest is), `evidence` (a link), the date it was `checked`, and what it corrects: `doubtful = true` for a meeting that most likely won't take place (it stays listed, marked "May not take place"), or the right `start_time`. Once the city changes the listing after `checked`, or a correction matches no meeting or more than one, it isn't shown and the build warns, until it's checked again; a meeting the city no longer lists needs none.
- **`documents = false`** is for a town with a calendar but no agendas and minutes collected yet. Each meeting page links to its agenda (and minutes) where the city posts them, and the pages that need the documents are left out: decisions, search, the RSS feed, and agenda items in the street lookup.

## Adding a section

1. Add a `[[sections]]` entry to `config/<town>.toml`. It appears in the main navigation.
2. Create `site/pages/<slug>/index.html` extending `base.html`. It is served at `/<slug>/`.
3. Sub-pages go in sub-folders: `site/pages/<slug>/<name>/index.html` is served at `/<slug>/<name>/`.
4. Pages generated from data (one per record) use a template in `site/templates/` and are added in `pipeline/build_site.py`.

New pages are picked up by the tests and the site checks automatically. A section belongs to the engine, so every town that lists it gets it.

## Sites in other languages

A site can also be built in Spanish (every town in the Publick network is):

```toml
[site]
languages = ["en", "es"]

# The town's own text in Spanish, each keyed by its English as the config or the data writes it:
# the tagline and masthead, section titles and summaries, glossary definitions, participation
# notes, officials' seats, 311 categories, and board names.
[strings.es]
"An independent guide to city government in Lawrence, Massachusetts" = "Una guía independiente sobre el gobierno de la ciudad de Lawrence, Massachusetts"
"City Council" = "Concejo Municipal"
"Pothole" = "Bache"
```

- English pages are at the site's root, as before; Spanish pages are the same pages under `/es/` (`/es/meetings/`). Every page links its other version (`hreflang`), and a link at the top of each page goes to the same page in the other language. The homepage opens in the language the visitor's browser asks for first: the network's Worker redirects `/` to `/es/` for a browser set to Spanish (`worker/sites.js`). Choosing a language with the link (`?lang=es`) is remembered in a cookie and wins over the browser's there. Every other address opens as asked, so a shared link opens in the language it was shared in. A site on GitHub Pages, without the Worker, opens in English.
- **The engine's own wording** (about 840 strings in the templates, the phrases built in Python, and the scripts' messages) is translated in `site/strings/es.po`, one file for every town. Write English as usual and mark it: `{{ _("...") }}` or `{% trans %}...{% endtrans %}` in a template, `_("...")` or `ngettext(...)` in Python. Then `python -m pipeline.i18n update` adds the new strings to `es.po`, and `python -m pipeline.i18n missing` lists what has no Spanish yet. A string without a translation is shown in English. The tests fail if `es.po` is out of date, or if a translation drops a value its English has (`%(name)s`, `{name}`).
- **The town's own text** (tagline, masthead, section summaries, glossary, participation notes, officials' seats, and the names in its data: boards and 311 categories) needs no one's translation to start. Each comes from the first of:
  1. the town's `[strings.es]`;
  2. the engine's Spanish for what many towns share: section names, common boards ("Planning Board"), roles, seats, and the section summaries town configs copy (`pipeline/common_strings.py`, translated in `es.po`), and numbered seats ("Ward 3" is "Distrito 3");
  3. a machine draft: each run, before summarizing, drafts whatever is still missing (a new town's config, a board or 311 category the city just added) with the translation model, checked the same way (numbers and placeholders kept) and reviewed by a second model, into `data/strings/es.json`. It costs a fraction of a summary, and may spend up to $0.05 a run past the town's budget share. A draft that fails twice stays in English.

  A text with none of these is shown in English with a warning, never stopping the build: a gap in Spanish doesn't stop either language publishing. No person reviews the drafts; `python -m pipeline.translate drafts` prints them as `[strings.es]` lines for anyone who wants to correct them in the config, which then wins. Board names are shown with their official English name after them ("Concejo Municipal (City Council)"), so readers can match them to the city's notices; 311 categories are shown in Spanish only.
- **Summaries** are translated from the English summary (never from the PDF) by Claude Sonnet 5.5 at low effort (`pipeline/translate.py`; `translation_model` in `[summaries]` to change it, with its `translation_input_price`, `translation_output_price`, and `translation_effort`, which Claude Haiku 4.5 takes none of). Until October 2026 Claude Haiku 4.5 translated them, and more of its translations failed the checks than passed, so it cost more per translation shown. No person checks them, so two checks do. One without AI, entry by entry: numbers kept and none added (whatever the Spanish number format), amounts' million or billion, a.m. and p.m., names kept as written, and what happened not turned round (a "not" lost or added, approved as denied, tabled as approved, unanimous changed); a date in figures may be written out. Then a second request (Claude Sonnet 5.5; `translation_review_model`, with its prices) reviews the meaning of each translation that passes, given the translator's rules and words so it doesn't fail a translation for following them; the whole, translation and review, is about a cent. A translation that fails either is made again once, as a correction (the model gets its first translation and what was wrong with it), then kept, so it isn't paid for again, and the page shows the English with a note that the translation didn't pass. The check runs again each build. Every translated summary says it was translated automatically by AI and links the English, as does every Spanish page's footer. Each translation is its own record, `data/summaries/es/<document hash>.json`, made again only when its English summary changes, so turning Spanish on never regenerates an English summary. Translations come out of the same budget as summaries, new documents first, and older summaries' translations before the older documents still waiting for an English summary, which cost about ten times as much (`translation_cost` in the month's ledger). A summary not translated yet is shown in English, marked `lang="en"`, with a note saying so. Decisions are sorted, and public hearings and glossary terms found, in the English.
- Agendas, minutes, and transcripts stay in English, as the official record. Downloads, saved PDFs, and the feed are shared by both languages. Search on the Spanish pages also finds the translated summaries.
- The site checks run on every page in both languages. A missing page under `/es/` gets the Spanish 404 page from the network's Worker (`worker/sites.js`), which must be deployed before the first town with Spanish goes live.

## Weekly digest

Each town with meetings has a weekly digest at `/digest/` (`pipeline/digest.py`), in English for now. An issue is dated a Sunday and lists:

- the meetings of the week ahead, Monday to Sunday, by day, each with its agenda summary's line once an agenda is posted;
- the minutes this site first collected in the week before, Monday to Sunday, with what each meeting decided, as the decisions page shows them (after the fact check).

It's made from what the site already has, with no AI calls. A week with no meetings and no new minutes has no issue. Minutes collected on the day a town's meetings were first read are its history, not news, and are left out, as are minutes of a meeting more than 90 days before the issue (a source's history read for the first time). An issue's page shows what the site knows about its week as of the latest build, so a meeting cancelled after the Sunday is shown cancelled. When the meetings calendar is behind (`absences.calendar_behind`), an issue whose week isn't over says meetings may be missing.

Each issue is at `/digest/<its Monday>/`, and `/digest/feed.xml` has the last 12, each whole as plain HTML with every link in full, for an email provider to send. An item's date is when its email is due: the Sunday at 5:30 PM, the town's own time (`digest.SEND_TIME`; decided 2026-10-06, because it gives a day's notice of Monday evening meetings, and leaves the morning's daily run hours to finish). An issue first appears in the build on its Sunday, so a town whose Sunday run doesn't happen has no new issue to send until its next build. Sending the email isn't built yet: whatever sends it sends each item once its date has passed.

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
python -m pipeline.deploy publish [--town <town>] [--site _site] [--domain <domain>]   # the site goes live at its config's domain
python -m pipeline.deploy rollback [--town <town>] [--build <build>] [--domain <domain>] # back to the previous build, or a named one
python -m pipeline.deploy prune [--keep 10] [--dry-run]              # delete old builds and unused files
```

`--domain` names the site by its address instead of by its town's config, as the network's rollback does. Files are stored once by content and shared across sites, so a daily publish uploads only what changed. A site goes live with one write, after all its files are uploaded. See `pipeline/deploy.py` for the bucket layout.

GitHub starts scheduled workflows when it can, sometimes hours late. A network can start its daily runs on time with a second Worker, `worker/scheduler-index.js`: on each Cron Trigger it starts the network workflow through GitHub's API (a daily run, which takes only the towns that are due, so extra starts do nothing), and it opens an issue if the status page shows no daily run has finished for 30 hours. It needs a GitHub token that can start the workflow and open issues, and the account needs a `workers.dev` subdomain for Cron Triggers, even though the Worker has no address of its own. See `worker/scheduler.js`, and the network repository's `wrangler.scheduler.toml`.

Setup, once for the network:

1. **Create a bucket** for sites (**R2 object storage → Create bucket**), separate from the documents bucket and with no public address: only the Worker reads it.
2. **Create an API token** with *Object Read & Write* on that bucket only. Set the secrets `SITES_ACCESS_KEY_ID` and `SITES_SECRET_ACCESS_KEY`, and `SITES_ENDPOINT` (`https://<account id>.r2.cloudflarestorage.com`) and `SITES_BUCKET`.
3. **Deploy the Worker** from `worker/index.js` with an R2 binding named `SITES` to that bucket.
4. **Route the sites to it:** a proxied (orange cloud) DNS record for each site's hostname, or one wildcard such as `*.example.org`, and a Worker route such as `*.example.org/*`. A site's specific DNS record takes precedence over the wildcard, so delete a town's GitHub Pages `CNAME` to move it to the Worker.

## Document storage

Agenda and minutes PDFs average well over a megabyte, and git keeps every version forever, so a repository that holds them only grows: one holding every town's, as a network's does, would soon be too large to clone quickly (and a GitHub Pages site may be at most 1 GB). Without a `[storage]` table they're committed under `data/meetings/` and copied into the site, which works for a small or short-lived town. With one, they go to an S3-compatible bucket and pages link to the bucket's public address. Git keeps each document's text, summary and SHA-256 hash, so the site is still rebuilt entirely from the repository.

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

   The default is the config file's name (`gloucester`), so a town in a network sets its folder's name, `<town>-<state>`, to keep two towns' files apart.

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
| GitHub organization | `publick-org`: this engine, and the network repository [`publick.org`](https://github.com/publick-org/publick.org), with each town in `towns/<town>-<state>/` and the publick.org homepage and status page |
| Site addresses | `<town>-<state>.publick.org`, served by one Cloudflare Worker from the sites bucket through a wildcard `*.publick.org` record; `publick.org` is the homepage, with [publick.org/status/](https://publick.org/status/) |
| DNS and registrar | Cloudflare |
| Site names | `<Town> Publick` (`name_prefix = "<Town> "`, `name_suffix = "Publick"`, `network = "Publick"`), sharing the Publick "P" icon |
| Documents bucket | R2 bucket `publick-documents` at `https://files.publick.org`, `prefix = "<town>-<state>"` |
| Email | `<town>-<state>@publick.org` for each town and `hello@publick.org`, forwarded by Cloudflare Email Routing |
| Page views | One GoatCounter site, `publick`, for every town, each with `prefix = "<town>-<state>"` |
| Daily runs | Started every hour from 09:05 to 14:05 UTC by the `publick-scheduler` Worker, with GitHub's schedule as a backup; each takes the towns that are due. AI summaries and translations share an $80 monthly budget. Massachusetts's DLS reports are fetched once for every town, into the network repository's `states/ma/` |
| Alerts | One GitHub issue, "Towns need attention", kept up to date by each daily run and assigned to the maintainer; the scheduler opens "The network's daily runs have stopped" after 30 hours without one |
| Secrets | `ANTHROPIC_API_KEY`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`, `SITES_ENDPOINT`, `SITES_BUCKET`, `SITES_ACCESS_KEY_ID`, `SITES_SECRET_ACCESS_KEY`, `BLS_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ENGINE_PR_TOKEN`, `SCHEDULER_GITHUB_TOKEN`, set once on the network repository |

Adding a town to the network is a pull request to the network repository that adds its folder (see its README), plus a `<town>-<state>@publick.org` routing rule. No DNS change is needed.
