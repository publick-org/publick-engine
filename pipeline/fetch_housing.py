"""Collect housing figures for the town.

- New homes permitted each year, from the Census Bureau Building Permits Survey.
- Home value, rent, and rent burden, from the Census Bureau's American Community
  Survey (5-year estimates, with margins of error), through Census Reporter.
- Figures from the town's state, where its package in pipeline/states/ has
  them (Massachusetts: the Subsidized Housing Inventory and parcels by type).

Writes data/housing/housing.json. Each source is fetched separately; if one
fails, its last saved figures are kept. The figures change a few times a year,
so the fetch is skipped when the saved file is less than a week old.

Usage:
    python -m pipeline.fetch_housing [--town gloucester] [--force]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from pipeline import states
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient
from pipeline.rhythms import Part, Rhythm, add_months, latest_year, month_of, month_period, on

REFRESH_DAYS = 7
PERMIT_YEARS = 10
BPS_URL = "https://www2.census.gov/econ/bps/Place"
BPS_PAGE = "https://www.census.gov/construction/bps/"
CENSUS_REPORTER = "https://api.censusreporter.org/1.0/data/show/latest"
ACS_TABLES = ["B25002", "B25003", "B25004", "B25064", "B25070", "B25077"]


# ---- Building permits ----

def _year_to_date(data: dict) -> int | None:
    ytd = (data.get("permits") or {}).get("year_to_date")
    return month_period(ytd["year"], ytd["through_month"]) if ytd else None


def _acs_year(data: dict) -> int | None:
    release = (data.get("acs") or {}).get("release") or ""
    return int(release.split()[1]) if release.startswith("ACS ") else None


def _parcels_year(data: dict) -> int | None:
    return (data.get("parcels") or {}).get("fiscal_year")


# Census posts a month's permits by place the next month and the year's final
# file in May; the ACS 5-year estimates come in December. Massachusetts parcel
# counts come with the fiscal year's tax rates. The Subsidized Housing
# Inventory changes irregularly, so it has no expected date.
HOUSING_PARTS = (
    Part(latest_year("permits.years", "year"), on(6, years_after=1), lambda y: f"{y} building permits"),
    Part(_year_to_date, lambda p: add_months(month_of(p), 2), lambda p: f"{month_of(p):%B %Y} building permits"),
    Part(_acs_year, on(12, 15, years_after=1), lambda y: f"ACS {y} 5-year estimates"),
)
PARCELS_PART = Part(_parcels_year, on(3), lambda y: f"Fiscal year {y} parcel counts")


def rhythm(state_parts: set[str]) -> Rhythm:
    """Housing's rhythm for a town with these state housing parts (pipeline.states)."""
    parts = HOUSING_PARTS + ((PARCELS_PART,) if "parcels" in state_parts else ())
    return Rhythm("Housing figures", "housing/housing.json", "Fetch housing figures", "monthly", parts)


def bps_url(config: dict, name: str) -> str:
    h = config["housing"]
    return f"{BPS_URL}/{quote(h['bps_region'])}/{h['bps_prefix']}{name}.txt"


def bps_town(config: dict) -> tuple[int, str]:
    """Which column of a place file finds the town, and its code there.

    A city is listed by its Census place ([housing] bps_place, column 6). A New
    England town that isn't a Census place, like Wallingford, Connecticut, is
    listed with place 00000 and its town (MCD) code ([housing] bps_mcd, column 7),
    which is unique within its state.
    """
    h = config["housing"]
    if "bps_mcd" in h:
        return 6, h["bps_mcd"]
    if h.get("bps_place", "00000") == "00000":
        raise SystemExit("[housing] needs bps_place (the town's Census place code) or, for a town that "
                         "isn't a Census place, bps_mcd (its town code): place 00000 is every such town.")
    return 5, h["bps_place"]


def parse_bps(text: str, state: str, code: str, column: int = 5) -> dict | None:
    """The town's row of a Building Permits Survey place file: the row in the
    state whose place code (column 5) or town code (column 6) is the town's.

    Columns 17-28 are Census estimates (reported months plus imputed ones), in
    four groups of buildings, units, and value: 1 unit, 2 units, 3-4, and 5+.
    """
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 29 or row[1].strip() != state or row[column].strip() != code:
            continue
        units = [int(row[18]), int(row[21]), int(row[24]), int(row[27])]
        return {
            "units": sum(units),
            "by_size": dict(zip(["1 unit", "2 units", "3-4 units", "5+ units"], units)),
            "months_reported": int(row[15]),
        }
    return None


