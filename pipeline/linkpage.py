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
  Google Doc. [page_meetings.columns] maps a heading to its board, and each
  column is read as a page of that board's meetings (apptegy_columns).

- A folder of files, a board's year of agendas or minutes. Manchester School
  District's documents (Apptegy, /documents/.../<folder id>) list each file by
  the name it was given ("2026-10-13 BOSC Agenda", "2026-9-16 Finance and
  Facilities Minutes"), in the page's Nuxt payload, not as links
  (apptegy_folder()). Only the files that are an agenda or minutes are read,
  newest first, so a memo or handout filed with them isn't taken for a
  meeting; and a folder of years ("2024", "2025", "2026") is read through the
  years from since on (year_folders(); the caller reads them).

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
import json
import re
from datetime import date
from urllib.parse import unquote, urljoin

from pipeline import finalsite
from pipeline.filelist import DATE_IN_TITLE, MONTHS
from pipeline.meeting_names import find_board, parse_name, words

SOURCE = "page"
# What a page of [page_meetings.pages] may set of its own, over the table's (pipeline/fetch_page_meetings.py).
PAGE_SETTINGS = ("source_name", "one_meeting_a_day", "agenda_pattern", "minutes_pattern", "exclude_pattern",
                 "name_ignore_pattern", "other_pages")

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
# "2026-10-13", and without zeros, "2026-9-16" (Manchester School District's file names).
ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
# "10/13/26", "9/8/2026": month, day and year in figures, the year of two digits taken as 20xx. Not a
# fraction or a part of a longer run of figures and slashes ("8/10", "1/2/3/4").
NUMERIC_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
# "10-14-2026", "9-9-2026": month, day, and a four-digit year (two digits after dashes could be a
# phone number's).
DASHED_DATE = re.compile(r"(?<![\d./-])(\d{1,2})-(\d{1,2})-(\d{4})(?![\d-])")
# A link that redirects to the document it names: an Apptegy short link ("https://aptg.co/Lj5CdR").
SHORT_LINK = re.compile(r"^https?://(?:www\.)?aptg\.co/\w+/?$", re.I)
# A date in figures, month first, with dots: "10.07.26", "05.06.2026" (Bristol Public Schools' minutes).
DOTTED_DATE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?![\d.])")
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


def find_date(value: str) -> re.Match | None:
    """The first date in a text, written out, as yyyy-mm-dd (or yyyy-m-d), as m/d/yy (or m/d/yyyy), as mm.dd.yy, or as
    mm-dd-yyyy."""
    found = [m for m in (ISO_DATE.search(value), DATE_IN_TITLE.search(value), NUMERIC_DATE.search(value),
                         DOTTED_DATE.search(value), DASHED_DATE.search(value)) if m]
    return min(found, key=lambda m: m.start()) if found else None


def date_value(found: re.Match) -> str | None:
    try:
        if found.re is ISO_DATE:
            return date(*(int(g) for g in found.groups())).isoformat()
        if found.re is NUMERIC_DATE:
            month, day, year = (int(g) for g in found.groups())
            return date(year + (2000 if year < 100 else 0), month, day).isoformat()
        if found.re is DASHED_DATE:
            month, day, year = (int(g) for g in found.groups())
            return date(year, month, day).isoformat()
        if found.re is DOTTED_DATE:
            month, day, year = (int(g) for g in found.groups())
            return date(year + 2000 if year < 100 else year, month, day).isoformat()
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


def only_a_date(label: str) -> bool:
    """Whether a link's label is a date and nothing else ("January 14, 2026", "2026-01-14")."""
    day = find_date(label)
    return bool(day) and not re.sub(r"[\W_]+", "", without(label, day))


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


# An Apptegy page's content, as its script holds it: window.clientWorkStateTemp = JSON.parse("...").
APPTEGY_STATE = re.compile(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)')


def apptegy_structure(page: str) -> dict | None:
    """The tree of an Apptegy page's content (page.content.structure in the JSON its script parses):
    nodes of layout rows (LAYOUT_NODE_*), their columns (BASE_COLUMN), and content (CONTENT_NODE_*,
    with its HTML in content.html). None for a page that isn't one."""
    for found in APPTEGY_STATE.finditer(page):
        try:
            state = json.loads(json.loads(f'"{found.group(1)}"'))
        except ValueError:
            continue
        structure = ((state.get("page") or {}).get("content") or {}).get("structure") if isinstance(state, dict) else None
        if isinstance(structure, dict):
            return structure
    return None


