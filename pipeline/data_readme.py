"""A town's data/README.md: each file in its data folder, what it holds, and where it comes from, on what terms.

    python -m pipeline.data_readme [--town <town>] [--force]

A town's data folder is public, and its README is the record of each file's source and license. It's written
from the town's config and the files its data folder has: a row for each kind of file that's there, its source
named and linked as the config gives it (the meetings' calendars and portals, the ward file's publisher, the
Census place), and the state's figures from the sources each state package reads (STATE_SOURCES). Run it
when a town is added, and again when its sources change.

An existing README is left as it is, since one written by hand may say more; --force writes it again.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from pipeline.config import DATA_DIR, DEFAULT_TOWN, load_config

FILE = "README.md"

PUBLIC_RECORDS = "(public records)"
PUBLIC_DOMAIN = "(public domain)"
BLS = "[BLS Local Area Unemployment Statistics](https://www.bls.gov/lau/) (public domain)"
BPS = "[Census Building Permits Survey](https://www.census.gov/construction/bps/)"
SEECLICKFIX = ("[SeeClickFix](https://seeclickfix.com), "
               "[CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/)")
CENSUS_POPULATION = ("[Census population estimates]"
                     "(https://www.census.gov/data/tables/time-series/demo/popest/2020s-total-cities-and-towns.html)")

# Each state's figures, by the file each source writes: what the file holds and its source, as the state's
# package reads it (pipeline/states/<state>/). A state added there gets its rows here.
STATE_SOURCES = {
    "MA": {
        "finance/tax_bill.json": (
            "Average single-family tax bill, by fiscal year",
            "[Mass. Division of Local Services, Municipal Databank](https://dls-gw.dor.state.ma.us/reports/rdPage.aspx"
            "?rdReport=AverageSingleTaxBill.SingleFamTaxBill_wRange) (public records)"),
        "finance/budget.json": (
            "General fund spending by function, revenue by source, levy and levy limit, free cash, stabilization "
            "fund, bond ratings, and spending per resident with the state median",
            "[Mass. Division of Local Services, Municipal Databank](https://dls-gw.dor.state.ma.us/reports/rdPage.aspx"
            "?rdReport=ScheduleA.GenFund_MAIN) (public records)"),
        "schools/schools.json": (
            "Graduation rate, chronic absenteeism, and MCAS results for the district and the state, by year",
            "[DESE, Education-to-Career data hub](https://educationtocareer.data.mass.gov) (public records)"),
    },
    "NH": {
        "finance/tax_bill.json": (
            "Average single-family tax bill, calculated from the state's tax rates and the parcel map",
            "[NH Department of Revenue Administration](https://www.revenue.nh.gov/about-dra/municipal-and-property-"
            "division/municipal-and-property-reports/municipal-and-village), [NH GRANIT](https://granit.unh.edu) "
            "parcels (public records)"),
        "finance/budget.json": (
            "Tax rates, what the property tax raises, and property tax per resident with the state median",
            f"NH Department of Revenue Administration; {CENSUS_POPULATION} (public records)"),
        "schools/schools.json": (
            "Graduation rate, state test results (SAS), and cost per pupil for the district and the state, by year",
            "[New Hampshire Department of Education](https://www.education.nh.gov) (public records)"),
    },
    "CT": {
        "finance/tax_bill.json": (
            "Average single-family tax bill, calculated from the state's mill rates and the statewide Parcel and "
            "CAMA file",
            "[Connecticut Office of Policy and Management, on data.ct.gov](https://data.ct.gov/d/emyx-j53e) "
            "(public records)"),
        "finance/budget.json": (
            "Mill rate, tax levy, grand list, adopted budget, and the audited Municipal Fiscal Indicators, with "
            "property tax per resident and the state median",
            "Connecticut Office of Policy and Management, on [data.ct.gov](https://data.ct.gov) (public records)"),
        "schools/schools.json": (
            "Graduation rate, chronic absenteeism, state test results (Smarter Balanced), and spending per pupil "
            "for the district and the state, by year",
            "[Connecticut State Department of Education, EdSight](https://public-edsight.ct.gov) (public records)"),
    },
    "VT": {
        "finance/tax_bill.json": (
            "Average homestead tax bill by tax year, calculated from the state's tax rates and the homestead "
            "values in the statewide parcel data",
            "[Vermont Department of Taxes, PVR Annual Report](https://tax.vermont.gov/pvr-annual-report), "
            "[VCGI statewide parcel data](https://geodata.vermont.gov/datasets/vt-data-statewide-standardized-"
            "parcel-data-parcel-polygons) (public records)"),
        "finance/budget.json": (
            "Tax rates (homestead and nonhomestead education, municipal) with the median of Vermont's towns, what "
            "the property tax raised by part, the grand list, and property tax per resident with the state median",
            "[Vermont Department of Taxes, PVR Annual Report](https://tax.vermont.gov/pvr-annual-report); "
            f"{CENSUS_POPULATION} (public records)"),
        "schools/schools.json": (
            "Graduation rate, chronic absenteeism, and state test results (VTCAP) for the district and the state, "
            "by year, and the district's budget and education spending per pupil",
            "Vermont Agency of Education: [Vermont Education Dashboard data on data.vermont.gov]"
            "(https://data.vermont.gov), [per pupil spending report](https://education.vermont.gov/accountability-"
            "data/financial-reports/per-pupil-spending) (public records)"),
    },
    "ME": {
        "finance/tax_bill.json": (
            "Average single-family tax bill by tax year, calculated from the average assessed value of the "
            "town's single-family homes and the tax rate",
            "[Maine Revenue Services](https://www.maine.gov/revenue/taxes/property-tax/municipal-services/"
            "valuation-return-statistical-summary) and the [Maine GeoLibrary's parcel table]"
            "(https://www.arcgis.com/home/item.html?id=346131b710a645ffb624f448a9cba6d4) (public records)"),
        "finance/budget.json": (
            "Tax rate by tax year with the median of Maine's municipalities, the property tax commitment, taxable "
            "valuation and certified ratio, and property tax per resident with the state median",
            "[Maine Revenue Services, Municipal Valuation Return Statistical Summary](https://www.maine.gov/revenue/"
            f"taxes/property-tax/municipal-services/valuation-return-statistical-summary); {CENSUS_POPULATION} "
            "(public records)"),
        "schools/schools.json": (
            "Graduation rate, chronic absenteeism, state test results, and spending per pupil for the district "
            "and the state, by year",
            "[Maine Department of Education, ESSA Dashboard](https://www.maine.gov/doe/dashboard) (public records)"),
    },
    "RI": {
        "finance/budget.json": (
            "Tax rates by class of property, the tax levy and net assessed value by class, and property tax per "
            "resident, with the state's median",
            "[Rhode Island Division of Municipal Finance](https://municipalfinance.ri.gov/) statewide tables; "
            f"{CENSUS_POPULATION} (public records)"),
        "schools/schools.json": (
            "Graduation rate, chronic absenteeism, RICAS results (grades 3-8), and spending per pupil for the "
            "district and the state, by year",
            "[Rhode Island Department of Education, report card data files and assessment data portal]"
            "(https://reportcard.ride.ri.gov/DataFiles) (public records)"),
    },
}

# Recording platforms ([[recordings.sources]] platform), as their names are written.
PLATFORM_NAMES = {"telvue": "TelVue", "cablecast": "Cablecast", "castus": "Castus", "civicclerk": "CivicClerk",
                  "internet_archive": "the Internet Archive", "youtube": "YouTube"}
AREAS = {"wards": "Ward", "precincts": "Voting precinct", "districts": "District"}


def link(text: str, url: str | None) -> str:
    return f"[{text}]({url})" if url else text


def listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def kind(config: dict, data_dir: Path) -> str:
    """"city" or "town": the config's [town] kind, else the Census Bureau's word (data/place.json)."""
    if config["town"].get("kind"):
        return config["town"]["kind"]
    place = data_dir / "place.json"
    return json.loads(place.read_text(encoding="utf-8")).get("kind", "city") if place.exists() else "city"


def meeting_sources(config: dict, place: str) -> str:
    """The town's meeting sources, each named and linked as its pages link them: "The city's calendar and Agenda
    Center", then any school board's or other body's source by its name."""
    from pipeline.build_site import meeting_links
    links = meeting_links(config)
    own = []
    if config.get("meetings") and links["has_calendar"] and links["calendar"]:
        own.append(link("calendar", links["calendar"]))
    if links["agenda_center"]:
        own.append(link("Agenda Center", links["agenda_center"]))
    if "archive" in config and links["archive"]:
        own.append(link("Archive Center", links["archive"]))
    if links["portal"]:
        own.append(link("meeting portal", links["portal"]))
    if links["documents_page"]:
        own.append(link("documents page", links["documents_page"]))
    if links["opengov_only"]:
        o = config["opengov_meetings"]
        return link(o["source_name"], o["page_url"])
    # Each other source once by its name, linked to its first page; a page of the same name on another site
    # (a school board's website and its BoardBook page) is added by its host.
    named: dict[str, list[str]] = {}
    for s in links["schools"]:
        urls = named.setdefault(s["name"], [])
        if s["url"] not in urls and urlsplit(s["url"]).netloc not in {urlsplit(u).netloc for u in urls}:
            urls.append(s["url"])
    others = [link(name, urls[0]) + "".join(f", also on {link(urlsplit(u).netloc, u)}" for u in urls[1:])
              for name, urls in named.items()]
    return "; ".join(([f"The {place}'s {listed(own)}"] if own else []) + ([listed(others)] if others else []))


