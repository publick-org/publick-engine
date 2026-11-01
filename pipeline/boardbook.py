"""A board's meetings from its public page on BoardBook Premier (meetings.boardbook.org).

Many school districts post their board's meetings on BoardBook Premier, by
Sparq Data Solutions: Bristol Public Schools (Connecticut) among them. Each
organization has one public page, `/Public/Organization/<id>`, with a table of
every meeting it has posted (Bristol's goes back to 2008), newest first. A
row gives the meeting's date, time and name in one line ("November 4, 2026 at
6:30 PM - Board of Education Meeting"), its type ("Regular", "Special",
"Working"), its place (a name and an address), its online meeting's link, a
recording's link once one is posted ("Recorded Meeting URL": YouTube, Zoom or
Google Drive), and links to its agenda (`/Public/Agenda/<org>?meeting=<id>`)
and, where the district posts them there, its minutes
(`/Public/Minutes/<org>?meeting=<id>`).

The agenda is a web page of the agenda's items and their attachments. Its
Download link (`/Public/DownloadAgenda/<org>?meeting=<id>`) gives the agenda
package: the agenda's pages, then every attachment (Bristol's run to 200
pages and 20 MB), bookmarked by item and attachment, the agenda's root
bookmark named "Agenda". The agenda alone is the package's pages before the
first page an attachment's bookmark points to; a package without that
structure, or without attachments, is the agenda as it is. The package is the
same bytes each time it's asked for while the agenda is unchanged, and so is
the agenda cut from it.

The minutes link leads to a viewer of the minutes as published (a
"snapshot"), whose script names the PDF it shows
(`/Public/DownloadSnapshot?SnapshotUri=...`).

The board comes from the meeting's name: [boardbook_meetings.bodies] maps
names, or words in them, to the site's boards ("Policy Committee" = "Board
of Education Policy Committee"), the longest found winning, else `body`. A
meeting with neither is reported, not guessed at.

This module has the parts that need no network or storage; the run is
pipeline/fetch_boardbook_meetings.py.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from pipeline import finalsite
from pipeline.meeting_names import find_board, parse_name, words

SOURCE = "boardbook"

TABLE = re.compile(r'<table\b[^>]*\bid="PublicMeetingsTable"[^>]*>(.*?)</table>', re.S | re.I)
ROW = re.compile(r"<tr\b[^>]*\brow-for-board\b[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)
LINK = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
# "November 4, 2026 at 6:30 PM - Board of Education Meeting"; the time may be left out.
HEADING = re.compile(r"^(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})(?:\s+at\s+(?P<time>\d{1,2}:\d{2}\s*[AP]M))?\s*-\s*(?P<name>.*)$",
                     re.I)
MEETING_TYPE = re.compile(r"Meeting Type:\s*</b>\s*([^<]*)", re.I)
# The place, its parts each in a span of their own: "BOE Auditorium ...", "129 Church Street", "Bristol, CT 06010".
LOCATION_PART = re.compile(r"<span\b[^>]*\bid=['\"]location[\w-]*-(?:description|line1|line2|csz)['\"][^>]*>(.*?)</span>",
                           re.S | re.I)
# A Zoom meeting's address, the only kind the site offers as "Join by Zoom".
ZOOM_LINK = re.compile(r"^https://[\w.-]*zoom\.us/j/", re.I)
# "*Revised*", "- REVISED" in a meeting's name: a revised agenda, not another meeting.
REVISED = re.compile(r"\*?\s*\brevised\b\s*\*?", re.I)
# The agenda page's items and the files attached to them: what the saved agenda is made from.
AGENDA_ITEMS = re.compile(r'<table\b[^>]*\bagenda-items-section\b[^>]*>(.*?)</table>', re.S | re.I)
ATTACHMENT_ID = re.compile(r"\bdata-documentid=['\"](\d+)['\"]", re.I)
DOWNLOAD_AGENDA = re.compile(r'href=["\']([^"\']*/Public/DownloadAgenda/[^"\']+)["\']', re.I)
# The minutes viewer's script: "loadUrlFormat = '/Public/DownloadSnapshot?SnapshotUri=Org2447%2F...pdf'".
SNAPSHOT = re.compile(r"['\"](/Public/DownloadSnapshot\?SnapshotUri=[^'\"]+)['\"]", re.I)
# A bookmark of an agenda item, as numbered on the agenda: "1. CALL TO ORDER", "8.2.a. Grants".
ITEM_BOOKMARK = re.compile(r"^\d+\.(?:[\da-z]{1,3}\.)*\s", re.I)


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def meeting_id(url: str) -> int | None:
    """'/Public/Agenda/bristol?meeting=772970' -> 772970."""
    value = parse_qs(urlsplit(url).query).get("meeting", [""])[0]
    return int(value) if value.isdigit() else None


def board_for(name: str, settings: dict) -> str | None:
    """The site's board for a meeting: the longest of `bodies` named in its name, else `body`."""
    return find_board(name, [], settings.get("bodies", {})) or settings.get("body")