def node_html(node: dict) -> str:
    """A node's HTML: its own content's and its children's, in order."""
    content = node.get("content")
    own = content.get("html", "") if isinstance(content, dict) and isinstance(content.get("html"), str) else ""
    return own + "".join(node_html(child) for child in node.get("nodes") or [] if isinstance(child, dict))


def apptegy_columns(page: str) -> dict[str, str]:
    """An Apptegy page's columns that a heading names, as {heading: the column's HTML}. A column's
    heading is its first node, a heading; or, for a column of a row just under a row of as many
    columns each holding only a heading, the heading above it (Biddeford's: a row of four headings,
    then a row of four accordions)."""
    structure = apptegy_structure(page)
    columns: dict[str, str] = {}

    def heading(node: dict) -> str | None:
        return text(node_html(node)) or None if node.get("type") == "CONTENT_NODE_HEADING" else None

    def walk(node: dict) -> None:
        above: list[str] | None = None
        for child in node.get("nodes") or []:
            if not isinstance(child, dict):
                continue
            cells = [c for c in child.get("nodes") or [] if isinstance(c, dict) and c.get("type") == "BASE_COLUMN"]
            if not str(child.get("type", "")).startswith("LAYOUT_NODE") or not cells:
                above = None
                walk(child)
                continue
            contents = [[n for n in c.get("nodes") or [] if isinstance(n, dict)] for c in cells]
            names = [heading(nodes[0]) if nodes else None for nodes in contents]
            if all(len(nodes) == 1 and name for nodes, name in zip(contents, names)):
                above = names
                continue
            for i, nodes in enumerate(contents):
                name = names[i] if nodes and names[i] else above[i] if above and len(above) == len(cells) else None
                rest = nodes[1:] if nodes and names[i] else nodes
                if name:
                    columns[name] = "".join(node_html(n) for n in rest)
            above = None
            for nodes in contents:
                for n in nodes:
                    walk(n)

    if structure:
        walk(structure)
    return columns


