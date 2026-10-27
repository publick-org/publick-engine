"""Massachusetts housing figures, added to the housing page by pipeline.fetch_housing.

- The share of year-round homes on the state's Subsidized Housing Inventory
  (Chapter 40B), from the state's PDF, when [housing] has shi_url. In the
  network, the PDF is read once for every town (refresh, statewide.py).
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
from pipeline.i18n import N_
from pipeline.states.ma import statewide
from pipeline.states.ma.dls import REPORT_URL, table

# The housing.json keys these add.
keys = ("shi", "parcels")
SHI_PAGE = "https://www.mass.gov/info-details/subsidized-housing-inventory-shi"
PARCELS_REPORT = "PropertyTaxInformation.LA4.Parcel_counts_vals"
# DLS parcel columns shown on the page, with plain names. Parcels, not homes:
# a condominium building has one parcel per unit, an apartment building one in all.
# Saved in English and translated where they're shown (the build's data_label).
PARCEL_TYPES = {"Single Family 101": N_("Single-family homes"), "Condominiums 102": N_("Condominiums"),
                "Two Family 104": N_("Two-family homes"), "Three Family 105": N_("Three-family homes"),
                "Apartment 111-125": N_("Apartment buildings (4 or more units)"),
                "Miscellaneous Residential 103,109": N_("Other residential")}


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


# One municipality's row: name, Muni_ID (its DOR code), year-round homes, development units, SHI units, percent.
SHI_ROW = re.compile(r"^([A-Z][A-Za-z.' -]*?) (\d+) ([\d,]+) ([\d,]+) ([\d,]+) ([\d.]+)%", re.M)
# The inventory as the statewide store keeps it (statewide.py).
SHI_FILE = "shi.json"


def parse_inventory(text: str) -> dict:
    """Every municipality's row of the inventory, by name as the PDF lists it."""
    as_of = re.search(r"as of ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    rows = {m.group(1): {"year_round_homes": int(m.group(3).replace(",", "")),
                         "development_units": int(m.group(4).replace(",", "")),
                         "shi_units": int(m.group(5).replace(",", "")),
                         "percent": float(m.group(6))}
            for m in SHI_ROW.finditer(text)}
    if not rows or not as_of:
        raise FetchError("the Subsidized Housing Inventory's rows could not be read")
    census = re.search(r"(\d{4}) Census", text)
    return {"as_of": datetime.strptime(as_of.group(1), "%B %d, %Y").date().isoformat(),
            "census_year": int(census.group(1)) if census else None, "rows": rows}


def town_row(inventory: dict, town: str) -> dict:
    """The town's figures from the inventory: year-round homes, development units, SHI units, percent."""
    row = inventory["rows"].get(town)
    if not row:
        raise FetchError(f"{town} not found in the Subsidized Housing Inventory")
    return {"as_of": inventory["as_of"], "census_year": inventory["census_year"], **row, "goal_percent": 10}


def parse_shi(text: str, town: str) -> dict:
    return town_row(parse_inventory(text), town)


def fetch_inventory(client, url: str) -> dict:
    response = client.get(url)
    if not response.content.startswith(b"%PDF"):
        raise FetchError("the Subsidized Housing Inventory link did not return a PDF")
    return parse_inventory(pdf_text(response.content))


def shi(client, config: dict) -> dict:
    """The town's row, from the statewide store when it has the town, else from the PDF itself."""
    h = config["housing"]
    saved = statewide.read(SHI_FILE)
    inventory = saved if saved and h["shi_name"] in saved["rows"] else fetch_inventory(client, h["shi_url"])
    return {**town_row(inventory, h["shi_name"]), "source_url": SHI_PAGE}


def refresh(state_dir, configs: list[dict], _client, now: datetime) -> dict:
    """The statewide store's copy of the inventory (statewide.py), fetched once for these towns. mass.gov
    refuses the network's own User-Agent, so the PDF is asked for as a town's request is (client())."""
    if not statewide.due(state_dir, SHI_FILE, now):
        return {"exports": 1, "fetched": 0}
    inventory = fetch_inventory(client(configs[0]), configs[0]["housing"]["shi_url"])
    statewide.save(state_dir, SHI_FILE, statewide.dumps({k: v for k, v in inventory.items() if k != "rows"},
                                                        inventory["rows"]), now)
    return {"exports": 1, "fetched": 1}


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
