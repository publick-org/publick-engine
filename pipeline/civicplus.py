"""Parsers for CivicPlus (CivicEngage) city websites.

Gloucester posts each public meeting as a calendar event. The calendar RSS
feed lists upcoming events; each event's page carries the start time,
location, remote-attendance link, and a "Download Agenda" link into the
city's Archive Center.

The RSS feed lists a fixed number of events (Gloucester's 20, Beverly's 10),
community events included, so it reaches only a week or two ahead. The
calendar's list view (Calendar.aspx?CID=0&view=list&month=11&year=2026)
lists every event in a month, under each of the city's calendars ("City
Meetings", "Board of Health"), with its date, time, place and address.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from urllib.parse import urljoin

import feedparser

TIME_PREFIX = re.compile(r"^\s*\d{1,2}:\d{2}\s*[AP]\.?M\.?\s*[-–:]?\s*", re.I)
TIME_SUFFIX = re.compile(r",?\s*\d{1,2}:\d{2}\s*[AP]\.?M\.?\s*$", re.I)
PLACEHOLDER_LOCATIONS = {"", "event location"}
STATUS_WORDS = {
    "cancelled": re.compile(r"\bcancell?ed\b", re.I),
    "postponed": re.compile(r"\bpostponed\b", re.I),
    "rescheduled": re.compile(r"\brescheduled\b", re.I),
}


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<br\s*/?>", ", ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*,(\s*,)*\s*", ", ", value)
    return value.strip(" ,")


def parse_title(title: str) -> dict:
    """Split a calendar title like '6:00 PM Licensing Board Special Meeting'."""
    raw = clean_text(title)
    status = next((s for s, rx in STATUS_WORDS.items() if rx.search(raw)), "scheduled")
    name = raw
    for rx in STATUS_WORDS.values():
        name = rx.sub("", name)
    name = re.sub(r"^[\s\-–:*]+|[\s\-–:*]+$", "", name)
    name = TIME_SUFFIX.sub("", TIME_PREFIX.sub("", name))
    name = re.sub(r"^[\s\-–:*]+|[\s\-–:*]+$", "", name)
    name = re.sub(r"\s{2,}", " ", name)
    if name.isupper():
        name = tidy_case(name)
    special = bool(re.search(r"\bspecial\b", name, re.I))
    # The board, without what the clerk added after it ("Planning Board - Public
    # Hearing", "License Board Meeting - Agenda - October 1, 2026") or in brackets
    # ("Community Preservation Committee (CPC)"), or the kind of meeting.
    body = re.split(r"\s+[-–]+\s+", re.sub(r"\s*\([^)]*\)", "", name))[0]
    body = re.sub(r"^regular\s+", "", body, flags=re.I)
    body = re.sub(r"\s+(?:(?:regular|special)\s+)?(?:meeting|public hearings?)$|\s+regular$", "", body, flags=re.I)
    body = re.sub(r"\bspecial\s+", "", body, flags=re.I).strip() or name
    return {"title": name, "body": body, "status": status, "special": special}


SMALL_WORDS = {"a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to"}


def tidy_case(name: str) -> str:
    """'LAWRENCE SCHOOL COMMITTEE' -> 'Lawrence School Committee'. Short words other
    than the small ones are taken for abbreviations ('ZBA', 'LHA') and kept."""
    out = []
    for i, word in enumerate(name.split(" ")):
        low = word.lower()
        if low in SMALL_WORDS and i:
            out.append(low)
        elif len(word.strip("[]()")) <= 3 and word.isupper():
            out.append(word)
        else:
            out.append(word.capitalize())
    return " ".join(out)


def parse_time(value: str) -> str | None:
    """'06:00 PM' -> '18:00'."""
    value = value.strip().upper().replace(".", "")
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            pass
    return None


def parse_times(value: str) -> tuple[str | None, str | None]:
    parts = [p for p in re.split(r"\s*-\s*", value or "") if p.strip()]
    start = parse_time(parts[0]) if parts else None
    end = parse_time(parts[1]) if len(parts) > 1 else None
    # CivicPlus fills a missing end time with 11:59 PM; an all-day event is 12:00 AM - 11:59 PM.
    if end == "23:59":
        end = None
    if start == "00:00" and end is None:
        start = None
    return start, end


def parse_calendar_feed(content: bytes | str, base_url: str) -> list[dict]:
    feed = feedparser.parse(content)
    events = []
    for entry in feed.entries:
        link = urljoin(base_url, entry.get("link", ""))
        match = re.search(r"EID=(\d+)", link)
        if not match:
            continue
        try:
            date = datetime.strptime(entry.get("calendarevent_eventdates", "").strip(), "%B %d, %Y").date()
        except ValueError:
            continue
        start, end = parse_times(entry.get("calendarevent_eventtimes", ""))
        event = {
            "id": match.group(1),
            "source_url": f"{base_url.rstrip('/')}/Calendar.aspx?EID={match.group(1)}",
            "raw_title": clean_text(entry.get("title")),
            "date": date.isoformat(),
            "start_time": start,
            "end_time": end,
            "location": clean_text(entry.get("calendarevent_location")),
        }
        event.update(parse_title(entry.get("title", "")))
        events.append(event)
    return events


def parse_event_page(page: str, base_url: str) -> dict:
    """Pull structured details from a Calendar.aspx?EID=... page."""
    details: dict = {}

    agenda = re.search(r'id="[^"]*lnkDownloadAgenda"[^>]*href="([^"]+)"', page, re.I)
    if agenda:
        url = urljoin(base_url, html.unescape(agenda.group(1)))
        adid = re.search(r"ADID=(\d+)", url)
        details["agenda_url"] = url
        details["agenda_id"] = adid.group(1) if adid else None

    start = re.search(r'itemprop="startDate"[^>]*>\s*([0-9T:\-]+)\s*<', page)
    if start:
        details["start"] = start.group(1)

    name = re.search(r'itemprop="name"[^>]*>(.*?)</div>', page, re.S)
    # "Event Location" is the CivicPlus placeholder when no facility is set.
    if name and clean_text(name.group(1)).lower() not in PLACEHOLDER_LOCATIONS:
        details["location_name"] = clean_text(name.group(1))

    address = re.search(r'itemprop="address"[^>]*>(.*?)</span>\s*</div>', page, re.S)
    if address:
        details["address"] = clean_text(address.group(1))

    link = re.search(r'id="[^"]*lnkLink"[^>]*href="([^"]+)"', page, re.I)
    if link:
        details["remote_url"] = html.unescape(link.group(1))

    return details


# ---- Calendar list view ----------------------------------------------------

LIST_CALENDAR = re.compile(r'<div id="CID(\d+)" class="calendar">')
LIST_EVENT = re.compile(r'<h3>\s*<a id="eventTitle_(\d+)"[^>]*>(.*?)</a>', re.S)
LIST_DATE = re.compile(r'<div class="date">(.*?)</div>', re.S)
LIST_PLACE = re.compile(r'itemprop="location"[^>]*>\s*<span itemprop="name">(.*?)</span>', re.S)
LIST_NOTE = re.compile(r'<div class="name">\s*<(?:p|ul|div|strong)\b', re.S)
ADDRESS_PART = re.compile(r'itemprop="(streetAddress|addressLocality|addressRegion|postalCode)">(.*?)</span>', re.S)


def list_url(base_url: str, month: date) -> str:
    """Every event of the month on every one of the city's calendars."""
    return f"{base_url.rstrip('/')}/Calendar.aspx?CID=0&view=list&month={month.month}&year={month.year}"


