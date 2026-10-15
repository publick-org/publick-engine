"""Which ward a street address is in, for the Officials page's address box,
from the Census Bureau's address ranges -> data/addresses.json.

A reader types "12 Main St" and the page names their ward and who represents
it. The page has no geocoder and sends the address nowhere: it looks the street
up in a small file of house number ranges, each already given its ward here.

The ranges are the Census Bureau's TIGER/Line address range-feature files
(ADDRFEAT), one a county, free and with no key, published each fall for every
county in the country. Each record is a stretch of street between two
intersections, its name ("Main St"), its line, and the house numbers on its
left and right sides ("1 to 99", odd). So a town needs nothing of its own to
have the box but its ward file: the counties its wards are in come from the
Census Bureau's TIGERweb, and each range is placed in a ward by the ward file,
on the town's own machine:

- Each side of a stretch is placed by points along it, set back from the
  middle of the street on that side, as houses are. A ward line often runs
  down the middle of a street, with one side in each ward; set back, each side
  falls in its own.
- Where the ward changes partway along a side, the numbers are split where
  the line crosses, as far along the street as the numbers are (the Census
  Bureau's own assumption: numbers spread evenly between the ends). Numbers
  within AMBIGUOUS_M meters of the crossing are given both wards, and the page
  says so rather than guess: "Ward 2 or Ward 3".
- Numbers outside every ward are in another town, and are left out.

The file keeps each street's ranges, joined where neighbors share a ward and
a side, for the page to read: [first, last, parity, ward]. It's made again
when the Census Bureau publishes a new year, or the ward file changes; it
records the ward file it was made with, and the build leaves the box out
until the two match again (pipeline/build_site.py).

The files are read here without a GIS library: a shapefile's lines and its
table are simple, fixed formats (read_shapefile). A county's file is 1 to 8 MB,
downloaded once a run for every town in it (DOWNLOADS, in the system's
temporary folder), and only when the town's file is to be made again.

Usage:
    python -m pipeline.fetch_addresses [--town gloucester] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import struct
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pipeline import officials
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.files import write_atomic
from pipeline.geo import PrecinctLookup
from pipeline.http import FetchError, PoliteClient

ADDRFEAT_URL = "https://www2.census.gov/geo/tiger/TIGER{year}/ADDRFEAT/tl_{year}_{county}_addrfeat.zip"
COUNTIES_URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query"
FILE = "addresses.json"
# A county's files, downloaded once a run for every town in the county.
DOWNLOADS = Path(tempfile.gettempdir()) / "publick-addrfeat"
# How often a town whose file is the latest year's asks whether the next year's is out.
CHECK_DAYS = 30
# Houses stand back from the middle of the street: each side is placed by points this far to its side.
SETBACK_M = 15
# Points along each side, about this far apart (at least 3, at most 40, a side).
SPACING_M = 20
# Numbers this close to where a side crosses into another ward are given both wards.
AMBIGUOUS_M = 25
# Joining neighboring ranges of the same ward and side: a gap of up to this many numbers is closed.
JOIN_GAP = 50
PARITY = {"B": 0, "O": 1, "E": 2}
# Near matches offered for a street the page can't find as typed, at most.
MATCHES = 5

# Words of a street name written one way, so "12 North Main Street" finds TIGER's "N Main St". The
# page reads this same table (pipeline/build_site.py puts it in the file it serves).
WORDS = {
    # Street types, as the Postal Service abbreviates them (TIGER's names use the same).
    "STREET": "ST", "STR": "ST", "ROAD": "RD", "AVENUE": "AVE", "AV": "AVE", "AVN": "AVE", "DRIVE": "DR",
    "LANE": "LN", "CIRCLE": "CIR", "CR": "CIR", "CIRC": "CIR", "COURT": "CT", "TERRACE": "TER", "TERR": "TER",
    "TR": "TER", "PLACE": "PL", "TRAIL": "TRL", "HIGHWAY": "HWY", "BOULEVARD": "BLVD", "BV": "BLVD",
    "PARKWAY": "PKWY", "PKY": "PKWY", "TURNPIKE": "TPKE", "TPK": "TPKE", "SQUARE": "SQ", "HEIGHTS": "HTS",
    "HT": "HTS", "RIDGE": "RDG", "CROSSING": "XING", "POINT": "PT", "LANDING": "LNDG", "COVE": "CV",
    "VIEW": "VW", "COMMON": "CMN", "COMMONS": "CMNS", "ALLEY": "ALY", "ESTATES": "ESTS", "WY": "WAY",
    "EXTENSION": "EXT", "EXTENDED": "EXT", "EXN": "EXT", "EXD": "EXT", "EX": "EXT",
    # Directions.
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW",
    "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    # Saint, Mount, Fort, Route; numbered streets written out.
    "SAINT": "ST", "MOUNT": "MT", "FORT": "FT", "ROUTE": "RTE",
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH", "SIXTH": "6TH",
    "SEVENTH": "7TH", "EIGHTH": "8TH", "NINTH": "9TH", "TENTH": "10TH", "ELEVENTH": "11TH", "TWELFTH": "12TH",
}
# Street types and directions, which a reader may leave out or write differently ("Main" for "Main St";
# "Elm St" for "Elm Ave"); the page then offers the streets that differ only in them.
TYPES = {v for k, v in WORDS.items() if k not in ("SAINT", "MOUNT", "FORT", "ROUTE")
         and not v[0].isdigit() and v not in ("N", "S", "E", "W", "NE", "NW", "SE", "SW")} | {
    "ST", "RD", "AVE", "DR", "LN", "WAY", "CIR", "CT", "TER", "PL", "PARK", "PATH", "ROW", "RUN", "LOOP", "PASS"}
DIRECTIONS = ("N", "S", "E", "W", "NE", "NW", "SE", "SW")


def street_key(name: str) -> str:
    """'North Main Street' -> 'N MAIN ST': the form the file is keyed by and the page looks up."""
    words = re.sub(r"[.,'’]", "", name.upper()).replace("-", " ").split()
    return " ".join(WORDS.get(w, w) for w in words)


def base_key(key: str) -> str:
    """'N MAIN ST' -> 'MAIN': the name without its directions and type, to offer near matches
    ('N ST', North Street, -> 'N')."""
    words = key.split()
    while len(words) > 1 and words[-1] in DIRECTIONS:
        words = words[:-1]
    if len(words) > 1 and words[-1] in TYPES:
        words = words[:-1]
    while len(words) > 1 and words[0] in DIRECTIONS:
        words = words[1:]
    return " ".join(words)


# Reading a shapefile. Its .shp holds each record's shape, after a 100-byte header: a record header of
# two big-endian ints (number, length in 16-bit words), then the shape, little-endian. Type 3 is a
# line (PolyLine): a box, the number of parts and points, each part's first point, and the points.
# Its .dbf holds each record's fields as fixed-width text, after a header that describes them.

def read_dbf(data: bytes) -> list[dict | None]:
    count, header_size, record_size = struct.unpack("<IHH", data[4:12])
    fields, at = [], 32
    while data[at] != 0x0D:
        fields.append((data[at:at + 11].split(b"\0")[0].decode("ascii"), data[at + 16]))
        at += 32
    rows = []
    for i in range(count):
        record = data[header_size + i * record_size:header_size + (i + 1) * record_size]
        if record[:1] == b"*":  # deleted
            rows.append(None)
            continue
        row, at = {}, 1
        for name, size in fields:
            row[name] = record[at:at + size].decode("utf-8", "replace").strip()
            at += size
        rows.append(row)
    return rows


def read_shp(data: bytes) -> list[list[tuple[float, float]]]:
    """Each record's line, its parts joined in order, as (longitude, latitude); [] for an empty one."""
    lines, at = [], 100
    while at + 8 <= len(data):
        size = struct.unpack(">i", data[at + 4:at + 8])[0] * 2
        content = data[at + 8:at + 8 + size]
        at += 8 + size
        if struct.unpack("<i", content[:4])[0] != 3:
            lines.append([])
            continue
        parts, points = struct.unpack("<ii", content[36:44])
        start = 44 + 4 * parts
        values = struct.unpack(f"<{2 * points}d", content[start:start + 16 * points])
        lines.append(list(zip(values[0::2], values[1::2])))
    return lines


