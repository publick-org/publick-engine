"""Connecticut budget figures for the budget page, from the Office of Policy and
Management's (OPM) statewide datasets on data.ct.gov. All as published, except
where marked:

- the town's adopted budget, revenue by source and spending in OPM's four
  groups (education, debt service, contingency, everything else), from the
  Fiscal Health Monitoring System, where every town files its adopted budget;
- the mill rate by fiscal year, with the median of Connecticut's 169 towns
  (from fiscal year 2021, when OPM's file began listing every town's rate in
  one column; the median is calculated by Publick);
- the tax levy (real estate, personal property, motor vehicles) and the net
  grand list (the taxable value of the town's property, by kind);
- property tax per resident, with the median of the state's towns: each town's
  adjusted tax levy over its population (the Department of Public Health's
  estimate), from the Municipal Fiscal Indicators, OPM's figures from each
  town's audit, a little over two years behind. Calculated by Publick.

Fiscal years start July 1, and a fiscal year's taxes are on the grand list of
the October 1 a year and nine months before: fiscal year 2027's on the October
2025 list. Writes data/finance/budget.json. Run by python -m pipeline.fetch_budget.
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
from pipeline.states.ct import opendata
from pipeline.states.ct.opendata import number, quoted
from pipeline.states.ct.tax_bill import mill_rates

YEARS = 10
# A year's median is shown only once nearly every town has a rate in the column read.
MEDIAN_TOWNS = 150
# The adopted budget's revenue and spending, as OPM's columns, with plain names. They're saved in English
# and translated where they're shown (the build's data_label).
REVENUE = {"property_tax_revenue": N_("Property tax"), "revenues_from_state_of_ct": N_("State aid"),
           "revenues_from_federal": N_("Federal aid"), "use_of_fund_balance": N_("Savings (fund balance)"),
           "all_other_revenue": N_("Other")}
SPENDING = {"education_expenditures": N_("Education"), "debt_service": N_("Debt service"),
            "contingency_amount": N_("Contingency"), "all_other_expenditures": N_("Everything else")}
LEVY = {"net_real_property_tax_levy": "real_property", "net_personal_prop_tax_levy": "personal_property",
        "net_motor_vehicle_tax_levy": "motor_vehicles", "total_tax_levy": "total"}
GRAND_LIST = {"residential": "residential", "apartment": "apartments", "commercial": "commercial",
              "industrial": "industrial", "net_real_property": "real_property",
              "total_net_motor_vehicle": "motor_vehicles", "net_personal_property": "personal_property",
              "total_net_grand_list": "total"}


# Towns adopt their budgets in the spring and file them with OPM by summer; the
# mill rates reach OPM's file by September. The audited indicators follow the
# fiscal year by about two and a half years.
RHYTHM = Rhythm(N_("City budget"), "finance/budget.json", "Fetch budget figures", "yearly", (
    Part(latest_year("adopted", "fiscal_year"), on(10, years_after=-1),
         lambda y: _("Fiscal year {year} adopted budget").format(year=y)),
    Part(latest_year("rates", "fiscal_year"), on(10, years_after=-1),
         lambda y: _("Fiscal year {year} mill rate").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=90)


def rates(client, town: str, code: str) -> list[dict]:
    """The town's mill rate by fiscal year, with the state's median town's."""
    town_rates = mill_rates(client, town, code)
    rows = opendata.query(client, opendata.MILL_RATES,
                          select="fiscal_year, median(mill_rate_real_personal) AS median, count(*) AS towns",
                          where="municipality NOT LIKE '% - %' AND mill_rate_real_personal >= 1", group="fiscal_year")
    medians = {int(r["fiscal_year"]): (number(r["median"]), int(number(r["towns"]))) for r in rows
               if number(r.get("towns")) and number(r["towns"]) >= MEDIAN_TOWNS}
    return [{"fiscal_year": y, "rate": rate, "state_median": medians.get(y, (None, None))[0],
             "towns": medians.get(y, (None, None))[1]} for y, rate in sorted(town_rates.items())][-YEARS:]


