"""A city's meetings, agendas and minutes from its ProudCity website.

ProudCity is a WordPress platform for city websites (its plugins are named wp-proud-*). Its Meetings
feature gives each meeting a post of the type "meeting", listed by the site's REST API at
/wp-json/wp/v2/meetings (pipeline/wordpress.py) and shown at /meetings/<slug>/. Holyoke, Massachusetts
posts every board's meetings there: about 550 a year, from the City Council and its committees to the
School Committee, the Planning Board and the License Board.

The API lists each post's id, address, title and when it was last changed (`modified`), but not its
body. Each meeting's page has the rest:
- a header with the meeting's date ("Sep 15 2026"), time ("6:30 pm"), place ("City Hall Holyoke") and
  address ("536 Dwight St, Holyoke 01040"). The date is read from here, not from the title: a title can
  carry the wrong year ("Parent and Community Engagement Committee, October 5, 2025", held 2026-10-05);
- tabs, each shown only when it has something:
  - Agenda: the agenda typed in as text, and an attached file (a PDF, or a Word file) beside it;
  - Agenda Packet: links to the packet's files, or to a Google Drive folder of them;
  - Minutes: the minutes as an attached file;
  - Video: the meeting's recording, a YouTube video.
An attached file is a document box with a Download button (an <a> with a `download` attribute) to a
file in ProudCity's storage (storage.googleapis.com/proudcity/<site>/...).

A meeting's board comes from its title: [proudcity_meetings.bodies] maps names, or words in them, to the
site's boards (the longest found wins), else `body`; a title with neither is reported, not guessed at,
and its page isn't read. exclude_pattern leaves out posts that aren't a board's meeting by their title
(Holyoke's School Committee's school walkthroughs and site councils). "Cancelled" in a title cancels the
meeting (pipeline/meeting_names.py), and "Special" makes it a special meeting.

The agenda is the attached file, else the agenda's text, saved as a PDF of its paragraphs
(pipeline/wordfile.py). A post can be made months ahead with only the meeting's notice in its Agenda tab
("AGENDA PENDING"), and the agenda filled in days before the meeting. The minutes are the Minutes tab's file. A board can link its minutes only from a
later meeting's packet instead (Holyoke's School Committee: "9.14.26 School Committee Meeting Minutes",
a Google Doc, in the packet of 2026-10-13). A link in the Agenda or Agenda Packet tab whose line says
"minutes" and whose label has a date (pipeline/agenda_minutes.py) is the minutes of the meeting that day
of the board its label names (bodies), or else of the linking meeting's board. Packet files are kept as
links, never downloaded.

This module has the parts that need no network or storage; the run is
pipeline/fetch_proudcity_meetings.py.
"""

from __future__ import annotations

import html
import re
from urllib.parse import unquote, urljoin, urlsplit

from pipeline import agenda_minutes, dates, wordpress
from pipeline.finalsite import recording
from pipeline.linkpage import links_in, time_of
from pipeline.meeting_names import find_board, parse_name

SOURCE = "proudcity"
# The REST route that lists a ProudCity site's meetings, and the fields read from it.
ROUTE = "wp/v2/meetings"
LISTING_FIELDS = ("id", "link", "title", "date", "modified")
# What on a site's homepage says it's ProudCity: its core plugin's files.
MARK = "wp-proud-core"

H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S)
DATE_BOX = re.compile(r'class="date-box"[^>]*>(.*?)</div>', re.S)
START_TIME = re.compile(r'class="start-time"[^>]*>(.*?)</span>', re.S)
LOCATION = re.compile(r'class="location"[^>]*>(.*?)</span>', re.S)
# The address, the line under the time and place.
ADDRESS = re.compile(r'class="location"[^>]*>[^<]*</span>\s*</h3>\s*<h6\b[^>]*>(.*?)</h6>', re.S)
TAB = re.compile(r'<div\s+id="tab-([\w-]+)"\s+class="tab-pane\b[^>]*>')
# Where an Agenda tab's text ends: the attached file's box beside it.
FILE_BOX = re.compile(r'<div\s+class="col-md-3\b')
DOWNLOAD = re.compile(r'<a\b(?=[^>]*\bdownload\b)[^>]*\bhref="([^"]+)"', re.S)
LINK = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.S)
FRAME = re.compile(r'<iframe\b[^>]*\bsrc="([^"]+)"', re.S)
BLOCK_END = re.compile(r"</(?:p|li|h[1-6]|tr|div|blockquote)>", re.I)


