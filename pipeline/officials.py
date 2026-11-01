"""Who represents you: a town's elected officials, from its config's [officials]
table, and its wards, for the Officials page (/officials/).

The members are kept by hand: they change at elections and vacancies, and no
city publishes them as data. The ward boundaries are the town's ward file in
data/static/ (the same file 311 uses), simplified for the page's map.

A list kept by hand goes out of date at each election, so a town's config
names its next municipal election ([officials] next_election). The list is
due a check once the election is held, or once new terms begin, if that's
later (the first month a member's term ends on or after the election's month,
from the members' own term_ends). A list not checked by then, GRACE_DAYS
after, is behind: the About page's data status, the network's status page,
and the daily alert say so, and the Officials page says some members may
have changed (review, pipeline/freshness.py).

Members elected before they take office are recorded with their body as
incoming: the day they take office ([officials.bodies.incoming] takes_office)
and the body's whole membership from that day (members). Until then the page
lists the sitting members and names those elected; from that day it lists the
incoming members, with no change to the config, since every daily run builds
the page again. The list is then due a check once they have taken office, for
the officers a body chooses when it organizes.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

MEMBER_FIELDS = {"name", "seat", "ward", "wards", "role", "term_ends", "email", "phone", "url"}
BODY_FIELDS = {"name", "board", "url", "note", "members", "incoming"}
INCOMING_FIELDS = {"takes_office", "members"}
# Days after a list is due a check (the election, or new terms) before it counts as behind.
GRACE_DAYS = 7


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", "and")).strip("-")


def ward_key(ward: str) -> tuple:
    """Ward 2 before Ward 10; wards named with letters after numbers."""
    return (0, int(ward), "") if ward.isdigit() else (1, 0, ward)


def wards_file(config: dict) -> str | None:
    """The town's ward file: [officials] wards_file, or the one 311 uses unless its areas are voting
    precincts. A town that places 311 requests in voting precincts has no wards to look up."""
    sc = config.get("seeclickfix", {})
    return config.get("officials", {}).get("wards_file") or (sc.get("precincts_file") if areas(config) != "precincts" else None)


# What a town can call its areas on its pages: "Ward 3", "Precinct 3201", "District 3".
AREAS = ("wards", "precincts", "districts")


def areas(config: dict) -> str:
    """What the town calls its areas on the 311 and Officials pages ([town] areas): wards (the default),
    voting precincts, or districts. [seeclickfix] areas = "precincts", where towns without wards
    first said so, still names them."""
    where = f"config/{config['slug']}.toml"
    town = config.get("town", {}).get("areas")
    sc = config.get("seeclickfix", {}).get("areas")
    if town is not None and town not in AREAS:
        raise SystemExit(f'[town] areas in {where} must be "wards", "precincts" or "districts", not {town!r}.')
    if sc is not None and sc not in ("wards", "precincts"):
        raise SystemExit(f'[seeclickfix] areas in {where} must be "wards" or "precincts", not {sc!r} '
                         f'(districts are [town] areas).')
    if town and sc and town != sc:
        raise SystemExit(f"[town] areas and [seeclickfix] areas in {where} differ: keep [town] areas only.")
    return town or sc or "wards"


def at_large(config: dict) -> bool:
    """Every seat is elected by the whole town: there are no wards to map. Readers know whether
    their town has wards, so the page shows no map and doesn't say why."""
    return not any(m.get("ward") is not None or m.get("wards")
                   for b in config.get("officials", {}).get("bodies", []) for m in b.get("members", []))


