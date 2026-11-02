"""The site's meetings as a calendar file (/meetings/calendar.ics), for a reader to subscribe to.

A calendar app (Google Calendar, Apple Calendar, Outlook) subscribed to the
file's address reads it again every few hours, so a meeting added, moved, or
cancelled on the site reaches the reader's calendar without their doing
anything. Each language has its own file: the Spanish site's is at
/es/meetings/calendar.ics, with the boards' Spanish names and links to the
Spanish pages.

Each event is one meeting the site lists, from DAYS_BACK days ago on: its
board as the title, its place where the listing gives one, and its page on
the site as its link. A meeting with a start time is placed at that time in
the town's time zone (written in UTC, so the file needs no time zone
definitions); one without is a whole-day event. A meeting's end is given
only where its listing gives one. A cancelled or postponed meeting stays, marked
cancelled (STATUS:CANCELLED), so a calendar that already had it shows it as
called off rather than dropping it unexplained. Each event's id (UID) is the
meeting's page and the site's domain, so it stays the same from one build to
the next and an app updates the event rather than adding it again.

The file follows RFC 5545: lines end in CRLF, text has its commas,
semicolons, and backslashes escaped, and lines longer than 75 octets are
folded.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.i18n import _, pgettext

# How far back the file lists meetings: enough for the past few weeks to stay on a calendar.
DAYS_BACK = 60
# How often a calendar app is asked to read the file again (the sites are built a few times a day).
REFRESH = "PT6H"
# A meeting's status that means it won't take place on its date.
CALLED_OFF = ("cancelled", "postponed")


def escape_text(value: str) -> str:
    """Text as an iCalendar TEXT value: backslashes, semicolons, and commas escaped, newlines as \\n."""
    return (value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line: str) -> str:
    """A content line folded so no line is longer than 75 octets: each continuation starts with a
    space. A multibyte character is never split."""
    out, current, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        # The first line may hold 75 octets; each continuation 74, after its leading space.
        if size + n > (75 if not out else 74):
            out.append(current)
            current, size = "", 0
        current += ch
        size += n
    out.append(current)
    return "\r\n ".join(out)


def utc(day: str, hhmm: str, zone: ZoneInfo) -> str:
    """A local date and HH:MM time in the town's zone, as an iCalendar UTC date-time."""
    local = datetime.combine(date.fromisoformat(day), time.fromisoformat(hhmm), zone)
    return local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def place(m: dict) -> str:
    """Where the meeting is held, as its listing gives it: the place's name and address, or its
    location text."""
    parts = [p for p in (m.get("location_name"), m.get("address")) if p]
    return ", ".join(parts) or (m.get("location") or "")


def event(m: dict, base_url: str, prefix: str, domain: str, zone: ZoneInfo, stamp: str) -> list[str]:
    """One meeting's VEVENT, as its content lines (not yet folded)."""
    url = base_url + prefix + m["url"]
    lang = prefix.strip("/") or "en"
    slug = m["url"].strip("/").split("/")[-1]
    title = m["body"]
    status = m.get("status")
    if status in CALLED_OFF:
        # The board's name, then why the meeting is off, as the site's own lists say it.
        why = pgettext("meeting", "Cancelled") if status == "cancelled" else pgettext("meeting", "Postponed")
        title = f"{title} ({why})"
    lines = ["BEGIN:VEVENT", f"UID:{slug}.{lang}@{domain}", f"DTSTAMP:{stamp}"]
    if m.get("start_time"):
        lines.append(f"DTSTART:{utc(m['date'], m['start_time'], zone)}")
        if m.get("end_time") and m["end_time"] > m["start_time"]:
            lines.append(f"DTEND:{utc(m['date'], m['end_time'], zone)}")
    else:
        day = date.fromisoformat(m["date"])
        lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
    lines.append(f"SUMMARY:{escape_text(title)}")
    where = place(m)
    if where:
        lines.append(f"LOCATION:{escape_text(where)}")
    lines += [f"URL:{url}", f"DESCRIPTION:{escape_text(url)}"]
    lines.append("STATUS:CANCELLED" if status in CALLED_OFF else "STATUS:CONFIRMED")
    lines.append("END:VEVENT")
    return lines


def write(path: Path, meetings: list[dict], config: dict, base_url: str, built_at: datetime, prefix: str = "") -> None:
    """The calendar file of the site's meetings (one language's: prefix "" for English, "/es" for
    Spanish), from DAYS_BACK days before the build on."""
    site = config["site"]
    zone = ZoneInfo(site["timezone"])
    since = (built_at.date() - timedelta(days=DAYS_BACK)).isoformat()
    stamp = built_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Wording to translate stays out of f-strings: only Python 3.12 and later find it there.
    name = site["name"] + ": " + _("Meetings")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//Publick//{site['domain']}//{(prefix.strip('/') or 'en').upper()}",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{escape_text(name)}", f"X-WR-TIMEZONE:{site['timezone']}",
             f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}", f"X-PUBLISHED-TTL:{REFRESH}"]
    for m in meetings:
        if m["date"] >= since:
            lines += event(m, base_url, prefix, site["domain"], zone, stamp)
    lines.append("END:VCALENDAR")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\r\n".join(fold(line) for line in lines) + "\r\n").encode("utf-8"))