def text(fragment: str) -> str:
    return wordpress.rendered(fragment)


# ---- The listing --------------------------------------------------------------------------

def listing_params(modified_after: str) -> dict:
    """The listing's query: every meeting post changed after a time, the most recently changed first."""
    return {"orderby": "modified", "order": "desc", "modified_after": modified_after}


def parse_listing(items: list[dict]) -> list[dict]:
    """The listing's posts: their id, address, title as plain text, the day it was published, when last
    changed, and the date in the title, if it has one (which may be the wrong year)."""
    found = []
    for item in items:
        title = wordpress.rendered(item.get("title"))
        day = dates.find_date(title)
        found.append({"id": str(item["id"]), "link": item["link"], "title": title,
                      "posted": str(item.get("date") or "")[:10], "modified": item["modified"],
                      "title_date": day.isoformat() if day else None})
    return found


def before(post: dict, since: str) -> bool:
    """Whether a listed post's meeting is before `since` by its title, without reading its page: its title's
    date is, and the title's year isn't behind the year it was published, as a mistyped year is
    ("October 5, 2025", posted in September 2026 for a meeting in October 2026)."""
    day = post["title_date"]
    return bool(day) and day < since and not (post.get("posted") and day[:4] < post["posted"][:4])


def name_of(title: str) -> str:
    """A title without its date or status, as a board's posts share it: "CANCELLED Holyoke Local Cultural
    Council Meeting November 10, 2026" -> "Holyoke Local Cultural Council Meeting"."""
    return parse_name(dates.without_dates(title))["title"].strip(" ,-–:")


# ---- A meeting's page ---------------------------------------------------------------------

def tabs(page: str) -> dict[str, str]:
    """Each tab's HTML, by its name ("agenda", "agenda-packet", "minutes", "video")."""
    found = list(TAB.finditer(page))
    end = page.find("</main>")
    return {m.group(1): page[m.end():found[i + 1].start() if i + 1 < len(found) else (end if end > 0 else len(page))]
            for i, m in enumerate(found)}


def paragraphs(fragment: str) -> list[str]:
    """A fragment's text, a paragraph (or list item, heading, table row) at a time, its line breaks kept."""
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    found = []
    for block in BLOCK_END.split(fragment):
        lines = [" ".join(wordpress.rendered(line).split()) for line in re.sub(r"<[^>]+>", " ", block).split("\n")]
        joined = "\n".join(line for line in lines if line)
        if joined:
            found.append(joined)
    return found


def attached(fragment: str, page_url: str) -> dict | None:
    """A tab's attached file: its address, and its file name."""
    found = DOWNLOAD.search(fragment)
    if not found:
        return None
    url = urljoin(page_url, html.unescape(found.group(1)).strip())
    return {"url": url, "file": unquote(urlsplit(url).path.rsplit("/", 1)[-1])}


def links(fragment: str, page_url: str) -> list[dict]:
    """A tab's links, each with its label: not a file's Download button, a mail address, or a phone number."""
    return [link for link in links_in(fragment, page_url) if link["text"] != "Download"]


def video(fragment: str) -> dict | None:
    """The Video tab's YouTube video: its id, and where in it the meeting starts."""
    for url in FRAME.findall(fragment) + [u for u, _ in LINK.findall(fragment)]:
        url = html.unescape(url).strip()
        found = recording("https:" + url if url.startswith("//") else url)
        if found:
            return found
    return None


