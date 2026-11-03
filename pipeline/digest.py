"""The weekly digest: one page a week for each town's meetings, and a feed of them for email.

Each issue is dated a Sunday and covers two things, from data the site already has (no AI calls):

- the meetings of the week ahead, Monday to Sunday, with their agenda summaries where an agenda
  is posted (most are posted 2 to 4 days before the meeting, so the week's later meetings often
  have none yet);
- the minutes first collected in the week before, Sunday to Saturday, with what each meeting decided.

An issue is built from the first build on or after its Saturday, the day before its date, and shows
what the site knows about that week as of the latest build. A week with no meetings and no new
minutes has no issue. Issues dated before MORNING_FROM were built from their Sunday's run and list
the minutes collected Monday to Sunday; their pages keep those weeks, so no minutes move from one
issue to another.

The issues are published at /digest/<Monday>/ with a list at /digest/ and a feed at
/digest/feed.xml (build_site.py). Each feed item's date is when its email is due: the Sunday at
SEND_TIME, the town's own time, early enough to be read with the morning's news. Built the day
before, it doesn't wait on the Sunday's daily run, which ends at a different time in each town;
that run builds the issue too where Saturday's didn't. Whatever sends the email
(the network's scheduler Worker, worker/digest.js, through Buttondown) sends each item once its
date has passed.

The email's signup form (site/templates/digest_form.html) is shown on /digest/ and the home page, under
the week ahead, and each meeting's and board's page links to it, when the town's config turns it on:

    [digest]
    signup = true
    provider = "Buttondown"                                # who keeps the addresses, named on the form and the About page
    privacy_url = "https://buttondown.com/legal/privacy"   # their privacy policy, linked from the About page

The form posts to the site's own /digest/subscribe, which only the network's Worker answers
(worker/digest.js), so a town turns it on only where that Worker serves it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# When an issue's email is due, on its Sunday, in the town's timezone.
SEND_TIME = time(7, 0)
# The first issue built the Saturday before and sent Sunday morning. Earlier issues were built on their
# Sunday and due at EVENING_SEND_TIME; their feed dates and the weeks of minutes their pages list stay as sent.
MORNING_FROM = date(2026, 11, 8)
EVENING_SEND_TIME = time(17, 30)
# Minutes collected for a meeting longer ago than this are left out: a source's history read
# for the first time, not news. Most minutes are collected 2 to 6 weeks after their meeting (they're
# approved at the next one); 90 days let a town's first weeks list minutes from months before
# (Burlington's "Week of October 12, 2026" had 35, from July on), so 45 since 2026-10-12.
MINUTES_MAX_AGE = timedelta(days=45)
# Meetings whose minutes the email lists in full; the rest are on the issue's page, a link away.
EMAIL_MINUTES = 10
# Issues in the feed, newest first.
FEED_ISSUES = 12
# Links in the email say where the reader came from, for the page counts (site/static/js/count.js).
EMAIL_REF = "digest-email"
# The email's footer, as the law on commercial email asks: a postal address, and a link to unsubscribe.
# The email goes without Buttondown's template around it (worker/digest.js), so nothing else adds them.
# The address is Buttondown's, which it offers its newsletters for this (decided 2026-10-12), as its
# privacy policy gives it (https://buttondown.com/legal/privacy, updated 2026-04-01). Left as
# "[mailing address]", the scheduler refuses to send the email (tests still go).
MAILING_ADDRESS = "c/o Buttondown, LLC, 406 W Franklin St. #201, Richmond, VA 23221"
# Buttondown's tag for each reader's own unsubscribe link, filled in as it sends.
UNSUBSCRIBE_TAG = "{{ unsubscribe_url }}"


def signup(config: dict) -> dict | None:
    """The email signup's settings ([digest] in the town's config), or None when it's off."""
    settings = config.get("digest", {})
    if not settings.get("signup"):
        return None
    missing = [key for key in ("provider", "privacy_url") if not settings.get(key)]
    if missing:
        raise SystemExit(f"[digest] signup in config/{config.get('slug', 'the town')}.toml needs {' and '.join(missing)}: "
                         "who keeps the addresses, and their privacy policy, for the form and the About page.")
    return {"provider": settings["provider"], "privacy_url": settings["privacy_url"]}


