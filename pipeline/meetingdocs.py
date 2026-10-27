"""A board's meetings, agendas and minutes from a city's listing of its events' documents.

Somerville posts its School Committee's agendas, minutes, presentations and recordings on
the city website's "Agendas, Minutes, & More" page (`/meetingdocs`, a Drupal view of the
city's events), filtered by department (`?title_1=School Committee`) and by date
(`field_event_date_value[min]`), 50 rows to a page (`&page=1`, ...), newest first. Each row
is an event: its date and time as the city writes it ("Mon, 10/19/2026 - 7:00pm"), its
title, linked to the event's own page ("School Committee Regular Meeting"), its
department, and every document posted for it, each with a type the city picks from a list
(Agenda, Revised Agenda, Minutes, Presentation, Meeting Materials, Video Recording, Meeting
Notice), the time it was posted, and a label of the clerk's own ("Amended Agenda- Regular
School Committee Meeting 10-19-2026"). The page repeats an event's row once for each of
its documents, each with all of them.

The machine-readable times beside each date and each document's posting aren't reliable
(an event's repeated rows can differ by hours), so times are read from the text, which is
the city's local time.

The agenda is the newest posted document of type Agenda or Revised Agenda whose label or
file name says it's an agenda (agenda_pattern, default "agenda") and doesn't match
not_agenda_pattern: Somerville types its "Call for the Week" notices Agenda too, one
document for every meeting of a week, which would be summarized as each meeting's agenda.
A meeting with only such a notice has no agenda here; its page links the event's. The minutes are the newest of type Minutes. A YouTube Video
Recording is the meeting's recording. The board comes from the title, as
[meetingdocs_meetings.bodies] maps names, or words in them, to the site's boards (the
longest found wins), then `body`; a title with none is reported, not guessed at. "CANCELLED"
in a title cancels the meeting.

This module has the parts that need no network or storage; the run is
pipeline/fetch_meetingdocs_meetings.py.
"""

from __future__ import annotations

import html
import re
from datetime import date
from urllib.parse import urlencode, urljoin, urlsplit

from pipeline.finalsite import recording
from pipeline.meeting_names import find_board, parse_name

SOURCE = "meetingdocs"

ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL = re.compile(r'<td[^>]*class="([^"]*)"[^>]*>(.*?)</td>', re.S)
# "Mon, 10/19/2026 - 7:00pm"
WHEN = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s*-\s*(\d{1,2}):(\d{2})\s*([ap])m)?", re.I)
DOCUMENT = re.compile(r'<div\s+class="paragraph paragraph--type--event-document.*?(?=<div\s+class="paragraph paragraph--type--event-document|$)', re.S)
# A field of a document: its machine name, and the HTML of its value.
FIELD = re.compile(r'field--name-field-([\w-]+).*?<div\s+class="field__item">(.*?)</div>', re.S)
LINK = re.compile(r'<a\s+href="([^"]+)"')
POSTED = re.compile(r'field--name-field-event-document-time-posted.*?<time[^>]*>([^<]+)</time>', re.S)
URL_FIELD = re.compile(r'field--name-field-url.*?<a\s+href="([^"]+)"', re.S)


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def listing_url(settings: dict, since: str, page: int = 0) -> str:
    """A page of the listing: page_url (with the department filter) from `since` on."""
    parts = urlsplit(settings["page_url"])
    query = (parts.query + "&" if parts.query else "") + urlencode({"field_event_date_value[min]": since})
    if page:
        query += f"&page={page}"
    return parts._replace(query=query).geturl()


def has_next(page: str) -> bool:
    return "pager__item--next" in page


def when(cell: str) -> tuple[str, str | None] | None:
    """('2026-10-19', '19:00') from a date cell's text; None if it has no date."""
    found = WHEN.search(text(cell))
    if not found:
        return None
    month, day, year, hour, minute, half = found.groups()
    try:
        day_iso = date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None
    if hour is None:
        return day_iso, None
    clock = int(hour) % 12 + (12 if half.lower() == "p" else 0)
    return day_iso, f"{clock:02d}:{minute}"


def documents(cell: str, page_url: str) -> list[dict]:
    """Every document in a row's documents cell: its type, label, address, and when it was posted."""
    found = []
    for block in DOCUMENT.findall(cell):
        fields = {name: value for name, value in FIELD.findall(block)}
        link = LINK.search(fields.get("event-document", "")) or URL_FIELD.search(block)
        posted = POSTED.search(block)
        posted = when(posted.group(1)) if posted else None
        if not link:
            continue
        url = urljoin(page_url, html.unescape(link.group(1)))
        found.append({
            "type": text(fields.get("event-document-type", "")),
            "label": text(fields.get("custom-document-type", "")),
            "file": text(fields.get("event-document", "")),
            "url": url,
            # Local time, as the page writes it: "2026-10-16T10:14".
            "posted": f"{posted[0]}T{posted[1] or '00:00'}" if posted else "",
        })
    return found


