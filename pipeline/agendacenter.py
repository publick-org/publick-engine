"""Parsers for CivicPlus Agenda Center, where many CivicPlus cities post
agendas and minutes instead of (or as well as) on the city calendar.

Malden posts every board there. The Agenda Center's search page lists every
board's agendas for a date range in one request, grouped by board (the
Agenda Center's categories), one row per meeting: the meeting date, when the
agenda was posted, its title, and links to the agenda and (once posted) the
minutes. There are no times or places; those are only in the agenda itself.

Row titles are typed by each board's clerk ("Agenda for PCAC Meeting
9/16/2026", "CANCELLED: CASC 8/12/26 agenda"), so the board comes from the
category, and the title only for committees of a category the town lists
([meetings.agenda_center.committees]): "Finance Committee Agenda" under City
Council is the City Council Finance Committee.

An agenda keeps its number when the city posts a revised version; its posted
time changes, so each version is told apart by both.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime

from pipeline.meeting_names import find_board, parse_name

ROW = re.compile(r'<tr id="row[^"]*" class="catAgendaRow">(.*?)</tr>', re.S)
CATEGORY = re.compile(r'<h2 tabindex="0" role="button"[^>]*aria-controls="category-panel-(\d+)"[^>]*>(.*?)</h2>', re.S)
# An agenda written in the Agenda Center's own builder is linked as its web page ("?html=true"), with
# the PDF and the packet in the row's Download menu; the same address without the query is the PDF.
AGENDA_LINK = re.compile(r'<a id="(\d{8})-(\d+)"[^>]*href="(/AgendaCenter/ViewFile/Agenda/_\d{8}-\d+)(?:\?html=true)?"[^>]*>(.*?)</a>',
                         re.S)
MINUTES_LINK = re.compile(r'href="(/AgendaCenter/ViewFile/Minutes/_\d{8}-\d+)(?:\?html=true)?"')
POSTED = re.compile(r"Posted\s+(.*?\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)", re.S)


def search_url(base_url: str, start: date, end: date) -> str:
    """Every board's agendas for meetings from start to end."""
    return (f"{base_url.rstrip('/')}/AgendaCenter/Search/?term=&CIDs=all&startDate={start:%m/%d/%Y}"
            f"&endDate={end:%m/%d/%Y}&dateRange=&dateSelector=")


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def posted_at(row: str) -> str | None:
    """'Posted Sep 24, 2026 11:54 AM' -> '2026-09-24T11:54'."""
    found = POSTED.search(row)
    if not found:
        return None
    try:
        return datetime.strptime(text(found.group(1)), "%b %d, %Y %I:%M %p").isoformat(timespec="minutes")
    except ValueError:
        return None


def parse_listing(page: str, base_url: str) -> list[dict]:
    """Every row of an Agenda Center page, with the category it is listed under."""
    base = base_url.rstrip("/")
    heads = list(CATEGORY.finditer(page))
    rows = []
    for i, head in enumerate(heads):
        section = page[head.end():heads[i + 1].start() if i + 1 < len(heads) else len(page)]
        for row in ROW.finditer(section):
            agenda = AGENDA_LINK.search(row.group(1))
            if not agenda:
                continue
            stamp, number, path, title = agenda.groups()
            try:
                day = datetime.strptime(stamp, "%m%d%Y").date()
            except ValueError:
                continue
            minutes = MINUTES_LINK.search(row.group(1))
            rows.append({
                "category": text(head.group(2)),
                "category_id": head.group(1),
                "number": number,
                "date": day.isoformat(),
                "posted_at": posted_at(row.group(1)),
                "title": text(title),
                "agenda_url": base + path,
                "minutes_url": base + minutes.group(1) if minutes else None,
            })
    return rows


# A category named last-name-first, as many cities' Agenda Centers sort them:
# "Health, Board of", "Appeals, Zoning Board of", "Aging, Council on".
INVERTED = re.compile(r"^(?P<subject>[^,]+),\s+(?P<board>.+\b(?:of|on|for))$", re.I)


def natural_name(category: str) -> str:
    """'Health, Board of' -> 'Board of Health'. Other names are kept as they are."""
    m = INVERTED.match(category.strip())
    return f"{m['board']} {m['subject']}" if m else category


def body_for(row: dict, aliases: dict, committees: dict) -> str:
    """The category's board (a town's alias for it, or its name the right way round),
    or a committee of it named in the row's title."""
    body = next((target for name, target in aliases.items() if name.lower() == row["category"].lower()),
                natural_name(row["category"]))
    listed = committees.get(row["category"]) or committees.get(body) or {}
    return find_board(row["title"], [], listed) or body


def to_event(row: dict, aliases: dict | None = None, committees: dict | None = None) -> dict:
    """A row as a meeting record for pipeline.fetch_meetings."""
    parsed = parse_name(row["title"])
    body = body_for(row, aliases or {}, committees or {})
    hearing = re.search(r"\bhearings?\b", row["title"], re.I) and not re.search(r"\bmeeting\b", row["title"], re.I)
    kind = "Special Meeting" if parsed["special"] else "Public Hearing" if hearing else "Meeting"
    posted = (row["posted_at"] or "").replace("-", "").replace(":", "").replace("T", "")
    return {
        "id": f"agendacenter-{row['number']}",
        "source": "agendacenter",
        "source_url": row["agenda_url"],
        "date": row["date"],
        "start_time": None,
        "end_time": None,
        "body": body,
        "title": f"{body} {kind}",
        "posted_title": row["title"],
        "status": parsed["status"],
        "special": parsed["special"],
        "documents_url": row["agenda_url"],
        "minutes_url": row["minutes_url"],
        "minutes_id": f"agendacenter-{row['number']}" if row["minutes_url"] else None,
        # A revised agenda keeps its number but gets a new posted time.
        "agenda_id": f"{row['number']}-{posted}" if posted else row["number"],
        "agenda_url": row["agenda_url"],
    }
