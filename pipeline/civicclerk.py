"""Parsers for CivicClerk, a meeting portal some cities use for agendas and minutes.

Manchester posts its Board of Mayor and Aldermen and the board's committees
there. The portal's public API (OData, no key needed) lists each meeting with
its name, start time, place, and the files published for it (agenda, packet,
minutes), 15 meetings to a page.

Start times are the city's local time, although the API marks them "Z" (UTC):
a Board of Mayor and Aldermen meeting at 7:00 PM is "19:00:00Z".

Each published file (the agenda, the full agenda packet, the minutes) has a
number, and the API serves it as a PDF. A town that collects documents saves
the agenda (not the packet, which can run to hundreds of pages) and the
minutes; a revised file is published under a new number.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

from pipeline.meeting_names import find_board, parse_name


def events_url(api_url: str, since: date) -> str:
    """The first page of meetings starting on or after a date, oldest first."""
    query = f"$filter=startDateTime ge {since.isoformat()}&$orderby=startDateTime"
    return f"{api_url.rstrip('/')}/Events?{quote(query, safe='$=&')}"


def next_page(data: dict) -> str | None:
    return data.get("@odata.nextLink")


def event_url(portal_url: str, event_id) -> str:
    """The meeting's page on the portal, which lists its agenda and minutes."""
    return f"{portal_url.rstrip('/')}/event/{event_id}/files"


def file_url(api_url: str, file_id) -> str:
    """A published file, as a PDF."""
    return f"{api_url.rstrip('/')}/Meetings/GetMeetingFileStream(fileId={file_id},plainText=false)"


def documents(event: dict, api_url: str) -> dict:
    """The agenda and minutes an event has published, as fields for its meeting record."""
    found = {}
    for f in event.get("publishedFiles") or []:
        kind = {"Agenda": "agenda", "Minutes": "minutes"}.get(f.get("type"))
        if kind and f.get("fileId") and f"{kind}_id" not in found:
            found[f"{kind}_id"] = f"civicclerk-{f['fileId']}"
            found[f"{kind}_url"] = file_url(api_url, f["fileId"])
    return found


def address(location: dict | None, states: dict | None = None) -> str:
    """'One City Hall Plaza, Manchester, NH 03101'. states maps a state's name
    to the abbreviation used for it ({"New Hampshire": "NH"}); the portal has both."""
    if not location:
        return ""
    place = ", ".join(p.strip() for p in (location.get("address1"), location.get("address2"), location.get("city")) if p and p.strip())
    state = (location.get("state") or "").strip()
    state = (states or {}).get(state, state)
    region = " ".join(p for p in (state, (location.get("zipCode") or "").strip()) if p)
    return ", ".join(p for p in (place, region) if p)


# Words that make a name a public body's ("Finance Subcommittee", "Harbor Commission"), which keeps its own
# name rather than its category's (parse_events, by_category).
BODY_WORDS = re.compile(r"\b(?:committee|sub-?committee|commission|commissioners|board|council|authority|"
                        r"trustees|task force|working group|partnership)\b", re.I)


def parse_events(data: dict, portal_url: str, boards: list[str] | None = None, aliases: dict | None = None,
                 states: dict | None = None, api_url: str | None = None, by_category: bool = False) -> list[dict]:
    """One page of the Events API as meeting records. With api_url, each record
    also names its agenda and minutes files, for a town that collects them. With
    by_category, an event whose name names no listed board, and isn't a body's
    name (BODY_WORDS), is filed under the board its category names, where it
    names one ([meetings.civicclerk]
    board_from_category: a portal whose event names are often an item, not a
    board, as Springfield's "EVERSOURCE - 41 Oakland St." under Board of Public
    Works). Off by default: elsewhere a category can be wrong for an event
    named for a board that isn't listed."""
    events = []
    for e in data.get("value", []):
        if e.get("isDeleted"):
            continue
        start = e.get("startDateTime") or ""
        day, time = start[:10], start[11:16]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        url = event_url(portal_url, e["id"])
        event = {
            "id": f"civicclerk-{e['id']}",
            "source": "civicclerk",
            "source_url": url,
            "date": day,
            # Midnight means no time was given.
            "start_time": time if time and time != "00:00" else None,
            "end_time": None,
            "address": address(e.get("eventLocation"), states),
            # The agenda and minutes stay on the portal for now; this links to them once posted.
            "documents_url": url if e.get("publishedFiles") else None,
        }
        # The portal can put the event's recurrence before its name: "One Time EventJoint Public Hearing ...",
        # or give it as the whole name, the board then only in the event's category ("Peaks Island Council").
        raw = e.get("eventName") or ""
        name = (re.sub(r"^\s*one[ -]time event\s*", "", raw, flags=re.I)
                or (e.get("categoryName") or "").strip() or raw)
        event.update(parse_name(name, boards, aliases))
        category = (e.get("categoryName") or "").strip()
        if (by_category and boards and category and find_board(event["title"], boards, aliases or {}) is None
                and not BODY_WORDS.search(event["title"])):
            event["body"] = find_board(category, boards, aliases or {}) or event["body"]
        if api_url:
            event.update(documents(e, api_url))
        events.append(event)
    return events
