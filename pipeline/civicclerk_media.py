"""Meetings' recordings on a CivicClerk portal (pipeline/recordings.py).

Portland's portal keeps each meeting's video with the meeting itself: its
event in the Events API (pipeline/civicclerk.py) has hasMedia, and its file's
name in mediaAwsKeyName, served from the portal's media host, an Azure blob
that serves byte ranges, at <media_url>/<mediaAwsKeyName>. That holds both for
a meeting recorded from the portal's live stream (mediaSourcePathMp4 is then
the whole address, and the video is up within minutes of the meeting's end)
and for a recording uploaded later (a Zoom meeting's; mediaSourcePathMp4 is
then a "stream/..." path that doesn't resolve as written). durationMin is the
recording's length in seconds, whatever its name says.

A portal whose events link YouTube instead (externalMediaUrl, Nashua's) has
no mediaAwsKeyName: those aren't read here.

The recording is matched to its event's meeting record ("civicclerk-<id>"),
so a town that lists its meetings from the same portal ([meetings.civicclerk])
needs no matching by board and date.

  [[recordings.sources]]
  platform = "civicclerk"
  api_url = "https://portlandme.api.civicclerk.com/v1"
  portal_url = "https://portlandme.portal.civicclerk.com"
  media_url = "https://cpmedia.azureedge.net/portlandme"
  boards = { "City Council" = "^City Council( Meeting)?$" }   # the event's name, or else its category

Start times are the city's local time, marked "Z" as the rest of the API's are.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from pipeline import civicclerk, recordings

PLATFORM = "civicclerk"


def check(source: dict, where: str) -> None:
    missing = [k for k in ("api_url", "portal_url", "media_url") if not source.get(k)]
    if missing:
        raise SystemExit(f"{where} (civicclerk): needs {', '.join(missing)}.")


def events_url(api_url: str, since: str) -> str:
    """The meetings with a recording starting on or after since, oldest first."""
    query = f"$filter=startDateTime ge {since} and hasMedia eq true&$orderby=startDateTime"
    return f"{api_url.rstrip('/')}/Events?{quote(query, safe='$=&')}"


def name(event: dict) -> str:
    """The event's name as the portal lists it, without the recurrence it can start with."""
    raw = event.get("eventName") or ""
    return re.sub(r"^\s*one[ -]time event\s*", "", raw, flags=re.I).strip()


def parse(data: dict, source: dict) -> list[dict]:
    """One page of the Events API as recordings of the boards the source names."""
    out = []
    for e in data.get("value", []):
        key = (e.get("mediaAwsKeyName") or "").strip()
        if e.get("isDeleted") or not e.get("hasMedia") or not key:
            continue
        board = (recordings.board_for_title(name(e), source["boards"])
                 or recordings.board_for_title((e.get("categoryName") or "").strip(), source["boards"]))
        if not board:
            continue
        out.append({
            "id": f"civicclerk-video-{e['id']}",
            "platform": PLATFORM,
            "board": board,
            "date": (e.get("startDateTime") or "")[:10],
            "title": name(e) or e.get("categoryName"),
            "url": civicclerk.event_url(source["portal_url"], e["id"]).removesuffix("/files") + "/media",
            "seconds": e.get("durationMin") or None,
            "listing": f"civicclerk-{e['id']}",
            "media_file": key,
        })
    return out


def find(client, source: dict, since: str, timezone: str) -> list[dict]:
    url, out = events_url(source["api_url"], since), []
    while url:
        data = client.get(url).json()
        out += parse(data, source)
        url = civicclerk.next_page(data)
    return out


def media(client, source: dict, recording: dict) -> dict | None:
    return {"url": f"{source['media_url'].rstrip('/')}/{quote(recording['media_file'])}"}
