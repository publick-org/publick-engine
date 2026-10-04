"""Maine's average single-family tax bill, calculated by Publick.

Maine doesn't publish an average bill, so Publick calculates one, as for New
Hampshire: the average assessed value (land plus buildings) of the town's
single-family homes, from the Maine GeoLibrary's parcel assessment table
(Maine Parcels Organized Towns ADB, on the state's ArcGIS server), times the
town's tax rate for the newest tax year in Maine Revenue Services' Municipal
Valuation Return Statistical Summary (figures/tax.json, from the yearly
extract). Exemptions, such as the homestead exemption, aren't taken off, so
it's the bill before them. The figure is labeled with the MVR's tax year: the
year whose April 1 assessment the rate was set on.

The parcel table is what towns choose to send the GeoLibrary, when they choose
to: it has no assessment year, only about 170 towns have values in it, and some
of those are years old. So the figure is calculated only while the town's
parcels add up to about the MVR's taxable land and buildings for that year
(VALUE_CHECK); otherwise the figure already saved is kept, with a warning. Each
tax year's figure is saved as it's calculated, so the history grows a year at
a time.

Towns code their parcels' land use differently ("101" is a single-family home
in Lewiston, "1010" in many towns, often with codes of their own for waterfront
homes), so the single-family codes are the town's own, in [finance]
single_family_use, read off the town's rows of the table (its LAND_USE and
LAND_USE_D fields). A town whose parcels are all coded "0", with only a
description, can't have its homes picked out this way. Writes
data/finance/tax_bill.json. Run by python -m pipeline.fetch_finance.
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
from pipeline.rhythms import Part, Rhythm, latest_year, on
from pipeline.states.me import figures

PARCELS = ("https://services1.arcgis.com/RbMX0mRVOFNTdLzd/arcgis/rest/services/"
           "Maine_Parcels_Organized_Towns/FeatureServer/9/query")
PARCELS_PAGE = "https://www.arcgis.com/home/item.html?id=346131b710a645ffb624f448a9cba6d4"
# A parcel's assessed value: the table has land and buildings in separate fields.
VALUE = "LAND_VAL + BLDG_VAL"
# The town's parcels, all of them, over the MVR's taxable land and buildings for the tax year. Parcels
# include property exempt from tax, which the valuation leaves out, so the ratio is a little over 1
# (Lewiston's is 1.10 for 2024). Parcels sent before a revaluation, or only some of a town's, put it far outside.
VALUE_CHECK = (0.9, 1.3)


# MRS publishes a tax year's summary in the November or December after the next
# (2024's is dated November 19, 2025); it reaches the engine's figures when it's
# extracted (extract.py).
RHYTHM = Rhythm(N_("Tax bill (calculated)"), "finance/tax_bill.json", "Fetch tax bill", "yearly", (
    Part(latest_year("years", "tax_year"), on(12, years_after=1), lambda y: _("Tax year {year}").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=60)


def quoted(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def parcel_stats(client, geocode: str, uses: tuple[str, ...] | None = None) -> dict:
    """Count, total, and average assessed value of the town's parcels (with uses, only those with a value).

    The service takes a statistic on an expression, so the average is of each parcel's land plus buildings."""
    where = f"GEOCODE = {quoted(geocode)}"
    if uses:
        where += f" AND LAND_USE IN ({', '.join(quoted(u) for u in uses)}) AND {VALUE} > 0"
    stats = [{"statisticType": t, "onStatisticField": f, "outStatisticFieldName": t}
             for t, f in (("count", "OBJECTID"), ("sum", VALUE), ("avg", VALUE))]
    data = client.get(PARCELS + "?" + urlencode({"where": where, "outStatistics": json.dumps(stats), "f": "json"})).json()
    if "error" in data:
        raise FetchError(f"parcel table: {data['error'].get('message')}")
    found = data["features"][0]["attributes"] if data.get("features") else {}
    if not found.get("count") or not found.get("sum"):
        return {"count": found.get("count") or 0, "total": None, "average": None}
    return {"count": found["count"], "total": found["sum"], "average": found["avg"]}


def mills(rate: float) -> float:
    """MRS's rate as dollars per $1,000 of assessed value: 0.03177 -> 31.77."""
    return round(rate * 1000, 3)


def method(tax_year: int, parcels: int, uses: tuple[str, ...]) -> str:
    codes = ", ".join(f'"{u}"' for u in uses)
    return (f"The average assessed value, land and buildings, of {parcels:,} single-family homes (land use "
            f"{codes}) in the Maine GeoLibrary's parcel table, times the {tax_year} tax rate from Maine "
            "Revenue Services. It's the bill before exemptions, such as the homestead exemption.")


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    fin = config["finance"]
    name = figures.municipality(config)
    geocode = str(fin["megis_geocode"])
    uses = tuple(str(u) for u in fin["single_family_use"])
    tax_year, mvr = figures.rows("tax", name)[-1]
    path = data_dir / "finance" / "tax_bill.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() and not force else {}
    years = {y["tax_year"]: y for y in saved.get("years", [])}

    everything = parcel_stats(client, geocode)
    if not everything["total"]:
        raise FetchError(f"no assessed values for GEOCODE {geocode} in the parcel table")
    ratio = everything["total"] / mvr["land_buildings"]
    result = {"tax_year": tax_year, "value_ratio": round(ratio, 3)}
    if VALUE_CHECK[0] <= ratio <= VALUE_CHECK[1]:
        homes = parcel_stats(client, geocode, uses)
        if not homes["count"]:
            raise FetchError(f"no parcels with land use {', '.join(uses)} for GEOCODE {geocode}; set [finance] "
                             "single_family_use to the town's single-family codes")
        rate = mills(mvr["rate"])
        years[tax_year] = {
            "tax_year": tax_year, "period": f"Tax year {tax_year}",
            "average_bill": round(homes["average"] * rate / 1000), "average_value": round(homes["average"]),
            "parcels": homes["count"], "rate": rate, "certified_ratio": mvr.get("ratio"),
            "calculated": method(tax_year, homes["count"], uses),
        }
        result["average_bill"] = years[tax_year]["average_bill"]
    else:
        # The parcel table's values aren't the MVR's newest year's (an old submission, most often).
        print(f"::warning::{name}'s parcels add up to {ratio:.2f} times its {tax_year} taxable land and buildings "
              f"in the MVR, outside {VALUE_CHECK[0]}–{VALUE_CHECK[1]}: the town's values in the parcel table may be "
              "from another year. " + ("Kept the saved tax bill." if years else "No tax bill saved."))
        result["kept"] = sorted(years)
        if not years:
            raise FetchError(f"{name}'s parcel values don't match its {tax_year} MVR valuation; no tax bill yet")
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("tax"),
        "source": "Calculated by Publick from Maine Revenue Services' Municipal Valuation Return Statistical Summary "
                  "and the Maine GeoLibrary's parcel table",
        "source_url": figures.load("tax")["source_url"],
        "parcels_url": PARCELS_PAGE,
        "years": [years[y] for y in sorted(years)],
    }
    save_json(path, data)
    return result
