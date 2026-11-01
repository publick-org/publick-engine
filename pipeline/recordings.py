"""Meetings' recordings, found on the town's or its station's own video platform.

Minutes come a week or more after a meeting; its recording, the same night or
the next day. A town with a [recordings] table has each new recording of its
boards' meetings found, matched to its meeting, and transcribed
(pipeline/fetch_recordings.py), so the meeting is summarized from what was
said until its minutes come (pipeline/summarize.py's "recording" kind).

  [recordings]
  since = "2026-10-27"        # meetings on or after this date; optional
  max_age_days = 14           # meetings at most this many days old (default 14)

  [[recordings.sources]]
  platform = "telvue"         # one of PLATFORMS
  ...                         # what the platform needs (its module's docstring)
  boards = { "City Council" = "1757" }

Each source maps the town's boards, named as their meetings are, to a pattern
their recordings' titles match (a regular expression, case ignored); where
several match, the longest match wins ("School Committee Finance
Subcommittee" before "School Committee"). A recording of no board named isn't
read. A platform that keeps recordings in playlists or shows reads only those
the source lists. The meeting's date is read from the title too, where the
platform gives none of its own: a recording is often posted days later.

A recording is matched to its meeting by board and date: the board's one
meeting that day, not cancelled (or, where the platform is the town's meeting
portal too, to the meeting it's posted with). Two that day (a special meeting before a
regular one), or none, and it isn't matched; it's looked for again each run,
since a meeting can be listed after its recording is up.

Every recording found is kept in data/meetings/recordings.json, by its id
("<platform>-<the platform's id>"), with its meeting's id once matched and
its transcript once made. The step that finds and transcribes recordings
runs on its own (it can take an hour a meeting) and writes only that file, so
it never conflicts with the daily run's commits. The daily run reads it:
listings.read() gives each meeting its recordings and its transcript
(attach()), as it gives its agendas and minutes.

A meeting is transcribed once, from its first recording found; a later one
is skipped. YouTube (BACKUP) is read only for a meeting no other platform has. A meeting recorded in parts (one video each, "Part 1", "Part 2")
is transcribed from every part on the same platform, and its transcript is
their transcripts in order, once each is made; a part found later makes it a
new document, summarized again. A meeting whose minutes came first isn't
transcribed at all.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from datetime import date
from pathlib import Path

from pipeline.files import write_atomic

FILE = "recordings.json"
STATUS_FILE = "recordings_status.json"
# Each platform's module, which reads its listings:
# - check(source, where): stops on a source that can't work;
# - find(client, source, since, timezone): its recordings of meetings on or after since, each a dict with
#   "id", "platform", "board", "date", "title", "url" (its page), and where known "seconds" (its length),
#   "listing" (the meeting record it's posted with) and whatever media() needs;
# - media(client, source, recording): where its audio is read from ({"url", "headers"}), or None if
#   the platform has none for it yet;
# - and, for a platform whose recordings can't be downloaded, transcript(client, source, recording): the
#   transcript made some other way, and how, or None while it can't be made yet (pipeline/youtube.py).
PLATFORMS = {
    "telvue": "pipeline.telvue",
    "cablecast": "pipeline.cablecast",
    "castus": "pipeline.castus",
    "civicclerk": "pipeline.civicclerk_media",
    "internet_archive": "pipeline.internet_archive",
    "youtube": "pipeline.youtube",
}
# Platforms read only for a meeting the others don't have: YouTube, whose videos Gemini transcribes
# (pipeline/youtube.py), is the backup to the town's or its station's own.
BACKUP = ("youtube",)
MAX_AGE_DAYS = 14
# The folders of the documents bucket a recording's files are kept in (pipeline/documents.py).
AUDIO_FOLDER, TRANSCRIPT_FOLDER = "recordings", "transcripts"


def platform(name: str):
    return importlib.import_module(PLATFORMS[name])


def check(config: dict) -> None:
    """A [recordings] table that can't work stops the run before it starts."""
    settings = config.get("recordings")
    if settings is None:
        return
    sources = settings.get("sources")
    if not sources:
        raise SystemExit("[recordings] needs at least one [[recordings.sources]].")
    for i, source in enumerate(sources):
        where = f"[[recordings.sources]] {i + 1}"
        name = source.get("platform")
        if name not in PLATFORMS:
            raise SystemExit(f"{where}: platform must be one of {', '.join(PLATFORMS)}, not {name!r}.")
        boards = source.get("boards")
        if getattr(platform(name), "boards_by_name", lambda source: False)(source):
            # Its recordings come with their meetings (YouTube's linked from the town's listings): only named.
            if not isinstance(boards, list) or not boards or not all(isinstance(b, str) and b for b in boards):
                raise SystemExit(f"{where} ({name}): boards must list the boards whose meetings' videos are read.")
            platform(name).check(source, where)
            continue
        if not isinstance(boards, dict) or not boards or not all(isinstance(v, str) and v for v in boards.values()):
            raise SystemExit(f"{where} ({name}): boards must map each board's name to a pattern its recordings' "
                             "titles match.")
        for board, pattern in boards.items():
            try:
                re.compile(pattern)
            except re.error as e:
                raise SystemExit(f"{where} ({name}): boards {board!r}: {pattern!r} isn't a regular expression ({e}).")
        platform(name).check(source, where)


