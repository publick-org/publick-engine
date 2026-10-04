"""Connecticut's open data portal, data.ct.gov: its datasets through the Socrata API.

Every figure the Connecticut package reads for the tax bill and budget is a
statewide dataset there, published by the Office of Policy and Management (OPM),
so one query answers for one town or for all 169 at once (a median, say).
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from pipeline.http import FetchError

PORTAL = "https://data.ct.gov"
CATALOG = "https://api.us.socrata.com/api/catalog/v1"
LIMIT = 5000

# The datasets, by their Socrata ids.
MILL_RATES = "emyx-j53e"           # Mill Rates for FY 2014-2027 (OPM), one row per town or district a year
TAX_LEVY = "he33-brru"             # Tax levy by town and district, FY2019 on (OPM)
GRAND_LIST = "webp-fgt3"           # Net grand list by town, by grand list year (OPM)
FISCAL_INDICATORS = "ej6f-y2wf"    # Municipal Fiscal Indicators, from each town's audit (OPM)
ADOPTED_BUDGETS = "pcg4-s5rc"      # Adopted budgets from the Fiscal Health Monitoring System (OPM)
# The yearly parcel files are found by name, so a new year's is read once it's published.
CAMA_NAME = re.compile(r"^(\d{4}) Connecticut Parcel and CAMA Data$")


def page(dataset: str) -> str:
    """The dataset's page on data.ct.gov, for a source link."""
    return f"{PORTAL}/d/{dataset}"


def query_url(dataset: str, **params) -> str:
    return f"{PORTAL}/resource/{dataset}.json?" + urlencode({f"${k}": v for k, v in params.items()})


def query(client, dataset: str, **params) -> list[dict]:
    """The rows a SoQL query returns: where, select, group, order, limit."""
    params.setdefault("limit", LIMIT)
    data = client.get(query_url(dataset, **params)).json()
    if isinstance(data, dict):
        raise FetchError(f"data.ct.gov ({dataset}): {data.get('message') or data.get('error') or data}")
    return data


def quoted(text: str) -> str:
    """A string for a SoQL query."""
    return "'" + str(text).replace("'", "''") + "'"


def cama_datasets(client) -> dict[int, str]:
    """{collection year: dataset id} for the yearly Parcel and CAMA files, found in the portal's catalog."""
    url = CATALOG + "?" + urlencode({"domains": "data.ct.gov", "q": "Connecticut Parcel and CAMA Data", "limit": 50})
    found = {}
    for result in client.get(url).json().get("results", []):
        resource = result.get("resource", {})
        if m := CAMA_NAME.match(resource.get("name", "").strip()):
            found[int(m.group(1))] = resource["id"]
    if not found:
        raise FetchError("no Parcel and CAMA datasets in data.ct.gov's catalog")
    return found


def number(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
