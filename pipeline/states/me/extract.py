"""Save Maine's statewide figures into the engine, once a year.

Maine Revenue Services (MRS) publishes every municipality's tax rate,
commitment, and valuation once a year in the Municipal Valuation Return (MVR)
Statistical Summary, a PDF of about 150 pages; and the Department of
Education's ESSA Dashboard holds every district's graduation rate, chronic
absenteeism, state test results, and spending per pupil. Neither has a file a
town's daily run could read row by row, so this command reads them once a year
(when a new year is posted) and keeps only the figures Publick shows, for every
municipality and district, in pipeline/states/me/figures/. Each Maine town's
run reads its own rows from there. Years already saved are kept; a year read
again replaces the same year saved before.

    python -m pipeline.states.me.extract                # the MVR summaries not yet saved, from MRS's page
    python -m pipeline.states.me.extract --population   # the Census Bureau's estimates
    python -m pipeline.states.me.extract --schools      # the ESSA Dashboard's four measures
    python -m pipeline.states.me.extract ~/Downloads/*.pdf ~/Downloads/*.csv   # files downloaded by hand

The MVR summaries are found on MRS_PAGE by their links' text ("2024 Municipal
Valuation Return Statistical Summary Report") and read with pdfplumber: Section
1 has a row per municipality, by county, with its certified ratio, commitment,
tax rate, and taxable valuation. Its layout has been the same since the 2015
summary (FIRST_MVR_YEAR); older ones aren't read. MRS publishes a tax year's
summary about 19 months after its April 1 assessment date (2024's is dated
November 19, 2025). --mvr also fetches years already saved, the newest first.

The ESSA Dashboard is a Tableau workbook. Its Data Download view has a crosstab
for each measure, which --schools exports through Tableau's own web session
(the requests the dashboard's Download button makes), one school year at a
time, for every district and "Statewide". Tableau's session API isn't a
published one, so if it stops working, download the four crosstabs by hand from
DASHBOARD_PAGE (Data Download, choose the measure, every district and year,
Download > Crosstab > CSV: a tab-separated UTF-16 file) and name them here; each
is recognized by its header. Measures: Graduation Rate, Chronic Absenteeism,
Assessments (2 level), Per Pupil Spending.

--population reads the Census Bureau's newest city and town estimates for Maine
and maps each to its MVR name ("St. Agatha town" -> "SAINT AGATHA", "Cyr
plantation" -> "CYR PLT"); it says how many matched. Run it after the MVR
summaries, whose names it matches.

Commit the changed files in figures/; the next engine release takes them to
every Maine town.
"""

from __future__ import annotations

import argparse
import csv
import io
import itertools
import json
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

FIGURES_DIR = Path(__file__).resolve().parent / "figures"
USER_AGENT = "publick.org (+https://publick.org)"

MRS_PAGE = ("https://www.maine.gov/revenue/taxes/property-tax/municipal-services/"
            "valuation-return-statistical-summary")
FIRST_MVR_YEAR = 2015
POPULATION_PAGE = "https://www.census.gov/data/tables/time-series/demo/popest/2020s-total-cities-and-towns.html"
POPULATION_URL = ("https://www2.census.gov/programs-surveys/popest/datasets/2020-{year}/cities/totals/"
                  "sub-est{year}_23.csv")
DOE_PAGE = "https://www.maine.gov/doe/dashboard"
TABLEAU = "https://oit-tableau.maine.gov"
WORKBOOK, VIEW = "MaineSchoolDashboardsDATADOWNLOAD", "DataDownload"
DASHBOARD_PAGE = f"{TABLEAU}/t/DOE/views/{WORKBOOK}/{VIEW}"

# The sources' own names for their totals, kept next to each municipality's or district's rows.
STATE_TOTAL = "STATE TOTAL"
STATEWIDE = "Statewide"

FILES = {
    "tax": {"source": "Maine Revenue Services, Municipal Valuation Return Statistical Summary",
            "source_url": MRS_PAGE},
    "population": {"source": "U.S. Census Bureau, Population Estimates Program (city and town totals)",
                   "source_url": POPULATION_PAGE},
    "graduation": {"source": "Maine Department of Education, ESSA Dashboard (High School Graduation Rate)",
                   "source_url": DOE_PAGE},
    "absenteeism": {"source": "Maine Department of Education, ESSA Dashboard (Chronic Absenteeism)",
                    "source_url": DOE_PAGE},
    "assessment": {"source": "Maine Department of Education, ESSA Dashboard (Student Assessment)",
                   "source_url": DOE_PAGE},
    "spending": {"source": "Maine Department of Education, ESSA Dashboard (Per Pupil Spending)",
                 "source_url": DOE_PAGE},
}


