"""How many of a town's main boards' meetings were summarized from their recordings, and by when.

A meeting's recording is meant to give it a summary the day after it's held,
until its minutes come (pipeline/fetch_recordings.py, pipeline/summarize.py).
This counts, for the main boards' meetings of the last WINDOW_DAYS days (not
today's), the step each one has reached:

  summarized        a summary from its recording ("next_day" counts those made
                    by the end of the day after the meeting, in the town's time)
  minutes           no summary from a recording, but its minutes came
  transcribed       its recording is transcribed, not yet summarized
  waiting           its recording is found and matched, not yet transcribed
                    (the run's hours, the platform still processing it, or a
                    failed try with another to come)
  failed            its recordings have used their tries (recordings.MAX_TRIES)
  unmatched         a recording of the board that day was found, but not matched
                    to the meeting (none listed, or more than one)
  not_found         a source reads the board, and has no recording of it yet
  no_source         the town reads recordings, but no source names the board
  no_recordings     the town has no [recordings] table

The main boards are found the same way for every town, from its config: the
governing body, the school boards, [meetings] main_boards
(build_site.main_boards()), and any board whose name says it's about planning
or zoning (a board or commission, not a committee). No list of boards is kept
per town.

Each town's daily run keeps the counts in its run record (pipeline/network.py,
run.json), with up to MAX_MISSES of the meetings missed, so the network's
report (network.py recordings) reads only the run records, however many
towns there are.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import listings, recordings
from pipeline.build_site import main_boards

WINDOW_DAYS = 7
MAX_MISSES = 20
# Boards whose meetings are main wherever they are: planning boards and commissions, zoning boards of
# appeals and of review, planning and zoning commissions; not a council's committee ("Planning &
# Economic Development Committee"), which [meetings] main_boards can name.
PLANNING = re.compile(r"\b(planning|zoning)\b", re.I)
BOARD = re.compile(r"\b(board|commission)\b", re.I)
COMMITTEE = re.compile(r"committee\b", re.I)
# Steps in the order a meeting goes through them; a meeting is counted at the furthest it has reached.
STEPS = ("summarized", "minutes", "transcribed", "waiting", "failed", "unmatched", "not_found", "no_source",
         "no_recordings")
# The steps that aren't a miss: a summary from the recording, or the minutes, which replace it.
DONE = ("summarized", "minutes")


def names(meeting: dict) -> list[str]:
    return [meeting["body"], *meeting.get("body_names", [])]


def planning_or_zoning(name: str) -> bool:
    return bool(PLANNING.search(name) and BOARD.search(name) and not COMMITTEE.search(name))


def is_main(meeting: dict, boards: list[str]) -> bool:
    """Whether the meeting is a main board's: not one of its committees', which some calendars list under
    the board's name ("City Council - Ordinance Committee", listed as the City Council's)."""
    if COMMITTEE.search(meeting.get("title") or "") and not any(COMMITTEE.search(n) for n in names(meeting)):
        return False
    return any(planning_or_zoning(n) or any(recordings.same_board(n, b) for b in boards) for n in names(meeting))


def source_boards(config: dict) -> list[str] | None:
    """Every board any of the town's recordings sources reads; None when one reads every board the town
    lists (boards = "listed", recordings.LISTED)."""
    out = []
    for source in (config.get("recordings") or {}).get("sources", []):
        boards = source.get("boards") or []
        if isinstance(boards, str):
            return None
        out += list(boards)
    return out


def recording_summary(data_dir: Path, meeting: dict) -> dict | None:
    """The saved summary of the meeting's transcript, if there's one."""
    for doc in reversed(meeting.get("transcripts") or []):
        path = data_dir / "summaries" / f"{doc['sha256']}.json"
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("kind") == "recording":
                return record
    return None


def step(meeting: dict, group: list[dict], unmatched: list[dict], watched: bool, reads: bool,
         summary: dict | None) -> tuple[str, str]:
    """The furthest step the meeting has reached, and what's known about why it's there."""
    if summary:
        return "summarized", summary.get("generated_at", "")
    if meeting.get("minutes"):
        return "minutes", ""
    if any(r.get("transcript") for r in group):
        return "transcribed", "transcript not summarized yet"
    if group:
        if any(recordings.usable(r) for r in group):
            errors = [r["error"] for r in group if r.get("error")]
            return "waiting", f"{len(group)} recording(s) found" + (f"; last error: {errors[0]}" if errors else "")
        errors = [r.get("error") or "too short" for r in group]
        return "failed", f"{group[0]['id']}: {errors[0]}"
    if unmatched:
        return "unmatched", ", ".join(r["id"] for r in unmatched)
    if not reads:
        return "no_recordings", ""
    return ("not_found", "") if watched else ("no_source", "")


def coverage(config: dict, data_dir: Path, today: date | None = None, days: int = WINDOW_DAYS) -> dict:
    """The town's main boards' meetings of the last `days` days (before today), counted by step."""
    today = today or datetime.now(ZoneInfo(config["site"]["timezone"])).date()
    since, until = (today - timedelta(days=days)).isoformat(), (today - timedelta(days=1)).isoformat()
    meetings, moved = listings.read(data_dir)
    store = recordings.load(data_dir)
    grouped = recordings.by_meeting(store, meetings, moved)
    loose = [r for r in store.values() if not r.get("meeting") or r["meeting"] not in meetings]
    boards = main_boards(config)
    watched_boards = source_boards(config)
    reads = bool(config.get("recordings"))
    counts = {s: 0 for s in STEPS}
    out = {"since": since, "until": until, "held": 0, "next_day": 0, "steps": counts, "misses": []}
    seen = set()
    # A board's meetings one day are counted once: by the one its recordings are matched to, where one is
    # (the main meeting of a sitting, recordings.sitting()).
    for m in sorted(meetings.values(), key=lambda m: (m["date"], m["body"], not grouped.get(m["id"]), m["id"])):
        if not since <= m["date"] <= until or m.get("status") == "cancelled" or not m.get("listed", True):
            continue
        if not is_main(m, boards):
            continue
        key = (m["date"], m["body"].lower())
        if key in seen:
            continue
        seen.add(key)
        group = grouped.get(m["id"], [])
        unmatched = [r for r in loose if r.get("date") == m["date"]
                     and any(recordings.same_board(r.get("board", ""), n) for n in names(m))]
        watched = watched_boards is None or any(recordings.same_board(b, n) for b in watched_boards
                                                for n in names(m))
        reached, detail = step(m, group, unmatched, watched, reads, recording_summary(data_dir, m))
        out["held"] += 1
        counts[reached] += 1
        if reached == "summarized":
            made = detail[:10]
            if made and made <= (date.fromisoformat(m["date"]) + timedelta(days=1)).isoformat():
                out["next_day"] += 1
        elif reached not in DONE and len(out["misses"]) < MAX_MISSES:
            out["misses"].append({"date": m["date"], "board": m["body"], "step": reached,
                                  **({"detail": detail[:200]} if detail else {})})
    return out
