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

CivicPlus's newer calendar answers at /m/calendar, and a city that has it
redirects the addresses above there: a month's list to
/m/calendar?startDate=2026-11-01&endDate=2026-11-30&cat=0, an event's page to
/m/calendar/event/detail/13065 (the event keeps its number). Its list is by
day and gives no address; the event's page does. parse_list() and
parse_event_page() read either.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from urllib.parse import unquote_plus, urljoin, urlsplit

import feedparser

from pipeline import dates

# A time of day: "6:30 PM", "6:30pm", "10:00 a.m.", "6 PM", or "6:30" with neither.
CLOCK = r"(?:\d{1,2}(?::\d{2}(?!\d))?\s*[AP]\.?\s?M\.?(?![a-z])|\d{1,2}:\d{2}(?!\d))"
# One at the start or end of a meeting's name says when, not which board: "6:30 Licensing Board Meeting",
# "Planning Board, 7:00 PM". The meeting's time is the listing's own.
TIME_PREFIX = re.compile(rf"^\s*(?:at\s+)?{CLOCK}\s*[-–:,@]?\s*", re.I)
TIME_SUFFIX = re.compile(rf"[\s,\-–:@]*(?:\bat\s+)?{CLOCK}\s*$", re.I)
PLACEHOLDER_LOCATIONS = {"", "event location"}
STATUS_WORDS = {
    "cancelled": re.compile(r"\bcancell?ed\b", re.I),
    "postponed": re.compile(r"\bpostponed\b", re.I),
    "rescheduled": re.compile(r"\brescheduled\b", re.I),
}
# What a status word in a listing's name stands next to when it labels the meeting: "CANCELED -
# Committee on Finance", "Policy Committee (Cancelled)", "Licensing Board Agenda POSTPONED to February
# 26", "Meeting Canceled due to lack of quorum", "This meeting was cancelled."
LABEL_BEFORE = re.compile(r"(?:^|[-–:(\[*.!|]|\bmeeting|\bthis meeting (?:is|was|has been|will be))$", re.I)
LABEL_AFTER = re.compile(r"^(?:$|[-–:)\]*!|]|\.\s*$)")
# Or tells of another meeting: "... in place of the regular meeting that was canceled."
ANOTHER_MEETING = re.compile(r"\b(?:that|which) (?:was|were|had been|has been)$", re.I)


def labels(text: str, match: re.Match) -> bool:
    """Whether a status word found in a listing's name labels the meeting it names, rather than
    telling of another one: "Special Meeting. This meeting is the rescheduling of the October 1
    regular meeting that was canceled due to ..." was held. The word labels it at the start or end of
    the name, set off by punctuation, after "meeting", or in capitals in a name that isn't; not after
    "that was"."""
    before, after = text[:match.start()].rstrip(), text[match.end():].lstrip()
    if ANOTHER_MEETING.search(before):
        return False
    return bool(LABEL_BEFORE.search(before) or LABEL_AFTER.match(after)
                or (match.group().isupper() and not text.isupper()))


def status_of(text: str) -> str:
    """A meeting's status from its listing's name: the first status word that labels it."""
    return next((s for s, rx in STATUS_WORDS.items()
                 if any(labels(text, m) for m in rx.finditer(text))), "scheduled")


def without_status(text: str) -> str:
    """A listing's name without the status words that label its meeting."""
    for rx in STATUS_WORDS.values():
        text = rx.sub(lambda m: "" if labels(text, m) else m.group(), text)
    return text


# What a clerk writes after a board's name that names one of its committees: "City Council - Ordinance
# Committee", "City Council - Committee of the Whole" (Lawrence's calendar). The meeting is the committee's.
OF_BOARD = re.compile(r"^[\w&,/' ]*\b(?:sub)?committee(?: of the whole)?(?:\s+meeting)?$", re.I)
# A committee's meeting posted as the full council's too, so a quorum of it may attend: "Committee on
# Administration and Finance co-posted with the Committee of the Whole". The meeting is the committee's.
CO_POSTED = re.compile(r"\s*,?\s*\bco-?posted\b.*$", re.I)


def board_part(name: str) -> str:
    """A meeting's name without what says when, or who else it's posted for, rather than which board:
    'Planning Board Meeting, December 17, 2026' -> 'Planning Board Meeting'. A date written into a
    name isn't part of the board's: "Board of Health Meeting 10/13/2026", or what's left of
    "RESCHEDULED TO 10/21/2026 Commission on Disabilities Meeting" or "RESCHEDULED to 10-14-Beverly
    Regional Airport Commission" once its status word is taken out; with or without its year. Nor is
    a time at its start or end ("6:30 Licensing Board Meeting"), after its date is taken out."""
    name = CO_POSTED.sub("", dates.without_dates(name, yearless=True))
    name = TIME_PREFIX.sub("", TIME_SUFFIX.sub("", name))
    return re.sub(r"\s{2,}", " ", re.sub(r"^[\s,\-–:]+|[\s,\-–:]+$", "", name))


