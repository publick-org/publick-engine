"""Save Vermont's statewide figures from the state's yearly files.

Vermont's Department of Taxes, Division of Property Valuation and Review (PVR),
publishes each tax year's rates, grand lists, and taxes raised for every town as
spreadsheets in its Annual Report's supplemental data, and the Agency of
Education (AOE) publishes each fiscal year's spending per pupil for every
school district. The files appear once a year, under names that change from
year to year, three links deep, so this command finds them by their links' text,
reads only the figures the pages show, and saves them in
pipeline/states/vt/figures/; each Vermont town's daily run reads its own rows
from there. Years already saved are kept; a year in a new file replaces the same
year saved before.

    python -m pipeline.states.vt.extract                    # fetch every year the state's pages list
    python -m pipeline.states.vt.extract ~/Downloads/*.xlsx # or read files downloaded by hand
    python -m pipeline.states.vt.extract --population       # Census estimates (or name a downloaded sub-est CSV)

The state's sites answer scripts that name themselves (they refuse only the
default User-Agent of curl and Python's requests). A file downloaded by hand is
recognized by what's in it, not its name:

  PVR Annual Report supplemental data (ANNUAL_REPORT), each year:
    Tax Rates: Taxes and Tax Rates by County (.xlsx)
    Education and Municipal Listed and Equalized Grand List by Town (.xlsx)
  AOE, Per Pupil Spending (SPENDING_PAGE), each fiscal year from 2025:
    FY <year> Report (.xlsx, "Classifying School Districts by Size and Type")

Commit the changed files in figures/; the next engine release takes them to
every Vermont town.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import openpyxl

FIGURES_DIR = Path(__file__).resolve().parent / "figures"
USER_AGENT = "publick.org (+https://publick.org)"

ANNUAL_REPORT = "https://tax.vermont.gov/pvr-annual-report"
SPENDING_PAGE = "https://education.vermont.gov/accountability-data/financial-reports/per-pupil-spending"
POPULATION_PAGE = "https://www.census.gov/data/tables/time-series/demo/popest/2020s-total-cities-and-towns.html"
POPULATION_URL = ("https://www2.census.gov/programs-surveys/popest/datasets/2020-{year}/cities/totals/"
                  "sub-est{year}_50.csv")
# The state's own name for its totals, used as the key next to each town's or district's.
STATE = "State"
# Spending per pupil is per long-term weighted pupil from fiscal year 2025, when Vermont's pupil weights changed;
# earlier years' per equalized pupil can't be compared with it.
FIRST_SPENDING_YEAR = 2025

FILES = {
    "tax": {"source": "Vermont Department of Taxes, Property Valuation and Review, Annual Report: "
                      "Taxes and Tax Rates by County", "source_url": ANNUAL_REPORT},
    "grand_list": {"source": "Vermont Department of Taxes, Property Valuation and Review, Annual Report: "
                             "Education and Municipal Listed and Equalized Grand List by Town",
                   "source_url": ANNUAL_REPORT},
    "spending": {"source": "Vermont Agency of Education, Per Pupil Spending", "source_url": SPENDING_PAGE},
    "population": {"source": "U.S. Census Bureau, Population Estimates Program (city and town totals)",
                   "source_url": POPULATION_PAGE},
}


def norm(text) -> str:
    """A header as plain lowercase words, with the state's abbreviations spelled out."""
    text = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return re.sub(r"\bgl\b", "grand list", text).replace(" actual ", " ")


def number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def rounded(value, places: int = 4) -> float | None:
    """Spreadsheet floats ('0.7897000000000001') as the state wrote them."""
    n = number(value)
    return None if n is None else round(n, places)


def sheet_rows(content: bytes, sheet: int | str = 0) -> list[tuple]:
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = book[sheet] if isinstance(sheet, str) else book.worksheets[sheet]
    return [tuple(row) for row in ws.iter_rows(values_only=True)]


def table(rows: list[tuple], must: tuple[str, ...]) -> tuple[dict[str, int], list[tuple]]:
    """({header: column}, rows below the header): the header is the first row naming every one of must."""
    for i, row in enumerate(rows[:10]):
        header = {norm(c): j for j, c in enumerate(row) if c is not None}
        if all(m in header for m in must):
            return header, rows[i + 1:]
    raise ValueError(f"no header row with {', '.join(must)}")


