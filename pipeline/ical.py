"""Parser for an iCalendar feed (.ics), as a school district's website publishes its calendar.

Beverly Public Schools' website (on Edlio) publishes its district calendar at
/apps/events/ical/?id=0, school holidays and sports alongside the School
Committee's meetings: "Regular School Committee Meeting-BMS Library",
"Committee of the Whole Meeting", "Policy Subcommittee Meeting-BMS Library".
Each event has an id (UID), a start (DTSTART, local time or a whole day), a
title (SUMMARY) and sometimes a place (LOCATION) and a page (URL). Lines are
folded at 75 characters and commas escaped, as the standard has it.

A Google Calendar feed (Winooski School District's) has a board's regular
meetings as one repeating event: a start, a rule (RRULE,
"FREQ=MONTHLY;BYDAY=2WE;UNTIL=..."), the dates left out (EXDATE), and an event
of its own for each meeting changed from the rule (RECURRENCE-ID, the date it
replaces). Each repeating event is read as its meetings, up to `until`: one a
date the rule gives, less those left out, with each changed meeting in place
of the one it replaces. Each has the event's UID with the date it has under the
rule ("...@google.com#20261014"), so a meeting keeps its id when it's changed
or moved. A time in UTC ("20260616T140000Z") is the town's local time; a time
with a TZID is that zone's, a time with neither is local already.
A meeting's id is its event's UID: the number a numbered UID starts with
(Beverly's "48810607@www.beverlyschools.org#20261125"), or the whole UID. A
feed that gives its events a new UID each time it's read (Westbrook School
Department's, on Apptegy) sets [ical_meetings] uid_changes, and its meetings'
ids are their dates and boards (dated_id).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

from pipeline.meeting_names import find_board, parse_name

SOURCE = "ical"
# How far a repeating event with no end is read when the caller gives no `until`.
OPEN_ENDED_YEARS = 2
# A Google Calendar event's UID, its "_R<date>" naming a series split from an earlier one
# ("1o4b4qef9ngoopgpl46l5qghmc_R20260311T220000@google.com"), and an occurrence's "#<date>".
GOOGLE_UID = re.compile(r"([\w-]+?)(?:_R\d{8}T\d{6})?@google\.com(?:#(\d{8}))?")


def unescape(value: str) -> str:
    return re.sub(r"\\([\\,;nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value).strip()


def properties(block: str) -> list[tuple[str, dict, str]]:
    """Each line of an event: its name, its parameters ("TZID", "VALUE"), and its raw value."""
    found = []
    for line in block.splitlines():
        head, _, value = line.partition(":")
        name, *params = head.split(";")
        found.append((name.upper(), dict(p.partition("=")[::2] for p in params), value.strip()))
    return found


def when(value: str, params: dict) -> datetime | date | None:
    """A DTSTART, RECURRENCE-ID or EXDATE value: a date for a whole day; a time aware of its zone when it's
    UTC ("Z") or names a zone (TZID) Python knows; otherwise a local time, naive."""
    found = re.fullmatch(r"(\d{8})(?:T(\d{6})(Z?))?", value)
    if not found:
        return None
    if not found.group(2):
        return datetime.strptime(found.group(1), "%Y%m%d").date()
    moment = datetime.strptime(found.group(1) + found.group(2), "%Y%m%d%H%M%S")
    if found.group(3):
        return moment.replace(tzinfo=timezone.utc)
    try:
        return moment.replace(tzinfo=ZoneInfo(params["TZID"])) if params.get("TZID") else moment
    except (ZoneInfoNotFoundError, ValueError):
        return moment


def instant(moment: datetime | date) -> datetime | date:
    """A value to compare an occurrence with an EXDATE or a RECURRENCE-ID, whatever zone each is written in."""
    return moment.astimezone(timezone.utc) if isinstance(moment, datetime) and moment.tzinfo else moment


def local(moment: datetime | date, tz: tzinfo | None) -> tuple[str, str | None]:
    """(date, "HH:MM" or None) in the town's time zone."""
    if not isinstance(moment, datetime):
        return moment.isoformat(), None
    if moment.tzinfo and tz:
        moment = moment.astimezone(tz)
    return moment.date().isoformat(), moment.strftime("%H:%M")


def occurrences(rule: str, start: datetime | date, until: date) -> list[datetime | date]:
    """The starts a repeating event's RRULE gives, from its first to `until` (inclusive), in its own zone."""
    if isinstance(start, datetime):
        first = start
        if not start.tzinfo:
            rule = re.sub(r"(UNTIL=\d{8}T\d{6})Z", r"\1", rule)   # a local start's end is local too
    else:
        first = datetime.combine(start, datetime.min.time())
        rule = re.sub(r"(UNTIL=\d{8})T\d{6}Z?", r"\1", rule)
    last = datetime.combine(until, datetime.max.time()).replace(tzinfo=first.tzinfo)
    found = rrulestr(rule, dtstart=first).between(first, last, inc=True)
    return found if isinstance(start, datetime) else [d.date() for d in found]