# Town Meeting is a board, "Meeting" and all, as is a Representative Town Meeting. The words before it that
# say which of its sessions this is aren't part of its name: "Annual", "Special", "Adjourned", "Open", a
# season, a month, a year or an ordinal ("Annual Fall Town Meeting", "October Special Town Meeting", "Second
# Special Town Meeting", "First Session of the Annual Town Meeting"); nor is a session's number or
# "Continued" after it ("Annual Town Meeting, Night 2"), though a year after "Day" is ("Town Meeting Day 2026").
SESSION = (r"annual|special|adjourned|regular|continued|reconvened|open|fall|autumn|spring|summer|winter|"
           r"january|february|march|april|may|june|july|august|september|october|november|december|"
           r"first|second|third|fourth|fifth|\d{1,2}(?:st|nd|rd|th)|\d{4}|fy\s?\d{2,4}|"
           r"(?:session|night|day|part)(?:\s+#?\d{1,2})?\s+of(?:\s+the)?")
TOWN_MEETING = re.compile(rf"(?:^|\s)(?:(?:{SESSION})\s+)*(?P<board>(?:representative\s+)?town\s+meeting)"
                          r"(?:[\s,;]+(?:continued|reconvened|(?:session|night|day|part)\s*#?\s*\d{1,2}|#\s*\d{1,2}))?$", re.I)


def town_meeting(name: str) -> str | None:
    """A meeting's name, as the board's, when it names Town Meeting: "Annual Fall Town Meeting" -> "Town
    Meeting", "Special Representative Town Meeting" -> "Representative Town Meeting", "Sutton Annual Town
    Meeting" -> "Sutton Town Meeting". None for any other name ("Town Meeting Day", "Downtown Meeting")."""
    found = TOWN_MEETING.search(name.strip())
    if not found:
        return None
    board = " ".join(w.capitalize() for w in found["board"].split())
    return f"{name.strip()[:found.start()].strip()} {board}".strip()


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
    status = status_of(raw)
    name = without_status(raw)
    name = re.sub(r"^[\s\-–:*]+|[\s\-–:*]+$", "", name)
    name = TIME_SUFFIX.sub("", TIME_PREFIX.sub("", name))
    name = re.sub(r"^[\s\-–:*]+|[\s\-–:*]+$", "", name)
    name = re.sub(r"\s{2,}", " ", name)
    if name.isupper():
        name = tidy_case(name)
    special = bool(re.search(r"\bspecial\b", name, re.I))
    # The board, without what the clerk added after it ("Planning Board - Public
    # Hearing", "License Board Meeting - Agenda - October 1, 2026"), after a colon
    # ("Waltham School Committee: FY28 Budget Workshop"), or in brackets ("Community
    # Preservation Committee (CPC)"), its date (board_part), or the kind of meeting;
    # but with the committee of it that follows ("City Council - Ordinance
    # Committee", "Conservation Commission: Rapid Response Subcommittee": its committee).
    pieces = re.split(r"\s+[-–]+\s+|:\s+", board_part(re.sub(r"\s*\([^)]*\)", "", name)))
    body = pieces[0]
    if len(pieces) > 1 and OF_BOARD.match(pieces[1]):
        committee = re.sub(r"\s+meeting$", "", pieces[1], flags=re.I)
        body = f"{body} {committee}"
    body = re.sub(r"^regular\s+", "", body, flags=re.I)
    # The kind of meeting, and "Agenda", which a board's name never ends with ("…Subcommittee on Mission
    # Meeting Agenda"); but Town Meeting is a board, whichever session it is (town_meeting).
    body = re.sub(r"\s+agenda$", "", body, flags=re.I)
    body = town_meeting(body) or re.sub(r"\s+(?:(?:regular|special)\s+)?(?:meeting|public hearings?)$|\s+regular$",
                                        "", body, flags=re.I)
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


