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
  time   its start time, where it gives one; and for one that doesn't (an Agenda Center's listing),
         the time its agenda gives, from the agenda's summary (agenda_times).

The listings of one board on one day are put into meetings by these rules, in this order:

  1. Listings that give the same start time are one meeting. A listing without a time whose agenda
     gives one joins the meeting that starts then, if one meeting of the board that day does: Derry's
     calendar lists the Conservation Commission's 7 PM meeting, and its Agenda Center that meeting
     and a site walk, neither with a time, but their agendas say 7 PM and 3 PM. Where no meeting
     starts then, the listing is placed by the rules below, as one without a time: an agenda and a
     calendar can give one meeting's time half an hour apart.
  2. A listing without a time joins the meeting with a listing of its own kind, if one meeting
     of the board that day has one.
  3. Otherwise it joins the only meeting of the board that day that it could be: one whose
     listings' kinds differ from its own only in saying it's special or a hearing (SAME_MEETING),
     with no listing from the same source. A calendar's "Conservation Commission Meeting" and an
     Agenda Center's "Conservation Commission Special Meeting Agenda" name one meeting
     differently; but one source's two listings of different kinds are two meetings (Lowell's
     Agenda Center posts a School Committee meeting and a special meeting for one day), and a
     site walk, a workshop, an executive session, a committee's meeting or a Drive file's own
     variant is a meeting of its own.
  4. Otherwise, if the board has two or more meetings that day that it could be (two of its
     kind, or by rule 3 several listed elsewhere), it's shown on its own, and its page says it
     couldn't be told which it is (unplaced); a meeting already identified that way becomes so
     when the second meeting it could be is found. If it could be none of them, it's a meeting of
     its own.

The decision is kept in data/meetings/identities.json, written with the meetings
(save_store in pipeline/fetch_meetings.py), and never made again: a listing found the next
day joins the meeting it matches, and one already placed stays where it is. But an agenda's
summary is made after its listing is placed: a meeting none of whose listings gives a time, whose
agenda's time is found to be that of the board's only other meeting that day, is that meeting
(rule 1, join_at_agenda_time). The one identified first keeps its address, and the other's leads
to it, as below. Each meeting is kept under its id, which is its page's address, fixed when it's
first identified:

  - the date and the board, as the page shows it: 2026-10-07-school-committee;
  - for a board's second meeting that day (one already has the address), its start time,
    2026-10-06-planning-commission-1915, or without one the next free number, -2, -3, in the
    order the meetings were first recorded.

So a meeting's address doesn't depend on which source listed it, nor change when another
source lists it later. The meetings already published when this file was first written kept
their addresses (the address of their first recorded listing); every other address a
meeting's listings had (`addresses`) leads to the meeting's page: a permanent redirect from the
network's Worker, and a page that sends readers on (moved.html) on any other host
(build_site.REDIRECTS_FILE). A new town's meetings have their own addresses from its first run.

A board is shown under its name as first recorded (known_boards), whichever of its names a
meeting's listings give, so it has one page; a page another of its names had leads there.

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
# Kinds that tell nothing apart: a listing that says its meeting is special or has a hearing, where
# another source's listing doesn't, can be the same meeting (rule 3).
SAME_MEETING = {"special", "hearing"}
PLURALS = {"committees": "committee", "subcommittees": "subcommittee", "hearings": "hearing", "workshops": "workshop"}
# Words a Drive file's variant can have that say nothing of which meeting it is.
PLAIN_VARIANT = {"meeting", "mtg", "regular", "agenda", "minutes", "and", "the", "of", "a"}
# A start time as an agenda's summary gives it (pipeline/summarize.py), as build_site.from_agenda reads it.
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


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


def agenda_times(store: dict, data_dir: Path) -> dict[str, str]:
    """The start time each listing without one of its own has from its agenda, by listing id: the time
    the summary of its latest agenda gives (summaries/<sha256>.json), as the meeting's page shows it.
    A listing whose agenda has no summary, or one that gives no time, has none."""
    # Imported here: pipeline.summarize reads the meetings through pipeline.listings, which uses this module.
    from pipeline import summarize
    times = {}
    for i, m in store.items():
        if m.get("start_time") or not m.get("agendas"):
            continue
        summary = summarize.cached(data_dir, m["agendas"][-1]["sha256"], "agenda", current=False) or {}
        if HHMM.fullmatch(summary.get("start_time") or ""):
            times[i] = summary["start_time"]
    return times