def list_address(fragment: str) -> str:
    """'3 Pond Rd', 'Gloucester', 'MA', '01930' -> '3 Pond Rd, Gloucester, MA 01930'.
    Only the town and state ('Beverly, MA') isn't an address."""
    parts = {k: clean_text(v) for k, v in ADDRESS_PART.findall(fragment)}
    if not parts.get("streetAddress"):
        return ""
    region = " ".join(p for p in (parts.get("addressRegion"), parts.get("postalCode")) if p)
    return ", ".join(p for p in (parts.get("streetAddress"), parts.get("addressLocality"), region) if p)


def parse_list(page: str, base_url: str) -> list[dict]:
    """Every event in a month's list view, with the calendar it is on.

    The date line reads 'November 12, 2026, 5:30 PM - 8:30 PM' or '..., All Day'.
    The place is the event's facility, given in the page's hidden schema.org
    data; where a city typed a note in the location box instead ("This event is
    virtual..."), there is none, and the note isn't taken for a place."""
    base = base_url.rstrip("/")
    heads = list(LIST_CALENDAR.finditer(page))
    events = []
    for i, head in enumerate(heads):
        section = page[head.end():heads[i + 1].start() if i + 1 < len(heads) else len(page)]
        title = re.search(r'<h2 class="title">(.*?)</h2>', section, re.S)
        items = list(LIST_EVENT.finditer(section))
        for j, item in enumerate(items):
            chunk = section[item.end():items[j + 1].start() if j + 1 < len(items) else len(section)]
            when = LIST_DATE.search(chunk)
            parts = [p.strip() for p in clean_text(when.group(1)).split(",")] if when else []
            try:
                day = datetime.strptime(", ".join(parts[:2]), "%B %d, %Y").date()
            except ValueError:
                continue
            times = ",".join(parts[2:])
            start, end = (None, None) if re.search(r"all day", times, re.I) else parse_times(times.replace("\u2009", " "))
            # A note typed in the location box (a paragraph of instructions) isn't a place.
            place = None if LIST_NOTE.search(chunk) else LIST_PLACE.search(chunk)
            location_name = clean_text(place.group(1)) if place else ""
            event = {
                "id": item.group(1),
                "calendar_id": head.group(1),
                "calendar": clean_text(title.group(1)) if title else "",
                "source_url": f"{base}/Calendar.aspx?EID={item.group(1)}",
                "raw_title": clean_text(item.group(2)),
                "date": day.isoformat(),
                "start_time": start,
                "end_time": end,
                "location_name": "" if location_name.lower() in PLACEHOLDER_LOCATIONS else location_name,
                "address": list_address(chunk),
            }
            event.update(parse_title(item.group(2)))
            events.append(event)
    return events


