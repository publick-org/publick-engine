"""Meetings' recordings on a TelVue video player (pipeline/recordings.py).

Lowell's station (LTC) and Waltham's (WCAC) post their meetings to a TelVue
player at videoplayer.telvue.com/player/<player>, a playlist a board (Lowell's
City Council is playlist 1757). The player has no public API: its pages are
read as HTML.

- A playlist's page lists its first 50 recordings, each with its media id,
  title and length, and says how many it has (data-playlist-item-count); the
  rest come 50 at a time from .../playlist_items?offset=50. Lowell's are
  listed newest first, Waltham's oldest first, so every page is read.
- The title names the board and the meeting's date ("City Council - October
  27, 2026" in Lowell, "City Council 10-26-26" in Waltham, with " Part 2"
  where a meeting is posted in parts). The player gives only how long ago a
  recording was added, so the title's date is the meeting's.
- A recording's page has the player's settings, with its HLS master playlist
  on telvuevod-secure.akamaized.net. Some recordings have an audio-only stream
  (index-saac_128k_0-a1.m3u8), which is read; the others' smallest stream, at
  about 0.4 Mbps, is read for its audio.

Lowell's recordings are up the night of the meeting; the station's own
captions (on some) are too poor to use.

  [[recordings.sources]]
  platform = "telvue"
  player = "4jeRlBTetCoFJdyT5ssIvBrB9VUvRN0M"
  playlists = [1757, 1759]
  boards = { "City Council" = "^City Council - ", "School Committee" = "^School Committee - " }
"""

from __future__ import annotations

import html
import json
import re

from pipeline import recordings
from pipeline.http import FetchError

PLATFORM = "telvue"
BASE = "https://videoplayer.telvue.com/player"
PAGE = 50
ITEM = re.compile(r'<li class="[^"]*video-item-container[^"]*">(.*?)</li>', re.S)
MEDIA_ID = re.compile(r'/media/(\d+)')
TITLE = re.compile(r'<span class="h4 title block">(.*?)</span>', re.S)
DURATION = re.compile(r'<span class="duration">\s*(\d{1,2}):(\d{2}):(\d{2})\s*</span>')
COUNT = re.compile(r'data-playlist-item-count="(\d+)"')
SETUP = re.compile(r"Player\.setupData\['playlist'\]\s*=\s*(\[.*?\]);\s*$", re.M)


def check(source: dict, where: str) -> None:
    if not re.fullmatch(r"[\w-]+", source.get("player") or ""):
        raise SystemExit(f"{where} (telvue): needs player, the key in the player's address.")
    if not source.get("playlists") or not all(str(p).isdigit() for p in source["playlists"]):
        raise SystemExit(f"{where} (telvue): needs playlists, the numbers of the playlists to read.")


def player_url(source: dict) -> str:
    return f"{BASE}/{source['player']}"


def items(page: str) -> list[dict]:
    """The recordings a playlist's page (or a page of its items) lists: media id, title, and length."""
    out = []
    for block in ITEM.findall(page):
        media, title = MEDIA_ID.search(block), TITLE.search(block)
        if not media or not title:
            continue
        length = DURATION.search(block)
        out.append({"media_id": media.group(1), "title": " ".join(html.unescape(title.group(1)).split()),
                    "seconds": (int(length.group(1)) * 3600 + int(length.group(2)) * 60 + int(length.group(3)))
                    if length else None})
    return out


def recording(item: dict, source: dict) -> dict | None:
    """A listed recording of one of the source's boards, with the meeting's date from its title."""
    board = recordings.board_for_title(item["title"], source["boards"])
    day = recordings.title_date(item["title"])
    if not board or not day:
        return None
    return {"id": f"telvue-{item['media_id']}", "platform": PLATFORM, "board": board, "date": day,
            "title": item["title"], "url": f"{player_url(source)}/media/{item['media_id']}",
            "seconds": item["seconds"], "part": recordings.title_part(item["title"]), "media_id": item["media_id"]}


def playlist(client, source: dict, number) -> list[dict]:
    """Every recording in a playlist, a page of PAGE at a time."""
    url = f"{player_url(source)}/playlists/{number}"
    page = client.get(url).text
    found = items(page)
    count = COUNT.search(page)
    total = int(count.group(1)) if count else len(found)
    while len(found) < total:
        more = items(client.get(f"{url}/playlist_items?offset={len(found)}").text)
        if not more:
            break
        found += more
    return found


def find(client, source: dict, since: str, timezone: str) -> list[dict]:
    out = []
    for number in source["playlists"]:
        for item in playlist(client, source, number):
            found = recording(item, source)
            if found and found["date"] >= since:
                out.append(found)
    return out


def setup(page: str) -> dict:
    """The player's settings for the recording its page plays."""
    m = SETUP.search(page)
    if not m:
        raise FetchError("the recording's page has no player settings")
    return json.loads(m.group(1))[0]


def media(client, source: dict, rec: dict) -> dict | None:
    settings = setup(client.get(rec["url"]).text)
    master = settings.get("file")
    if not master:
        return None
    if settings.get("duration"):
        rec["seconds"] = rec.get("seconds") or int(settings["duration"])
    return {"url": recordings.hls_stream(client.get(master).text, master)}