def rows(config: dict, data_dir: Path) -> list[tuple[str, str, str]]:
    """(path, contents, source) for each kind of file the town's data folder has."""
    has = lambda path: (data_dir / path).exists()  # noqa: E731
    place = kind(config, data_dir)
    site = config["site"]["name"]
    languages = config["site"].get("languages", ["en"])
    storage = config.get("storage")

    def bucket(folder: str = "") -> str | None:
        """Where the town's files in the documents bucket ([storage]) are, as a phrase, or None without one."""
        if not storage:
            return None
        host = storage["public_url"].split("://", 1)[-1].rstrip("/")
        prefix = storage.get("prefix", config["slug"]).strip("/")
        return f"kept in the documents bucket at `{host}/{prefix}/{folder}` rather than in git"

    out = []

    if has("meetings/meetings.json"):
        out.append(("`meetings/meetings.json`",
                    "One record per public meeting: board, date, time, place, status (cancelled, removed), links to "
                    "its agenda and minutes, and every change since it was first seen",
                    f"{meeting_sources(config, place) or f'The {place} website'} {PUBLIC_RECORDS}"))
        statuses = sorted(p.name for p in (data_dir / "meetings").glob("*status.json"))
        if statuses:
            out.append((", ".join(f"`meetings/{s}`" for s in statuses),
                        "When each meetings source was last checked, for the site and the stale-data alert", "Derived"))
        out.append(("Agendas and minutes",
                    f"Each agenda and set of minutes as the {place} posted it, "
                    + (bucket() or "in `meetings/`"),
                    f"The meeting sources above {PUBLIC_RECORDS}"))
    if has("meetings/recordings.json"):
        platforms = sorted({s["platform"] for s in config.get("recordings", {}).get("sources", [])},
                           key=list(PLATFORM_NAMES).index)
        out.append(("`meetings/recordings.json`",
                    "Meetings' recordings, each matched to its meeting, and how far each got toward a summary",
                    f"{listed([PLATFORM_NAMES[p] for p in platforms]) or 'The meetings video platforms'} "
                    f"{PUBLIC_RECORDS}; transcribed by {site}"))
    if has("summaries"):
        spanish = ", and their Spanish translations" if "es" in languages else ""
        out.append(("`summaries/`" + (", `summaries/es/`" if "es" in languages else ""),
                    f"AI-written summaries of agendas and minutes{spanish}, each checked against its document and "
                    "labeled as AI-generated on the site", f"{site}, from the documents above"))
    if has("strings"):
        out.append(("`strings/`", "Machine-drafted translations of the town's own text (board and category names, "
                    "section summaries), until its config gives its own", site))

    if has("311"):
        if "socrata_311" in config:
            s = config["socrata_311"]
            source = f"{link(s.get('publisher', s['domain']), 'https://' + s['domain'])}"
            source += f", {link(s['license'], s.get('license_url'))}" if s.get("license") else f" {PUBLIC_RECORDS}"
        else:
            source = SEECLICKFIX
        where = bucket("data/311/") or "in `311/`"
        out.append(("`311/requests.json`, `311/scorecard.json`",
                    "One record per public 311 request (category, location, area, status, and when it was submitted, "
                    "acknowledged, closed or reopened; no descriptions, photos or reporter details), and the figures "
                    f"the 311 pages show, {where}", source))
        out.append(("`311/status.json`, `311/sync.json`", "When 311 was last read, and how far its reading has got",
                    "Derived"))

    for path, (contents, source) in STATE_SOURCES.get(config["town"].get("state_abbr", ""), {}).items():
        if has(path):
            out.append((f"`{path}`", contents, source))

    if has("housing/housing.json"):
        housing = config.get("housing", {})
        contents = "New homes permitted by year; home value, rent, and rent burden with margins of error"
        geo = housing.get("census_geo")
        sources = [BPS, link("American Community Survey via Census Reporter",
                             f"https://censusreporter.org/profiles/{geo}/" if geo else "https://censusreporter.org")]
        if housing.get("shi_url"):
            contents += "; the Subsidized Housing Inventory's share"
            sources.append("[EOHLC Subsidized Housing Inventory]"
                           "(https://www.mass.gov/info-details/subsidized-housing-inventory-shi)")
        out.append(("`housing/housing.json`", contents, ", ".join(sources) + f" {PUBLIC_RECORDS}"))
    if has("labor/unemployment.json"):
        out.append(("`labor/unemployment.json`",
                    f"Monthly unemployment rate for the {place}, with the statewide rate", BLS))
    if has("permits/permits.json"):
        permits = config.get("permits", {})
        out.append(("`permits/permits.json`", "Building and demolition permits",
                    f"{link(f'The {place} permit data', permits.get('page_url'))} {PUBLIC_RECORDS}"))

    credit = config.get("seeclickfix") or config.get("officials", {})
    area = AREAS.get(config["town"].get("areas", "wards"), "Ward")
    for shape in sorted((data_dir / "static").glob("*.geojson")):
        publisher = credit.get("wards_publisher", "MassGIS")
        year = credit.get("wards_year", 2022)
        url = credit.get("wards_url", "https://gis.data.mass.gov/maps/aec5130790814ace94438d3bcf23cf9a")
        out.append((f"`static/{shape.name}`", f"{area} boundaries, with 2020 census population",
                    f"{link(f'{publisher} ({year})', url)}; population from the "
                    f"[U.S. Census Bureau](https://www.census.gov) {PUBLIC_DOMAIN}"))
    if has("addresses.json"):
        out.append(("`addresses.json`", "House number ranges by street, each with its ward (and its voting precinct, "
                    "where the ward file names them), for the Officials page's address lookup",
                    f"[Census TIGER/Line address ranges](https://www.census.gov/geographies/mapping-files/time-series/"
                    f"geo/tiger-line-file.html) {PUBLIC_DOMAIN}"))
    if has("place.json"):
        out.append(("`place.json`", f"The Census Bureau's word for the place (\"{place}\"), for the site's wording",
                    f"[Census TIGERweb](https://tigerweb.geo.census.gov) {PUBLIC_DOMAIN}"))

    bookkeeping = [f"`{p}`" for p in ("meetings/identities.json", "run.json", "checks.json")
                   if has(p)] + [f"`{p.name}`" for p in sorted(data_dir.glob("summary-*.json"))]
    if bookkeeping:
        out.append((", ".join(bookkeeping), "Each meeting's identity and page address, the last daily run's record, "
                    "the checks' results, and the summaries' bookkeeping", "Derived"))
    return out


