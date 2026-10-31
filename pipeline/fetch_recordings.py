"""Find the town's new meeting recordings, match each to its meeting, and transcribe them.

See pipeline/recordings.py for the [recordings] table and what's kept, and
pipeline/transcribe.py for how a recording is read and transcribed.

Each run:
1. Lists each source's recordings of meetings on or after [recordings] since,
   and at most max_age_days old, and matches each new one, and each one not
   yet matched, to its meeting.
2. Transcribes the matched recordings, newest meeting first: each meeting
   once, and not one whose minutes have come. A recording is transcribed only
   if what it's likely to take fits what's left of the run's --hours (Whisper
   at transcribe.SPEED times real time); the rest wait for the next run. The
   audio and the transcript go in the documents bucket, under recordings/ and
   transcripts/, and the recording's entry in recordings.json is saved after
   each one, so a run stopped partway keeps what it finished.

A recording that fails to be read or transcribed is tried again on the next
run, up to MAX_TRIES times. One whose platform has no audio for it yet (the
Internet Archive converting an upload) waits without counting a try.

Usage:
    python -m pipeline.fetch_recordings [--town lowell] [--hours 4] [--find-only]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import listings, recordings, transcribe
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient

MAX_TRIES = recordings.MAX_TRIES
# Reading the audio, counted against the run's hours as a share of the recording's length (a four-hour
# meeting's took 7.6 minutes).
DOWNLOAD_SHARE = 1 / 20
# How much faster than real time Gemini is counted on to transcribe a YouTube video, a part at a time.
YOUTUBE_SPEED = 10
# Counted for a recording whose platform doesn't give its length, until its audio is read.
UNKNOWN_SECONDS = 3 * 3600


def earliest(settings: dict, today: date) -> str:
    """The first meeting date whose recordings are read: [recordings] since, at most max_age_days back."""
    oldest = (today - timedelta(days=settings.get("max_age_days", recordings.MAX_AGE_DAYS))).isoformat()
    return max(oldest, str(settings.get("since", oldest)))


def find(config: dict, client, store: dict, meetings: dict, moved: dict, since: str, today: str,
         stamp: str) -> dict:
    """List each source's recordings into the store, and match the ones not yet matched."""
    counts = {"listed": 0, "new": 0, "matched": 0, "unmatched": [], "errors": []}
    for i, source in enumerate(config["recordings"]["sources"]):
        module = recordings.platform(source["platform"])
        try:
            found = module.find(client, source, since, config["site"]["timezone"])
        except FetchError as e:
            counts["errors"].append(f"{source['platform']} (source {i + 1}): {e}")
            continue
        for item in found:
            if not item.get("date") or not since <= item["date"] <= today:
                continue
            counts["listed"] += 1
            recording = store.get(item["id"])
            if recording is None:
                recording = store[item["id"]] = {"found_at": stamp}
                counts["new"] += 1
            recording.update({k: v for k, v in item.items() if k != "media"}, source=i)
            if not recording.get("meeting") or recording["meeting"] not in meetings:
                meeting, why = recordings.match(recording, meetings, moved)
                recording["meeting"] = meeting
                if meeting:
                    recording["matched_at"] = stamp
                    counts["matched"] += 1
                else:
                    counts["unmatched"].append(f"{recording['board']} {recording['date']} ({recording['id']}): {why}")
    return counts


def to_transcribe(store: dict, meetings: dict, since: str) -> list[dict]:
    """The recordings to transcribe, newest meeting first: those each meeting's transcript is made from
    (recordings.chosen()) not yet transcribed, for meetings on or after since whose minutes haven't come."""
    out = []
    for meeting_id, group in recordings.by_meeting(store, meetings).items():
        meeting = meetings[meeting_id]
        if meeting["date"] < since or meeting.get("minutes"):
            continue
        out += [r for r in recordings.chosen(group) if not r.get("transcript")]
    return sorted(out, key=lambda r: (meetings[r["meeting"]]["date"], -(r.get("part") or 0), r["id"]), reverse=True)


def likely_seconds(recording: dict) -> float:
    """How long the recording is likely to take to read and transcribe: by Whisper here, or by Gemini (a
    fraction of that, YOUTUBE_SPEED)."""
    length = recording.get("seconds") or UNKNOWN_SECONDS
    if recording.get("platform") in recordings.BACKUP:
        return length / YOUTUBE_SPEED
    return length / transcribe.SPEED + length * DOWNLOAD_SHARE


