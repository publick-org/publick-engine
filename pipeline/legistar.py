"""Parsers for Legistar, the legislative system some city councils post their agendas and minutes in.

Somerville posts its City Council and the council's standing committees there.
Legistar's public web API (`webapi.legistar.com/v1/<client>`, OData, no key needed
where the city publishes it) lists each meeting with its body, date, time, place,
and the agenda and minutes files the system makes from the clerk's records, up to
1,000 meetings to a request.

Dates come as local midnight ("2026-10-08T00:00:00") and times as text ("7:00 PM").
The clerk's comment on a meeting says what its name doesn't: "Cancelled", "Special
Meeting", "Joint meeting with the Planning Board", or the online meeting's link.

A revised agenda or the minutes once adopted are published again, often at the same
address; each file's id names its publication time, so a new publication is a new
file and the change is recorded.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import quote

from pipeline.civicplus import status_of, without_status
from pipeline.meeting_names import parse_name

API = "https://webapi.legistar.com/v1"
# The most the API returns to a request.
PAGE = 1000
# Characters an OData query keeps as they are.
SAFE = "$=&',"


def api_url(client: str) -> str:
    """The city's API, by its Legistar client name ("somervillema")."""
    return f"{API}/{client}"


def site_url(client: str) -> str:
    """The city's public Legistar site, where each meeting has its page."""
    return f"https://{client}.legistar.com/Calendar.aspx"


def events_url(api: str, since: date, skip: int = 0) -> str:
    """A page of meetings on or after a date, oldest first."""
    query = f"$filter=EventDate ge datetime'{since.isoformat()}'&$orderby=EventDate,EventId&$top={PAGE}"
    if skip:
        query += f"&$skip={skip}"
    return f"{api.rstrip('/')}/events?{quote(query, safe=SAFE)}"


def clock(text: str | None) -> str | None:
    """'6:00 PM' -> '18:00'; None when no time is given."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?\s*", text or "")
    if not match:
        return None
    hour, minute, half = int(match[1]) % 12, match[2], match[3].lower()
    return f"{hour + (12 if half == 'p' else 0):02d}:{minute}"


def stamp(published: str | None) -> str:
    """A publication time as digits ('2026-10-25T15:07:06.407' -> '20261025150706'), for a file's id."""
    try:
        return datetime.fromisoformat(published).strftime("%Y%m%d%H%M%S") if published else ""
    except ValueError:
        return re.sub(r"\D", "", published or "")[:14]


def documents(event: dict) -> dict:
    """The agenda and minutes a meeting has published, as fields for its meeting record."""
    found = {}
    for kind, field in (("agenda", "Agenda"), ("minutes", "Minutes")):
        url = event.get(f"Event{field}File")
        if url:
            when = stamp(event.get(f"Event{field}LastPublishedUTC"))
            found[f"{kind}_id"] = f"legistar-{event['EventId']}-{kind}" + (f"-{when}" if when else "")
            found[f"{kind}_url"] = url
    return found


def parse_events(data: list[dict], boards: list[str] | None = None, aliases: dict | None = None,
                 with_documents: bool = True) -> list[dict]:
    """One page of the events API as meeting records. With with_documents, each record also
    names its agenda and minutes files, for a town that collects them."""
    events = []
    for e in data:
        day = (e.get("EventDate") or "")[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        name = (e.get("EventBodyName") or "").strip()
        if not name:
            continue
        # The comment can be a link to the online meeting, a note, or both.
        comment = re.sub(r"\s+", " ", e.get("EventComment") or "").strip()
        link = re.search(r"https?://\S+", comment)
        note = (comment[:link.start()] + comment[link.end():]).strip() if link else comment
        # The comment's status ("Cancelled") is the meeting's; what else it says is part of its name.
        status = status_of(note) if note else "scheduled"
        rest = re.sub(r"^[\s,;:\-–]+|[\s,;:\-–]+$", "", without_status(note)) if note else ""
        event = {
            "id": f"legistar-{e['EventId']}",
            "source": "legistar",
            "source_url": e.get("EventInSiteURL") or None,
            "date": day,
            "start_time": clock(e.get("EventTime")),
            "end_time": None,
            "location_name": (e.get("EventLocation") or "").strip(),
            "address": "",
            "documents_url": (e.get("EventInSiteURL") or None)
            if e.get("EventAgendaFile") or e.get("EventMinutesFile") else None,
        }
        # The board is the body's own name; the comment only adds to the title.
        parsed = parse_name(name, boards, aliases)
        event.update(parsed)
        if rest:
            event["title"] = f"{parsed['title']} - {rest}"
            event["special"] = parsed["special"] or bool(re.search(r"\bspecial\b", rest, re.I))
        if status != "scheduled":
            event["status"] = status
        if link:
            event["remote_url"] = link.group().rstrip(".,;)")
        if with_documents:
            event.update(documents(e))
        events.append(event)
    return events
