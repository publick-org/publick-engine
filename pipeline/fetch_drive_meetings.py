"""Collect agendas and minutes posted in public Google Drive folders.

Some public bodies post their agendas and minutes in Google Drive instead of on
the city website. In Gloucester that is the School Committee and its
subcommittees: the school district keeps one Drive folder of agendas and one of
minutes, each holding a folder per committee. The current school year's files
sit loose in each committee's folder, and earlier years are in school-year
folders ("2025-2026 School Year").

The meeting date and any variation come from the file name, such as
"SC Agenda 9_9_26.pdf", "Special SC Agenda 7_20_26", "SC Agenda REVISED 6_10_26"
or "Personnel Agenda (Food Service Workers) 3_16_26.pdf". Documents are saved
alongside the city's under data/meetings/ and attached to the meeting on the
city calendar when there is one; otherwise the meeting is recorded from its
documents. Executive session minutes and joint meetings with other boards
are left out; their names are listed in the run's summary.

Agendas are often posted only days before a meeting, so upcoming meetings
come from the district's published meeting schedule ("School Committee -
October 14th and 28th, 2026; ..."), for the next few weeks. Documents attach to
those meetings when they are posted. A scheduled meeting that drops off the
schedule before it happens is marked as no longer listed, not deleted.

Other districts keep a folder per meeting instead (Lewiston's School
Committee, with `meetings_folder`): one folder a school year ("2026-2027 School
Committee"), and in it one folder a meeting, named by its date ("04 10-5-26",
"01 September 8, 2025"). The meeting's agenda is in its folder ("00 10-5-26
Amended Agenda.pdf"), and its minutes come later, in the packet of the meeting
that approves them ("03a 9-21-26 Minutes.pdf"), so each set of minutes is
attached by the date in its own name. A folder named "CANCELED MEETING 3/23/26"
cancels that day's meeting; one named "... NOTICE" holds a notice, not a
meeting, and is left out. The time comes from the agenda ("Call meeting to
order on or about 5:30 pm") when its text says so.

Usage:
    python -m pipeline.fetch_drive_meetings [--town gloucester]
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

from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.fetch_meetings import load_store, meetings_dir, pdf_has_text, record_change, save_json, slugify, unique_slug
from pipeline.http import FetchError, PoliteClient
from pipeline.pdftext import plain_text

FOLDER_URL = "https://drive.google.com/embeddedfolderview?id={folder}"
FILE_URL = "https://drive.google.com/uc?export=download&id={id}"
VIEW_URL = "https://drive.google.com/file/d/{id}/view"
ENTRY = re.compile(r'<div class="flip-entry" id="entry-([\w-]+)".*?<a href="https://drive\.google\.com/([a-z]+)/'
                   r'.*?<div class="flip-entry-title">(.*?)</div>', re.S)
# "9_9_26", "10_08_25", "6_17_2026", at the end of the name.
DATE_IN_NAME = re.compile(r"(\d{1,2})_(\d{1,2})_(\d{4}|\d{2})\s*(?:\.pdf)?\s*$", re.I)
SCHOOL_YEAR = re.compile(r"\b(\d{4})-(\d{4})\b")
# Words in a file name that describe the document, not which meeting it is.
DOCUMENT_WORDS = re.compile(r"\b(?:agenda|minutes|revised|amended|special|online|sub-?committee)\b", re.I)
LEFT_OUT = re.compile(r"\b(?:ES|executive session|joint)\b", re.I)
MONTHS = {name: n for n, name in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                              "september", "october", "november", "december"], 1)}
# "School Committee - " or "Building and Finance Subcommittee - " starts a committee's dates.
SCHEDULE_ENTRY = re.compile(r"([A-Z][A-Za-z& ]*?(?:Committee|Subcommittee)) ?- ")
# A folder a meeting: "10-5-26", "8-24-2026", "3/23/26", or "September 8, 2025", anywhere in the name.
NUMERIC_DATE = re.compile(r"(?<![\d.])(\d{1,2})[-/](\d{1,2})[-/](\d{4}|\d{2})(?![\d/-])")
LONG_DATE = re.compile(r"\b([A-Za-z]+)\.? (\d{1,2}),? (\d{4})\b")
CANCELLED = re.compile(r"\bcancell?ed\b", re.I)
NOTICE = re.compile(r"\bnotice\b", re.I)
AGENDA = re.compile(r"\bagenda\b", re.I)
# "03b 10-5-26 Personnel Agenda Cover.pdf" is the cover of a confidential item, not the agenda.
NOT_THE_AGENDA = re.compile(r"\b(?:personnel|cover|executive session)\b", re.I)
MINUTES = re.compile(r"\bminutes\b", re.I)
# "Call meeting to order on or about 5:30 pm", "called to order at 6:00 p.m."
CALL_TO_ORDER = re.compile(r"\border\b\W+(?:\w+\W+){0,4}?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b", re.I)
# A packet's place in the folder ("00 ", "03a "), left out when telling copies of one document apart.
PACKET_NUMBER = re.compile(r"^\d+[a-z]?\s+", re.I)


def list_folder(page: str) -> list[dict]:
    """Entries of a public Drive folder page: files and subfolders."""
    return [{"id": fid, "folder": kind == "drive", "name": html.unescape(name).strip()}
            for fid, kind, name in ENTRY.findall(page)]


def parse_name(name: str, abbreviations: list[str]) -> dict | None:
    """Meeting date and details from a file name, or None if it has no date.

    'SC Governance Workshop Agenda 4_8_26.pdf' -> 2026-04-08, variant
    'Governance Workshop'. abbreviations are the committees' short names in
    file names ('SC', 'B&F'), removed before the variant is read."""
    text = re.sub(r"\s+", " ", name).strip()
    found = DATE_IN_NAME.search(text)
    if not found:
        return None
    month, day, year = (int(n) for n in found.groups())
    try:
        when = date(year + 2000 if year < 100 else year, month, day)
    except ValueError:
        return None
    words = text[:found.start()]
    rest = DOCUMENT_WORDS.sub(" ", words)
    for short in sorted(abbreviations, key=len, reverse=True):
        rest = re.sub(rf"(?<!\w){re.escape(short)}(?!\w)", " ", rest, flags=re.I)
    return {
        "date": when.isoformat(),
        "revised": bool(re.search(r"\b(?:revised|amended)\b", words, re.I)),
        "special": bool(re.search(r"\bspecial\b", words, re.I)),
        "variant": re.sub(r"\s+", " ", rest).strip(" -()"),
        "left_out": bool(LEFT_OUT.search(words)),
    }


def find_date(text: str) -> str | None:
    """The first date in a folder or file name: '04 10-5-26', '01 September 8, 2025', 'CANCELED MEETING 3/23/26'."""
    if found := NUMERIC_DATE.search(text):
        month, day, year = (int(n) for n in found.groups())
    elif (found := LONG_DATE.search(text)) and found.group(1).lower() in MONTHS:
        month, day, year = MONTHS[found.group(1).lower()], int(found.group(2)), int(found.group(3))
    else:
        return None
    try:
        return date(year + 2000 if year < 100 else year, month, day).isoformat()
    except ValueError:
        return None


def start_time(text: str) -> str | None:
    """The time an agenda calls the meeting to order, as "17:30", or None if it doesn't say."""
    found = CALL_TO_ORDER.search(text)
    if not found:
        return None
    hour, minute = int(found.group(1)), int(found.group(2) or 0)
    if not (1 <= hour <= 12 and minute < 60):
        return None
    hour = hour % 12 + (12 if found.group(3).lower() == "p" else 0)
    return f"{hour:02d}:{minute:02d}"


