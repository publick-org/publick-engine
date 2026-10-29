"""A meeting's recording as text: its audio saved, and transcribed by Whisper.

The audio is read from the recording's own address (an HLS stream or a video
file) by ffmpeg, and saved as Opus, one channel at 16 kHz (what Whisper reads)
and 24 kbps: about 45 MB for a four-hour meeting, where the station's own
audio stream is five times that.

The transcript is made by faster-whisper's small.en model, int8 on the CPU,
with its voice detection on (the minutes of silence before a meeting and
during a recess give nothing), a beam of 1, and each part of the meeting read
without the text before it (condition_on_previous_text off, which keeps one
misheard passage from repeating). On four CPUs it runs at about five times
real time: Lowell's City Council of 2026-09-22, 4 h 14 min, in 48 minutes. A
whole meeting read at once took 14 GB and was stopped for memory, so it's
read CHUNK_SECONDS at a time.

The transcript is plain text, a paragraph a line, each starting with where it
is in the recording ("[1:02:03] ..."), for the summary to be written from and
checked against (pipeline/summarize.py) and, later, for a decision to link its
moment in the recording.

faster-whisper and its model are needed only by the step that transcribes
(requirements-recordings.txt); nothing else imports them.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

MODEL = "small.en"
COMPUTE_TYPE = "int8"
SAMPLE_RATE = 16000
CHUNK_SECONDS = 30 * 60
# How much faster than real time the step is counted on to transcribe, erring slow, for what a run
# can take on (pipeline/fetch_recordings.py): measured at 4.6 to 5.3 on four CPUs.
SPEED = 4.0
# A paragraph of the transcript ends at a pause this long, or once it runs this long.
PAUSE_SECONDS = 2.0
PARAGRAPH_SECONDS = 60.0
# How long ffmpeg may take to read a recording, at most: a four-hour meeting's audio took 7.6 minutes.
DOWNLOAD_TIMEOUT = 90 * 60


class TranscribeError(RuntimeError):
    pass


def audio_command(media: dict, out: Path, user_agent: str) -> list[str]:
    """The ffmpeg command that saves a recording's audio (media: its "url", and any "headers" the
    platform needs) as Opus at out."""
    headers = "".join(f"{k}: {v}\r\n" for k, v in (media.get("headers") or {}).items())
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-user_agent", user_agent, *(["-headers", headers] if headers else []),
            "-i", media["url"], "-vn", "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-c:a", "libopus", "-b:a", "24k", "-application", "voip", str(out)]


def save_audio(media: dict, out: Path, user_agent: str, timeout: float = DOWNLOAD_TIMEOUT) -> None:
    try:
        done = subprocess.run(audio_command(media, out, user_agent), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise TranscribeError(f"the audio took more than {timeout // 60:.0f} minutes to read") from e
    if done.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise TranscribeError(f"ffmpeg couldn't read the audio: {done.stderr.strip()[-500:] or done.returncode}")


def duration(path: Path) -> float:
    """The audio's length in seconds, as ffprobe reads it."""
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                          capture_output=True, text=True)
    try:
        return float(json.loads(done.stdout)["format"]["duration"])
    except (ValueError, KeyError) as e:
        raise TranscribeError(f"ffprobe couldn't read the audio's length: {done.stderr.strip()[-300:]}") from e


def samples(path: Path, start: float, seconds: float):
    """seconds of the audio from start, as Whisper reads it: one channel at SAMPLE_RATE, as float32."""
    import numpy as np
    done = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(seconds),
                           "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
                          capture_output=True)
    if done.returncode != 0:
        raise TranscribeError(f"ffmpeg couldn't read the audio at {start:.0f}s: {done.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(done.stdout, np.int16).astype(np.float32) / 32768.0


def load_model(name: str = MODEL, threads: int = 0):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type=COMPUTE_TYPE, cpu_threads=threads)


def transcribe(model, path: Path, length: float | None = None, chunk: float = CHUNK_SECONDS,
               read=samples) -> list[tuple[float, float, str]]:
    """The audio's segments, (start, end, text), with their times in the whole recording, read chunk
    seconds at a time."""
    length = duration(path) if length is None else length
    out = []
    start = 0.0
    while start < length:
        audio = read(path, start, chunk)
        if len(audio):
            segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                           condition_on_previous_text=False)
            out += [(start + s.start, start + s.end, s.text.strip()) for s in segments if s.text.strip()]
        start += chunk
    return out


def stamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def paragraphs(segments: list[tuple[float, float, str]], pause: float = PAUSE_SECONDS,
               longest: float = PARAGRAPH_SECONDS) -> list[tuple[float, str]]:
    """The segments joined into paragraphs: a new one at a pause, or once one runs longest seconds."""
    out, start, end, words = [], None, None, []
    for s_start, s_end, text in segments:
        if words and (s_start - end >= pause or s_end - start > longest):
            out.append((start, " ".join(words)))
            words = []
        if not words:
            start = s_start
        words.append(text)
        end = s_end
    if words:
        out.append((start, " ".join(words)))
    return out


def text(segments: list[tuple[float, float, str]]) -> str:
    """The transcript as it's saved: a paragraph a line, each with its time in the recording."""
    return "".join(f"[{stamp(start)}] {words}\n" for start, words in paragraphs(segments))


TIME = re.compile(r"^\[[\d:]+\] ", re.M)


def untimed(transcript: str) -> str:
    """The transcript without each paragraph's time: what was said, as a summary's quotes copy it."""
    return TIME.sub("", transcript)


def word_count(transcript: str) -> int:
    """The words said, without each paragraph's time."""
    return len(untimed(transcript).split())


def run(model, media: dict, folder: Path, name: str, user_agent: str, clock=time.monotonic) -> dict:
    """Save a recording's audio under folder as <name>.ogg and transcribe it to <name>.txt. Returns the
    audio's length and how long each part took."""
    started = clock()
    audio = folder / f"{name}.ogg"
    save_audio(media, audio, user_agent)
    read_at = clock()
    length = duration(audio)
    words = text(transcribe(model, audio, length))
    if not words.strip():
        raise TranscribeError("Whisper heard nothing in the recording")
    (folder / f"{name}.txt").write_text(words, encoding="utf-8")
    return {"seconds": round(length), "download_seconds": round(read_at - started),
            "transcribe_seconds": round(clock() - read_at)}
