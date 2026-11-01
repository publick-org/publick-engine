"""Meetings' recordings on YouTube, transcribed by Gemini: the backup (pipeline/recordings.py).

A meeting is transcribed from the town's or its station's own platform where
it's there; YouTube is read for a meeting it doesn't have. YouTube doesn't let
a server download its videos, nor read its pages (it answers with a check for
robots), so:

- New videos are listed by the YouTube Data API (playlistItems.list, 50 a
  page, a quota unit each, the newest first): the next page is read only
  while the last one held no video published before the run's earliest
  meeting date, up to LIST_PAGES. The playlist's, or for a channel its
  uploads, the playlist "UU" + the channel id after "UC". Each comes with
  its title, which names the board and the meeting's date. Without
  YOUTUBE_API_KEY they're found in the channel's or playlist's RSS feed
  (/feeds/videos.xml?channel_id= or ?playlist_id=), its latest 15, which
  YouTube has also answered with 404 and 500 for every channel for hours.
- Each video's length, and whether it's still a live stream (or one yet to
  start), come from the YouTube Data API (videos.list, at
  youtube.googleapis.com), with a Google API key of its own
  (YOUTUBE_API_KEY): Gemini's keys are bound to a service account, and such
  a key can't be allowed the YouTube Data API. The key is sent in a header,
  never in an address a log could show.
- The transcript is Gemini's, from the video's public link
  (pipeline/gemini.py): this platform makes its own, rather than giving
  ffmpeg and Whisper an address to read. It's asked for a part at a time,
  and the parts answered are kept in the documents bucket
  (transcripts/<id>.parts.json) until the last is: a try stopped by an
  error, or by Gemini being unavailable, goes on from there. Gemini
  unavailable (the day's requests used up, or busy through every retry)
  isn't a failed try; the video waits for the next run.

Without either key, videos are found and matched, and wait to be transcribed.

A source with neither channel nor playlist takes the videos the town's own
meeting listings link (a meeting's video_id, from PrimeGov's videoUrl,
CivicClerk's externalMediaUrl, a Finalsite or link page's YouTube link, or a
ClerkBase docket's, pipeline/clerkbase.py):
each is matched to the meeting that links it, so its boards are only named,
with no pattern, and nothing is read from a title. Where the link starts
partway through the video (&t=), so does the transcript. This needs no
channel or pattern per town: where a town's listings link its videos, it's
the way to read them.

  [[recordings.sources]]
  platform = "youtube"
  boards = ["City Council"]   # the videos these boards' meetings link

  [[recordings.sources]]
  platform = "youtube"
  channel = "UC..."        # or playlist = "PL..."
  boards = { "City Council" = '^City Council Meeting' }
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET

import requests

from pipeline import gemini, recordings
from pipeline.http import FetchError

PLATFORM = "youtube"
FEED = "https://www.youtube.com/feeds/videos.xml"
VIDEOS = "https://youtube.googleapis.com/youtube/v3/videos"
PLAYLIST_ITEMS = "https://youtube.googleapis.com/youtube/v3/playlistItems"
# Pages of 50 a source's listing may read: a busy channel's uploads of the last few weeks. Most runs need one.
LIST_PAGES = 2
KEY_ENV = "YOUTUBE_API_KEY"
NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
# fetch_recordings.find() gives find() the town's meetings, for a source that reads their links.
READS_MEETINGS = True
DURATION = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def api_key() -> str | None:
    return os.environ.get(KEY_ENV) or None


def from_meetings(source: dict) -> bool:
    """Whether the source's videos are those the town's meeting listings link (no channel or playlist)."""
    return not (source.get("channel") or source.get("playlist"))


def boards_by_name(source: dict) -> bool:
    """Whether the source's boards are only named (recordings.check()): its videos come with their meetings."""
    return from_meetings(source)


def check(source: dict, where: str) -> None:
    channel, playlist = source.get("channel") or "", source.get("playlist") or ""
    if from_meetings(source):
        return
    if bool(channel) == bool(playlist) or not re.fullmatch(r"(UC|PL|UU|OL)[\w-]{10,}", channel or playlist):
        raise SystemExit(f"{where} (youtube): needs channel (its id, UC...) or playlist (PL...), not both.")


def feed_url(source: dict) -> str:
    return f"{FEED}?channel_id={source['channel']}" if source.get("channel") else f"{FEED}?playlist_id={source['playlist']}"


def playlist_id(source: dict) -> str:
    """The source's playlist, or its channel's uploads (UC... -> UU...)."""
    return source.get("playlist") or "UU" + source["channel"][2:]


def video(video_id: str, title: str, published: str, source: dict) -> dict | None:
    """A video of one of the source's boards, with the meeting's date from its title; None for any other."""
    title = " ".join(title.split())
    board = recordings.board_for_title(title, source["boards"])
    day = recordings.title_date(title)
    if not video_id or not board or not day:
        return None
    return {"id": f"youtube-{video_id}", "platform": PLATFORM, "board": board, "date": day, "title": title,
            "url": gemini.video_url(video_id), "part": recordings.title_part(title), "video_id": video_id,
            "published": published}


