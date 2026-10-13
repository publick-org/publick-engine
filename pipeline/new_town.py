"""Start a new town's folder: what the city uses, its codes and wards, and a config to finish by hand.

Run from the network repository (publick-org/publick.org), with the engine on
PYTHONPATH, as for building a town:

    python -m pipeline.new_town Beverly MA https://www.beverlyma.gov

It does what the network's ADDING-A-TOWN.md does by hand, step by step, and
leaves the rest marked for a person:

1. What the city uses. A CivicPlus site's Agenda Center, Archive Center, and
   calendars (each calendar's name, and how many of its events are meetings),
   or a CivicClerk portal (<city>.portal.civicclerk.com). A site that answers
   with a browser check ("Just a moment...") stops here: Publick never works
   around one, and asks the town to allow its crawler. So does a meeting
   platform the engine doesn't read, since that reader is its own piece of
   work. Two or three minutes files are downloaded, to see whether they're
   scans, which the model transcribes at a cost every month. SeeClickFix's
   places named for the town are listed; the organization ID is a person's to
   confirm, since the issues listing that showed a city's activity now needs a
   login.
2. Codes. The BLS area (la.area), and the Census place or town derived from
   it, in any state; in Massachusetts also the DLS name and DOR code and the
   DESE district (pipeline/states/ma/lookups.py).
3. Wards. In Massachusetts, MassGIS's 2022 wards and precincts, written as
   data/static/<town>-precincts-2022.geojson, laid out as Malden's is.
4. Officials: an empty skeleton. Never filled in from any source: a person
   does that from the city's own pages.
5. The config, started from the closest live town (the same meeting platform
   and state): its shared settings, the answers above, and colors from the
   city's stylesheet. No [digest]: a town joins the weekly digest once two
   full weeks have passed since launch.
6. Checks, in a copy of the folder, so the town's data/ stays empty: build
   with no data, fetch a few weeks of meetings (--full: everything from
   `since`), build again, run the site checks, and list the meetings shown as
   one (pipeline.listings).

Everything left for a person is a comment starting "# To fill in:" in the
config. The report lists each one, with what was found and where each answer
came from. A town's site checks fail while any is left
(site_checks/test_town_site.py), so a config can't be merged half done.

Usage:
    python -m pipeline.new_town <Town> <state> <city website URL> [--network DIR] [--out DIR]
                                [--report FILE] [--full] [--no-checks]
"""

from __future__ import annotations

import argparse
import colorsys
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from importlib import import_module
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from pypdf import PdfReader

from pipeline import agendacenter, civicclerk, civicplus, fetch_place
from pipeline.config import ENGINE_DIR
from pipeline.fetch_meetings import pdf_has_text
from pipeline.http import FetchError, PoliteClient

# Names Publick, with an address to write to, as BLS asks of automated requests.
USER_AGENT = "Publick town helper (+https://publick.org; hello@publick.org)"
# The New England states, by code: the state's name and its FIPS code.
STATES = {"CT": ("Connecticut", "09"), "MA": ("Massachusetts", "25"), "ME": ("Maine", "23"),
          "NH": ("New Hampshire", "33"), "RI": ("Rhode Island", "44"), "VT": ("Vermont", "50")}
# States whose own codes and wards the helper looks up. The rest are marked to fill in.
LOOKUPS = {"MA": "pipeline.states.ma.lookups"}
FILL_IN = "To fill in:"

BLS_AREAS = "https://download.bls.gov/pub/time.series/la/la.area"
CENSUS_REPORTER = "https://api.censusreporter.org/1.0/geo/latest"
SEECLICKFIX_PLACES = "https://seeclickfix.com/api/v2/places"

# A page that checks the visitor's browser before showing the site (Cloudflare's, Imperva's).
BROWSER_CHECK = re.compile(r"Just a moment(?:\.\.\.|…)|Checking your browser|cf-browser-verification|"
                           r"challenges\.cloudflare\.com|_Incapsula_Resource", re.I)
# Meeting platforms the engine doesn't read, found by their marks on the city's homepage.
OTHER_PLATFORMS = {"Govstack": r"govstack", "Granicus (Legistar)": r"legistar\.com", "BoardDocs": r"boarddocs\.com",
                   "Municode Meetings": r"municodemeetings\.com", "Drupal": r'name="Generator" content="Drupal',
                   "Diligent Community": r"diligent\.community|diligentoneplatform"}
# Platforms the engine reads that the helper doesn't set up: a config by hand, from the town named.
BY_HAND = {"a DotNetNuke calendar ([meetings.dnn], as Manchester's)": r"DotNetNuke|dnn_ctr"}
# A calendar event that is a public body's meeting, by its title.
MEETING = r"\b(boards?|commissions?|committees?|subcommittees?|councils?|authority|trustees|meetings?|hearings?|ZBA|task force)\b"
# Names a city's governing body goes by; the engine assumes "City Council" unless told otherwise.
GOVERNING = ("City Council", "Town Council", "Board of Mayor and Aldermen", "Board of Aldermen", "Select Board",
             "Board of Selectmen", "Selectboard")
SCHOOL_BOARDS = ("School Committee", "Board of Education", "School Board")

# Colors in CivicPlus's own stylesheets, the same on every CivicPlus site (its link blue, its error
# red), so not the city's. A city's colors are those its stylesheets use at least MIN_USES times.
CIVICPLUS_COLORS = {"#0098f7", "#ff0000", "#12639d", "#cc3333", "#0b5486", "#fef7cb", "#f3eec3", "#f6d146", "#a7c6e4"}
MIN_USES = 10
# WCAG AA for text on white.
MIN_CONTRAST = 4.5


class BrowserCheck(Exception):
    """A site answered with a browser check instead of its page."""


class Stop(Exception):
    """Something only a person, or a new reader in the engine, can get past."""


class Client(PoliteClient):
    """The engine's polite client, which also stops at a browser check and counts the connections
    the server dropped (a slower pace in the town's config helps a site that drops them)."""

    def __init__(self, delay: float = 2.0):
        super().__init__(USER_AGENT, delay=delay, timeout=60.0)
        self.resets = 0
        self.session.hooks["response"].append(self._check)
        get = self.session.get

        def counted(*args, **kwargs):
            try:
                return get(*args, **kwargs)
            except requests.ConnectionError:
                self.resets += 1
                raise

        self.session.get = counted

    @staticmethod
    def _check(response, *args, **kwargs):
        if response.headers.get("cf-mitigated") == "challenge" or (
                response.status_code in (403, 429, 503) and BROWSER_CHECK.search(response.text[:20000])):
            raise BrowserCheck(response.url)


def try_get(client, url: str):
    """The response, or None for a page the site doesn't have (or won't give)."""
    try:
        return client.get(url)
    except FetchError:
        return None


@dataclass
class Answer:
    step: int
    what: str
    answer: str
    source: str