# ---- What a recording's title says ---------------------------------------------------------------------

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE_PATTERNS = (
    # 2026-10-27
    (re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), ("y", "m", "d")),
    # 10/27/2026, 10-27-26, 10.27.2026, and "Council -10/19/2026": a dash before the date that
    # follows no digit is a separator, not part of a longer date.
    (re.compile(r"(?<![\d/.])(?<!\d-)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?![\d/.-])"), ("m", "d", "y")),
    # October 27, 2026; Oct. 27th 2026
    (re.compile(MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)", re.I), ("b", "d", "y")),
    # 27 October 2026
    (re.compile(r"(?<!\d)(\d{1,2})\s+" + MONTH + r",?\s+(\d{4})(?!\d)", re.I), ("d", "b", "y")),
)


def title_date(title: str) -> str | None:
    """The first date a title gives, as YYYY-MM-DD, or None."""
    for pattern, order in DATE_PATTERNS:
        for m in pattern.finditer(title or ""):
            parts = dict(zip(order, m.groups()))
            year = int(parts["y"]) + (2000 if len(parts["y"]) == 2 else 0)
            month = MONTHS.index(parts["b"][:3].lower()) + 1 if "b" in parts else int(parts["m"])
            try:
                return date(year, month, int(parts["d"])).isoformat()
            except ValueError:
                continue
    return None


def board_for_title(title: str, boards: dict) -> str | None:
    """The board whose pattern the title matches; where several do, the one whose match is longest
    ("School Committee Finance Subcommittee" before "School Committee")."""
    found = []
    for board, pattern in boards.items():
        m = re.search(pattern, title or "", re.I)
        if m:
            found.append((len(m.group(0)), board))
    return max(found)[1] if found else None


PART = re.compile(r"\bpart\s*(\d{1,2}|one|two|three|four|five)\b", re.I)
PART_WORDS = ("one", "two", "three", "four", "five")


def title_part(title: str) -> int | None:
    """Which part of a meeting recorded in several a title says it is ("Part 2"), or None."""
    m = PART.search(title or "")
    if not m:
        return None
    n = m.group(1).lower()
    return PART_WORDS.index(n) + 1 if n in PART_WORDS else int(n)


def hls_stream(master: str, base_url: str) -> str:
    """Of an HLS master playlist's streams, the one to read a meeting's audio from: its audio track, where
    it lists one apart (EXT-X-MEDIA), or a stream with audio only, else the smallest. A master that is
    itself a stream's playlist is read as it is."""
    from urllib.parse import urljoin
    streams, lines = [], [line.strip() for line in master.splitlines()]
    for line in lines:
        if line.startswith("#EXT-X-MEDIA:") and re.search(r"\bTYPE=AUDIO\b", line):
            uri = re.search(r'\bURI="([^"]+)"', line)
            if uri:
                return urljoin(base_url, uri.group(1))
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            uri = next((u for u in lines[i + 1:] if u and not u.startswith("#")), None)
            bandwidth = re.search(r"\bBANDWIDTH=(\d+)", line)
            codecs = re.search(r'CODECS="([^"]*)"', line)
            if uri:
                audio_only = bool(codecs) and not re.search(r"avc|hvc|hev|vp0?9|av01", codecs.group(1))
                streams.append((not audio_only, int(bandwidth.group(1)) if bandwidth else 0, urljoin(base_url, uri)))
    return min(streams)[2] if streams else base_url


# ---- Matched to meetings -------------------------------------------------------------------------------