# ---- Archive Center -------------------------------------------------------

MONTH_DATE = re.compile(r"\b([A-Z][a-zA-Z]{2,8})\.?\s*(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
ISO_DATE = re.compile(r"(?<!\d)(\d{4})[-_.](\d{2})[-_.](\d{2})(?!\d)")
NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?!\d)")


def parse_title_date(title: str) -> str | None:
    """Find the meeting date in an archive item title such as 'May 21, 2026 REVISED'
    or 'Affordable Housing Trust Minutes 3-9-2026'. Returns YYYY-MM-DD or None."""
    m = MONTH_DATE.search(title)
    if m:
        month = m.group(1).capitalize()
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(f"{month} {m.group(2)} {m.group(3)}", fmt).date().isoformat()
            except ValueError:
                pass
    m = ISO_DATE.search(title)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            return None
    m = NUMERIC_DATE.search(title)
    if m:
        year = int(m.group(3))
        year = year + 2000 if year < 100 else year
        try:
            return datetime(year, int(m.group(1)), int(m.group(2))).date().isoformat()
        except ValueError:
            return None
    return None


def parse_archive_index(page: str, base_url: str) -> list[dict]:
    """Parse the Archive Center main page (Archive.aspx).

    Returns one entry per collection with its most recent items (the page
    lists about 15 per collection)."""
    collections = []
    for m in re.finditer(
        r'<label for="amidDDN(\d+)">(.*?)</label>\s*</span>\s*<br>\s*<select[^>]*>(.*?)</select>', page, re.S
    ):
        amid, name, options = m.group(1), clean_text(m.group(2)).rstrip(":").strip(), m.group(3)
        name = re.sub(r"\s{2,}", " ", name)
        items = []
        for adid, title in re.findall(r'<option value="1_\d_0_(\d+)">([^<]*)</option>', options):
            title = clean_text(title)
            items.append({
                "id": adid,
                "title": title,
                "date": parse_title_date(title),
                "url": f"{base_url.rstrip('/')}/Archive.aspx?ADID={adid}",
            })
        collections.append({"id": amid, "name": name, "items": items})
    return collections


def collection_body(name: str) -> tuple[str, str]:
    """Split a collection name into (public body, document kind).

    'Planning Board - Minutes' -> ('Planning Board', 'minutes')
    'City Council Agendas and Packets' -> ('City Council', 'agendas')
    'Finance Committee - Minutes 2026' -> ('Finance Committee', 'minutes'): a collection of one
    year's documents, or of a range of years ('Planning Board Minutes 2026 - 2030')"""
    years = r"(?:\s+\d{4}(?:\s*-\s*\d{4})?)?"
    kinds = [
        (r"\s*-?\s*Meeting Results" + years + "$", "results"),
        (r"\s*-?\s*Minutes" + years + "$", "minutes"),
        (r"\s*-?\s*Agendas(?: and Packets)?" + years + "$", "agendas"),
    ]
    for pattern, kind in kinds:
        if re.search(pattern, name, re.I):
            return re.sub(pattern, "", name, flags=re.I).strip(" :-"), kind
    return name.strip(" :-"), "other"