def parse(feed: bytes, source: dict) -> list[dict]:
    """The feed's videos of the source's boards."""
    found = (video(e.findtext("yt:videoId", "", NS), e.findtext("atom:title", "", NS),
                   e.findtext("atom:published", "", NS), source)
             for e in ET.fromstring(feed).findall("atom:entry", NS))
    return [f for f in found if f]


def listed(source: dict, key: str, get=requests.get, since: str = "") -> list[dict]:
    """The videos of the source's boards in its playlist's newest pages, from the Data API: the next page only
    while every video on the last was published on or after since (a meeting's date), up to LIST_PAGES."""
    out, page = [], None
    for _ in range(LIST_PAGES):
        params = {"part": "snippet,contentDetails", "playlistId": playlist_id(source), "maxResults": 50}
        if page:
            params["pageToken"] = page
        try:
            response = get(PLAYLIST_ITEMS, params=params, headers={"x-goog-api-key": key}, timeout=60)
        except requests.RequestException as e:
            raise FetchError(f"YouTube Data API: {e}") from e
        if response.status_code >= 400:
            raise FetchError(f"YouTube Data API: HTTP {response.status_code}: {response.text[:300]}", response.status_code)
        data = response.json()
        reached = False
        for item in data.get("items") or []:
            snippet, content = item.get("snippet") or {}, item.get("contentDetails") or {}
            published = content.get("videoPublishedAt") or snippet.get("publishedAt", "")
            reached = reached or bool(published and since and published[:10] < since)
            found = video(content.get("videoId") or (snippet.get("resourceId") or {}).get("videoId", ""),
                          snippet.get("title", ""), published, source)
            if found:
                out.append(found)
        page = data.get("nextPageToken")
        # A video published before since was of an older meeting: what's after it is older still.
        if not page or reached:
            break
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


def linked(source: dict, meetings: dict, since: str) -> list[dict]:
    """The videos the source's boards' meetings on or after since link, each with its meeting (listing)."""
    out = []
    for meeting in meetings.values():
        video_id = meeting.get("video_id")
        if not video_id or meeting.get("date", "") < since or meeting.get("status") == "cancelled":
            continue
        names = [meeting.get("body", ""), *meeting.get("body_names", [])]
        board = next((b for b in source["boards"] if any(recordings.same_board(n, b) for n in names)), None)
        if not board:
            continue
        start = meeting.get("video_start") or 0
        out.append({"id": f"youtube-{video_id}" + (f"-{start}" if start else ""), "platform": PLATFORM,
                    "board": board, "date": meeting["date"], "title": meeting.get("title") or board,
                    "url": gemini.video_url(video_id) + (f"&t={start}s" if start else ""), "part": None,
                    "video_id": video_id, "listing": meeting["id"], **({"video_start": start} if start else {})})
    return out


def find(client, source: dict, since: str, timezone: str, get=requests.get, meetings: dict | None = None) -> list[dict]:
    key = api_key()
    if from_meetings(source):
        found = linked(source, meetings or {}, since)
    else:
        found = listed(source, key, get, since) if key else parse(client.get(feed_url(source)).content, source)
    found = [f for f in found if f["date"] >= since]
    if found and key:
        lengths = details([f["video_id"] for f in found], key, get)
        for f in found:
            f["seconds"] = (lengths.get(f["video_id"]) or {}).get("seconds")
    return found


def media(client, source: dict, recording: dict) -> dict | None:
    """Nothing for ffmpeg to read: YouTube's videos are transcribed by Gemini (transcript())."""
    return None


def transcript(client, source: dict, recording: dict, storage=None, post=requests.post) -> tuple[str, dict] | None:
    """The video's transcript and how it was made, or None while it can't be made yet: no key, still live, its
    length not known, or Gemini unavailable for now (the day's requests used up, or busy). The parts answered
    so far are kept in storage (transcripts/<id>.parts.json), so a try cut short goes on from where it
    stopped."""
    key = gemini.api_key()
    if not key or not recording.get("seconds"):
        return None
    model = source.get("model", gemini.MODEL)
    parts = f"{recording['id']}.parts.json"
    done = None
    if storage is not None:
        try:
            done = json.loads(storage.get(recordings.TRANSCRIPT_FOLDER, parts))
        except (OSError, FetchError, ValueError):
            pass

    def keep(done: dict) -> None:
        if storage is not None:
            storage.put(recordings.TRANSCRIPT_FOLDER, parts, json.dumps(done).encode("utf-8"))

    try:
        text, usage = gemini.transcribe(post, recording["video_id"], int(recording["seconds"]), recording["title"],
                                        recording["date"], key, model, done=done, keep=keep,
                                        start=int(recording.get("video_start") or 0))
    except gemini.Unavailable:
        return None
    return text, {"model": model, "usage": usage}
