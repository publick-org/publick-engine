"""Vermont budget figures for the budget page, from the Department of Taxes'
Property Valuation and Review (PVR) figures (figures/, from the yearly extract):

- the tax rates by tax year: homestead education, nonhomestead education, and
  municipal (with the local agreement rate). Published; each with the median of
  Vermont's towns, calculated by Publick;
- what the property tax raised, by part: the education taxes, which go to the
  state's Education Fund and pay for every town's schools, and the town's own
  municipal tax. Published;
- the grand list: the town's listed value, its common level of appraisal (CLA,
  how listed values compare with sale prices), and its equalized value.
  Published;
- property tax per resident, with the median of the state's towns: each town's
  total property tax over its Census population estimate for the same year.
  Calculated by Publick.

Vermont names a tax year by its grand list of April 1: tax year 2025's taxes pay
for the fiscal year from July 2025 to June 2026. Writes data/finance/budget.json.
Run by python -m pipeline.fetch_budget.
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
from pipeline.states.vt import figures

RATES = ("homestead_rate", "nonhomestead_rate", "municipal_rate", "local_agreement_rate")
# What the property tax raised, by part. Saved in English and translated where shown (the build's data_label).
TAXES = {"homestead_taxes": N_("Education tax on homesteads"), "nonhomestead_taxes": N_("Education tax on other property"),
         "municipal_taxes": N_("City tax"), "local_agreement_taxes": N_("Local agreement tax")}


# PVR posts a tax year's figures in its Annual Report's data the January after.
RHYTHM = Rhythm(N_("City budget"), "finance/budget.json", "Fetch budget figures", "yearly", (
    Part(latest_year("rates", "tax_year"), on(3, years_after=1), lambda y: _("Tax year {year} tax rates").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    """Nothing is fetched: the figures are in the engine."""
    return PoliteClient(config["site"]["user_agent"])


def medians(year: int) -> dict:
    """The median town's rate of each kind, for the year. A town with no homesteads (a gore) has no homestead
    rate of its own that anyone pays, so only rates above zero count."""
    rows = figures.load("tax")["years"].get(str(year), {}).values()
    return {k: round(statistics.median(v), 4) if (v := [r[k] for r in rows if r.get(k)]) else None
            for k in ("homestead_rate", "nonhomestead_rate", "municipal_rate")}


def total_taxes(row: dict) -> float:
    return sum(row.get(k) or 0 for k in TAXES)


def per_resident(tax_year: int, name: str) -> dict | None:
    """Property tax per resident for the town, with the median of the state's towns, for tax_year."""
    tax = figures.load("tax")["years"].get(str(tax_year), {})
    people = figures.load("population")["years"].get(str(tax_year), {})
    values = {town: total_taxes(row) / people[town] for town, row in tax.items() if total_taxes(row) and people.get(town)}
    if name not in values:
        return None
    return {
        "tax_year": tax_year, "town": round(values[name]), "population": people[name],
        "state_median": round(statistics.median(values.values())), "communities": len(values),
        "calculated": (f"Each town's total {tax_year} property tax (education, municipal, and local agreement taxes), "
                       f"from PVR, divided by its Census population estimate for July 1, {tax_year}. The median is of "
                       f"{len(values)} Vermont towns and cities; the Census doesn't count a few villages and unified "
                       "towns PVR lists separately."),
    }


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    name = config["finance"]["pvr_town"]
    rows = figures.rows("tax", name)
    lists = {int(y): r[name] for y, r in figures.load("grand_list")["years"].items() if name in r}
    rates = [{"tax_year": y, **{k: row.get(k) for k in RATES},
              "state_median": medians(y)} for y, row in rows if all(row.get(k) is not None for k in RATES[:3])]
    taxes = [{"tax_year": y, "parts": {label: round(row[k]) for k, label in TAXES.items() if row.get(k)},
              "total": round(total_taxes(row))} for y, row in rows if total_taxes(row)]
    grand_list = [{"tax_year": y, "listed_value": round(r["municipal_grand_list"] * 100) if r.get("municipal_grand_list") else None,
                   "cla": r.get("cla"), "equalized_value": r.get("equalized_municipal_value"), "parcels": r.get("parcels")}
                  for y, r in sorted(lists.items())]
    resident = next((r for y, _ in reversed(rows) if (r := per_resident(y, name))), None)
    if not rates:
        raise FetchError(f"no tax rates for {name}")
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("tax", "grand_list", "population"),
        "source_urls": {"rates": figures.load("tax")["source_url"], "grand_list": figures.load("grand_list")["source_url"],
                        "population": figures.load("population")["source_url"]},
        "rates": rates,
        "rates_median_calculated": ("The middle of each kind of rate among the Vermont towns in PVR's table that "
                                    "year; a town with no homesteads (an unorganized town or gore) has no homestead "
                                    "rate that counts."),
        "taxes": taxes,
        "grand_list": grand_list,
        "per_resident": resident,
    }
    save_json(data_dir / "finance" / "budget.json", data)
    return {"rates": rates[-1]["tax_year"], "per_resident": resident and resident["town"]}
