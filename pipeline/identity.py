"""Which meeting each listing is, decided once and kept: a meeting's identity.

A meeting is a board's meeting on a day. Whichever sources list it (a city calendar, an Agenda
Center, CivicClerk, a school district's page, Drive folders or portal, an Archive Center, a
calendar feed, a page of dates), its listings are matched by what they say about it, the same
way for every source, never by which source listed it first:

  board  the listing's board, after the town's board mapping ([meetings.aliases] and the
         sources' own `bodies`), compared in a normal form (board_key): case, punctuation and
         "&"/"and" aside, without "the", the town's name in front ("Waltham School Committee"),
         plurals ("Historic Districts Commission"), or "Sub Committee" written apart. A joint
         meeting's board is every board it names (joint_boards): "School Committee and City
         Council Joint Meeting", posted under the City Council, and "Joint Meeting - City
         Council" on the school district's page are one meeting of both.
  kind   the words of its title, beyond its boards' names, that tell one meeting of a board
         from another on a day (listing_kind): special, hearing, workshop, executive, joint,
         committee, subcommittee, walk; and a Drive file's own variant ("Governance Workshop").
  time   its start time, where it gives one.

The listings of one board on one day are put into meetings by these rules, in this order:

  1. Listings that give the same start time are one meeting.
  2. A listing without a time joins the meeting with a listing of its own kind, if one meeting
     of the board that day has one.
  3. Otherwise it joins the board's only meeting that day, if that meeting has no listing from
     the same source: a calendar's "Conservation Commission Meeting" and an Agenda Center's
     "Conservation Commission Special Meeting Agenda" name one meeting differently, but one
     source's two listings of different kinds are two meetings (Lowell's Agenda Center posts a
     School Committee meeting and a special meeting for one day).
  4. Otherwise, if the board has two or more meetings that day that it could be (two of its
     kind, or several none of its kind, listed elsewhere), it's shown on its own, and its page
     says it couldn't be told which it is (unplaced). If it could be none of them, it's a
     meeting of its own.

The decision is kept in data/meetings/identities.json, written with the meetings
(save_store in pipeline/fetch_meetings.py), and never made again: a listing found the next
day joins the meeting it matches, and one already placed stays where it is. Each meeting is
kept under its id, which is its page's address, fixed when it's first identified:

  - the date and the board, as the page shows it: 2026-10-07-school-committee;
  - for a board's second meeting that day (one already has the address), its start time,
    2026-10-06-planning-commission-1915, or without one the next free number, -2, -3, in the
    order the meetings were first recorded.

So a meeting's address doesn't depend on which source listed it, nor change when another
source lists it later. The meetings already published when this file was first written kept
their addresses (the address of their first recorded listing); every other address a
meeting's listings had (`addresses`) sends its readers to the meeting's page (moved.html).

When a listing changes after it's placed:

  - its time, place, title or status: it stays in its meeting; the page shows the change in
    its history.
  - its date or board (a meeting moved to another day): when every listing of the meeting
    moves, the meeting moves with them and keeps its address (as a meeting listed in one
    place always has); when only some do, those leave it and are placed again on their new
    day, by the rules above, and the meeting stays with the listings that didn't move.
  - a listing given a new id by its source, keeping its own address (a district's Drive
    meeting taken over by the city calendar): it goes back to the meeting at that address.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from pipeline.files import write_atomic
from pipeline.meeting_names import words

LEDGER = "identities.json"

# Words in a listing's title that make it a different meeting of the board than one without them:
# its Grant Committee, a subcommittee, a special meeting, a hearing.
KIND_WORDS = {"committee", "subcommittee", "workshop", "hearing", "special", "joint", "executive", "walk"}
PLURALS = {"committees": "committee", "subcommittees": "subcommittee", "hearings": "hearing", "workshops": "workshop"}
# Words a Drive file's variant can have that say nothing of which meeting it is.
PLAIN_VARIANT = {"meeting", "mtg", "regular", "agenda", "minutes", "and", "the", "of", "a"}


def first_recorded(m: dict) -> tuple:
    """The order listings were recorded in: by first_seen, then (first seen in the same run) by id in
    number order, agendacenter-999 before agendacenter-1000, whatever order the store lists them in."""
    return m.get("first_seen", ""), tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", m["id"]))


def source(m: dict) -> str:
    return m.get("source", "calendar")


def _singular(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")) else w


def board_key(body: str, town: str = "") -> str:
    """A board's name in the form compared: 'Waltham Historical Commission' (in Waltham) and
    'Historical Commission' -> 'historical commission'; 'City Council Finance Sub Committee' ->
    'city council finance subcommittee'; 'Board of Assessor' and 'Board of Assessors' -> 'board of assessor'."""
    text = words(body)
    prefix = words(town) if town.strip() else None
    if prefix and text.startswith(prefix) and len(text) > len(prefix):
        text = text[len(prefix) - 1:]
    text = text.replace(" sub committee ", " subcommittee ")
    return " ".join(_singular(w) for w in text.split() if w != "the")


def _key_text(text: str, town: str) -> str:
    return f" {board_key(text, town)} "


def joint_boards(m: dict, known: dict[str, str], town: str = "") -> list[str]:
    """The boards a joint meeting's listing names, as board keys, or [] for one board's meeting.
    A listing is a joint meeting's when its title says "joint" and names another of the town's boards,
    unless the town's board mapping already gave it a joint board of its own (known: every board key recorded, to its name). 'School Committee and City
    Council Joint Meeting', for the City Council -> ['city council', 'school committee']."""
    title = m.get("posted_title") or m.get("title") or ""
    own = board_key(m["body"], town)
    # A board the town's mapping already names as a joint one ("Joint City Council and School
    # Committee") is that board.
    if " joint " in f" {own} " or " joint " not in words(title):
        return []
    haystack = _key_text(title, town)
    found = {k for k in known if " joint " not in f" {k} " and f" {k} " in haystack} | {own}
    # "City Council" inside "City Council Finance Committee" is the committee's name, not a second board.
    found = {k for k in found if not any(k != o and f" {k} " in f" {o} " for o in found)}
    return sorted(found) if len(found) >= 2 else []


