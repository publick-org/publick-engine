"""Rhode Island budget figures for the budget page, from the Division of
Municipal Finance's (DMF) statewide tables (figures/, from the yearly extract).
As published, except where marked:

- the tax rate by fiscal year for each class of property: residential real
  estate, commercial real estate, personal property (business equipment), and
  motor vehicles, in dollars per $1,000 of assessed value, and whether the
  town revalued that year (the DMF's note);
- the median of the 39 cities and towns' residential rates each year.
  Calculated by Publick;
- the tax levy (what the property tax raised) and the net assessed value
  (the taxable value of the town's property, after exemptions), by class;
- property tax per resident, with the median of the 39: each one's total levy
  over its Census population estimate for July 1 of the year its fiscal year
  begins. Calculated by Publick.

Years are fiscal years, as the DMF names them: fiscal year 2026 (July 2025 to
June 2026, for most towns) taxes property as assessed on December 31, 2024.
The DMF posts a year's rates and values in November or December, after it
begins, and the levy soon after. Writes data/finance/budget.json. Run by
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
from pipeline.states.ri import figures
from pipeline.states.ri.extract import MUNICIPALITIES, STATE

YEARS = 12

# The DMF's files for fiscal year 2026 were posted in November and December 2025;
# fiscal year 2025's rates in December 2024. They reach the engine's figures when
# they're extracted (extract.py).
RHYTHM = Rhythm(N_("Tax rates and levy"), "finance/budget.json", "Fetch budget figures", "yearly", (
    Part(latest_year("rates", "fiscal_year"), on(12, years_after=-1),
         lambda y: _("Fiscal year {year} tax rates").format(year=y)),
    Part(latest_year("levy", "fiscal_year"), on(1), lambda y: _("Fiscal year {year} tax levy").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    """Nothing is fetched: the figures are in the engine."""
    return PoliteClient(config["site"]["user_agent"])


def rates(name: str) -> list[dict]:
    """The town's rates by fiscal year, with the median of the state's residential rates."""
    years = figures.load("rates")["years"]
    out = []
    for year, row in figures.rows("rates", name):
        residential = [r["residential"] for town, r in years[str(year)].items()
                       if town in MUNICIPALITIES and r.get("residential") is not None]
        out.append({"fiscal_year": year, **{k: row.get(k) for k in ("residential", "commercial", "personal_property",
                                                                    "motor_vehicles")},
                    "revalued": bool(row.get("revalued")),
                    "state_median": round(statistics.median(residential), 2) if residential else None,
                    "communities": len(residential)})
    return out[-YEARS:]


def by_class(kind: str, name: str) -> list[dict]:
    """The town's levy or assessed value by class, by fiscal year, with the state's total."""
    years = figures.load(kind)["years"]
    return [{"fiscal_year": year, **row, "state_total": (years[str(year)].get(STATE) or {}).get("total")}
            for year, row in figures.rows(kind, name)][-YEARS:]


def per_resident(fiscal_year: int, name: str) -> dict | None:
    """Property tax per resident for the town, with the median of the state's 39, for one fiscal year."""
    levy = figures.load("levy")["years"].get(str(fiscal_year), {})
    people = figures.load("population")["years"].get(str(fiscal_year - 1), {})
    values = {town: row["total"] / people[town] for town, row in levy.items()
              if town in MUNICIPALITIES and row.get("total") and people.get(town)}
    if name not in values:
        return None
    return {
        "fiscal_year": fiscal_year, "town": round(values[name]), "population": people[name],
        "state_median": round(statistics.median(values.values())), "communities": len(values),
        "calculated": (f"Each city's and town's total property tax levy for fiscal year {fiscal_year}, from the "
                       f"Division of Municipal Finance, divided by its Census population estimate for July 1, "
                       f"{fiscal_year - 1}, when the fiscal year began. The median is of {len(values)} Rhode Island "
                       "cities and towns."),
    }


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    name = config["finance"]["dmf_municipality"]
    rate_rows = rates(name)
    if not rate_rows:
        raise FetchError(f"no tax rates for {name}")
    levy = by_class("levy", name)
    resident = next((r for y in reversed(levy) if (r := per_resident(y["fiscal_year"], name))), None)
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("rates", "assessed", "levy", "population"),
        "source": "Rhode Island Division of Municipal Finance",
        "source_urls": {kind: figures.load(kind)["source_url"] for kind in ("rates", "assessed", "levy", "population")},
        "rates": rate_rows,
        "rates_median_calculated": ("The middle of the residential rates of the Rhode Island cities and towns in "
                                    "each year's table, as the Division of Municipal Finance publishes them."),
        "levy": levy,
        "assessed": by_class("assessed", name),
        "per_resident": resident,
    }
    save_json(data_dir / "finance" / "budget.json", data)
    return {"rates": rate_rows[-1]["fiscal_year"], "levy": levy[-1]["fiscal_year"] if levy else None,
            "per_resident": resident and resident["town"]}
