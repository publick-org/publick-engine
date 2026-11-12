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
- A table whose links are labelled only with their meeting's date, with the
  column they're in saying what they are. Westbrook School Department's School
  Committee page (Apptegy) has, under "Meeting Information 2025-2026", rows of
  a date and two links both labelled "January 14, 2026", under the columns
  "AGENDA/BACK-UP INFORMATION" and "APPROVED MINUTES"; its committees' pages
  have "Agenda & Information" and "Finance Meeting Report". Above the table, a
  "Meeting Announcement" names the next meeting ("The Westbrook School
  Committee will meet on Wednesday, October 14, 2026 at 7:00 PM in Room 114 at
  Westbrook High School") and links its agenda ("AGENDA & Back Up
  Informaton"), as a line of a date with its documents under it.

- Columns, each a board's. Biddeford Schools' "Agendas and Minutes" page
  (Apptegy) has a row of four headings ("School Committee", "Standing
  Committee on Finance/Building & Grounds", "Advisory Committee on
  Curriculum", "Advisory Committee on Policy") and under it a row of four
  columns, each an accordion of school years with a link a meeting, its label
  the meeting's date and time ("October 28, 2026(7:00pm)"), to its packet, a
  Google Doc. [page_meetings] columns maps a heading to its board, and each
  column is read as a page of that board's meetings (apptegy.column_meetings).

- A folder of files, a board's year of agendas or minutes. Manchester School
  District's documents (Apptegy, /documents/.../<folder id>) list each file by
  the name it was given ("2026-10-13 BOSC Agenda", "2026-9-16 Finance and
  Facilities Minutes"), in the page's Nuxt payload, not as links
  (apptegy.folder()). Only the files that are an agenda or minutes are read,
  newest first, so a memo or handout filed with them isn't taken for a
  meeting; and a folder of years ("2024", "2025", "2026") is read through the
  years from since on (apptegy.year_folders(); the caller reads them).

A link inside a table row belongs to that row's meeting; any other link with a
date in its label is an entry of its own; and a document link without one
belongs to the last short line of text with a date before it. An entry's date is the first date in its text, written out
("September 16, 2026", "Sept. 16 2026"), as "2026-09-16", or in figures, month first: with
slashes and a two- or four-digit year (Hamden Public Schools' "10/13/26"), with dots and a two- or
four-digit year ("10.07.26", "05.06.2026", as Bristol Public Schools titles its minutes), or with
dashes and a four-digit year ("10-14-2026", "9-9-2026": Winooski School District's "Agenda
10-14-2026 WSD Regular Board Meeting"). Its meeting's
name is the rest of the text: for a row, its cells without their links'
labels and without cells that only repeat their column's heading; for a link,
its label without the date and the words "agenda" and "minutes"; for a line,
the rest of the line. A board whose page names one meeting a day in more than
one way (Westbrook's announcement, and the row of the same date in the year's
table, with no name at all) sets one_meeting_a_day: its entries of a date are
then one meeting of their board, whatever else their text says. Entries of
the same date and name are one meeting, so "June 10, 2026 Special Meeting"
and "June 10 2026 Special Meeting Minutes" are documents of one meeting, and
"September 16, 2026 Agenda" and "September 16, 2026 Minutes" of another.
Words that name a meeting one way on its agenda and another on its minutes
(Winooski's agenda "WSD Regular Board Meeting", minutes "Regular Board
Meeting") are left out of that comparison by name_ignore_pattern.

A district can post its agendas on one page and its minutes on another
(Winooski's): other_pages are read with the page, as one list.

An Apptegy short link (aptg.co/...), as South Burlington School District
labels some agendas and minutes, redirects to its file; the caller resolves
it (resolve), and the file it leads to is the document, as a link to it would
be.

What each link is comes from its label: minutes if it matches the town's
minutes_pattern (default "minutes"), an agenda if it matches agenda_pattern
(default "agenda"); a YouTube link is the meeting's recording. A link in a
table labelled only with a date is named by its column's heading instead, so
Westbrook's "January 14, 2026" under "APPROVED MINUTES" is the minutes, and
one under "Finance Meeting Report" is a link of that name. Waltham's
agenda_pattern also takes "Special Meeting", "Budget Workshop" and "Public
Hearing", the names it gives those meetings' agendas. Other links (postings,
packets, folders, videos on Drive) are kept with the meeting as links.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from pipeline import agenda_minutes, apptegy, dates, finalsite
from pipeline.meeting_names import find_board, parse_name, words

SOURCE = "page"
# What a page of [page_meetings.pages] may set of its own, over the table's (pipeline/fetch_page_meetings.py).
PAGE_SETTINGS = ("source_name", "one_meeting_a_day", "exclude_pattern", "name_ignore_pattern", "other_pages")

ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
LINK = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
# The page outside its tables, a piece at a time: a link, a tag, or text.
TOKEN = re.compile(r'(?P<link><a\b[^>]*\bhref="(?P<href>[^"]+)"[^>]*>(?P<label>.*?)</a>)|(?P<tag><[^>]+>)|(?P<text>[^<]+)',
                   re.S | re.I)
# Tags that end a line of text.
BLOCK_TAG = re.compile(r"</?(?:p|div|li|ul|ol|h[1-6]|br|section|header|article|table|dt|dd)\b", re.I)
# A line longer than this is a paragraph, not a meeting's heading. Westbrook's committee
# announcements run to 125 characters ("The Policy Committee will meet on Wednesday, October 28, 2026
# at 5:00 PM in room 114 at Central Office (117 Stroudwater Street)").
HEADING_LENGTH = 160
# A Google Drive folder: a link to a meeting's files, never itself saved.
DRIVE_FOLDER = re.compile(r"^https://drive\.google\.com/drive/(?:u/\d+/)?folders/", re.I)
# "6:00 PM", "6 p.m.", "6:30pm": a meeting's time, where an entry gives one.
TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.I)
# Words in a link's label that say which document it is, not which meeting.
MOVED_FROM = re.compile(r"\b(?:rescheduled|moved|postponed)\s+from\b.*", re.I)
DOCUMENT_WORDS = re.compile(r"\b(?:amended|revised|original|draft|approved|agenda|minutes)\b", re.I)
STATUS_IN_NAME = re.compile(r"\b(?:cancell?ed|postponed|rescheduled)\b", re.I)


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def find_date(value: str) -> dates.Found | None:
    """The first date in a text (pipeline/dates.py): "2026-9-16" (Manchester School District's file
    names), "10/13/26", "10-14-2026" (Winooski School District's labels), "05.06.2026" (Bristol Public
    Schools' minutes), "Sept. 2, 2026". A page's figures with dashes and a two-digit year could be a
    phone number's, so not "10-14-26"."""
    found = dates.find_dates(value, short_dashed_years=False)
    return found[0] if found else None


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


def only_a_date(label: str) -> bool:
    """Whether a link's label is a date and nothing else ("January 14, 2026", "2026-01-14")."""
    dated = find_date(label)
    return bool(dated) and not re.sub(r"[\W_]+", "", without(label, dated))


def row_links(row: str, headings: list[str], page_url: str) -> list[dict]:
    """A table row's links, in order. A link labelled only with a date (Westbrook's "January 14, 2026")
    says nothing of what it is: its column's heading does ("APPROVED MINUTES"), and is its label. So
    does a cell's one link whose cell, read as one word, is a date ("J" and a link "anuary 14, 2026").
    A row whose cells can't be matched to its links (a link outside any cell) keeps its links' own
    labels."""
    links = links_in(row, page_url)
    by_cell = []
    for i, cell in enumerate(CELL.findall(row)):
        in_cell = links_in(cell, page_url)
        whole = len(in_cell) == 1 and only_a_date(html.unescape(re.sub(r"<[^>]+>", "", cell)))
        by_cell += [(i, link, whole or only_a_date(link["text"])) for link in in_cell]
    if [link for _, link, _ in by_cell] != links:
        return links
    return [{**link, "text": headings[i]} if dated and i < len(headings) and headings[i] else link
            for i, link, dated in by_cell]


def without(value: str, found: dates.Found | None) -> str:
    return value if found is None else value[:found.start] + " " + value[found.end:]


def tidy(value: str) -> str:
    """A meeting's name: its words, without stray punctuation around them ("(Special BOE)" ->
    "Special BOE")."""
    value = re.sub(r"^[\s\-–:*,.]+|[\s\-–:*,.]+$", "", value)
    if re.fullmatch(r"\([^()]*\)", value):
        value = value[1:-1]
    return re.sub(r"\s{2,}", " ", value).strip()


def row_name(row: str, headings: list[str], columns: list[str] | None = None) -> str:
    """A table row's meeting name: its cells without the date, their links' labels, any cell
    that only repeats its column's heading (a cell with nothing posted yet), and a note marked with
    an asterisk next to a document ("Minutes *pending approval of BOE", Hamden's), which goes when
    the minutes are approved. With columns (headings,
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
        value = tidy(re.sub(r"\*.*", " ", value)) if value.startswith("*") else value
        if value and words(value) != words(heading):
            parts.append(value)
    return tidy(" ".join(parts))


# A link whose quotes are escaped (href=\\\"...\\\"): the page's content is HTML inside a JSON
# string in a script, as on Worcester Public Schools' Apptegy site.
ESCAPED_LINK = re.compile(r'<a\b[^>]*\bhref=\\+"')


def readable(page: str) -> str:
    """The page's HTML to read: without its scripts and styles, or, for a page whose content is HTML
    escaped inside a script's JSON, that HTML with its quotes and slashes read back (its scripts kept,
    since the content is in one)."""
    if not ESCAPED_LINK.search(page):
        return re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<style\b.*?</style>", " ", page, flags=re.S | re.I)
    return re.sub(r'\\+/', "/", re.sub(r'\\+"', '"', page))


def as_links(page: str, settings: dict) -> str:
    """The page to read: an Apptegy document folder as the list of links its files would be, any other page
    as it is."""
    items = apptegy.folder(page)
    return page if items is None else apptegy.folder_links(items, lambda name: kind_of(name, settings))



def entries(page: str, page_url: str, name_columns: list[str] | None = None) -> list[dict]:
    """Each table row with a link, and each link outside a table row: its date (or None), the
    meeting's name, any time it gives, and its links, in the page's order."""
    page = readable(page)
    found, headings = [], []
    for row in ROW.finditer(page):
        links = row_links(row.group(1), headings, page_url)
        cells = [text(c) for c in CELL.findall(row.group(1))]
        if not links:
            # A row of headings, kept to tell an empty cell ("Minutes") from a meeting's name, and to
            # say what a link labelled only with a date is. A row with no text at all is a spacer.
            if any(cells) and not find_date(" ".join(cells)):
                headings = cells
            continue
        raw = text(row.group(1))
        dated = find_date(raw)
        found.append({"start": row.start(), "date": dated.day.isoformat() if dated else None,
                      "name": row_name(row.group(1), headings, name_columns), "time": time_of(without(raw, dated)),
                      "links": links, "raw": raw})
    outside = ROW.sub(lambda m: " " * len(m.group(0)), page)
    heading, buffer = None, []

    def flush(at: int):
        # A short line of text with a date ("August 24, 2026 - Public Hearing") heads the
        # undated links after it, until the next such line.
        nonlocal heading
        line = " ".join(" ".join(buffer).split())
        buffer.clear()
        dated = find_date(line) if len(line) <= HEADING_LENGTH else None
        if dated:
            rest = without(line, dated)
            heading = {"start": at, "date": dated.day.isoformat(), "name": tidy(rest), "time": time_of(rest), "links": [],
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
            dated = find_date(label)
            if dated:
                rest = without(label, dated)
                found.append({"start": token.start(), "date": dated.day.isoformat(), "name": tidy(DOCUMENT_WORDS.sub(" ", rest)),
                              "time": time_of(rest), "links": [target], "raw": label})
            elif heading and (finalsite.is_document(target["url"]) or finalsite.recording(target["url"])):
                heading["links"].append(target)
    flush(len(outside))
    found = [e for e in found if e["links"]]
    return [{k: v for k, v in e.items() if k != "start"} for e in sorted(found, key=lambda e: e["start"])]


def slug(value: str) -> str:
    return "-".join(words(re.sub(r"[^A-Za-z0-9]+", " ", value)).split())


def meeting_key(name: str, ignore: str | None = None) -> str:
    """What tells a day's meetings apart, the same when the district later marks one cancelled or
    amended: 'Finance Subcommittee - CANCELLED' -> 'finance subcommittee'. Words matching ignore
    ([page_meetings] name_ignore_pattern) are left out too."""
    if ignore:
        name = re.sub(ignore, " ", name, flags=re.I)
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


def meetings(page: str, page_url: str, settings: dict, board: str | None = None,
             more: list[tuple[str, str]] = (), resolve=None) -> tuple[list[dict], list[str]]:
    """The page's meetings, newest listed first as the page lists them, each with its date, board,
    name, status, whether it's special, its time if given, and its documents (as
    meeting_fields gives them); and the entries with a document but no date, or no
    board, reported rather than guessed at. Links whose label matches exclude_pattern (a
    translation, say) are left out. `board` is the board of a page of one board's meetings
    ([page_meetings.pages]): every meeting on it is that board's, and its key names it, so
    two boards' meetings on one day are kept apart. `more`, (page, address) pairs, are read
    with the page as one list ([page_meetings] other_pages). resolve(url), if given, is where a
    short link (apptegy.SHORT_LINK) leads, or None; it's asked only for entries dated from `since` on. With
    [page_meetings] columns, an Apptegy page's columns are read instead, each as a page of its board's
    meetings (apptegy.column_meetings). With one_meeting_a_day, a date's entries are one meeting of
    their board, keyed by the date (and the page's board), whatever their names; the shortest name is
    the meeting's (a table row's, over an announcement's sentence)."""
    if board is None and settings.get("columns"):
        # An Apptegy page whose columns are each a board's (apptegy.column_meetings).
        return apptegy.column_meetings(page, settings["columns"],
                                       lambda column, column_board: meetings(column, page_url, settings, column_board,
                                                                             resolve=resolve))
    exclude, ignore = settings.get("exclude_pattern"), settings.get("name_ignore_pattern")
    one_a_day = settings.get("one_meeting_a_day", False)
    bodies, default = ({}, board) if board else (settings.get("bodies", {}), settings.get("body"))
    grouped, unrecognized = {}, []
    listed = entries(as_links(page, settings), page_url, settings.get("name_columns"))
    for other, url in more:
        listed += entries(as_links(other, settings), url, settings.get("name_columns"))
    for entry in listed:
        links = [link for link in entry["links"] if not (exclude and re.search(exclude, link["text"], re.I))]
        if resolve and entry["date"] and entry["date"] >= str(settings.get("since", "")):
            links = [{**link, "url": resolve(link["url"]) or link["url"]} if apptegy.SHORT_LINK.match(link["url"]) else link
                     for link in links]
        documents = [link for link in links if finalsite.is_document(link["url"]) or finalsite.recording(link["url"])]
        # A dated entry whose agenda or minutes is a Drive folder (Derry's of October 27, 2026: "agenda" and
        # "attachments" folders, and a video) is still a meeting: listed, its folders kept as links, never
        # downloaded.
        folders = [link for link in links if DRIVE_FOLDER.search(link["url"]) and kind_of(link["text"], settings)]
        if not documents and not (folders and entry["date"]):
            continue
        name = entry["name"]
        body = find_board(name, [], bodies) or default
        if not entry["date"] or not body:
            unrecognized.append(entry["raw"])
            continue
        key = (entry["date"], slug(body) if one_a_day and not board else "" if one_a_day else meeting_key(name, ignore))
        meeting = grouped.setdefault(key, {"date": entry["date"], "name": name, "body": body, "time": None, "links": [],
                                           "names": []})
        meeting["names"].append(name)
        if one_a_day and len(name) < len(meeting["name"]):
            meeting["name"] = name
        meeting["time"] = meeting["time"] or entry["time"]
        meeting["links"].extend(links)
    found = []
    for (day, key), m in grouped.items():
        # "Rescheduled from 8/10" says this is the meeting's new date: it takes place.
        info = parse_name(MOVED_FROM.sub(" ", m["name"]).strip() or m["body"], [], bodies)
        # Entries taken as one meeting (one_meeting_a_day): a status any of them gives ("CANCELLED" in an
        # announcement whose table row doesn't say it) is the meeting's, though the shortest name is shown.
        said = [parse_name(MOVED_FROM.sub(" ", n).strip() or m["body"], [], bodies)["status"] for n in m["names"]]
        status = next((s for s in said if s != "scheduled"), info["status"])
        special = bool(re.search(r"\bspecial\b", m["name"], re.I)) and "special" not in words(m["body"]).split()
        fields = meeting_fields(m["links"], settings)
        name_key = "-".join(key.split()) or ("" if board and one_a_day else "meeting")
        page_key = "-".join(p for p in (slug(board), name_key) if p) if board else name_key
        found.append({"key": f"{day}-{page_key}", "date": day, "body": m["body"],
                      "name": m["name"], "status": status, "special": special,
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
    page_example = ('like "https://www.example.k12.nh.us/board/policy" = "Board of Education Policy Committee", or to a '
                    'table of the board and the page\'s own settings, like { board = "School Committee", source_name = '
                    '"Example School District", one_meeting_a_day = false }')
    if not isinstance(pages, dict) or not all(re.match(r"https?://", str(url)) and (isinstance(b, str) and b or isinstance(b, dict))
                                              for url, b in pages.items()):
        raise SystemExit(f"{where}: pages must map the address of a page of one board's meetings to that board, {page_example}.")
    for url, page in pages.items():
        if not isinstance(page, dict):
            continue
        what = f"{where}: the page {url}"
        unknown = set(page) - {"board", *PAGE_SETTINGS}
        if unknown or not (isinstance(page.get("board"), str) and page["board"]):
            raise SystemExit(f"{what} needs a board, and may set only {', '.join(PAGE_SETTINGS)}"
                             + (f" (not {', '.join(sorted(unknown))})" if unknown else "") + f": {page_example}.")
        if "source_name" in page and not (isinstance(page["source_name"], str) and page["source_name"]):
            raise SystemExit(f"{what}: source_name must name who posts the page's documents.")
        if not isinstance(page.get("one_meeting_a_day", False), bool):
            raise SystemExit(f"{what}: one_meeting_a_day must be true or false.")
        if not isinstance(page.get("other_pages", []), list) or not all(re.match(r"https?://", str(u))
                                                                        for u in page.get("other_pages", [])):
            raise SystemExit(f"{what}: other_pages must list addresses of more pages of its board's documents.")
        for key in ("exclude_pattern", "name_ignore_pattern"):
            if key in page:
                try:
                    re.compile(page[key])
                except (re.error, TypeError) as e:
                    raise SystemExit(f"{what}: {key} isn't a regular expression ({e}).") from None
    others = settings.get("other_pages", [])
    if not isinstance(others, list) or not all(re.match(r"https?://", str(url)) for url in others):
        raise SystemExit(f'{where}: other_pages must list the addresses of more pages of the board\'s documents, like '
                         '["https://www.example.org/board/minutes/"].')
    for key in ("agenda_pattern", "minutes_pattern", "exclude_pattern", "name_ignore_pattern"):
        if key in settings:
            try:
                re.compile(settings[key])
            except (re.error, TypeError) as e:
                raise SystemExit(f"{where}: {key} isn't a regular expression ({e}).") from None
    for key in ("recheck_days", "max_documents_per_run"):
        if key in settings and not (isinstance(settings[key], int) and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
    if not isinstance(settings.get("one_meeting_a_day", False), bool):
        raise SystemExit(f"{where}: one_meeting_a_day must be true or false.")
    # The settings of one platform or one way of posting, read by its own module.
    apptegy.check_columns(settings, where)
    agenda_minutes.check(settings, where)