def is_zoom_link(url: str) -> bool:
    """Whether url is a Zoom meeting or webinar link (zoom.us, or a city's own subdomain of it)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "zoom.us" or host.endswith(".zoom.us")) and \
        bool(re.match(r"/(j|w|webinar|meeting|my)/", parts.path))


# A "Download Agenda" link into the city's Agenda Center: its listing of the board's meeting
# ("/AgendaCenter/Commission-on-Climate-Action-and-Sustain-59/#_10142026-4468") or the agenda itself
# ("/AgendaCenter/ViewFile/Agenda/_10142026-4468"). The number after the date is the Agenda Center's own
# for the meeting (agendacenter-4468, pipeline/agendacenter.py).
AGENDA_CENTER_LINK = re.compile(r"/AgendaCenter/.*_\d{8}-(\d+)$", re.I)


def parse_event_page(page: str, base_url: str) -> dict:
    """Pull structured details from an event's page: Calendar.aspx?EID=..., or the newer calendar's
    /m/calendar/event/detail/..."""
    if DETAIL_PAGE.search(page):
        return parse_detail_page(page, base_url)
    details: dict = {}

    agenda = re.search(r'id="[^"]*lnkDownloadAgenda"[^>]*href="([^"]+)"', page, re.I)
    if agenda:
        url = urljoin(base_url, html.unescape(agenda.group(1)))
        adid = re.search(r"ADID=(\d+)", url)
        details["agenda_url"] = url
        details["agenda_id"] = adid.group(1) if adid else None
        center = AGENDA_CENTER_LINK.search(url)
        if center:
            details["agenda_center_id"] = center.group(1)

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

    # The event's "Link" field: kept only as a Zoom meeting's link, since the meeting page offers it as
    # "Join by Zoom". Cities also put a board's page, the Archive Center, or a stream there (Concord,
    # New Hampshire), often as a path on the city's site; those aren't a way to take part.
    link = re.search(r'id="[^"]*lnkLink"[^>]*href="([^"]+)"', page, re.I)
    if link:
        url = urljoin(base_url, html.unescape(link.group(1)).strip())
        if is_zoom_link(url):
            details["remote_url"] = url

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
    virtual..."), there is none, and the note isn't taken for a place.

    The newer calendar's list (parse_day_list) gives the same, without "address"."""
    if DAY_HEAD.search(page):
        return parse_day_list(page, base_url)
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


# ---- The newer calendar (/m/calendar) ---------------------------------------

DAY_HEAD = re.compile(r'id="calendar-events-header-(\d{4}-\d{2}-\d{2})"')
DAY_EVENT = re.compile(r'<li id="event-(\d+)"[^>]*\bcalendar-list-event\b')
DAY_TITLE = re.compile(r'<a href="([^"]+)"[^>]*\bcalendar-title-link\b[^>]*>(.*?)</a>', re.S)
DAY_TIMES = re.compile(r'<div class="events-category">.*?</svg>([^<]*)</div>', re.S)
DAY_PLACE = re.compile(r'<div class="calendar-location-content">(.*?)</div>', re.S)
DAY_CALENDAR = re.compile(r'<div class="badge\b[^>]*>(.*?)</div>', re.S)
# The boxes to pick which calendars to show: each calendar's number and name.
CATEGORY = re.compile(r'<input id="category-(\d+)"[^>]*\bdata-category-name="([^"]*)"')


def calendar_names(page: str) -> dict[str, str]:
    """The newer calendar page's calendars, by number."""
    return {number: clean_text(name) for number, name in CATEGORY.findall(page)}


def parse_day_list(page: str, base_url: str) -> list[dict]:
    """Every event in the newer calendar's list of a month, with the calendar it is on.

    The list is by day ("Wednesday, November 18"), and each event gives its times ("6:00 PM - No set end
    time", "All Day"), its place, and its calendar's name. A place the city keeps is named with the building
    it is in ("City Hall > City Council Committee Room") and is recorded as the older list names it, by the
    room alone; a note typed in the location box is marked up as text (a paragraph) and isn't a place. There
    is no address, so the events have no "address": it is on the event's page (parse_detail_page). An event
    listed on more than one day is its first day's."""
    numbers = {name.lower(): number for number, name in calendar_names(page).items()}
    days = list(DAY_HEAD.finditer(page))
    events, seen = [], set()
    for i, day in enumerate(days):
        section = page[day.end():days[i + 1].start() if i + 1 < len(days) else len(page)]
        items = list(DAY_EVENT.finditer(section))
        for j, item in enumerate(items):
            chunk = section[item.end():items[j + 1].start() if j + 1 < len(items) else len(section)]
            title = DAY_TITLE.search(chunk)
            if not title or item.group(1) in seen:
                continue
            seen.add(item.group(1))
            times = DAY_TIMES.search(chunk)
            times = clean_text(times.group(1)) if times else ""
            start, end = (None, None) if re.search(r"all day", times, re.I) else parse_times(times)
            place = DAY_PLACE.search(chunk)
            noted = bool(place) and place.group(1).lstrip().startswith("<")
            location_name = clean_text(place.group(1)).rsplit(" > ", 1)[-1] if place and not noted else ""
            calendar = DAY_CALENDAR.search(chunk)
            calendar = clean_text(calendar.group(1)) if calendar else ""
            event = {
                "id": item.group(1),
                "calendar_id": numbers.get(calendar.lower()),
                "calendar": calendar,
                "source_url": urljoin(base_url.rstrip("/") + "/", html.unescape(title.group(1))),
                "raw_title": clean_text(title.group(2)),
                "date": day.group(1),
                "start_time": start,
                "end_time": end,
                "location_name": "" if location_name.lower() in PLACEHOLDER_LOCATIONS else location_name,
            }
            event.update(parse_title(title.group(2)))
            events.append(event)
    return events