def number(text) -> float | None:
    """'$69,289,125', '54%', '0.03177', '1,452' -> a number; a blank, dash, or suppressed value ('*') -> None."""
    text = str(text or "").strip().replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def whole(text) -> int | None:
    n = number(text)
    return None if n is None else round(n)


# ---- MRS: the Municipal Valuation Return Statistical Summary ----

# Section 1's money columns, in order.
MONEY = ("commitment", "valuation", "land", "buildings", "land_buildings")
VALUE = re.compile(r"^(\d+%|\$[\d,]+|\d*\.\d+|\$?-)$")


def mvr_row(line: str) -> tuple[str, dict] | None:
    """A Section 1 line as (name, record), or None for a line that isn't a row of figures.

    'LEWISTON 54% $69,289,125 0.03177 $2,180,960,818 $489,861,819 $1,599,848,782 $2,089,710,601'
    """
    words = line.split()
    first = next((i for i, w in enumerate(words) if VALUE.match(w)), None)
    if not first:
        return None
    name, values = " ".join(words[:first]), words[first:]
    if not all(VALUE.match(v) for v in values):
        return None
    ratio = [v for v in values if v.endswith("%")]
    rate = [v for v in values if not v.startswith("$") and "." in v]
    money = [v for v in values if v.startswith("$")]
    if len(money) != len(MONEY):
        return None
    record = {"ratio": whole(ratio[0]) if ratio else None, "rate": number(rate[0]) if rate else None}
    record.update({field: whole(v) for field, v in zip(MONEY, money)})
    return name, record


TITLE = "certified ratio, commitment, tax rate"


def title(text: str) -> str | None:
    """A Section 1 page's title line ("2024 Municipal Valuation Return Statistical Summary - Certified Ratio,
    Commitment, Tax Rate, ..."), among its first lines (2015's pages start with their page number)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()][:3]
    return next((line for line in lines if TITLE in line.lower() and "valuation return" in line.lower()), None)


def mvr_text(pages: list[str], name: str = "") -> tuple[int, dict]:
    """(tax year, {municipality: record, "STATE TOTAL": record}) from the summary's pages' text.

    Each municipality's record has its county, the certified ratio (assessed value as a percentage of market
    value), the commitment (the property tax raised), the tax rate (as MRS writes it: 0.03177 is $31.77 per
    $1,000), the total taxable valuation, and the taxable land, buildings, and land and buildings. The rows
    are checked against the state's total commitment, so a row the reader missed doesn't pass unnoticed.
    Section 1 ends with the state's total; anything after it isn't read."""
    year, county, out = None, None, {}
    for text in pages:
        heading = title(text)
        if not heading or STATE_TOTAL in out:
            continue
        found = re.match(r"(20\d\d) Municipal Valuation Return", heading)
        year = year or (found and int(found.group(1)))
        for line in (line.strip() for line in text.splitlines()):
            if line == "COUNTY TOTALS":
                county = None  # Each county's totals, then the state's: only the state's are kept.
                continue
            if line.endswith(" COUNTY") and not any(c.isdigit() for c in line):
                county = line[:-7].title()
                continue
            row = mvr_row(line)
            if not row:
                continue
            town, record = row
            if town == STATE_TOTAL:
                out[STATE_TOTAL] = {k: v for k, v in record.items() if k not in ("ratio", "rate")}
                break
            elif county and town != "TOTALS":
                if town in out:
                    raise SystemExit(f"{name}: {town} is in Section 1 twice; the summary's layout may have changed.")
                out[town] = {"county": county, **record}
    if not year or STATE_TOTAL not in out or len(out) < 400:
        raise SystemExit(f"{name}: found {len(out)} municipalities in Section 1, not a full Municipal Valuation "
                         f"Return Statistical Summary (from {FIRST_MVR_YEAR} on).")
    total = sum(r["commitment"] or 0 for town, r in out.items() if town != STATE_TOTAL)
    # MRS rounds each row, so the sum is off by a few dollars; the smallest municipality raises thousands.
    if abs(total - out[STATE_TOTAL]["commitment"]) > 500:
        raise SystemExit(f"{name}: the municipalities' commitments add up to ${total:,}, not the state total's "
                         f"${out[STATE_TOTAL]['commitment']:,}; a row wasn't read.")
    return year, out


def mvr(content: bytes, name: str = "") -> tuple[int, dict]:
    import pdfplumber

    pages = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if title(text):
                pages.append(text)
            # Section 1 ends with the state's total. (2018's summary has it twice; the first is read.)
            if pages and re.search(rf"^{STATE_TOTAL} ", text, re.M):
                break
    return mvr_text(pages, name)


def mvr_links(html: str) -> dict[int, str]:
    """{tax year: PDF address} from MRS's page, by the links' text."""
    out = {}
    for href, text in re.findall(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", text))).strip()
        found = re.match(r"(20\d\d) Municipal Valuation Return Statistical Summary", text, re.I)
        if found:
            out[int(found.group(1))] = urljoin(MRS_PAGE, unescape(href))
    return out


# ---- Census ----

# Census names MRS writes otherwise, beyond "St." and "plantation".
CENSUS_ALIASES = {"VERONA ISLAND": "VERONA"}


def census_name(name: str) -> str | None:
    """A Census name as MRS writes it: 'Lewiston city' -> 'LEWISTON', 'St. Agatha town' -> 'SAINT AGATHA',
    'Cyr plantation' -> 'CYR PLT'. Unorganized territories, reservations, and gores aren't municipalities."""
    words = name.split()
    kind = words[-1]
    if kind not in ("city", "town", "plantation"):
        return None
    base = " ".join(words[:-1]).upper()
    base = re.sub(r"^ST\.? ", "SAINT ", base)
    base = CENSUS_ALIASES.get(base, base)
    return f"{base} PLT" if kind == "plantation" else base