class Listing:
    """What a record says about which meeting it is. agenda_time: the start time its agenda gives
    (agenda_times), used only when the record gives none."""

    def __init__(self, m: dict, known: dict[str, str], town: str, agenda_time: str | None = None):
        self.record = m
        self.id = m["id"]
        self.date = m["date"]
        self.time = m.get("start_time") or None
        self.agenda_time = None if self.time else agenda_time
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
    def starts(self) -> set[str]:
        """When it starts: its listings' times, or where none gives one, their agendas'."""
        return self.times or {x.agenda_time for x in self.listings if x.agenda_time}

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
    """The meeting an untimed listing is, among a board's meetings that day (its agenda's time, then rules
    2 to 4): a cluster, None for a meeting of its own, or AMBIGUOUS when it could be more than one."""
    at = [c for c in clusters if x.agenda_time in c.starts and not c.unplaced] if x.agenda_time else []
    if len(at) == 1:
        return at[0]
    same = [c for c in clusters if x.kind in c.kinds and not c.unplaced]
    if len(same) == 1:
        return same[0]
    if len(same) > 1:
        return AMBIGUOUS
    could = [c for c in clusters if not c.unplaced and any(x.kind ^ k <= SAME_MEETING for k in c.kinds)]
    elsewhere = [c for c in could if x.source not in c.sources]
    if len(elsewhere) == 1 and len(could) == 1:
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
        elif target is AMBIGUOUS:
            # It could now be either of two meetings, as it would have been found to be had they been
            # recorded first.
            c.unplaced = True
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


def join_at_agenda_time(clusters: list[Cluster]) -> list[tuple[Cluster, Cluster]]:
    """A board's meetings on a day that are one by rule 1, though identified apart: a meeting none of whose
    listings gives a time, whose agendas give one, and the board's only other meeting that day at that time.
    An agenda's summary is made after its listing is placed, so its time is found after the listing has
    been identified as a meeting of its own. The meeting identified before the other keeps its address (a
    meeting already kept, before one only now placed). Returns (gone, home) pairs, home having gone's listings."""
    joined = []
    live = list(clusters)
    for c in sorted(clusters, key=lambda c: c.first.order):
        if c not in live or c.times or len(c.starts) != 1:
            continue
        at = [o for o in live if o is not c and not o.unplaced and c.starts <= o.starts]
        if len(at) != 1:
            continue
        home, gone = sorted((c, at[0]), key=lambda k: (k.meeting is None, k.first.order))
        home.listings += gone.listings
        # Its agenda's time tells which meeting it is.
        home.unplaced = False
        live.remove(gone)
        joined.append((gone, home))
    return joined