def on_newer_calendar(url: str | None) -> bool:
    """Whether a listing's address is the newer calendar's: its list gave no address for it."""
    return "/m/calendar/" in (url or "")


DETAIL_PAGE = re.compile(r'<h1 class="calendar-event-title\b')
DETAIL_PLACE = re.compile(r'aria-label="Event location">(.*?)(?:<div class="col event-detail-sidebar">|<hr\b|$)', re.S)
DETAIL_FACILITY = re.compile(r'<a href="[^"]*/Facilities/Facility/Details/[^"]*"[^>]*>(.*?)</a>', re.S)
DETAIL_MAP = re.compile(r'<a href="https?://maps\.google\.com/maps\?q=([^"]*)"')
# A place typed in, not one the city keeps, where the page has no map link: its lines and the town in plain
# text ("City Hall -Second Floor Conference Room , 205 Main St. , Biddeford ME 04005"). A note is marked up.
DETAIL_WRITTEN = re.compile(r'<span class="d-block">([^<]*)</span>')
WRITTEN_TOWN = re.compile(r"^(.+?)\s*,\s*([^,]+?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")
DETAIL_SIDEBAR = re.compile(r'<div class="col event-detail-sidebar">(.*?)(?:<hr\b|aria-labelledby="calendar-related-events-heading"|$)',
                            re.S)
DETAIL_LINK = re.compile(r'<a\b[^>]*\bhref="([^"]*)"[^>]*>(.*?)</a>', re.S)


def parse_detail_page(page: str, base_url: str) -> dict:
    """An event's page on the newer calendar: what parse_event_page() reads from the older one.

    Its place is a place the city keeps, by name, over a map link that searches for its address ("9 Dale
    Ave, Gloucester, MA 01930", as the older list gave it); the text of that link adds the floor, so the
    address is the one searched for. Its side column links the agenda ("View Agenda") and the event's own
    link, which is kept only as a Zoom meeting's, as from the older page.

    Where the page has no map link, the place is written out, its second line with its first; the older
    list gave the first line and the town ("City Hall -Second Floor Conference Room, Biddeford, ME 04005").
    Which commas part the lines isn't marked, so "address" is that reading with the first line up to its
    first comma, and "address_readings" has it with each longer first line: the page says nothing new of a
    meeting recorded with any of them."""
    details: dict = {}
    place = DETAIL_PLACE.search(page)
    if place:
        name = DETAIL_FACILITY.search(place.group(1))
        if name and clean_text(name.group(1)).lower() not in PLACEHOLDER_LOCATIONS:
            details["location_name"] = clean_text(name.group(1))
        searched = DETAIL_MAP.search(place.group(1))
        searched = unquote_plus(html.unescape(searched.group(1))).strip() if searched else ""
        # Only the town and state (", Lewiston, ME 04240", with no street before them) isn't an address.
        if searched and not searched.startswith(","):
            details["address"] = clean_text(searched)
        written = None if searched else DETAIL_WRITTEN.search(place.group(1))
        written = WRITTEN_TOWN.match(clean_text(written.group(1))) if written else None
        if written:
            lines, town = written.group(1).split(", "), f"{written.group(2)}, {written.group(3)} {written.group(4)}"
            readings = [f"{', '.join(lines[:n])}, {town}" for n in range(1, len(lines) + 1)]
            details["address"], details["address_readings"] = readings[0], readings
    side = DETAIL_SIDEBAR.search(page)
    for href, text in DETAIL_LINK.findall(side.group(1)) if side else []:
        url = urljoin(base_url, html.unescape(href).strip())
        if clean_text(text).lower() == "view agenda":
            if "agenda_url" not in details:
                adid = re.search(r"ADID=(\d+)", url)
                details["agenda_url"] = url
                details["agenda_id"] = adid.group(1) if adid else None
                center = AGENDA_CENTER_LINK.search(url)
                if center:
                    details["agenda_center_id"] = center.group(1)
        elif "remote_url" not in details and is_zoom_link(url):
            details["remote_url"] = url
    return details


# ---- Archive Center -------------------------------------------------------

def parse_title_date(title: str) -> str | None:
    """Find the meeting date in an archive item title such as 'May 21, 2026 REVISED'
    or 'Affordable Housing Trust Minutes 3-9-2026'. Returns YYYY-MM-DD or None."""
    day = dates.find_date(title)
    return day.isoformat() if day else None


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