def load(config: dict, data_dir: Path, boards: dict[str, str] | None = None, today: date | None = None) -> dict:
    """The page's officials: each body with its members, and who represents each ward.
    boards maps meeting board names to their pages, to link each body's meetings; today is the build's day."""
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
    today = today or date.today()
    bodies, by_ward, citywide = [], defaultdict(list), []
    for body in table["bodies"]:
        unknown = set(body) - BODY_FIELDS
        if unknown or not body.get("name") or not body.get("members"):
            raise SystemExit(f"Each [[officials.bodies]] in {where} needs a name and members"
                             + (f"; unknown {', '.join(sorted(unknown))}" if unknown else "") + ".")
        sitting = [checked_member(m, body["name"], wards, config) for m in body["members"]]
        coming = incoming(body, where)
        if coming:
            arriving = [checked_member(m, body["name"], wards, config) for m in coming["members"]]
            continuing(body, sitting, arriving, coming["takes_office"], where)
            if today >= coming["takes_office"]:
                print(f"::warning::The {body['name']}'s incoming members took office on {coming['takes_office']}: "
                      f"make them its members in {where}.")
                sitting, coming = arriving, None
        members = []
        for m, seat_wards in sitting:
            linked = sorted(seat_wards, key=ward_key) if wards else []
            member = {**m, "wards": [{"ward": w, "id": f"ward-{slugify(w)}"} for w in linked],
                      "term_ends": str(m["term_ends"]) if "term_ends" in m else None,
                      "id": slugify(m["name"]),
                      # The member's row: someone on two bodies (the mayor) has a row on each.
                      "anchor": f"{slugify(body['name'])}-{slugify(m['name'])}"}
            members.append(member)
            for ward in linked:
                by_ward[ward].append({"body": body["name"], "body_id": slugify(body["name"]),
                                      "order": (len(bodies), len(linked)), **member})
            if not seat_wards and "wards" not in m:
                citywide.append({"body": body["name"], **member})
        board = body.get("board", body["name"])
        bodies.append({**body, "id": slugify(body["name"]), "members": members, "board_url": boards.get(board),
                       # A Seat column only where seats differ: "At-large" on every row, or a mayor's one
                       # "Citywide", says nothing the body's note doesn't.
                       "show_seat": len({m["seat"] for m in members}) > 1,
                       # Members elected and not yet in office: named under the body until they take office.
                       "incoming": coming and elected(sitting, arriving, coming["takes_office"])})
    due = review(config, today)
    return {
        "checked": checked.isoformat(),
        # Not checked since the election, or since new terms began: the page says members may have changed.
        "out_of_date": due if due and due["behind"] else None,
        "bodies": bodies,
        # A dash for a term or contact no official source gives, explained under the tables.
        "dashes": any(not m["term_ends"] or not (m.get("email") or m.get("phone")) for b in bodies for m in b["members"]),
        "at_large": at_large(config),
        # Every ward in the ward file, even one with no ward seat, so the list matches the map.
        # In a ward, each body's member for that ward alone comes before its member for a district of wards.
        "wards": [{"ward": w, "id": f"ward-{slugify(w)}", "members": sorted(by_ward.get(w, []), key=lambda m: m["order"])}
                  for w in wards],
        # Members elected by the whole town (the mayor, at-large seats), who represent every ward too: the
        # address box names them with a ward's own.
        "citywide": citywide,
    }


def checked_member(m: dict, body: str, wards: list[str], config: dict) -> tuple[dict, list[str]]:
    """A member of the body as the config gives them, checked, with the wards their seat is elected by."""
    where = f"config/{config['slug']}.toml"
    unknown = set(m) - MEMBER_FIELDS
    if unknown or not m.get("name") or not m.get("seat"):
        raise SystemExit(f"Each member of the {body} in {where} needs a name and a seat"
                         + (f"; unknown {', '.join(sorted(unknown))}" if unknown else "") + ".")
    # A seat elected by one ward (ward = 3), or by several (wards = [1, 2, 3], a district of wards), or
    # by a part of the city the ward map doesn't follow (wards = [], Worcester's School Committee districts,
    # each of precincts from several wards): listed with its body, not under a ward or as citywide.
    if "ward" in m and "wards" in m:
        raise SystemExit(f"{m['name']} ({body}) in {where} has both ward and wards; use one.")
    seat_wards = [str(w) for w in (m["wards"] if "wards" in m else [m["ward"]] if "ward" in m else [])]
    for ward in seat_wards:
        if wards and ward not in wards:
            raise SystemExit(f"{m['name']} ({body}) is for ward {ward}, which isn't in the ward file "
                             f"{wards_file(config)} ({', '.join(wards) or 'none'}).")
    if "term_ends" in m and not re.fullmatch(r"\d{4}-\d{2}", str(m["term_ends"])):
        raise SystemExit(f'term_ends for {m["name"]} in {where} must be a month, like "2028-01".')
    return m, seat_wards


