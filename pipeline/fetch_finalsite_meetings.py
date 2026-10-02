"""Collect a school board's meetings, agendas and minutes from its Finalsite page.

Many school districts post their board's meetings on their own Finalsite
website rather than with the town's (see pipeline/finalsite.py). In
Wallingford, Connecticut that is the Board of Education and its committees:
one post a meeting, with the agenda and minutes as Google Docs. Meetings are
recorded alongside the town's under data/meetings/, as their own meetings:
the boards are named in [finalsite_meetings.bodies], and none of them is a
board the town's calendar collects.

Each run reads the board's page once. A new post's body is read once; a
post is read again while its meeting is recent (recheck_days, default 60)
and it has no minutes yet, for the minutes and recording added after the
meeting. Posts for meetings before `since` are never read.

Agendas and minutes are saved as PDFs, Google Docs as exported by Google.
A Doc is edited in place (draft minutes become approved minutes at the same
address), so a saved Doc is exported again while it can still change: an
agenda until its meeting, minutes while the meeting is recent. A changed Doc
is saved as a new version and the change recorded in the meeting's history;
an unchanged one isn't saved again. Backup folders, presentations, and other
documents (Wallingford's "Motions") are kept as links, never downloaded. A
cancelled meeting's agenda is linked, not saved.

Titles that name no listed board, or no date, are listed in the run's
summary, not guessed at.

A district that posts each meeting only when its agenda is ready may publish
the year's dates on a page of their own (Wallingford's "Board of Education
Schedule 2026": "10/19/26 Operations Comm.", "10/26/26 BOE Meeting").
schedule_url names it, and [finalsite_meetings.schedule_names] the boards by
the names it uses; its upcoming meetings are listed to schedule_days_ahead
(default 60) days out. When the district posts one, the post takes over its
record, which keeps its page address. A scheduled meeting that leaves the
schedule before it happens, with no post, is marked as no longer listed.

Usage:
    python -m pipeline.fetch_finalsite_meetings [--town wallingford]
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import finalsite
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import (
    load_store, meetings_dir, merge, pdf_has_text, record_change, save_json, slugify, unique_slug,
)
from pipeline.filelist import title_for
from pipeline.http import FetchError, PoliteClient


def new_meeting(store: dict, post: dict, info: dict, settings: dict, stamp: str, meeting_id: str | None = None,
                source_url: str | None = None) -> dict:
    meeting = {
        "id": meeting_id or f"{finalsite.SOURCE}-{post['post_id']}",
        "source": finalsite.SOURCE,
        "source_url": source_url or settings["page_url"],
        "source_name": settings["source_name"],
        "post_id": post["post_id"],
        "first_seen": stamp,
        "slug": unique_slug(f"{info['date']}-{slugify(info['body'])}", store),
        "date": info["date"],
        "body": info["body"],
        "start_time": None,
        "end_time": None,
        "location": "",
    }
    store[meeting["id"]] = meeting
    return meeting


# A date in the schedule, 'M/D/YY' or 'M/D/YYYY', and a weekday after it: '1/19/27 (Tues.)'.
SCHEDULE_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])(?:\s*\([A-Za-z.]+\))?")
SCHEDULE_MONTH = re.compile(r"\b(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\b")


def parse_schedule(page: str) -> list[tuple[str, str]]:
    """(name, date) pairs from a schedule page: each date and the words after it, up to the
    next date or month heading ('10/19/26 Operations Comm.' -> ('Operations Comm.', '2026-10-19'))."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
    found = []
    dates = list(SCHEDULE_DATE.finditer(text))
    for i, d in enumerate(dates):
        end = dates[i + 1].start() if i + 1 < len(dates) else min(len(text), d.end() + 80)
        name = SCHEDULE_MONTH.split(text[d.end():end])[0].strip(" -")
        year = int(d.group(3)) + (2000 if len(d.group(3)) == 2 else 0)
        try:
            found.append((name, date(year, int(d.group(1)), int(d.group(2))).isoformat()))
        except ValueError:
            continue
    return found