def load(data_dir: Path) -> dict | None:
    """The kept identities, or None if this town has none yet."""
    path = data_dir / "meetings" / LEDGER
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save(data_dir: Path, ledger: dict) -> None:
    write_atomic(data_dir / "meetings" / LEDGER, json.dumps(ledger, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", "and")).strip("-")


def display_body(records: list[dict], known: dict[str, str], town: str) -> str:
    """The board a meeting is shown under: the board's name as first recorded (known_boards), so
    'Waltham Historical Commission' on the calendar and 'Historical Commission' in the Agenda Center
    are one board, with one page; or, for a joint meeting posted under each of its boards, 'Joint
    City Council and School Committee'."""
    first = min(records, key=first_recorded)
    if len({board_key(m["body"], town) for m in records}) == 1:
        return known.get(board_key(first["body"], town), first["body"])
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


def assign(store: dict, ledger: dict | None, town: str = "", times: dict[str, str] | None = None) -> dict:
    """The identities of every listing in the store: those kept in ledger, with every other listing
    placed by the rules above. ledger None is a town whose meetings were published before identities
    were kept: each meeting identified now keeps the address its first recorded listing had. times:
    the start times listings' agendas give (agenda_times)."""
    first_time = ledger is None
    ledger = ledger or {}
    store = with_ids(store)
    town = town or ledger.get("town", "")
    known = known_boards(store, town)
    # Every name each board's listings have been recorded under, kept when a listing is later renamed:
    # a board page published under any of them stays reachable (build_site, moved pages).
    names = {k: set(v) for k, v in ledger.get("boards", {}).items()}
    for m in store.values():
        names.setdefault(board_key(m["body"], town), set()).add(m["body"])
    listing = {i: Listing(m, known, town, (times or {}).get(i)) for i, m in store.items()}
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
    kept: dict[tuple, list[Cluster]] = defaultdict(list)
    for mid, e in sorted(meetings.items()):
        if e["listings"]:
            kept[(e["date"], e["board"])].append(Cluster([listing[i] for i in e["listings"]], mid, e.get("unplaced", False)))
    created: list[Cluster] = []
    for day in sorted(set(days) | set(kept)):
        existing = kept[day]
        new = cluster_day(existing, days[day]) if days[day] else []
        gone = []
        for c, home in join_at_agenda_time(existing + new):
            gone.append(c)
            if c.meeting:
                # Its address leads to the meeting it's part of, as its other addresses did.
                e = meetings.pop(c.meeting)
                meetings[home.meeting]["addresses"] = meetings[home.meeting].get("addresses", []) + [c.meeting] + e.get("addresses", [])
        created += [c for c in new if c not in gone]
        for c in existing:
            if c in gone:
                continue
            meetings[c.meeting]["listings"] = [x.id for x in sorted(c.listings, key=lambda x: x.order)]
            if c.unplaced:
                meetings[c.meeting]["unplaced"] = True
            else:
                meetings[c.meeting].pop("unplaced", None)

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
    return {"town": town, "boards": {k: sorted(v) for k, v in sorted(names.items())},
            "meetings": dict(sorted(meetings.items()))}


def update(data_dir: Path, store: dict, town: str, previous: dict | None = None) -> dict:
    """Place the store's new listings, and keep the result (pipeline/fetch_meetings.save_store).
    previous is the store as it was before this run: a town's first identities are those of the
    meetings it had published, before this run's new listings are placed among them; a new town's
    (no meetings before this run) have their own addresses from the start."""
    ledger = load(data_dir)
    times = agenda_times({**(previous or {}), **store}, data_dir)
    if ledger is None:
        ledger = assign(previous or {}, None, town, times)
    ledger = assign(store, ledger, town, times)
    save(data_dir, ledger)
    return ledger


def meetings(store: dict, ledger: dict | None, town: str = "", times: dict[str, str] | None = None) -> list[dict]:
    """Each meeting with listings: {"id" (its address), "records" (first recorded first), "addresses"
    (its other addresses), "unplaced", "body" (the board it's shown under), "body_names" (the boards
    its listings name, which may have had pages of their own), "kinds_differ", "kinds_apart" (they differ
    in more than saying it's special or a hearing)}. ledger is
    the kept identities, from which any listing not yet placed is placed as assign() would; times, the
    start times listings' agendas give (agenda_times)."""
    town = town or (ledger or {}).get("town", "")
    store = with_ids(store)
    full = assign(store, ledger, town, times)
    known = known_boards(store, town)
    out = []
    for mid, e in full["meetings"].items():
        records = sorted((store[i] for i in e["listings"]), key=first_recorded)
        if not records:
            continue
        kinds = {Listing(m, known, town).kind for m in records}
        out.append({"id": mid, "records": records, "addresses": e.get("addresses", []), "unplaced": e.get("unplaced", False),
                    "body": display_body(records, known, town), "body_names": sorted({n for m in records
                                                        for n in full["boards"].get(board_key(m["body"], town), [m["body"]])}),
                    "kinds_differ": len(kinds) > 1, "kinds_apart": any((a ^ b) - SAME_MEETING for a in kinds for b in kinds)})
    return out