def population(text: str, names: set[str] | None = None) -> tuple[dict[int, dict], list[str]]:
    """({year: {MRS name: estimate}}, the Census names not matched) for Maine's towns, cities, and plantations."""
    out: dict[int, dict] = {}
    unmatched = []
    for r in csv.DictReader(io.StringIO(text)):
        if r["SUMLEV"] != "061":
            continue
        name = census_name(r["NAME"])
        if name is None:
            continue
        if names is not None and name not in names:
            # MRS sometimes drops a word the Census keeps, or the other way round.
            alike = [n for n in names if n.replace(" ", "") == name.replace(" ", "")]
            if len(alike) != 1:
                unmatched.append(r["NAME"])
                continue
            name = alike[0]
        for key, value in r.items():
            if key.startswith("POPESTIMATE") and key[11:].isdigit():
                out.setdefault(int(key[11:]), {})[name] = int(value)
    return out, unmatched


def fetch_population(now: datetime) -> str:
    """The newest Census city and town estimates for Maine (census.gov allows scripts)."""
    import requests
    for year in range(now.year, now.year - 3, -1):
        response = requests.get(POPULATION_URL.format(year=year), timeout=60, headers={"User-Agent": USER_AGENT})
        if response.ok and response.text.startswith("SUMLEV,"):
            print(f"Census estimates, vintage {year}")
            return response.content.decode("latin-1")
    raise SystemExit("No Census city and town estimates found for the last three years.")


# ---- Department of Education: the ESSA Dashboard's crosstabs ----

def school_year(text: str) -> int:
    """'2024-2025' -> 2025, the year a school year ends (and the class a graduation rate is for)."""
    return int(text.strip()[-4:])


def crosstab_rows(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter="\t"))
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows]


def whole_district(r: dict) -> bool:
    """The row for a district (or the state) as a whole, all students."""
    return (r.get("School Name") == "All Schools" and r.get("Population") == "All Students"
            and r.get("Disaggregated", "All Students") == "All Students")