def location(cell: str) -> str:
    """The place as the row gives it: 'BOE Auditorium and via Zoom Meeting Platform, 129 Church Street,
    Bristol, CT 06010'."""
    return ", ".join(part for part in (text(p) for p in LOCATION_PART.findall(cell)) if part)


def parse_row(row: str, page_url: str) -> dict | None:
    """One row of the meetings table, as found: its date, time, name, type, place and links; None for a
    row that isn't a meeting (no date)."""
    cells = CELL.findall(row)
    if not cells:
        return None
    heading = re.search(r"<div\b[^>]*>(.*?)</div>", cells[0], re.S | re.I)
    found = HEADING.match(text(heading.group(1)) if heading else "")
    if not found:
        return None
    day = datetime.strptime(found.group("date"), "%B %d, %Y").date().isoformat()
    start = None
    if found.group("time"):
        start = datetime.strptime(found.group("time").upper().replace(" ", ""), "%I:%M%p").strftime("%H:%M")
    kind = MEETING_TYPE.search(cells[0])
    links = [{"url": urljoin(page_url, html.unescape(url).strip()), "text": text(label)}
             for cell in cells[1:] for url, label in LINK.findall(cell) if "maps.google." not in url]
    return {"date": day, "start_time": start, "name": found.group("name").strip(),
            "type": text(kind.group(1)) if kind else "", "location": location(cells[1]) if len(cells) > 1 else "",
            "links": links}


def parse_listing(page: str, settings: dict) -> tuple[list[dict], list[str]]:
    """The organization's meetings from `since` on, each with the fields a meeting record takes, and the
    names of meetings with no board, reported rather than guessed at. Names matching exclude_pattern
    are left out."""
    table = TABLE.search(page)
    if not table:
        return [], []
    since, exclude = str(settings["since"]), settings.get("exclude_pattern")
    found, unrecognized = [], []
    for row in ROW.findall(table.group(1)):
        entry = parse_row(row, settings["page_url"])
        if entry is None or entry["date"] < since:
            continue
        name = entry["name"]
        if exclude and re.search(exclude, name, re.I):
            continue
        by_label = {}
        for link in entry["links"]:
            by_label.setdefault(link["text"].casefold(), link["url"])
        agenda_url = by_label.get("agenda")
        ident = meeting_id(agenda_url or by_label.get("minutes") or by_label.get("projector") or "")
        if ident is None:
            continue
        info = parse_name(name, [], {})
        plain = REVISED.sub(" ", re.sub(r"^\s*(?:cancell?ed|postponed|rescheduled)\b\s*[:\-–]*\s*", "", name, flags=re.I))
        plain = " ".join(plain.split(" -")[0].split()) or name
        body = board_for(plain, settings)
        if body is None:
            unrecognized.append(name)
            continue
        remote = next((link["url"] for link in entry["links"]
                       if link["text"].casefold() == "virtual meeting url" and ZOOM_LINK.match(link["url"])), None)
        recorded = [link for link in entry["links"] if link["text"].casefold() == "recorded meeting url"]
        video = next((v for v in (finalsite.recording(link["url"]) for link in recorded) if v), None)
        others = [{"url": link["url"], "text": link["text"]} for link in recorded
                  if not (video and finalsite.recording(link["url"]) == video)]
        others += [link for link in entry["links"] if link["text"].casefold() == "supplemental resources"]
        special = (entry["type"].casefold() == "special" or bool(re.search(r"\bspecial\b", plain, re.I))) \
            and "special" not in words(body).split()
        found.append({
            "id": ident,
            "date": entry["date"],
            "start_time": entry["start_time"],
            "name": name,
            "body": body,
            "type": entry["type"],
            "status": info["status"],
            "special": special,
            "location": entry["location"],
            "remote_url": remote,
            "agenda_page": agenda_url,
            "minutes_page": by_label.get("minutes"),
            "links": others,
            **(video or {}),
        })
    return found, unrecognized