@dataclass
class NewTown:
    name: str
    state: str
    url: str
    today: date = field(default_factory=date.today)
    answers: list[Answer] = field(default_factory=list)
    # What was found, for the config: each step's results.
    site: dict = field(default_factory=dict)
    codes: dict = field(default_factory=dict)
    wards: dict = field(default_factory=dict)
    template: dict = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    # Left for a person in the config, each a "To fill in" comment there.
    todo: list[tuple[int, str]] = field(default_factory=list)
    # Left for a person, outside the config.
    notes: list[tuple[int, str]] = field(default_factory=list)
    stopped: str | None = None

    @property
    def slug(self) -> str:
        """The config's name: 'beverly', 'south-kingstown'."""
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")

    @property
    def folder(self) -> str:
        return f"{self.slug}-{self.state.lower()}"

    @property
    def state_name(self) -> str:
        return STATES[self.state][0]

    @property
    def kind(self) -> str:
        """'city' or 'town', as the Census Bureau names the place."""
        return self.codes.get("kind", "city")

    def found(self, step: int, what: str, answer, source: str) -> None:
        self.answers.append(Answer(step, what, str(answer), source))

    def note(self, step: int, text: str) -> None:
        self.notes.append((step, text))


# ---- Step 1: what the city uses ---------------------------------------------------------

def calendar_names(page: str) -> dict[str, str]:
    """A CivicPlus calendar page's calendars, by id: the boxes to pick which calendars to show."""
    found = re.findall(r'<label for="chkCalendarID_(\d+)">\s*<input[^>]*>\s*([^<]+?)\s*</label>', page)
    return {cid: civicplus.clean_text(name) for cid, name in found}


def civicclerk_name(page: str, town: NewTown) -> str:
    """The city's CivicClerk name: the portal its site links, or the usual <city><state> ('nashuanh')."""
    linked = re.search(r"https?://([a-z0-9-]+)\.portal\.civicclerk\.com", page, re.I)
    return linked.group(1).lower() if linked else re.sub(r"[^a-z]", "", town.name.lower()) + town.state.lower()


def find_platform(town: NewTown, client) -> None:
    """Step 1: the city's meeting platforms, read from its own pages."""
    home = client.get(town.url)
    page = home.text
    parts = urlsplit(home.url)
    base = f"{parts.scheme}://{parts.netloc}"
    site = town.site
    site.update(base_url=base, home=page, platforms=[])
    if re.search(r"civicplus", page, re.I):
        site["platforms"].append("CivicPlus")
        town.found(1, "Website", "CivicPlus (the homepage names it)", town.url)
        listing = try_get(client, agendacenter.search_url(base, town.today - timedelta(days=365), town.today + timedelta(days=60)))
        rows = agendacenter.parse_listing(listing.text, base) if listing is not None else []
        if rows:
            site["agenda_center"] = rows
            town.found(1, "Agenda Center", f"{len(rows)} agendas in the last year, under {len({r['category'] for r in rows})} "
                       "categories", f"{base}/AgendaCenter")
        archive = try_get(client, f"{base}/Archive.aspx")
        collections = civicplus.parse_archive_index(archive.text, base) if archive is not None else []
        if collections:
            site["archive"] = collections
            town.found(1, "Archive Center", ", ".join(c["name"] for c in collections), f"{base}/Archive.aspx")
        calendar = try_get(client, f"{base}/Calendar.aspx")
        names = calendar_names(calendar.text) if calendar is not None else {}
        if names:
            site["calendars"] = read_calendars(client, base, names, town.today)
            listed = "; ".join(f"{c['name']} ({c['meetings']} meetings of {c['events']} events)" for c in site["calendars"])
            town.found(1, "City calendars, the next three months", listed, f"{base}/Calendar.aspx")
    name = civicclerk_name(page, town)
    api_url, portal_url = f"https://{name}.api.civicclerk.com/v1", f"https://{name}.portal.civicclerk.com"
    events = read_civicclerk(client, api_url, town.today)
    if events:
        site["civicclerk"] = {"api_url": api_url, "portal_url": portal_url, "events": events}
        town.found(1, "CivicClerk portal", f"{len(events)} meetings in the last year", portal_url)
    if not any(k in site for k in ("agenda_center", "civicclerk")) and not any(c["chosen"] for c in site.get("calendars", [])):
        by_hand = [n for n, mark in BY_HAND.items() if re.search(mark, page, re.I)]
        if by_hand:
            raise Stop(f"{town.url} has {', '.join(by_hand)}. The engine reads it, but the helper doesn't set it up: "
                       "write [meetings] by hand from that town's config.")
        others = [n for n, mark in OTHER_PLATFORMS.items() if re.search(mark, page, re.I)]
        what = f"its meetings on {', '.join(others)}" if others else "no Agenda Center, city calendar of meetings, or CivicClerk portal"
        raise Stop(f"{town.url} has {what}. The engine needs a reader for it first: that's its own piece of work "
                   "(ADDING-A-TOWN.md, step 1).")


def read_calendars(client, base: str, names: dict[str, str], today: date) -> list[dict]:
    """Each of the city's calendars, with how many of its events in this month and the next two are
    meetings. A calendar chosen for the config has some, and they're most of its events."""
    counts = {cid: Counter() for cid in names}
    month = today.replace(day=1)
    for _ in range(3):
        response = try_get(client, civicplus.list_url(base, month))
        for e in civicplus.parse_list(response.text, base) if response is not None else []:
            if e["calendar_id"] in counts:
                counts[e["calendar_id"]]["events"] += 1
                counts[e["calendar_id"]]["meetings"] += bool(re.search(MEETING, e["raw_title"], re.I))
        month = (month + timedelta(days=32)).replace(day=1)
    calendars = []
    for cid, name in names.items():
        events, meetings = counts[cid]["events"], counts[cid]["meetings"]
        calendars.append({"id": cid, "name": name, "events": events, "meetings": meetings,
                          "chosen": meetings > 0 and meetings * 2 >= events})
    return calendars


def read_civicclerk(client, api_url: str, today: date, pages: int = 20) -> list[dict]:
    """The portal's events of the last year (up to `pages` pages), or none if the city has no portal."""
    url, events = civicclerk.events_url(api_url, today - timedelta(days=365)), []
    for _ in range(pages):
        try:
            data = client.get(url).json()
        except (FetchError, ValueError):
            break
        events += [e for e in data.get("value", []) if not e.get("isDeleted")]
        url = civicclerk.next_page(data)
        if not url:
            break
    return events


def categories(town: NewTown) -> Counter:
    """The boards the platforms list meetings under: the Agenda Center's categories, the portal's."""
    found = Counter(r["category"] for r in town.site.get("agenda_center", []))
    found.update(e.get("categoryName") or e.get("eventCategoryName") or "" for e in town.site.get("civicclerk", {}).get("events", []))
    found.pop("", None)
    return found


def governing_body(town: NewTown) -> str | None:
    listed = {c.lower(): c for c in categories(town)}
    return next((g for g in GOVERNING if g.lower() in listed), None)


def school_board(town: NewTown) -> str | None:
    return next((c for c in categories(town) if any(b.lower() in c.lower() for b in SCHOOL_BOARDS)), None)