def graduation(rows: list[dict]) -> dict[int, dict]:
    """{class year: {district: {cohort, graduated, rate}}}: the four-year adjusted cohort graduation rate."""
    out: dict[int, dict] = {}
    for r in rows:
        if whole_district(r) and r["Cohort"] == "Four Year":
            out.setdefault(school_year(r["Year"]), {})[r["District Name"]] = {
                "cohort": whole(r["Adjusted Cohort"]), "graduated": whole(r["Graduate Count"]), "rate": number(r["Rate"])}
    return out


def absenteeism(rows: list[dict]) -> dict[int, dict]:
    """{school year: {district: {absent, students, rate}}}: students absent 10% or more of the days enrolled."""
    out: dict[int, dict] = {}
    for r in rows:
        if whole_district(r):
            out.setdefault(school_year(r["Year"]), {})[r["District Name"]] = {
                "absent": whole(r["Number of Students Chronically Absent"]), "students": whole(r["Total Students"]),
                "rate": number(r["Percentage of Students Chronically Absent"])}
    return out


SUBJECTS = {"English Language Arts": "ela", "Mathematics": "math"}


def assessment(rows: list[dict]) -> dict[int, dict]:
    """{spring: {district: {ela, math: {proficient, tested, participation}}}}, all grades tested together.

    proficient is the published share at or above state expectations (the two-level crosstab's "At or Above
    State Expectations")."""
    out: dict[int, dict] = {}
    for r in rows:
        subject = SUBJECTS.get(r.get("Assessment", ""))
        if subject and whole_district(r) and r["Achievement Level"] == "At or Above State Expectations":
            out.setdefault(school_year(r["Year"]), {}).setdefault(r["District Name"], {})[subject] = {
                "proficient": number(r["Percentage of Students at Achievement Level"]),
                "tested": whole(r["Total Students Tested"]), "participation": number(r["Percentage of Students Tested"])}
    return out


def spending(rows: list[dict]) -> dict[int, dict]:
    """{school year: {district: {per_pupil, total}}}: total spending per pupil and in all, as the dashboard has it."""
    out: dict[int, dict] = {}
    for r in rows:
        if r.get("School Name") != "All Schools" or r.get("Type") != "Total":
            continue
        field = {"Total Per Pupil": "per_pupil", "Total Expenditures": "total"}.get(r.get("Level"))
        if field:
            amount = number(r["Amount"])
            out.setdefault(school_year(r["Year"]), {}).setdefault(r["District Name"], {})[field] = (
                amount if amount is None else round(amount, 2))
    return out


# Each crosstab: the Data Download parameter that shows it, its sheet, a column only it has, and its reader.
CROSSTABS = {
    "graduation": ("Graduation Rate", "data grad rate", "Adjusted Cohort", graduation),
    "absenteeism": ("Chronic Absenteeism", "data ca", "Percentage of Students Chronically Absent", absenteeism),
    "assessment": ("Assessments (2 level)", "data assessments 2", "Achievement Level", assessment),
    "spending": ("Per Pupil Spending", "data pp spending", "Finance Category", spending),
}


def crosstab(text: str) -> tuple[str, dict[int, dict]] | None:
    """(figures file, {year: rows}) for a Data Download crosstab, recognized by its header."""
    header = text.lstrip("﻿").split("\n", 1)[0]
    if "\t" not in header or "District Name" not in header:
        return None
    for kind, (_, _, column, read_rows) in CROSSTABS.items():
        if column in header.split("\t"):
            return kind, read_rows(crosstab_rows(text))
    return None


