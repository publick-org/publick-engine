"""Maine's answers for a new town (pipeline/new_town.py; pipeline/states/ma/lookups.py says what a lookups
module gives):

- MRS's name for the town ([finance] mrs_municipality), from the yearly files saved in figures/;
- for the tax bill, its GEOCODE in the Maine GeoLibrary's parcel table and the land use codes it gives its
  single-family homes, as the table describes them ("SINGLE FAMILY"). A town whose parcels have no land use
  ("0" throughout) can't have its homes picked out, so it has no tax bill, as Portland and Bangor don't;
  one whose codes have no descriptions is left for a person;
- the Department of Education's name for its district ([schools] doe_district), from figures/.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from pipeline.http import FetchError
from pipeline.states.lookups import BUDGET_DOCUMENTS, figure_names, figures_source, naming, result, same, todo
from pipeline.states.me.tax_bill import PARCELS, quoted

FINANCE = ("Tax rate, property tax, and the city's budget (pipeline/states/me/), from Maine Revenue Services' "
           "figures; the average single-family bill is calculated from them and the Maine GeoLibrary's parcel table.")
SCHOOLS = "Schools: the Department of Education's ESSA Dashboard."
# The parcel service's towns, with each one's GEOCODE (PARCELS is its parcels).
TOWNS = PARCELS.replace("/9/query", "/10/query")
SCHOOL_FILES = ("graduation", "assessment", "spending")


def geocode_url(name: str) -> str:
    return TOWNS + "?" + urlencode({"where": f"TOWN = {quoted(name)}", "outFields": "GEOCODE",
                                    "returnDistinctValues": "true", "returnGeometry": "false", "f": "json"})


def land_uses_url(geocode: str) -> str:
    """How many of the town's parcels have each land use, with its description."""
    stats = [{"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "parcels"}]
    return PARCELS + "?" + urlencode({"where": f"GEOCODE = {quoted(geocode)}", "groupByFieldsForStatistics":
                                      "LAND_USE,LAND_USE_D", "outStatistics": json.dumps(stats), "f": "json"})


def land_uses(data: dict) -> list[tuple[int, str, str]]:
    """(parcels, code, description), the most used first."""
    rows = [(f["attributes"]["parcels"], str(f["attributes"]["LAND_USE"] or "").strip(),
             str(f["attributes"]["LAND_USE_D"] or "").strip()) for f in data.get("features", [])]
    return sorted(rows, key=lambda r: -r[0])


def tax_bill_keys(name: str, client, out: dict) -> dict:
    """megis_geocode and single_family_use, when the parcel table describes the town's single-family code."""
    try:
        found = client.get(geocode_url(name)).json().get("features", [])
    except (FetchError, ValueError) as e:
        out["notes"].append(f"The Maine GeoLibrary's parcel table could not be read ({e}).")
        return {}
    geocodes = sorted({f["attributes"]["GEOCODE"] for f in found})
    if len(geocodes) != 1:
        out["finance_todo"] = (f"megis_geocode and single_family_use: the parcel table has {len(geocodes) or 'no'} "
                               f"GEOCODE for \"{name}\" ({', '.join(geocodes) or 'none'}). Without them, no tax bill.")
        return {}
    geocode = geocodes[0]
    try:
        uses = land_uses(client.get(land_uses_url(geocode)).json())
    except (FetchError, ValueError) as e:
        out["notes"].append(f"The parcel table's land uses for GEOCODE {geocode} could not be read ({e}).")
        return {}
    single = [code for _, code, about in uses if "SINGLE FAMILY" in about.upper()]
    source = "Maine GeoLibrary parcel table"
    if single:
        out["found"].append(("Parcel table", f"GEOCODE {geocode}, single-family land use {', '.join(single)}",
                             land_uses_url(geocode)))
        return {"megis_geocode": geocode, "single_family_use": single}
    if all(code in ("", "0") for _, code, _ in uses):
        out["finance_comment"] = (f"No average single-family tax bill: the town's rows in the {source} (GEOCODE "
                                  f"{geocode}) all have land use \"0\", so its homes can't be picked out, and the "
                                  "tax bill's keys (megis_geocode, single_family_use) are left out.")
        return {}
    common = ", ".join(f'"{code}" ({n:,})' for n, code, _ in uses[:6])
    out["finance_todo"] = (f"single_family_use, the land use codes the town gives its single-family homes in the "
                           f"{source} (GEOCODE {geocode}), which has no descriptions for them; the most used: "
                           f"{common}. Set megis_geocode = \"{geocode}\" with them, or leave both out (no tax bill).")
    return {}


def codes(name: str, client, network: Path) -> dict:
    out = result()
    town = same(name.upper(), figure_names("me", "tax"))
    if town:
        out["finance"] = {"mrs_municipality": town.title() if town.isupper() else town,
                          **tax_bill_keys(name, client, out)}
        out["found"].append(("MRS name", town, figures_source("me", "tax")))
        todo(out, "finance", BUDGET_DOCUMENTS.format(kind="city"))
    districts = figure_names("me", *SCHOOL_FILES)
    district = same(f"{name} Public Schools", districts) or same(f"{name} School Department", districts)
    if district:
        out["schools"] = {"doe_district": district, "district_name": district}
        out["found"].append(("Department of Education district", district, figures_source("me", "graduation")))
        out["notes"].append(f"district_name is \"{district}\": check it's the district's own name.")
    else:
        listed = "; ".join(f'"{d}"' for d in naming(name, districts))
        out["schools_todo"] = ("[schools] doe_district, the town's district as the ESSA Dashboard names it "
                               "(pipeline/states/me/figures/; many towns are in an RSU or MSAD), and district_name"
                               + (f": the files name {listed}." if listed else "."))
    return out
