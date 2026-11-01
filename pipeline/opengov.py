"""A public body's meetings from Rhode Island's Open Meetings portal (opengov.sos.ri.gov).

Every Rhode Island public body files its meeting notices, agendas and minutes
with the Secretary of State's Open Meetings portal (R.I. Gen. Laws 42-46-6
and 42-46-7), so one reader serves any of them: South Kingstown's School
Committee, which the town's own meeting portal doesn't carry, among them.

The portal's RSS feeds list a public body's meetings by its exact name
(`PublicBodies=`): upcoming (feedType=1), completed (3, its whole history, a
few hundred kilobytes) and cancelled (4). Each item links the meeting's page
(`MeetingInformation?MeetingID=`) and gives its date and time, and its
location before "Filed on". The meeting's page names the body and lists the
agendas filed for it, an amended agenda after the first, and the minutes,
each a file the portal serves from `/Common/DownloadMeetingFiles?FilePath=`.
A page marked "Annual Calendar" (the year's list of regular meetings) or
"Public Announcement" (a hearing or other posting that isn't a meeting
notice) isn't a meeting.

This module has the parts that need no network or storage; the run is
pipeline/fetch_opengov_meetings.py.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from pipeline import clerkbase

SOURCE = "opengov"
BASE = "https://opengov.sos.ri.gov"
FEEDS = {"upcoming": 1, "completed": 3, "cancelled": 4}

# "Agenda filed on Oct  8 2026, 03:43PM by ...", "SC Approved - 9.22.2026, Minutes filed on Sep 22 2026, 06:47PM by ..."
FILE_LINK = re.compile(r"DownloadMeetingFiles\('([^']+)'\)[^>]*>([^<]*)</a>")
FILED_ON = re.compile(r"filed on ([A-Z][a-z]{2})\s+(\d{1,2}) (\d{4}), (\d{1,2}):(\d{2})\s*([AP]M)", re.I)
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def feed_url(feed: str, body: str) -> str:
    return (f"{BASE}/RSSFeed/Rss?feedType={FEEDS[feed]}&entityID=0&filter1=&filter2=&Keywords=&FromDate=&ToDate="
            f"&RefineSearch=&PublicBodies={quote(body)}")


def meeting_url(meeting_id: int | str) -> str:
    return f"{BASE}/OpenMeetingsPublic/MeetingInformation?MeetingID={meeting_id}"


def file_url(path: str) -> str:
    """A filed document's address: '\\Notices\\3959\\2026\\568418.pdf' -> .../DownloadMeetingFiles?FilePath=%5CNotices..."""
    return f"{BASE}/Common/DownloadMeetingFiles?FilePath={quote(path, safe='')}"


def parse_feed(text: str) -> list[dict]:
    """A feed's meetings: id, date, start time, and location, newest or oldest first as the feed has them.
    An empty feed ("No Upcoming Meetings found.") has none."""
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError as e:
        raise ValueError(f"not a feed ({e})") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError(f"not a feed (<{root.tag}>)")
    found = []
    for item in root.iter("item"):
        link = re.search(r"MeetingID=(\d+)", item.findtext("link") or "")
        published = item.findtext("pubDate") or ""
        try:
            when = parsedate_to_datetime(published)
        except (TypeError, ValueError):
            continue
        if not link or when is None:
            continue
        description = html.unescape(item.findtext("description") or "")
        place = re.split(r"<br\s*/?>|Filed on:", description)[0]
        found.append({"id": int(link.group(1)), "date": when.date().isoformat(), "start_time": when.strftime("%H:%M"),
                      "location": " ".join(place.split()).strip(" ,")})
    return found


def _cell(page: str, label: str) -> str:
    """The value beside a label in the meeting page's table: 'Public Body Name:' -> 'South Kingstown School Committee'."""
    found = re.search(rf"{re.escape(label)}\s*</label>.*?</td>\s*<td[^>]*>(.*?)</td>", page, re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", found.group(1))).split()) if found else ""


def filed_at(label: str) -> str | None:
    """'Agenda filed on Oct  8 2026, 03:43PM by ...' -> '2026-10-08T15:43'."""
    found = FILED_ON.search(label)
    if not found:
        return None
    month, day, year, hour, minute, half = found.groups()
    hour = int(hour) % 12 + (12 if half.upper() == "PM" else 0)
    try:
        return datetime(int(year), MONTHS.index(month[:3].lower()) + 1, int(day), hour, int(minute)).isoformat(timespec="minutes")
    except ValueError:
        return None


def parse_meeting(page: str) -> dict:
    """What a meeting's page says: its public body, whether it's a meeting at all (not an annual
    calendar or a public announcement), whether it was cancelled, and its agendas and minutes,
    oldest filed first, each with its path, label, and when it was filed."""
    documents = {"agendas": [], "minutes": []}
    for path, label in FILE_LINK.findall(page):
        path = path.replace("\\\\", "\\")
        # The label ends with who filed it ("... by <name>"); only what and when are kept.
        label = re.sub(r"\s+by\s+.*$", "", " ".join(html.unescape(label).split()))
        kind = "minutes" if re.search(r"\\Minutes\\", path, re.I) or re.search(r"\bminutes filed\b", label, re.I) else "agendas"
        number = re.search(r"(\d+)\.\w+$", path)
        documents[kind].append({"path": path, "label": label, "filed": filed_at(label) or "",
                                "number": number.group(1) if number else None})
    for docs in documents.values():
        docs.sort(key=lambda d: d["filed"])
    cancelled = re.search(r'id="HdnCancelMeetingFlag"[^>]*value="1"', page)
    return {
        "body": _cell(page, "Public Body Name:"),
        "annual_calendar": _cell(page, "Annual Calendar:").lower().startswith("yes"),
        "announcement": _cell(page, "Public Announcement:").lower().startswith("yes"),
        "cancelled": bool(cancelled),
        "address": _cell(page, "Address:"),
        **documents,
    }


# ---- The town's config ------------------------------------------------------------------

def check(config: dict) -> None:
    """A town's [opengov_meetings] table, checked when its config is loaded."""
    settings = config.get("opengov_meetings")
    if settings is None:
        return
    where = f"[opengov_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("page_url", "source_name", "since", "bodies") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the page the site links for these boards, who posts "
                         "them, the first meeting date to collect, and the public bodies to read.")
    if not re.match(r"https?://", str(settings["page_url"])):
        raise SystemExit(f'{where}: page_url must be a web address, like "https://www.skschools.net/school-committee".')
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-01-01".')
    bodies = settings["bodies"]
    if not isinstance(bodies, dict) or not all(isinstance(v, str) and v for v in bodies.values()):
        raise SystemExit(f'{where}: bodies must map public bodies, named exactly as the portal has them, to board '
                         'names, like "South Kingstown School Committee" = "School Committee".')
    for key in ("recheck_days", "max_documents_per_run"):
        if key in settings and not (isinstance(settings[key], int) and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
    if "dockets" in settings:
        clerkbase.check(settings["dockets"], where)
