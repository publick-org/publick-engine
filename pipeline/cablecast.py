"""Meetings' recordings on a Cablecast station's video on demand (pipeline/recordings.py).

Methuen's station (methuentv.cablecast.tv), Beverly's (BevCam,
bevcam.cablecast.tv) and Salem's (SATV, cc.satvonline.org, over plain HTTP
only) run Tightrope's Cablecast, whose public API lists the station's shows,
newest event date first, page by page (offset counts pages, not shows), with
each one's recording ("vods", with include=vod). It takes a project (a series:
Methuen's City Council is 157) but no date, category or sort, so a source
names its projects, or its categories (Beverly's and Salem's meetings aren't
all in a project), and the newest pages are read until they reach since.

A show's title (cgTitle, the one shown) names the board and the meeting's
date ("City Council Meeting - October 19, 2026", "Salem Zoning Board of
Appeals Meeting 10/21/26"). The show's event date can be a day off, or the
day it aired, so the title's date is the meeting's, where it gives one.

A recording made since 2025 is a folder of files: vod.mp4, its HLS playlist
vod.m3u8, whose audio track is a playlist of its own (1080p_audio.m3u8, about
130 kbps), which is read. An older one is a single MP4, read whole. Some
stations publish a machine transcript beside it (transcript.en.txt); it isn't
used: Whisper's is made the same way for every town.

  [[recordings.sources]]
  platform = "cablecast"
  host = "https://methuentv.cablecast.tv"
  projects = [157]            # or categories = [44]
  boards = { "City Council" = "^City Council (Regular )?Meeting\\b" }
"""

from __future__ import annotations

import re

from pipeline import recordings

PLATFORM = "cablecast"
PAGE_SIZE = 50
# Pages read at most for one project or category: the newest 500 shows.
MAX_PAGES = 10
CANCELLED = re.compile(r"\bcancel", re.I)


def check(source: dict, where: str) -> None:
    if not re.fullmatch(r"https?://[\w.-]+", (source.get("host") or "").rstrip("/")):
        raise SystemExit(f"{where} (cablecast): needs host, the station's address (https://<station>.cablecast.tv).")
    if not source.get("projects") and not source.get("categories"):
        raise SystemExit(f"{where} (cablecast): needs projects or categories, the numbers of those to read.")


def api(source: dict) -> str:
    return f"{source['host'].rstrip('/')}/cablecastapi/v1"


def shows_url(source: dict, page: int, project=None) -> str:
    return (f"{api(source)}/shows?include=vod&page_size={PAGE_SIZE}&offset={page}"
            + (f"&project={project}" if project is not None else ""))


def parse(data: dict, source: dict) -> list[dict]:
    """A page of shows as recordings of the source's boards, each with its finished recording."""
    vods = {v["id"]: v for v in data.get("vods") or []}
    categories = set(source.get("categories") or [])
    out = []
    for show in data.get("shows") or []:
        title = " ".join((show.get("cgTitle") or show.get("title") or "").split())
        if categories and show.get("category") not in categories:
            continue
        vod = next((vods[i] for i in show.get("vods") or [] if i in vods and vods[i].get("url")
                    and vods[i].get("vodState", "complete") == "complete"), None)
        board = recordings.board_for_title(title, source["boards"])
        if not vod or not board or CANCELLED.search(title):
            continue
        day = (recordings.title_date(title) or recordings.title_date(show.get("title") or "")
               or (show.get("eventDate") or "")[:10])
        out.append({"id": f"cablecast-{show['id']}", "platform": PLATFORM, "board": board, "date": day, "title": title,
                    "url": f"{source['host'].rstrip('/')}/internetchannel/show/{show['id']}",
                    "seconds": vod.get("length") or show.get("totalRunTime") or None,
                    "part": recordings.title_part(title), "vod_url": vod["url"]})
    return out


def find(client, source: dict, since: str, timezone: str) -> list[dict]:
    out, seen = [], set()
    for project in source.get("projects") or [None]:
        for page in range(MAX_PAGES):
            data = client.get(shows_url(source, page, project)).json()
            for found in parse(data, source):
                if found["id"] not in seen and found["date"] >= since:
                    seen.add(found["id"])
                    out.append(found)
            dates = [s["eventDate"][:10] for s in data.get("shows") or [] if s.get("eventDate")]
            if len(data.get("shows") or []) < PAGE_SIZE or (dates and min(dates) < since):
                break
    return out


def media(client, source: dict, recording: dict) -> dict | None:
    url = recording["vod_url"]
    if url.endswith("/vod.mp4"):
        master = url[:-len("vod.mp4")] + "vod.m3u8"
        return {"url": recordings.hls_stream(client.get(master).text, master)}
    return {"url": url}