def permits(client, config: dict, now: datetime) -> dict:
    h = config["housing"]
    column, code = bps_town(config)
    years = []
    for year in range(now.year - 1, now.year - PERMIT_YEARS - 2, -1):
        try:
            found = parse_bps(client.get(bps_url(config, f"{year}a")).text, h["bps_state"], code, column)
        except FetchError as e:
            if e.status == 404 and year == now.year - 1:
                continue  # last year's annual file is published in the spring
            raise
        if found:
            years.append({"year": year, **found, "estimated": found["months_reported"] < 12})
        if len(years) >= PERMIT_YEARS:
            break
    # Year to date: the newest monthly cumulative file for this year.
    ytd = None
    for month in range(now.month - 1, 0, -1):
        try:
            text = client.get(bps_url(config, f"{now.year % 100:02d}{month:02d}y")).text
        except FetchError as e:
            if e.status == 404:
                continue
            raise
        found = parse_bps(text, h["bps_state"], code, column)
        if found:
            ytd = {"year": now.year, "through_month": month, "units": found["units"],
                   "estimated": found["months_reported"] < month}
        break
    if not years:
        raise FetchError("no building permit figures found")
    return {"years": sorted(years, key=lambda y: y["year"]), "year_to_date": ytd}


# ---- American Community Survey ----

def moe_sum(*moes: float) -> float:
    return math.sqrt(sum(m * m for m in moes))


def moe_share(part: float, part_moe: float, whole: float, whole_moe: float) -> float:
    """Margin of error of a proportion (Census Bureau formula), in percentage points."""
    p = part / whole
    inside = part_moe ** 2 - p * p * whole_moe ** 2
    if inside < 0:  # the Bureau's fallback when the proportion formula fails
        inside = part_moe ** 2 + p * p * whole_moe ** 2
    return math.sqrt(inside) / whole * 100


def acs_place(tables: dict) -> dict:
    def est(table, cell):
        return tables[table]["estimate"][f"{table}{cell:03d}"]

    def err(table, cell):
        return tables[table]["error"][f"{table}{cell:03d}"]

    # Rent burden: B25070 cells 7-10 are 30% or more of income; 11 is "not computed".
    renters = est("B25070", 1) - est("B25070", 11)
    renters_moe = moe_sum(err("B25070", 1), err("B25070", 11))
    over_30 = sum(est("B25070", c) for c in range(7, 11))
    over_30_moe = moe_sum(*(err("B25070", c) for c in range(7, 11)))
    over_50 = est("B25070", 10)
    return {
        "median_home_value": {"value": round(est("B25077", 1)), "moe": round(err("B25077", 1))},
        "median_rent": {"value": round(est("B25064", 1)), "moe": round(err("B25064", 1))},
        "rent_30_plus": {"value": round(over_30 / renters * 100, 1),
                         "moe": round(moe_share(over_30, over_30_moe, renters, renters_moe), 1)},
        "rent_50_plus": {"value": round(over_50 / renters * 100, 1),
                         "moe": round(moe_share(over_50, err("B25070", 10), renters, renters_moe), 1)},
        "homes": round(est("B25002", 1)),
        "owner_occupied": round(est("B25003", 2)),
        "renter_occupied": round(est("B25003", 3)),
        "vacant": round(est("B25002", 3)),
        "seasonal": round(est("B25004", 6)),
    }


def acs(client, config: dict) -> dict:
    h = config["housing"]
    url = CENSUS_REPORTER + "?" + urlencode({"table_ids": ",".join(ACS_TABLES),
                                             "geo_ids": f"{h['census_geo']},{h['state_geo']}"})
    data = client.get(url).json()
    return {
        "release": data["release"]["name"],
        "years": data["release"]["years"],
        "town": acs_place(data["data"][h["census_geo"]]),
        "state": acs_place(data["data"][h["state_geo"]]),
        "source_url": f"https://censusreporter.org/profiles/{h['census_geo']}/",
    }


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False,
        state_client=None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    path = data_dir / "housing" / "housing.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if saved and not force and now - datetime.fromisoformat(saved["updated_at"]) < timedelta(days=REFRESH_DAYS):
        return {"skipped": f"updated less than {REFRESH_DAYS} days ago"}
    data = {"updated_at": now.isoformat(timespec="seconds"), "bps_page": BPS_PAGE}
    problems = []
    sources = [("permits", lambda: permits(client, config, now)), ("acs", lambda: acs(client, config))]
    # The state's own figures, if it has any.
    state = states.for_town(config).housing_module()
    if state:
        sources += state.sources(config, client, state_client, now)
        for key in state.keys:
            data[key] = None
    for key, fetch in sources:
        try:
            data[key] = fetch()
        except (FetchError, KeyError, ValueError) as e:
            problems.append(f"{key}: {e}")
            data[key] = saved.get(key)
    if not any(data[k] for k, _ in sources):
        raise FetchError("; ".join(problems))
    save_json(path, data)
    return {"problems": problems, "requests": getattr(client, "request_count", None)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="fetch even if the saved file is recent")
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "housing"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=2.0, timeout=60.0)
    state = states.for_town(config).housing_module()
    try:
        result = run(config, client, args.data, force=args.force, state_client=state.client(config) if state else None)
    except FetchError as e:
        print(f"::error::Housing figures could not be fetched: {e}")
        return 1
    for problem in result.get("problems", []):
        print(f"::warning::Housing: kept the last saved figures for {problem}")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
