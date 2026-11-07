"""Addresses on agendas and minutes, meeting by meeting: what the street lookup lists, what each street's
and each address's feed carries (/streets/feeds/), and the last RECENT_DAYS days' new mentions as one
file (/streets/mentions.json), so they can be read without reading every feed.

An address is found in a document's own text without AI (streets.addresses_in), and its street written
one way (streets.street_keys). A mention is left out where the line is the document's header or
letterhead rather than an item:

- a line naming the meeting room or the town's own mailing address ("Conference Room", "Room 204",
  "City Hall", "2 Dale Ave, Gloucester, MA");
- an address followed by another town and its state: an applicant's own ("11 Azsr Ct, Halethorpe MD");
- the meeting's own place, from its listing;
- an address alone on its line (perhaps with the town, state and zip code), or given as the way in
  ("Parking Lot Entrance at 75 Sabin Street"), in VENUE_MEETINGS meetings' documents: where boards
  meet, or a letterhead. Once is an item ("the crosswalk at Gorton's parking lot entrance");
- an address in the first HEADER_LINES lines of VENUE_MEETINGS of one board's documents: that board's
  place (a school board's office), left out for that board only. A case heard again is lower down,
  under the items.

Each mention names the house numbers it covers (numbers()): a range is each number in it ("62-66" is 62,
64 and 66) and a list each one ("171 & 281R"), so "62 Main St" is found in "62-66 Main Street".

A mention is new for an address when its board hasn't listed that address with the same line before
(new()): an item carried from agenda to agenda, as a council's list of pending papers is, comes up once.
The minutes, which word it otherwise, come up again, as what was decided. Each feed carries new mentions
only.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from email.utils import format_datetime

from pipeline import streets as streets_mod
from pipeline.i18n import _, plain_date

# An address alone on its line in this many meetings' documents is where they meet, and one in the
# first HEADER_LINES lines of this many of one board's documents is where that board meets.
VENUE_MEETINGS = 3
HEADER_LINES = 8
# The most house numbers a range stands for ("2-40 Main St" is 20); a wider one is its two ends.
MAX_RANGE = 25
# The most items in a feed, and the most characters of a line.
FEED_ITEMS = 50
LINE_CHARS = 200
# How many days of new mentions /streets/mentions.json keeps.
RECENT_DAYS = 30
KINDS = ("agenda", "minutes")


def clip(text: str, limit: int = LINE_CHARS) -> str:
    """Shorten to a whole word within limit characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:—– ") + "…"


def numbers(written: str) -> list[str]:
    """The house numbers a mention's number stands for: '62-66' -> 62, 64, 66 (a range keeps its
    numbers' side of the street: odd or even); '171 & 281R' -> 171, 281R; '228/230' -> 228, 230. A range
    wider than MAX_RANGE numbers, or one with a letter, is its two ends."""
    written = written.strip().lstrip("#").upper()
    out: list[str] = []
    for part in re.split(r"\s*[&/,]\s*|\s+", written):
        ends = [e for e in re.split(r"\s*[-–]\s*", part) if e]
        if len(ends) == 2 and all(e.isdigit() for e in ends) and int(ends[0]) < int(ends[1]):
            first, last = int(ends[0]), int(ends[1])
            step = 2 if first % 2 == last % 2 else 1
            span = range(first, last + 1, step)
            out += [str(n) for n in span] if len(span) <= MAX_RANGE else ends
        else:
            out += ends
    return list(dict.fromkeys(n.lstrip("0") or "0" for n in out if re.fullmatch(r"\d+[A-Z]?", n)))


def address_key(number: str, street: str) -> str:
    """'62', 'MAIN ST' -> '62 MAIN ST': one address, as /streets/mentions.json names it."""
    return f"{number} {street}"


def slug(street: str) -> str:
    """'EASTERN POINT BLVD' -> 'eastern-point-blvd', for a feed's path."""
    return re.sub(r"[^a-z0-9]+", "-", street.lower()).strip("-")


def feed_path(street: str, number: str | None = None) -> str:
    """A street's feed, or one address's on it: /streets/feeds/main-st.xml, /streets/feeds/main-st/62.xml."""
    return f"/streets/feeds/{slug(street)}" + (f"/{number.lower()}.xml" if number else ".xml")