def minutes_files(town: NewTown) -> list[dict]:
    """Minutes posted for meetings in the last year, newest first: date, board, and address."""
    site, since = town.site, (town.today - timedelta(days=365)).isoformat()
    found = [{"date": r["date"], "board": r["category"], "url": r["minutes_url"]}
             for r in site.get("agenda_center", []) if r["minutes_url"] and r["date"] >= since]
    for e in site.get("civicclerk", {}).get("events", []):
        files = civicclerk.documents(e, site["civicclerk"]["api_url"])
        if "minutes_url" in files and e["startDateTime"][:10] >= since:
            found.append({"date": e["startDateTime"][:10], "board": e.get("categoryName") or e.get("eventName", ""),
                          "url": files["minutes_url"]})
    if not found:
        for c in site.get("archive", []):
            if civicplus.collection_body(c["name"])[1] == "minutes":
                found += [{"date": i["date"], "board": civicplus.collection_body(c["name"])[0], "url": i["url"]}
                          for i in c["items"] if i["date"] and i["date"] >= since]
    return sorted(found, key=lambda m: m["date"], reverse=True)


def check_minutes(town: NewTown, client, sample: int = 3) -> None:
    """Download a few of the main board's newest minutes: text, or scans the model transcribes?"""
    files = minutes_files(town)
    if not files:
        town.note(1, "No minutes posted in the last year were found: check where the city posts them.")
        return
    main = governing_body(town)
    chosen = ([m for m in files if main and m["board"].lower() == main.lower()] or files)[:sample]
    read = []
    for m in chosen:
        response = try_get(client, m["url"])
        if response is None:
            town.note(1, f"The minutes at {m['url']} could not be downloaded.")
            continue
        if not response.content.startswith(b"%PDF"):
            read.append({**m, "pdf": False})
            continue
        try:
            pages = len(PdfReader(io.BytesIO(response.content)).pages)
        except Exception:
            pages = None
        read.append({**m, "pdf": True, "pages": pages, "text": pdf_has_text(response.content)})
    town.site["minutes"] = {"count": len(files), "read": read}
    for m in read:
        what = ("not a PDF (minutes as Word files or web pages aren't read)" if not m["pdf"]
                else f"{m['pages']} pages, " + ("text" if m["text"] else "a scan" if m["text"] is False else "unreadable"))
        town.found(1, f"Minutes, {m['board']} {m['date']}", what, m["url"])
    scans = [m for m in read if m["pdf"] and m["text"] is False]
    if scans:
        per_file = sum(m["pages"] or 0 for m in scans) / len(scans)
        pages = round(len(files) * len(scans) / len(read) * per_file)
        town.found(1, "Transcription", f"about {pages} pages a year: {len(files)} minutes posted in the last year, "
                   f"{len(scans)} of {len(read)} read were scans, {per_file:.0f} pages each", "the minutes above")
        town.note(1, f"Minutes are scans: the model would transcribe about {pages} pages a year, at a cost every month.")


def find_seeclickfix(town: NewTown, client) -> None:
    """SeeClickFix's places named for the town, and whether the city's site links SeeClickFix."""
    url = f"{SEECLICKFIX_PLACES}?{urlencode({'address': f'{town.name}, {town.state}'})}"
    try:
        places = client.get(url).json().get("places", [])
    except (FetchError, ValueError):
        places = []
    names = {town.name.lower(), f"city of {town.name.lower()}", f"town of {town.name.lower()}"}
    candidates = [p for p in places if p.get("state") == town.state and p.get("name", "").lower() in names]
    linked = re.search(r'https?://(?:www\.)?seeclickfix\.com/[^"\'\s<>]+', town.site.get("home", ""))
    town.site["seeclickfix"] = {"places": candidates, "linked": linked.group(0) if linked else None}
    if candidates:
        town.found(1, "SeeClickFix places", "; ".join(f"{p['name']} (place {p['id']}, {p['html_url']})" for p in candidates), url)
    if linked:
        town.found(1, "SeeClickFix on the city's site", linked.group(0), town.url)


# ---- Colors from the city's stylesheets ------------------------------------------------

def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def contrast(color: str) -> float:
    """The color's contrast ratio with white (WCAG)."""
    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(color))
    return 1.05 / (0.2126 * r + 0.7152 * g + 0.0722 * b + 0.05)


def scaled(color: str, factor: float) -> str:
    return "#" + "".join(f"{round(c * factor * 255):02x}" for c in _rgb(color))


def darkened(color: str, target: float = MIN_CONTRAST) -> str:
    """The color, darker by as little as it takes to reach the target contrast with white."""
    factor = 1.0
    while contrast(scaled(color, factor)) < target and factor > 0:
        factor -= 0.01
    return scaled(color, factor)


def tint(color: str, amount: float = 0.1) -> str:
    """A light tint: the color a tenth of the way from white, as primary_soft is."""
    return "#" + "".join(f"{round(255 - (255 - c * 255) * amount):02x}" for c in _rgb(color))


def hue(color: str) -> float:
    return colorsys.rgb_to_hls(*_rgb(color))[0] * 360


def _colorful(color: str) -> bool:
    channels = _rgb(color)
    return max(channels) - min(channels) > 0.16