def listing_kind(m: dict, board_words: set[str] | None = None) -> frozenset[str]:
    """The words of a listing's title, beyond its boards' names, that say what kind of meeting of
    the board it is. 'Council on Aging Grant Committee Meeting Agenda (PDF)' for the Council on Aging
    -> {'committee'}; 'Board of Commissioners Meeting' for the Malden Housing Authority -> {}. A
    Drive file's variant ('Governance Workshop') counts as its title, and words of it that say no
    kind ('Governance') make a kind of their own."""
    title = m.get("posted_title") or m.get("title") or ""
    normal = lambda text: {PLURALS.get(w, w) for w in words(text).split()}
    board = board_words if board_words is not None else normal(m["body"])
    variant = m.get("variant") or ""
    found = (normal(f"{title} {variant}") - board) & KIND_WORDS
    rest = normal(variant) - board - KIND_WORDS - PLAIN_VARIANT
    if rest:
        found.add("variant " + " ".join(sorted(rest)))
    return frozenset(found | ({"special"} if m.get("special") else set()))


class Listing:
    """What a record says about which meeting it is."""

    def __init__(self, m: dict, known: dict[str, str], town: str):
        self.record = m
        self.id = m["id"]
        self.date = m["date"]
        self.time = m.get("start_time") or None
        self.source = source(m)
        self.order = first_recorded(m)
        joint = joint_boards(m, known, town)
        self.boards = joint or [board_key(m["body"], town)]
        self.board = " + ".join(self.boards)
        names = [known.get(k, k) for k in self.boards]
        board_words = {w for name in [m["body"], *names] for w in words(name).split()} | ({"joint"} if joint else set())
        self.kind = listing_kind(m, {PLURALS.get(w, w) for w in board_words})


class Cluster:
    """The listings of one meeting, while they're being placed."""

    def __init__(self, listings: list[Listing], meeting: str | None = None, unplaced: bool = False):
        self.listings = listings
        self.meeting = meeting
        self.unplaced = unplaced

    @property
    def times(self) -> set[str]:
        return {x.time for x in self.listings if x.time}

    @property
    def kinds(self) -> set[frozenset]:
        return {x.kind for x in self.listings}

    @property
    def sources(self) -> set[str]:
        return {x.source for x in self.listings}

    @property
    def first(self) -> Listing:
        return min(self.listings, key=lambda x: x.order)


AMBIGUOUS = object()


def place(x: Listing, clusters: list[Cluster]):
    """The meeting an untimed listing is, among a board's meetings that day (rules 2 to 4): a cluster,
    None for a meeting of its own, or AMBIGUOUS when it could be more than one."""
    same = [c for c in clusters if x.kind in c.kinds and not c.unplaced]
    if len(same) == 1:
        return same[0]
    if len(same) > 1:
        return AMBIGUOUS
    elsewhere = [c for c in clusters if x.source not in c.sources and not c.unplaced]
    if len(elsewhere) == 1 and len([c for c in clusters if not c.unplaced]) == 1:
        return elsewhere[0]
    return AMBIGUOUS if elsewhere else None


