# Working on the engine

The engine's layout, how to build and test it locally, and how to add a section.

## Layout

```
pipeline/                   Python package
  config.py                 Finds the town's repository and loads config/<town>.toml, over a network's shared
                            settings (defaults/network.toml, defaults/states/<st>.toml); show prints the result
  update.py                 Daily: runs every fetch below for one town, each in its own process (town.yml runs the same list step by step)
  fetch_meetings.py         Daily: city calendars (CivicPlus, a CivicPlus Agenda Center, CivicClerk, Legistar, PrimeGov, DotNetNuke, a calendar with a documents page),
                            a school district's calendar feed or page of dates -> data/meetings/
  listings.py               One meeting listed in more than one place (a calendar and an Agenda Center, a repost) shown as one;
                            `python -m pipeline.listings` lists the meetings put together, and why
  identity.py               Which meeting each listing is, by board, day, kind and time, decided once and kept in
                            data/meetings/identities.json, with each meeting's page address
  fetch_minutes.py          Daily: Archive Center minutes -> data/meetings/minutes/
  fetch_drive_meetings.py   Daily: School Committee agendas and minutes (Google Drive) -> data/meetings/
  fetch_finalsite_meetings.py   Daily: a school board's meetings, agendas and minutes (a Finalsite district website, Google Docs) -> data/meetings/
  fetch_page_meetings.py    Daily: a school board's meetings, agendas and minutes (a district page linking each document) -> data/meetings/
  fetch_diligent_meetings.py   Daily: a school board's meetings, agendas and minutes (a Diligent Community portal) -> data/meetings/
  fetch_meetingdocs_meetings.py   Daily: a board's meetings, agendas and minutes (a city's listing of its events' documents) -> data/meetings/
  fetch_boardbook_meetings.py   Daily: a school board's meetings, agendas and minutes (its organization's page on BoardBook Premier) -> data/meetings/
  fetch_sharpschool_meetings.py   Daily: a board's minutes (a library of them on a district's SharpSchool website) -> data/meetings/
  fetch_opengov_meetings.py    Daily: public bodies' meetings, agendas and minutes (Rhode Island's Open Meetings portal), and their
                            videos as a clerk's dockets on ClerkBase link them (clerkbase.py) -> data/meetings/
  fetch_recordings.py       On its own schedule: meetings' recordings found on the town's video platform (recordings.py: TelVue,
                            Cablecast, Castus, CivicClerk, the Internet Archive), matched to their meetings, and transcribed by
                            Whisper (transcribe.py) -> data/meetings/recordings.json, audio and transcripts in the bucket
  coverage.py               Each daily run: the main boards' meetings of the last week, by how far each got toward a summary
                            from its recording (summarized, the next day or later; minutes first; transcribed; found; not
                            matched; not found; no source) -> the run record (run.json), which network.py recordings adds up
  document_gaps.py          Each daily run: the last 90 days' meetings missing documents (boards with no agenda or minutes,
                            agendas but no minutes, a calendar listing likely of a meeting listed under another name, and
                            readers' meetings waiting) -> the run record (run.json), which network.py documents adds up
  summarize.py              Daily: agenda and minutes PDFs -> summaries (AI), and full text for scans (AI) -> data/summaries/,
                            each decision of minutes and recordings with a short title, main or routine, and a topic;
                            new documents first, with each request's cost in the cost log (cost_log.py); in a network's
                            daily run, sent as one batch at half the price, with the translations and their reviews,
                            and collected once it has ended
  cost_log.py               Each request to the model, written as it's answered, counted once, and each month's totals made
                            from it (the ledger, which the network's budget reads), kept out of git: in the private store
                            (see Cost records), else in data/costs/ and data/summary-costs.json.
                            `python -m pipeline.summarize ledger --check` compares the logs with the files saved
  translate.py              Summaries in the site's other languages, from the English summary (AI), checked without AI and
                            reviewed by AI -> data/summaries/<language>/, and drafts of the town's own text and names
                            -> data/strings/; run by summarize.py within its budget. `python -m pipeline.translate drafts` lists drafts
  factcheck.py              Each summary checked against its PDF's own text (no AI): numbers, amounts, dates, names, vote counts,
                            and each decision's quote from the minutes and its outcome (approved, denied, ...), read against how
                            the motion ended after the quote, so a dropped "not" or a failed vote called approved is caught. Kept in data/summaries/; what isn't in the document isn't shown. `python -m pipeline.factcheck` lists it
                            The page each decision's quote is on, which the site cites after it ("p. 3", linking the PDF at that page).
                            Each decision's title is held to the document too: one it doesn't support is left off, not its decision.
                            A recording's vote counts are read as a chair says them ("seven in the affirmative, none in the negative")
  evaluate.py               The minutes prompt run against the real model on minutes checked by hand (evals/minutes.json), with the
                            checks, before a prompt change ships: `ANTHROPIC_API_KEY=... python -m pipeline.evaluate`
  evaluate_translations.py  The translation prompt run the same way on summaries from towns in six states (evals/translations.json),
                            each with its town's glossary, scored on the glossary's gold terms and failing under the set's
                            thresholds: `python -m pipeline.evaluate --set translations`
  evaluate_batch.py         The minutes set sent through the Message Batches API at each effort, to measure how long a
                            batch takes and what a lower effort does: `python -m pipeline.evaluate --set batch`
  evaluate_shadow.py        The fact check replay's real agendas and minutes summarized again with a model (`--model`, `--effort`)
                            and checked as the site checks them, beside the published summaries: `python -m pipeline.evaluate --set shadow`
  evaluate_factcheck.py     The fact check replayed, without AI, on real summaries and their PDFs' page text (evals/factcheck/),
                            by the tests; a change that shows or hides anything fails them until accepted with `--update`
  pdftext.py                A PDF's own text: laid out as full text for a supported style (the software that made it), checked word for word; plain text for search
  votes.py                  Roll call votes from a supported style's minutes, checked against the body's [officials] members (no AI);
                            read with the minutes' text, kept in data/summaries/, and shown on the meeting pages of a town with
                            [votes] show. `python -m pipeline.votes` lists them for review
  fetch_311.py              Daily: SeeClickFix -> data/311/requests.json, only what changed since the last run
                            (its cursor in data/311/sync.json); a new town's history a month at a time (also --backfill YYYY-MM)
  fetch_socrata_311.py      Daily: a city's Socrata open data portal ([socrata_311]) -> data/311/requests.json, the rows
                            changed since the last run (its cursor in data/311/sync.json), instead of SeeClickFix
  source_311.py             Which source a town's 311 requests come from, and what it records, for the scorecard and pages
  compute_311.py            Daily: requests -> data/311/scorecard.json
  fetch_finance.py, fetch_budget.py, fetch_schools.py   Tax bill, budget, school figures, from the town's state's source -> data/finance/, data/schools/
  states/                   What differs by state (see docs/states.md): states/ma/ is Massachusetts (DLS, DESE),
                            states/nh/ New Hampshire (DRA, Department of Education, NH GRANIT), states/ct/
                            Connecticut (OPM on data.ct.gov, EdSight), states/vt/ Vermont (PVR, VCGI, AOE),
                            states/me/ Maine (MRS, Maine GeoLibrary, ESSA Dashboard), states/ri/ Rhode Island
                            (Division of Municipal Finance, RIDE); states/ma/statewide.py fetches the DLS reports,
                            the Subsidized Housing Inventory, and DESE's figures once for every town (a network's states/)
  fetch_labor.py            Unemployment rate (BLS LAUS) -> data/labor/
  fetch_place.py            Whether the town is a city or a town by law (Census TIGERweb) -> data/place.json, once;
                            the site's wording says "the city" or "the town" to match (i18n.py)
  fetch_addresses.py        The Officials page's address box: the Census Bureau's address ranges (TIGER/Line ADDRFEAT) for
                            the counties the town's wards are in, each range given its ward -> data/addresses.json;
                            made again when the Census Bureau publishes a new year, or the ward file changes
  fetch_permits.py          Building and demolition permits (city Data Hub) -> data/permits/
  fetch_housing.py          Housing (Census, plus the state's own figures: Massachusetts's SHI and parcels) -> data/housing/
  documents.py              Where agenda and minutes PDFs live: the town's bucket, or data/meetings/
  make_share_image.py       Draws the share image (and PNG icons for a town with its own icon)
  share_cards.py            Each recent and upcoming meeting's share card, drawn by build_site.py (Playwright)
  streets.py                Street-name matching for the street lookup
  freshness.py              Daily: whether each data source is still updating (fails a single town's run when one isn't)
  absences.py               Why something isn't shown, for the pages to say: a meeting's minutes, agenda or summary, a calendar
                            or a section's sources behind, a section the town doesn't have
  officials.py              Who represents you: the Officials page from [officials], with the ward map
  check_officials.py        Weekly: each body's members looked for by surname on the body's own page (its url, with its
                            images' alt text; in a browser, collapsed sections opened, when its names come from scripts)
                            -> data/officials_check.json, whose leads (a member not named, a page naming none, one that
                            can't be read) the run record keeps for network.py officials, one issue for a person; it
                            never changes the list
  rhythms.py                How often each figure source publishes: when it's checked, and when it's behind
  civicplus.py, agendacenter.py, civicclerk.py, legistar.py, primegov.py, dnn.py, filelist.py, finalsite.py, ical.py, linkpage.py, schedule.py, seeclickfix.py, socrata.py   Source parsers
  meeting_names.py          Which board a calendar entry is for, from its name
  dates.py                  Dates in titles, file names, labels and pages, read the same way by every source parser
  geo.py                    Ward/precinct point-in-polygon lookup
  http.py                   Rate-limited HTTP client with retries
  files.py                  Writes the data files a run commits whole or not at all (a temporary file, then moved into place)
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
                            states (statewide sources), canaries (the towns an engine move checks on every page);
                            a fetching run writes each town's result to its data/run.json,
                            with a few counts for the network homepage (boards followed, meetings in the next 14 days)
  new_town.py               Starts a network town's folder: what the city uses, its codes and ward file, a config started from
                            the closest live town with the rest marked "# To fill in:", the first checks, and a report;
                            `python -m pipeline.new_town <Town> <state> <city website URL>` (publick.org's ADDING-A-TOWN.md)
site/templates/             Shared layout and per-record templates (meeting, board)
site/pages/                 One folder per section; each index.html becomes /<section>/
site/states/<state>/        Each state's own pages (schools, budget) and page parts (its About page sources)
site/strings/<language>.po  Each language's translation of the English wording marked in templates and code
site/static/                CSS, icons, and other files copied as-is (a town's own site/static/ is laid on top)
site/static/vendor/leaflet/ Leaflet 1.9.4 map library, self-hosted (BSD-2-Clause)
tests/                      The engine's tests: pipeline, structure, link, and accessibility checks (offline)
tests/fixtures/town/        The tests' town: Gloucester's config, ward file, and share image
site_checks/                Checks for one town's built site, run by the town workflow before deploying
evals/minutes.json          Minutes checked by hand, decision by decision, for pipeline/evaluate.py
evals/factcheck/            Real summaries with their PDFs' page text, and what the fact check made of each, for pipeline/evaluate_factcheck.py
worker/                     The Cloudflare Workers: index.js serves every site published with deploy.py, by hostname
                            (the logic in sites.js); scheduler-index.js starts a network's daily runs, the engine's
                            release, and the network's engine pull request on time, watches the runs finish,
                            checks every site loads each hour, and sends the weekly digest (the logic in
                            scheduler.js); digest.js signs readers up for the
                            weekly digest's email, and sends it, through Buttondown; digest-test.js sends
                            an issue to test addresses alone, by hand; search.js submits each new town's
                            sitemap to Google Search Console; *.test.js are their tests
.github/workflows/town.yml  The daily update, build, check, and deploy that town repositories call
.github/workflows/ci.yml    The engine's lint (ruff, ruff.toml) and tests, on pushes to main and every pull request
.github/workflows/release.yml   The daily release (see Versions)
.github/workflows/evaluate.yml  The minutes and translation sets against the model, on a pull request labeled `evaluate`
                                that changes a summary prompt, the fact check, or the translator (never on any other)
.github/scripts/commit-data.sh  Commits a run's data, retrying against other runs' pushes
```

