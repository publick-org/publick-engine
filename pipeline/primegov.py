"""Parsers for PrimeGov, a meeting portal some cities post every board's agendas and minutes in.

Worcester posts its City Council, the council's standing committees, and its boards and
commissions there. The portal's public API (no key needed) lists a year's past meetings
(`/api/v2/PublicPortal/ListArchivedMeetings?year=2026`) and the meetings ahead
(`ListUpcomingMeetings`), each with its board's name as its title, its start time (local),
its place, its recording, and the documents published for it: compiled from templates the
city names ("Agenda", "Minutes", "Packet", "HTML Agenda", "Cancellation Notice"), each
served as a PDF through a redirect to the portal's storage.

A cancelled meeting is marked in its title ("Board of Health - CANCELLED"). A document
published again keeps its number with a new publication time; its id here names both, so
a revised agenda or corrected minutes are saved again and the change recorded.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from pipeline.finalsite import recording
from pipeline.meeting_names import parse_name

# The PDF a template compiles to; 3 is an HTML page.
PDF, HTML = 1, 3
# Which templates are the agenda and the minutes, by name, unless the town says otherwise.
AGENDA = r"^agenda$"
MINUTES = r"^minutes$"


def portal_url(client: str) -> str:
    """The city's portal, by its PrimeGov name ("worcesterma")."""
    return f"https://{client}.primegov.com/public/portal"


def archived_url(client: str, year: int) -> str:
    return f"https://{client}.primegov.com/api/v2/PublicPortal/ListArchivedMeetings?year={year}"


def upcoming_url(client: str) -> str:
    return f"https://{client}.primegov.com/api/v2/PublicPortal/ListUpcomingMeetings"


def document_url(client: str, template_id, output: int = PDF) -> str:
    """A compiled document: a PDF, or for an HTML template its page on the portal."""
    if output == HTML:
        return f"https://{client}.primegov.com/Portal/Meeting?meetingTemplateId={template_id}"
    return f"https://{client}.primegov.com/Public/CompiledDocument?meetingTemplateId={template_id}&compileOutputType={output}"


def stamp(published: str | None) -> str:
    """A publication time as digits ('2026-10-16T18:23:34.09' -> '20261016182334'), for a file's id."""
    try:
        return datetime.fromisoformat(published).strftime("%Y%m%d%H%M%S") if published else ""
    except ValueError:
        return re.sub(r"\D", "", published or "")[:14]


def documents(meeting: dict, client: str, agenda: str = AGENDA, minutes: str = MINUTES) -> dict:
    """The agenda and minutes a meeting has published (the first PDF template whose name matches
    each pattern), as fields for its meeting record."""
    found = {}
    for kind, pattern in (("agenda", agenda), ("minutes", minutes)):
        doc = next((d for d in meeting.get("documentList") or [] if d.get("compileOutputType") == PDF
                    and re.search(pattern, (d.get("templateName") or "").strip(), re.I)), None)
        if doc:
            when = stamp(doc.get("publishDate"))
            found[f"{kind}_id"] = f"primegov-{doc['id']}" + (f"-{when}" if when else "")
            found[f"{kind}_url"] = document_url(client, doc["templateId"])
    return found


def page_url(meeting: dict, client: str) -> str:
    """The meeting's own page: its agenda as the portal shows it (an HTML template), else its agenda's
    PDF, else the portal."""
    docs = meeting.get("documentList") or []
    page = next((d for d in docs if d.get("compileOutputType") == HTML), None)
    if page:
        return document_url(client, page["templateId"], HTML)
    pdf = next((d for d in docs if d.get("compileOutputType") == PDF and "agenda" in (d.get("templateName") or "").lower()), None)
    return document_url(client, pdf["templateId"]) if pdf else portal_url(client)


def parse_meetings(data: list[dict], client: str, boards: list[str] | None = None, aliases: dict | None = None,
                   with_documents: bool = True, agenda: str = AGENDA, minutes: str = MINUTES) -> list[dict]:
    """A list from the API as meeting records. With with_documents, each record also names its
    agenda and minutes files, for a town that collects them."""
    found = []
    for m in data:
        start = m.get("dateTime") or ""
        day, time = start[:10], start[11:16]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        title = re.sub(r"\s+", " ", m.get("title") or "").strip()
        if not title:
            continue
        url = page_url(m, client)
        meeting = {
            "id": f"primegov-{m['id']}",
            "source": "primegov",
            "source_url": url,
            "date": day,
            # Midnight means no time was given.
            "start_time": time if time and time != "00:00" else None,
            "end_time": None,
            "location_name": (m.get("location") or "").strip(),
            "address": "",
            "documents_url": url if m.get("documentList") else None,
        }
        meeting.update(parse_name(title, boards, aliases))
        if m.get("zoomMeetingLink"):
            meeting["remote_url"] = m["zoomMeetingLink"]
        meeting.update(recording(m.get("videoUrl") or "") or {})
        if with_documents:
            meeting.update(documents(m, client, agenda, minutes))
        found.append(meeting)
    return found
