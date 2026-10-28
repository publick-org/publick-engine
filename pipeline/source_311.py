"""Where a town's 311 requests come from, and what that source records.

A town's 311 requests come from one of two sources, each turned on by its config
table: SeeClickFix ([seeclickfix], pipeline/fetch_311.py) or a city's Socrata
open data portal ([socrata_311], pipeline/fetch_socrata_311.py). Both save the
same records in data/311/requests.json, and pipeline/compute_311.py works out
the same scorecard from either. What differs is what the source records, and so
what the 311 pages can show (describe()):

  acknowledged  when city staff acknowledged a request (SeeClickFix only)
  locations     an address and a map point: the maps, the repeat locations, the street lookup
  links         a public page for each request, which the lists link
  areas         a ward (or precinct, or district) for each request: from the map point and
                the ward file on SeeClickFix, from the row on a portal that records it
"""

from __future__ import annotations

from pipeline import socrata

TABLES = ("seeclickfix", "socrata_311")


def table(config: dict) -> str | None:
    """The config table of the town's 311 source, or None for a town without 311."""
    return next((t for t in TABLES if t in config), None)


def settings(config: dict) -> dict:
    return config.get(table(config) or "", {})


def has_areas(config: dict) -> bool:
    """Whether requests are placed in wards, precincts or districts."""
    if "seeclickfix" in config:
        # A town with no wards or precincts has no precincts_file.
        return bool(config["seeclickfix"].get("precincts_file"))
    if "socrata_311" in config:
        return "ward" in socrata.fields(config["socrata_311"])
    return False


def population_file(config: dict) -> str | None:
    """The file in data/static/ whose features give each area's population_2020, for requests per
    1,000 residents: SeeClickFix's ward file, or for a portal's wards, [socrata_311] wards_file or the
    Officials page's."""
    if "seeclickfix" in config:
        return config["seeclickfix"].get("precincts_file")
    if has_areas(config):
        return config["socrata_311"].get("wards_file") or config.get("officials", {}).get("wards_file")
    return None


def describe(config: dict) -> dict | None:
    """What the 311 pages need to know about the source, or None for a town without 311."""
    source = table(config)
    if source == "seeclickfix":
        return {"table": source, "settings": config[source], "name": "SeeClickFix", "url": "https://seeclickfix.com",
                "acknowledged": True, "locations": True, "links": True, "areas": has_areas(config)}
    if source == "socrata_311":
        s = config[source]
        return {"table": source, "settings": s, "name": s["publisher"], "url": f"https://{s['domain']}/d/{s['dataset']}",
                "portal": s["domain"], "license": s.get("license"), "license_url": s.get("license_url"),
                "acknowledged": False, "locations": False, "links": False, "areas": has_areas(config)}
    return None