def stylesheet_colors(css: str) -> Counter:
    """How often each six-digit color is used ('#fff' counts as '#ffffff')."""
    found = Counter()
    for c in re.findall(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", css):
        c = c.lower()
        found["#" + "".join(ch * 2 for ch in c[1:]) if len(c) == 4 else c] += 1
    return found


def pick_colors(counts: Counter) -> tuple[dict, list[str]] | None:
    """[site.colors] from a site's stylesheets, and how each was chosen. The city's colors are its
    most used colorful ones (not CivicPlus's own). The most used and the next most used of a close hue
    are primary (the lighter) and primary_dark (the darker); accent is the most used of another hue,
    neither too light nor near black. A color under 4.5:1 on white is darkened until it passes."""
    colors = [c for c, n in counts.most_common() if n >= MIN_USES and _colorful(c) and c not in CIVICPLUS_COLORS]
    if not colors:
        return None
    first = colors[0]
    family = [c for c in colors if min(abs(hue(c) - hue(first)), 360 - abs(hue(c) - hue(first))) <= 30][:2]
    how = []
    if len(family) == 2:
        dark, primary = sorted(family, key=contrast, reverse=True)
        how.append(f"its two most used {_name(first)}s ({counts[family[0]]} and {counts[family[1]]} uses)")
    else:
        primary, dark = first, darkened(first, 15.0)
        how.append(f"its most used color ({counts[first]} uses), and a darker shade of it")
    others = [c for c in colors if c not in family and min(abs(hue(c) - hue(first)), 360 - abs(hue(c) - hue(first))) > 30
              and contrast(c) <= 11]
    accent = None
    if others:
        accent = others[0]
        how.append(f"the most used of another hue ({counts[accent]} uses)")
    picked = {}
    for name, color in (("primary", primary), ("primary_dark", dark), ("accent", accent)):
        if not color:
            continue
        if contrast(color) < MIN_CONTRAST:
            how.append(f"{name} darkened from {color} ({contrast(color):.1f} to 1) to pass")
            color = darkened(color)
        picked[name] = color
    picked = {"primary": picked["primary"], "primary_dark": picked["primary_dark"],
              "primary_soft": tint(picked["primary"]), **({"accent": picked["accent"]} if "accent" in picked else {})}
    how.append("primary_soft a light tint of primary")
    return picked, how


def _name(color: str) -> str:
    """A rough name for a hue, for the config's comment."""
    h = hue(color)
    for limit, name in ((15, "red"), (45, "orange"), (70, "yellow"), (160, "green"), (190, "teal"), (255, "blue"),
                        (290, "purple"), (335, "magenta"), (360, "red")):
        if h <= limit:
            return name
    return "color"


def find_colors(town: NewTown, client) -> None:
    """[site.colors] from the stylesheets the city's homepage links."""
    links = re.findall(r"<link\b[^>]*>", town.site.get("home", ""), re.I)
    hrefs = [re.search(r'href="([^"]+\.css[^"]*)"', link) for link in links
             if re.search(r'rel="stylesheet"', link, re.I) and not re.search(r'media="print"', link, re.I)]
    counts = Counter()
    host = urlsplit(town.site["base_url"]).netloc
    for href in [h.group(1) for h in hrefs if h][:8]:
        url = urljoin(town.site["base_url"] + "/", href)
        if urlsplit(url).netloc != host or re.search(r"/App_Themes/ie\d|jquery", url, re.I):
            continue
        response = try_get(client, url)
        if response is not None:
            counts.update(stylesheet_colors(response.text))
    picked = pick_colors(counts)
    if picked:
        town.site["colors"], town.site["colors_how"] = picked
        town.found(1, "Colors", ", ".join(f"{k} {v} ({contrast(v):.1f} to 1)" for k, v in picked[0].items()),
                   f"{host}'s stylesheets")


# ---- Step 2: codes ----------------------------------------------------------------------

def bls_area(name: str, state: str, text: str) -> tuple[str, str] | None:
    """The town's BLS area code and name: '<Town> city, MA' (a Census place, CT...), else
    '<Town> town, MA' (a town, CS...)."""
    rows = {}
    for line in text.splitlines()[1:]:
        cells = line.rstrip("\r").split("\t")
        if len(cells) > 2:
            rows[cells[2].lower()] = cells[1]
    for kind in ("city", "town"):
        area = f"{name} {kind}, {state}"
        if area.lower() in rows:
            return rows[area.lower()], area
    return None


def find_codes(town: NewTown, client, network: Path) -> None:
    """Step 2: the BLS series and the Census place or town, then the state's own codes."""
    fips = STATES[town.state][1]
    try:
        area = bls_area(town.name, town.state, client.get(BLS_AREAS).text)
    except FetchError as e:
        area = None
        town.note(2, f"BLS's area list could not be read ({e}).")
    codes = town.codes
    if area:
        code, area_name = area
        codes["labor"] = {"local_series": f"LAU{code}03", "area": code, "area_name": area_name}
        town.found(2, "BLS area", f"{code} ({area_name}); series LAU{code}03", BLS_AREAS)
        if code.startswith("CT"):
            codes["housing"] = {"bps_state": fips, "bps_place": code[4:9], "census_geo": f"16000US{fips}{code[4:9]}"}
        else:
            # A town (county subdivision): its Census code also has its county's, from TIGERweb.
            url = f"{fetch_place.TIGERWEB}/1/query?" + urlencode({"where": f"STATE='{fips}' AND COUSUB='{code[4:9]}'",
                                                                   "outFields": "GEOID", "returnGeometry": "false", "f": "json"})
            response = try_get(client, url)
            features = response.json().get("features") if response is not None else None
            if features:
                codes["housing"] = {"bps_state": fips, "bps_mcd": code[4:9],
                                    "census_geo": f"06000US{features[0]['attributes']['GEOID']}"}
    housing = codes.get("housing")
    if housing:
        check_census(town, client, housing)
    lookups = LOOKUPS.get(town.state)
    if lookups:
        state_codes(town, client, network, import_module(lookups))


def check_census(town: NewTown, client, housing: dict) -> None:
    """Whether Census Reporter has figures for the place (the housing page's), and whether it's a
    city or a town by law (TIGERweb, as pipeline.fetch_place reads it)."""
    geo = housing["census_geo"]
    response = try_get(client, f"{CENSUS_REPORTER}/{geo}")
    properties = response.json().get("properties", {}) if response is not None else {}
    if properties.get("population"):
        town.found(2, "Census place", f"{geo} ({properties.get('display_name')}, population {properties['population']:,})",
                   f"{CENSUS_REPORTER}/{geo}")
    else:
        housing["census_geo_unknown"] = True
        town.found(2, "Census place", f"{geo}: Census Reporter has no figures for it", f"{CENSUS_REPORTER}/{geo}")
    try:
        place = fetch_place.parse(client.get(fetch_place.query_url(geo)).json())
        town.codes["kind"] = place["kind"]
        town.found(2, "City or town", f"{place['name']}: a {place['kind']}", fetch_place.query_url(geo))
    except (FetchError, ValueError):
        town.note(2, "Whether the place is a city or a town could not be read from TIGERweb; the config says city.")


def state_codes(town: NewTown, client, network: Path, lookups) -> None:
    """Massachusetts's DLS name and code and DESE district."""
    dls = lookups.dls_town(town.name, network / "states" / "ma" / "dls")
    if dls:
        town.codes["finance"] = {"dls_municipality": dls["dls_municipality"], "dls_code": dls["dls_code"]}
        town.found(2, "DLS name and DOR code", f"{dls['dls_municipality']}, {dls['dls_code']}", f"states/ma/dls/{dls['file']}")
    url = lookups.districts_url(town.name)
    try:
        rows = client.get(url).json()
    except (FetchError, ValueError):
        rows = []
    district = lookups.district(town.name, dls and dls["dls_code"], rows)
    if district:
        town.codes["schools"] = district
        town.found(2, "DESE district", f"{district['district_code']} ({district['district_name']})", url)
    elif rows:
        town.codes["districts"] = rows


# ---- Step 3: wards ----------------------------------------------------------------------

def find_wards(town: NewTown, client) -> None:
    lookups = LOOKUPS.get(town.state)
    if not lookups:
        return
    lookups = import_module(lookups)
    url = lookups.wards_url(town.name)
    try:
        made = lookups.ward_file(town.name, client.get(url).json())
    except (FetchError, ValueError):
        made = None
    if made:
        text, wards = made
        count = len(json.loads(text)["features"])
        town.wards = {"file": f"{town.slug}-precincts-2022.geojson", "text": text, "wards": wards, "map": lookups.MASSGIS_MAP}
        town.found(3, "Wards and precincts", f"{count} precincts" + ("" if wards else ", no wards"), url)


# ---- Step 5: the config -----------------------------------------------------------------

def network_configs(network: Path) -> list[tuple[str, dict]]:
    found = []
    for path in sorted(network.glob("towns/*/config/*.toml")):
        with path.open("rb") as f:
            found.append((path.parent.parent.name, tomllib.load(f)))
    return found


def readers(config: dict) -> set[str]:
    """A config's meeting readers: agenda_center, civicplus, civicclerk..."""
    meetings = config.get("meetings", {})
    return {k for k in ("agenda_center", "civicplus", "civicclerk", "dnn", "file_list") if k in meetings} | (
        {"civicplus"} if "calendar_feed" in meetings else set())


def closest_town(town: NewTown, network: Path) -> None:
    """The live town to start from: the same meeting platform and state, else the same platform,
    else the same state; among those, the one whose list was checked last (the newest)."""
    mine = town_readers(town)
    best = None
    for folder, config in network_configs(network):
        if folder == town.folder:
            continue
        same_state = config.get("town", {}).get("state_abbr") == town.state
        theirs = readers(config)
        score = (2 * bool(mine & theirs) + same_state + (mine == theirs),
                 str(config.get("officials", {}).get("checked", "")))
        if best is None or score > best[0]:
            best = (score, folder, config)
    if best:
        town.template = {"folder": best[1], "config": best[2], "same_state": best[2]["town"]["state_abbr"] == town.state}
        town.found(5, "Started from", f"{best[2]['town']['name']}'s config", f"towns/{best[1]}/config/")


def town_readers(town: NewTown) -> set[str]:
    site = town.site
    return ({"agenda_center"} if "agenda_center" in site else set()) | (
        {"civicplus"} if any(c["chosen"] for c in site.get("calendars", [])) else set()) | (
        {"civicclerk"} if "civicclerk" in site else set())


def left_to_fill_in(text: str) -> list[str]:
    """A config's "To fill in" comments, each with its line number."""
    return [f"line {n}: {line.strip()}" for n, line in enumerate(text.splitlines(), 1) if FILL_IN in line]


def toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


class Config:
    """A config's text, written line by line, and what it leaves to fill in."""

    def __init__(self):
        self.lines: list[str] = []
        self.todo: list[tuple[int, str]] = []

    def comment(self, text: str) -> None:
        self.lines += textwrap.wrap(text, 78, initial_indent="# ", subsequent_indent="# ", break_on_hyphens=False,
                                    break_long_words=False)

    def fill_in(self, step: int, text: str) -> None:
        """A comment for a person: "# To fill in: ...", its next lines indented under it."""
        self.lines += textwrap.wrap(f"{FILL_IN} {text}", 78, initial_indent="# ", subsequent_indent="#   ",
                                    break_on_hyphens=False, break_long_words=False)
        self.todo.append((step, text))

    def table(self, name: str, comment: str = "", /, **values) -> None:
        if self.lines:
            self.lines.append("")
        if comment:
            self.comment(comment)
        self.lines.append(f"[{name}]")
        self.set(**values)

    def set(self, **values) -> None:
        self.lines += [f"{k} = {toml(v)}" for k, v in values.items() if v is not None]

    def blank(self) -> None:
        self.lines.append("")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def shared(town: NewTown, table: str, *keys: str, same_state: bool = False) -> dict:
    """Settings every town shares, from the town the config starts from."""
    if not town.template or (same_state and not town.template["same_state"]):
        return {}
    values = town.template["config"].get(table, {})
    return {k: values[k] for k in keys if k in values}


def first_of_month_back(today: date, months: int) -> date:
    month = today.month - months
    return date(today.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def config_text(town: NewTown, short: bool = False) -> Config:
    """The town's config. With short, only a few weeks of meetings are fetched (step 6's copy)."""
    c = Config()
    site, template = town.site, town.template
    place, where = town.kind, f"{town.name}, {town.state_name}"
    domain = f"{town.folder}.publick.org"
    since = date(town.today.year, 1, 1)
    fetch_since = (town.today - timedelta(days=28)) if short else since
    summaries_since = first_of_month_back(town.today, 3)
    meetings = town_readers(town)
    governing = governing_body(town) or ("Town Council" if place == "town" else "City Council")

    c.comment(f"Site configuration for {where}. Everything town-specific lives here; the code is the Publick "
              "engine (github.com/publick-org/publick-engine).")
    c.lines.append("#")
    c.comment(f"Started on {town.today.isoformat()} by the engine's helper (python -m pipeline.new_town)"
              + (f", from {template['config']['town']['name']}'s config." if template else "."))
    c.fill_in(5, f"what the {place} uses for meetings, minutes, 311, and its school board, in a few sentences, as "
              "other towns' configs begin.")
    c.lines.append("#")
    c.comment("The site is in English and Spanish ([site] languages), as every town's is. The first runs draft the "
              "Spanish for the town's own text; [strings.es], at the end, can correct it.")
    c.blank()
    c.lines.append("[site]")
    c.set(name=f"{town.name} Publick")
    c.comment("The masthead shows name_prefix in dark ink and name_suffix in the accent color: the town, then the network.")
    c.set(name_prefix=f"{town.name} ", name_suffix="Publick", domain=domain)
    s = shared(town, "site", "network", "network_url", "repo_url", "timezone", "languages")
    c.comment("The network of town sites this one belongs to, named in every page footer.")
    c.set(network=s.get("network", "Publick"), network_url=s.get("network_url", "https://publick.org"),
          tagline=f"Public data on how {where} decides and spends. Updated daily.")
    c.comment("One line under the name at the top of every page.")
    c.set(masthead=f"An independent guide to {place} government in {where}", repo_url=s.get("repo_url"))
    c.comment("Public contact address. Shown on the About and Accessibility pages when set.")
    c.set(contact_email=f"{town.folder}@publick.org", timezone=s.get("timezone", "America/New_York"),
          user_agent=f"{domain} (+https://{domain}/about/)")
    c.comment("English at the site's root, Spanish under /es/.")
    c.set(languages=s.get("languages", ["en", "es"]))

    if site.get("colors"):
        colors = site["colors"]
        ratios = ", ".join(f"{contrast(v):.1f}" for v in colors.values() if v != colors["primary_soft"])
        c.blank()
        c.comment(f"From the {place} website's stylesheets ({urlsplit(site['base_url']).netloc}): "
                  + "; ".join(site["colors_how"]) + f". Each passes WCAG AA on white ({ratios} to 1).")
        c.fill_in(5, f"check the colors against the {place}'s website.")
        c.lines.append("[site.colors]")
        c.set(**colors)
    else:
        c.blank()
        c.fill_in(5, f"[site.colors], the most used colors in the {place} website's stylesheet, each 4.5:1 on white.")

    c.table("town", name=town.name, state=town.state_name, state_abbr=town.state, official_site=town.url.rstrip("/"))
    if town.wards and not town.wards["wards"]:
        c.comment(f"{town.name} has no wards: its areas are its voting precincts (MassGIS's 2022 file has no WARD).")
        c.set(areas="precincts")

    c.blank()
    c.comment("Sections appear in the main navigation in this order. Each one is a folder under site/pages/<slug>/ "
              "and is served at https://<domain>/<slug>/.")
    sections = {s["slug"]: s for s in (template["config"].get("sections", []) if template else [])}
    what = (f"on the {place} calendar and in its Agenda Center" if {"civicplus", "agenda_center"} <= meetings
            else f"in the {place}'s Agenda Center" if "agenda_center" in meetings
            else f"on the {place} calendar" if "civicplus" in meetings else "")
    summary = (f"Every board and committee meeting {what}, with agendas, and minutes from {since:%B %Y} on." if what
               else f"When the {governing}, its committees, and the {place}'s boards and commissions meet, with agendas, "
                    f"and minutes from {since:%B %Y} on.")
    order = [("meetings", {"title": "Meetings", "summary": summary})]
    if town.state in LOOKUPS:
        order += [(slug, sections[slug]) for slug in ("schools", "budget", "housing") if slug in sections]
    for slug, section in order:
        c.lines += ["[[sections]]", f"slug = {toml(slug)}"]
        c.set(**{k: section[k] for k in ("title", "nav", "summary") if k in section}, status="live")
        c.blank()
    c.fill_in(4, "the Officials page's section (below, commented out), once [officials] is filled in.")
    c.lines += ["# [[sections]]", '# slug = "officials"', '# title = "Who represents you"', '# nav = "Officials"',
                f'# summary = "The mayor, {governing}, and School Committee, by office and by ward, and how to reach them."',
                '# status = "live"', ""]
    about = sections.get("about", {"title": "About", "summary": "Data sources, update schedule, metric definitions, and corrections."})
    c.lines += ["[[sections]]", 'slug = "about"']
    c.set(title=about["title"], summary=about["summary"], status="live")

    a = shared(town, "analytics", "goatcounter", "public_stats")
    c.table("analytics", "Page view counting with GoatCounter, on the network's site; prefix is this town's folder.",
            goatcounter=a.get("goatcounter", "publick"), prefix=town.folder, public_stats=a.get("public_stats", ""))

    config_meetings(c, town, governing, fetch_since, short)

    st = shared(town, "storage", "endpoint", "bucket", "public_url")
    c.table("storage", "Agenda and minutes PDFs live in the network's documents bucket, not in git (see "
            "pipeline/documents.py). Keys: STORAGE_ACCESS_KEY_ID, STORAGE_SECRET_ACCESS_KEY.", **st, prefix=town.folder)
    if not st:
        c.fill_in(5, "the bucket's endpoint, bucket and public_url, as every town's.")
    sm = shared(town, "summaries", "model", "max_per_run", "input_price", "output_price", "max_cost_per_run")
    c.table("summaries", "Plain-English agenda text and previews, and decisions from minutes, written by a language "
            "model and labeled as AI-generated on the site. Only meetings from `since` on are summarized, about three "
            "months before launch, so the town's history doesn't take the month's budget.",
            model=sm.get("model"), since=summaries_since.isoformat(), max_per_run=sm.get("max_per_run"))
    c.comment("Prices per million tokens, for the spending limit below.")
    c.set(input_price=sm.get("input_price"), output_price=sm.get("output_price"))
    c.comment("A ceiling for one run. The network's monthly summary budget (SUMMARY_BUDGET in "
              ".github/workflows/network.yml) decides what each run may spend, new documents first.")
    c.set(max_cost_per_run=sm.get("max_cost_per_run"))

    c.blank()
    c.fill_in(5, f'how residents can take part, per board ([participation."{governing}"], [participation."School '
              'Committee"]): comment, source_title, source_url, video_title, video_url, each from the board\'s own '
              "rules or agendas.")
    c.fill_in(5, "[[glossary]], terms that come up in this city's agendas, each from an official page.")

    config_codes(c, town)
    sources = [{"label": f"Meetings ({'Agenda Center' if 'agenda_center' in meetings else 'CivicClerk' if 'civicclerk' in meetings else 'city calendar'})",
                "file": "meetings/status.json", "max_days": 2}]
    fr = shared(town, "freshness", "summary_grace_days")
    c.table("freshness", "How often each data source should update. Figures published monthly or yearly are judged by "
            "the engine, by the period they cover (pipeline/rhythms.py).",
            summary_grace_days=fr.get("summary_grace_days", 2))
    c.lines.append("sources = [")
    c.lines += [f"  {{ label = {toml(s['label'])}, file = {toml(s['file'])}, max_days = {s['max_days']} }}," for s in sources]
    c.lines.append("]")

    config_311(c, town)
    config_officials(c, town, governing)
    return c


def config_meetings(c: Config, town: NewTown, governing: str, since: date, short: bool) -> None:
    site, place = town.site, town.kind
    base = site["base_url"]
    if "agenda_center" in site:
        archive_url, archive_name = f"{base}/AgendaCenter", f"{place}'s Agenda Center"
    elif "civicclerk" in site:
        archive_url, archive_name = site["civicclerk"]["portal_url"], f"{place}'s CivicClerk portal"
    else:
        archive_url, archive_name = f"{base}/Archive.aspx", f"{place}'s Archive Center"
    readers_found = [n for k, n in (("civicplus", f"the {place} calendar"), ("agenda_center", "its Agenda Center"),
                                    ("civicclerk", "its CivicClerk portal")) if k in town_readers(town)]
    c.table("meetings", f"Public meetings, from {' and '.join(readers_found)}.",
            base_url=base if "civicclerk" not in town_readers(town) or "agenda_center" in site else None,
            calendar_url=site["civicclerk"]["portal_url"] if town_readers(town) == {"civicclerk"} else None,
            archive_url=archive_url, archive_name=archive_name,
            governing_body=governing if governing != "City Council" else None)
    if site.get("resets"):
        c.comment(f"The {place}'s site resets some connections ({site['resets']} of the helper's); a slower pace and "
                  "the client's retries help.")
        c.set(request_delay=5.0)
    elif "CivicPlus" in site.get("platforms", []):
        c.comment("CivicPlus sites reset some connections (Beverly's, Lawrence's, Lowell's, Methuen's, Waltham's); a "
                  "slower pace and the client's retries help.")
        c.set(request_delay=5.0)
    else:
        c.set(request_delay=3.0)
    c.set(max_event_pages=10 if short else 40)
    if "civicclerk" in site and town_readers(town) == {"civicclerk"}:
        boards = sorted({e.get("categoryName") for e in site["civicclerk"]["events"] if e.get("categoryName")})
        c.fill_in(5, "check each of the portal's categories below is a public body, named as the city names it, and "
                  "leave out any that isn't.")
        c.lines.append("boards = [")
        c.lines += [f"  {toml(b)}," for b in boards]
        c.lines.append("]")

    calendars = site.get("calendars", [])
    chosen = [cal for cal in calendars if cal["chosen"]]
    if chosen:
        others = [cal["name"] for cal in calendars if not cal["chosen"]]
        c.table("meetings.civicplus", f"The {place} calendar, read month by month: the meetings the {place} lists "
                f"before their agendas are posted, with their times and places. Read: the calendars whose events, "
                f"over the next three months, were mostly meetings."
                + (f" Not read: {', '.join(others)}." if others else ""), calendars=[cal["name"] for cal in chosen])
        if any(cal["meetings"] < cal["events"] for cal in chosen):
            c.comment("Some of their events aren't meetings: only titles naming a board or a meeting are kept.")
            c.lines.append(f"include_pattern = '{MEETING}'")
        if "agenda_center" in site:
            c.comment("A meeting on the calendar and in the Agenda Center is shown once.")
        c.fill_in(5, "check the calendars read, and any left out that list meetings.")

    c.table("meetings.aliases")
    c.fill_in(5, "board names the engine doesn't fix itself: a calendar meeting next to an Agenda Center one of the "
              "same day, not shown as one, needs an alias (python -m pipeline.listings, in the report).")

    if "agenda_center" in site:
        c.table("meetings.agenda_center", base_url=base)
        c.comment("The first run lists meetings from here, and minutes are collected from here on.")
        c.set(since=since.isoformat())
        main = [r for r in site["agenda_center"] if r["category"].lower() == governing.lower()
                and re.search(r"committee", r["title"], re.I) and not re.search(r"of the whole", r["title"], re.I)]
        if main:
            titles = {re.sub(r"\s*\(PDF\)?$|\s*\(PDF$", "", r["title"]).strip(): None for r in main}
            titles = "; ".join(f'"{t}"' for t in sorted(titles, key=len)[:3])
            c.blank()
            c.fill_in(5, f'[meetings.agenda_center.committees."{governing}"]: committees posted under the {governing}, '
                      f"each by a phrase in its titles ({titles}).")
    if "civicclerk" in site:
        cc = site["civicclerk"]
        c.table("meetings.civicclerk", api_url=cc["api_url"], portal_url=cc["portal_url"])
        c.comment("Meetings, agendas, and minutes are collected from here on.")
        c.set(since=since.isoformat())
    if "archive" in site and "agenda_center" not in site and "civicclerk" not in site:
        c.table("archive", "Minutes from the city's Archive Center. Minutes for meetings on or after minutes_since are "
                "collected.", index="Archive.aspx", minutes_since=since.isoformat())
        c.fill_in(5, "[archive.aliases], the Archive Center's collection names for boards the calendar names otherwise.")

    board = school_board(town)
    if board:
        c.blank()
        c.comment(f"The {board} posts with the other boards (\"{board}\").")
    else:
        c.blank()
        c.fill_in(1, "the School Committee's meetings: where the district posts them ([ical_meetings], "
                  "[page_meetings], [finalsite_meetings], [drive_meetings]), named in the Meetings section's "
                  "summary and in [freshness] sources; a platform the engine doesn't read is left out and said so "
                  "(ADDING-A-TOWN.md, step 1).")


def config_codes(c: Config, town: NewTown) -> None:
    codes, state = town.codes, town.state
    package = f"pipeline/states/{state.lower()}/__init__.py"
    if "schools" in codes:
        c.table("schools", "School district figures from the state's education data portal (DESE).", **codes["schools"])
    else:
        c.blank()
        listed = "; ".join(f"{r['dist_code']} {r['dist_name']}" for r in codes.get("districts", []))
        c.fill_in(2, f"[schools], the town's school district (the keys in the engine's {package})"
                  + (f": the portal lists {listed}." if listed else "."))
    if "finance" in codes:
        c.table("finance", "Tax bill and budget figures (Mass. Division of Local Services). Some DLS reports take the "
                "name, others the DOR code.", **codes["finance"])
    else:
        c.blank()
        c.fill_in(2, f"[finance], the state's codes for the town (the keys in the engine's {package}).")

    housing = codes.get("housing")
    h = shared(town, "housing", "bps_region", "bps_prefix", "state_geo", "shi_url", same_state=True)
    if housing:
        c.table("housing", "Housing: Census building permits (state and place FIPS codes), American Community Survey "
                "through Census Reporter (geo ids)"
                + (", and the state's Subsidized Housing Inventory PDF (the town's name as the PDF lists it)." if "shi_url" in h else "."),
                bps_region=h.get("bps_region", "Northeast Region"), bps_prefix=h.get("bps_prefix", "ne"),
                bps_state=housing["bps_state"], bps_place=housing.get("bps_place"), bps_mcd=housing.get("bps_mcd"))
        if housing.get("census_geo_unknown"):
            c.fill_in(2, f"census_geo: Census Reporter has no figures for {housing['census_geo']}, the place BLS "
                      f"lists; find the town's place on censusreporter.org (Methuen's is \"Methuen Town\").")
            c.lines.append(f"# census_geo = {toml(housing['census_geo'])}")
        else:
            c.set(census_geo=housing["census_geo"])
        c.set(state_geo=h.get("state_geo", f"04000US{STATES[state][1]}"), shi_url=h.get("shi_url"),
              shi_name=town.name if "shi_url" in h else None)
    else:
        c.blank()
        c.fill_in(2, "[housing], the Census place or town (ADDING-A-TOWN.md, step 2): BLS's area list has no "
                  f"\"{town.name} city, {state}\" or \"{town.name} town, {state}\".")

    labor = codes.get("labor")
    lb = shared(town, "labor", "state_series", "bulk_file", same_state=True)
    if labor:
        c.table("labor", f"Unemployment rate (BLS Local Area Unemployment Statistics, area {labor['area']}, "
                f"{labor['area_name']}). City rates are not seasonally adjusted; the state series is shown for context.",
                local_series=labor["local_series"], state_series=lb.get("state_series", f"LAUST{STATES[state][1]}0000000000003"),
                bulk_file=lb.get("bulk_file"))
    else:
        c.blank()
        c.fill_in(2, "[labor] local_series, from BLS's area list (download.bls.gov/pub/time.series/la/la.area).")


def config_311(c: Config, town: NewTown) -> None:
    scf = town.site.get("seeclickfix", {})
    if scf.get("places") or scf.get("linked"):
        c.blank()
        places = "; ".join(f"{p['name']}, place {p['id']} ({p['html_url']})" for p in scf.get("places", []))
        c.fill_in(1, "[seeclickfix], with the city's organization ID, once a person has confirmed the city uses it "
                  "(the issues listing needs a login now; the Open311 listing at "
                  "seeclickfix.com/open311/v2/<id>/services.json names its request types), and the 311 section and "
                  "its [freshness] sources (with 311 the tagline can say \"responds, decides, and spends\", as "
                  "Gloucester's, Malden's, and Methuen's do); or, if the city takes requests some other way, "
                  "[absences] 311 saying so. "
                  + (f"SeeClickFix's places: {places}." if places else "")
                  + (f" The city's site links {scf['linked']}." if scf.get("linked") else ""))
    else:
        c.table("absences")
        c.fill_in(1, "311, why the town has no 311 section (the system the city uses instead), as the About page "
                  "says: no SeeClickFix place is named for it.")


def config_officials(c: Config, town: NewTown, governing: str) -> None:
    c.table("officials", "Who represents you (the Officials page). Kept by hand from official city and school "
            "websites; checked is the date the list was last checked against them. Only city- and school-issued "
            "email addresses, and the mayor's office phone.")
    c.fill_in(4, "checked, the date the list below was checked against official pages.")
    c.fill_in(4, "next_election, the date of the town's next regular municipal election, from the city's election "
              "calendar or its charter.")
    if town.wards:
        if town.wards["wards"]:
            c.comment(f"The ward file is data/static/{town.wards['file']} (MassGIS).")
            c.set(wards_file=town.wards["file"], wards_publisher="MassGIS", wards_year="2022", wards_url=town.wards["map"])
        else:
            c.comment(f"No wards: data/static/{town.wards['file']} (MassGIS) has the voting precincts, for 311.")
    else:
        c.fill_in(3, "wards_file, the town's wards (ADDING-A-TOWN.md, step 3), or none if every seat is elected "
                  "townwide.")
    for body in ("Mayor", governing, "School Committee"):
        c.blank()
        c.fill_in(4, f"the {body}, from the {town.kind}'s and the school district's own pages, confirmed against "
                  "the last election's official results (ADDING-A-TOWN.md, step 4). Never from news sites or memory.")
        c.lines += ["# [[officials.bodies]]", f"# name = {toml(body)}", '# url = ""', '# note = ""', "# members = [",
                    '#   { name = "", seat = "" },', "# ]"]


# ---- Step 6: checks ---------------------------------------------------------------------

def run_checks(town: NewTown, folder: Path, full: bool, runner=subprocess.run) -> None:
    """Build, fetch, build again, check, and list, in a copy of the folder (its data/ stays empty), and
    draw the town's share image (site/static/share/<town>.png) in the folder itself."""
    work = Path(tempfile.mkdtemp(prefix=f"new-town-{town.folder}-"))
    shutil.copytree(folder, work, dirs_exist_ok=True)
    if not full:
        (work / "config" / f"{town.slug}.toml").write_text(config_text(town, short=True).text(), encoding="utf-8")
    env = {**os.environ, "PUBLICK_TOWN_DIR": str(work), "TOWN": town.slug, "DOCUMENTS_LOCAL": "1",
           "PYTHONPATH": os.pathsep.join(filter(None, [str(ENGINE_DIR), os.environ.get("PYTHONPATH")]))}
    python = sys.executable
    site = str(work / "_site")
    # Everything but the check that nothing is left to fill in: the report lists what is.
    pytest = [python, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(ENGINE_DIR / "site_checks"),
              "-k", "not test_nothing_left_to_fill_in"]
    steps = [
        ("Build with no data", [python, "-m", "pipeline.build_site", "--out", site, "--no-share-cards"]),
        ("Fetch meetings" + ("" if full else " (four weeks)"), [python, "-m", "pipeline.fetch_meetings"]),
        ("Build with meetings", [python, "-m", "pipeline.build_site", "--out", site, "--no-share-cards"]),
        ("Site checks", pytest),
        ("Meetings shown as one", [python, "-m", "pipeline.listings"]),
        # The share image is the town's own, so it goes in its folder, not the copy.
        ("Share image", [python, "-m", "pipeline.make_share_image"]),
    ]
    for name, command in steps:
        extra = ({"PUBLICK_SITE_DIR": site, "PUBLICK_CHECK_PAGES": "sample"} if name == "Site checks"
                 else {"PUBLICK_TOWN_DIR": str(folder.resolve())} if name == "Share image" else {})
        try:
            result = runner(command, cwd=work, env={**env, **extra}, capture_output=True, text=True, timeout=3600)
            ok, output = result.returncode == 0, (result.stdout or "") + (result.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as e:
            ok, output = False, str(e)
        town.checks.append({"name": name, "ok": ok, "output": output.strip()})
        if not ok and name.startswith("Build"):
            break
    boards = Counter(m["body"] for m in json.loads((work / "data" / "meetings" / "meetings.json").read_text())
                     .values()) if (work / "data" / "meetings" / "meetings.json").exists() else Counter()
    town.site["boards"] = boards
    shutil.rmtree(work, ignore_errors=True)


# ---- The report -------------------------------------------------------------------------

def report(town: NewTown, todo: list[tuple[int, str]]) -> str:
    lines = [f"# New town: {town.name}, {town.state_name}", "",
             f"`python -m pipeline.new_town {town.name} {town.state} {town.url}`, {town.today.isoformat()}."]
    if town.stopped:
        lines += ["", f"**Stopped:** {town.stopped}"]
    lines += ["", "## What was found", "", "| Step | What | Answer | Source |", "|---|---|---|---|"]
    lines += [f"| {a.step} | {a.what} | {a.answer.replace('|', '/')} | {a.source} |" for a in town.answers]
    if town.checks:
        lines += ["", "## Checks", ""]
        lines += [f"- {c['name']}: {'passed' if c['ok'] else 'failed'}" for c in town.checks]
        boards = town.site.get("boards")
        if boards:
            lines += ["", "Boards recorded by the fetch (check each is under its usual name):", ""]
            lines += [f"- {b} ({n})" for b, n in sorted(boards.items())]
        for c in town.checks:
            if c["output"] and (not c["ok"] or c["name"] == "Meetings shown as one"):
                lines += ["", f"<details><summary>{c['name']}</summary>", "", "```", c["output"][-6000:], "```", "",
                          "</details>"]
    left = sorted(todo + town.notes, key=lambda t: t[0])
    if left:
        lines += ["", "## Left for a person", ""]
        lines += [f"{i}. Step {step}: {text}" for i, (step, text) in enumerate(left, 1)]
    lines += ["", "Not in the config until two full weeks after launch: the weekly digest ([digest], and the town in "
              "DIGEST_TOWNS in wrangler.scheduler.toml)."]
    return "\n".join(lines) + "\n"


# ---- Running it -------------------------------------------------------------------------

def run(town: NewTown, client, network: Path, out: Path, checks: bool = True, full: bool = False) -> int:
    """Every step for a town. Returns 0 when its folder is written, 2 when it stopped at step 1."""
    try:
        find_platform(town, client)
    except BrowserCheck as e:
        town.stopped = (f"{e} answered with a browser check, which stops automated reading. Ask the town to allow "
                        "Publick's crawler; never work around the check.")
        return 2
    except Stop as e:
        town.stopped = str(e)
        return 2
    except FetchError as e:
        town.stopped = f"The city's website could not be read: {e}"
        return 2
    for step in (check_minutes, find_seeclickfix, find_colors):
        try:
            step(town, client)
        except BrowserCheck as e:
            town.note(1, f"{e} answered with a browser check; never work around it.")
    town.site["resets"] = client.resets
    find_codes(town, client, network)
    find_wards(town, client)
    closest_town(town, network)
    config = config_text(town)
    (out / "config").mkdir(parents=True)
    (out / "config" / f"{town.slug}.toml").write_text(config.text(), encoding="utf-8")
    (out / "data" / "static").mkdir(parents=True)
    if town.wards:
        (out / "data" / "static" / town.wards["file"]).write_text(town.wards["text"], encoding="utf-8")
    town.todo = config.todo
    if checks:
        run_checks(town, out, full)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("town", help='the town\'s name, as the state writes it ("Beverly")')
    parser.add_argument("state", help="its state's two-letter code")
    parser.add_argument("url", help="the city's website")
    parser.add_argument("--network", type=Path, default=Path("."), help="the network repository (default: here)")
    parser.add_argument("--out", type=Path, help="where to write the town's folder (default: towns/<town>-<state>/)")
    parser.add_argument("--report", type=Path, help="where to write the report (default: new-town-<town>-<state>.md)")
    parser.add_argument("--full", action="store_true", help="fetch every meeting from `since`, not four weeks")
    parser.add_argument("--no-checks", action="store_true", help="write the folder without step 6's checks")
    args = parser.parse_args()
    state = args.state.upper()
    if state not in STATES:
        raise SystemExit(f"{args.state}: the helper knows the New England states ({', '.join(STATES)}).")
    town = NewTown(args.town, state, args.url)
    out = args.out or args.network / "towns" / town.folder
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} already exists: the helper starts a town, never changes one.")
    code = run(town, Client(), args.network, out, checks=not args.no_checks, full=args.full)
    path = args.report or Path(f"new-town-{town.folder}.md")
    path.write_text(report(town, town.todo), encoding="utf-8")
    print(path.read_text(encoding="utf-8"))
    print(f"Wrote {path}" + ("" if code else f" and {out}/"))
    return code


if __name__ == "__main__":
    sys.exit(main())
