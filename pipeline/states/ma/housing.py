"""Massachusetts housing figures, added to the housing page by pipeline.fetch_housing.

- The share of year-round homes on the state's Subsidized Housing Inventory
  (Chapter 40B), from the state's PDF, when [housing] has shi_url.
- Residential parcels by type, from the Mass. Division of Local Services, when
  the town has a [finance] table.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

import requests
from pypdf import PdfReader

from pipeline.http import FetchError, PoliteClient
from pipeline.states.ma.dls import REPORT_URL, table

# The housing.json keys these add.
keys = ("shi", "parcels")
SHI_PAGE = "https://www.mass.gov/info-details/subsidized-housing-inventory-shi"
PARCELS_REPORT = "PropertyTaxInformation.LA4.Parcel_counts_vals"
# DLS parcel columns shown on the page, with plain names. Parcels, not homes:
# a condominium building has one parcel per unit, an apartment building one in all.
PARCEL_TYPES = {"Single Family 101": "Single-family homes", "Condominiums 102": "Condominiums",
                "Two Family 104": "Two-family homes", "Three Family 105": "Three-family homes",
                "Apartment 111-125": "Apartment buildings (4 or more units)",
                "Miscellaneous Residential 103,109": "Other residential"}


def parts(config: dict) -> set[str]:
    """The figures this town has sources for."""
    return ({"shi"} if "shi_url" in config.get("housing", {}) else set()) | ({"parcels"} if "finance" in config else set())


def client(config: dict) -> PoliteClient:
    """mass.gov refuses the site's usual User-Agent but accepts the HTTP library's
    own, so the inventory's request sends that plus the site's domain."""
    return PoliteClient(f"{requests.utils.default_user_agent()} ({config['site']['domain']})", timeout=60.0)


def sources(config: dict, client, state_client, now: datetime) -> list[tuple]:
    """(key, fetch) for each figure this town has. state_client is client(config), for the inventory."""
    have = parts(config)
    return ([("shi", lambda: shi(state_client or client, config))] if "shi" in have else []) + \
        ([("parcels", lambda: parcels(client, config, now))] if "parcels" in have else [])


# ---- Subsidized Housing Inventory ----

def pdf_text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


def parse_shi(text: str, town: str) -> dict:
    """The town's row of the inventory: year-round homes, development units, SHI units, percent."""
    as_of = re.search(r"as of ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    row = re.search(rf"^{re.escape(town)} \d+ ([\d,]+) ([\d,]+) ([\d,]+) ([\d.]+)%", text, re.M)
    if not row or not as_of:
        raise FetchError(f"{town} not found in the Subsidized Housing Inventory")
    as_of_date = datetime.strptime(as_of.group(1), "%B %d, %Y").date().isoformat()
    census = re.search(r"(\d{4}) Census", text)
    return {
        "as_of": as_of_date,
        "census_year": int(census.group(1)) if census else None,
        "year_round_homes": int(row.group(1).replace(",", "")),
        "development_units": int(row.group(2).replace(",", "")),
        "shi_units": int(row.group(3).replace(",", "")),
        "percent": float(row.group(4)),
        "goal_percent": 10,
    }


def shi(client, config: dict) -> dict:
    h = config["housing"]
    response = client.get(h["shi_url"])
    if not response.content.startswith(b"%PDF"):
        raise FetchError("the Subsidized Housing Inventory link did not return a PDF")
    return {**parse_shi(pdf_text(response.content), h["shi_name"]), "source_url": SHI_PAGE}


# ---- Parcels by type ----

def parcels(client, config: dict, now: datetime) -> dict:
    name = config["finance"]["dls_municipality"]
    newest = now.year + 1 if now.month >= 7 else now.year
    for fy in (newest, newest - 1, newest - 2):
        found = table(client, PARCELS_REPORT, "xtParcels", ("iclMuni", name), islYear=fy)
        if found and found[0].get("Single Family 101"):
            row = found[0]
            return {"fiscal_year": int(row["Fiscal Year"]),
                    "types": {label: int(row[col] or 0) for col, label in PARCEL_TYPES.items()},
                    "source_url": f"{REPORT_URL}?rdReport={PARCELS_REPORT}"}
    raise FetchError("no parcel counts returned")