## Build and test locally

Requires Python 3.12 or newer (what the workflows use; the tests need it). Clone this repository next to a town's repository, then run commands from the town's repository with the engine on `PYTHONPATH`:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r ../publick-engine/requirements.txt -r ../publick-engine/requirements-dev.txt
python -m playwright install chromium
export PYTHONPATH=../publick-engine

python -m pipeline.fetch_meetings         # update meetings from the city website
python -m pipeline.fetch_311              # update 311 requests from SeeClickFix
python -m pipeline.fetch_socrata_311      # or from the city's open data portal ([socrata_311])
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

A town with a `[storage]` table keeps agenda and minutes PDFs in a bucket (see [Document storage](storage.md#document-storage)). A local fetch then needs the bucket's keys (`STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`), or set `DOCUMENTS_LOCAL=1` to keep PDFs under `data/meetings/` instead. Building the site needs no keys.

The engine's own tests run from this repository, offline, against saved Gloucester data in `tests/fixtures/`, with whole sites for Manchester, Malden and Wallingford built from their meeting systems' saved pages (`tests/test_sample_towns.py`):

```sh
ruff check .                                                # the lint CI runs first (ruff.toml)
python -m pytest
node --test worker/sites.test.js worker/scheduler.test.js   # the Workers' tests (Node 22)
```

The fact check (`pipeline/factcheck.py`) is also replayed on real summaries. `evals/factcheck/sample.json.gz` holds 45 summaries from each town of the network (316 decisions and agenda items: agendas and minutes, every meeting platform, each prompt version, scans checked against their transcription, and summaries the check passed, failed, and found weak), each with its PDF's page text as `factcheck.pages()` reads it and the words a run gives the check, but not the PDF. `evals/factcheck/expected.json` is what the check made of each: its result, its problems, and what the site shows of the headline, the summary, and each decision or item. `tests/test_factcheck_replay.py` checks every summary again and fails on any whose result changed, saying which decision or item is now shown or hidden, and why. When the change is intended, accept it, and commit the new `expected.json` with it, so the diff shows what it does:

```sh
python -m pipeline.evaluate_factcheck                       # what changed, summary by summary
python -m pipeline.evaluate_factcheck --update              # accept it: rewrite evals/factcheck/expected.json
python -m pipeline.evaluate_factcheck build ../publick.org  # the same summaries again, as the network has them now (--resample to choose again)
```

## Adding a section

1. Add a `[[sections]]` entry to `config/<town>.toml`.
2. Create `site/pages/<slug>/index.html` extending `base.html`. It is served at `/<slug>/`.
3. Sub-pages go in sub-folders: `site/pages/<slug>/<name>/index.html` is served at `/<slug>/<name>/`.
4. Pages generated from data (one per record) use a template in `site/templates/` and are added in `pipeline/build_site.py`.
5. Put the page in the menu. The menu is "This week" and three groups, each named for what a reader comes for: City Hall (meetings, decisions, boards, officials), Your street (the street lookup, 311), and The city (budget, schools, housing). They're listed in `site_nav()` in `pipeline/build_site.py`. Each group's menu item goes to its first page, and its pages are tabs above each of them; a page under one (a meeting, a board, a 311 category) has a breadcrumb instead.

New pages are picked up by the tests and the site checks automatically. A section belongs to the engine, so every town that lists it gets it.
