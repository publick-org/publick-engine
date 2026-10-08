"""Whether the town is a city or a town by law, from the Census Bureau -> data/place.json.

The site says "the city" or "the town" (and "la ciudad" or "el pueblo") as the
place is: Beverly is a city, Wallingford a town. The Census Bureau names each
place by its legal type ("Beverly city", "Wallingford town"), and its TIGERweb
map service gives the name and type code (LSAD) of the place [housing]
census_geo names, with no key. It's fetched once, when the town has no
data/place.json; --force fetches it again. A town's [town] kind, when set,
comes before it (pipeline/build_site.py).

Usage:
    python -m pipeline.fetch_place [--town gloucester] [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.files import write_atomic
from pipeline.http import FetchError, PoliteClient

TIGERWEB = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer"
# census_geo's summary level -> the map service's layer: county subdivisions (New England's
# towns, as Wallingford is given), and incorporated places (Beverly).
LAYERS = {"060": 1, "160": 4}
# Legal types (LSAD codes) read as a town; every other one, a city among them (25), reads as a city.
TOWN_TYPES = {"43": "town", "44": "township", "47": "village", "21": "borough"}


def query_url(census_geo: str) -> str:
    """'06000US0917078740' -> the map service's query for county subdivision 0917078740."""
    level, geoid = census_geo[:3], census_geo.split("US", 1)[1]
    if level not in LAYERS:
        raise ValueError(f"census_geo {census_geo}: summary level {level} isn't a city or town")
    return f"{TIGERWEB}/{LAYERS[level]}/query?" + urlencode(
        {"where": f"GEOID='{geoid}'", "outFields": "NAME,LSADC,GEOID", "returnGeometry": "false", "f": "json"})


def parse(data: dict) -> dict:
    """The place's Census name and whether it is a city or a town."""
    features = data.get("features") or []
    if not features:
        raise ValueError("no place with that code")
    found = features[0]["attributes"]
    return {"name": found["NAME"], "lsad": found["LSADC"],
            "kind": "town" if found["LSADC"] in TOWN_TYPES else "city"}


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    path = data_dir / "place.json"
    if path.exists() and not force:
        return {"skipped": "already recorded"}
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    url = query_url(config["housing"]["census_geo"])
    data = {**parse(client.get(url).json()), "source_url": url, "updated_at": now.isoformat(timespec="seconds")}
    write_atomic(path, json.dumps(data, indent=2) + "\n")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="fetch again even if it's recorded")
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "housing"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=60.0)
    try:
        result = run(config, client, args.data, force=args.force)
    except (FetchError, KeyError, ValueError) as e:
        print(f"::warning::Whether the town is a city or a town could not be fetched: {e}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