def read_shapefile(zipped: bytes) -> list[tuple[dict, list]]:
    """A zipped shapefile's records: each row of its table with its line."""
    with zipfile.ZipFile(io.BytesIO(zipped)) as z:
        names = z.namelist()
        shp = z.read(next(n for n in names if n.endswith(".shp")))
        dbf = z.read(next(n for n in names if n.endswith(".dbf")))
    rows, lines = read_dbf(dbf), read_shp(shp)
    if len(rows) != len(lines):
        raise ValueError(f"the shapefile has {len(lines)} shapes for {len(rows)} rows")
    return [(row, line) for row, line in zip(rows, lines) if row is not None and line]


class Side:
    """One side of a stretch of street: points along it, set back from the middle by SETBACK_M meters,
    left (+1) or right (-1) of the line's direction, measured in meters near the town."""

    def __init__(self, line: list, side: int):
        self.line = line
        self.side = side
        self.kx = 111_320 * math.cos(math.radians(line[0][1]))
        self.ky = 110_540
        self.along = [0.0]
        for (x0, y0), (x1, y1) in zip(line, line[1:]):
            self.along.append(self.along[-1] + math.hypot((x1 - x0) * self.kx, (y1 - y0) * self.ky))
        self.length = self.along[-1]

    def point(self, t: float) -> tuple[float, float]:
        """The point a fraction t of the way along, set back to this side: (longitude, latitude)."""
        at = t * self.length
        i = next((i for i in range(1, len(self.along)) if self.along[i] >= at), len(self.along) - 1)
        (x0, y0), (x1, y1) = self.line[i - 1], self.line[i]
        piece = self.along[i] - self.along[i - 1]
        f = (at - self.along[i - 1]) / piece if piece else 0.0
        dx, dy = (x1 - x0) * self.kx, (y1 - y0) * self.ky
        norm = math.hypot(dx, dy) or 1.0
        # Left of the direction (dx, dy) is (-dy, dx).
        return (x0 + (x1 - x0) * f - self.side * dy / norm * SETBACK_M / self.kx,
                y0 + (y1 - y0) * f + self.side * dx / norm * SETBACK_M / self.ky)