# ---- A meeting's documents ---------------------------------------------------------------

def agenda_fingerprint(page: str) -> str | None:
    """What the saved agenda is made from, on its page: the items' text and the attached files'
    numbers. The same fingerprint means the same agenda, so its package isn't downloaded again."""
    items = AGENDA_ITEMS.search(page)
    if not items:
        return None
    content = text(items.group(1)) + "|" + ",".join(ATTACHMENT_ID.findall(items.group(1)))
    return hashlib.sha256(content.encode()).hexdigest()


def download_url(page: str, page_url: str) -> str | None:
    """The agenda page's Download link: the agenda package."""
    found = DOWNLOAD_AGENDA.search(page)
    return urljoin(page_url, html.unescape(found.group(1))) if found else None


def snapshot_url(page: str, page_url: str) -> str | None:
    """The minutes PDF that the minutes viewer shows."""
    found = SNAPSHOT.search(page)
    return urljoin(page_url, html.unescape(found.group(1))) if found else None


def agenda_pages(package: bytes) -> tuple[int, int]:
    """(the agenda's pages, the package's pages): the pages before the first one an attachment's
    bookmark points to. A package whose bookmarks aren't BoardBook's ("Agenda" first) or that has no
    attachments is all agenda."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(package))
    total = len(reader.pages)
    outline = reader.outline
    if not outline or isinstance(outline[0], list) or str(outline[0].title).strip().casefold() != "agenda":
        return total, total
    first = total + 1

    def walk(entries):
        nonlocal first
        for entry in entries:
            if isinstance(entry, list):
                walk(entry)
                continue
            title = str(entry.title).strip()
            if title.casefold() == "agenda" or ITEM_BOOKMARK.match(title):
                continue
            page = reader.get_destination_page_number(entry) + 1
            if page > 1:
                first = min(first, page)
    walk(outline)
    return min(first - 1, total), total


def agenda_alone(package: bytes) -> tuple[bytes, int, int]:
    """(the agenda's own pages as a PDF, its pages, the package's pages). The same package gives
    the same bytes."""
    from pypdf import PdfReader, PdfWriter

    pages, total = agenda_pages(package)
    if pages == total:
        return package, pages, total
    reader, writer = PdfReader(io.BytesIO(package)), PdfWriter()
    for page in reader.pages[:pages]:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), pages, total


# ---- The town's config ------------------------------------------------------------------

def check(config: dict) -> None:
    """A town's [boardbook_meetings] table, checked when its config is loaded."""
    settings = config.get("boardbook_meetings")
    if settings is None:
        return
    where = f"[boardbook_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("page_url", "source_name", "since") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the organization's public page, the district's "
                         "name, and the first meeting date to collect.")
    if not re.fullmatch(r"https://meetings\.boardbook\.org/Public/Organization/[\w-]+", str(settings["page_url"])):
        raise SystemExit(f'{where}: page_url must be the organization\'s public page, like '
                         '"https://meetings.boardbook.org/Public/Organization/2447".')
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-01-01".')
    bodies = settings.get("bodies", {})
    if not isinstance(bodies, dict) or not all(isinstance(v, str) and v for v in bodies.values()):
        raise SystemExit(f'{where}: bodies must map words in meeting names to board names, like '
                         '"Policy Committee" = "Board of Education Policy Committee".')
    if not bodies and not settings.get("body"):
        raise SystemExit(f"{where} needs bodies, body, or both: the board each meeting is of.")
    if "exclude_pattern" in settings:
        try:
            re.compile(settings["exclude_pattern"])
        except (re.error, TypeError) as e:
            raise SystemExit(f"{where}: exclude_pattern isn't a regular expression ({e}).") from None
    if not isinstance(settings.get("minutes", True), bool):
        raise SystemExit(f"{where}: minutes must be true or false: whether BoardBook carries the board's minutes.")
    for key in ("recheck_days", "max_documents_per_run"):
        if key in settings and not (isinstance(settings[key], int) and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
    if "request_delay" in settings and not (isinstance(settings["request_delay"], (int, float))
                                            and settings["request_delay"] >= 0):
        raise SystemExit(f"{where}: request_delay must be a number of seconds.")
