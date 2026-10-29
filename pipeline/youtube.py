"""Meetings' recordings on YouTube, transcribed by Gemini: the backup (pipeline/recordings.py).

A meeting is transcribed from the town's or its station's own platform where
it's there; YouTube is read for a meeting it doesn't have. YouTube doesn't let
a server download its videos, nor read its pages (it answers with a check for
robots), so:

- New videos are found in the channel's or playlist's RSS feed
  (/feeds/videos.xml?channel_id= or ?playlist_id=), its latest 15, each with
  its title, which names the board and the meeting's date.
- Each video's length, and whether it's still a live stream (or one yet to
  start), come from the YouTube Data API (videos.list), with the same Google
  API key as Gemini's (GEMINI_API_KEY, which must be allowed the YouTube Data
  API v3), sent in a header, never in an address a log could show.
- The transcript is Gemini's, from the video's public link
  (pipeline/gemini.py): this platform makes its own, rather than giving
  ffmpeg and Whisper an address to read.

Without the key, videos are found and matched, and wait to be transcribed.

  [[recordings.sources]]
  platform = "youtube"
  channel = "UC..."        # or playlist = "PL..."
  boards = { "City Council" = '^City Council Meeting' }
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

from pipeline import gemini, recordings
from pipeline.http import FetchError

PLATFORM = "youtube"
FEED = "https://www.youtube.com/feeds/videos.xml"
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
DURATION = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def check(source: dict, where: str) -> None:
    channel, playlist = source.get("channel") or "", source.get("playlist") or ""
    if bool(channel) == bool(playlist) or not re.fullmatch(r"(UC|PL|UU|OL)[\w-]{10,}", channel or playlist):
        raise SystemExit(f"{where} (youtube): needs channel (its id, UC...) or playlist (PL...), not both.")


def feed_url(source: dict) -> str:
    return f"{FEED}?channel_id={source['channel']}" if source.get("channel") else f"{FEED}?playlist_id={source['playlist']}"


def parse(feed: bytes, source: dict) -> list[dict]:
    """The feed's videos of the source's boards, with the meeting's date from each title."""
    out = []
    for entry in ET.fromstring(feed).findall("atom:entry", NS):
        video = entry.findtext("yt:videoId", "", NS)
        title = " ".join(entry.findtext("atom:title", "", NS).split())
        board = recordings.board_for_title(title, source["boards"])
        day = recordings.title_date(title)
        if not video or not board or not day:
            continue
        out.append({"id": f"youtube-{video}", "platform": PLATFORM, "board": board, "date": day, "title": title,
                    "url": gemini.video_url(video), "part": recordings.title_part(title), "video_id": video,
                    "published": entry.findtext("atom:published", "", NS)})
    return out


def seconds(duration: str) -> int | None:
    """An ISO 8601 duration ("PT3H12M5S") in seconds."""
    m = DURATION.match(duration or "")
    if not m or not any(m.groups()):
        return None
    d, h, mnt, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mnt) * 60 + s


def details(ids: list[str], key: str, get=requests.get) -> dict[str, dict]:
    """Each video's length in seconds, or None while it's live or yet to start, from the Data API."""
    out = {}
    for i in range(0, len(ids), 50):
        try:
            response = get(VIDEOS, params={"part": "contentDetails,snippet", "id": ",".join(ids[i:i + 50])},
                           headers={"x-goog-api-key": key}, timeout=60)
        except requests.RequestException as e:
            raise FetchError(f"YouTube Data API: {e}") from e
        if response.status_code >= 400:
            raise FetchError(f"YouTube Data API: HTTP {response.status_code}: {response.text[:300]}", response.status_code)
        for item in response.json().get("items") or []:
            live = (item.get("snippet") or {}).get("liveBroadcastContent", "none") != "none"
            out[item["id"]] = {"seconds": None if live else seconds((item.get("contentDetails") or {}).get("duration"))}
    return out


def find(client, source: dict, since: str, timezone: str, get=requests.get) -> list[dict]:
    found = [f for f in parse(client.get(feed_url(source)).content, source) if f["date"] >= since]
    key = gemini.api_key()
    if found and key:
        lengths = details([f["video_id"] for f in found], key, get)
        for f in found:
            f["seconds"] = (lengths.get(f["video_id"]) or {}).get("seconds")
    return found


def media(client, source: dict, recording: dict) -> dict | None:
    """Nothing for ffmpeg to read: YouTube's videos are transcribed by Gemini (transcript())."""
    return None


def transcript(client, source: dict, recording: dict, post=requests.post) -> tuple[str, dict] | None:
    """The video's transcript and how it was made, or None while it can't be made yet: no key, still live, or
    its length not known."""
    key = gemini.api_key()
    if not key or not recording.get("seconds"):
        return None
    model = source.get("model", gemini.MODEL)
    text, usage = gemini.transcribe(post, recording["video_id"], int(recording["seconds"]), recording["title"],
                                    recording["date"], key, model)
    return text, {"model": model, "usage": usage}