def transcribe_one(config: dict, client, recording: dict, storage, model, user_agent: str, stamp: str,
                   transcriber=transcribe.run) -> bool:
    """Read and transcribe one recording, keep its audio and transcript, and record the transcript. False,
    and nothing done, when its platform has no audio for it yet (still being processed). model is Whisper's,
    or a function that loads it, called only for a recording Whisper transcribes."""
    source = config["recordings"]["sources"][recording["source"]]
    module = recordings.platform(source["platform"])
    name = recording["id"]
    if hasattr(module, "transcript"):
        # A platform that makes its own (YouTube's, by Gemini).
        made = module.transcript(client, source, recording, storage)
        if not made:
            return False
        words = made[0].encode("utf-8")
        storage.put(recordings.TRANSCRIPT_FOLDER, f"{name}.txt", words)
        recording["transcript"] = {
            "file": f"{name}.txt", "sha256": hashlib.sha256(words).hexdigest(), "bytes": len(words),
            "words": transcribe.word_count(made[0]), "seconds": recording.get("seconds"), "transcribed_at": stamp,
            **made[1]}
        recording.pop("error", None)
        return True
    media = module.media(client, source, recording)
    if not media:
        return False
    model = model() if callable(model) else model
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        took = transcriber(model, media, folder, name, user_agent)
        words = (folder / f"{name}.txt").read_bytes()
        audio = folder / f"{name}.ogg"
        if audio.exists():
            storage.put(recordings.AUDIO_FOLDER, audio.name, audio.read_bytes())
            recording["audio"] = {"file": audio.name, "bytes": audio.stat().st_size}
        storage.put(recordings.TRANSCRIPT_FOLDER, f"{name}.txt", words)
    recording["seconds"] = took["seconds"]
    recording["transcript"] = {
        "file": f"{name}.txt", "sha256": hashlib.sha256(words).hexdigest(), "bytes": len(words),
        "words": transcribe.word_count(words.decode("utf-8")), "seconds": took["seconds"], "model": transcribe.MODEL,
        "transcribed_at": stamp, "download_seconds": took["download_seconds"],
        "transcribe_seconds": took["transcribe_seconds"]}
    recording.pop("error", None)
    return True


def run(config: dict, client, data_dir: Path, now: datetime | None = None, hours: float | None = None,
        find_only: bool = False, load_model=transcribe.load_model, transcriber=transcribe.run,
        clock=time.monotonic) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["recordings"]
    since = earliest(settings, now.date())
    meetings, moved = listings.read(data_dir)
    store = recordings.load(data_dir)
    counts = find(config, client, store, meetings, moved, since, today, stamp)
    recordings.save(data_dir, store)

    todo = [] if find_only else to_transcribe(store, meetings, since)
    started, done, waiting, not_ready, errors = clock(), [], [], [], list(counts["errors"])
    storage, model = open_documents(config, data_dir), None
    for recording in todo:
        if hours is not None and clock() - started + likely_seconds(recording) > hours * 3600:
            waiting.append(recording["id"])
            continue
        def whisper():
            """Whisper's model, loaded once, when the first recording that needs it comes."""
            nonlocal model
            model = model or load_model()
            return model

        try:
            if transcribe_one(config, client, recording, storage, whisper, config["site"]["user_agent"], stamp,
                              transcriber):
                done.append(recording["id"])
            else:
                not_ready.append(recording["id"])
        except (FetchError, transcribe.TranscribeError) as e:
            recording["tries"] = recording.get("tries", 0) + 1
            recording["error"] = str(e)
            errors.append(f"{recording['id']} ({recording['board']} {recording['date']}): {e}")
        recordings.save(data_dir, store)

    status = {
        "updated_at": stamp,
        "since": since,
        "recordings_listed": counts["listed"],
        "recordings_new": counts["new"],
        "recordings_matched": counts["matched"],
        # Recordings of a board's meeting that couldn't be matched to one: none listed that day, or more than one.
        "unmatched": counts["unmatched"],
        "transcribed": done,
        "waiting": waiting,
        # Matched, but their platform has no audio for them yet (still processing the upload).
        "not_ready": not_ready,
        "requests": getattr(client, "request_count", None),
        "errors": errors,
    }
    save_json(data_dir / "meetings" / recordings.STATUS_FILE, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--hours", type=float, default=None,
                        help="the time the run may take; a recording that wouldn't fit waits for the next run")
    parser.add_argument("--find-only", action="store_true", help="find and match recordings, but transcribe none")
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "recordings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["recordings"].get("request_delay", 2.0))
    status = run(config, client, args.data, hours=args.hours, find_only=args.find_only)
    print(json.dumps(status, indent=2))
    for line in status["unmatched"]:
        print(f"::notice::Recording not matched to a meeting: {line}")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