def find(meetings: list[dict], town: dict, text_of) -> list[dict]:
    """Every address on the meetings' agendas and minutes, oldest first, without the headers and
    letterheads above. text_of gives a summary record's document text (build_site.doc_text). Each
    mention: the meeting's url, date and board (body, as shown, and body_en), the document's kind and
    when it was posted, the address as written, its street keys and house numbers, and its line."""

    def place(address: str) -> tuple[str, list[str]]:
        number = streets_mod.HOUSE_NUMBER.match(address.upper() + " ")
        return (number.group(0).strip() if number else ""), streets_mod.street_keys(address, town["name"])

    def lines_of(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.strip()]

    def line_with(lines: list[str], address: str) -> tuple[int, str]:
        at = next((i for i, line in enumerate(lines) if address in line), None)
        return (len(lines) if at is None else at), clip(re.sub(r"[#*_>|]+", " ", lines[at] if at is not None else address).strip())

    venue_line = re.compile(r"\b(?:conference room|meeting room|auditorium|council chambers?|city hall|held at"
                            r"|room \d+)\b"
                            rf"|,\s*{re.escape(town['name'])},?\s*{re.escape(town['state_abbr'])}\b", re.I)
    elsewhere = re.compile(r",\s*([A-Z][A-Za-z.'’]*(?:\s+[A-Z][A-Za-z.'’]*){0,2}),?\s+[A-Z]{2}\b")
    way_in = re.compile(r"\b(?:parking (?:lot )?entrance|enter ?(?:the )?parking lot|entrance at|entrance on)\b", re.I)
    own_place = re.compile(rf"\b(?:{re.escape(town['name'])}|{re.escape(town['state_abbr'])})\b|[\W\d_]", re.I)

    docs = []
    for m in meetings:
        for kind, record, doc in (("agenda", m.get("preview"), m.get("agenda")), ("minutes", m.get("minutes_summary"), m.get("minutes_doc"))):
            text = text_of(record)
            if text:
                docs.append((m, kind, doc or {}, lines_of(text), streets_mod.addresses_in(text)))

    # Meeting places (City Hall, the high school library) head every agenda; they aren't news.
    venues = {(num, key) for m in meetings for num, keys in [place(m.get("address") or "")] for key in keys}
    alone, headed = defaultdict(set), defaultdict(set)
    for m, kind, doc, lines, found in docs:
        for address in found:
            at, line = line_with(lines, address)
            flat = re.sub(r"\s+", " ", address)
            num, keys = place(address)
            for key in keys:
                if not own_place.sub("", re.sub(r"\s+", " ", line).replace(flat, "", 1)) or way_in.search(line):
                    alone[(num, key)].add(m["url"])
                if at < HEADER_LINES:
                    headed[(num, key, m.get("body_en", m["body"]))].add(m["url"])
    venues |= {where for where, urls in alone.items() if len(urls) >= VENUE_MEETINGS}

    board_places = {where for where, urls in headed.items() if len(urls) >= VENUE_MEETINGS}

    out = []
    for m, kind, doc, lines, found in docs:
        board = m.get("body_en", m["body"])
        for address in found:
            _at, line = line_with(lines, address)
            num, keys = place(address)
            after = elsewhere.match(line[line.find(address) + len(address):]) if address in line else None
            if venue_line.search(line) or (after and after.group(1).lower() != town["name"].lower()):
                continue
            keys = [k for k in keys if (num, k) not in venues and (num, k, board) not in board_places]
            if keys:
                out.append({"url": m["url"], "date": m["date"], "body": m["body"], "body_en": board, "kind": kind,
                            "posted": doc.get("fetched_at") or m["date"], "address": address, "streets": keys,
                            "numbers": numbers(num) if num else [], "line": line})
    out.sort(key=lambda x: (x["posted"], x["date"], KINDS.index(x["kind"])))
    return out


def same_line(line: str) -> str:
    """A line as compared from one meeting to the next: its words, without the item's number or letter."""
    text = line.lower()
    while True:
        shorter = re.sub(r"^\W*(?:\d{1,3}|[a-z]|[ivx]{1,4})[.)]\s+", "", text)
        if shorter == text:
            break
        text = shorter
    return " ".join(re.findall(r"[a-z0-9]+", text))


def new(found: list[dict]) -> list[dict]:
    """Each mention with "new": the addresses, as (street, number), whose board hasn't listed them with
    this line before, and "street_new": the streets, for a mention with no house number, likewise.
    found is find()'s, oldest first."""
    seen: set[tuple] = set()
    for x in found:
        line = same_line(x["line"])
        x["new"], x["street_new"] = [], []
        for street in x["streets"]:
            for what, into in ([((street, n), x["new"]) for n in x["numbers"]] or [(street, x["street_new"])]):
                if (what, x["body_en"], line) not in seen:
                    seen.add((what, x["body_en"], line))
                    into.append(what)
    return found