def first_collected(docs: list[dict], tz: ZoneInfo) -> date | None:
    """The day, in the town's time, a meeting's first document of a kind was collected."""
    stamps = [datetime.fromisoformat(d["fetched_at"]) for d in docs if d.get("fetched_at")]
    return min(stamps).astimezone(tz).date() if stamps else None


def minutes_posted(meetings: list[dict], tracking_since: str | None, tz: ZoneInfo) -> dict[int, date | None]:
    """The day each meeting's minutes were first collected, by id(meeting), in the town's time; None
    for a meeting without minutes, for minutes that turned out to be another document (an agenda
    filed as minutes), and for minutes collected on the site's first day, the town's history read
    all at once rather than minutes just posted. Collected each morning, so posted by that day."""
    since = datetime.fromisoformat(tracking_since).astimezone(tz).date() if tracking_since else None
    days = {}
    for m in meetings:
        day = first_collected(m.get("minutes", []), tz) if (m.get("minutes_summary") or {}).get("is_minutes", True) else None
        days[id(m)] = day if day and since and since < day else None
    return days


def sundays(since: date, today: date) -> list[date]:
    """Every Sunday from the first on or after since to the last on or before today, newest first."""
    first = since + timedelta(days=(6 - since.weekday()) % 7)
    last = today - timedelta(days=(today.weekday() + 1) % 7)
    return [last - timedelta(weeks=n) for n in range(((last - first).days // 7) + 1)] if first <= last else []


def last_day(sunday: date) -> date:
    """The last day of the minutes an issue lists, and the first day it's built: its Saturday, or for an
    issue dated before MORNING_FROM, its Sunday."""
    return sunday - timedelta(days=1) if sunday >= MORNING_FROM else sunday


def send_at(sunday: date, tz: ZoneInfo) -> datetime:
    """When an issue's email is due, in the town's time."""
    return datetime.combine(sunday, SEND_TIME if sunday >= MORNING_FROM else EVENING_SEND_TIME, tz)


def issues(meetings: list[dict], tracking_since: str | None, today: date, tz: ZoneInfo) -> list[dict]:
    """The digest's issues, newest first. meetings are every meeting (build_site.load_meetings),
    in date order; tracking_since is when the site first saw any of them. Minutes collected on
    that first day are the town's history, read all at once, and are left out. Each issue also has
    the meetings summarized from their recordings that week, before their minutes ("recorded")."""
    if not tracking_since:
        return []
    since = datetime.fromisoformat(tracking_since).astimezone(tz).date()
    minutes_day = minutes_posted(meetings, tracking_since, tz)
    # The day each meeting's recording was transcribed, for one with a summary from it.
    recording_day = {id(m): first_collected([m["transcript_doc"]], tz)
                     if m.get("recording_summary") and m.get("transcript_doc") else None for m in meetings}
    found = []
    # Tomorrow's Sunday too: an issue is built from its Saturday.
    for sunday in sundays(since, today + timedelta(days=1)):
        end = last_day(sunday)
        if end > today:
            continue
        # The minutes from the day after the issue before's to this one's last day.
        start = last_day(sunday - timedelta(weeks=1)) + timedelta(days=1)
        monday = sunday + timedelta(days=1)
        week = [m for m in meetings if monday.isoformat() <= m["date"] <= (sunday + timedelta(days=7)).isoformat()]
        minutes = [m for m in meetings
                   if minutes_day[id(m)] and start <= minutes_day[id(m)] <= end
                   and m["date"] >= (sunday - MINUTES_MAX_AGE).isoformat()]
        # Meetings summarized from their recordings, transcribed that week, whose minutes hadn't come by its end.
        recorded = [m for m in meetings
                    if recording_day[id(m)] and start <= recording_day[id(m)] <= end
                    and not (minutes_day[id(m)] and minutes_day[id(m)] <= end)
                    and m["date"] >= (sunday - MINUTES_MAX_AGE).isoformat()]
        if not week and not minutes and not recorded:
            continue
        found.append({
            "sunday": sunday.isoformat(), "monday": monday.isoformat(), "end": (sunday + timedelta(days=7)).isoformat(),
            "minutes_from": start.isoformat(), "minutes_to": end.isoformat(),
            "url": f"/digest/{monday.isoformat()}/",
            "send_at": send_at(sunday, tz),
            "meetings": week, "minutes": minutes, "recorded": recorded,
            # The week's meetings by day, for the page.
            "days": [(day, [m for m in week if m["date"] == day]) for day in sorted({m["date"] for m in week})],
        })
    return found
