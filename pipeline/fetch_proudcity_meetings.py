"""Collect a city's meetings, agendas and minutes from its ProudCity website.

A ProudCity site posts each meeting as a post with its own page (see pipeline/proudcity.py). Meetings
are recorded alongside the town's under data/meetings/, as posted by [proudcity_meetings] source_name:
the boards are named in its bodies and body.

Each run reads only what changed. It asks the site's REST API for the meeting posts modified since the
previous run started, less two days (pipeline/wordpress.py): one request on most days. A post's page is
read when it's new or has changed since it was last read: a city adds an agenda, then minutes or a
recording, by editing the post, so a changed post is all there is to read again. The first run lists
the posts changed from a year before `since` on, and reads those whose title's date (if it has one) is
from a year before `since` on, since a title can carry the wrong year; a meeting whose page dates it
before `since` isn't recorded. A post whose title dates it before `since` isn't read, unless its title's year is behind the
year it was posted, as a mistyped year is. Posts whose title has no board, or that exclude_pattern leaves
out, aren't read either: they're kept in meetings/proudcity_status.json (skipped), for a year, and read
once bodies or exclude_pattern takes them in, and the names they share, without their dates, are
reported (unrecognized).

Pages are read newest meeting first (meetings more than a week ahead last), up to max_pages_per_run
(default 100) a run, and until the run has
saved max_documents_per_run (default 60) documents (a page's are saved together, so it can go a few
over). What's left is kept in meetings/proudcity_status.json (to_read) and read on later runs, newest
first: the first run of a site with a year of meetings reads the rest over the next days.

From each page read:
- the agenda: its attached file (a Word file saved as a PDF of its text), else its text, saved as a
  PDF of its paragraphs once the meeting is a week away or less: a post made further ahead holds the
  meeting's notice, and the agenda is filled in later by editing the post, which has it read again. A
  cancelled meeting's agenda isn't saved;
- the minutes: the Minutes tab's file, unless minutes = false;
- the minutes of earlier meetings its Agenda or Agenda Packet tab links (but those exclude_pattern leaves
  out by their label) (a Google Doc exported as a PDF,
  a Drive file as Google gives it for download), each the minutes of the meeting that day of the board
  its label names, or the linking meeting's board, where that meeting has no minutes of its own (a
  download that fails is tried on the next two runs). A
  link whose meeting isn't recorded (yet) is kept in the status file and tried again each run, and
  listed there (linked_minutes_unmatched), never made into a meeting;
- the packet's links, kept as `links`, never downloaded, and the YouTube recording.

A document is saved again, as a new version with the change in the meeting's history, only when its
content changed. The same file address is the same file: ProudCity names each upload with a hash.

An upcoming meeting whose post is gone (asked of the API with include=, one request) is marked as no
longer listed.

Usage:
    python -m pipeline.fetch_proudcity_meetings [--town holyoke]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import finalsite, proudcity, wordfile, wordpress
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import (
    load_store, meetings_dir, merge, pdf_has_text, record_change, save_json, save_store, slugify, unique_slug,
)
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient
from pipeline.waiting import Waiting

STATUS_FILE = "proudcity_status.json"
# The most pages of 100 changed posts one listing reads: more is an error, not a part of the listing.
LISTING_PAGES = 50
# How far before `since` the first run lists, and reads titles dated from.
FIRST_RUN_BACK = timedelta(days=366)
# How many times a linked minutes' download is tried, a run each, before it's given up.
LINKED_TRIES = 3
# How soon before its meeting an agenda typed into the page is taken for the agenda. A post made weeks ahead
# holds the meeting's notice ("AGENDA PENDING") until the clerk fills in the agenda, a few days before the
# meeting (the law's 48 hours at least), by editing the post, which has it read again.
AGENDA_TEXT_DAYS = 7


def meeting_id(post_id: str) -> str:
    return f"{proudcity.SOURCE}-{post_id}"


def new_meeting(store: dict, post: dict, found: dict, info: dict, settings: dict, stamp: str) -> dict:
    meeting = {
        "id": meeting_id(post["id"]),
        "source": proudcity.SOURCE,
        "source_url": post["link"],
        "source_name": settings["source_name"],
        "post_id": post["id"],
        "first_seen": stamp,
        "slug": unique_slug(f"{found['date']}-{slugify(info['body'])}", store),
        "date": found["date"],
        "body": info["body"],
        # As first read, so the first run's merge records no change.
        "start_time": found["start_time"],
        "end_time": None,
        "location": found["location"],
        "status": info["status"],
    }
    store[meeting["id"]] = meeting
    return meeting


class Saver:
    """Saves a meeting's agendas and minutes, counting what it saved."""

    def __init__(self, client, storage, settings: dict, stamp: str):
        self.client, self.storage, self.settings, self.stamp = client, storage, settings, stamp
        self.added = self.changed = 0

    @property
    def saved(self) -> int:
        return self.added + self.changed

    def keep(self, meeting: dict, kind: str, content: bytes, record: dict) -> None:
        """Save a document unless it's the same as the meeting's latest of its kind."""
        field = "agendas" if kind == "agenda" else "minutes"
        docs = meeting.get(field, [])
        latest = docs[-1] if docs else None
        sha = hashlib.sha256(content).hexdigest()
        if latest and latest["sha256"] == sha:
            # The same content at a new address: remember it, so it isn't downloaded again.
            latest.update(source_url=record["source_url"])
            return
        doc_id = f"{record.pop('key')}-{sha[:8]}"
        self.storage.put(field, f"{doc_id}.pdf", content)
        doc = {"id": doc_id, **record, "file": f"{doc_id}.pdf", "sha256": sha, "bytes": len(content),
               "has_text": pdf_has_text(content), "fetched_at": self.stamp}
        if latest:
            record_change(meeting, kind, latest["id"], doc_id, self.stamp)
            self.changed += 1
        else:
            self.added += 1
        meeting.setdefault(field, []).append(doc)

    def download(self, url: str) -> tuple[bytes, str | None]:
        """A file as a PDF, and what it was converted from: a Word file's text, or a PDF as it is."""
        content = self.client.get(url).content
        if wordfile.is_docx(content):
            return wordfile.to_pdf(content), "docx"
        if not content.startswith(b"%PDF"):
            # A Google Doc or Drive file that isn't shared publicly answers with a sign-in page.
            raise FetchError(f"{url}: not a PDF or a Word (.docx) file")
        return content, None

    def site_file(self, meeting: dict, kind: str, file: dict) -> None:
        """An attached file of the meeting's page, unless the latest saved is from the same address."""
        field = "agendas" if kind == "agenda" else "minutes"
        docs = meeting.get(field, [])
        if docs and docs[-1]["source_url"] == file["url"]:
            return
        content, converted = self.download(file["url"])
        self.keep(meeting, kind, content, {
            "key": f"{proudcity.SOURCE}-{meeting['post_id']}", "title": file["file"], "source_url": file["url"],
            "original_filename": file["file"], **({"converted_from": converted} if converted else {})})

    def agenda_text(self, meeting: dict, paragraphs: list[str], page_url: str) -> None:
        """The agenda typed into the page, saved as a PDF of its paragraphs."""
        content = wordfile.text_pdf(paragraphs, "Publick (from the agenda on a city web page)")
        self.keep(meeting, "agenda", content, {
            "key": f"{proudcity.SOURCE}-{meeting['post_id']}", "title": "Agenda", "source_url": page_url,
            "original_filename": None, "converted_from": "html"})

    def linked_minutes(self, meeting: dict, url: str, linked_from: str) -> None:
        """Minutes another meeting's page links (a Google Doc, a Drive file, a PDF)."""
        content, converted = self.download(finalsite.download_url(url))
        self.keep(meeting, "minutes", content, {
            "key": finalsite.file_key(url) or hashlib.sha256(url.encode()).hexdigest()[:16],
            "title": "Minutes", "source_url": url, "original_filename": None,
            "posted_on": finalsite.posted_on(url, self.settings["source_name"]), "linked_from": linked_from,
            **({"converted_from": converted} if converted else {})})