def decode(content: bytes) -> str:
    """A crosstab as Tableau saves it (UTF-16 with a byte order mark), or as UTF-8."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    return content.decode("utf-8-sig", errors="replace")


class Dashboard:
    """The Data Download view, through the web session Tableau's own viewer uses. Every request the
    dashboard's Download button makes, in order: start a session, set the filters and the measure, open
    the crosstab dialog for the sheet's id, export it, and fetch the file."""

    PARAMETER = "[Parameters].[NAEP Subject Parameter (copy) 1]"

    def __init__(self):
        import requests
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Origin": TABLEAU, "Referer": f"{DASHBOARD_PAGE}?:embed=y",
                                  "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
        start = self.post(f"{TABLEAU}/vizql/t/DOE/w/{WORKBOOK}/v/{VIEW}/startSession/viewing", params={":embed": "y"})
        info = start.json()
        self.session, self.root = info["sessionid"], TABLEAU + info["vizql_root"]
        boot = self.post(f"{self.root}/bootstrapSession/sessions/{self.session}", data={
            "sheet_id": info["sheetId"], "stickySessionKey": "{}",
            "showParams": json.dumps({"checkpoint": False, "refresh": False, "refreshUnmodified": False})})
        self.years = self.year_values(boot.text)

    def post(self, url: str, **kwargs):
        response = self.http.post(url, timeout=600, **kwargs)
        if not response.ok:
            raise SystemExit(f"ESSA Dashboard: HTTP {response.status_code} from {url}")
        return response

    @staticmethod
    def year_values(boot: str) -> list[str]:
        """The Year filter's school years ('2024-2025'), from the session's opening state."""
        text = boot.replace('\\"', '"')
        found = re.search(r'"caption":"Year","collation"', text)
        # The filter's values follow its caption, up to the next field's.
        values = text[found.end():text.find('"caption":', found.end())] if found else ""
        years = re.findall(r'"v":"(\d{4}-\d{4})"', values)
        if not years:
            raise SystemExit("ESSA Dashboard: no school years in the Data Download's Year filter.")
        return sorted(set(years))

    def command(self, group: str, name: str, data: dict):
        return self.post(f"{self.root}/sessions/{self.session}/commands/{group}/{name}", data=data)

    def filter(self, field: str, values: list[str] | None = None) -> None:
        data = {"dashboard": "Data Download", "qualifiedFieldCaption": field, "exclude": "false",
                "filterUpdateType": "filter-replace" if values else "filter-all"}
        if values:
            data["filterValues"] = json.dumps(values)
        self.command("tabdoc", "dashboard-categorical-filter", data)

    def export(self, measure: str, sheet: str, year: str) -> str:
        """One school year of one measure's crosstab, every district, as text ("" for a year without it)."""
        self.command("tabdoc", "set-parameter-value", {"globalFieldName": self.PARAMETER, "valueString": measure,
                                                       "useUsLocale": "false"})
        self.filter("Year", [year])
        dialog = self.command("tabsrv", "export-crosstab-server-dialog", {"thumbnailUris": "{}"}).text
        found = re.search(r'"sheetName": ?"' + re.escape(sheet) + r'", ?"sheetdocId": ?"([^"]+)"', dialog)
        if not found:
            # The sheet is hidden for a year with no figures (no tests in 2020-21).
            return ""
        result = self.command("tabsrv", "export-crosstab-to-csvserver",
                              {"sheetdocId": found.group(1), "useTabs": "true", "sendNotifications": "true"}).text
        key = re.search(r'"resultKey": ?"([^"]+)"', result)
        if not key:
            raise SystemExit(f"ESSA Dashboard: the {measure} crosstab for {year} wasn't exported.")
        response = self.http.get(f"{self.root}/tempfile/sessions/{self.session}/", timeout=600,
                                 params={"key": key.group(1), "keepfile": "yes", "attachment": "yes"})
        return decode(response.content)


def fetch_schools():
    """(figures file, {year: rows}) for each of the four measures in turn, every school year and district,
    from the ESSA Dashboard."""
    board = Dashboard()
    board.filter("District Name")
    print(f"ESSA Dashboard: school years {', '.join(board.years)}")
    for kind, (measure, sheet, _, read_rows) in CROSSTABS.items():
        out: dict[int, dict] = {}
        for year in board.years:
            years = read_rows(crosstab_rows(board.export(measure, sheet, year)))
            print(f"  {measure}, {year}: {sum(len(rows) for rows in years.values())} districts")
            for y, rows in years.items():
                out.setdefault(y, {}).update(rows)
        if not out:
            raise SystemExit(f"ESSA Dashboard: no {measure} figures for any year; the workbook may have changed.")
        yield kind, out


# ---- Saving ----

def load(kind: str) -> dict:
    path = FIGURES_DIR / f"{kind}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {**FILES[kind], "years": {}}


def save(kind: str, data: dict, now: datetime) -> None:
    data.update(FILES[kind], extracted_at=now.isoformat(timespec="seconds"))
    data["years"] = dict(sorted(data["years"].items()))
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIGURES_DIR / f"{kind}.json").write_text(dump(data), encoding="utf-8")