# ---- PVR: taxes and tax rates, and the grand list ----

TAX_COLUMNS = {
    "homestead education grand list": "homestead_grand_list", "nonhomestead education grand list": "nonhomestead_grand_list",
    "municipal grand list": "municipal_grand_list", "education homestead taxes": "homestead_taxes",
    "education nonhomestead taxes": "nonhomestead_taxes", "municipal taxes": "municipal_taxes",
    "homestead tax rate": "homestead_rate", "nonhomestead tax rate": "nonhomestead_rate",
    "municipal tax rate": "municipal_rate", "local agreement tax rate": "local_agreement_rate",
    "local agreement taxes collected": "local_agreement_taxes",
}
RATES = ("homestead_rate", "nonhomestead_rate", "municipal_rate", "local_agreement_rate")
GRAND_LIST_COLUMNS = {
    "taxable parcel count": "parcels", "parcel count": "parcels", "cod": "cod", "cla": "cla",
    "education grand list (egl)": "education_grand_list",
    "state certified equalized education property value (eepv)": "equalized_value",
    "municipal grand list": "municipal_grand_list", "equalized municipal property value": "equalized_municipal_value",
}


def taxes(content: bytes) -> dict[int, dict]:
    """{tax year: {town: record}} from a Taxes and Tax Rates workbook. A town's own row is District ID 0; its fire,
    village, and improvement districts follow on rows of their own, with only their own rate and taxes."""
    header, rows = table(sheet_rows(content), ("tax year", "town name", "homestead tax rate"))
    out: dict[int, dict] = {}
    for row in rows:
        year, town = number(row[header["tax year"]]), row[header["town name"]]
        district = number(row[header["district id"]]) if "district id" in header else 0
        if not year or not town or district:
            continue
        record = {"town_code": int(number(row[header["town code"]]))}
        for column, key in TAX_COLUMNS.items():
            if column in header:
                record[key] = rounded(row[header[column]], 4 if key in RATES else 2)
        out.setdefault(int(year), {})[str(town).strip()] = record
    if not out:
        raise ValueError("no towns in the tax rates workbook")
    return out


def grand_lists(content: bytes) -> dict[int, dict]:
    """{tax year: {town: record}} from a Listed and Equalized Grand List workbook."""
    header, rows = table(sheet_rows(content), ("tax year", "town name", "cla"))
    out: dict[int, dict] = {}
    for row in rows:
        year, town = number(row[header["tax year"]]), row[header["town name"]]
        if not year or not town:
            continue
        record = {"town_code": int(number(row[header["town code"]]))}
        for column, key in GRAND_LIST_COLUMNS.items():
            if column in header and key not in record:
                value = rounded(row[header[column]], 2)
                record[key] = round(value) if key == "parcels" and value is not None else value
        out.setdefault(int(year), {})[str(town).strip()] = record
    if not out:
        raise ValueError("no towns in the grand list workbook")
    return out


# ---- AOE: spending per pupil ----

def spending(content: bytes) -> tuple[int, dict]:
    """(fiscal year, {district code: record}) from an AOE per pupil spending workbook: each district's budget as
    voted and its education spending (the budget less the district's own other revenues, what the education tax
    pays), per long-term weighted pupil, and the state's, from the printout's "All towns" rows."""
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    data = next(name for name in book.sheetnames if name.lower().startswith("spenddata"))
    printout = next(name for name in book.sheetnames if "printout" in name.lower())
    year = int(re.search(r"FY\s*(\d{2,4})", data, re.I).group(1))
    year = year + 2000 if year < 100 else year
    rows = sheet_rows(content, data)
    header, body = table(rows, ("district name", "lea"))
    pupils = next(j for h, j in header.items() if h.endswith("ltw adm") and "per" not in h)
    budget = next(j for h, j in header.items() if h.endswith("budgets per ltw adm"))
    education = next(j for h, j in header.items() if h.endswith("education spending per ltw adm"))
    out = {}
    for row in body:
        code = row[header["lea"]]
        if not code or number(row[budget]) is None:
            continue
        out[str(code).strip()] = {"name": str(row[header["district name"]]).strip(), "pupils": rounded(row[pupils], 2),
                                  "budget": rounded(row[budget], 2), "education_spending": rounded(row[education], 2)}
    # The printout's sections: C, budgets per pupil, and E, education spending per pupil; each ends with the state.
    section, state = None, {}
    for row in sheet_rows(content, printout):
        if isinstance(row[0], str) and re.fullmatch(r"[A-Z]\.", row[0].strip()):
            section = row[0].strip()[0]
        elif section in ("C", "E") and isinstance(row[1], str) and row[1].lower().startswith("all towns"):
            state["budget" if section == "C" else "education_spending"] = number(row[6])
    if len(state) != 2 or not out:
        raise ValueError("the per pupil spending workbook's layout has changed")
    out[STATE] = {"name": "State of Vermont", **state}
    return year, out