def update_schedule(store: dict, page: str, settings: dict, today: str, stamp: str) -> int:
    """Record the schedule's upcoming meetings, to schedule_days_ahead out, that no post lists
    yet. (A past date with no post isn't taken for a meeting that was held.) Returns how many are new."""
    ahead = (date.fromisoformat(today) + timedelta(days=settings.get("schedule_days_ahead", 60))).isoformat()
    scheduled = {(body, day) for name, day in parse_schedule(page)
                 if (body := finalsite.find_board(name, [], settings["schedule_names"]))
                 and max(settings["since"], today) <= day <= ahead}
    recorded = {(m["body"], m["date"]) for m in store.values() if m.get("source") == finalsite.SOURCE}
    added = 0
    for body, day in sorted(scheduled - recorded):
        meeting = new_meeting(store, {"post_id": None}, {"date": day, "body": body}, settings, stamp,
                              meeting_id=f"schedule-{slugify(body)}-{day}", source_url=settings["schedule_url"])
        meeting.update(title=title_for(body, False), status="scheduled", special=False, listed=True, last_seen=stamp)
        added += 1
    # An upcoming meeting known only from the schedule that has left it was probably moved or cancelled.
    for m in store.values():
        if (m["id"].startswith("schedule-") and m.get("source") == finalsite.SOURCE and m.get("listed", True)
                and m["date"] >= today and (m["body"], m["date"]) not in scheduled):
            merge(m, {"listed": False}, stamp, track=True)
    return added


def adopt_scheduled(store: dict, post: dict, info: dict) -> dict | None:
    """A meeting first recorded from the schedule, now posted: it becomes the post's record,
    keeping its page address."""
    scheduled = store.pop(f"schedule-{slugify(info['body'])}-{info['date']}", None)
    if scheduled:
        scheduled.update(id=f"{finalsite.SOURCE}-{post['post_id']}", post_id=post["post_id"])
        store[scheduled["id"]] = scheduled
    return scheduled


def download(client, url: str, settings: dict, stamp: str) -> tuple[dict, bytes]:
    """A document's PDF and its record. Nothing is saved: the caller saves it if it's new."""
    response = client.get(finalsite.download_url(url))
    content = response.content
    if not content.startswith(b"%PDF"):
        # A Doc that isn't shared publicly answers with a sign-in page.
        raise FetchError(f"{url}: not a PDF")
    sha = hashlib.sha256(content).hexdigest()
    key = finalsite.file_key(url) or hashlib.sha256(url.encode()).hexdigest()[:16]
    doc_id = f"{key}-{sha[:8]}"
    return {
        "id": doc_id,
        "source_url": url,
        "posted_by": settings["source_name"],
        "posted_on": finalsite.posted_on(url, settings["source_name"]),
        "file": f"{doc_id}.pdf",
        "original_filename": finalsite.original_filename(response.headers.get("content-disposition", "")),
        "sha256": sha,
        "bytes": len(content),
        "has_text": pdf_has_text(content),
        "fetched_at": stamp,
    }, content


