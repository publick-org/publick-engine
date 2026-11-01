"""Parser for an iCalendar feed (.ics), as a school district's website publishes its calendar.

Beverly Public Schools' website (on Edlio) publishes its district calendar at
/apps/events/ical/?id=0, school holidays and sports alongside the School
Committee's meetings: "Regular School Committee Meeting-BMS Library",
"Committee of the Whole Meeting", "Policy Subcommittee Meeting-BMS Library".
Each event has an id (UID), a start (DTSTART, local time or a whole day), a
title (SUMMARY) and sometimes a place (LOCATION) and a page (URL). Lines are
folded at 75 characters and commas escaped, as the standard has it.

A meeting's id is its event's UID: the number a numbered UID starts with
(Beverly's "48810607@www.beverlyschools.org#20261125"), or the whole UID. A
feed that gives its events a new UID each time it's read (Westbrook School
Department's, on Apptegy) sets [ical_meetings] uid_changes, and its meetings'
ids are their dates and boards (dated_id).
"""

from __future__ import annotations

import re
from datetime import datetime

from pipeline.meeting_names import find_board, parse_name

SOURCE = "ical"


def unescape(value: str) -> str:
    return re.sub(r"\\([\\,;nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value).strip()


def parse_events(content: str) -> list[dict]:
    """Every event: its uid, start date and time, title, place and page."""
    text = re.sub(r"\r?\n[ \t]", "", content)
    events = []
    for block in re.findall(r"BEGIN:VEVENT\r?\n(.*?)END:VEVENT", text, re.S):
        fields = {}
        for line in block.splitlines():
            name, _, value = line.partition(":")
            fields.setdefault(name.split(";")[0].upper(), unescape(value))
        start = re.fullmatch(r"(\d{8})(?:T(\d{2})(\d{2})\d{2}Z?)?", fields.get("DTSTART", ""))
        if not start or not fields.get("UID"):
            continue
        day = datetime.strptime(start.group(1), "%Y%m%d").date().isoformat()
        events.append({
            "uid": fields["UID"], "date": day, "start_time": f"{start.group(2)}:{start.group(3)}" if start.group(2) else None,
            "title": fields.get("SUMMARY", ""), "location": fields.get("LOCATION", ""), "url": fields.get("URL") or None,
            "cancelled": fields.get("STATUS", "").upper() == "CANCELLED",
        })
    return events


# A UID that's a number, alone or with more numbers, a host or a date after it: Beverly's (Edlio)
# "48810607@www.beverlyschools.org#20261125", Somerville's "934-1793646000-1793646000@www.somerville.k12.ma.us".
NUMBERED = re.compile(r"(\d+)(?:-\d+)*(?:[@#]|$)")


def meeting_id(uid: str) -> str:
    """A meeting's id from its event's UID: the UID's number, for a UID NUMBERED, or else the whole
    UID (Westbrook's Apptegy feed: "2319c3cf-cdd4-48cd-89f7-e7d35837163c"), so two events whose UIDs
    merely start with the same digits are two meetings."""
    number = NUMBERED.match(uid)
    return f"{SOURCE}-{number.group(1) if number else uid}"


def dated_id(date: str, body: str, start_time: str | None = None) -> str:
    """A meeting's id from its date and board (and its time, for a board's second meeting that day),
    for a feed that gives its events a new UID each time it's read ([ical_meetings] uid_changes):
    Westbrook School Department's (Apptegy) gives its "Policy Committee Meeting" of January 27, 2027
    a new random UUID with every request."""
    slug = "-".join(re.findall(r"[a-z0-9]+", body.lower()))
    return f"{SOURCE}-{date}-{slug}" + (f"-{start_time.replace(':', '')}" if start_time else "")


DATED = re.compile(rf"{SOURCE}-\d{{4}}-\d{{2}}-\d{{2}}-")


def to_meeting(event: dict, bodies: dict) -> dict | None:
    """An event as a meeting record for pipeline.fetch_meetings, if its title names one of `bodies`
    (names in titles mapped to the site's boards, the longest found winning), or None."""
    body = find_board(event["title"], [], bodies)
    if not body:
        return None
    parsed = parse_name(event["title"], [], bodies)
    return {
        "id": meeting_id(event["uid"]),
        "source": SOURCE,
        "source_url": event["url"],
        "date": event["date"],
        "start_time": event["start_time"],
        "end_time": None,
        "body": body,
        "title": f"{body} {'Special Meeting' if parsed['special'] else 'Meeting'}",
        "posted_title": event["title"],
        "location_name": event["location"],
        "status": "cancelled" if event["cancelled"] else parsed["status"],
        "special": parsed["special"],
    }


def check(config: dict) -> None:
    """A town's [ical_meetings] table, checked when its config is loaded."""
    settings = config.get("ical_meetings")
    if settings is None:
        return
    where = f"[ical_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("ical_url", "page_url", "source_name", "since", "bodies") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the calendar feed, the page it's on, who keeps it, "
                         "the first meeting date to collect, and the boards its event titles name.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-07-01".')
    if not isinstance(settings.get("uid_changes", False), bool):
        raise SystemExit(f"{where}: uid_changes must be true or false.")
    if not isinstance(settings["bodies"], dict):
        raise SystemExit(f'{where}: bodies must map names in event titles to board names, like '
                         '"Committee of the Whole" = "School Committee of the Whole".')
