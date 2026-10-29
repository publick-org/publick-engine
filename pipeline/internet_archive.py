"""Meetings' recordings in an Internet Archive collection (pipeline/recordings.py).

Some community media stations copy their YouTube uploads to the Internet
Archive, which, unlike YouTube, lets a server download them: Town Meeting TV
(CCTV, Burlington) as the collection cctvvt, LTC (Lowell) as ltcmediacenter.
Each upload is an item, listed by the advanced search API, newest first; its
title names the board and the meeting's date ("Burlington City Council -
10/26/2026", "Lowell City Council - October 20, 2026"). The item's own date is
YouTube's upload date, often the day after, so the meeting's date is read from
the title. A collection holds more than one town's meetings, and other
programs: the boards' patterns say which titles are the town's.

Items come in batches, one to ten days after the meeting. Each holds the
station's original video, and the Archive's MP3 of it once its conversion is
done (an hour to a few days later): the MP3 is read, or, when the item has no
conversion pending and still no MP3, its smaller video. An item whose
conversion is still to run waits for it.

Town Meeting TV's descriptions start with the meeting's agenda on the city's
CivicClerk portal ("Agenda: https://burlingtonvt.portal.civicclerk.com/event/
10093/..."): the recording is then matched to that event's meeting record.

  [[recordings.sources]]
  platform = "internet_archive"
  collection = "cctvvt"
  boards = { "City Council" = "^Burlington City Council\\b" }
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from pipeline import recordings

PLATFORM = "internet_archive"
SEARCH = "https://archive.org/advancedsearch.php"
FIELDS = ("identifier", "title", "date", "publicdate", "description")
ROWS = 300
# The files read for the audio, best first, by the Archive's name for their format.
FORMATS = ("MP3", "h.264", "h.264 HD")
CIVICCLERK_EVENT = re.compile(r"\.portal\.civicclerk\.com/event/(\d+)")


def check(source: dict, where: str) -> None:
    if not re.fullmatch(r"[\w.-]+", source.get("collection") or ""):
        raise SystemExit(f"{where} (internet_archive): needs collection, the collection's identifier.")


def search_url(collection: str, since: str) -> str:
    """The collection's items made public on or after since, newest first."""
    params = [("q", f"collection:({collection}) AND publicdate:[{since} TO null]"),
              *(("fl[]", f) for f in FIELDS), ("sort[]", "publicdate desc"), ("rows", str(ROWS)),
              ("page", "1"), ("output", "json")]
    return f"{SEARCH}?{urlencode(params)}"


def text(value) -> str:
    return " ".join(value) if isinstance(value, list) else str(value or "")


def parse(data: dict, source: dict) -> list[dict]:
    out = []
    for doc in data.get("response", {}).get("docs", []):
        title = text(doc.get("title")).strip()
        board = recordings.board_for_title(title, source["boards"])
        day = recordings.title_date(title)
        if not board or not day:
            continue
        event = CIVICCLERK_EVENT.search(text(doc.get("description")))
        out.append({
            "id": f"archive-{doc['identifier']}",
            "platform": PLATFORM,
            "board": board,
            "date": day,
            "title": title,
            "url": f"https://archive.org/details/{doc['identifier']}",
            "item": doc["identifier"],
            **({"listing": f"civicclerk-{event.group(1)}"} if event else {}),
        })
    return out


def find(client, source: dict, since: str, timezone: str) -> list[dict]:
    return parse(client.get(search_url(source["collection"], since)).json(), source)


def audio_file(metadata: dict) -> dict | None:
    """The item's file to read the audio from, or None while its conversion is still to run."""
    files = metadata.get("files") or []
    for fmt in FORMATS:
        if fmt != "MP3" and metadata.get("pending_tasks"):
            return None
        found = next((f for f in files if f.get("format") == fmt), None)
        if found:
            return found
    return None


def length(found: dict) -> int | None:
    """A file's length in seconds, as the Archive gives it: "6178.46", or "01:42:59" for an MP3."""
    value = str(found.get("length") or "")
    try:
        if ":" in value:
            seconds = 0
            for part in value.split(":"):
                seconds = seconds * 60 + float(part)
            return round(seconds)
        return round(float(value)) if value else None
    except ValueError:
        return None


def media(client, source: dict, recording: dict) -> dict | None:
    item = recording["item"]
    found = audio_file(client.get(f"https://archive.org/metadata/{quote(item)}").json())
    if not found:
        return None
    if length(found):
        recording["seconds"] = length(found)
    return {"url": f"https://archive.org/download/{quote(item)}/{quote(found['name'])}"}