def place_side(side: Side, ward_at, first: int, last: int, parity: int) -> list[tuple[int, int, frozenset]]:
    """A side's numbers, first at the line's start and last at its end, in runs by ward:
    (low, high, wards), with wards empty outside the town and two wards near a crossing."""
    count = max(3, min(40, round(side.length / SPACING_M)))
    ts = [(i + 0.5) / count for i in range(count)]
    wards = [ward_at(side.point(t)) for t in ts]
    low, high = min(first, last), max(first, last)
    if len(set(wards)) == 1:
        return [(low, high, frozenset([wards[0]] if wards[0] else []))]
    # Where the ward changes between two points, found to a meter or so.
    changes = []
    for (t0, w0), (t1, w1) in zip(zip(ts, wards), zip(ts[1:], wards[1:])):
        if w0 == w1:
            continue
        a, b = t0, t1
        while (b - a) * side.length > 1:
            mid = (a + b) / 2
            if ward_at(side.point(mid)) == w0:
                a = mid
            else:
                b = mid
        changes.append(((a + b) / 2, w0, w1))
    near = AMBIGUOUS_M / side.length if side.length else 1.0
    step = 1 if parity == 0 else 2
    start = low if parity == 0 or low % 2 == (1 if parity == 1 else 0) else low + 1
    runs: list[list] = []
    for n in range(start, high + 1, step):
        t = 0.5 if first == last else (n - first) / (last - first)
        here = {w for c, w0, w1 in changes if abs(t - c) <= near for w in (w0, w1)}
        if not here:
            here = {next((w for c, w, _ in changes if t < c), changes[-1][2])}
        found = frozenset(w for w in here if w)
        if runs and runs[-1][2] == found:
            runs[-1][1] = n
        else:
            runs.append([n, n, found])
    return [tuple(r) for r in runs]


def ward_finder(wards_path: Path):
    """ward_at((longitude, latitude)) -> the ward's name, or None outside every ward."""
    lookup = PrecinctLookup(wards_path)

    def ward_at(point):
        found = lookup.find(point[1], point[0])
        return str(found["ward"]) if found else None
    return ward_at, lookup