def folder_url(folder_id: str, resource_key: str | None = None) -> str:
    """A public folder's page; an older shared folder also needs its resource key."""
    return FOLDER_URL.format(folder=folder_id) + (f"&resourcekey={resource_key}" if resource_key else "")


def meeting_folders(client, settings: dict, since: str) -> dict:
    """What a folder-a-meeting layout holds from `since` on: each meeting's date and whether it was
    cancelled, its agendas, and every set of minutes by the date in its name. A copy of the same
    minutes in a later meeting's packet is the same document: the latest copy is kept."""
    root = list_folder(client.get(folder_url(settings["meetings_folder"], settings.get("resource_key"))).text)
    meetings, agendas, minutes, left_out, unreadable = {}, [], {}, [], []
    for year in root:
        found = SCHOOL_YEAR.search(year["name"])
        if not year["folder"] or not found or int(found.group(2)) < int(since[:4]):
            continue
        for folder in list_folder(client.get(folder_url(year["id"])).text):
            if not folder["folder"]:
                continue
            day = find_date(folder["name"])
            if day is None:
                unreadable.append(folder["name"])
                continue
            if day < since:
                continue
            if NOTICE.search(folder["name"]):
                left_out.append(folder["name"])
                continue
            cancelled = bool(CANCELLED.search(folder["name"]))
            meetings[day] = meetings.get(day, False) or cancelled
            if cancelled:
                continue
            for file in list_folder(client.get(folder_url(folder["id"])).text):
                if file["folder"]:
                    continue
                if LEFT_OUT.search(file["name"]):
                    left_out.append(file["name"])
                elif MINUTES.search(file["name"]):
                    if (when := find_date(file["name"])) is None:
                        unreadable.append(file["name"])
                    elif when >= since:
                        minutes[(when, PACKET_NUMBER.sub("", file["name"]).lower())] = (when, file)
                elif AGENDA.search(file["name"]) and not NOT_THE_AGENDA.search(file["name"]):
                    agendas.append((day, file))
    info = lambda day, name: {"date": day, "revised": bool(re.search(r"\b(?:revised|amended)\b", name, re.I)),
                              "special": bool(re.search(r"\bspecial\b", name, re.I)), "variant": "", "left_out": False}
    return {
        "meetings": meetings,
        "documents": [("agendas", f, info(d, f["name"])) for d, f in agendas]
                     + [("minutes", f, info(d, f["name"])) for d, f in minutes.values()],
        "left_out": left_out,
        "unreadable": unreadable,
    }