def read_page(client, store: dict, post: dict, settings: dict, saver: Saver, pending: dict, stamp: str,
              since: str, today: str) -> tuple[str, dict | None]:
    """Read a post's page: record its meeting, save its documents, and note the minutes it links.
    Returns what became of it ("created", "recorded", "before", "excluded", "no date") and its meeting."""
    found = proudcity.parse_meeting(client.get(post["link"]).text, post["link"])
    title = found["title"] or post["title"]
    info = proudcity.meeting_info(title, settings)
    if info is None:
        return "excluded", None
    if not found["date"]:
        return "no date", None
    if found["date"] < since:
        return "before", None
    meeting = store.get(meeting_id(post["id"]))
    created = meeting is None
    if created:
        meeting = new_meeting(store, post, found, info, settings, stamp)
    fields = {"date": found["date"], "body": info["body"], "status": info["status"], "special": info["special"],
              "title": title_for(info["body"], info["special"]), "posted_title": title,
              "documents_url": post["link"], "location": found["location"], "links": found["links"], "listed": True}
    if found["start_time"]:
        fields["start_time"] = found["start_time"]
    fields.update(found["video"] or {})
    merge(meeting, fields, stamp, track=True)
    meeting.update(post_modified=post["modified"], last_seen=stamp, checked_at=stamp)

    if info["status"] != "cancelled":
        if found["agenda_file"]:
            saver.site_file(meeting, "agenda", found["agenda_file"])
        elif found["agenda_text"] and found["date"] <= text_until(today):
            saver.agenda_text(meeting, found["agenda_text"], post["link"])
    if settings.get("minutes", True):
        if found["minutes_file"]:
            saver.site_file(meeting, "minutes", found["minutes_file"])
        for linked in found["linked_minutes"]:
            # exclude_pattern leaves out minutes of what isn't a board's meeting ("Executive Session Minutes").
            if linked["date"] < found["date"] and not proudcity.excluded(linked["label"], settings):
                body = proudcity.linked_board(linked["label"], settings) or info["body"]
                pending[linked["url"]] = {"date": linked["date"], "body": body, "label": linked["label"],
                                          "linked_from": post["link"]}
    return "created" if created else "recorded", meeting