def capitalized(s: str) -> str:
    """The text with its first letter capitalized, inside a link's brackets too ("[the City of ...](...)")."""
    i = 1 if s.startswith("[") else 0
    return s[:i] + s[i:i + 1].upper() + s[i + 1:]


def text(config: dict, data_dir: Path) -> str:
    def cell(s: str) -> str:
        return s.replace("|", "\\|")
    table = "\n".join(f"| {cell(p)} | {cell(c)} | {cell(capitalized(s))} |" for p, c, s in rows(config, data_dir))
    return f"""# Data

Collected by the site's daily run. Records are never deleted; changes over time are kept in each record and in
git history.

| Path | Contents | Source and license |
|---|---|---|
{table}

What {config['site']['name']} makes here (the summaries and translations, and figures it calculates) is under
the license in the repository's `LICENSE`; each source keeps its own terms.

<!-- Written by the engine's pipeline/data_readme.py from the town's config and data folder. -->
"""


def write(config: dict, data_dir: Path, force: bool = False) -> Path | None:
    """Write the README, unless one is there and force isn't set. Returns its path, or None if left as it was."""
    path = data_dir / FILE
    if path.exists() and not force:
        return None
    path.write_text(text(config, data_dir), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="write it again, even if the folder has one")
    args = parser.parse_args()
    path = write(load_config(args.town), args.data, force=args.force)
    print(f"Wrote {path}" if path else f"{args.data / FILE} is there already; --force writes it again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