# ---- Census: population ----

def place_name(name: str) -> str:
    """A town's name, plain, for matching the Census's names to PVR's: "Essex Jct." and "Essex Junction city" are
    both "essex junction city"; "Avery's gore" and "Averys Gore", "averys gore"."""
    name = re.sub(r"[.'’]", "", name.lower())
    name = re.sub(r"\bjct\b", "junction", name)
    name = re.sub(r"\bst\b", "saint", name)
    return re.sub(r"\s+", " ", name).strip()


def population(text: str, towns: list[str]) -> dict[int, dict]:
    """{year: {PVR town name: estimate}}. The Census names each town with its kind ("Burlington city", "Rutland
    town"); PVR names a town by itself unless a city and a town share the name ("Rutland City", "Rutland Town")."""
    census: dict[str, dict] = {}
    for r in csv.DictReader(io.StringIO(text)):
        if r["SUMLEV"] == "061":
            census.setdefault(place_name(r["NAME"]), r)
    by_base: dict[str, list] = {}
    for full, r in census.items():
        by_base.setdefault(re.sub(r" (town|city|gore|grant|location)$", "", full), []).append(r)
    out: dict[int, dict] = {}
    for town in towns:
        key = place_name(town)
        r = census.get(key) or census.get(key + " city") or census.get(key + " town")
        if r is None and len(by_base.get(key, [])) == 1:
            r = by_base[key][0]
        if r is None:
            continue
        for column, value in r.items():
            if column.startswith("POPESTIMATE") and column[11:].isdigit() and value:
                out.setdefault(int(column[11:]), {})[town] = int(value)
    return out


# ---- Finding the files ----

class Links(HTMLParser):
    """Every link on a page: [(text, href)]."""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href, self.text = dict(attrs).get("href"), []

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            self.links.append((re.sub(r"\s+", " ", "".join(self.text)).strip(), self.href))
            self.href = None

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)


def links(client, url: str) -> list[tuple[str, str]]:
    parser = Links()
    parser.feed(client.get(url).text)
    return [(text, urljoin(url, href)) for text, href in parser.links]


def workbook_on(client, document_page: str) -> str | None:
    """The .xlsx a state document page offers. Some older years' pages offer only a PDF."""
    found = [href for _, href in links(client, document_page) if href.lower().endswith(".xlsx")]
    if not found:
        print(f"No workbook on {document_page}; skipped.")
    return found[0] if found else None


def state_files(client) -> list[str]:
    """The workbooks the state's pages list now: each Annual Report year's two, and each fiscal year's spending."""
    found = []
    for text, year_page in links(client, ANNUAL_REPORT):
        if not re.match(r"\d{4} Supplemental Data", text):
            continue
        for title, page in links(client, year_page):
            if re.match(r"Tax Rates: Taxes and Tax Rates by County", title) and "(PDF)" not in title \
                    or re.match(r"Education and Municipal Listed and Equalized Grand List by Town", title) \
                    and "(PDF)" not in title:
                found.append(workbook_on(client, page))
    for text, page in links(client, SPENDING_PAGE):
        m = re.match(r"FY\s*(\d{4})\s+Report", text.replace("​", "").replace("\xa0", " ").strip())
        if m and int(m.group(1)) >= FIRST_SPENDING_YEAR:
            found.append(workbook_on(client, page))
    return [url for url in dict.fromkeys(found) if url]


class Client:
    """A plain HTTP client that names itself (the state's sites refuse requests' default User-Agent)."""

    def __init__(self):
        import requests
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def get(self, url: str):
        response = self.session.get(url, timeout=90)
        response.raise_for_status()
        return response