def parse_listing(page: str, page_url: str) -> list[dict]:
    """The events on a page of the listing, once each: their id (the event page's path), title,
    date, time, page and documents."""
    events = {}
    body = page[page.find("<tbody"):page.find("</tbody>")] if "<tbody" in page else page
    for row in ROW.findall(body):
        cells = {k: v for k, v in CELL.findall(row)}
        date_cell = next((v for k, v in cells.items() if "event-date" in k), "")
        title_cell = next((v for k, v in cells.items() if "views-field-title" in k), "")
        link = LINK.search(title_cell)
        day = when(date_cell)
        if not link or not day:
            continue
        path = urlsplit(html.unescape(link.group(1))).path
        event = events.setdefault(path, {
            "id": path.strip("/").removeprefix("events/").replace("/", "-"),
            "title": text(title_cell), "date": day[0], "start_time": day[1],
            "url": urljoin(page_url, path), "documents": [],
        })
        docs_cell = next((v for k, v in cells.items() if "event-documents" in k), "")
        for doc in documents(docs_cell, page_url):
            if doc["url"] not in {d["url"] for d in event["documents"]}:
                event["documents"].append(doc)
    return list(events.values())


def newest(docs: list[dict]) -> dict | None:
    return max(docs, key=lambda d: d["posted"]) if docs else None


def pick_documents(docs: list[dict], settings: dict) -> dict:
    """The meeting's agenda, minutes and recording: {"agenda": doc, "minutes": doc, "video": {...}}."""
    pattern, other = settings.get("agenda_pattern", r"\bagenda\b"), settings.get("not_agenda_pattern")
    agendas = [d for d in docs if d["type"].lower() in ("agenda", "revised agenda")
               and (re.search(pattern, d["label"], re.I) or re.search(pattern, d["file"], re.I))
               and not (other and (re.search(other, d["label"], re.I) or re.search(other, d["file"], re.I)))]
    minutes = [d for d in docs if d["type"].lower() == "minutes"]
    picked = {k: v for k, v in (("agenda", newest(agendas)), ("minutes", newest(minutes))) if v}
    for d in docs:
        video = recording(d["url"]) if d["type"].lower() == "video recording" else None
        if video:
            picked["video"] = video
            break
    return picked


def to_meetings(events: list[dict], settings: dict) -> tuple[list[dict], list[str]]:
    """The events as meetings of the town's boards, and the titles with no board."""
    bodies, default = settings.get("bodies", {}), settings.get("body")
    exclude = settings.get("exclude_pattern")
    found, unrecognized = [], []
    for e in events:
        if exclude and re.search(exclude, e["title"], re.I):
            continue
        body = find_board(e["title"], [], bodies) or default
        if not body:
            unrecognized.append(e["title"])
            continue
        info = parse_name(e["title"], [body], {})
        found.append({**e, "body": body, "status": info["status"], "special": info["special"],
                      **pick_documents(e["documents"], settings)})
    return found, unrecognized


def check(config: dict) -> None:
    """A town's [meetingdocs_meetings] table, checked when its config is loaded."""
    settings = config.get("meetingdocs_meetings")
    if settings is None:
        return
    where = f"[meetingdocs_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("page_url", "source_name", "since") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the listing's address (with its department "
                         "filter), who posts it, and the first meeting date to collect.")
    if not re.match(r"https://[\w.-]+/", str(settings["page_url"])):
        raise SystemExit(f'{where}: page_url must be the listing\'s address, like '
                         '"https://www.somervillema.gov/meetingdocs?title_1=School+Committee".')
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-01-01".')
    bodies = settings.get("bodies", {})
    if not isinstance(bodies, dict) or not all(isinstance(v, str) and v for v in bodies.values()):
        raise SystemExit(f'{where}: bodies must map meeting titles, or words in them, to board names, like '
                         '"Regular Meeting" = "School Committee".')
    if not bodies and not settings.get("body"):
        raise SystemExit(f"{where} needs bodies, body, or both: the board each meeting is of.")
    for key in ("exclude_pattern", "agenda_pattern", "not_agenda_pattern"):
        if key in settings:
            try:
                re.compile(settings[key])
            except (re.error, TypeError) as e:
                raise SystemExit(f"{where}: {key} isn't a regular expression ({e}).") from None


def posted_stamp(posted: str) -> str:
    """'2026-10-16T10:14' -> '202610161014', for a document's id."""
    return re.sub(r"\D", "", posted)[:12]
