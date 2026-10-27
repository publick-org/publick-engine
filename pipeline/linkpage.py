"""Parser for a page that links each meeting's documents, one link a document.

Some school districts post their board's agendas and minutes as files (usually
Google Drive files) linked from one page of their website, with no post or
calendar entry a meeting. Three layouts are read:

- A table with a row a meeting. Methuen Public Schools' "Meetings, Documents
  and Recordings" page (Finalsite) has, under a tab for each school year, rows
  of a date ("2026-10-13"), the meeting's name ("Business Meeting", "Policy
  Subcommittee", "Finance Subcommittee - CANCELLED"), and links labelled
  "Posting", "Agenda", "Minutes" and "Video" (a YouTube recording, or a Drive
  file). A cell with nothing posted yet repeats its column's heading
  ("Packet", "Minutes") as plain text.
- A list of links, each naming its meeting. Waltham Public Schools' "Minutes
  and Documentation" page (ParentSquare SmartSites) has, under each month,
  links like "September 16, 2026 Agenda", "September 16, 2026 Agenda
  (Español)", "September 16, 2026 Minutes", "June 10, 2026 Special Meeting",
  "June 10 2026 Special Meeting Minutes", and a Drive folder of "Additional
  Resources".
- Lines of dates, each with its documents under it. Nashua School District's
  "BOE Meeting Agendas and Minutes" page (Finalsite) has, under each month, a
  line "August 24, 2026 - Public Hearing" or "July 13, 2026 (Special BOE)",
  then a list of links labelled "Agenda and Attachments", "Minutes",
  "Non-Public Minutes". Its committees each have a page of their own, the same
  shape ([page_meetings.pages] maps each to its board).

A link inside a table row belongs to that row's meeting; any other link with a
date in its label is an entry of its own; and a document link without one
belongs to the last short line of text with a date before it. An entry's date is the first date in its text, written out
("September 16, 2026", "Sept. 16 2026") or as "2026-09-16". Its meeting's
name is the rest of the text: for a row, its cells without their links'
labels and without cells that only repeat their column's heading; for a link,
its label without the date and the words "agenda" and "minutes"; for a line,
the rest of the line. Entries of
the same date and name are one meeting, so "June 10, 2026 Special Meeting"
and "June 10 2026 Special Meeting Minutes" are documents of one meeting, and
"September 16, 2026 Agenda" and "September 16, 2026 Minutes" of another.

What each link is comes from its label: minutes if it matches the town's
minutes_pattern (default "minutes"), an agenda if it matches agenda_pattern
(default "agenda"); a YouTube link is the meeting's recording. Waltham's
agenda_pattern also takes "Special Meeting", "Budget Workshop" and "Public
Hearing", the names it gives those meetings' agendas. Other links (postings,
packets, folders, videos on Drive) are kept with the meeting as links.
"""

from __future__ import annotations

import html
import re
from datetime import date
from urllib.parse import urljoin

from pipeline import finalsite
from pipeline.filelist import DATE_IN_TITLE, MONTHS
from pipeline.meeting_names import find_board, parse_name, words

SOURCE = "page"

ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
LINK = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
# The page outside its tables, a piece at a time: a link, a tag, or text.
TOKEN = re.compile(r'(?P<link><a\b[^>]*\bhref="(?P<href>[^"]+)"[^>]*>(?P<label>.*?)</a>)|(?P<tag><[^>]+>)|(?P<text>[^<]+)',
                   re.S | re.I)
# Tags that end a line of text.
BLOCK_TAG = re.compile(r"</?(?:p|div|li|ul|ol|h[1-6]|br|section|header|article|table|dt|dd)\b", re.I)
# A line longer than this is a paragraph, not a meeting's heading.
HEADING_LENGTH = 120
ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
# "6:00 PM", "6 p.m.", "6:30pm": a meeting's time, where an entry gives one.
TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.I)
# Words in a link's label that say which document it is, not which meeting.
MOVED_FROM = re.compile(r"\b(?:rescheduled|moved|postponed)\s+from\b.*", re.I)
DOCUMENT_WORDS = re.compile(r"\b(?:amended|revised|original|draft|approved|agenda|minutes)\b", re.I)
STATUS_IN_NAME = re.compile(r"\b(?:cancell?ed|postponed|rescheduled)\b", re.I)


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def find_date(value: str) -> re.Match | None:
    """The first date in a text, written out or as yyyy-mm-dd."""
    found = [m for m in (ISO_DATE.search(value), DATE_IN_TITLE.search(value)) if m]
    return min(found, key=lambda m: m.start()) if found else None


def date_value(found: re.Match) -> str | None:
    try:
        if found.re is ISO_DATE:
            return date(*(int(g) for g in found.groups())).isoformat()
        month, day, year = found.groups()
        return date(int(year), MONTHS.index(month[:3].lower()) + 1, int(day)).isoformat()
    except ValueError:
        return None


