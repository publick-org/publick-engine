"""A public YouTube video of a meeting transcribed by Google's Gemini API (pipeline/youtube.py).

YouTube refuses to let a server download its videos, so a meeting posted only
there can't be read and transcribed by Whisper (pipeline/transcribe.py). The
Gemini API takes a public YouTube link and fetches the video itself. It's
asked for a transcript only, word for word; the summary is written from the
transcript as any recording's is (pipeline/summarize.py).

A meeting runs for hours, and its transcript is longer than one response can
be, so the video is asked for CLIP_SECONDS at a time (the request's
start_offset and end_offset, in seconds), each part's paragraphs with their
time in the video. The offsets are strings of seconds ("1200s"), as the API
takes them. The picture isn't needed: it's sampled as rarely as the API
allows (FPS). The model thinks as little as it can (THINKING): its thoughts
add nothing to a transcript, and at the model's default level it has
answered a part full of speech with nothing.

A part the API can't answer for now (HTTP 429 short of the day's limit, or
5xx: "high demand") is asked again after a wait (RETRY_WAITS), as is a part
answered with nothing at all: a part with no speech is answered
"[no speech]". When the day's requests are used up (HTTP 429, "per day"),
or the API is still busy after the last wait, Unavailable: the parts already
answered are kept by the caller (done, keep), and the rest are asked for on
a later run.

The request is the Interactions API's (POST /v1beta/interactions, the key in
x-goog-api-key), with the video as {"type": "video", "uri": <its link>} and
store false, so Google keeps no copy of the conversation. The API key is
GEMINI_API_KEY.
"""

from __future__ import annotations

import os
import re
import time

from pipeline.http import FetchError
from pipeline.transcribe import TranscribeError, stamp

API = "https://generativelanguage.googleapis.com/v1beta/interactions"
KEY_ENV = "GEMINI_API_KEY"
MODEL = "gemini-3.8-flash"
CLIP_SECONDS = 20 * 60
# Frames a second of the picture: as few as can be asked for, since only what's said is wanted.
FPS = 0.1
# The lowest level gemini-3.8-flash offers ("minimal" isn't, for it).
THINKING = "low"
# Twenty minutes of a meeting is about 3,000 words, 4,000 tokens: well under this, which counts thoughts too.
MAX_OUTPUT_TOKENS = 16000
TIMEOUT = 600
# Seconds to wait before asking for a part again.
RETRY_WAITS = (30, 120, 300)
RETRY_STATUS = {429, 500, 502, 503, 504}
NO_SPEECH = "[no speech]"

PROMPT = """This is part of the recording of a public meeting of a city government: {title}, {date}. This part runs from {start} to {end} of the recording.

Transcribe everything said in this part, word for word, in the language spoken. Do not summarize, correct, or leave anything out, and do not describe the picture. Write [inaudible] for words that can't be made out.

Write one paragraph a line, starting a new one when the speaker or the subject changes, or at least once a minute. Start each paragraph with the time in the whole recording when it begins, in brackets, as [h:mm:ss] (for example [0:21:05]).

If nothing is said in this part, write only [no speech]."""

# [h:mm:ss], or [m:ss] with the minutes past 59 ([84:10]) as the model sometimes writes them.
LINE = re.compile(r"^\s*\[(\d{1,3}):(\d{2})(?::(\d{2}))?\]\s*(.*)$")
# A time inside a line: gemini-3.8-flash has written a whole part as one line, its paragraphs' times inline.
INLINE = re.compile(r"[ \t]*(\[\d{1,3}:\d{2}(?::\d{2})?\])")


def api_key() -> str | None:
    return os.environ.get(KEY_ENV) or None


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def request(video_id: str, start: int, end: int, title: str, date: str, model: str = MODEL) -> dict:
    """The request for one part of the video's transcript."""
    return {
        "model": model,
        "store": False,
        "input": [
            {"type": "video", "uri": video_url(video_id),
             "processing": {"type": "static", "start_offset": f"{start}s", "end_offset": f"{end}s", "fps": FPS}},
            {"type": "text", "text": PROMPT.format(title=title, date=date, start=stamp(start), end=stamp(end))},
        ],
        "generation_config": {"max_output_tokens": MAX_OUTPUT_TOKENS, "temperature": 0, "thinking_level": THINKING},
    }


