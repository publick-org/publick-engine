"""Massachusetts's answers for a new town (pipeline/new_town.py): the DLS name and
DOR code, the DESE district, and the ward and precinct file.

Every state's lookups module gives the helper the same things: FINANCE and
SCHOOLS, the comments above those tables in the config; codes(name, client,
network), what the state's sources say of the town (see codes() below); and,
where the state publishes every town's wards or precincts, wards(name, client).

- The DLS name and DOR code ([finance]) from the network's copy of DLS's
  reports (states/ma/dls/ in the network repository), the town's row in the
  newest average tax bill report ("DOR Code": "030", "Municipality": "Beverly").
  The config gives the code without its leading zeros ("30").
- The DESE district ([schools]) from the state's education data portal: the
  district named for the town, whose code is the DOR code followed by 0000
  (Beverly's 00300000). The config names it as the district does, "Beverly
  Public Schools"; a regional district is left to a person.
- The wards and precincts from MassGIS's 2022 file, laid out as every
  Massachusetts town's data/static/<town>-precincts-2022.geojson: ward,
  precinct, district and 2020 population, coordinates to 6 places, sorted by
  ward and precinct. A town without wards (Methuen) has only voting precincts:
  each is its own area, its number given as both ward and precinct.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from pipeline.http import FetchError

DESE_PORTAL = "https://educationtocareer.data.mass.gov"
DESE_DISTRICTS = f"{DESE_PORTAL}/resource/n2xa-p822.json"
MASSGIS = "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/WardsPrecincts2022"
# The file's own address, as every town's ward file names its source.
MASSGIS_SOURCE = f"MassGIS, Wards and Precincts (2022), {MASSGIS}/FeatureServer/0"
MASSGIS_MAP = "https://gis.data.mass.gov/maps/aec5130790814ace94438d3bcf23cf9a"

FINANCE = "Tax bill and budget figures (Mass. Division of Local Services). Some DLS reports take the name, others the DOR code."
SCHOOLS = "School district figures from the state's education data portal (DESE)."


def codes(name: str, client, network: Path) -> dict:
    """What the state's sources say of the town, for the config's [finance] and [schools]:

    finance, schools: each table's keys, or None when not found;
    finance_todo, schools_todo: what's left for a person in that table (a "To fill in" comment), if anything;
    finance_comment: a comment under [finance], if any;
    found: (what, answer, source) for the report; notes: anything else a person should know."""
    out = {"found": [], "notes": []}
    dls = dls_town(name, network / "states" / "ma" / "dls")
    if dls:
        out["finance"] = {"dls_municipality": dls["dls_municipality"], "dls_code": dls["dls_code"]}
        out["found"].append(("DLS name and DOR code", f"{dls['dls_municipality']}, {dls['dls_code']}",
                             f"states/ma/dls/{dls['file']}"))
    url = districts_url(name)
    try:
        rows = client.get(url).json()
    except (FetchError, ValueError):
        rows = []
    found = district(name, dls and dls["dls_code"], rows)
    if found:
        out["schools"] = found
        out["found"].append(("DESE district", f"{found['district_code']} ({found['district_name']})", url))
    elif rows:
        listed = "; ".join(f"{r['dist_code']} {r['dist_name']}" for r in rows)
        out["schools_todo"] = ("[schools], the town's school district (the keys in the engine's "
                               f"pipeline/states/ma/__init__.py): the portal lists {listed}.")
    return out


def dls_town(name: str, dls_dir: Path) -> dict | None:
    """The town's DLS name and DOR code, from the newest average tax bill report in dls_dir."""
    reports = sorted(dls_dir.glob("AverageSingleTaxBill.*.json"))
    if not reports:
        return None
    rows = json.loads(reports[-1].read_text(encoding="utf-8"))
    row = next((r for r in rows if r.get("Municipality", "").lower() == name.lower()), None)
    if not row:
        return None
    return {"dls_municipality": row["Municipality"], "dls_code": str(int(row["DOR Code"])), "file": reports[-1].name}


def districts_url(name: str) -> str:
    """The portal's districts with the town's name in theirs."""
    name = name.replace("'", "''")
    query = f"SELECT dist_code, dist_name WHERE dist_name like '%{name}%' GROUP BY dist_code, dist_name"
    return f"{DESE_DISTRICTS}?{urlencode({'$query': query})}"


def district(name: str, dls_code: str | None, rows: list[dict]) -> dict | None:
    """The town's own district among the portal's rows: named for the town, and numbered
    by its DOR code when that's known."""
    code = f"{int(dls_code):04d}0000" if dls_code else None
    for row in rows:
        if row.get("dist_name", "").lower() == name.lower() and (not code or row.get("dist_code") == code):
            return {"district_code": row["dist_code"], "district_name": f"{row['dist_name']} Public Schools",
                    "portal": DESE_PORTAL}
    return None


def wards_url(name: str) -> str:
    """MassGIS's wards and precincts for the town, as GeoJSON."""
    return f"{MASSGIS}/MapServer/0/query?" + urlencode({
        "where": f"TOWN='{name.upper()}'", "outFields": "WARD,PRECINCT,WP_DISTRICT,POP_2020", "outSR": "4326",
        "f": "geojson"})


def _round(coordinates):
    if isinstance(coordinates[0], list):
        return [_round(c) for c in coordinates]
    return [round(coordinates[0], 6), round(coordinates[1], 6)]


def _number(value: str) -> tuple:
    """'2' before '10'; a precinct with a letter ('6A') after the numbers."""
    return (0, int(value), "") if value.isdigit() else (1, 0, value)


def ward_file(name: str, data: dict) -> tuple[str, bool] | None:
    """The town's ward file, as text, and whether it has wards (False: voting precincts only).
    None when MassGIS has nothing for the town."""
    features = data.get("features") or []
    if not features:
        return None
    wards = any(f["properties"].get("WARD") for f in features)
    out = []
    for f in features:
        p = f["properties"]
        out.append({"type": "Feature",
                    "properties": {"ward": p["WARD"] if wards else p["PRECINCT"], "precinct": p["PRECINCT"],
                                   "district": p["WP_DISTRICT"], "population_2020": p["POP_2020"]},
                    "geometry": {"type": f["geometry"]["type"], "coordinates": _round(f["geometry"]["coordinates"])}})
    out.sort(key=lambda f: (_number(f["properties"]["ward"]), _number(f["properties"]["precinct"])))
    title = f"{name} wards and precincts (2022)" if wards else f"{name} voting precincts (2022)"
    text = json.dumps({"type": "FeatureCollection", "name": title, "source": MASSGIS_SOURCE, "features": out},
                      separators=(",", ":"))
    return text, wards


def wards(name: str, client) -> dict | None:
    """The town's ward and precinct file from MassGIS, or None if it has none for the town."""
    url = wards_url(name)
    try:
        made = ward_file(name, client.get(url).json())
    except (FetchError, ValueError):
        return None
    if not made:
        return None
    text, has_wards = made
    return {"name": "precincts-2022", "text": text, "wards": has_wards, "map": MASSGIS_MAP, "source": url,
            "publisher": "MassGIS", "year": "2022",
            "no_wards": "MassGIS's 2022 file has no WARD"}