def ranges(records, wards_path: Path) -> dict[str, dict]:
    """Every street in the wards, by key: its name as the Census Bureau writes it, and its ranges
    [low, high, parity (0 both, 1 odd, 2 even), ward (a name, or a list of two near a crossing)]."""
    ward_at, lookup = ward_finder(wards_path)
    x0 = min(b[0] for _, b in lookup.features) - 0.003
    y0 = min(b[1] for _, b in lookup.features) - 0.003
    x1 = max(b[2] for _, b in lookup.features) + 0.003
    y1 = max(b[3] for _, b in lookup.features) + 0.003
    found = defaultdict(list)
    names = defaultdict(lambda: defaultdict(int))
    for row, line in records:
        name = row.get("FULLNAME", "")
        if not name or not any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in line):
            continue
        key = street_key(name)
        for direction, (start, end, parity) in ((1, ("LFROMHN", "LTOHN", "PARITYL")),
                                                 (-1, ("RFROMHN", "RTOHN", "PARITYR"))):
            first, last = row.get(start, ""), row.get(end, "")
            # Hyphenated and lettered numbers ("12-98", "A99") are rare here, and left out.
            if not (first.isdigit() and last.isdigit()):
                continue
            side = Side(line, direction)
            if side.length == 0:
                continue
            code = PARITY.get(row.get(parity) or "B", 0)
            for low, high, wards in place_side(side, ward_at, int(first), int(last), code):
                if wards:
                    found[key].append((code, low, high, wards))
                    names[key][name] += 1
    return {key: {"name": max(names[key], key=lambda n: (names[key][n], n)), "ranges": join(found[key])}
            for key in sorted(found)}


def join(found: list[tuple]) -> list[list]:
    """A street's ranges, sorted, with neighbors of the same side and ward joined across a small gap."""
    out: list[list] = []
    for code, low, high, wards in sorted(found, key=lambda r: (r[0], r[1], r[2])):
        ward = sorted(wards, key=officials.ward_key)
        ward = ward[0] if len(ward) == 1 else ward
        last = out[-1] if out else None
        if last and last[2] == code and last[3] == ward and low <= last[1] + JOIN_GAP:
            last[1] = max(last[1], high)
        else:
            out.append([low, high, code, ward])
    return out


def interior_point(geometry: dict) -> tuple[float, float]:
    """A point inside a ward's largest piece: the middle of the widest stretch across it, halfway up."""
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    ring = max((p[0] for p in polys), key=len)
    ys = [y for _, y in ring]
    y = (min(ys) + max(ys)) / 2
    xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                for (x0, y0), (x1, y1) in zip(ring, ring[1:]) if (y0 > y) != (y1 > y))
    a, b = max(zip(xs[0::2], xs[1::2]), key=lambda p: p[1] - p[0])
    return (a + b) / 2, y


