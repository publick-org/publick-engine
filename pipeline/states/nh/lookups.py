"""New Hampshire's answers for a new town (pipeline/new_town.py; pipeline/states/ma/lookups.py says what a
lookups module gives): the DRA's name for the town and the Department of Education's for its district, both
from the state's yearly files saved in figures/. A town that is part of a cooperative district, or sends its
students to another town's schools, gets the districts named for it to choose from. A city's wards come from
NH GRANIT's statewide layer, with their population from the 2020 census blocks."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlencode

from pipeline.states.lookups import (BLOCKS_SOURCE, BUDGET_DOCUMENTS, blocks_population, figure_names, figures_source,
                                     naming, polygons, result, rounded, same, todo, ward_collection)

FINANCE = ("Tax rate, property tax, and the average single-family tax bill (pipeline/states/nh/), from the New "
           "Hampshire DRA's tax rates in the engine's yearly statewide figures and NH GRANIT's parcel map.")
SCHOOLS = "Schools: the New Hampshire Department of Education's statewide figures."
SCHOOL_FILES = ("graduation", "assessment", "cost_per_pupil")


def codes(name: str, client, network: Path) -> dict:
    out = result()
    town = same(name, figure_names("nh", "tax"))
    if town:
        out["finance"] = {"dra_municipality": town}
        todo(out, "finance", BUDGET_DOCUMENTS.format(kind="town"))
        out["found"].append(("DRA name", town, figures_source("nh", "tax")))
    districts = figure_names("nh", *SCHOOL_FILES)
    district = same(name, districts)
    if district:
        out["schools"] = {"doe_district": district, "district_name": f"{district} School District"}
        out["found"].append(("Department of Education district", district, figures_source("nh", "graduation")))
        out["notes"].append(f"district_name is \"{district} School District\": check it's the district's own name.")
        if not same(name, figure_names("nh", "graduation")):
            out["notes"].append(f"{district} has no graduation rate in the state's files: its students finish high "
                                "school in another district.")
    else:
        listed = "; ".join(f'"{d}"' for d in naming(name, districts))
        out["schools_todo"] = ("[schools] doe_district, the town's district as the Department of Education's files "
                               "name it (pipeline/states/nh/figures/), and district_name"
                               + (f": the files name {listed}." if listed else "."))
    return out


# NH GRANIT's voting wards (2022) for every town: a ward city's wards, "Nashua Ward 1", and a town without
# wards as one "<Town>- entire" area (so are slivers of some ward cities, which are dropped).
WARDS = "https://nhgeodata.unh.edu/hosting/rest/services/Hosted/APB_ElectoralDistricts/FeatureServer/11"
WARDS_ITEM = "https://www.arcgis.com/home/item.html?id=3d1a8bdf3eaa49e09cf5d9da5b56bf9c"


def wards_url(name: str) -> str:
    return f"{WARDS}/query?" + urlencode({"where": f"name = '{name.replace(chr(39), chr(39) * 2)}'",
                                          "outFields": "name,district", "outSR": "4326", "f": "geojson"})


def ward_features(data: dict) -> list[dict]:
    """The wards, numbered, in order; a ward in more than one piece as one MultiPolygon."""
    pieces: dict[str, list] = {}
    for f in data.get("features") or []:
        m = re.fullmatch(r".* Ward (\d+)", f["properties"].get("district") or "")
        if m:
            pieces.setdefault(m.group(1), []).extend(polygons(f["geometry"]))
    out = []
    for ward in sorted(pieces, key=int):
        parts = pieces[ward]
        geometry = ({"type": "Polygon", "coordinates": rounded(parts[0], repeats=False)} if len(parts) == 1
                    else {"type": "MultiPolygon", "coordinates": rounded(parts, repeats=False)})
        out.append({"type": "Feature", "properties": {"ward": ward, "district": ward}, "geometry": geometry})
    return out


def wards(name: str, client) -> dict | None:
    """The city's wards, with each one's 2020 population, or None for a town without wards."""
    url = wards_url(name)
    features = ward_features(client.get(url).json())
    if not features:
        return None
    for f, people in zip(features, blocks_population(client, features)):
        f["properties"]["population_2020"] = people
    source = f"NH GRANIT, New Hampshire Political Districts (Voting Wards) - 2022, {WARDS}; {BLOCKS_SOURCE}"
    return {"name": "wards-2022", "text": ward_collection(f"{name} wards (2022)", source, features), "wards": True,
            "map": WARDS_ITEM, "source": url, "publisher": "NH GRANIT", "year": 2022}