def text_until(today: str) -> str:
    """The last meeting date whose agenda typed into its page is saved (AGENDA_TEXT_DAYS)."""
    return (date.fromisoformat(today) + timedelta(days=AGENDA_TEXT_DAYS)).isoformat()


def reading_order(post: dict, today: str) -> tuple:
    """Pages are read newest meeting first, those of meetings more than AGENDA_TEXT_DAYS ahead last (their
    posts hold only a notice yet), by the title's date, or when the post changed for a title without one."""
    day = post["title_date"] or post["modified"][:10]
    later = day > text_until(today)
    return (not later, day if not later else "", day)


def attach_linked(store: dict, pending: dict, saver: Saver, limit: int, since: str, errors: list) -> list[dict]:
    """Save the minutes later packets link to the meetings they're of. Returns the links whose meeting
    isn't recorded (kept in `pending` for the next run)."""
    by_day = {(m["body"], m["date"]): m for m in store.values() if m.get("source") == proudcity.SOURCE}
    unmatched = []
    for url, link in sorted(pending.items(), key=lambda item: item[1]["date"], reverse=True):
        if link["date"] < since:
            del pending[url]
            continue
        meeting = by_day.get((link["body"], link["date"]))
        if meeting is None:
            unmatched.append({"url": url, **link})
            continue
        latest = (meeting.get("minutes") or [None])[-1]
        # The meeting's own minutes, or these already saved: nothing to do.
        if latest and (not latest.get("linked_from") or latest["source_url"] == url):
            del pending[url]
            continue
        if saver.saved >= limit:
            continue
        try:
            saver.linked_minutes(meeting, url, link["linked_from"])
        except FetchError as e:
            # Tried again on the next runs, a few times: a timeout passes, a Doc not shared publicly doesn't.
            errors.append(str(e))
            link["tries"] = link.get("tries", 0) + 1
            if link["tries"] < LINKED_TRIES:
                continue
        del pending[url]
    return unmatched


