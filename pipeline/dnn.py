"""Parsers for city websites built on DotNetNuke (DNN) with its Events calendar.

Manchester's city calendar is one. It has no feed, but its month view is plain
HTML at an address per month (.../ModuleID/<module>/mctl/EventMonth/selecteddate/
10-01-2026). Each meeting is a link to its event page, carrying a tooltip whose
header gives the name and times ("Planning Board Public Hearings - 10/1/2026
6:00 PM - 10/1/2026 10:00 PM") and whose body is the city's description, often
with a link to the agenda. The event page adds the location ("City Hall").

Some boards link their agenda as a PDF named for the meeting's date
("2026-10-01_PB_AGENDA.PDF", "2026-09-10 ZBA Agenda.pdf"); others link a page
or a file that serves the whole year (the Manchester Development Corporation's
meeting schedule). Only the first kind is one meeting's agenda (agenda_file).
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date, datetime
from urllib.parse import unquote, urljoin, urlsplit

from pipeline.civicplus import clean_text
from pipeline.meeting_names import find_board, parse_name

EVENT_LINK = re.compile(r'<a id="ctlEvents_Mod_\d+_EventID_(\d+)_EventDate_\w+" title="([^"]*)" href="([^"]+)"[^>]*>', re.S)
HEADER = re.compile(r'Eventtooltipheader">(.*?)</td>', re.S)
BODY = re.compile(r'Eventtooltipbody">(.*?)</td>', re.S)
WHEN = re.compile(r"\s+-\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)(?:\s+-\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M))?\s*$", re.I)
LINK = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
GENERIC_NAME = re.compile(r"(?:(?:regular|special|monthly)\s+)?meeting", re.I)
LOCATION = re.compile(r'<span class="SubHead">Location:</span>\s*</td>\s*<td>\s*<span class="Normal">(.*?)</span>', re.S)


def month_url(calendar_url: str, module_id: int, month: date) -> str:
    return f"{calendar_url.rstrip('/')}/ModuleID/{module_id}/mctl/EventMonth/selecteddate/{month.replace(day=1):%m-%d-%Y}"


def to_time(value: str) -> str:
    """'6:00 PM' -> '18:00'."""
    return datetime.strptime(re.sub(r"\s+", " ", value.strip().upper()), "%I:%M %p").strftime("%H:%M")


def parse_month(page: str, base_url: str, boards: list[str] | None = None, aliases: dict | None = None) -> list[dict]:
    """Every event in a month view, with its tooltip's links (see links_in)."""
    events = []
    for event_id, tooltip, href in EVENT_LINK.findall(page):
        tooltip = html.unescape(tooltip)
        header = HEADER.search(tooltip)
        when = WHEN.search(clean_text(header.group(1))) if header else None
        if not when:
            continue
        name = clean_text(header.group(1))[:when.start()]
        start_day, start, end_day, end = when.groups()
        start_time = to_time(start)
        end_time = to_time(end) if end and end_day == start_day else None
        body = BODY.search(tooltip)
        event = {
            "id": f"dnn-{event_id}",
            "source_url": urljoin(base_url, html.unescape(href)),
            "date": datetime.strptime(start_day, "%m/%d/%Y").date().isoformat(),
            # Midnight is an all-day event; an end time equal to the start means none was given.
            "start_time": None if start_time == "00:00" else start_time,
            "end_time": None if end_time in (start_time, "23:59") else end_time,
            "links": links_in(body.group(1) if body else "", base_url),
        }
        event.update(parse_name(name, boards, aliases))
        # "Regular Meeting" names no board; the description may ("Manchester Health Department").
        if GENERIC_NAME.fullmatch(event["title"]) and body:
            event["body"] = find_board(clean_text(body.group(1)), boards or [], aliases or {}) or event["body"]
        events.append(event)
    return events


def links_in(fragment: str, base_url: str) -> list[dict]:
    return [{"url": urljoin(base_url, html.unescape(url).strip()), "text": clean_text(text)}
            for url, text in LINK.findall(fragment)]


def agenda_file(links: list[dict], day: str) -> dict | None:
    """The meeting's own agenda among an event's links, as agenda_id and
    agenda_url fields: a PDF whose file name has the meeting's date (2026-10-01,
    2026_10_01 or 20261001). A revised agenda is posted under a new address (the
    file name or its ?ver= changes), so the address names the file."""
    year, month, day_ = day.split("-")
    dated = re.compile(rf"{year}[-_ ]?{month}[-_ ]?{day_}")
    for link in links:
        name = unquote(urlsplit(link["url"]).path.rsplit("/", 1)[-1])
        if name.lower().endswith(".pdf") and dated.search(name):
            return {"agenda_id": "dnn-" + hashlib.sha256(link["url"].encode()).hexdigest()[:16], "agenda_url": link["url"]}
    return None


def parse_event_page(page: str) -> dict:
    """The location from an event's page, when the city gave one."""
    location = LOCATION.search(page)
    name = clean_text(location.group(1)) if location else ""
    return {"location_name": name} if name else {}