def levy(client, code: str) -> list[dict]:
    rows = opendata.query(client, opendata.TAX_LEVY, where=f"town_code={quoted(code)}")
    out = [{"fiscal_year": int(r["fiscal_year"]), **{key: round(number(r.get(col)) or 0) for col, key in LEVY.items()}}
           for r in rows if " - " not in r.get("municipality_district", "") and number(r.get("total_tax_levy"))]
    return sorted(out, key=lambda y: y["fiscal_year"])[-YEARS:]


def grand_list(client, code: str) -> list[dict]:
    rows = opendata.query(client, opendata.GRAND_LIST, where=f"town_code={int(code)}")
    out = [{"grand_list_year": int(r["year"]), **{key: round(number(r.get(col)) or 0) for col, key in GRAND_LIST.items()}}
           for r in rows if number(r.get("total_net_grand_list"))]
    return sorted(out, key=lambda y: y["grand_list_year"])[-YEARS:]


def adopted(client, town: str) -> list[dict]:
    """The town's adopted budgets, as filed with OPM. The system names towns in capitals, without a code."""
    rows = opendata.query(client, opendata.ADOPTED_BUDGETS, where=f"entity_name={quoted(town.upper())}")
    out = []
    for r in rows:
        total = number(r.get("total_expenditures"))
        if not total:
            continue
        out.append({
            "fiscal_year": int(r["fiscal_period_of_budget"]), "total": round(total),
            "revenue": {label: round(number(r.get(col)) or 0) for col, label in REVENUE.items()},
            "spending": {label: round(number(r.get(col)) or 0) for col, label in SPENDING.items()},
            "adopted_on": (r.get("date_budget_adopted") or "")[:10] or None,
        })
    return sorted(out, key=lambda y: y["fiscal_year"])[-YEARS:]


def per_resident(client, code: str) -> dict | None:
    """Property tax per resident, with the median of the state's towns, for the newest audited fiscal year."""
    newest = opendata.query(client, opendata.FISCAL_INDICATORS, select="max(fiscal_year_end) AS year")
    if not newest or not newest[0].get("year"):
        return None
    year = int(newest[0]["year"])
    rows = opendata.query(client, opendata.FISCAL_INDICATORS,
                          select="town, tax_code, population_state_dept_of_public_health, current_year_adjusted_tax",
                          where=f"fiscal_year_end={quoted(year)}")
    # The City of Groton, inside the town of Groton, has a row of its own but no code or population.
    values = {}
    for r in rows:
        people, tax = number(r.get("population_state_dept_of_public_health")), number(r.get("current_year_adjusted_tax"))
        if r.get("tax_code") and people and tax:
            values[str(int(number(r["tax_code"])))] = (tax / people, people)
    key = str(int(code))
    if key not in values:
        return None
    return {
        "fiscal_year": year, "town": round(values[key][0]), "population": round(values[key][1]),
        "state_median": round(statistics.median(v for v, _ in values.values())), "communities": len(values),
        "calculated": (f"Each town's adjusted tax levy for fiscal year {year}, from its audit as reported to OPM, "
                       f"divided by its population, the Department of Public Health's estimate. The median is of "
                       f"{len(values)} Connecticut towns."),
    }


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    fin = config["finance"]
    town, code = fin["opm_town"], str(fin["opm_code"])
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "source": "Connecticut Office of Policy and Management, on data.ct.gov",
        "source_urls": {"adopted": opendata.page(opendata.ADOPTED_BUDGETS), "rates": opendata.page(opendata.MILL_RATES),
                        "levy": opendata.page(opendata.TAX_LEVY), "grand_list": opendata.page(opendata.GRAND_LIST),
                        "per_resident": opendata.page(opendata.FISCAL_INDICATORS)},
        "adopted": adopted(client, town),
        "rates": rates(client, town, code),
        "rates_median_calculated": ("The middle of the mill rates of the Connecticut towns with a rate that fiscal "
                                    "year, as OPM publishes them."),
        "levy": levy(client, code),
        "grand_list": grand_list(client, code),
        "per_resident": per_resident(client, code),
    }
    if not data["rates"]:
        raise FetchError(f"no mill rates for {town} (OPM code {code})")
    save_json(data_dir / "finance" / "budget.json", data)
    return {"rates": data["rates"][-1]["fiscal_year"],
            "adopted": data["adopted"][-1]["fiscal_year"] if data["adopted"] else None,
            "per_resident": data["per_resident"] and data["per_resident"]["town"]}
