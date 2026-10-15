"""A board's meetings from its Diligent Community portal (formerly iCompass, *.community.highbond.com).

Many school districts post their board's meetings on a Diligent Community
portal: Burlington School District (Vermont) and Portland Public Schools
(Maine) among them. The portal's calendar reads one public JSON service,
`/Services/MeetingsService.svc/meetings?from=...&to=...`, which lists every
meeting in a date range: its id, name ("BSD School Board Meeting - Oct 06
2026"), meeting type ("BSD School Board Meeting"), date and time, location,
and whether its agenda is published. A meeting the district has scheduled but
not yet published is listed too, so upcoming meetings are known before their
agendas.

A meeting's documents are another call,
`/Services/MeetingsService.svc/meetings/<id>/meetingDocuments`: each with a
DocumentType (the portal's own numbers, below). The agenda alone is its
published agenda document printed to PDF (`/document/<id>/?printPdf=true`);
the agenda package (`/document/<id>`) adds every attachment, often dozens of
pages, and is used only when the agenda isn't a document the portal prints.
Minutes are the adopted minutes when posted, else the published (draft) ones.

The board comes from the meeting's name first and its type second, because a
portal's types aren't always the board: Portland files its retreats, budget
forums and an ad-hoc committee under "Attendance Boundaries Advisory
Committee". [diligent_meetings.bodies] maps names, or words in them, to the
site's boards ("Business Meeting" = "Board of Public Education"); the longest
found in the meeting's name wins, then its type's, then `body`. A meeting
with none is reported, not guessed at.

This module has the parts that need no network or storage; the run is
pipeline/fetch_diligent_meetings.py.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from pipeline.meeting_names import find_board, parse_name, words

SOURCE = "diligent"

# The portal's DocumentType numbers (its meeting page's packageDocumentTypes).
AGENDA_HTML = 1             # published agenda, open session: a Word document shown as HTML, printable to PDF
AGENDA_PACKAGE = 4          # published agenda package, open session: the agenda and every attachment, as a PDF
MINUTES_PDF = 10            # published minutes, open session (the portal shows draft minutes once published)
ADOPTED_MINUTES_PDF = 53    # minutes as adopted

# " - Oct 06 2026" at the end of a meeting's name, or before a status: "Policy Committee - Jun 08 2026 (Cancelled)".
DATE_SUFFIX = re.compile(r"\s*-\s*[A-Z][a-z]{2} \d{1,2} \d{4}(?=\s*(?:\([^)]*\))?\s*$)")
# "CANCELLED - ", "Canceled: ", "Canceled due to weather: ", "Cancelled - " before the name.
STATUS_PREFIX = re.compile(r"^\s*(?:cancell?ed|postponed|rescheduled)\b[^:\-–]{0,40}?\s*[:\-–]\s*", re.I)
# "Remote Only [https://us06web.zoom.us/j/...]", "... via Zoom (https://tinyurl.com/...)", "via Zoom: https://...".
LINK_IN_LOCATION = re.compile(r"\s*[\[(]\s*https?://[^\])\s]+\s*[\])]|\s*https?://[^\])\s]+")
# A Zoom meeting's address, the only kind the site offers as "Join by Zoom".
ZOOM_LINK = re.compile(r"https://[\w.-]*zoom\.us/[^\])\s]+")


def base_url(settings: dict) -> str:
    """'https://bsdvt.community.highbond.com/Portal/' -> 'https://bsdvt.community.highbond.com'."""
    parts = urlsplit(settings["page_url"])
    return f"{parts.scheme}://{parts.netloc}"


def listing_url(settings: dict, start: str, end: str) -> str:
    return f"{base_url(settings)}/Services/MeetingsService.svc/meetings?from={start}&to={end}&loadEmptyMeetings=false"


def documents_url(settings: dict, meeting_id: int | str) -> str:
    return f"{base_url(settings)}/Services/MeetingsService.svc/meetings/{meeting_id}/meetingDocuments"


def meeting_page(settings: dict, meeting_id: int | str) -> str:
    return f"{base_url(settings)}/Portal/MeetingInformation.aspx?Id={meeting_id}"


def name_of(entry: dict) -> str:
    """The meeting's name without its date: 'BSD School Board Meeting - Oct 06 2026' -> 'BSD School Board Meeting'."""
    return DATE_SUFFIX.sub("", " ".join(str(entry.get("Name") or "").split()))


def board_for(name: str, meeting_type: str, settings: dict) -> str | None:
    """The site's board for a meeting: the longest of `bodies` named in its name, else its type's, else `body`."""
    bodies = settings.get("bodies", {})
    return (find_board(name, [], bodies)
            or next((board for key, board in bodies.items() if words(key) == words(meeting_type)), None)
            or settings.get("body"))


