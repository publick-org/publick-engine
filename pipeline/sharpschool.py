"""A board's minutes from a document library on a school district's SharpSchool website.

Springfield Public Schools posts its School Committee's minutes in a document library on its
SharpSchool website (/school_committee/school_committee_meeting-_minutes_archive): a folder a
year ("2026 Meeting Minutes"), each holding a PDF a meeting, named by the clerk with the
meeting's date and kind: "09.10.26 Minutes Regular Meeting", "02.26.26 Minutes Chapter 74
Meeting", and, for a day with two special meetings, its start time ("03.30.26 Minutes Special
Meeting 515", at 5:15 PM).

The page draws the library with a script that asks the site's content service
(/portal/svc/ContentItemSvc.asmx/GetItemList) for a folder's items, posting the folder's id
and the library's settings, which the page writes into that script:
new ContentItemListUI('<folder id>', null, '[]', {<settings>}, selectMultiSwitch...). Without
the settings the service refuses (HTTP 500). Each item has its name, type (a folder or a
file), file extension, the time it was last changed ("/Date(1791577344000)/", in milliseconds),
and its download link (common/pages/GetFile.ashx?key=...).

A file is a meeting's minutes when its name starts with a date and says "minutes". The rest of
the name, beyond "Minutes", is the meeting's kind as the clerk wrote it ("Special Meeting"),
which, with the date, keeps a meeting's record and tells two meetings of a day apart; a time
at its end ("515", "5:15", "5:15 PM") is the start time, in the afternoon for an hour from 1
to 7 without "am" or "pm" (the board doesn't meet at 1 to 7 in the morning). A file with
neither is reported, not guessed at.

This module has the parts that need no network or storage; the run is
pipeline/fetch_sharpschool_meetings.py.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from pipeline.meeting_names import words

SOURCE = "sharpschool"
SERVICE = "/portal/svc/ContentItemSvc.asmx/GetItemList"

# The library's script on the page: its folder's id and its settings.
LIBRARY = re.compile(r"new ContentItemListUI\('(\d+)',\s*null,\s*'\[\]',\s*(\{.*?\}),\s*selectMultiSwitch", re.S)
# "09.10.26 Minutes Regular Meeting", "9-10-2026 ...".
DATE = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})\b\s*(.*)$", re.S)
MINUTES = re.compile(r"\bminutes?\b", re.I)
# A start time at the end of the name: "515", "5:15", "5:15 PM", "530pm".
TIME = re.compile(r"\s+(\d{1,2}):?(\d{2})\s*(?:([ap])\.?\s*m\.?)?\s*$", re.I)
CHANGED = re.compile(r"/Date\((-?\d+)\)/")
YEAR = re.compile(r"\b(20\d{2})\b")


def library(page: str) -> tuple[str, str] | None:
    """The library's folder id and its settings (the JSON the service is posted), from its page."""
    found = LIBRARY.search(page)
    return (found.group(1), found.group(2)) if found else None


def service_url(page_url: str) -> str:
    return urljoin(page_url, SERVICE)


def request_body(folder: str, settings: str) -> dict:
    return {"parentId": folder, "Params": settings}


def items(answer: dict) -> list[dict]:
    """A folder's items from the service's answer: {"d": {"DataObject": [...]}}."""
    found = (answer.get("d") or {}).get("DataObject") or []
    return found if isinstance(found, list) else []


def is_folder(item: dict) -> bool:
    return item.get("Type") == "content_folder"


def year_folders(found: list[dict], since: str) -> list[dict]:
    """The folders of years from `since` on: "2026 Meeting Minutes", or "2025-2026" for a school year."""
    first = int(since[:4])
    return [f for f in found if is_folder(f) and any(int(y) >= first for y in YEAR.findall(f.get("Name", "")))]


def changed(item: dict, timezone_name: str) -> str:
    """When the file was last changed, in the town's time: '2026-10-09T13:02'; '' if not given."""
    found = CHANGED.search(str(item.get("ModifiedDate") or ""))
    if not found:
        return ""
    when = datetime.fromtimestamp(int(found.group(1)) / 1000, timezone.utc).astimezone(ZoneInfo(timezone_name))
    return when.strftime("%Y-%m-%dT%H:%M")


def start_time(hour: int, minute: int, half: str | None) -> str | None:
    if not (1 <= hour <= 12 and minute < 60):
        return None
    if half:
        hour = hour % 12 + (12 if half.lower() == "p" else 0)
    elif hour < 8:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def parse_name(name: str) -> dict | None:
    """A file's meeting from its name: '03.30.26 Minutes Special Meeting 515' ->
    {"date": "2026-03-30", "kind": "Special Meeting", "start_time": "17:15", "special": True};
    None for a name without a date or without "minutes"."""
    found = DATE.match(name)
    if not found or not MINUTES.search(found.group(4)):
        return None
    month, day, year, rest = found.groups()
    year = int(year) + (2000 if len(year) == 2 else 0)
    try:
        day_iso = date(year, int(month), int(day)).isoformat()
    except ValueError:
        return None
    rest = " ".join(MINUTES.sub(" ", rest).split())
    clock = TIME.search(f" {rest}")
    time = start_time(int(clock.group(1)), int(clock.group(2)), clock.group(3)) if clock else None
    if time:
        rest = TIME.sub("", f" {rest}").strip()
    return {"date": day_iso, "kind": rest or "Meeting", "start_time": time, "special": "special" in words(rest).split()}


def files(found: list[dict], settings: dict, timezone_name: str) -> tuple[list[dict], list[str]]:
    """The minutes among a folder's items, newest change first for a meeting posted twice, and the
    names of files that aren't any meeting's minutes."""
    exclude = settings.get("exclude_pattern")
    minutes, unrecognized = {}, []
    for item in found:
        if is_folder(item) or not item.get("DownloadLink"):
            continue
        name = str(item.get("Name") or "").strip()
        if exclude and re.search(exclude, name, re.I):
            continue
        meeting = parse_name(name)
        if not meeting:
            unrecognized.append(name)
            continue
        key = f"{meeting['date']} {meeting['kind']} {meeting['start_time'] or ''}"
        entry = {**meeting, "name": name, "item_id": item.get("ItemId"), "url": item["DownloadLink"],
                 "extension": str(item.get("Extension") or "").lower(), "changed": changed(item, timezone_name)}
        if key not in minutes or entry["changed"] > minutes[key]["changed"]:
            minutes[key] = entry
    return list(minutes.values()), unrecognized


def check(config: dict) -> None:
    """A town's [sharpschool_meetings] table, checked when its config is loaded."""
    settings = config.get("sharpschool_meetings")
    if settings is None:
        return
    where = f"[sharpschool_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("page_url", "source_name", "since", "body") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the library's page, who posts it, the first "
                         "meeting date to collect, and the board whose minutes it holds.")
    if not re.match(r"https://[\w.-]+/", str(settings["page_url"])):
        raise SystemExit(f'{where}: page_url must be the library\'s page, like '
                         '"https://www.springfieldpublicschools.com/school_committee/school_committee_meeting-_minutes_archive".')
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-01-01".')
    if "exclude_pattern" in settings:
        try:
            re.compile(settings["exclude_pattern"])
        except (re.error, TypeError) as e:
            raise SystemExit(f"{where}: exclude_pattern isn't a regular expression ({e}).") from None


def posted_stamp(posted: str) -> str:
    """'2026-10-09T13:02' -> '202610091302', for a document's id."""
    return re.sub(r"\D", "", posted)[:12]
