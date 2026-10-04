"""Save Rhode Island's statewide property tax figures from the state's files, once a year.

Rhode Island's Division of Municipal Finance (DMF), in the Department of
Revenue, publishes three PDF tables a year, one row for each of the state's 39
cities and towns:

  Tax Rates by Class of Property: the rate per $1,000 of assessed value for
    residential real estate (RRE), commercial real estate (COMM), personal
    property (PP), and motor vehicles (MV), with notes (one marks the places
    that revalued that year)
  Net Assessed Value by Class of Property: residential, commercial/industrial,
    tangible (personal property), and motor vehicles, the municipal total, and
    a statewide total
  Tax Levy by Class of Property: what each class's tax raised, in the same
    columns

municipalfinance.ri.gov answers automated requests with a browser challenge,
and a new year's files get names that can't be guessed (they've been
"2025-Tax-Rates-12-31-24 Final.pdf", "Statewide-Tax-Levy-by-Class-of-Property-
12-31-24A.pdf"), so they're downloaded by hand in a browser, about once a year,
when the new year is posted (the rates and values in November or December, the
levy a few weeks later), and this command reads them. Each file is recognized
by its own heading, not its name. It keeps every municipality's figures in
pipeline/states/ri/figures/, and each Rhode Island town's daily run reads its
own rows from there. Years already saved are kept; a year in a new file
replaces the same year saved before.

Years are fiscal years, as the files' own headings name them: a file "as
assessed on December 31, 2024" is tax roll year 2025 and fiscal year 2026
("FY 2026 Rhode Island Tax Rates by Class of Property"), which for most towns
runs from July 1, 2025 to June 30, 2026, and is the year towns budget by. The
oldest files have no "FY" in their heading; their fiscal year is the
assessment year plus two, which every newer heading agrees with.

Download from DMF_PAGE (Data and Analysis), then, from the engine's folder:

    python -m pipeline.states.ri.extract ~/Downloads/*.pdf
    python -m pipeline.states.ri.extract --population    # Census estimates, fetched directly

Commit the changed files in figures/; the next engine release takes them to
every Rhode Island town.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FIGURES_DIR = Path(__file__).resolve().parent / "figures"

DMF_PAGE = "https://municipalfinance.ri.gov/"
POPULATION_PAGE = "https://www.census.gov/data/tables/time-series/demo/popest/2020s-total-cities-and-towns.html"
POPULATION_URL = ("https://www2.census.gov/programs-surveys/popest/datasets/2020-{year}/cities/totals/"
                  "sub-est{year}_44.csv")

# The key for the statewide total row, next to each municipality's.
STATE = "State"

# Rhode Island's 39 cities and towns, as the DMF's files name them (the rates file in capitals).
MUNICIPALITIES = (
    "Barrington", "Bristol", "Burrillville", "Central Falls", "Charlestown", "Coventry", "Cranston", "Cumberland",
    "East Greenwich", "East Providence", "Exeter", "Foster", "Glocester", "Hopkinton", "Jamestown", "Johnston",
    "Lincoln", "Little Compton", "Middletown", "Narragansett", "New Shoreham", "Newport", "North Kingstown",
    "North Providence", "North Smithfield", "Pawtucket", "Portsmouth", "Providence", "Richmond", "Scituate",
    "Smithfield", "South Kingstown", "Tiverton", "Warren", "Warwick", "West Greenwich", "West Warwick", "Westerly",
    "Woonsocket",
)
_BY_UPPER = {name.upper(): name for name in MUNICIPALITIES}

# Each figures file: what it holds, where it's from.
FILES = {
    "rates": {"source": "Rhode Island Division of Municipal Finance, Tax Rates by Class of Property",
              "source_url": DMF_PAGE},
    "assessed": {"source": "Rhode Island Division of Municipal Finance, Net Assessed Value by Class of Property",
                 "source_url": DMF_PAGE},
    "levy": {"source": "Rhode Island Division of Municipal Finance, Tax Levy by Class of Property",
             "source_url": DMF_PAGE},
    "population": {"source": "U.S. Census Bureau, Population Estimates Program (city and town totals)",
                   "source_url": POPULATION_PAGE},
}
# The rates file's columns, and the assessed value and levy files' (in their order), as saved.
RATE_CLASSES = ("residential", "commercial", "personal_property", "motor_vehicles")
VALUE_CLASSES = ("residential", "commercial", "tangible", "motor_vehicles", "total")


def pdf_text(content: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def fiscal_year(text: str, name: str) -> int:
    """The fiscal year a file's figures are for: its assessment date's year plus two, checked against
    the "FY" in its heading when it has one."""
    assessed = re.search(r"December\s+31,\s*(20\d\d)", text[:500])
    if not assessed:
        raise SystemExit(f"{name}: no assessment date (December 31, <year>) in its heading.")
    year = int(assessed.group(1)) + 2
    heading = re.search(r"\bFY\s*(20\d\d)\b", text[:300])
    if heading and int(heading.group(1)) != year:
        raise SystemExit(f"{name}: its heading says FY {heading.group(1)}, but its assessment date is "
                         f"December 31, {year - 2}, which is fiscal year {year}.")
    return year


def municipality(words: list[str]) -> tuple[str | None, int]:
    """The municipality a row starts with, and how many words its name took. A footnote mark stuck
    to the name ("Cumberland1") is dropped."""
    for n in (3, 2, 1):
        if len(words) < n:
            continue
        name = " ".join(words[:n])
        name = re.sub(r"(?<=[A-Za-z])\d+$", "", name).upper()
        if name in _BY_UPPER:
            return _BY_UPPER[name], n
        if name == "STATEWIDE TOTAL":
            return STATE, n
    return None, 0


def money(token: str) -> int:
    return int(token.replace("$", "").replace(",", ""))


def rates(text: str) -> dict[str, dict]:
    """{municipality: {residential, commercial, personal_property, motor_vehicles, revalued}} from a tax
    rates file. A rate the file leaves blank (motor vehicles once their tax ended), or sends to a note,
    is None; revalued is there, true, for a place whose notes include the one for a revaluation or
    statistical update."""
    revaluation = {m.group(1) for m in re.finditer(r"^(\d+)\)\s*Municipality had a revaluation", text, re.M | re.I)}
    out = {}
    for line in text.splitlines():
        words = line.split()
        name, used = municipality(words)
        if name in (None, STATE):
            continue
        # West Warwick's commercial rate was, until fiscal year 2017, "See Note 4": several rates.
        rest = re.sub(r"(?i)\bsee note \d+\b", "-", " ".join(words[used:])).split()
        notes = []
        while rest and re.fullmatch(r"\d+,?(\d+,?)*", rest[0]):
            notes += re.findall(r"\d+", rest.pop(0))
        if not 3 <= len(rest) <= 4 or not all(re.fullmatch(r"\$?\d+\.\d+|-", w) for w in rest):
            print(f"  {name}: row not read, as the file has it: {line.strip()}", file=sys.stderr)
            continue
        values = [None if w == "-" else float(w.replace("$", "")) for w in rest] + [None] * (4 - len(rest))
        record = dict(zip(RATE_CLASSES, values))
        if revaluation & set(notes):
            record["revalued"] = True
        out[name] = record
    return out


def by_class(text: str) -> dict[str, dict]:
    """{municipality or "State": {residential, commercial, tangible, motor_vehicles, total}} from a net
    assessed value or tax levy file. Some years' levy files end each row with the DMF's levy per capita,
    which isn't kept; a lone footnote mark after a name ("East Providence 2") is dropped."""
    columns = len(VALUE_CLASSES) + (1 if re.search(r"Capita", text[:600]) else 0)
    out = {}
    for line in text.splitlines():
        words = line.split()
        name, used = municipality(words)
        if name is None:
            continue
        numbers = words[used:]
        if not all(re.fullmatch(r"\$?[\d,]+", w) for w in numbers) or len(numbers) < columns:
            print(f"  {name}: row not read, as the file has it: {line.strip()}", file=sys.stderr)
            continue
        values = [money(w) for w in numbers[len(numbers) - columns:]][:len(VALUE_CLASSES)]
        out[name] = dict(zip(VALUE_CLASSES, values))
    return out


