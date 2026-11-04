"""Where a town's meetings are missing their agendas and minutes, for the network's report.

A board's documents can be posted where the town's sources don't read them (a
department's own page), its listing can be recorded under another board's name
than its documents' (the city calendar's "Beverly Retirement Board", the Agenda
Center's "Contributory Retirement Board"), or a reader can be weeks behind.
None of these fails a run, and each is plain in the data. This counts, from the
meetings as the site lists them (pipeline/listings.py), for the meetings held in
the last WINDOW_DAYS days (not today's, and not cancelled ones):

  no_documents  boards with at least MIN_MEETINGS meetings and no agenda or
                minutes for any of them
  no_minutes    boards with at least MIN_MEETINGS meetings held MINUTES_DAYS
                or more days ago, with agendas but no minutes for any of them
                (minutes come weeks after a meeting, once the board approves
                them)
  pairs         a meeting only a calendar lists (a city calendar, a district's
                feed or schedule), with no documents, on the day another board
                whose name shares a word with its own (meeting_names.
                distinct_words) has a meeting with documents from another
                source: most likely one meeting listed under two names. A
                listing marked postponed or rescheduled isn't one: it was
                moved to another day
  waiting       each document reader's meetings still waiting for their
                documents, from its status file under data/meetings/ (a reader
                saves only so many documents a run, newest first)

Many towns' boards don't post minutes, or post them late, and a board can meet
the same day as another of a like name: these are leads to look at, not
errors. Each town's daily run keeps the counts in its run record
(pipeline/network.py, run.json, "documents"), with at most MAX_LISTED of each,
so the network's report (network.py documents) reads only the run records.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from pipeline import listings
from pipeline.absences import NOT_HELD
from pipeline.meeting_names import distinct_words

WINDOW_DAYS = 90
MINUTES_DAYS = 45
MIN_MEETINGS = 2
MAX_LISTED = 20
# Sources that list a meeting's date and time, with no documents of their own.
CALENDARS = ("calendar", "ical", "schedule")
# Status files of readers that don't save documents.
NOT_DOCUMENTS = ("recordings_status.json",)


def has_agenda(meeting: dict) -> bool:
    return bool(meeting.get("agendas") or meeting.get("agenda_url") or meeting.get("documents_url"))


def has_minutes(meeting: dict) -> bool:
    return bool(meeting.get("minutes") or meeting.get("minutes_url"))


def waiting(data_dir: Path) -> dict[str, int]:
    """Each reader's meetings or documents waiting for a later run, by its status file's name."""
    out = {}
    for path in sorted((data_dir / "meetings").glob("*status.json")):
        if path.name in NOT_DOCUMENTS:
            continue
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        count = sum(v for k, v in status.items() if k.endswith("_waiting") and isinstance(v, int)
                    and not isinstance(v, bool))
        if count:
            out[path.name.removesuffix(".json")] = count
    return out


def gaps(data_dir: Path, town: str = "", today: date | None = None) -> dict:
    today = today or date.today()
    since = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    minutes_by = (today - timedelta(days=MINUTES_DAYS)).isoformat()
    meetings, _ = listings.read(data_dir, town=town)
    held = [m for m in meetings.values()
            if since <= m["date"] < today.isoformat() and m.get("status") != "cancelled"]
    by_board: dict[str, list[dict]] = {}
    for m in held:
        by_board.setdefault(m["body"], []).append(m)

    no_documents, no_minutes = [], []
    for board, group in sorted(by_board.items()):
        if len(group) >= MIN_MEETINGS and not any(has_agenda(m) or has_minutes(m) for m in group):
            no_documents.append({"board": board, "meetings": len(group)})
            continue
        old = [m for m in group if m["date"] <= minutes_by]
        if len(old) >= MIN_MEETINGS and any(has_agenda(m) for m in old) and not any(has_minutes(m) for m in group):
            no_minutes.append({"board": board, "meetings": len(old)})

    by_day: dict[str, list[dict]] = {}
    for m in held:
        by_day.setdefault(m["date"], []).append(m)
    pairs = []
    for day, group in sorted(by_day.items()):
        for m in group:
            # A listing moved to another day (postponed, or rescheduled: "RESCHEDULED to 10-14") isn't a
            # second listing of the meeting held that day.
            if (m.get("source", "calendar") not in CALENDARS or has_agenda(m) or has_minutes(m)
                    or m.get("status") in NOT_HELD):
                continue
            words = distinct_words(m["body"], town)
            other = next((o for o in group if o is not m and o["body"] != m["body"] and (has_agenda(o) or has_minutes(o))
                          and o.get("source") != m.get("source", "calendar")
                          and words & distinct_words(o["body"], town)), None)
            if other:
                pairs.append({"date": day, "board": m["body"], "other": other["body"]})

    no_documents.sort(key=lambda b: (-b["meetings"], b["board"]))
    no_minutes.sort(key=lambda b: (-b["meetings"], b["board"]))
    return {
        "since": since,
        "until": (today - timedelta(days=1)).isoformat(),
        "held": len(held),
        "boards": len(by_board),
        "counts": {"no_documents": len(no_documents), "no_minutes": len(no_minutes), "pairs": len(pairs)},
        "no_documents": no_documents[:MAX_LISTED],
        "no_minutes": no_minutes[:MAX_LISTED],
        "pairs": pairs[:MAX_LISTED],
        "waiting": waiting(data_dir),
    }