# A Nuxt site's state, as the page carries it: a JSON array whose containers hold the indexes of their values.
NUXT_DATA = re.compile(r'<script\b[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S | re.I)
# The wrappers Nuxt puts around a value ("Reactive", index): the value is the index's.
NUXT_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef"}


def apptegy_folder(page: str) -> list[dict] | None:
    """An Apptegy document folder's items, as its page's Nuxt payload has them
    (data["document-folder-id-<id>"].foldersAndDocuments): its files ({"file_name", "url", "position"})
    and its folders ({"folder_name", "url", "isFolder"}). None for a page that isn't one."""
    found = NUXT_DATA.search(page)
    if not found:
        return None
    try:
        data = json.loads(found.group(1))
    except ValueError:
        return None
    if not isinstance(data, list) or not data:
        return None

    def value(i, depth: int = 0):
        # An index into the payload, read back into what it stands for; a negative one is undefined or null.
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(data) or depth > 12:
            return None
        v = data[i]
        if isinstance(v, list):
            if len(v) == 2 and v[0] in NUXT_WRAPPERS:
                return value(v[1], depth + 1)
            return [value(x, depth + 1) for x in v]
        if isinstance(v, dict):
            return {k: value(x, depth + 1) for k, x in v.items()}
        return v

    # The payload's first entry is its root, inside a wrapper ("ShallowReactive", index): read only its "data".
    root, depth = data[0], 0
    while isinstance(root, list) and len(root) == 2 and root[0] in NUXT_WRAPPERS and depth < 5:
        root, depth = data[root[1]] if isinstance(root[1], int) and 0 <= root[1] < len(data) else None, depth + 1
    state = value(root["data"], 1) if isinstance(root, dict) and "data" in root else None
    if not isinstance(state, dict):
        return None
    folders = [f for key, f in state.items() if key.startswith("document-folder-id-") and isinstance(f, dict)]
    if not folders:
        return None
    return [item for f in folders for item in f.get("foldersAndDocuments") or [] if isinstance(item, dict)]


def position(item: dict) -> int:
    try:
        return int(item.get("position") or 0)
    except (TypeError, ValueError):
        return 0


def folder_links(items: list[dict], settings: dict) -> str:
    """An Apptegy folder's agendas and minutes as a list of links, labelled with their files' names, newest
    first (the folder lists them oldest first, by position). Its other files (memos, handouts) are left out."""
    files = sorted((item for item in items if item.get("file_name") and item.get("url")
                    and kind_of(str(item["file_name"]), settings)), key=position, reverse=True)
    return "<ul>" + "".join(f'<li><a href="{html.escape(str(f["url"]))}">{html.escape(str(f["file_name"]))}</a></li>'
                            for f in files) + "</ul>"


def as_links(page: str, settings: dict) -> str:
    """The page to read: an Apptegy document folder as the list of links its files would be, any other page
    as it is."""
    items = apptegy_folder(page)
    return page if items is None else folder_links(items, settings)


def year_folders(page: str, page_url: str, since: str) -> list[str]:
    """An Apptegy document folder's folders named by a year ("2025", "2026"), from since's year on, newest
    first: a board's folder of years, each year's files in a folder of its own."""
    items = apptegy_folder(page) or []
    years = [(int(item["folder_name"]), item["url"]) for item in items
             if item.get("url") and re.fullmatch(r"\d{4}", str(item.get("folder_name") or "").strip())
             and int(item["folder_name"]) >= int(str(since)[:4])]
    return [urljoin(page_url, str(url)) for _, url in sorted(set(years), reverse=True)]


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
    short link (SHORT_LINK) leads, or None; it's asked only for entries dated from `since` on. With
    [page_meetings.columns], the page's columns named by those headings are read instead, each as a
    page of its board's meetings, and a heading not found is reported. With one_meeting_a_day, a date's entries are one meeting of
    their board, keyed by the date (and the page's board), whatever their names; the shortest name is
    the meeting's (a table row's, over an announcement's sentence)."""
    if board is None and settings.get("columns"):
        # Each column a board's ([page_meetings.columns]): read as a page of that board's meetings.
        on_page = {words(name): column for name, column in apptegy_columns(page).items()}
        found, unrecognized = [], []
        for name, column_board in settings["columns"].items():
            if words(name) not in on_page:
                unrecognized.append(f"(no column headed {name!r})")
                continue
            in_column, not_read = meetings(on_page[words(name)], page_url, settings, column_board,
                                           resolve=resolve)
            found += in_column
            unrecognized += not_read
        return found, unrecognized
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
            links = [{**link, "url": resolve(link["url"]) or link["url"]} if SHORT_LINK.match(link["url"]) else link
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


# A Google redirect around a link in a Google Doc's HTML export ("https://www.google.com/url?q=<link>&sa=...").
GOOGLE_REDIRECT = re.compile(r"^https://www\.google\.com/url\?q=([^&]+)")


def minutes_in_agenda(page: str) -> list[tuple[str, str]]:
    """The minutes an agenda links, as (date, link): a paragraph of the agenda that says "minutes" and links
    a document labelled with a date (Biddeford School Committee's packets: "5. Approval of Last Meeting's
    Minutes: 9/23/2026 (Motion Needed)", "9/23/2026" linking that meeting's minutes, a Google Doc). page
    is the agenda as HTML (a Google Doc's export)."""
    found = []
    for paragraph in re.split(r"</p>|</li>", page, flags=re.I):
        if not re.search(r"\bminutes\b", text(paragraph), re.I):
            continue
        for href, label in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', paragraph, re.I | re.S):
            url = html.unescape(href)
            wrapped = GOOGLE_REDIRECT.match(url)
            url = unquote(wrapped.group(1)) if wrapped else url
            day = find_date(text(label))
            if day and date_value(day) and finalsite.is_document(url):
                found.append((date_value(day), url))
    return found


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
        for key in ("agenda_pattern", "minutes_pattern", "exclude_pattern", "name_ignore_pattern"):
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
    columns = settings.get("columns", {})
    if not isinstance(columns, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v
                                                for k, v in columns.items()):
        raise SystemExit(f'{where}: columns must map a column\'s heading on the page to its board, like '
                         '"Advisory Committee on Policy" = "School Committee Policy Committee".')
    if not isinstance(settings.get("one_meeting_a_day", False), bool):
        raise SystemExit(f"{where}: one_meeting_a_day must be true or false.")
    if not isinstance(settings.get("minutes_in_agenda", False), bool):
        raise SystemExit(f"{where}: minutes_in_agenda must be true or false.")
