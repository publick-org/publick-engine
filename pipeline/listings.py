"""One meeting listed in more than one place, shown as one.

A city can list a meeting on its calendar and post its agenda in its Agenda
Center; a clerk can post the same agenda twice under different titles, or a
revised agenda as a new posting; a school district's schedule can list a
meeting the city calendar lists too. Each listing stays its own record in
data/meetings/meetings.json, as its source gives it, so each source keeps
updating its own record, and nothing is lost if this rule changes. The
records of one meeting are put together when they are read (combined()).

Records are one meeting when they are the same board (by name, after the
town's aliases) on the same day, and of the same variant (a school
committee's workshop is a meeting of its own), and either

  - both give a start time, and it is the same, or
  - one gives none, and they are the same kind of meeting: their titles
    agree, beyond the board's name, on the words that tell one meeting of a
    board from another (committee, subcommittee, special, hearing, joint,
    workshop). An Agenda Center lists a board's committees under the board, so
    "Council on Aging Grant Committee" and "Council on Aging Board of
    Directors" on one day are two meetings, where "Board of Commissioners
    Meeting" and "Malden Housing Authority Meeting" are one, posted twice.

Two meetings of a board on one day at different times stay two, and a
record without a time that could be either of them is left on its own.

The combined meeting keeps the page address of the record recorded first, so
a page already published stays where it is; the others' pages show the same
meeting (see build_site). Its time and place come from the listing that gives
them, its documents from every listing, newest last, and its history from
every listing, each change saying where it was made.

    python -m pipeline.listings [--data data]   lists the meetings put together, and why
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import defaultdict
from pathlib import Path

from pipeline.meeting_names import words

# Listings that give a meeting's time and place, before ones that give only its documents.
DOCUMENT_SOURCES = ("agendacenter", "drive", "finalsite", "archive")
STATUS_ORDER = ("cancelled", "postponed", "rescheduled")


# Words in a listing's title that make it a different meeting of the board than one
# without them: its Grant Committee, a subcommittee, a special meeting, a hearing.
KIND_WORDS = {"committee", "subcommittee", "workshop", "hearing", "special", "joint", "executive", "walk"}
PLURALS = {"committees": "committee", "subcommittees": "subcommittee", "hearings": "hearing", "workshops": "workshop"}


def kind(m: dict) -> frozenset[str]:
    """The words in a record's title, beyond its board's own name, that say what kind of
    meeting of the board it is. 'Council on Aging Grant Committee Meeting Agenda (PDF)'
    for the Council on Aging -> {'committee'}; 'Board of Commissioners Meeting' for the
    Malden Housing Authority -> {}."""
    title = m.get("posted_title") or m.get("title") or ""
    normal = lambda text: {PLURALS.get(w, w) for w in words(text).split()}
    found = (normal(title) - normal(m["body"])) & KIND_WORDS
    return frozenset(found | ({"special"} if m.get("special") else set()))


def groups(store: dict) -> list[tuple[list[dict], str]]:
    """The records of each meeting listed more than once, primary first, and why they are one."""
    buckets = defaultdict(list)
    for m in store.values():
        buckets[(m["date"], words(m["body"]), m.get("variant", ""))].append(m)
    found = []
    for records in buckets.values():
        if len(records) < 2:
            continue
        by_time = defaultdict(list)
        for m in records:
            if m.get("start_time"):
                by_time[m["start_time"]].append(m)
        clusters = list(by_time.values())
        # An untimed record goes with the board's one timed meeting that day, if it is
        # of the same kind; with two or more, which one it is for can't be told.
        if len(clusters) <= 1:
            for m in (m for m in records if not m.get("start_time")):
                home = next((c for c in clusters if kind(c[0]) == kind(m)), None)
                if home is None:
                    clusters.append(home := [])
                home.append(m)
        for ms in clusters:
            if len(ms) > 1:
                ms.sort(key=lambda m: (m.get("first_seen", ""), number(m)))
                found.append((ms, reason(ms)))
    return found


# Why records are one meeting, as the meeting page and `python -m pipeline.listings` say it.
REASONS = {
    "same_time": "same board, day and start time",
    "one_time": "same board and day; one listing gives the time, the others none",
    "no_time": "same board and day; no listing gives a time",
}


def reason(records: list[dict]) -> str:
    timed = [m for m in records if m.get("start_time")]
    return "same_time" if len(timed) == len(records) else "one_time" if timed else "no_time"


def place_first(records: list[dict]) -> list[dict]:
    """Listings that give a time and place first, then the primary's order."""
    return sorted(records, key=lambda m: (m.get("source") in DOCUMENT_SOURCES, not m.get("start_time")))


