# State figures

Tax bills, budgets and school figures come from each state's own agencies. The engine has a package for each New England state; a town elsewhere still gets every other section.

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
town's codes when they aren't "101" or "1010". A town that phases in a
revaluation's increases over several years (Connecticut General Statutes
12-62c) taxes each parcel's assessment before the revaluation plus a share of
its increase, while the parcel files carry the full new assessments, far over
the grand list until the last year; `[finance] phase_in = { revaluation =
2024, years = 4 }` (the revaluation's grand list year and the number of years,
from the town's own account of its phase-in: Hamden's) has each parcel's value
in those years worked out the same way, from its assessment in the parcel file
of the revaluation's year, matched by its `pid`; a decrease isn't phased in.

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
Education publish tax rates, grand lists, parcels by property category, taxes
raised, and spending per pupil as statewide workbooks once a year, under names
that change each year. One command finds the newest on the state's pages, by
their links' text, and saves every town's and district's rows into the engine:

```sh
python -m pipeline.states.vt.extract                # or name workbooks downloaded by hand
python -m pipeline.states.vt.extract --population   # Census estimates, matched to the state's town names
```

Commit `pipeline/states/vt/figures/`, as for New Hampshire. The average
homestead bill is calculated daily from those rates and VCGI's statewide parcel
data, while the parcel data's grand list year has rates in the saved figures and
its homestead values add up to the state's homestead grand list. Where the
parcel data lacks some of a town's parcels (Winooski's maps predate many of its
condominiums), the bill is calculated while the parcel data's R1 parcels, the
homes it averages, match PVR's count and listed value of the town's R1, and the
homestead value it lacks is no more than the grand list it lacks; the bill's
note says so. Graduation rates, chronic absenteeism, and test results come from
data.vermont.gov at each run; each spring's test results are a dataset of their
own, found by name.

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