def parse_events(content: str, until: date | None = None, tz: tzinfo | None = None) -> list[dict]:
    """Every event: its uid, start date and time, title, place and page, and whether it's cancelled. A
    repeating event is each of its meetings to `until` (by default OPEN_ENDED_YEARS after its first, where
    its rule doesn't end), each marked `recurring`. Times in UTC are given in `tz`."""
    text = re.sub(r"\r?\n[ \t]", "", content)
    events, rules, changed = [], [], {}
    for block in re.findall(r"BEGIN:VEVENT\r?\n(.*?)END:VEVENT", text, re.S):
        props = properties(block)
        fields, params = {}, {}
        for name, param, value in props:
            fields.setdefault(name, unescape(value))
            params.setdefault(name, param)
        start = when(fields.get("DTSTART", ""), params.get("DTSTART", {}))
        if start is None or not fields.get("UID"):
            continue
        day, time = local(start, tz)
        event = {
            "uid": fields["UID"], "date": day, "start_time": time,
            "title": fields.get("SUMMARY", ""), "location": fields.get("LOCATION", ""), "url": fields.get("URL") or None,
            "cancelled": fields.get("STATUS", "").upper() == "CANCELLED",
        }
        replaces = when(fields.get("RECURRENCE-ID", ""), params.get("RECURRENCE-ID", {}))
        if replaces is not None:
            # One meeting of a repeating event, changed: it takes the place of the one on its old date.
            changed[(fields["UID"], instant(replaces))] = {
                **event, "uid": f"{fields['UID']}#{local(replaces, tz)[0].replace('-', '')}", "recurring": True}
        elif fields.get("RRULE"):
            left_out = {instant(w) for name, param, value in props if name == "EXDATE"
                        for v in value.split(",") if (w := when(v, param)) is not None}
            rules.append((event, fields["RRULE"], start, left_out))
        else:
            events.append(event)
    for event, rule, start, left_out in rules:
        first = start.date() if isinstance(start, datetime) else start
        last = until or first + timedelta(days=366 * OPEN_ENDED_YEARS)
        try:
            starts = occurrences(rule, start, last)
        except (ValueError, TypeError):
            starts = [start]   # a rule that can't be read: its first meeting only
        for moment in starts:
            if instant(moment) in left_out or (event["uid"], instant(moment)) in changed:
                continue
            day, time = local(moment, tz)
            events.append({**event, "uid": f"{event['uid']}#{day.replace('-', '')}", "date": day, "start_time": time,
                           "recurring": True})
    events += changed.values()
    return events


# A UID that's a number, alone or with more numbers, a host or a date after it: Beverly's (Edlio)
# "48810607@www.beverlyschools.org#20261125", Somerville's "934-1793646000-1793646000@www.somerville.k12.ma.us".
NUMBERED = re.compile(r"(\d+)(?:-\d+)*(?:[@#]|$)")


def event_id(event: dict) -> str:
    """A meeting's id from its event's UID: its number, for a UID NUMBERED, else the whole UID (Westbrook's
    Apptegy feed: "2319c3cf-cdd4-48cd-89f7-e7d35837163c"), so two events whose UIDs merely start with the same
    digits are two meetings. Edlio's ("54210334@www.beverlyschools.org#20261014") and The Events
    Calendar's ("934-1793646000-1793646000@www.somerville.k12.ma.us") start with the event's number; a Google
    Calendar event's is its id, without the "_R<date>" of a series split from another, with the date of a
    repeating event's meeting ("1o4b4qef9ngoopgpl46l5qghmc-20261014"). Another repeating event's meeting is its
    number, or UID, and its date."""
    uid = event["uid"]
    google = GOOGLE_UID.fullmatch(uid)
    if google:
        return "-".join(g for g in google.groups() if g)
    base = uid.split("#")[0] if event.get("recurring") else uid
    number = NUMBERED.match(base)
    occurrence = re.search(r"#(\d{8})$", uid) if event.get("recurring") else None
    return (number.group(1) if number else base) + (f"-{occurrence.group(1)}" if occurrence else "")


def meeting_id(uid: str) -> str:
    """The id of the meeting a single (not repeating) event with this UID is: event_id's, with the source."""
    return f"{SOURCE}-{event_id({'uid': uid})}"

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
        "id": f"{SOURCE}-{event_id(event)}",
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