# ---- Saving ----

def load(kind: str) -> dict:
    path = FIGURES_DIR / f"{kind}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {**FILES[kind], "years": {}}


def dump(data: dict) -> str:
    """JSON with one line per town or district, so a new year's file changes little else and stays small."""
    compact = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    head = [f" {compact(k)}: {compact(v)}" for k, v in data.items() if k != "years"]
    years = [f"  {compact(year)}: {{\n" + ",\n".join(f"   {compact(name)}: {compact(row)}" for name, row in rows.items())
             + "\n  }" for year, rows in data["years"].items()]
    return "{\n" + ",\n".join(head + [' "years": {\n' + ",\n".join(years) + "\n }"]) + "\n}\n"


def save(kind: str, data: dict, now: datetime) -> None:
    data.update(FILES[kind], extracted_at=now.isoformat(timespec="seconds"))
    data["years"] = dict(sorted(data["years"].items()))
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIGURES_DIR / f"{kind}.json").write_text(dump(data), encoding="utf-8")


class Unknown(Exception):
    """A file this command doesn't know."""


def read(content: bytes, name: str) -> tuple[str, dict[int, dict]]:
    """(figures file, {year: {name: record}}) for one workbook, recognized by its contents."""
    if not content.startswith(b"PK"):
        raise Unknown(name)
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    if any(sheet.lower().startswith("spenddata") for sheet in book.sheetnames):
        year, rows = spending(content)
        return "spending", {year: rows}
    cells = " ".join(norm(c) for row in sheet_rows(content)[:6] for c in row if c)
    if "homestead tax rate" in cells:
        return "tax", taxes(content)
    if "cla" in cells.split() and "equalized" in cells:
        return "grand_list", grand_lists(content)
    raise Unknown(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="workbooks downloaded by hand (default: fetch them)")
    parser.add_argument("--population", action="store_true", help="fetch the Census population estimates")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    client = Client()
    found: dict[str, list] = {}
    files = [(p.name, p.read_bytes()) for p in args.files]
    # A Census estimates file is matched to the saved towns after the workbooks are read.
    census_files = [(name, content) for name, content in files if content.startswith(b"SUMLEV,")]
    sources = [(name, content) for name, content in files if not content.startswith(b"SUMLEV,")]
    fetched = not args.files and not args.population
    if fetched:
        sources = [(url.rsplit("/", 1)[1], client.get(url).content) for url in state_files(client)]
    for name, content in sources:
        try:
            kind, years = read(content, name)
        except Unknown:
            # The state's pages also list older years, in layouts this command doesn't read.
            if fetched:
                print(f"{name}: an older layout; skipped.")
                continue
            raise SystemExit(f"{name}: not a workbook this command knows. See the list at the top of "
                             "pipeline/states/vt/extract.py.")
        found.setdefault(kind, []).append(years)
        print(f"{name}: {kind}, {', '.join(str(y) for y in sorted(years))}")
    for kind, batches in found.items():
        saved = load(kind)
        for years in batches:
            for year, rows in years.items():
                saved["years"][str(year)] = dict(sorted(rows.items()))
        save(kind, saved, now)
        print(f"Saved figures/{kind}.json: {', '.join(saved['years'])}")
    census = [content.decode("latin-1") for _, content in census_files]
    if args.population:
        for year in range(now.year, now.year - 3, -1):
            response = client.session.get(POPULATION_URL.format(year=year), timeout=60)
            if response.ok and response.text.startswith("SUMLEV,"):
                print(f"Census estimates, vintage {year}")
                census.append(response.content.decode("latin-1"))
                break
        else:
            raise SystemExit("No Census city and town estimates found for the last three years.")
    for text in census:
        towns = sorted({town for rows in load("tax")["years"].values() for town in rows})
        if not towns:
            raise SystemExit("Save the tax rates first: population is matched to PVR's town names.")
        years = population(text, towns)
        matched = set(years[max(years)]) if years else set()
        saved = load("population")
        saved["years"].update({str(y): dict(sorted(rows.items())) for y, rows in years.items()})
        save("population", saved, now)
        print(f"Saved figures/population.json: {len(matched)} of {len(towns)} towns matched"
              + (f"; not matched: {', '.join(t for t in towns if t not in matched)}" if len(matched) < len(towns) else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