def location(text: str) -> tuple[str, str | None]:
    """A listing's location as the place, without web addresses, and its Zoom link apart:
    'Remote Only [https://us06web.zoom.us/j/840]' -> ('Remote Only', 'https://us06web.zoom.us/j/840').
    Other links (a shortened address, the city's posting of a joint meeting) are left out."""
    text = " ".join((text or "").split())
    link = ZOOM_LINK.search(text)
    place = LINK_IN_LOCATION.sub("", text).strip(" :,;-")
    return ("" if place in ("", "*") else place), (link.group(0) if link else None)


def parse_listing(entries: list[dict], settings: dict) -> tuple[list[dict], list[str]]:
    """The service's meetings, each with the fields a meeting record takes, and the names of
    meetings with no board, reported rather than guessed at. Names matching exclude_pattern are
    left out."""
    exclude = settings.get("exclude_pattern")
    found, unrecognized = [], []
    for entry in entries:
        name = name_of(entry)
        if not entry.get("Id") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(entry.get("MeetingDate", ""))):
            continue
        if exclude and re.search(exclude, name, re.I):
            continue
        info = parse_name(name, [], {})
        plain = STATUS_PREFIX.sub("", name).strip() or name
        body = board_for(plain, str(entry.get("MeetingTypeName") or ""), settings)
        if body is None:
            unrecognized.append(name)
            continue
        when = re.search(r"\b(\d{2}):(\d{2})$", str(entry.get("MeetingDateTime") or ""))
        place, link = location(str(entry.get("MeetingLocation") or ""))
        found.append({
            "id": int(entry["Id"]),
            "date": entry["MeetingDate"],
            "start_time": f"{when.group(1)}:{when.group(2)}" if when else None,
            "name": name,
            "body": body,
            "status": info["status"],
            "special": bool(re.search(r"\bspecial\b", plain, re.I)) and "special" not in words(body).split(),
            "published": bool(entry.get("Published")),
            "location": place,
            "remote_url": link,
        })
    return found, unrecognized


def pick_documents(documents: list[dict], settings: dict) -> dict:
    """A meeting's agenda and minutes among its documents, as {"agenda": {...}, "minutes": {...}},
    each with the address to download and the portal's last-modified mark. The agenda is the
    published agenda printed to PDF, or the agenda package when the agenda is HTML only; the
    minutes are the adopted minutes, or the published ones."""
    base = base_url(settings)
    by_type = {}
    for doc in documents:
        by_type.setdefault(doc.get("DocumentType"), doc)
    picked = {}
    agenda = by_type.get(AGENDA_HTML)
    if agenda and str(agenda.get("Format", "")).lower() != "html":
        picked["agenda"] = {"url": f"{base}/document/{agenda['Id']}/?printPdf=true", "doc": agenda}
    elif package := by_type.get(AGENDA_PACKAGE):
        picked["agenda"] = {"url": f"{base}/document/{package['Id']}", "doc": package}
    if minutes := by_type.get(ADOPTED_MINUTES_PDF) or by_type.get(MINUTES_PDF):
        picked["minutes"] = {"url": f"{base}/document/{minutes['Id']}", "doc": minutes}
    return {kind: {"url": p["url"], "id": p["doc"]["Id"], "last_modified": p["doc"].get("LastModified")}
            for kind, p in picked.items()}


# ---- The town's config ------------------------------------------------------------------

def check(config: dict) -> None:
    """A town's [diligent_meetings] table, checked when its config is loaded."""
    settings = config.get("diligent_meetings")
    if settings is None:
        return
    where = f"[diligent_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("page_url", "source_name", "since") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the portal's address, the district's name, and "
                         "the first meeting date to collect.")
    if not re.match(r"https://[\w.-]+/", str(settings["page_url"])):
        raise SystemExit(f'{where}: page_url must be the portal\'s address, like '
                         '"https://example.community.highbond.com/Portal/".')
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-01-01".')
    bodies = settings.get("bodies", {})
    if not isinstance(bodies, dict) or not all(isinstance(v, str) and v for v in bodies.values()):
        raise SystemExit(f'{where}: bodies must map meeting names or types to board names, like '
                         '"BSD School Board Meeting" = "School Board".')
    if not bodies and not settings.get("body"):
        raise SystemExit(f"{where} needs bodies, body, or both: the board each meeting is of.")
    if "exclude_pattern" in settings:
        try:
            re.compile(settings["exclude_pattern"])
        except (re.error, TypeError) as e:
            raise SystemExit(f"{where}: exclude_pattern isn't a regular expression ({e}).") from None
    for key in ("recheck_days", "max_documents_per_run", "days_ahead"):
        if key in settings and not (isinstance(settings[key], int) and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
