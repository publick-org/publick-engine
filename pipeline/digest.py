"""The weekly digest: one page a week for each town's meetings, and a feed of them for email.

Each issue is dated a Sunday and covers two things, from data the site already has (no AI calls):

- the meetings of the week ahead, Monday to Sunday, with their agenda summaries where an agenda
  is posted (most are posted 2 to 4 days before the meeting, so the week's later meetings often
  have none yet);
- the minutes first collected in the week before, Monday to Sunday, with what each meeting decided.

An issue is built from the first build on or after its Sunday, and shows what the site knows
about that week as of the latest build. A week with no meetings and no new minutes has no issue.

The issues are published at /digest/<Monday>/ with a list at /digest/ and a feed at
/digest/feed.xml (build_site.py). Each feed item's date is when its email is due: the Sunday at
SEND_TIME, the town's own time (decided 2026-10-06: Sunday evening gives a day's notice of Monday
evening meetings, and the morning's daily run hours to finish first). Whatever sends the email
(the network's scheduler Worker, worker/digest.js, through Buttondown) sends each item once its
date has passed.

The email's signup form is shown on /digest/ when the town's config turns it on:

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
SEND_TIME = time(17, 30)
# Minutes collected for a meeting longer ago than this are left out: a source's history read
# for the first time, not news.
MINUTES_MAX_AGE = timedelta(days=90)
# Issues in the feed, newest first.
FEED_ISSUES = 12
# Links in the email say where the reader came from, for the page counts (site/static/js/count.js).
EMAIL_REF = "digest-email"


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


def sundays(since: date, today: date) -> list[date]:
    """Every Sunday from the first on or after since to the last on or before today, newest first."""
    first = since + timedelta(days=(6 - since.weekday()) % 7)
    last = today - timedelta(days=(today.weekday() + 1) % 7)
    return [last - timedelta(weeks=n) for n in range(((last - first).days // 7) + 1)] if first <= last else []


def issues(meetings: list[dict], tracking_since: str | None, today: date, tz: ZoneInfo) -> list[dict]:
    """The digest's issues, newest first. meetings are every meeting (build_site.load_meetings),
    in date order; tracking_since is when the site first saw any of them. Minutes collected on
    that first day are the town's history, read all at once, and are left out."""
    if not tracking_since:
        return []
    since = datetime.fromisoformat(tracking_since).astimezone(tz).date()
    # Minutes that turned out to be another document (an agenda filed as minutes) aren't news of a meeting.
    minutes_day = {id(m): first_collected(m.get("minutes", []), tz) if (m.get("minutes_summary") or {}).get("is_minutes", True) else None
                   for m in meetings}
    found = []
    for sunday in sundays(since, today):
        monday = sunday + timedelta(days=1)
        week = [m for m in meetings if monday.isoformat() <= m["date"] <= (sunday + timedelta(days=7)).isoformat()]
        minutes = [m for m in meetings
                   if minutes_day[id(m)] and since < minutes_day[id(m)] and sunday - timedelta(days=6) <= minutes_day[id(m)] <= sunday
                   and m["date"] >= (sunday - MINUTES_MAX_AGE).isoformat()]
        if not week and not minutes:
            continue
        found.append({
            "sunday": sunday.isoformat(), "monday": monday.isoformat(), "end": (sunday + timedelta(days=7)).isoformat(),
            "minutes_from": (sunday - timedelta(days=6)).isoformat(),
            "url": f"/digest/{monday.isoformat()}/",
            "send_at": datetime.combine(sunday, SEND_TIME, tz),
            "meetings": week, "minutes": minutes,
            # The week's meetings by day, for the page.
            "days": [(day, [m for m in week if m["date"] == day]) for day in sorted({m["date"] for m in week})],
        })
    return found