def population(text: str) -> dict[int, dict]:
    """{year: {municipality: estimate}} from the Census Bureau's city and town totals for Rhode Island
    ('South Kingstown town' -> 'South Kingstown')."""
    out: dict[int, dict] = {}
    for r in csv.DictReader(io.StringIO(text)):
        if r["SUMLEV"] != "061" or not r["NAME"].endswith((" town", " city")):
            continue
        name = r["NAME"].rsplit(" ", 1)[0]
        if name not in MUNICIPALITIES:
            continue
        for key, value in r.items():
            if key.startswith("POPESTIMATE") and key[11:].isdigit():
                out.setdefault(int(key[11:]), {})[name] = int(value)
    return out


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
    """JSON with one line per municipality, so a new year's file changes little else and stays small."""
    def compact(v):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

    head = [f" {compact(k)}: {compact(v)}" for k, v in data.items() if k != "years"]
    years = [f"  {compact(year)}: {{\n" + ",\n".join(f"   {compact(name)}: {compact(row)}" for name, row in rows.items())
             + "\n  }" for year, rows in data["years"].items()]
    return "{\n" + ",\n".join(head + [' "years": {\n' + ",\n".join(years) + "\n }"]) + "\n}\n"


KINDS = (("Tax Rates by Class of Property", "rates", rates),
         ("Net Assessed Value by Class of Property", "assessed", by_class),
         ("Tax Levy by Class of Property", "levy", by_class))


