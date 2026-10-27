"""Rhode Island's answers for a new town (pipeline/new_town.py; pipeline/states/ma/lookups.py says what a
lookups module gives): the Division of Municipal Finance's name for the town, from the tables saved in
figures/, and its district's RIDE code, from the districts ("LEA") sheet of RIDE's newest graduation rate
file (or its finance file, for a district without a high school). A town in a regional district (Chariho,
Exeter-West Greenwich, Foster-Glocester, Bristol Warren) gets the districts named for it to choose from. Its
voting precincts, and wards where it has them, come from RIGIS's statewide layer, with their population from
the 2020 census blocks."""

from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import urlencode

import openpyxl

from pipeline.http import FetchError
from pipeline.states.lookups import (BUDGET_DOCUMENTS, blocks_population, figure_names, figures_source, naming, result,
                                     rounded, same, todo, ward_collection)
from pipeline.states.ri.schools import DATA_FILES, report_files

FINANCE = ("Tax rates, levy, and assessed values (pipeline/states/ri/), from the Division of Municipal Finance's "
           "statewide tables. Rhode Island publishes no average single-family tax bill.")
SCHOOLS = "School district figures from the Rhode Island Department of Education (RIDE)."


def districts(content: bytes) -> dict[str, str]:
    """{name: code} from a report card data file's districts sheet."""
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    sheet = next((s for s in book.worksheets if s.title.upper().startswith("LEA")), None)
    if sheet is None:
        return {}
    rows = sheet.iter_rows(values_only=True)
    header = [str(h or "") for h in next(rows)]
    code, name = header.index("LEACode"), header.index("LEAName")
    return {str(r[name]).strip(): str(r[code]).strip().zfill(2) for r in rows if r[code] and r[name]}


def codes(name: str, client, network: Path) -> dict:
    out = result()
    town = same(name, figure_names("ri", "rates", "levy"))
    if town:
        out["finance"] = {"dmf_municipality": town}
        todo(out, "finance", BUDGET_DOCUMENTS.format(kind="town"))
        out["found"].append(("Division of Municipal Finance name", town, figures_source("ri", "rates")))
    try:
        files = report_files(client.get(DATA_FILES).text)
    except FetchError as e:
        out["notes"].append(f"RIDE's data files could not be read ({e}).")
        files = {}
    listed: dict[str, str] = {}
    for measure in ("graduation", "spending"):
        if not files.get(measure):
            continue
        url = files[measure][max(files[measure])]
        try:
            listed.update(districts(client.get(url).content))
        except (FetchError, ValueError, KeyError) as e:
            out["notes"].append(f"RIDE's {measure} file could not be read ({e}).")
            continue
        district = same(name, listed)
        if district:
            out["schools"] = {"ride_district": int(listed[district]), "district_name": f"{district} Public Schools"}
            out["found"].append(("RIDE district", f"{listed[district]} ({district})", url))
            out["notes"].append(f"district_name is \"{district} Public Schools\": check it's the district's own name.")
            return out
    named = "; ".join(f'{listed[d]} "{d}"' for d in naming(name, listed))
    out["schools_todo"] = ("[schools] ride_district, the town's district's RIDE code (the LEA sheet of RIDE's report "
                           "card data files), and district_name" + (f": RIDE lists {named}." if named else "."))
    return out


# RIGIS's voting precincts (2022) for every town: DISTRICT, the precinct's four-digit code, and Ward, its ward
# or council district (0 where the town has none).
PRECINCTS = "https://services2.arcgis.com/S8zZg9pg23JUEexQ/ArcGIS/rest/services/BND_Voting_Precincts_2022/FeatureServer/0"
PRECINCTS_ITEM = "https://www.arcgis.com/home/item.html?id=9104ca2e5e9b4cdb9985e8935ef2514d"


def wards_url(name: str) -> str:
    return f"{PRECINCTS}/query?" + urlencode({"where": f"TOWN = '{name.replace(chr(39), chr(39) * 2)}'",
                                              "outFields": "DISTRICT,Ward", "outSR": "4326", "f": "geojson"})


def wards(name: str, client) -> dict | None:
    """The town's precincts, each with its ward where it has wards, and each one's 2020 population."""
    url = wards_url(name)
    found = [f for f in client.get(url).json().get("features") or [] if str(f["properties"].get("DISTRICT")).isdigit()]
    if not found:
        return None
    has_wards = any(int(f["properties"].get("Ward") or 0) for f in found)
    features = []
    for f in sorted(found, key=lambda f: (int(f["properties"].get("Ward") or 0), f["properties"]["DISTRICT"])):
        precinct = str(f["properties"]["DISTRICT"])
        ward = str(int(f["properties"]["Ward"])) if has_wards else precinct
        features.append({"type": "Feature", "properties": {"ward": ward, "precinct": precinct, "district": precinct},
                         "geometry": {"type": f["geometry"]["type"], "coordinates": rounded(f["geometry"]["coordinates"])}})
    for f, people in zip(features, blocks_population(client, features)):
        f["properties"]["population_2020"] = people
    title = f"{name} wards and precincts (2022)" if has_wards else f"{name} voting precincts (2022)"
    return {"name": "precincts-2022", "text": ward_collection(title, f"RIGIS, Voting Precincts (2022), {PRECINCTS}", features),
            "wards": has_wards, "map": PRECINCTS_ITEM, "source": url, "publisher": "RIGIS", "year": 2022,
            "no_wards": "RIGIS's 2022 precincts have no ward"}