def cluster_day(existing: list[Cluster], new: list[Listing]) -> list[Cluster]:
    """A board's meetings on a day: its meetings already identified (existing, never merged with one
    another) with its new listings placed among them. Returns the new clusters."""
    clusters = list(existing)
    created: list[Cluster] = []
    timed = sorted((x for x in new if x.time), key=lambda x: x.order)
    untimed = sorted((x for x in new if not x.time), key=lambda x: x.order)
    # 1. The same start time.
    by_time: dict[str, Cluster] = {}
    for x in timed:
        home = next((c for c in clusters if x.time in c.times), None) or by_time.get(x.time)
        if home is None:
            home = by_time[x.time] = Cluster([])
            created.append(home)
        home.listings.append(x)
    # A meeting already identified without a time takes the new timed listing it would have gone
    # with had both been recorded at once, so the order sources list a meeting in doesn't matter.
    taken = set()
    for c in [c for c in existing if not c.times and not c.unplaced]:
        others = [o for o in clusters + created if o is not c]
        target = place(c.first, others)
        if isinstance(target, Cluster) and target in created and id(target) not in taken \
                and all(place(x, others) is target for x in c.listings):
            taken.add(id(target))
            c.listings += target.listings
            created.remove(target)
    clusters += created
    # 2-4. Listings without a time: first those of a kind one meeting has, then the rest in the order
    # recorded, so a listing never goes where it would have gone only for another's being placed first.
    rest = []
    for x in untimed:
        target = place(x, clusters)
        if isinstance(target, Cluster) and x.kind in target.kinds:
            target.listings.append(x)
        else:
            rest.append(x)
    for x in rest:
        target = place(x, clusters)
        if isinstance(target, Cluster):
            target.listings.append(x)
            continue
        unplaced = target is AMBIGUOUS
        # A repost of a listing already shown on its own goes with it.
        home = next((c for c in clusters if c.unplaced and x.kind in c.kinds), None) if unplaced else None
        if home is None:
            home = Cluster([], unplaced=unplaced)
            clusters.append(home)
            created.append(home)
        home.listings.append(x)
    return created


def load(data_dir: Path) -> dict | None:
    """The kept identities, or None if this town has none yet."""
    path = data_dir / "meetings" / LEDGER
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save(data_dir: Path, ledger: dict) -> None:
    write_atomic(data_dir / "meetings" / LEDGER, json.dumps(ledger, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", "and")).strip("-")


def display_body(records: list[dict], known: dict[str, str], town: str) -> str:
    """The board a meeting is shown under: its first recorded listing's, or, for a joint meeting
    posted under each of its boards, 'Joint City Council and School Committee'."""
    first = min(records, key=first_recorded)
    if len({board_key(m["body"], town) for m in records}) == 1:
        return first["body"]
    names = sorted({known.get(k, k) for k in joint_boards(first, known, town)} or {m["body"] for m in records})
    return "Joint " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0])


def with_ids(store: dict) -> dict:
    """The store with each record's id in it, as saved (a test's store may leave it to the key)."""
    return {i: m if "id" in m else {**m, "id": i} for i, m in store.items()}


def known_boards(store: dict, town: str) -> dict[str, str]:
    """Every board recorded, by board_key, to its name as first recorded."""
    known: dict[str, str] = {}
    for m in sorted(store.values(), key=first_recorded):
        known.setdefault(board_key(m["body"], town), m["body"])
    return known