def number(m: dict) -> tuple:
    """A record's id in number order: agendacenter-999 before agendacenter-1000."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", m["id"]))


def combine(records: list[dict], why: str, listing_of=lambda m: m.get("source", "calendar")) -> dict:
    """One meeting from its records (primary first). listing_of names where a record is listed,
    for its history's sentences ("Removed from the city calendar")."""
    primary = records[0]
    out = copy.deepcopy(primary)
    placed = place_first(records)
    # Postings in the order the city made them: a source numbers its listings as they
    # are posted (an Agenda Center's revised agenda keeps its number). The newest of
    # each source gives that source's word on the meeting: a cancellation notice, then
    # a new agenda, is a meeting back on.
    newest = sorted(records, key=number)
    for field in ("start_time", "end_time"):
        out[field] = next((m[field] for m in placed if m.get("start_time") and m.get(field)), None)
    for field in ("location_name", "address", "location", "remote_url"):
        value = next((m[field] for m in placed if m.get(field)), None)
        if value:
            out[field] = value
    for field in ("documents_url", "agenda_url", "minutes_url"):
        out[field] = next((m[field] for m in newest[::-1] if m.get(field)), None)
    rank = {m["id"]: i for i, m in enumerate(newest)}
    for field in ("agendas", "minutes"):
        docs, seen = [], set()
        for m in newest:
            for d in m.get(field, []):
                if d["sha256"] not in seen:
                    seen.add(d["sha256"])
                    docs.append((d.get("fetched_at", ""), rank[m["id"]], len(docs), d))
        out[field] = [d for *_, d in sorted(docs, key=lambda t: t[:3])]
    out["history"] = sorted(({**h, "listing": listing_of(m)} for m in records for h in m.get("history", [])),
                            key=lambda h: h["at"])
    # A cancellation on any listing stands: a city that cancels a meeting in one place
    # often leaves it as it was in another.
    latest = {m.get("source", "calendar"): m for m in newest if m.get("listed", True)}
    statuses = {m.get("status", "scheduled") for m in latest.values()}
    out["status"] = next((s for s in STATUS_ORDER if s in statuses), "scheduled")
    out["listed"] = bool(latest)
    out["special"] = any(m.get("special") for m in records)
    out["first_seen"] = min((m["first_seen"] for m in records if m.get("first_seen")), default=None)
    out["last_seen"] = max((m["last_seen"] for m in records if m.get("last_seen")), default=None)
    # Where the time and place come from, for "Not listed on the city calendar".
    out["source"] = placed[0].get("source", "calendar")
    out["source_url"] = placed[0].get("source_url")
    out["listings"] = [{"id": m["id"], "source": m.get("source", "calendar"), "source_url": m.get("source_url"),
                        "title": m.get("posted_title") or m.get("title"), "status": m.get("status", "scheduled"),
                        "source_name": m.get("source_name"), "listed": m.get("listed", True), "slug": m["slug"]}
                       for m in newest]
    out["same_as"] = why
    return out


def combined(store: dict, listing_of=lambda m: m.get("source", "calendar")) -> tuple[dict, dict]:
    """The store with each meeting listed more than once as one record (under its primary's id),
    and the other records' ids, each to its primary's."""
    out = dict(store)
    moved = {}
    for records, why in groups(store):
        primary = records[0]["id"]
        out[primary] = combine(records, why, listing_of)
        for m in records[1:]:
            del out[m["id"]]
            moved[m["id"]] = primary
    return out, moved


def main() -> int:
    from pipeline.config import DATA_DIR
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    path = args.data / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for records, why in sorted(groups(store), key=lambda g: g[0][0]["date"]):
        first = records[0]
        print(f"{first['date']} {first['body']}: {REASONS[why]}")
        for m in records:
            print(f"    {m['id']:<28} {m.get('source', 'calendar'):<13} {m.get('start_time') or '--:--'}  "
                  f"{m.get('posted_title') or m.get('title', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