def parse_meeting(page: str, page_url: str) -> dict:
    """What a meeting's page says: its title, date, start time, place, agenda (its attached file, and
    its text), minutes (the attached file), packet links, recording, and the minutes of other meetings
    it links (linked_minutes, each with its date, address and label)."""
    title, box = H1.search(page), DATE_BOX.search(page)
    start, place, address = START_TIME.search(page), LOCATION.search(page), ADDRESS.search(page)
    day = dates.find_date(text(box.group(1))) if box else None
    location = ", ".join(part for part in (text(place.group(1)) if place else "",
                                            text(address.group(1)).strip(" ,") if address else "") if part)
    parts = tabs(page)
    agenda = parts.get("agenda", "")
    box_at = FILE_BOX.search(agenda)
    linked = [m for name in ("agenda", "agenda-packet") for m in agenda_minutes.minutes_links(parts.get(name, ""))]
    return {
        "title": text(title.group(1)) if title else "",
        "date": day.isoformat() if day else None,
        "start_time": time_of(text(start.group(1))) if start else None,
        "location": location,
        "agenda_file": attached(agenda, page_url),
        "agenda_text": paragraphs(agenda[:box_at.start()] if box_at else agenda),
        "minutes_file": attached(parts.get("minutes", ""), page_url),
        "links": links(parts.get("agenda-packet", ""), page_url),
        "video": video(parts.get("video", "")),
        "linked_minutes": list({m["url"]: m for m in linked}.values()),
    }


def page_url(settings: dict) -> str:
    """The site's list of every meeting post, linked as where the city posts its meetings."""
    return f"{settings['site_url'].rstrip('/')}/meetings/"


# ---- Boards -------------------------------------------------------------------------------

def board_for(title: str, settings: dict) -> str | None:
    """The board a title names ([proudcity_meetings.bodies], the longest found), else `body`."""
    return find_board(dates.without_dates(title), [], settings.get("bodies", {})) or settings.get("body")


def meeting_info(title: str, settings: dict) -> dict | None:
    """A title's board, status and whether it's a special meeting; None for a title exclude_pattern leaves
    out, or one with no board."""
    if excluded(title, settings):
        return None
    body = board_for(title, settings)
    if not body:
        return None
    found = parse_name(dates.without_dates(title), [body], {})
    return {"body": body, "status": found["status"], "special": found["special"]}


def excluded(title: str, settings: dict) -> bool:
    """Whether exclude_pattern leaves a title out."""
    exclude = settings.get("exclude_pattern")
    return bool(exclude and re.search(exclude, title, re.I))


def linked_board(label: str, settings: dict) -> str | None:
    """The board a linked minutes' label names ("6.25.26 Policy and Governance Meeting Minutes"), by
    [proudcity_meetings.bodies]; None if it names none."""
    return find_board(dates.without_dates(label, short_dashed_years=False), [], settings.get("bodies", {}))


# ---- The town's config --------------------------------------------------------------------

def check(config: dict) -> None:
    """A town's [proudcity_meetings] table, checked when its config is loaded."""
    settings = config.get("proudcity_meetings")
    if settings is None:
        return
    where = f"[proudcity_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("site_url", "source_name", "since") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the website's address, who posts it, and the first "
                         "meeting date to collect.")
    wordpress.check_site_url(settings["site_url"], where)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-07-01".')
    bodies = settings.get("bodies", {})
    if not isinstance(bodies, dict) or not all(isinstance(v, str) and v for v in bodies.values()):
        raise SystemExit(f'{where}: bodies must map meeting titles, or words in them, to board names, like '
                         '"Finance Committee" = "City Council Finance Committee".')
    if not bodies and not settings.get("body"):
        raise SystemExit(f"{where} needs bodies, body, or both: the board each meeting is of.")
    if "exclude_pattern" in settings:
        try:
            re.compile(settings["exclude_pattern"])
        except (re.error, TypeError) as e:
            raise SystemExit(f"{where}: exclude_pattern isn't a regular expression ({e}).") from None
    for key in ("max_documents_per_run", "max_pages_per_run"):
        if key in settings and not (isinstance(settings[key], int) and not isinstance(settings[key], bool)
                                    and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
    if not isinstance(settings.get("minutes", True), bool):
        raise SystemExit(f"{where}: minutes must be true or false.")