def newest_first(x: dict) -> tuple:
    """A feed's order, reversed: the day its document was collected, then its meeting's date, so a board's
    minutes collected together in one run are in the order they were met."""
    return x["posted"][:10], x["date"], KINDS.index(x["kind"])


def item_title(x: dict, what: str) -> str:
    when = plain_date(date.fromisoformat(x["date"]))
    title = (_("{board}: agenda for {date}") if x["kind"] == "agenda" else _("{board}: minutes of {date}")).format(board=x["body"], date=when)
    return f"{title} – {what}"


def write_feeds(site: Path | None, found: list[dict], config: dict, base_url: str) -> dict:
    """A feed for each street with a new mention, and for each address on it (feed_path()), each its
    newest FEED_ITEMS, written into the built site's folder site (None: none written). A feed's content
    changes only when a mention comes or goes: it has no build time, so a daily run leaves it as it was
    and publishing doesn't upload it again. Returns, by street key, its feed and its addresses'
    ([number, feed], in house-number order), for the street lookup."""
    by_street, by_address = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for x in found:
        for street in x["streets"]:
            numbers_new = [n for s, n in x["new"] if s == street]
            if numbers_new or street in x["street_new"]:
                by_street[street].append(x)
            for number in numbers_new:
                by_address[street][number].append(x)
    site_name, town = config["site"]["name"], config["town"]["name"]

    def posted(x: dict) -> datetime:
        return datetime.fromisoformat(x["posted"] if "T" in x["posted"] else x["posted"] + "T12:00:00")

    def write(path: str, title: str, description: str, link: str, items: list[tuple[str, dict]]) -> None:
        if site is None:
            return
        entries = "".join(
            f"<item><title>{xml_escape(what)}</title><link>{base_url}{x['url']}</link>"
            f"<guid isPermaLink=\"false\">{base_url}{x['url']}#{x['kind']}-{xml_escape(slug(x['address']))}</guid>"
            f"<pubDate>{format_datetime(posted(x))}</pubDate>"
            f"<description>{xml_escape(x['line'])}</description></item>\n"
            for what, x in items)
        target = site / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n'
            f"<title>{xml_escape(site_name)}: {xml_escape(title)}</title><link>{base_url}{link}</link>"
            f"<description>{xml_escape(description)}</description><language>en</language>\n{entries}</channel></rss>\n",
            encoding="utf-8")

    out = {}
    for street, items in sorted(by_street.items()):
        name = streets_mod.street_name(street)
        query = "/streets/?q=" + name.replace(" ", "+")
        newest = sorted(items, key=newest_first, reverse=True)[:FEED_ITEMS]
        write(feed_path(street), name,
              _("Each agenda and set of minutes that names an address on {street}, in {town}.").format(street=name, town=town),
              query, [(item_title(x, x["address"]), x) for x in newest])
        addresses = []
        for number in sorted(by_address[street], key=lambda n: (int(re.match(r"\d+", n).group(0)), n)):
            label = f"{number} {name}"
            newest = sorted(by_address[street][number], key=newest_first, reverse=True)[:FEED_ITEMS]
            write(feed_path(street, number), label,
                  _("Each agenda and set of minutes that names {address}, in {town}.").format(address=label, town=town),
                  query, [(item_title(x, label), x) for x in newest])
            addresses.append([number, feed_path(street, number)])
        out[street] = {"feed": feed_path(street), "addresses": addresses}
    return out


def recent(found: list[dict], built_at: datetime, days: int = RECENT_DAYS) -> dict:
    """The new mentions posted in the last days, newest first, for /streets/mentions.json: each with its
    addresses ('62 MAIN ST') or, with no house number, its streets, its board and document, its meeting
    and when the document was posted, and its line."""
    since = (built_at - timedelta(days=days)).isoformat()
    items = [{"addresses": [address_key(n, s) for s, n in x["new"]], "streets": x["street_new"], "board": x["body_en"], "kind": x["kind"],
              "date": x["date"], "url": x["url"], "posted": x["posted"], "address": x["address"], "line": x["line"]}
             for x in found if (x["new"] or x["street_new"]) and x["posted"] >= since]
    return {"format": 1, "days": days, "mentions": sorted(items, key=lambda i: i["posted"], reverse=True)}