def same_board(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def match(recording: dict, meetings: dict, moved: dict | None = None) -> tuple[str | None, str]:
    """The id of the recording's meeting, or None, and why: the meeting whose listing the platform names
    ("listing", a CivicClerk event's record), else the board's one meeting that day that wasn't cancelled."""
    listing = recording.get("listing")
    if listing:
        found = meetings.get((moved or {}).get(listing, listing))
        if found and found.get("status") != "cancelled":
            return found["id"], "matched"
    if not recording.get("date"):
        return None, "no date"
    same = [m for m in meetings.values() if m["date"] == recording["date"]
            and any(same_board(name, recording["board"]) for name in [m["body"], *m.get("body_names", [])])
            and m.get("status") != "cancelled"]
    if len(same) == 1:
        return same[0]["id"], "matched"
    return None, "no meeting listed" if not same else f"{len(same)} meetings that day"


# ---- Kept in data/meetings/recordings.json ---------------------------------------------------------

def path(data_dir: Path) -> Path:
    return data_dir / "meetings" / FILE


def load(data_dir: Path) -> dict:
    p = path(data_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save(data_dir: Path, store: dict) -> None:
    write_atomic(path(data_dir), json.dumps(dict(sorted(store.items())), indent=2, ensure_ascii=False) + "\n")


# A recording that failed to be read or transcribed this many times is left (pipeline/fetch_recordings.py).
MAX_TRIES = 2
# A recording shorter than this is a clip or a placeholder, not a meeting.
MIN_SECONDS = 120


def usable(recording: dict) -> bool:
    return (recording.get("tries", 0) < MAX_TRIES
            and (recording.get("seconds") or MIN_SECONDS) >= MIN_SECONDS)


def chosen(group: list[dict]) -> list[dict]:
    """Of one meeting's recordings, those its transcript is made from: the one it was transcribed from, or
    else its first one found that can be read, not a backup platform's (BACKUP) unless no other has it; and
    where that's one part of several ("Part 2"), every part from the same source, in order."""
    group = sorted(group, key=lambda r: (r.get("found_at", ""), r["id"]))
    readable = [r for r in group if r.get("transcript") or usable(r)]
    done = [r for r in readable if r.get("transcript")]
    own = [r for r in readable if r["platform"] not in BACKUP]
    first = (done or own or readable or [None])[0]
    if first is None:
        return []
    if not first.get("part"):
        return [first]
    parts = [r for r in readable if r.get("source") == first.get("source") and r.get("part")]
    return sorted(parts, key=lambda r: (r["part"], r["id"]))


def by_meeting(store: dict, meetings: dict, moved: dict | None = None) -> dict[str, list[dict]]:
    """The store's recordings matched to each meeting, by its id."""
    out = {}
    for recording in store.values():
        meeting = meetings.get((moved or {}).get(recording.get("meeting"), recording.get("meeting")))
        if meeting:
            out.setdefault(meeting["id"], []).append(recording)
    return out


def transcript_doc(parts: list[dict]) -> dict:
    """A meeting's transcript, from its recordings' (one, or each part's), as its agendas and minutes are
    listed: a recording whose part comes later makes it a new document."""
    first, ts = parts[0], [r["transcript"] for r in parts]
    sha = ts[0]["sha256"] if len(ts) == 1 else hashlib.sha256(" ".join(t["sha256"] for t in ts).encode()).hexdigest()
    return {"id": first["id"], "title": first.get("title"), "source_url": first.get("url"),
            "file": ts[0]["file"], "sha256": sha, "bytes": sum(t["bytes"] for t in ts),
            "fetched_at": max(t["transcribed_at"] for t in ts), "seconds": sum(t.get("seconds") or 0 for t in ts),
            "platform": first["platform"],
            **({"parts": [{"file": t["file"], "title": r.get("title"), "url": r.get("url")} for r, t in zip(parts, ts)]}
               if len(parts) > 1 else {})}


def attach(meetings: dict, data_dir: Path, moved: dict | None = None) -> None:
    """Give each meeting its recordings ("recordings": each one matched to it, for its page to link)
    and its transcript ("transcripts", as its documents are listed, once every part is transcribed),
    from data/meetings/recordings.json. moved maps a record's id to the meeting it was put together into
    (listings.combined())."""
    store = load(data_dir)
    for meeting_id, group in by_meeting(store, meetings, moved).items():
        meeting = meetings[meeting_id]
        meeting["recordings"] = [{k: r[k] for k in ("id", "platform", "title", "url", "seconds", "part") if r.get(k)}
                                 for r in sorted(group, key=lambda r: (r.get("part") or 0, r.get("found_at", "")))]
        parts = chosen(group)
        if parts and all(r.get("transcript") for r in parts):
            meeting["transcripts"] = [transcript_doc(parts)]
