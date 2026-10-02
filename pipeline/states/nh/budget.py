"""New Hampshire budget figures for the budget page.

- The tax rate by year and its four parts (city, county, state education, local
  education), and what each part raises, from the DRA's statewide figures
  (figures/tax.json, from the yearly extract). Published.
- Property tax per resident, with the median of New Hampshire's towns and
  cities: each one's total tax commitment over its Census population estimate
  for the same year. Calculated by Publick.
- The city's adopted budget by function, from a table on the city's website,
  when [finance] has budget_table_url: a table with a column per fiscal year
  ("FY 2027"), a row per function, then a "Total" row and any after it. Years
  that drop off the city's page are kept.

Writes data/finance/budget.json. Run by python -m pipeline.fetch_budget.
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient
from pipeline.i18n import N_, _
from pipeline.states.nh import figures
from pipeline.rhythms import Part, Rhythm, latest_year, on

RATE_FIELDS = ("set_on", "municipal", "county", "state_education", "local_education", "total", "valuation", "commitment")
# What the property tax raised, by part, as the DRA names them.
EFFORT_FIELDS = {"town_tax_effort": "City", "local_school_tax_effort": "Local schools",
                 "state_education_tax_effort": "State education tax", "county_tax_effort": "County",
                 "village_tax_effort": "Village districts"}


# The city adopts a fiscal year's budget in June, before the year starts July 1.
# Tax rates as for the tax bill (tax_bill.py).
RHYTHM = Rhythm(N_("City budget"), "finance/budget.json", "Fetch budget figures", "yearly", (
    Part(latest_year("city_budget.years", "fiscal_year"), on(7, years_after=-1),
         lambda y: _("Fiscal year {year} city budget").format(year=y)),
    Part(latest_year("rates", "tax_year"), on(2, years_after=1), lambda y: _("Tax year {year} tax rates").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=60)


# ---- The city's budget table ----

class Tables(HTMLParser):
    """Every table on a page, as rows of cell text."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.tables and self.tables[-1]:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.tables[-1][-1].append(re.sub(r"\s+", " ", unescape("".join(self.cell))).strip())
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def amount(text: str) -> int | None:
    """'$1,234' -> 1234; '($191,759,220)' -> -191759220."""
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    return -int(digits) if text.strip().startswith("(") else int(digits)


def parse_budget_table(html: str) -> list[dict]:
    """[{fiscal_year, total, lines: {function: amount}, summary: [{label, amount}]}], oldest first.

    summary is the rows from "Total" on, in the city's order (a list, since saved files sort keys)."""
    parser = Tables()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        years = {j: int(m.group(1)) for j, cell in enumerate(table[0]) if (m := re.fullmatch(r"FY\s*(\d{4})", cell))}
        if len(years) < 2:
            continue
        out = {y: {"fiscal_year": y, "total": None, "lines": {}, "summary": []} for y in years.values()}
        summary = False
        for row in table[1:]:
            if not row or not row[0]:
                continue
            # A trailing * points to a footnote on the city's page, which isn't copied.
            label = row[0].rstrip(" *")
            if label.lower().startswith("total"):
                summary = True
            for j, year in years.items():
                value = amount(row[j]) if j < len(row) else None
                if value is None:
                    continue
                if summary:
                    out[year]["summary"].append({"label": label, "amount": value})
                else:
                    out[year]["lines"][label] = value
                if label.lower().startswith("total") and out[year]["total"] is None:
                    out[year]["total"] = value
        found = [out[y] for y in sorted(out) if out[y]["lines"]]
        if found:
            return found
    raise FetchError("no budget table with fiscal year columns on the city's page")


# ---- Figures ----

def per_resident(tax_year: int, name: str) -> dict | None:
    """Property tax per resident for the town, with the median of the state's towns and cities, for tax_year."""
    tax = figures.load("tax")["years"].get(str(tax_year), {})
    people = figures.load("population")["years"].get(str(tax_year), {})
    values = {town: row["commitment"] / people[town] for town, row in tax.items()
              if row.get("commitment") and people.get(town)}
    if name not in values:
        return None
    return {
        "tax_year": tax_year, "town": round(values[name]), "population": people[name],
        "state_median": round(statistics.median(values.values())), "communities": len(values),
        "calculated": (f"Each town's total {tax_year} property tax commitment, from the DRA, divided by its Census "
                       f"population estimate for July 1, {tax_year}. The median is of {len(values)} New Hampshire "
                       "towns and cities."),
    }


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    fin = config["finance"]
    name = fin["dra_municipality"]
    path = data_dir / "finance" / "budget.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    rows = figures.rows("tax", name)
    rates = [{"tax_year": y, **{f: row.get(f) for f in RATE_FIELDS}} for y, row in rows if row.get("total")]
    effort_year, effort_row = next(((y, r) for y, r in reversed(rows) if r.get("total_tax_effort")), (None, None))
    effort = None
    if effort_row:
        effort = {"tax_year": effort_year, "parts": {label: effort_row[f] for f, label in EFFORT_FIELDS.items()
                                                     if effort_row.get(f)},
                  "veterans_credits": effort_row.get("veterans_credits"), "commitment": effort_row.get("commitment")}
    resident = next((r for y, _ in reversed(rows) if (r := per_resident(y, name))), None)

    city_budget = None
    if fin.get("budget_table_url"):
        found = parse_budget_table(client.get(fin["budget_table_url"]).text)
        kept = {y["fiscal_year"]: y for y in (saved.get("city_budget") or {}).get("years", [])}
        kept.update({y["fiscal_year"]: y for y in found})
        city_budget = {"source_url": fin["budget_table_url"],
                       "source_name": fin.get("budget_table_name", "the city's website"),
                       "years": [kept[y] for y in sorted(kept)]}

    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("tax", "population"),
        "source_urls": {"rates": figures.load("tax")["source_url"],
                        "population": figures.load("population")["source_url"]},
        "rates": rates,
        "tax_effort": effort,
        "per_resident": resident,
        "city_budget": city_budget,
    }
    if not rates:
        raise FetchError(f"no tax rates for {name}")
    save_json(path, data)
    return {"rates": rates[-1]["tax_year"], "per_resident": resident and resident["town"],
            "city_budget": city_budget and city_budget["years"][-1]["fiscal_year"]}
