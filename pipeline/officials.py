"""Who represents you: a town's elected officials, from its config's [officials]
table, and its wards, for the Officials page (/officials/).

The members are kept by hand: they change at elections and vacancies, and no
city publishes them as data. The ward boundaries are the town's ward file in
data/static/ (the same file 311 uses), simplified for the page's map.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

MEMBER_FIELDS = {"name", "seat", "ward", "role", "term_ends", "email", "phone", "url"}
BODY_FIELDS = {"name", "board", "url", "note", "members"}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", "and")).strip("-")


def ward_key(ward: str) -> tuple:
    """Ward 2 before Ward 10; wards named with letters after numbers."""
    return (0, int(ward), "") if ward.isdigit() else (1, 0, ward)


def wards_file(config: dict) -> str | None:
    """The town's ward file: [officials] wards_file, or the one 311 uses."""
    return config.get("officials", {}).get("wards_file") or config.get("seeclickfix", {}).get("precincts_file")


def load(config: dict, data_dir: Path, boards: dict[str, str] | None = None) -> dict:
    """The page's officials: each body with its members, and who represents each ward.
    boards maps meeting board names to their pages, to link each body's meetings."""
    table = config.get("officials")
    where = f"config/{config['slug']}.toml"
    if not table or not table.get("bodies"):
        raise SystemExit(f"The officials section needs an [officials] table with at least one [[officials.bodies]] in {where}.")
    try:
        checked = date.fromisoformat(str(table["checked"]))
    except (KeyError, ValueError):
        raise SystemExit(f'[officials] checked in {where} must be the date the list was last checked, like "2026-10-01".')
    wards = sorted(ward_names(data_dir, config), key=ward_key)
    boards = boards or {}
    bodies, by_ward = [], defaultdict(list)
    for body in table["bodies"]:
        unknown = set(body) - BODY_FIELDS
        if unknown or not body.get("name") or not body.get("members"):
            raise SystemExit(f"Each [[officials.bodies]] in {where} needs a name and members"
                             + (f"; unknown {', '.join(sorted(unknown))}" if unknown else "") + ".")
        members = []
        for m in body["members"]:
            unknown = set(m) - MEMBER_FIELDS
            if unknown or not m.get("name") or not m.get("seat"):
                raise SystemExit(f"Each member of the {body['name']} in {where} needs a name and a seat"
                                 + (f"; unknown {', '.join(sorted(unknown))}" if unknown else "") + ".")
            ward = str(m["ward"]) if "ward" in m else None
            if ward is not None and wards and ward not in wards:
                raise SystemExit(f"{m['name']} ({body['name']}) is for ward {ward}, which isn't in the ward file "
                                 f"{wards_file(config)} ({', '.join(wards) or 'none'}).")
            term_ends = str(m["term_ends"]) if "term_ends" in m else None
            if term_ends is not None and not re.fullmatch(r"\d{4}-\d{2}", term_ends):
                raise SystemExit(f'term_ends for {m["name"]} in {where} must be a month, like "2028-01".')
            member = {**m, "ward": ward, "ward_id": f"ward-{slugify(ward)}" if ward and wards else None, "term_ends": term_ends,
                      "id": slugify(m["name"]),
                      # The member's row: someone on two bodies (the mayor) has a row on each.
                      "anchor": f"{slugify(body['name'])}-{slugify(m['name'])}"}
            members.append(member)
            if member["ward_id"]:
                by_ward[ward].append({"body": body["name"], "body_id": slugify(body["name"]), **member})
        board = body.get("board", body["name"])
        bodies.append({**body, "id": slugify(body["name"]), "members": members, "board_url": boards.get(board)})
    return {
        "checked": checked.isoformat(),
        "bodies": bodies,
        # Every ward in the ward file, even one with no ward seat, so the list matches the map.
        "wards": [{"ward": w, "id": f"ward-{slugify(w)}", "members": by_ward.get(w, [])} for w in wards],
    }


def ward_names(data_dir: Path, config: dict) -> set[str]:
    path = wards_path(data_dir, config)
    if path is None:
        return set()
    return {str(f["properties"]["ward"]) for f in json.loads(path.read_text(encoding="utf-8"))["features"]}


def wards_path(data_dir: Path, config: dict) -> Path | None:
    name = wards_file(config)
    if not name:
        return None
    path = data_dir / "static" / name
    if not path.exists():
        # As with a section's data before its first fetch: the page is built without its wards.
        print(f"::warning::The ward file {path} is missing; the Officials page has no wards.")
        return None
    return path


def map_data(data_dir: Path, config: dict, places: int = 5) -> list[dict]:
    """Each ward's shape for the page's map: its precincts' polygons, for filling
    and for finding the ward a point is in, and its outline, the precinct edges
    that aren't shared with another precinct of the same ward. Coordinates are
    rounded to about a meter."""
    path = wards_path(data_dir, config)
    if path is None:
        return []
    polygons = defaultdict(list)
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in parts:
            polygons[str(f["properties"]["ward"])].append(
                [[[round(x, places), round(y, places)] for x, y, *_ in ring] for ring in poly])
    return [{"ward": w, "polygons": polygons[w], "outline": outline(polygons[w])}
            for w in sorted(polygons, key=ward_key)]


def outline(polygons: list) -> list[list]:
    """The edges of a set of polygons that only one of them has, joined into lines:
    the outside of a ward made of precincts, without the lines between them."""
    count = defaultdict(int)
    for poly in polygons:
        for ring in poly:
            for a, b in zip(ring, ring[1:]):
                if a != b:
                    count[frozenset((tuple(a), tuple(b)))] += 1
    edges = defaultdict(list)
    for edge, n in count.items():
        if n == 1:
            a, b = tuple(edge)
            edges[a].append(b)
            edges[b].append(a)
    lines = []
    while edges:
        start = next(iter(edges))
        line = [start]
        while edges.get(line[-1]):
            here = line[-1]
            nxt = edges[here].pop()
            edges[nxt].remove(here)
            for p in (here, nxt):
                if not edges[p]:
                    del edges[p]
            line.append(nxt)
        lines.append([list(p) for p in line])
    return lines