def update_documents(client, meeting: dict, storage, settings: dict, today: str, recent: str, stamp: str,
                     counts: dict, errors: list) -> None:
    """Save a meeting's agenda and minutes if they are new or have changed, adding to `counts`."""
    for kind, field in (("agenda", "agendas"), ("minutes", "minutes")):
        url = meeting.get(f"{kind}_url")
        docs = meeting.get(field, [])
        if not url or (kind == "agenda" and meeting["status"] == "cancelled" and not docs):
            continue
        latest = docs[-1] if docs else None
        if latest and latest["source_url"] == url:
            # The same document: only a Google Doc can change, while its meeting is upcoming
            # (an agenda) or recent (minutes).
            if not finalsite.is_google_doc(url) or meeting["date"] < (today if kind == "agenda" else recent):
                continue
            counts["checked"] += 1
        try:
            doc, content = download(client, url, settings, stamp)
        except FetchError as e:
            errors.append(str(e))
            continue
        if latest and latest["sha256"] == doc["sha256"]:
            continue
        storage.put(field, doc["file"], content)
        if latest:
            record_change(meeting, kind, latest["id"], doc["id"], stamp)
            counts["changed" if latest["source_url"] == url else "added"] += 1
        else:
            counts["added"] += 1
        meeting.setdefault(field, []).append(doc)


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    settings = config["finalsite_meetings"]
    since, bodies = settings["since"], settings["bodies"]
    recent = (now.date() - timedelta(days=settings.get("recheck_days", 60))).isoformat()
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)

    page = client.get(settings["page_url"]).text
    posts = finalsite.parse_board(page)
    if not posts:
        # The page always lists the current year's meetings: none means its layout changed.
        raise FetchError(f"{settings['page_url']}: no posts found")

    created, unrecognized, listed, to_read = 0, [], set(), []
    for post in posts:
        info = finalsite.parse_title(post["title"], bodies)
        if info is None:
            unrecognized.append(post["title"])
            continue
        if info["date"] < since:
            continue
        meeting = store.get(f"{finalsite.SOURCE}-{post['post_id']}")
        new = meeting is None
        if new:
            meeting = adopt_scheduled(store, post, info)
            if meeting is None:
                meeting = new_meeting(store, post, info, settings, stamp)
                created += 1
            meeting["source_url"] = settings["page_url"]
        merge(meeting, {"date": info["date"], "body": info["body"], "title": title_for(info["body"], info["special"]),
                        "posted_title": post["title"], "status": info["status"], "special": info["special"],
                        "listed": True}, stamp, track=True)
        meeting["last_seen"] = stamp
        listed.add(meeting["id"])
        if new or (meeting["date"] >= recent and meeting["status"] == "scheduled" and not meeting.get("minutes_url")):
            to_read.append((post, meeting))

    # Newest first: the first run of a long year reads the rest on later runs.
    to_read.sort(key=lambda pm: pm[1]["date"], reverse=True)
    batch = to_read[:settings.get("max_posts_per_run", 40)]
    errors, counts = [], {"added": 0, "changed": 0, "checked": 0}
    for post, meeting in batch:
        try:
            fragment = client.get(finalsite.post_url(settings["page_url"], post["element"], post["post_id"])).text
        except FetchError as e:
            errors.append(str(e))
            continue
        merge(meeting, finalsite.meeting_fields(finalsite.parse_post(fragment, settings["page_url"])), stamp, track=True)
        meeting["checked_at"] = stamp

    scheduled = (update_schedule(store, client.get(settings["schedule_url"]).text, settings, today, stamp)
                 if settings.get("schedule_url") else 0)

    for meeting in sorted(store.values(), key=lambda m: (m["date"], m["id"])):
        if meeting.get("source") != finalsite.SOURCE or meeting["date"] < since or meeting["id"].startswith("schedule-"):
            continue
        # An upcoming meeting whose post is gone was most likely called off or moved.
        if meeting["id"] not in listed and meeting["date"] >= today and meeting.get("listed", True):
            merge(meeting, {"listed": False}, stamp, track=True)
        update_documents(client, meeting, storage, settings, today, recent, stamp, counts, errors)

    save_json(meetings_dir(data_dir) / "meetings.json", store)
    status_path = meetings_dir(data_dir) / "finalsite_status.json"
    reported = json.loads(status_path.read_text(encoding="utf-8")).get("unrecognized_titles", []) if status_path.exists() else []
    status = {
        "updated_at": stamp,
        "posts_listed": len(posts),
        "posts_read": len(batch),
        "posts_waiting": len(to_read) - len(batch),
        "meetings_created": created,
        "scheduled_meetings_added": scheduled,
        "documents_added": counts["added"],
        "documents_changed": counts["changed"],
        "documents_rechecked": counts["checked"],
        "requests": getattr(client, "request_count", None),
        # Titles without a date or a listed board: add the board to [finalsite_meetings.bodies] if it's one.
        "unrecognized_titles": sorted(unrecognized),
        "new_unrecognized_titles": sorted(set(unrecognized) - set(reported)),
        "more_pages": finalsite.more_pages(page),
        "errors": errors,
    }
    save_json(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "finalsite_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=config["finalsite_meetings"].get("request_delay", 3.0))
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::The board's Finalsite page could not be read: {e}")
        return 1
    print(json.dumps(status, indent=2))
    for title in status["new_unrecognized_titles"]:
        print(f"::warning::No listed board or no date in the post title {title!r}")
    if status["more_pages"]:
        print("::warning::The board's page lists its posts on more than one page; only the first is read.")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