def assign(store: dict, ledger: dict | None, town: str = "") -> dict:
    """The identities of every listing in the store: those kept in ledger, with every other listing
    placed by the rules above. ledger None is a town whose meetings were published before identities
    were kept: each meeting identified now keeps the address its first recorded listing had."""
    first_time = ledger is None
    ledger = ledger or {}
    store = with_ids(store)
    town = town or ledger.get("town", "")
    known = known_boards(store, town)
    listing = {i: Listing(m, known, town) for i, m in store.items()}
    meetings: dict[str, dict] = {}
    placed: dict[str, str] = {}
    # Each kept meeting keeps the listings that still fit it, or, when every one has moved to another
    # day (or board), moves with them.
    for mid, entry in sorted(ledger.get("meetings", {}).items()):
        xs = sorted((listing[i] for i in entry.get("listings", []) if i in listing and i not in placed), key=lambda x: x.order)
        fit = [x for x in xs if (x.date, x.board) == (entry["date"], entry["board"])]
        if xs and not fit:
            fit = [x for x in xs if (x.date, x.board) == (xs[0].date, xs[0].board)]
            entry = {**entry, "date": xs[0].date, "board": xs[0].board}
        meetings[mid] = {**entry, "listings": [x.id for x in fit]}
        for x in fit:
            placed[x.id] = mid
    # A listing given a new id, keeping its own address, goes back to the meeting at that address.
    for x in sorted((x for x in listing.values() if x.id not in placed), key=lambda x: x.order):
        mid = x.record.get("slug")
        if mid in meetings and not meetings[mid]["listings"]:
            meetings[mid].update(listings=[x.id], date=x.date, board=x.board)
            placed[x.id] = mid

    days: dict[tuple, list[Listing]] = defaultdict(list)
    for x in listing.values():
        if x.id not in placed:
            days[(x.date, x.board)].append(x)
    created: list[Cluster] = []
    for (day, board), new in sorted(days.items()):
        existing = [Cluster([listing[i] for i in e["listings"]], mid, e.get("unplaced", False))
                    for mid, e in sorted(meetings.items()) if (e["date"], e["board"]) == (day, board) and e["listings"]]
        created += cluster_day(existing, new)
        for c in existing:
            meetings[c.meeting]["listings"] = [x.id for x in sorted(c.listings, key=lambda x: x.order)]

    # Addresses: every page address already given, and every listing's own (published as a meeting's
    # page or a moved page before identities were kept), are never given to another meeting.
    taken = {mid for mid in meetings} | {a for e in meetings.values() for a in e.get("addresses", [])}
    own = {i: m.get("slug") for i, m in store.items() if m.get("slug")}
    reserved = taken | set(own.values())
    for c in sorted(created, key=lambda c: (c.listings[0].date, c.listings[0].board, min(c.times, default="~"), c.first.order)):
        xs = sorted(c.listings, key=lambda x: x.order)
        mine = {own[x.id] for x in xs if x.id in own}
        if first_time and xs[0].id in own and own[xs[0].id] not in taken:
            mid = own[xs[0].id]
        else:
            natural = f"{xs[0].date}-{slugify(display_body([x.record for x in xs], known, town))}"
            free = lambda s: s not in taken and (s not in reserved or s in mine)
            time = min(c.times, default=None)
            candidates = [natural] + ([f"{natural}-{time.replace(':', '')}"] if time else [])
            mid = next((s for s in candidates if free(s)), None)
            n = 2
            while mid is None:
                mid = f"{natural}-{n}" if free(f"{natural}-{n}") else None
                n += 1
        taken.add(mid)
        entry = {"date": xs[0].date, "board": xs[0].board, "listings": [x.id for x in xs]}
        if first_time:
            # In the order the meeting's page lists its listings, as their moved pages were numbered.
            from pipeline.listings import posted
            entry["addresses"] = [own[x.id] for x in sorted(xs, key=lambda x: posted(x.record)) if own.get(x.id, mid) != mid]
        if c.unplaced:
            entry["unplaced"] = True
        meetings[mid] = entry
    return {"town": town, "meetings": dict(sorted(meetings.items()))}


def update(data_dir: Path, store: dict, town: str, previous: dict | None = None) -> dict:
    """Place the store's new listings, and keep the result (pipeline/fetch_meetings.save_store).
    previous is the store as it was before this run: a town's first identities are those of the
    meetings it had published, before this run's new listings are placed among them."""
    ledger = load(data_dir)
    if ledger is None and previous:
        ledger = assign(previous, None, town)
    ledger = assign(store, ledger, town)
    save(data_dir, ledger)
    return ledger


def meetings(store: dict, ledger: dict | None, town: str = "") -> list[dict]:
    """Each meeting with listings: {"id" (its address), "records" (first recorded first), "addresses"
    (its other addresses), "unplaced", "body" (the board it's shown under), "kinds_differ"}. ledger is
    the kept identities, from which any listing not yet placed is placed as assign() would."""
    town = town or (ledger or {}).get("town", "")
    store = with_ids(store)
    full = assign(store, ledger, town)
    known = known_boards(store, town)
    out = []
    for mid, e in full["meetings"].items():
        records = sorted((store[i] for i in e["listings"]), key=first_recorded)
        if not records:
            continue
        out.append({"id": mid, "records": records, "addresses": e.get("addresses", []), "unplaced": e.get("unplaced", False),
                    "body": display_body(records, known, town),
                    "kinds_differ": len({Listing(m, known, town).kind for m in records}) > 1})
    return out