def mark_gone(client, store: dict, settings: dict, today: str, stamp: str) -> None:
    """Mark upcoming meetings whose post the site no longer lists (deleted, or made private) as no longer listed."""
    upcoming = sorted(m["post_id"] for m in store.values()
                      if m.get("source") == proudcity.SOURCE and m["date"] >= today and m.get("listed", True))
    for start in range(0, len(upcoming), wordpress.PER_PAGE):
        ids = upcoming[start:start + wordpress.PER_PAGE]
        found = wordpress.read_all(client, settings["site_url"], proudcity.ROUTE, {"include": ",".join(ids)},
                                   fields=("id",), max_pages=1)
        listed = {str(item["id"]) for item in found}
        for post_id in ids:
            if post_id not in listed:
                merge(store[meeting_id(post_id)], {"listed": False}, stamp, track=True)


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["proudcity_meetings"]
    since = str(settings["since"])
    first_back = (date.fromisoformat(since) - FIRST_RUN_BACK).isoformat()
    year_ago = (now.date() - FIRST_RUN_BACK).isoformat()
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)
    status_path = meetings_dir(data_dir) / STATUS_FILE
    previous = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}

    # What changed since the last run, or, on the first, from a year before `since`.
    after = wordpress.changed_since(previous.get("listed_at")) or f"{first_back}T00:00:00"
    posts = proudcity.parse_listing(wordpress.read_all(
        client, settings["site_url"], proudcity.ROUTE, proudcity.listing_params(after),
        fields=proudcity.LISTING_FIELDS, max_pages=LISTING_PAGES))
    to_read = dict(previous.get("to_read", {}))
    # Posts not read for their title (no board in bodies, or left out by exclude_pattern), from this listing and
    # those before: each is read once bodies or exclude_pattern takes it in, though the city never edits it.
    skipped = {**previous.get("skipped", {}), **{p["id"]: p for p in posts}}
    for post_id, post in list(skipped.items()):
        meeting = store.get(meeting_id(post_id))
        if meeting and meeting.get("post_modified") == post["modified"]:
            del skipped[post_id]   # read already, in the days the listing overlaps the last
        elif proudcity.before(post, max(since, year_ago)):
            # Before `since`, or held more than a year ago: a board named in bodies later starts from there.
            del skipped[post_id]
        elif not proudcity.excluded(post["title"], settings) and proudcity.board_for(post["title"], settings):
            to_read[post_id] = post
            del skipped[post_id]
    unrecognized = sorted({proudcity.name_of(p["title"]) for p in skipped.values()
                           if not proudcity.excluded(p["title"], settings)})

    pending = dict(previous.get("linked_minutes", {}))
    saver, errors, read, created, waiting = Saver(client, storage, settings, stamp), [], 0, 0, Waiting()
    limit, page_limit = settings.get("max_documents_per_run", 60), settings.get("max_pages_per_run", 100)
    # Newest meeting first, by its title's date, or when it was changed for a title without one.
    for post in sorted(to_read.values(), key=lambda p: reading_order(p, today), reverse=True):
        if read >= page_limit or saver.saved >= limit:
            waiting.add(post["title_date"] or post["modified"][:10])
            continue
        read += 1
        try:
            outcome, _ = read_page(client, store, post, settings, saver, pending, stamp, since, today)
        except FetchError as e:
            errors.append(str(e))
            if e.status not in (404, 410):
                continue   # left to read again on the next run; a page that's gone isn't
            outcome = "gone"
        if outcome == "no date":
            errors.append(f"{post['link']}: no meeting date in the page's header")
        created += outcome == "created"
        del to_read[post["id"]]

    unmatched = attach_linked(store, pending, saver, limit, since, errors)
    try:
        mark_gone(client, store, settings, today, stamp)
    except FetchError as e:
        errors.append(str(e))

    save_store(data_dir, store, config)
    status = {
        "updated_at": stamp,
        # When this run listed the changed posts from: the next run lists those changed since, less two days.
        "listed_at": stamp,
        "posts_listed": len(posts),
        "pages_read": read,
        "meetings_created": created,
        "documents_added": saver.added,
        "documents_changed": saver.changed,
        **waiting.status("meetings"),
        "requests": getattr(client, "request_count", None),
        # Titles with no board: add them, or words in them, to [proudcity_meetings.bodies] if they're a board's.
        # The names (titles without their dates) of posts with no board, until bodies names them.
        "unrecognized": unrecognized,
        "new_unrecognized": sorted(set(unrecognized) - set(previous.get("unrecognized", []))),
        # Minutes a packet links for a meeting not recorded: a board bodies doesn't name, or a meeting not read yet.
        "linked_minutes_unmatched": unmatched,
        "errors": errors,
        "to_read": to_read,
        "skipped": skipped,
        "linked_minutes": pending,
    }
    save_json(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "proudcity_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["proudcity_meetings"].get("request_delay", 2.0))
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::The city website's meetings could not be read: {e}")
        return 1
    print(json.dumps({k: v for k, v in status.items() if k not in ("to_read", "skipped", "linked_minutes")}, indent=2))
    for name in status["new_unrecognized"]:
        print(f"::warning::No listed board for {name!r}")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
