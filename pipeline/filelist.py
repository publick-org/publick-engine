"""Parsers for a town website with one documents page for every board's agendas
and minutes, laid out as a file list, and a meetings calendar by month.

Wallingford, Connecticut's website is one. Its footer credits Web Solutions, a
Connecticut web firm with a CMS of its own; no other town's site on it has
been found yet, so the reader is named for the page, not the vendor.

The documents page (/minutes-and-agendas/) is a single page of nested lists,
one folder per board, with earlier years in folders of their own ("2025 PZC
Archive"), and every file a link to DownloadFile.aspx?FileID=<number>. Files
aren't in date order. A link's text says what the file is and which meeting
it is for, typed by each board's clerk: "Agenda of Regular Meeting - October 6,
2026", "Amended Agenda of Regular Meeting- January 27, 2026", "Agenda and
Backup of Regular Meeting - September 22, 2026", "Special Meeting
Minutes-April 13, 2026", "Cancellation of Regular Meeting-August 11, 2026".
Its title attribute is the uploaded file's name. Some links carry a YouTube
recording of the meeting (data-video="CyV9OIpJIT8", sometimes with "&t=9805s").

File numbers go up as files are uploaded, so a meeting's newest version of a
document is the one with the highest number. A meeting's agenda is its newest
plain agenda (an amended or corrected one, or a special meeting's notice),
and only when there's none its agenda with backup, which can run to a hundred
scanned pages. Addenda, backup on its own, applications and reports aren't
agendas or minutes, and are left out.

The calendar (/events/meetings/<year>/<Month>/) lists each month's meetings,
with the time, the board's name as the calendar writes it ("Inland Wetland and
Watercourse Commission" for the documents page's "Inland Wetlands &
Watercourses Commission"), and sometimes a PDF labelled "Agenda", "Special
Agenda" or "Cancellation". Its datetime attributes give 7:00 PM as 07:00, so
the time is read from the text. Each meeting's own page gives its location.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date, datetime
from urllib.parse import urljoin

from pipeline.meeting_names import parse_name, words

SOURCE = "filelist"

# A folder (its name in <strong>), a file link (with its title and any video), or the end of a folder.
TOKEN = re.compile(r'<a href="#" class="dir"><strong>(?P<folder>.*?)</strong></a>\s*<ul>'
                   r'|<a href="(?P<href>[^"]*DownloadFile\.aspx\?FileID=(?P<id>\d+))"(?P<attrs>[^>]*)>(?P<text>.*?)</a>'
                   r'|(?P<close></ul>)', re.S)
TITLE_ATTR = re.compile(r'title="([^"]*)"')
VIDEO = re.compile(r'data-video="([^"]+)"')
BUTTON = re.compile(r"<button.*?</button>", re.S)

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
# "October 6, 2026", "Feb 11, 2026", "Sept. 9 2026", "November 10,2025".
DATE_IN_TITLE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{1,2}),?\s*(\d{4})\b", re.I)
# "BOAAminutes3.11.26.pdf", "IWWCAgenda9-2-26.pdf": the file name's date, for a title without one.
DATE_IN_NAME = re.compile(r"(?<!\d)(\d{1,2})[._-](\d{1,2})[._-](\d{4}|\d{2})(?!\d)")

SECTION = re.compile(r"<section>\s*<h1>(.*?)</h1>(.*?)</section>", re.S)
ARTICLE = re.compile(r"<article[^>]*>(.*?)</article>", re.S)
EVENT_LINK = re.compile(r'<a href="(/events/\d{4}/\d{2}/\d{2}/([^/"]+)/?)">(.*?)</a>', re.S)
TIME = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)", re.I)
LINK = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
LOCATION = re.compile(r"</time>\s*<p>(.*?)</p>", re.S)


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# ---- The documents page ----------------------------------------------------------

def parse_documents(page: str, base_url: str) -> list[dict]:
    """Every file on the documents page, with the folders it is in (the board's first)."""
    start = page.find('<ul class="fileList">')
    if start < 0:
        return []
    folders: list[str] = []
    files = []
    for token in TOKEN.finditer(page, start):
        if token["folder"] is not None:
            folders.append(text(token["folder"]))
        elif token["close"]:
            if not folders:
                break  # the end of the list
            folders.pop()
        elif folders:
            title = TITLE_ATTR.search(token["attrs"])
            video = VIDEO.search(token["text"])
            files.append({
                "folders": list(folders),
                "file_id": int(token["id"]),
                "url": urljoin(base_url, html.unescape(token["href"])),
                "title": text(BUTTON.sub("", token["text"])),
                "filename": html.unescape(title.group(1)) if title else "",
                **(recording(html.unescape(video.group(1))) if video else {}),
            })
    return files


def recording(value: str) -> dict:
    """'4LeVKM87A-I&t=9805s' -> the YouTube video's id, and where in it the meeting starts."""
    video_id, _, rest = value.partition("&")
    start = re.search(r"\bt=(\d+)s?\b", rest)
    return {"video_id": video_id.strip(), **({"video_start": int(start.group(1))} if start else {})}


def kind_of(title: str) -> str | None:
    """What a document is, from its title: "agenda", "minutes", "cancellation", or None
    for anything else (an addendum, backup on its own, an application, a report). A
    special meeting's notice is its agenda: it lists the business to be done."""
    t = title.lower()
    if re.search(r"\baddend", t):
        return None
    if "cancel" in t:
        return "cancellation"
    if "minutes" in t:
        return "minutes"
    if "agenda" in t or re.search(r"\bnotice\b", t):
        return "agenda"
    return None


def date_of(title: str, filename: str = "") -> str | None:
    """The meeting's date, from the title's last date, or else the file name's
    ('Minutes of Regular Meeting- Match 11, 2026' is BOAAminutes3.11.26.pdf)."""
    found = list(DATE_IN_TITLE.finditer(title))
    try:
        if found:
            month, day, year = found[-1].groups()
            return date(int(year), MONTHS.index(month[:3].lower()) + 1, int(day)).isoformat()
        named = DATE_IN_NAME.search(filename)
        if named:
            month, day, year = (int(n) for n in named.groups())
            return date(year + 2000 if year < 100 else year, month, day).isoformat()
    except ValueError:
        pass
    return None


def parse_title(title: str, filename: str = "") -> dict | None:
    """A document's kind, meeting date, and whether it is for a special meeting,
    or None if it isn't a dated agenda, minutes or cancellation."""
    kind, day = kind_of(title), date_of(title, filename)
    if not kind or not day:
        return None
    return {"kind": kind, "date": day, "special": bool(re.search(r"\bspecial\b", title, re.I)),
            "backup": bool(re.search(r"\b(?:backup|packet)\b", title, re.I))}


def folder_board(name: str, boards: list[str], aliases: dict) -> str | None:
    """The listed board a top-level folder is for: its name, or an alias of it, ignoring
    case, punctuation and "&"/"and". Folders are matched whole, so "Town Council
    Archive (1984 - 2022)" isn't the Town Council."""
    for key, target in [*((b, b) for b in boards), *aliases.items()]:
        if words(key) == words(name) and words(target) in {words(b) for b in boards}:
            return next(b for b in boards if words(b) == words(target))
    return None


def split_day(docs: list[dict]) -> list[tuple[bool, list[dict]]]:
    """A board's documents for one day as one meeting, or as a regular and a special
    meeting when both have the same kind of document, or one was cancelled and the
    other not (a regular meeting cancelled for a special one). Clerks label one
    meeting's agenda and minutes differently often enough ("Agenda of Special
    Meeting" with "Minutes of Regular Meeting") that a label alone isn't two meetings."""
    regular = [d for d in docs if not d["special"]]
    special = [d for d in docs if d["special"]]
    kinds = ({d["kind"] for d in regular}, {d["kind"] for d in special})
    if regular and special and (kinds[0] & kinds[1] or ("cancellation" in kinds[0]) != ("cancellation" in kinds[1])):
        return [(False, regular), (True, special)]
    # One meeting: special if its agenda says so (the notice), or else its other documents.
    first = min(docs, key=lambda d: (["agenda", "cancellation", "minutes"].index(d["kind"]), -d["file_id"]))
    return [(first["special"], docs)]


def meeting_documents(files: list[dict], boards: list[str], aliases: dict, since: str) -> list[dict]:
    """The listed boards' meetings since `since`, as found in their documents: each
    one's board, date, agenda, minutes, and whether it was cancelled."""
    by_day: dict[tuple[str, str], list[dict]] = {}
    for f in files:
        board = folder_board(f["folders"][0], boards, aliases)
        info = parse_title(f["title"], f["filename"]) if board else None
        if info and info["date"] >= since:
            by_day.setdefault((board, info["date"]), []).append({**f, **info})
    meetings = []
    for (board, day), docs in sorted(by_day.items()):
        for special, part in split_day(docs):
            part = sorted(part, key=lambda d: d["file_id"])
            agendas = [d for d in part if d["kind"] == "agenda"]
            agenda = ([d for d in agendas if not d["backup"]] or agendas or [None])[-1]
            minutes = ([d for d in part if d["kind"] == "minutes"] or [None])[-1]
            video = next((d for d in (agenda, minutes, *reversed(part)) if d and d.get("video_id")), {})
            meetings.append({
                "body": board,
                "date": day,
                "special": special,
                "first_file_id": part[0]["file_id"],
                "agenda": agenda,
                "minutes": minutes,
                # Minutes mean the meeting was held after all.
                "cancelled": any(d["kind"] == "cancellation" for d in part) and not minutes,
                **{k: video[k] for k in ("video_id", "video_start") if k in video},
            })
    return meetings


def document_fields(meeting: dict) -> dict:
    """A meeting's documents, as fields for its meeting record (see pipeline.fetch_meetings)."""
    fields = {"special": meeting["special"]}
    if meeting["agenda"]:
        fields.update(agenda_id=f"{SOURCE}-{meeting['agenda']['file_id']}", agenda_url=meeting["agenda"]["url"],
                      documents_url=meeting["agenda"]["url"])
    if meeting["minutes"]:
        fields.update(minutes_id=f"{SOURCE}-{meeting['minutes']['file_id']}", minutes_url=meeting["minutes"]["url"])
    if meeting["cancelled"]:
        fields["status"] = "cancelled"
    fields.update({k: meeting[k] for k in ("video_id", "video_start") if k in meeting})
    return fields


# ---- The calendar ---------------------------------------------------------------------

def month_url(calendar_url: str, month: date) -> str:
    """'https://www.wallingfordct.gov/events/meetings/' -> '.../events/meetings/2026/October/'."""
    return f"{calendar_url.rstrip('/')}/{month.year}/{month:%B}/"


def to_time(value: str) -> str:
    """'7:00 PM' -> '19:00'."""
    return datetime.strptime(re.sub(r"\s+", " ", value.strip().upper()), "%I:%M %p").strftime("%H:%M")


def parse_month(page: str, base_url: str, boards: list[str] | None = None, aliases: dict | None = None) -> list[dict]:
    """Every meeting in a month's list view (the calendar view repeats them), with the
    PDFs listed under it: each link's address and label."""
    start, end = page.find('id="listView"'), page.find('id="calendarView"')
    listing = page[start:end if end > start else len(page)] if start >= 0 else ""
    events = []
    for section in SECTION.finditer(listing):
        try:
            day = datetime.strptime(text(section.group(1)), "%A, %B %d, %Y").date().isoformat()
        except ValueError:
            continue
        for article in ARTICLE.findall(section.group(2)):
            link = EVENT_LINK.search(article)
            name = re.search(r"<h1>(.*?)</h1>", link.group(3), re.S) if link else None
            if not name:
                continue
            times = TIME.findall(text(link.group(3)))
            event = {
                "id": f"{SOURCE}-{link.group(2)}",
                "source": SOURCE,
                "source_url": urljoin(base_url, link.group(1)),
                "date": day,
                "start_time": to_time(times[0]) if times else None,
                "end_time": to_time(times[1]) if len(times) > 1 else None,
                "links": [{"url": urljoin(base_url, html.unescape(url)), "text": text(label)}
                          for url, label in LINK.findall(article[link.end():])],
            }
            event.update(parse_name(text(name.group(1)), boards, aliases))
            events.append(event)
    return events


def calendar_documents(event: dict) -> dict:
    """What the PDFs listed under a calendar entry say: whether the meeting was cancelled,
    whether it is special, and its agenda (agenda_id and agenda_url). An agenda posted again
    under a new address is a new version, so the address names the file."""
    found = {}
    for link in event["links"]:
        kind = kind_of(link["text"])
        if kind == "cancellation":
            found["status"] = "cancelled"
        elif kind == "agenda" and link["url"].lower().endswith(".pdf"):
            found.update(agenda_id=f"{SOURCE}-" + hashlib.sha256(link["url"].encode()).hexdigest()[:16],
                         agenda_url=link["url"], documents_url=link["url"])
            if re.search(r"\bspecial\b", link["text"], re.I):
                found["special"] = True
    return found


def title_for(body: str, special: bool) -> str:
    return f"{body} {'Special Meeting' if special else 'Meeting'}"


def parse_event_page(page: str) -> dict:
    """The location from a meeting's own page: 'Wallingford Town Hall<br>Robert F. Parisi
    Council Chambers<br>45 South Main Street<br>Wallingford, CT 06492' is the place's name
    up to the first line that starts with a number, and its address from there."""
    found = LOCATION.search(page)
    lines = [text(line) for line in re.split(r"<br\s*/?>", found.group(1))] if found else []
    lines = [line for line in lines if line]
    street = next((i for i, line in enumerate(lines) if line[0].isdigit()), len(lines))
    return {"location_name": ", ".join(lines[:street]), "address": ", ".join(lines[street:])}


# ---- Both together --------------------------------------------------------------------------

def same_meeting(a: dict, b: dict) -> bool:
    return a["date"] == b["date"] and words(a["body"]) == words(b["body"])


def adopt(store: dict, listed: list[dict]) -> None:
    """Meetings first recorded from their documents (a special meeting's notice is
    often posted before the calendar lists it) that the calendar now lists become
    the calendar's records, keeping their page address and documents. The time
    and title are the calendar's, not changes to record."""
    for e in listed:
        if e["id"] in store:
            continue
        special = bool(e.get("special") or calendar_documents(e).get("special"))
        known = sorted((m for m in store.values() if m["id"].startswith(f"{SOURCE}-file-") and same_meeting(m, e)),
                       key=lambda m: bool(m.get("special")) != special)
        if known:
            m = store.pop(known[0]["id"])
            for field in ("source_url", "title", "start_time", "end_time"):
                m.pop(field, None)
            m["id"] = e["id"]
            store[e["id"]] = m


def combine(listed: list[dict], found: list[dict], recorded: list[dict], documents_url: str,
            folders: set[str]) -> list[dict]:
    """The calendar's meetings with their documents, and the meetings known only from
    their documents, as meeting records for pipeline.fetch_meetings.

    Documents go with the calendar's meeting of the same board and day (a special
    meeting's with the special one, when the calendar's PDF says which), or with a
    meeting already recorded from either; otherwise they are a meeting of their own.
    A board with a folder on the documents page gets its agenda from there; the
    calendar's PDF is the same file, and is used only for a board without one
    (`folders`, the boards found there)."""
    events = []
    for e in listed:
        e = {k: v for k, v in e.items() if k != "links"} | calendar_documents(e)
        if words(e["body"]) in folders:
            for field in ("agenda_id", "agenda_url", "documents_url"):
                e.pop(field, None)
        events.append(e)
    listed_ids = {e["id"] for e in events}
    candidates = events + [m for m in recorded if m["id"] not in listed_ids]
    matched: dict[int, dict] = {}
    claimed: set[str] = set()
    # Same board, day and kind first; then same board and day.
    for exact in (True, False):
        for i, meeting in enumerate(found):
            if i in matched:
                continue
            match = next((c for c in candidates if c["id"] not in claimed and same_meeting(c, meeting)
                          and (not exact or bool(c.get("special")) == meeting["special"])), None)
            if match:
                matched[i] = match
                claimed.add(match["id"])

    by_id = {e["id"]: e for e in events}
    for i, meeting in enumerate(found):
        fields = document_fields(meeting)
        match = matched.get(i)
        if match is None:
            events.append({
                "id": f"{SOURCE}-file-{meeting['first_file_id']}",
                "source": SOURCE,
                "source_url": documents_url,
                "date": meeting["date"],
                "start_time": None,
                "end_time": None,
                "body": meeting["body"],
                "status": "scheduled",
                **fields,
            })
        elif match["id"] in by_id:
            by_id[match["id"]].update(fields)
        else:
            events.append({"id": match["id"], "date": match["date"], "body": match["body"], **fields})
    for e in events:
        e["title"] = title_for(e["body"], bool(e.get("special")))
    return events