def output_text(response: dict) -> str:
    """The text of a completed interaction's output."""
    if response.get("status") not in (None, "completed"):
        raise TranscribeError(f"Gemini didn't finish the transcript ({response.get('status')})")
    texts = [c.get("text", "") for step in response.get("steps") or [] if step.get("type") == "model_output"
             for c in step.get("content") or [] if c.get("type") == "text"]
    return "\n".join(texts)


def paragraphs(text: str, start: int, end: int) -> list[tuple[float, str]]:
    """The part's paragraphs, each with its time in the whole recording. A time the model gave from the start of
    the part, not of the recording, is moved to the recording's; a line without a time joins the paragraph
    before it, and a time inside a line starts a paragraph."""
    found = []
    for line in INLINE.sub(r"\n\1", text).splitlines():
        m = LINE.match(line)
        if m:
            h, mnt, s = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m.group(3) else (0, int(m.group(1)), int(m.group(2)))
            found.append([h * 3600 + mnt * 60 + s, m.group(4).strip()])
        elif line.strip() == NO_SPEECH:
            continue
        elif line.strip() and found:
            found[-1][1] += " " + line.strip()
        elif line.strip():
            found.append([start, line.strip()])
    if found and start and all(t < end - start + 60 for t, _ in found) and not any(t >= start for t, _ in found):
        found = [[t + start, words] for t, words in found]
    return [(float(t), words) for t, words in found if words and words not in ("[inaudible]", NO_SPEECH)]


class Unavailable(FetchError):
    """The API can't answer for now: the key's requests for the day are used up, or it stayed busy."""


def ask(post, body: dict, key: str, sleep=time.sleep) -> tuple[dict, str]:
    """One part's interaction and its text, asked again after a wait while the API is busy or answers nothing."""
    for wait in (*RETRY_WAITS, None):
        try:
            response = post(API, json=body, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                            timeout=TIMEOUT)
        except Exception as e:  # requests' errors
            error = FetchError(f"Gemini: {e}")
        else:
            if response.status_code < 400:
                data = response.json()
                text = output_text(data)
                if text.strip():
                    return data, text
                error = TranscribeError("Gemini answered a part with nothing")
            else:
                error = FetchError(f"Gemini: HTTP {response.status_code}: {response.text[:300]}", response.status_code)
                # The day's requests are used up: asking again before tomorrow won't help.
                if response.status_code == 429 and "per day" in response.text:
                    raise Unavailable(error.args[0], response.status_code)
                if response.status_code not in RETRY_STATUS:
                    raise error
        if wait is None:
            if isinstance(error, FetchError) and error.status in RETRY_STATUS:
                raise Unavailable(error.args[0], error.status)
            raise error
        sleep(wait)


def transcribe(post, video_id: str, seconds: int, title: str, date: str, key: str, model: str = MODEL,
               clip: int = CLIP_SECONDS, sleep=time.sleep, done: dict | None = None,
               keep=lambda done: None, start: int = 0) -> tuple[str, dict]:
    """The video's transcript, as transcribe.text() writes one ("[h:mm:ss] ..." a paragraph a line), and the
    tokens it took (output_tokens counts the model's thoughts too, as they're billed). post(url, json, headers,
    timeout) is requests.post, or a stand-in.

    done holds the parts already answered, from an earlier try ({"model", "clip", "parts": {start: lines},
    "usage"}): they aren't asked for again. keep(done) is called after each part, to save them. start is where
    in the video the meeting starts (a link's &t=): the parts are from there, their times still the video's."""
    if not done or done.get("model") != model or done.get("clip") != clip:
        done = {"model": model, "clip": clip, "parts": {},
                "usage": {"requests": 0, "input_tokens": 0, "output_tokens": 0}}
    usage = done["usage"]
    for start in range(start, seconds, clip):
        if str(start) in done["parts"]:
            continue
        end = min(start + clip, seconds)
        data, text = ask(post, request(video_id, start, end, title, date, model), key, sleep)
        used = data.get("usage") or {}
        usage["requests"] += 1
        usage["input_tokens"] += used.get("total_input_tokens", 0)
        usage["output_tokens"] += used.get("total_output_tokens", 0) + used.get("total_thought_tokens", 0)
        done["parts"][str(start)] = [f"[{stamp(t)}] {words}" for t, words in paragraphs(text, start, end)]
        keep(done)
    lines = [line for start in sorted(done["parts"], key=int) for line in done["parts"][start]]
    if not lines:
        raise TranscribeError("Gemini heard nothing in the recording")
    return "".join(line + "\n" for line in lines), usage
