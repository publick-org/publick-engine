"""What the states' lookups modules for a new town share (pipeline/states/<st>/lookups.py, used by
pipeline/new_town.py; pipeline/states/ma/lookups.py says what each module gives)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from pipeline.http import FetchError

STATES_DIR = Path(__file__).parent


def figure_names(state: str, *kinds: str) -> set[str]:
    """Every name in any year of the state's saved figures of these kinds (figures/<kind>.json)."""
    names: set[str] = set()
    for kind in kinds:
        data = json.loads((STATES_DIR / state / "figures" / f"{kind}.json").read_text(encoding="utf-8"))
        for rows in data["years"].values():
            names.update(rows)
    return names


def figures_source(state: str, kind: str) -> str:
    return f"pipeline/states/{state}/figures/{kind}.json"


def same(name: str, names) -> str | None:
    """The one of names that is name, ignoring case and spacing, as names writes it."""
    key = " ".join(name.split()).casefold()
    return next((n for n in sorted(names) if " ".join(str(n).split()).casefold() == key), None)


def naming(name: str, names) -> list[str]:
    """Those of names with the town's name in them, as words: candidates for a person to choose from (a name
    written twice, with different spacing, once)."""
    key = f" {' '.join(name.casefold().split())} "
    found: dict[str, str] = {}
    for n in sorted(names):
        words = " ".join(str(n).casefold().replace("-", " ").split())
        if key in f" {words} ":
            found.setdefault(words, n)
    return sorted(found.values())


def result() -> dict:
    return {"found": [], "notes": []}


def todo(out: dict, table: str, text: str) -> None:
    """Something more for a person to fill in, in [table]."""
    key = f"{table}_todo"
    out[key] = f"{out[key]} Also {text}" if out.get(key) else text


BUDGET_DOCUMENTS = ("budget_documents_url, the page where the {kind} posts its own budget books, which the budget "
                    "page links (leave it out if there's none).")


# ---- Ward files ----------------------------------------------------------------------------------------------
# 2020 census blocks, with each one's population and internal point, for a ward file's population_2020
# where the state's own layer has none (MassGIS's has).
BLOCKS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer/10/query"
BLOCKS_SOURCE = "population from the U.S. Census Bureau's 2020 census blocks"


def rounded(coordinates, repeats: bool = True):
    """Coordinates to 6 places; without repeats, a point the same as the one before it (after rounding) dropped."""
    if isinstance(coordinates[0], list):
        if not repeats and not isinstance(coordinates[0][0], list):
            out = []
            for point in coordinates:
                point = [round(point[0], 6), round(point[1], 6)]
                if not out or point != out[-1]:
                    out.append(point)
            return out
        return [rounded(c, repeats) for c in coordinates]
    return [round(coordinates[0], 6), round(coordinates[1], 6)]


def polygons(geometry: dict) -> list:
    return geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]


def blocks_population(client, features: list[dict]) -> list[int]:
    """Each feature's 2020 population: the census blocks whose internal point is inside it. Exact where the
    features follow block lines, as wards and precincts drawn after the 2020 census do."""
    from pipeline.geo import _bbox, _in_polygon

    boxes = [_bbox(f["geometry"]) for f in features]
    envelope = ",".join(str(v) for v in (min(b[0] for b in boxes), min(b[1] for b in boxes),
                                         max(b[2] for b in boxes), max(b[3] for b in boxes)))
    people, offset = [0] * len(features), 0
    while True:
        data = client.get(BLOCKS + "?" + urlencode({
            "geometry": envelope, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "outFields": "POP100,INTPTLAT,INTPTLON",
            "returnGeometry": "false", "resultOffset": offset, "f": "json"})).json()
        if "error" in data:
            raise FetchError(f"TIGERweb's census blocks: {data['error'].get('message')}")
        for block in data.get("features", []):
            a = block["attributes"]
            lon, lat = float(a["INTPTLON"]), float(a["INTPTLAT"])
            for i, f in enumerate(features):
                x0, y0, x1, y1 = boxes[i]
                if x0 <= lon <= x1 and y0 <= lat <= y1 and any(_in_polygon(lon, lat, p) for p in polygons(f["geometry"])):
                    people[i] += a["POP100"] or 0
                    break
        if not data.get("exceededTransferLimit"):
            return people
        offset += len(data.get("features", []))


def ward_collection(name: str, source: str, features: list[dict]) -> str:
    """A ward file's text, laid out as every town's data/static/ files are."""
    return json.dumps({"type": "FeatureCollection", "name": name, "source": source, "features": features},
                      separators=(",", ":"))