def dump(data: dict) -> str:
    """JSON with one line per municipality or district, so a new year's file changes little else."""
    compact = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    head = [f" {compact(k)}: {compact(v)}" for k, v in data.items() if k != "years"]
    years = [f"  {compact(year)}: {{\n" + ",\n".join(f"   {compact(name)}: {compact(row)}" for name, row in rows.items())
             + "\n  }" for year, rows in data["years"].items()]
    return "{\n" + ",\n".join(head + [' "years": {\n' + ",\n".join(years) + "\n }"]) + "\n}\n"


def read(path: Path) -> tuple[str, dict[int, dict]]:
    """(figures file, {year: {name: record}}) for one downloaded file, recognized by its contents."""
    content = path.read_bytes()
    if content.startswith(b"%PDF"):
        year, rows = mvr(content, str(path))
        return "tax", {year: rows}
    text = decode(content)
    if text.startswith("SUMLEV,"):
        return "population", {}
    found = crosstab(text)
    if found:
        return found
    raise SystemExit(f"{path}: not a file this command knows. See the list at the top of pipeline/states/me/extract.py.")


def merge(saved: dict, years: dict[int, dict]) -> None:
    for year, rows in years.items():
        saved["years"][str(year)] = dict(sorted(rows.items()))


def fetch_mvr(saved: dict, refetch: bool, http=None) -> dict[int, dict]:
    """The MVR summaries on MRS's page from FIRST_MVR_YEAR on: those not saved yet, or all with refetch.
    (maine.gov answers scripts.)"""
    if http is None:
        import requests
        http = requests.Session()
        http.headers["User-Agent"] = USER_AGENT
    page = http.get(MRS_PAGE, timeout=60)
    page.raise_for_status()
    links = {y: url for y, url in mvr_links(page.text).items() if y >= FIRST_MVR_YEAR}
    if not links:
        raise SystemExit(f"No Municipal Valuation Return Statistical Summary links on {MRS_PAGE}")
    out = {}
    for year in sorted(links, reverse=True):
        if str(year) in saved["years"] and not refetch:
            continue
        response = http.get(links[year], timeout=120)
        response.raise_for_status()
        found, rows = mvr(response.content, links[year])
        if found != year:
            raise SystemExit(f"{links[year]}: the link says {year}, the summary says {found}.")
        print(f"{links[year]}: tax year {year}, {len(rows) - 1} municipalities")
        out[year] = rows
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="MVR summaries, Census estimates, or ESSA crosstabs")
    parser.add_argument("--mvr", action="store_true", help="fetch every MVR summary from MRS's page, saved or not")
    parser.add_argument("--population", action="store_true", help="fetch the Census population estimates")
    parser.add_argument("--schools", action="store_true", help="export the ESSA Dashboard's four measures")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    found: dict[str, list] = {}
    census = None
    for path in args.files:
        kind, years = read(path)
        if kind == "population":
            census = path.read_bytes().decode("latin-1")
            continue
        found.setdefault(kind, []).append(years)
        print(f"{path.name}: {kind}, {', '.join(str(y) for y in sorted(years))}")
    if args.mvr or not (args.files or args.population or args.schools):
        found.setdefault("tax", []).append(fetch_mvr(load("tax"), refetch=args.mvr))
    batches = [(kind, years) for kind, found_years in found.items() for years in found_years]
    # Each measure is saved as soon as it's exported, so a failure later on doesn't lose it.
    for kind, years in itertools.chain(batches, fetch_schools() if args.schools else ()):
        saved = load(kind)
        merge(saved, years)
        save(kind, saved, now)
        print(f"Saved figures/{kind}.json: {', '.join(saved['years'])}")
    if args.population or census:
        tax = load("tax")["years"]
        if not tax:
            raise SystemExit("Save the MVR summaries first: the Census names are matched to theirs.")
        names = set(tax[max(tax)]) - {STATE_TOTAL}
        years, unmatched = population(census or fetch_population(now), names)
        matched = len(years[max(years)]) if years else 0
        print(f"Census estimates matched {matched} of the {len(names)} municipalities in the {max(tax)} MVR summary.")
        if unmatched:
            print(f"Not matched: {', '.join(unmatched)}")
        saved = load("population")
        merge(saved, years)
        save("population", saved, now)
        print(f"Saved figures/population.json: {', '.join(saved['years'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