def time_of(value: str) -> str | None:
    """'6:00 PM' -> '18:00'."""
    found = TIME.search(value)
    if not found:
        return None
    hour, minute = int(found.group(1)) % 12 + (12 if found.group(3).lower() == "p" else 0), int(found.group(2) or 0)
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def links_in(fragment: str, page_url: str) -> list[dict]:
    return [{"url": urljoin(page_url, html.unescape(url).strip()), "text": text(label)}
            for url, label in LINK.findall(fragment) if not url.startswith(("#", "mailto:", "tel:", "javascript:"))]


def without(value: str, found: re.Match | None) -> str:
    return value if found is None else value[:found.start()] + " " + value[found.end():]


def tidy(value: str) -> str:
    """A meeting's name: its words, without stray punctuation around them ("(Special BOE)" ->
    "Special BOE")."""
    value = re.sub(r"^[\s\-–:*,.]+|[\s\-–:*,.]+$", "", value)
    if re.fullmatch(r"\([^()]*\)", value):
        value = value[1:-1]
    return re.sub(r"\s{2,}", " ", value).strip()


def row_name(row: str, headings: list[str], columns: list[str] | None = None) -> str:
    """A table row's meeting name: its cells without the date, their links' labels, and any cell
    that only repeats its column's heading (a cell with nothing posted yet). With columns (headings,
    [page_meetings] name_columns), only those columns' cells: a page whose document cells have words
    of their own ("Agenda and Zoom Link (News Post, PDF) and Meeting Minutes", Medford's), which
    change as documents are posted."""
    parts = []
    wanted = {words(c) for c in columns or []}
    for i, cell in enumerate(CELL.findall(row)):
        heading = headings[i] if i < len(headings) else ""
        if wanted and words(heading) not in wanted:
            continue
        value = text(LINK.sub(" ", cell))
        value = text(without(value, find_date(value)))
        if value and words(value) != words(heading):
            parts.append(value)
    return tidy(" ".join(parts))


def entries(page: str, page_url: str, name_columns: list[str] | None = None) -> list[dict]:
    """Each table row with a link, and each link outside a table row: its date (or None), the
    meeting's name, any time it gives, and its links, in the page's order."""
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    found, headings = [], []
    for row in ROW.finditer(page):
        links = links_in(row.group(1), page_url)
        cells = [text(c) for c in CELL.findall(row.group(1))]
        if not links:
            # A row of headings, kept to tell an empty cell ("Minutes") from a meeting's name.
            if cells and not find_date(" ".join(cells)):
                headings = cells
            continue
        raw = text(row.group(1))
        day = find_date(raw)
        found.append({"start": row.start(), "date": date_value(day) if day else None,
                      "name": row_name(row.group(1), headings, name_columns), "time": time_of(without(raw, day)),
                      "links": links, "raw": raw})
    outside = ROW.sub(lambda m: " " * len(m.group(0)), page)
    heading, buffer = None, []

    def flush(at: int):
        # A short line of text with a date ("August 24, 2026 - Public Hearing") heads the
        # undated links after it, until the next such line.
        nonlocal heading
        line = " ".join(" ".join(buffer).split())
        buffer.clear()
        day = find_date(line) if len(line) <= HEADING_LENGTH else None
        if day:
            rest = without(line, day)
            heading = {"start": at, "date": date_value(day), "name": tidy(rest), "time": time_of(rest), "links": [],
                       "raw": line}
            found.append(heading)

    for token in TOKEN.finditer(outside):
        tag, words_ = token.group("tag"), token.group("text")
        if words_ is not None:
            buffer.append(html.unescape(words_))
        elif tag is not None:
            if BLOCK_TAG.match(tag):
                flush(token.start())
        else:
            flush(token.start())
            label, url = text(token.group("label")), html.unescape(token.group("href")).strip()
            if url.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            target = {"url": urljoin(page_url, url), "text": label}
            day = find_date(label)
            if day:
                rest = without(label, day)
                found.append({"start": token.start(), "date": date_value(day), "name": tidy(DOCUMENT_WORDS.sub(" ", rest)),
                              "time": time_of(rest), "links": [target], "raw": label})
            elif heading and (finalsite.is_document(target["url"]) or finalsite.recording(target["url"])):
                heading["links"].append(target)
    flush(len(outside))
    found = [e for e in found if e["links"]]
    return [{k: v for k, v in e.items() if k != "start"} for e in sorted(found, key=lambda e: e["start"])]


def slug(value: str) -> str:
    return "-".join(words(re.sub(r"[^A-Za-z0-9]+", " ", value)).split())


def meeting_key(name: str) -> str:
    """What tells a day's meetings apart, the same when the district later marks one cancelled or
    amended: 'Finance Subcommittee - CANCELLED' -> 'finance subcommittee'."""
    name = STATUS_IN_NAME.sub(" ", DOCUMENT_WORDS.sub(" ", MOVED_FROM.sub(" ", name)))
    return words(re.sub(r"[^A-Za-z]+", " ", name))


def kind_of(label: str, settings: dict) -> str | None:
    """"minutes", "agenda" or None, by the town's patterns for a link's label (minutes first,
    so "Special Meeting Minutes" is minutes where "Special Meeting" is an agenda)."""
    if re.search(settings.get("minutes_pattern", r"\bminutes\b"), label, re.I):
        return "minutes"
    if re.search(settings.get("agenda_pattern", r"\bagenda\b"), label, re.I) and not re.search(r"\baddend", label, re.I):
        return "agenda"
    return None