def read(path: Path) -> tuple[str, dict[int, dict]]:
    """(figures file, {year: {name: record}}) for one downloaded file, recognized by its contents."""
    content = path.read_bytes()
    if content.startswith(b"%PDF"):
        text = pdf_text(content)
        heading = re.sub(r"\s+", " ", text[:300])
        for title, kind, parse in KINDS:
            if title.lower() in heading.lower():
                rows = parse(text)
                if len([n for n in rows if n != STATE]) < len(MUNICIPALITIES) - 3:
                    raise SystemExit(f"{path}: only {len(rows)} rows read; the file's layout may have changed.")
                return kind, {fiscal_year(text, path.name): rows}
        raise SystemExit(f"{path}: not a file this command knows (a DMF tax rates, net assessed value, "
                         "or tax levy table). See the list at the top of pipeline/states/ri/extract.py.")
    text = content.decode("utf-8-sig", errors="replace")
    if text.startswith("SUMLEV,"):
        return "population", population(text)
    raise SystemExit(f"{path}: not a file this command knows. See the list at the top of pipeline/states/ri/extract.py.")


def fetch_population(now: datetime) -> str:
    """The newest Census city and town estimates for Rhode Island (census.gov allows scripts)."""
    import requests
    for year in range(now.year, now.year - 3, -1):
        response = requests.get(POPULATION_URL.format(year=year), timeout=60,
                                headers={"User-Agent": "publick.org (+https://publick.org)"})
        if response.ok and response.text.startswith("SUMLEV,"):
            print(f"Census estimates, vintage {year}")
            return response.content.decode("latin-1")
    raise SystemExit("No Census city and town estimates found for the last three years.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="downloaded DMF files (PDF)")
    parser.add_argument("--population", action="store_true", help="also fetch the Census population estimates")
    args = parser.parse_args()
    if not args.files and not args.population:
        parser.error("name the downloaded files, or --population")
    now = datetime.now(timezone.utc)
    found: dict[str, list] = {}
    for path in args.files:
        kind, years = read(path)
        found.setdefault(kind, []).append(years)
        print(f"{path.name}: {kind}, {'' if kind == 'population' else 'fiscal year '}"
              f"{', '.join(str(y) for y in sorted(years))}")
    if args.population:
        found.setdefault("population", []).append(population(fetch_population(now)))
    for kind, batches in found.items():
        saved = load(kind)
        for years in batches:
            for year, rows in years.items():
                saved["years"][str(year)] = dict(sorted(rows.items()))
        save(kind, saved, now)
        print(f"Saved figures/{kind}.json: {', '.join(saved['years'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