def counties_url(wards_path: Path) -> str:
    """TIGERweb's query for the counties the wards are in, by a point inside each."""
    features = json.loads(wards_path.read_text(encoding="utf-8"))["features"]
    points = [[round(c, 6) for c in interior_point(f["geometry"])] for f in features]
    return COUNTIES_URL + "?" + urlencode({
        "geometry": json.dumps({"points": points, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryMultipoint", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "outFields": "GEOID", "returnGeometry": "false", "f": "json"})


def download(client, year: int, county: str) -> bytes:
    """A county's ADDRFEAT file, once a run: a second town in the county reads the first's copy."""
    path = DOWNLOADS / f"tl_{year}_{county}_addrfeat.zip"
    if path.exists():
        return path.read_bytes()
    content = client.get(ADDRFEAT_URL.format(year=year, county=county)).content
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise FetchError(f"the Census Bureau's file for county {county} ({year}) isn't a zip file")
    write_atomic(path, content)
    return content


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def latest_year(client, county: str, today: int, known: int | None) -> int | None:
    """The newest year the Census Bureau has published the county's file for, from this year back,
    no further back than the one already made (None when it's still the newest)."""
    for year in range(today, known or today - 3, -1):
        try:
            download(client, year, county)
            return year
        except FetchError as e:
            if e.status != 404:
                raise
    if known is None:
        raise FetchError(f"the Census Bureau has no address ranges for county {county} from {today - 2} to {today}")
    return None


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    path = data_dir / FILE
    wards_path = officials.wards_path(data_dir, config)
    if wards_path is None or officials.at_large(config):
        return {"skipped": "no ward seats" if wards_path else "no ward file"}
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    wards_sha = file_hash(wards_path)
    same_wards = saved is not None and saved.get("wards_sha") == wards_sha
    if same_wards and not force:
        checked = datetime.fromisoformat(saved["checked_at"])
        if saved["year"] >= now.year or now - checked < timedelta(days=CHECK_DAYS):
            return {"skipped": f"made from {saved['year']}'s address ranges, checked {checked.date()}"}
    counties = sorted(f["attributes"]["GEOID"] for f in client.get(counties_url(wards_path)).json().get("features", []))
    if not counties:
        raise ValueError("TIGERweb found no county for the town's wards")
    year = latest_year(client, counties[0], now.year, saved["year"] if same_wards and not force else None)
    if year is None:
        saved["checked_at"] = now.isoformat(timespec="seconds")
        write_atomic(path, json.dumps(saved, ensure_ascii=False, separators=(",", ":")) + "\n")
        return {"skipped": f"{saved['year']}'s address ranges are still the newest"}
    records = [r for county in counties for r in read_shapefile(download(client, year, county))]
    streets = ranges(records, wards_path)
    data = {
        "year": year, "counties": counties,
        "source_urls": [ADDRFEAT_URL.format(year=year, county=c) for c in counties],
        "wards_file": wards_path.name, "wards_sha": wards_sha,
        "updated_at": now.isoformat(timespec="seconds"), "checked_at": now.isoformat(timespec="seconds"),
        "streets": {s["name"]: s["ranges"] for s in streets.values()},
    }
    # One line a street, so a year's changes read street by street in a diff.
    body = ",\n".join(f"{json.dumps(name, ensure_ascii=False)}:{json.dumps(r, separators=(',', ':'))}"
                      for name, r in data.pop("streets").items())
    head = json.dumps(data, ensure_ascii=False, separators=(",", ":"))[:-1]
    write_atomic(path, head + ',"streets":{\n' + body + "\n}}\n")
    return {"year": year, "counties": counties, "streets": len(streets),
            "ranges": sum(len(s["ranges"]) for s in streets.values())}


def load(data_dir: Path, config: dict) -> dict | None:
    """The town's address ranges for the page, or None when there are none, or they were made with
    another ward file than the town's (the box would name the wrong wards)."""
    path = data_dir / FILE
    wards_path = officials.wards_path(data_dir, config)
    if wards_path is None or not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("wards_sha") != file_hash(wards_path):
        print(f"::warning::{path} was made with another ward file than {wards_path.name}; "
              "the Officials page has no address box until pipeline.fetch_addresses runs again.")
        return None
    return data


def page_data(data: dict) -> dict:
    """What the Officials page's script reads (site/static/js/address.js): the streets' ranges, and the
    words it writes one way, the street types and directions, as street_key and street_for use them."""
    return {"words": WORDS, "types": sorted(TYPES), "directions": list(DIRECTIONS), "matches": MATCHES,
            "streets": data["streets"]}


# A unit after the street ("Apt 3", "#2"), and the house number before it ("12", "12A", "12-14", "12 1/2").
UNIT = re.compile(r"\s+(?:(?:APT|APARTMENT|UNIT|STE|SUITE|FL|FLOOR|RM|ROOM)\b|#).*$")
NUMBER = re.compile(r"\s*#?(\d+)(?:[A-Z]\b|\s+1/2|\s*[-–]\s*\d+[A-Z]?)?\s+")
ZIP = re.compile(r"\d{5}(?:-\d{4})?")


def parse(text: str, places: tuple[str, ...] = ()) -> tuple[int | None, str]:
    """'12A Main St., Apt 3, Lowell, MA 01852' -> (12, 'MAIN ST'): the house number, if any, and the street's
    key. Anything after a comma is left out, and places (the town, its state) and a ZIP code after the street.
    The page's script does the same."""
    text = UNIT.sub("", text.upper().strip(" ,").split(",")[0])
    number = NUMBER.match(text)
    words = re.sub(r"[.,'’]", "", text[number.end():] if number else text).split()
    tails = sorted((p.upper().split() for p in (*places, "USA", "US", "United States") if p), key=len, reverse=True)
    while words:
        if ZIP.fullmatch(words[-1]):
            words.pop()
            continue
        tail = next((t for t in tails if len(words) > len(t) and words[-len(t):] == t), None)
        if tail is None:
            break
        del words[-len(tail):]
    return (int(number.group(1)) if number else None), street_key(" ".join(words))


def directions(key: str) -> list[str]:
    """'N MAIN ST' -> ['N'], and 'KILBY ST N' -> ['N']: the directions before and after a name."""
    words = key.split()
    return sorted({w for w in (words[0], words[-1]) if w in DIRECTIONS}) if len(words) > 1 else []


def street_type(key: str) -> str:
    words = key.split()
    return words[-1] if len(words) > 1 and words[-1] in TYPES else ""


def distance(a: str, b: str) -> int:
    """How many letters differ between two names (added, dropped, or changed)."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def street_for(keys: dict[str, str], key: str) -> tuple[str | None, list[str]]:
    """The street a reader means, and the streets to offer when it isn't clear: (name, []) or (None, names).
    Written as typed, or with the same name and type and other directions ("Main St" for "N Main St"),
    is the street; then a place name after it ("High St Wakefield") is dropped. Any other near match,
    a different type ("Elm Ave" for "Elm St") or spelling ("Harriet Rd" for "Harriett Rd"), is offered,
    for the reader to choose, never taken as theirs."""
    if key in keys:
        return keys[key], []
    words = key.split()
    for drop in range(min(3, len(words) - 1) + 1):
        typed = " ".join(words[:len(words) - drop])
        if drop and (any(w in TYPES or w in DIRECTIONS for w in words[len(words) - drop:]) or typed not in keys):
            continue
        if typed in keys:
            return keys[typed], []
        same = [k for k in keys if base_key(k) == base_key(typed)]
        fits = [k for k in same if street_type(typed) in ("", street_type(k))
                and (not directions(typed) or directions(typed) == directions(k))]
        if len(fits) == 1:
            return keys[fits[0]], []
        if same:
            return None, sorted(keys[k] for k in same)
    base = base_key(key)
    if len(base) < 4:
        return None, []
    near = sorted((distance(base, base_key(k)), keys[k]) for k in keys if abs(len(base_key(k)) - len(base)) <= 2)
    return None, [name for d, name in near if d <= (1 if len(base) < 7 else 2)][:MATCHES]


def find(data: dict, text: str, places: tuple[str, ...] = (), keys: dict | None = None) -> dict:
    """What the page shows for an address, worked out as its script does: {"street": the street's name,
    "wards": the wards the address can be in, "how", and "matches", the streets to offer when the street
    isn't clear}. how is "number" (the ranges have the number), "street" (no number given: wards are the
    street's), "no number" (the ranges don't have the number: wards are the street's), "choose" (matches
    to offer), or "none". keys, each street's key and name, can be given to save making it each time."""
    number, key = parse(text, places)
    name, matches = street_for(keys or {street_key(n): n for n in data["streets"]}, key)
    if name is None:
        return {"street": None, "wards": [], "how": "choose" if matches else "none", "matches": matches}
    street = data["streets"][name]

    def wards(rows):
        return sorted({w for r in rows for w in ([r[3]] if isinstance(r[3], str) else r[3])}, key=officials.ward_key)
    if number is None:
        return {"street": name, "wards": wards(street), "how": "street", "matches": []}
    hits = wards(r for r in street if r[0] <= number <= r[1] and r[2] in (0, 2 - number % 2))
    if hits:
        return {"street": name, "wards": hits, "how": "number", "matches": []}
    return {"street": name, "wards": wards(street), "how": "no number", "matches": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="make the file again, even if it's up to date")
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "officials"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=120.0)
    try:
        result = run(config, client, args.data, force=args.force)
    except (FetchError, KeyError, ValueError, zipfile.BadZipFile) as e:
        print(f"::warning::The address ranges could not be fetched: {e}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
