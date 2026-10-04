"""Maine budget figures for the budget page, from Maine Revenue Services'
Municipal Valuation Return (MVR) Statistical Summary (figures/tax.json, from
the yearly extract) and the Census Bureau's population estimates
(figures/population.json):

- the tax rate by tax year, in dollars per $1,000 of assessed value, as
  published, with the median of Maine's municipalities that year, calculated
  by Publick from every municipality's published rate;
- the commitment (the property tax the town raised), its total taxable
  valuation, and its certified ratio (its assessed values as a percentage of
  market value), by tax year, as published;
- property tax per resident, with the median of Maine's municipalities: each
  one's commitment over its Census population estimate for July 1 of the same
  year. Calculated by Publick.

A tax year's rate is set on the property as it stood on April 1; the town's
fiscal year usually runs from July 1 of that year, but some towns' follow the
calendar year. MRS publishes each tax year's summary in the November or
December after the next. Writes data/finance/budget.json. Run by
python -m pipeline.fetch_budget.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient
from pipeline.i18n import N_, _
from pipeline.rhythms import Part, Rhythm, latest_year, on
from pipeline.states.me import figures
from pipeline.states.me.extract import STATE_TOTAL
from pipeline.states.me.tax_bill import mills

YEARS = 10

# As for the tax bill (tax_bill.py): a tax year's figures come out in the November or December after the next.
RHYTHM = Rhythm(N_("Tax rate and property tax"), "finance/budget.json", "Fetch budget figures", "yearly", (
    Part(latest_year("rates", "tax_year"), on(12, years_after=1), lambda y: _("Tax year {year} tax rates").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    """Nothing is fetched: the figures are in the engine."""
    return PoliteClient(config["site"]["user_agent"])


def municipalities(rows: dict) -> dict:
    """The year's municipalities, without the state's total."""
    return {name: row for name, row in rows.items() if name != STATE_TOTAL}


def median_rate(tax_year: int) -> tuple[float | None, int]:
    """(The median municipality's tax rate, how many municipalities) for the tax year. A municipality with no
    rate (a plantation that raised nothing that year) isn't counted."""
    rows = municipalities(figures.load("tax")["years"].get(str(tax_year), {}))
    rates = [mills(r["rate"]) for r in rows.values() if r.get("rate")]
    return (round(statistics.median(rates), 3), len(rates)) if rates else (None, 0)


def per_resident(tax_year: int, name: str) -> dict | None:
    """Property tax per resident for the town, with the median of Maine's municipalities, for tax_year."""
    tax = municipalities(figures.load("tax")["years"].get(str(tax_year), {}))
    people = figures.load("population")["years"].get(str(tax_year), {})
    values = {town: row["commitment"] / people[town] for town, row in tax.items()
              if row.get("commitment") and people.get(town)}
    if name not in values:
        return None
    return {
        "tax_year": tax_year, "town": round(values[name]), "population": people[name],
        "state_median": round(statistics.median(values.values())), "communities": len(values),
        "calculated": (f"Each municipality's {tax_year} property tax commitment, from Maine Revenue Services' "
                       f"Municipal Valuation Return, divided by its Census population estimate for July 1, {tax_year}. "
                       f"The median is of {len(values)} Maine towns, cities, and plantations."),
    }


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    name = figures.municipality(config)
    rows = figures.rows("tax", name)
    rates = []
    for year, row in rows[-YEARS:]:
        if not row.get("rate"):
            continue
        median, count = median_rate(year)
        rates.append({"tax_year": year, "rate": mills(row["rate"]), "state_median": median, "municipalities": count,
                      "certified_ratio": row.get("ratio"), "commitment": row.get("commitment"),
                      "valuation": row.get("valuation"), "land_buildings": row.get("land_buildings")})
    if not rates:
        raise FetchError(f"no tax rates for {name}")
    resident = next((r for year, _row in reversed(rows) if (r := per_resident(year, name))), None)
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("tax", "population"),
        "source_urls": {"rates": figures.load("tax")["source_url"],
                        "population": figures.load("population")["source_url"]},
        "rates": rates,
        "rates_median_calculated": (f"The middle of the tax rates of the {rates[-1]['municipalities']} Maine "
                                    "municipalities with a rate that year, as Maine Revenue Services publishes them."),
        "per_resident": resident,
    }
    save_json(data_dir / "finance" / "budget.json", data)
    return {"rates": rates[-1]["tax_year"], "rate": rates[-1]["rate"], "per_resident": resident and resident["town"]}