def meeting_fields(links: list[dict], settings: dict) -> dict:
    """What a meeting's links are: its agenda and minutes (agenda_url, minutes_url; documents_url
    is the agenda), its recording (video_id, and video_start in seconds), and every other link,
    kept as `links` with the page's own label. The page lists the newest first, so the first
    listed of each wins ("Amended Agenda" above "Agenda", "Agenda - 2" above "Agenda - 1"), and
    the others are kept as links."""
    fields, others = {}, []
    for link in links:
        video = finalsite.recording(link["url"])
        kind = kind_of(link["text"], settings) if finalsite.is_document(link["url"]) else None
        if video and "video_id" not in fields:
            fields.update(video)
        elif kind and f"{kind}_url" not in fields:
            fields[f"{kind}_url"] = link["url"]
        elif link not in others:
            others.append(link)
    if fields.get("agenda_url"):
        fields["documents_url"] = fields["agenda_url"]
    fields["links"] = others
    return fields


def meetings(page: str, page_url: str, settings: dict, board: str | None = None) -> tuple[list[dict], list[str]]:
    """The page's meetings, newest listed first as the page lists them, each with its date, board,
    name, status, whether it's special, its time if given, and its documents (as
    meeting_fields gives them); and the entries with a document but no date, or no
    board, reported rather than guessed at. Links whose label matches exclude_pattern (a
    translation, say) are left out. `board` is the board of a page of one board's meetings
    ([page_meetings.pages]): every meeting on it is that board's, and its key names it, so
    two boards' meetings on one day are kept apart."""
    exclude = settings.get("exclude_pattern")
    bodies, default = ({}, board) if board else (settings.get("bodies", {}), settings.get("body"))
    grouped, unrecognized = {}, []
    for entry in entries(page, page_url, settings.get("name_columns")):
        links = [link for link in entry["links"] if not (exclude and re.search(exclude, link["text"], re.I))]
        documents = [link for link in links if finalsite.is_document(link["url"]) or finalsite.recording(link["url"])]
        if not documents:
            continue
        name = entry["name"]
        body = find_board(name, [], bodies) or default
        if not entry["date"] or not body:
            unrecognized.append(entry["raw"])
            continue
        key = (entry["date"], meeting_key(name))
        meeting = grouped.setdefault(key, {"date": entry["date"], "name": name, "body": body, "time": None, "links": []})
        meeting["time"] = meeting["time"] or entry["time"]
        meeting["links"].extend(links)
    found = []
    for (day, key), m in grouped.items():
        # "Rescheduled from 8/10" says this is the meeting's new date: it takes place.
        info = parse_name(MOVED_FROM.sub(" ", m["name"]).strip() or m["body"], [], bodies)
        special = bool(re.search(r"\bspecial\b", m["name"], re.I)) and "special" not in words(m["body"]).split()
        fields = meeting_fields(m["links"], settings)
        name_key = "-".join(key.split()) or "meeting"
        found.append({"key": f"{day}-{slug(board)}-{name_key}" if board else f"{day}-{name_key}", "date": day, "body": m["body"],
                      "name": m["name"], "status": info["status"], "special": special,
                      "start_time": m["time"], **fields})
    return found, unrecognized


# ---- The town's config ------------------------------------------------------------------

def check(config: dict) -> None:
    """A town's [page_meetings] table, checked when its config is loaded."""
    settings = config.get("page_meetings")
    if settings is None:
        return
    where = f"[page_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("page_url", "source_name", "since", "body") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the page that links the documents, the district's "
                         "name, the first meeting date to collect, and the board its meetings are of.")
    if not re.match(r"https?://", str(settings["page_url"])):
        raise SystemExit(f'{where}: page_url must be the page\'s address, like "https://www.example.k12.ma.us/minutes".')
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-07-01".')
    bodies = settings.get("bodies", {})
    if not isinstance(bodies, dict) or not all(isinstance(v, str) and v for v in bodies.values()):
        raise SystemExit(f'{where}: bodies must map names on the page to board names, like '
                         '"Policy Subcommittee" = "School Committee Policy Subcommittee".')
    pages = settings.get("pages", {})
    if not isinstance(pages, dict) or not all(re.match(r"https?://", str(url)) and isinstance(b, str) and b
                                              for url, b in pages.items()):
        raise SystemExit(f'{where}: pages must map the address of a page of one board\'s meetings to that board, '
                         'like "https://www.example.k12.nh.us/board/policy" = "Board of Education Policy Committee".')
    for key in ("agenda_pattern", "minutes_pattern", "exclude_pattern"):
        if key in settings:
            try:
                re.compile(settings[key])
            except (re.error, TypeError) as e:
                raise SystemExit(f"{where}: {key} isn't a regular expression ({e}).") from None
    for key in ("recheck_days", "max_documents_per_run"):
        if key in settings and not (isinstance(settings[key], int) and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
