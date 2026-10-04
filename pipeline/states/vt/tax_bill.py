"""Vermont's average homestead tax bill, calculated by Publick.

Vermont doesn't publish an average bill, so Publick calculates one, as for New
Hampshire: the average listed value of the town's homesteads, from VCGI's
statewide standardized parcel data, taxed at the town's rates for the tax year
of that grand list, from the Department of Taxes' Property Valuation and Review
(PVR) figures (figures/tax.json, from the yearly extract).

Vermont taxes a home its owner lives in (a declared homestead) at the homestead
education rate, the rest of a property at the nonhomestead rate, and all of it
at the municipal rate (with the local agreement rate, a few cents for the
state's tax stabilization agreements). The homes counted are the parcels in
category R1 (a residence on less than six acres) declared as a homestead.
Vermont has no category for single-family homes alone, so R1 includes
condominiums and two- to four-family homes. Rates are per $100 of listed value.
The bill is before the property tax credit, which lowers many homestead bills
by household income, so it's what a homestead owes before that credit, not what
most families pay.

The parcel data has one grand list year (GLYEAR) at a time. The bill is
calculated only when PVR's figures have that year's rates, and only while the
town's homestead values on the parcel data add up to about PVR's homestead
grand list for the year (VALUE_CHECK); otherwise the last one saved is kept.
Each year's figure is saved as it's calculated, so the history grows a year at
a time. Writes data/finance/tax_bill.json. Run by python -m pipeline.fetch_finance.
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
from pipeline.states.vt import figures

PARCELS = ("https://services1.arcgis.com/BkFxaEFNwHqX3tAw/arcgis/rest/services/"
           "FS_VCGI_OPENDATA_Cadastral_VTPARCELS_poly_standardized_parcels_SP_v1/FeatureServer/0/query")
PARCELS_PAGE = "https://geodata.vermont.gov/datasets/vt-data-statewide-standardized-parcel-data-parcel-polygons"
HOMESTEADS = "CAT = 'R1' AND HSDECL = 'Y'"
# The town's homestead grand list on the parcel data (GLVAL_HS, 1% of listed value) over PVR's for the year.
# They're the same list, so the ratio is about 1 (Burlington's is 1.0001 for 2025).
VALUE_CHECK = (0.95, 1.05)
RATES = ("homestead_rate", "municipal_rate", "local_agreement_rate")


# PVR posts a tax year's rates in its Annual Report's data the January after; they reach the engine's figures
# when they're extracted (extract.py).
RHYTHM = Rhythm(N_("Tax bill (calculated)"), "finance/tax_bill.json", "Fetch tax bill", "yearly", (
    Part(latest_year("years", "tax_year"), on(3, years_after=1), lambda y: _("Tax year {year}").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=60)


def parcel_stats(client, town: str, where: str, stats: dict[str, tuple[str, str]]) -> dict:
    """The parcel data's statistics for the town's parcels matching where: {name: (statistic, field)}."""
    town_sql = town.replace("'", "''")
    url = PARCELS + "?" + urlencode({
        "where": f"TNAME = '{town_sql}' AND {where}", "f": "json",
        "outStatistics": json.dumps([{"statisticType": t, "onStatisticField": f, "outStatisticFieldName": name}
                                     for name, (t, f) in stats.items()])})
    data = client.get(url).json()
    if "error" in data:
        raise FetchError(f"parcel data: {data['error'].get('message')}")
    found = data["features"][0]["attributes"] if data.get("features") else {}
    if not found.get("count"):
        raise FetchError(f"no parcels for {town!r} in the parcel data")
    return found


def method(tax_year: int, homes: int) -> str:
    return (f"The average listed value of {homes:,} homesteads (homes their owners live in, on less than six acres) "
            f"in VCGI's statewide parcel data, taxed at the town's {tax_year} homestead education, municipal, and local "
            "agreement rates, with any part of a home's value not declared as homestead at the nonhomestead rate. It's "
            "the bill before the property tax credit, which lowers many homestead bills by household income.")


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    fin = config["finance"]
    name = fin["pvr_town"]
    town = fin.get("parcels_town", name)
    path = data_dir / "finance" / "tax_bill.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    years = {y["tax_year"]: y for y in saved.get("years", [])}
    pvr = dict(figures.rows("tax", name))

    totals = parcel_stats(client, town, "1=1", {"count": ("count", "OBJECTID"), "homestead_list": ("sum", "GLVAL_HS"),
                                                "year": ("max", "GLYEAR")})
    tax_year = int(totals["year"])
    result: dict = {"tax_year": tax_year}
    row = pvr.get(tax_year)
    if row and any(row.get(k) is None for k in ("homestead_rate", "nonhomestead_rate", "municipal_rate")):
        row = None   # a year whose figures lack a rate can't be used
    ratio = totals["homestead_list"] / row["homestead_grand_list"] if row and row.get("homestead_grand_list") else None
    result["value_ratio"] = ratio and round(ratio, 4)
    if row and ratio and VALUE_CHECK[0] <= ratio <= VALUE_CHECK[1]:
        homes = parcel_stats(client, town, HOMESTEADS, {"count": ("count", "OBJECTID"), "value": ("avg", "REAL_FLV"),
                                                         "homestead": ("avg", "HSTED_FLV")})
        value, homestead = homes["value"], min(homes["homestead"], homes["value"])
        bill = (homestead * row["homestead_rate"] + (value - homestead) * row["nonhomestead_rate"]
                + value * (row["municipal_rate"] + (row.get("local_agreement_rate") or 0))) / 100
        years[tax_year] = {
            "tax_year": tax_year, "period": f"Tax year {tax_year}", "average_bill": round(bill),
            "average_value": round(value), "parcels": homes["count"],
            "rate": round(row["homestead_rate"] + row["municipal_rate"] + (row.get("local_agreement_rate") or 0), 4),
            "rates": {k: row.get(k) for k in (*RATES, "nonhomestead_rate")},
            "calculated": method(tax_year, homes["count"]),
        }
        result["average_bill"] = years[tax_year]["average_bill"]
    elif row is None:
        # The parcel data has a grand list year whose rates PVR hasn't published yet.
        print(f"::notice::The parcel data is on the {tax_year} grand list, whose rates aren't in the saved PVR "
              "figures yet; kept the saved tax bill. python -m pipeline.states.vt.extract brings them in once posted.")
        result["kept"] = sorted(years)
    else:
        print(f"::warning::{name}'s homestead values on the parcel data add up to "
              f"{'nothing' if ratio is None else f'{ratio:.3f} times'} PVR's {tax_year} homestead grand list, outside "
              f"{VALUE_CHECK[0]}–{VALUE_CHECK[1]}. Kept the saved tax bill.")
        result["kept"] = sorted(years)
    if not years:
        raise FetchError(f"no tax bill for {name} yet: the parcel data's grand list year has no rates in the figures")
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("tax"),
        "source": "Calculated by Publick from the Vermont Department of Taxes' tax rates and VCGI's statewide "
                  "parcel data",
        "source_url": figures.load("tax")["source_url"],
        "parcels_url": PARCELS_PAGE,
        "years": [years[y] for y in sorted(years)],
    }
    save_json(path, data)
    return result
