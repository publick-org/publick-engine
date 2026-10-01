"""New Hampshire's average single-family tax bill, calculated by Publick.

New Hampshire doesn't publish an average bill, so Publick calculates one for
the newest tax year the DRA has rates for: the average assessed value of the
town's single-family homes (land use code 11, with or without a modifier such
as waterfront), from NH GRANIT's statewide parcel map, times that year's total
tax rate (dollars per $1,000 of assessed value). Exemptions and credits, such
as the veterans' credit, aren't taken off, so it's the bill before them, as
Massachusetts's average bill is.

The parcel map shows each town's current assessed values. They belong to the
DRA's newest tax year only until the town revalues: then the map has the new
values before the DRA has the new year's rate. So the figure is calculated
only while the town's parcel values add up to about the DRA's valuation for
that year (VALUE_CHECK), and otherwise the last one saved is kept. Each tax
year's figure is saved as it's calculated, so the history grows a year at a
time. Writes data/finance/tax_bill.json. Run by python -m pipeline.fetch_finance.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient
from pipeline.i18n import N_, _
from pipeline.states.nh import figures
from pipeline.rhythms import Part, Rhythm, latest_year, on

PARCELS = ("https://services9.arcgis.com/wnvDDrXX8EouLkZP/arcgis/rest/services/"
           "NH_Parcels_BEA_Public_View/FeatureServer/0/query")
PARCELS_PAGE = "https://www.arcgis.com/home/item.html?id=d2ffd679964f4212a4d4d89c5bd5cb20"
SINGLE_FAMILY = "(SLU = '11' OR SLU LIKE '11-%')"
# The town's parcel values, all of them, over the DRA's valuation for the tax year. Parcels include
# property exempt from tax, which the valuation leaves out, so the ratio is a little over 1 (Manchester's
# is 1.12 for 2025). A revaluation not yet in the DRA's figures puts it far outside.
VALUE_CHECK = (0.9, 1.3)


# The DRA sets each town's tax rate in the fall and finishes the year's list in
# January; the rates reach the engine's figures when they're extracted (extract.py).
RHYTHM = Rhythm(N_("Tax bill (calculated)"), "finance/tax_bill.json", "Fetch tax bill", "yearly", (
    Part(latest_year("years", "tax_year"), on(2, years_after=1), lambda y: _("Tax year {year}").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=60)


def parcel_stats(client, town: str, where: str) -> dict:
    """Count, total, and average assessed value of the town's parcels matching where."""
    stats = [{"statisticType": t, "onStatisticField": f, "outStatisticFieldName": t}
             for t, f in (("count", "OBJECTID"), ("sum", "TaxTotal"), ("avg", "TaxTotal"))]
    town_sql = town.replace("'", "''")
    url = PARCELS + "?" + urlencode({"where": f"Town = '{town_sql}' AND {where}", "outStatistics": json.dumps(stats),
                                     "f": "json"})
    data = client.get(url).json()
    if "error" in data:
        raise FetchError(f"parcel map: {data['error'].get('message')}")
    found = data["features"][0]["attributes"] if data.get("features") else {}
    if not found.get("count"):
        raise FetchError(f"no parcels for {town!r} on the parcel map")
    return {"count": found["count"], "total": found["sum"], "average": found["avg"]}


def method(tax_year: int, parcels: int) -> str:
    return (f"The average assessed value of {parcels:,} single-family homes on NH GRANIT's parcel map, "
            f"times the {tax_year} tax rate. It's the bill before exemptions and credits.")


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    fin = config["finance"]
    name = fin["dra_municipality"]
    tax_year, dra = figures.rows("tax", name)[-1]
    path = data_dir / "finance" / "tax_bill.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    years = {y["tax_year"]: y for y in saved.get("years", [])}

    town = fin.get("parcels_town", name)
    everything = parcel_stats(client, town, "1=1")
    homes = parcel_stats(client, town, SINGLE_FAMILY)
    ratio = everything["total"] / dra["valuation"]
    result = {"tax_year": tax_year, "value_ratio": round(ratio, 3)}
    if VALUE_CHECK[0] <= ratio <= VALUE_CHECK[1]:
        years[tax_year] = {
            "tax_year": tax_year, "period": f"Tax year {tax_year}",
            "average_bill": round(homes["average"] * dra["total"] / 1000),
            "average_value": round(homes["average"]), "parcels": homes["count"], "rate": dra["total"],
            "calculated": method(tax_year, homes["count"]),
        }
        result["average_bill"] = years[tax_year]["average_bill"]
    else:
        # The parcel map's values aren't the DRA's newest year's (a revaluation, most often): keep what's saved.
        print(f"::warning::{name}'s parcel values add up to {ratio:.2f} times the DRA's {tax_year} valuation, "
              f"outside {VALUE_CHECK[0]}–{VALUE_CHECK[1]}: the town may have revalued. Kept the saved tax bill; "
              "the next year's DRA figures (pipeline/states/nh/extract.py) will bring it up to date.")
        result["kept"] = sorted(years)
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("tax"),
        "source": "Calculated by Publick from the New Hampshire Department of Revenue Administration's tax rates "
                  "and NH GRANIT's parcel map",
        "source_url": figures.load("tax")["source_url"],
        "parcels_url": PARCELS_PAGE,
        "years": [years[y] for y in sorted(years)],
    }
    save_json(path, data)
    return result