def committee_files(client, folder_id: str, since: str) -> list[dict]:
    """Files in a committee's folder, and in its school-year folders from the
    year that includes `since` on."""
    files = []
    for entry in list_folder(client.get(FOLDER_URL.format(folder=folder_id)).text):
        if not entry["folder"]:
            files.append(entry)
            continue
        year = SCHOOL_YEAR.search(entry["name"])
        if year and int(year.group(2)) >= int(since[:4]):
            files += [e for e in list_folder(client.get(FOLDER_URL.format(folder=entry["id"])).text) if not e["folder"]]
    return files


def body_for(folder_name: str, bodies: dict) -> str | None:
    """'Program Subcommittee Agendas' -> the configured body for 'Program Subcommittee'.
    'and' and '&' are the same ('Building and Finance Subcommittee')."""
    return next((body for prefix, body in bodies.items() if slugify(folder_name).startswith(slugify(prefix))), None)


def parse_schedule(page: str) -> list[tuple[str, str]]:
    """(committee, date) pairs from the district's meeting schedule page:
    'School Committee - September 9th and 23rd, 2026; October 14th and 28th, 2026'.
    The page's formatting splits numbers and their endings ('2 3rd', '202 6',
    '9t h'), so those are rejoined first."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    text = re.sub(r"(?<=\d) (?=\d)", "", text)
    text = re.sub(r"(\d) ?(?:s ?t|n ?d|r ?d|t ?h)\b", r"\1", text)
    entries = list(SCHEDULE_ENTRY.finditer(text))
    found = []
    for i, entry in enumerate(entries):
        # A committee's dates run to the next committee, or to the page footer ("... | Phone").
        end = entries[i + 1].start() if i + 1 < len(entries) else len(text)
        month, pending = None, []
        for token in re.findall(r"[A-Za-z]+|\d+", text[entry.end():end].split("|")[0]):
            if token.lower() in MONTHS:
                month = MONTHS[token.lower()]
            elif token.isdigit() and len(token) == 4:
                for m, d in pending:
                    try:
                        found.append((entry.group(1).strip(), date(int(token), m, d).isoformat()))
                    except ValueError:
                        pass
                pending = []
            elif token.isdigit() and month:
                pending.append((month, int(token)))
    return found


def update_schedule(store: dict, page: str, settings: dict, today: date, stamp: str) -> int:
    """Record scheduled meetings from `since` to a few weeks ahead. Returns how many are new."""
    ahead = (today + timedelta(days=settings.get("schedule_days_ahead", 45))).isoformat()
    scheduled = {(body, day) for name, day in parse_schedule(page)
                 if (body := body_for(name, settings["bodies"])) and settings["since"] <= day <= ahead}
    added = 0
    for body, day in sorted(scheduled):
        meeting = find_meeting(store, body, day, "")
        if meeting is None:
            new_meeting(store, f"schedule-{slugify(body)}-{day}", body, {"date": day, "variant": "", "special": False},
                        settings, stamp, source_url=settings["schedule_url"])
            added += 1
        elif not meeting.get("listed", True) and meeting["id"].startswith("schedule-"):
            record_change(meeting, "listed", False, True, stamp)
            meeting["listed"] = True
    # An upcoming meeting known only from the schedule that has left it was probably moved or cancelled.
    if scheduled:
        for m in store.values():
            if (m["id"].startswith("schedule-") and m.get("listed", True) and m["date"] >= today.isoformat()
                    and (m["body"], m["date"]) not in scheduled and not m.get("agendas") and not m.get("minutes")):
                record_change(m, "listed", True, False, stamp)
                m["listed"] = False
    return added


def find_meeting(store: dict, body: str, day: str, variant: str) -> dict | None:
    return next((m for m in store.values()
                 if m["date"] == day and slugify(m["body"]) == slugify(body) and m.get("variant", "") == variant), None)


def new_meeting(store: dict, meeting_id: str, body: str, info: dict, settings: dict, stamp: str,
                source_url: str | None = None) -> dict:
    title = f"{body}: {info['variant']}" if info["variant"] else body
    meeting = {
        "id": meeting_id,
        "source": "drive",
        "source_url": source_url or settings["page_url"],
        "source_name": settings["source_name"],
        "first_seen": stamp,
        "last_seen": stamp,
        "slug": unique_slug(f"{info['date']}-{slugify(title)}", store),
        "date": info["date"],
        "body": body,
        "title": title,
        "variant": info["variant"],
        "status": "scheduled",
        "special": info["special"],
        "listed": True,
        "start_time": None,
        "end_time": None,
        "location": "",
    }
    store[meeting["id"]] = meeting
    return meeting


def save_document(client, file: dict, storage, folder: str, settings: dict, stamp: str) -> tuple[dict, bytes]:
    content = client.get(FILE_URL.format(id=file["id"])).content
    if not content.startswith(b"%PDF"):
        raise FetchError(f"{file['name']}: not a PDF")
    storage.put(folder, f"{file['id']}.pdf", content)
    return content, {
        "id": file["id"],
        "title": file["name"],
        "source_url": VIEW_URL.format(id=file["id"]),
        "posted_by": settings["source_name"],
        "posted_on": settings["posted_on"],
        "file": f"{file['id']}.pdf",
        "original_filename": file["name"],
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "has_text": pdf_has_text(content),
        "fetched_at": stamp,
    }


def run(config: dict, client, data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    stamp = now.isoformat(timespec="seconds")
    settings = config["drive_meetings"]
    since = settings["since"]
    store = load_store(data_dir)
    storage = open_documents(config, data_dir)
    known = {doc["id"] for m in store.values() for field in ("agendas", "minutes") for doc in m.get(field, [])}
    scheduled = (update_schedule(store, client.get(settings["schedule_url"]).text, settings, now.date(), stamp)
                 if settings.get("schedule_url") else 0)

    added, created, left_out, unreadable, errors = 0, 0, [], [], []
    documents = []
    if settings.get("meetings_folder"):
        body = settings["body"]
        layout = meeting_folders(client, settings, since)
        found, left_out, unreadable = layout["documents"], layout["left_out"], layout["unreadable"]
        # The same minutes copied into another packet: already attached under another file's id.
        copies = {(m["date"], kind, PACKET_NUMBER.sub("", doc["original_filename"]).lower())
                  for m in store.values() if m["body"] == body for kind in ("agendas", "minutes") for doc in m.get(kind, [])}
        for day, cancelled in sorted(layout["meetings"].items()):
            info = {"date": day, "variant": "", "special": False}
            meeting = find_meeting(store, body, day, "")
            if meeting is None:
                meeting = new_meeting(store, f"drive-{slugify(body)}-{day}", body, info, settings, stamp)
                created += 1
            if cancelled and meeting["status"] != "cancelled":
                record_change(meeting, "status", meeting["status"], "cancelled", stamp)
                meeting["status"] = "cancelled"
        documents = [(kind, body, file, info) for kind, file, info in found if file["id"] not in known
                     and (info["date"], kind, PACKET_NUMBER.sub("", file["name"]).lower()) not in copies]
    else:
        found = []
        for kind, root in (("agendas", settings["agendas_folder"]), ("minutes", settings["minutes_folder"])):
            for committee in list_folder(client.get(FOLDER_URL.format(folder=root)).text):
                body = body_for(committee["name"], settings["bodies"])
                if committee["folder"] and body:
                    found += [(kind, body, f) for f in committee_files(client, committee["id"], since)]
        for kind, body, file in found:
            info = parse_name(file["name"], settings.get("abbreviations", []))
            if info is None:
                unreadable.append(file["name"])
            elif info["left_out"]:
                left_out.append(file["name"])
            elif info["date"] >= since and file["id"] not in known:
                documents.append((kind, body, file, info))
    # Originals before revisions, so a meeting's latest version is its last.
    for kind, body, file, info in sorted(documents, key=lambda d: (d[3]["date"], d[3]["revised"], d[2]["name"])):
        try:
            content, doc = save_document(client, file, storage, kind, settings, stamp)
        except FetchError as e:
            errors.append(str(e))
            continue
        meeting = find_meeting(store, body, info["date"], info["variant"])
        if meeting is None:
            meeting = new_meeting(store, f"drive-{file['id']}", body, info, settings, stamp)
            created += 1
        docs = meeting.setdefault(kind, [])
        if docs:
            record_change(meeting, "agenda" if kind == "agendas" else "minutes", docs[-1]["id"], doc["id"], stamp)
        docs.append(doc)
        # The agenda says when the meeting starts, where no listing does.
        if kind == "agendas" and not meeting.get("start_time") and doc["has_text"]:
            if when := start_time(plain_text(content)):
                meeting["start_time"] = when
        known.add(file["id"])
        added += 1

    save_json(meetings_dir(data_dir) / "meetings.json", store)
    status_path = meetings_dir(data_dir) / "drive_status.json"
    reported = json.loads(status_path.read_text(encoding="utf-8")).get("unreadable_names", []) if status_path.exists() else []
    status = {
        "updated_at": stamp,
        "files_listed": len(found),
        "documents_added": added,
        "meetings_created": created,
        "scheduled_meetings_added": scheduled,
        "left_out": sorted(left_out),
        # File names without a readable date ("B & F Agenda 6_17_2.pdf"): check these by hand.
        "unreadable_names": sorted(unreadable),
        "new_unreadable_names": sorted(set(unreadable) - set(reported)),
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
    if not configured(config, "drive_meetings"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=60)
    try:
        status = run(config, client, args.data)
    except FetchError as e:
        print(f"::error::Google Drive folders could not be read: {e}")
        return 1
    print(json.dumps(status, indent=2))
    for name in status["new_unreadable_names"]:
        print(f"::warning::No meeting date in the file name {name!r}")
    for error in status["errors"]:
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