def incoming(body: dict, where: str) -> dict | None:
    """The body's members from the day newly elected members take office ([officials.bodies.incoming]),
    or None. takes_office is that day; members is the whole body from then on."""
    coming = body.get("incoming")
    if coming is None:
        return None
    unknown = set(coming) - INCOMING_FIELDS if isinstance(coming, dict) else set()
    if not isinstance(coming, dict) or unknown or not coming.get("members"):
        raise SystemExit(f"The {body['name']}'s incoming in {where} needs takes_office and members, the whole body "
                         f"from that day" + (f"; unknown {', '.join(sorted(unknown))}" if unknown else "") + ".")
    try:
        day = date.fromisoformat(str(coming["takes_office"]))
    except (KeyError, ValueError):
        raise SystemExit(f"takes_office for the {body['name']}'s incoming members in {where} must be the day they "
                         f'take office, like "2026-12-07".')
    return {"takes_office": day, "members": coming["members"]}


def continuing(body: dict, sitting: list, arriving: list, day: date, where: str) -> None:
    """Every sitting member whose term runs past the month the incoming members take office is one of them:
    incoming lists the whole body, so a member left out by mistake would leave the page that day."""
    names = {m["name"] for m, _ in arriving}
    for m, _ in sitting:
        if "term_ends" in m and str(m["term_ends"]) > day.strftime("%Y-%m") and m["name"] not in names:
            raise SystemExit(f"{m['name']}'s term on the {body['name']} ends in {m['term_ends']}, after the incoming "
                             f"members take office on {day}: list them in its incoming members too ({where}).")


def elected(sitting: list, arriving: list, day: date) -> dict:
    """Who the page names as elected and taking office on that day: the incoming members who aren't
    sitting with the same term (a member re-elected is named; one whose term continues isn't)."""
    now = {(m["name"], str(m.get("term_ends"))) for m, _ in sitting}
    new = [m for m, _ in arriving if (m["name"], str(m.get("term_ends"))) not in now]
    return {"takes_office": day.isoformat(), "show_seat": len({m["seat"] for m, _ in arriving}) > 1,
            "members": [{"name": m["name"], "seat": m["seat"]} for m in new]}


def review(config: dict, today: date) -> dict | None:
    """When the list is due a check, from the town's next municipal election ([officials] next_election),
    or None for a town that doesn't give one. Due on the election's day, or on the first day of the month
    new terms begin, if that's later: the first month a member's term ends on or after the election's
    month. Behind when it wasn't checked on or after that day, GRACE_DAYS after it; passed when it was,
    and the election is over, so the config's next_election should move to the one after.

    Members recorded as incoming, and not checked since they took office, are due a check that day
    instead (seated): the page lists them from then on, and the officers a body chooses as it organizes
    are known only after."""
    table = config.get("officials") or {}
    if "next_election" not in table:
        return None
    where = f"config/{config['slug']}.toml"
    try:
        election = date.fromisoformat(str(table["next_election"]))
    except ValueError:
        raise SystemExit(f'[officials] next_election in {where} must be the date of the town\'s next municipal election, '
                         f'like "2027-11-02".')
    checked = date.fromisoformat(str(table["checked"]))
    seated = max((coming["takes_office"] for body in table.get("bodies", []) if (coming := incoming(body, where))
                  and coming["takes_office"] > checked), default=None)
    if seated:
        return {"election": election, "terms": None, "seated": seated, "due": seated, "checked": checked,
                "behind": today >= seated + timedelta(days=GRACE_DAYS), "passed": False}
    ends = sorted(date.fromisoformat(f"{m['term_ends']}-01") for body in table.get("bodies", [])
                  for m in body.get("members", []) if re.fullmatch(r"\d{4}-\d{2}", str(m.get("term_ends", ""))))
    terms = next((d for d in ends if d >= election.replace(day=1)), None)
    due = max(election, terms) if terms else election
    return {"election": election, "terms": terms if terms and terms > election else None, "seated": None, "due": due,
            "checked": checked, "behind": checked < due and today >= due + timedelta(days=GRACE_DAYS),
            "passed": checked >= due and today >= due}


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
