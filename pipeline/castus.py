"""Meetings' recordings on a Castus cloud channel (pipeline/recordings.py).

Manchester's station (Manchester Public TV) and Medford's (Medford Community
Media) post their meetings to Castus's cloud video service, at
cloud.castus.tv/vod/<channel>. Its page is an app over a JSON API:

- the channel's settings (CONFIG/<channel>) give its owner's id ("user");
- a playlist's recordings, all at once, come from PLAYLISTS/<user>/playlist/
  <its name> (by name: by its id there's nothing);
- a recording's HLS address comes from a POST to MEDIA with its id.

A recording's title names the board and the meeting's date: "10/20/26 Board
of Mayor and Aldermen" in Manchester, "City Council 10/27/26" in Medford
(some have only a file name, the same). The recording's own date is when it
was posted, usually the next day but sometimes weeks later, so the title's
date is the meeting's. Every stream has video; the smallest (360p) is read
for its audio, where the channel has one, to keep what the station's
bandwidth is charged for small.

Medford's old video site (medford.vod.castus.tv) stopped in January 2026.

  [[recordings.sources]]
  platform = "castus"
  channel = "medford"
  playlists = ["Medford City Council"]
  boards = { "City Council" = "^City Council \\d" }
"""

from __future__ import annotations

import re
from urllib.parse import quote

from pipeline import recordings
from pipeline.http import FetchError

PLATFORM = "castus"
CONFIG = "https://837sc3bew0.execute-api.us-west-2.amazonaws.com"
PLAYLISTS = "https://2kbyogxrg4.execute-api.us-west-2.amazonaws.com"
MEDIA = "https://imd0mxanj2.execute-api.us-west-2.amazonaws.com/upload/get"
# The app's own pages are at /vod/#/<channel>/...: /vod/<channel>/... answers 404 with a page that sends there.
SITE = "https://cloud.castus.tv/vod"
# The channel's settings answer 409 now and then (its own page asks again, up to five times).
TRIES = 3


def check(source: dict, where: str) -> None:
    if not re.fullmatch(r"[\w-]+", source.get("channel") or ""):
        raise SystemExit(f"{where} (castus): needs channel, the name in its address (cloud.castus.tv/vod/<channel>).")
    if not source.get("playlists") or not all(isinstance(p, str) and p for p in source["playlists"]):
        raise SystemExit(f"{where} (castus): needs playlists, the names of the playlists to read.")


def owner(client, channel: str) -> str:
    for attempt in range(TRIES):
        try:
            return client.get(f"{CONFIG}/{channel}").json()["user"]
        except FetchError as e:
            if e.status != 409 or attempt == TRIES - 1:
                raise
    raise AssertionError("unreachable")


def title(video: dict) -> str:
    meta = video.get("metadata") or {}
    return " ".join((meta.get("title") or meta.get("filename") or "").split())


def parse(data: dict | None, source: dict) -> list[dict]:
    """A playlist's recordings of the source's boards."""
    out = []
    for video in (data or {}).get("videos") or []:
        name = title(video)
        board = recordings.board_for_title(name, source["boards"])
        day = recordings.title_date(name)
        if not board or not day or video.get("access", "open") != "open":
            continue
        try:
            seconds = round(float((video.get("metadata") or {}).get("duration") or 0)) or None
        except ValueError:
            seconds = None
        out.append({"id": f"castus-{video['_id']}", "platform": PLATFORM, "board": board, "date": day, "title": name,
                    "url": f"{SITE}/#/{source['channel']}/video/{video['_id']}", "seconds": seconds,
                    "part": recordings.title_part(name), "video_id": video["_id"]})
    return out


def find(client, source: dict, since: str, timezone: str) -> list[dict]:
    user, out = owner(client, source["channel"]), []
    for name in source["playlists"]:
        out += [f for f in parse(client.get(f"{PLAYLISTS}/{user}/playlist/{quote(name, safe='')}").json(), source)
                if f["date"] >= since]
    return out


def media(client, source: dict, recording: dict) -> dict | None:
    data = client.post_json(MEDIA, {"file": recording["video_id"], "type": "HLS", "user": ""}).json()
    master = ((data.get("response") or {}).get("payload") or {}).get("data")
    # "/" is a recording that's paid for; none is the city's.
    if not master or not master.startswith("http"):
        return None
    return {"url": recordings.hls_stream(client.get(master).text, master)}
