"""A city's agendas and minutes from the document library on its WordPress website, joined to its calendar's events.

Brockton, Massachusetts posts its agendas and minutes as posts of a "public-document" type in the site's
REST API (/wp-json/wp/v2/public-documents, pipeline/wordpress.py), beside its calendar of meetings (The
Events Calendar, pipeline/tribe.py). Each document is filed under a term of the "document-category"
taxonomy (/wp-json/wp/v2/document-categories): "Meeting Agendas" and "Meeting Minutes" among others. The
listing filters by them (?document-categories=15,17) and by when a post was last changed
(modified_after), so a run lists only what changed.

The fields that join a document to its meeting are Advanced Custom Fields (ACF) the site's builder
added, in each post's `acf`:
- `event-public-document`: the calendar event the document is for, by its id (a list of one);
- `event_snapshot`: the event's title, local date and time, and id, as they were when the document was
  saved (a later change to the event isn't copied here, so its date can be stale);
- `file-public-document`: the document's file, a media id (/wp-json/wp/v2/media gives its address);
  empty for a document posted as the post's own text (`content`, "Online/Text" on the site).
These names are Brockton's site builder's, not a published plugin's, so they're this module's constants
(EVENT_FIELD, SNAPSHOT_FIELD, FILE_FIELD, and the post type and taxonomy), not settings: a second site
with the same library under other names would make them settings. A change of them on Brockton's site
shows as documents that link no event, which the run counts and warns of (documents_without_event in
meetings/wordpress_status.json).

A document's meeting is the event it links, by id. A document that links none (a few are saved without
the link) is the meeting its title dates ("2026-09-16 – Women’s Commission Meeting – Meeting Minutes"),
of the board its title names ([wordpress_meetings.bodies], or a board [wordpress_meetings.categories]
names), when exactly one meeting recorded from the calendar is that board's on that day; otherwise it's
reported, not guessed.

A document's file is saved as it is (a Word file as a PDF of its text, pipeline/wordfile.py); a document
with no file is its text, saved as a PDF of its paragraphs (pipeline/htmlfile.py). Where a post has both,
the text is the file's, and the file is saved.

This module has the parts that need no network or storage; the run is
pipeline/fetch_wordpress_meetings.py.
"""

from __future__ import annotations

import re

from pipeline import dates, wordpress
from pipeline.meeting_names import find_board

# Brockton's site builder's post type, taxonomy and fields (see above).
ROUTE = "wp/v2/public-documents"
TAXONOMY = "document-categories"
TAXONOMY_ROUTE = f"wp/v2/{TAXONOMY}"
EVENT_FIELD = "event-public-document"
SNAPSHOT_FIELD = "event_snapshot"
FILE_FIELD = "file-public-document"
MEDIA_ROUTE = "wp/v2/media"
# The fields read from the listing: the text (`content`) only matters for a document with no file.
LISTING_FIELDS = ("id", "link", "title", "modified", TAXONOMY, "acf", "content")
MEDIA_FIELDS = ("id", "source_url")
TERM_FIELDS = ("id", "name")


def listing_params(term_ids: list[int], modified_after: str) -> dict:
    """The listing's query: the documents filed under the agenda and minutes terms, changed after a time,
    the most recently changed first."""
    return {TAXONOMY: ",".join(str(t) for t in sorted(term_ids)), "modified_after": modified_after,
            "orderby": "modified", "order": "desc"}


def include_params(post_ids: list[str]) -> dict:
    """Particular documents, by id (at most wordpress.PER_PAGE of them)."""
    return {"include": ",".join(post_ids)}


def kinds(terms: list[dict], settings: dict) -> dict[int, str]:
    """The agenda and minutes terms' ids, each with its kind: {15: "agenda", 17: "minutes"}, from the site's
    terms (/wp-json/wp/v2/document-categories) and their names in [wordpress_meetings.documents]. Raises
    ValueError naming a term the site doesn't have."""
    by_name = {wordpress.rendered(t.get("name")).strip().lower(): t["id"] for t in terms}
    found = {}
    for kind, key in (("agenda", "agenda_category"), ("minutes", "minutes_category")):
        term = by_name.get(str(settings[key]).strip().lower())
        if term is None:
            raise ValueError(f"no document category named {settings[key]!r} ({key})")
        found[int(term)] = kind
    return found


def one_id(value) -> str | None:
    """An ACF relationship's id: a number, a string of digits, a list of one, or an object with an id;
    None for an empty one."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("id") or value.get("ID") or value.get("event_id")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    value = str(value or "").strip()
    return value if value.isdigit() and int(value) > 0 else None


def parse_document(item: dict, term_kinds: dict[int, str]) -> dict:
    """A document as the listing gives it: its id, page, title as plain text, when changed, kind ("agenda",
    "minutes", or None), the event it links (event_id), the date its title gives (else the snapshot's), its
    file's media id, and its text (HTML)."""
    acf = item.get("acf") if isinstance(item.get("acf"), dict) else {}
    snapshot = acf.get(SNAPSHOT_FIELD) if isinstance(acf.get(SNAPSHOT_FIELD), dict) else {}
    title = wordpress.rendered(item.get("title"))
    day = dates.find_date(title)
    snapshot_day = str(snapshot.get("event_date") or "")[:10]
    terms = [t for t in item.get(TAXONOMY) or [] if isinstance(t, int)]
    kind = next((term_kinds[t] for t in sorted(terms, key=lambda t: term_kinds.get(t) != "agenda") if t in term_kinds),
                None)
    content = item.get("content")
    return {
        "id": str(item["id"]),
        "link": item.get("link") or "",
        "title": title,
        "modified": str(item.get("modified") or ""),
        "kind": kind,
        "event_id": one_id(acf.get(EVENT_FIELD)) or one_id(snapshot.get("event_id")),
        # A hand-typed title's date before the snapshot's, which can be stale.
        "date": day.isoformat() if day else (snapshot_day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot_day) else None),
        "media_id": one_id(acf.get(FILE_FIELD)),
        "html": content.get("rendered", "") if isinstance(content, dict) else str(content or ""),
    }


def board_of(title: str, settings: dict) -> str | None:
    """The board a document's title names: the longest of [wordpress_meetings.bodies], or of the boards
    [wordpress_meetings.categories] names, found in it."""
    boards = sorted(set(settings.get("categories", {}).values()))
    return find_board(dates.without_dates(title), boards, settings.get("bodies", {}))


def file_urls(items: list[dict]) -> dict[str, str]:
    """The media listing's files' addresses, by media id."""
    return {str(m["id"]): m["source_url"] for m in items if m.get("source_url")}


# ---- The town's config --------------------------------------------------------------------

def check(settings, where: str) -> None:
    """A [wordpress_meetings.documents] table, checked when the config is loaded."""
    if not isinstance(settings, dict):
        raise SystemExit(f"{where} must be a table, with agenda_category and minutes_category.")
    missing = [key for key in ("agenda_category", "minutes_category") if not str(settings.get(key) or "").strip()]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the names of the library's categories of agendas "
                         'and of minutes, like "Meeting Agendas".')
