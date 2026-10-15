"""One meeting listed in more than one place, shown as one.

A city can list a meeting on its calendar and post its agenda in its Agenda
Center; a clerk can post the same agenda twice under different titles, or a
revised agenda as a new posting; a school district's schedule can list a
meeting the city calendar lists too. Each listing stays its own record in
data/meetings/meetings.json, as its source gives it, so each source keeps
updating its own record, and nothing is lost if a rule changes. Which records
are one meeting, and the meeting's page address, are decided by
pipeline/identity.py, once, and kept in data/meetings/identities.json; the
records of each meeting are put together when they are read (combined()).

The meeting's time and place come from the listing that gives them, its
documents from every listing, newest last, and its history from every
listing, each change saying where it was made. Its page is at its own address
(identity.py), whichever listing was recorded first; every other address its
listings had says it moved (see build_site).

    python -m pipeline.listings [--data data]   lists the meetings put together, and why
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

from pipeline import identity

# Listings that give a meeting's time and place, before ones that give only its documents.
DOCUMENT_SOURCES = ("agendacenter", "drive", "finalsite", "page", "diligent", "opengov", "archive")
# Where a meeting's listings disagree on a link (its agenda, documents or minutes), the one listed last here
# is linked: listings that only date a meeting (a city calendar, a district's calendar feed, a page of
# the year's dates) first, then the city's document systems, then the board's own website or portal (a
# school district's, which posts its own board's agendas). The meeting page lists its listings in this
# order too. Within a source, its listings are in the order it posted them (number()).
LISTING_ORDER = ("calendar", "ical", "schedule", "agendacenter", "civicclerk", "filelist", "archive",
                 "drive", "finalsite", "page", "diligent", "opengov")
STATUS_ORDER = ("cancelled", "postponed", "rescheduled")


# Why records are one meeting, as the meeting page and `python -m pipeline.listings` say it.
REASONS = {
    "same_time": "same board, day and start time",
    "one_time": "same board and day; one listing gives the time, the others none",
    "no_time": "same board and day; no listing gives a time",
    "only_meeting": "the board's only meeting that day, named differently in each place",
}


def reason(records: list[dict], kinds_differ: bool = False) -> str:
    timed = [m for m in records if m.get("start_time")]
    if len(timed) == len(records):
        return "same_time"
    return "only_meeting" if kinds_differ else "one_time" if timed else "no_time"


def place_first(records: list[dict]) -> list[dict]:
    """Listings that give a time and place first, then the primary's order."""
    return sorted(records, key=lambda m: (m.get("source") in DOCUMENT_SOURCES, not m.get("start_time")))


def number(m: dict) -> tuple:
    """A record's id in number order: agendacenter-999 before agendacenter-1000."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", m["id"]))


def posted(m: dict) -> tuple:
    """A listing's place in LISTING_ORDER, by its source, then in the order its source posted it."""
    source = m.get("source", "calendar")
    return (LISTING_ORDER.index(source) if source in LISTING_ORDER else len(LISTING_ORDER)), number(m)


def combine(records: list[dict], why: str, listing_of=lambda m: m.get("source", "calendar")) -> dict:
    """One meeting from its records (primary first). listing_of names where a record is listed,
    for its history's sentences ("Removed from the city calendar")."""
    primary = records[0]
    out = copy.deepcopy(primary)
    placed = place_first(records)
    # Postings in the order the city made them: a source numbers its listings as they
    # are posted (an Agenda Center's revised agenda keeps its number). The newest of
    # each source gives that source's word on the meeting: a cancellation notice, then
    # a new agenda, is a meeting back on. Sources in LISTING_ORDER, so a link they
    # disagree on is the board's own website's, then the city's document system's.
    newest = sorted(records, key=posted)
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


def combined(store: dict, listing_of=lambda m: m.get("source", "calendar"), ledger: dict | None = None,
             town: str = "") -> tuple[dict, dict]:
    """The store with each meeting as one record (under its first recorded listing's id), at its page
    address (slug) with its other addresses (addresses), and the other records' ids, each to that id.
    ledger is the kept identities (identity.load); without any, every meeting keeps the address of
    its first recorded listing, as before identities were kept."""
    out, moved = {}, {}
    for meeting in identity.meetings(store, ledger, town):
        records = meeting["records"]
        if len(records) > 1:
            m = combine(records, reason(records, meeting["kinds_differ"]), listing_of)
            for r in records[1:]:
                moved[r["id"]] = records[0]["id"]
        else:
            m = dict(records[0])
        m.update(slug=meeting["id"], addresses=meeting["addresses"], unplaced=meeting["unplaced"], body=meeting["body"])
        out[records[0]["id"]] = m
    return out, moved


def read(data_dir: Path, listing_of=lambda m: m.get("source", "calendar"), town: str = "") -> tuple[dict, dict]:
    """combined() of the town's meetings as saved, with their kept identities."""
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return combined(store, listing_of, identity.load(data_dir), town)


def main() -> int:
    from pipeline.config import DATA_DIR
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    path = args.data / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for meeting in sorted(identity.meetings(store, identity.load(args.data)), key=lambda m: (m["records"][0]["date"], m["id"])):
        records = meeting["records"]
        if len(records) < 2 and not meeting["unplaced"]:
            continue
        why = "unplaced: could be more than one of the board's meetings that day" if meeting["unplaced"] else \
            REASONS[reason(records, meeting["kinds_differ"])]
        print(f"/meetings/{meeting['id']}/ {meeting['body']}: {why}")
        for m in records:
            print(f"    {m['id']:<28} {m.get('source', 'calendar'):<13} {m.get('start_time') or '--:--'}  "
                  f"{m.get('posted_title') or m.get('title', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
