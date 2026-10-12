"""Parser for a page that links each meeting's documents, one link a document.

Some school districts post their board's agendas and minutes as files (usually
Google Drive files) linked from one page of their website, with no post or
calendar entry a meeting. Two layouts are read:

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

A link inside a table row belongs to that row's meeting; any other link is an
entry of its own. An entry's date is the first date in its text, written out
("September 16, 2026", "Sept. 16 2026") or as "2026-09-16". Its meeting's
name is the rest of the text: for a row, its cells without their links'
labels and without cells that only repeat their column's heading; for a link,
its label without the date and the words "agenda" and "minutes". Entries of
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
    """A meeting's name: its words, without stray punctuation around them."""
    return re.sub(r"\s{2,}", " ", re.sub(r"^[\s\-–:*,.]+|[\s\-–:*,.]+$", "", value)).strip()


def row_name(row: str, headings: list[str]) -> str:
    """A table row's meeting name: its cells without the date, their links' labels, and any cell
    that only repeats its column's heading (a cell with nothing posted yet)."""
    parts = []
    for i, cell in enumerate(CELL.findall(row)):
        value = text(LINK.sub(" ", cell))
        value = text(without(value, find_date(value)))
        heading = headings[i] if i < len(headings) else ""
        if value and words(value) != words(heading):
            parts.append(value)
    return tidy(" ".join(parts))


def entries(page: str, page_url: str) -> list[dict]:
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
                      "name": row_name(row.group(1), headings), "time": time_of(without(raw, day)),
                      "links": links, "raw": raw})
    outside = ROW.sub(lambda m: " " * len(m.group(0)), page)
    for link in LINK.finditer(outside):
        label = text(link.group(2))
        url = html.unescape(link.group(1)).strip()
        day = find_date(label)
        if not day or url.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        rest = without(label, day)
        found.append({"start": link.start(), "date": date_value(day), "name": tidy(DOCUMENT_WORDS.sub(" ", rest)),
                      "time": time_of(rest), "links": [{"url": urljoin(page_url, url), "text": label}], "raw": label})
    return [{k: v for k, v in e.items() if k != "start"} for e in sorted(found, key=lambda e: e["start"])]


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


def meetings(page: str, page_url: str, settings: dict) -> tuple[list[dict], list[str]]:
    """The page's meetings, newest listed first as the page lists them, each with its date, board,
    name, status, whether it's special, its time if given, and its documents (as
    meeting_fields gives them); and the entries with a document but no date, or no
    board, reported rather than guessed at. Links whose label matches exclude_pattern (a
    translation, say) are left out."""
    exclude = settings.get("exclude_pattern")
    bodies, default = settings.get("bodies", {}), settings.get("body")
    grouped, unrecognized = {}, []
    for entry in entries(page, page_url):
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
        found.append({"key": f"{day}-{'-'.join(key.split()) or 'meeting'}", "date": day, "body": m["body"],
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
    for key in ("agenda_pattern", "minutes_pattern", "exclude_pattern"):
        if key in settings:
            try:
                re.compile(settings[key])
            except (re.error, TypeError) as e:
                raise SystemExit(f"{where}: {key} isn't a regular expression ({e}).") from None
    for key in ("recheck_days", "max_documents_per_run"):
        if key in settings and not (isinstance(settings[key], int) and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
