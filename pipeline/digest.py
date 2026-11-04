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

import html
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse
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
# The meetings with decisions the email lists; the rest are on the issue's page, a link away.
EMAIL_MEETINGS = 8
# A main board's decisions the email lists by title (main_boards in build_site.py); another meeting is its headline
# alone. The issue's page lists every main decision of every meeting.
EMAIL_DECISIONS = 5
# The longest the lead's headline runs in the email's subject, in characters, after it's cut to its first clause; a
# longer one is cut at a word.
SUBJECT_LENGTH = 60
# The longest a board's name runs before the lead's headline in the subject; a longer one is left out.
SUBJECT_NAME = 30
# Outcomes that put a matter off rather than settle it (build_site.PUT_OFF), listed after those that settled one.
PUT_OFF = ("referred", "tabled", "continued")
# Issues in the feed, newest first.
FEED_ISSUES = 12
# Links in the email say where the reader came from, for the page counts (site/static/js/count.js).
EMAIL_REF = "digest-email"
# The email's footer, as the law on commercial email asks: a postal address, and a link to unsubscribe.
# The email goes without Buttondown's template around it (worker/digest.js), so nothing else adds them.
# The address is Buttondown's, which it offers its newsletters for this, as its
# privacy policy gives it (https://buttondown.com/legal/privacy, updated 2026-04-01). Left as
# "[mailing address]", the scheduler refuses to send the email (tests still go).
MAILING_ADDRESS = "c/o Buttondown, LLC, 406 W Franklin St. #201, Richmond, VA 23221"
# Buttondown's tag for each reader's own unsubscribe link, filled in as it sends.
UNSUBSCRIBE_TAG = "{{ unsubscribe_url }}"
# The largest an issue's email may be, in bytes of HTML. Gmail cuts an email off at 102 KB and hides the rest, which
# would hide the footer with the unsubscribe link; this leaves room for what the provider adds as it sends.
EMAIL_MAX_BYTES = 100_000


def meeting_name(m: dict) -> str:
    """A meeting as the digest names it: its title where that says more than its board, " Meeting" and "Regular"
    (a board's subcommittee, filed under the board), else its board; with the part of the day, for a board that
    met twice that day."""
    title = (m.get("title") or "").removesuffix(" Meeting")
    extra = title[len(m["body"]):].strip() if title.startswith(m["body"] + " ") else ""
    # "Regular" says no more than the board's name does.
    name = title if extra and extra not in ("Regular", "Regular Session") else m["body"]
    return f"{name} ({m['day_part']})" if m.get("day_part") else name


# The most parts of a meeting's place the email gives, separated by commas: "Community Room, Beverly Police Department".
PLACE_PARTS = 2


def short_place(m: dict, town: str) -> str | None:
    """Where a meeting is, short, for the email's list: its place's name or description (not the street address
    alone), without the town, its state and ZIP code, which every meeting of the town shares, and at most its first
    PLACE_PARTS parts. None where the listing doesn't say."""
    place = (m.get("location_name") or m.get("location") or "").strip()
    if not place:
        return None
    # The town only where it ends the place, or a parenthesis in it: "Beverly Police Department" keeps its name.
    place = re.sub(rf",\s*{re.escape(town)}(,?\s*[A-Z]{{2}})?(\s+\d{{5}}(-\d{{4}})?)?(?=\s*(\)|$))", "", place).strip(" ,")
    parts, depth, current = [], 0, ""
    for ch in place:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())
    return ", ".join(p for p in parts[:PLACE_PARTS] if p) or None


def main_decisions(m: dict) -> tuple[list, int]:
    """A meeting's decisions residents look for, those that settled a matter first, then those that put one off
    (PUT_OFF), each in the order taken; and how many routine ones there were besides (Decision.main False). A
    summary that doesn't say which are routine has them all as main."""
    decided = m["decisions"]["decided"]
    routine = [d for d in decided if getattr(d, "main", None) is False]
    if not routine or len(routine) == len(decided):
        main, routine = list(decided), []
    else:
        main = [d for d in decided if getattr(d, "main", None) is not False]
    settled = [d for d in main if getattr(d, "outcome", None) not in PUT_OFF]
    return settled + [d for d in main if getattr(d, "outcome", None) in PUT_OFF], len(routine)


def decided(issue: dict, main_boards: list[str]) -> dict:
    """What an issue says was decided: the meetings whose minutes, or else whose recordings, came that week, the
    strongest first: a main board's (main_boards), then by how many of their main decisions settled a matter, then
    how many main decisions, then the newest. Meetings that decided nothing are listed apart ("quiet"). The lead is
    the first, and its title (lead_title) its headline's first clause, for the email's subject (headline())."""
    entries, quiet = [], []
    for source, meetings in (("minutes", issue["minutes"]), ("recording", issue["recorded"])):
        for m in meetings:
            main, routine = main_decisions(m)
            entry = {"meeting": m, "name": meeting_name(m), "source": source, "main": main, "routine": routine,
                     "recommended": m["decisions"]["recommended"], "main_board": m["body"] in main_boards,
                     "count": len(m["decisions"]["decided"])}
            (entries if main or entry["recommended"] else quiet).append(entry)
    settled = lambda e: sum(getattr(d, "outcome", None) not in PUT_OFF for d in e["main"])
    entries.sort(key=lambda e: (e["main_board"], settled(e), len(e["main"]), len(e["recommended"]), e["meeting"]["date"]),
                 reverse=True)
    quiet.sort(key=lambda e: e["meeting"]["date"], reverse=True)
    lead = entries[0] if entries else None
    return {"entries": entries, "quiet": quiet, "lead": lead, "lead_title": headline(lead) if lead else None}


# Past tenses that don't end in "ed", which a headline may start with (summarize.py asks it to start with the outcome).
PAST_TENSE = {"overrode", "held", "heard", "set", "sent", "took", "gave", "made", "chose", "kept", "put", "cut", "let",
              "paid", "sold", "bought", "began", "met", "won", "lost", "struck", "wrote", "drew", "upheld", "withdrew",
              "swore", "spent", "found", "built", "brought", "laid", "led", "told", "said", "left", "ran", "saw"}
# Where a headline's first clause ends: "Approved $200,000 soil cleanup at 448 High Street, and asked for ...".
CLAUSE_END = re.compile(r",\s|;\s|\s(?:and|after|while)\s")


def headline(entry: dict, limit: int = SUBJECT_LENGTH) -> str | None:
    """The lead's headline as a newspaper's: its first clause (the summary's headline leads with what residents
    look for), cut at a word to at most limit characters; after its board's name where that's at most SUBJECT_NAME
    characters ("City Council approved ..."), or alone. None for a meeting without a headline."""
    line = (entry["meeting"].get("preview_line") or "").strip().rstrip(".")
    if not line:
        return None
    end = CLAUSE_END.search(line)
    if end and len(line[:end.start()].split()) >= 4:
        line = line[:end.start()]
    if len(line) > limit:
        line = line[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    first = line.split(" ", 1)[0]
    # Only a name short enough for a subject, and only before what the board did ("Approved", "Overrode"):
    # "City Council approved ...".
    if len(entry["name"]) > SUBJECT_NAME or not (first.istitle() and first.isalpha()
                                                 and (first.endswith("ed") or first.lower() in PAST_TENSE)):
        return line
    return f"{entry['name']} {first.lower()}{line[len(first):]}"


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


def email_problems(body: str, base_url: str, site_dir: Path) -> list[str]:
    """What would keep an issue's email (its feed item's HTML) from being sent as it should, checked in each town's
    build before it's published (site_checks/test_town_site.py), so a town whose email fails is held back: past
    EMAIL_MAX_BYTES; without its unsubscribe link or postal address, which the law on commercial email asks for; a
    section (an <h2>) that links no meeting; a link to the site whose page, or its anchor, isn't in the build
    (site_dir); a link or image that isn't in full, which an email app can't follow. An empty list when it's fine."""
    problems = []
    size = len(body.encode())
    if size > EMAIL_MAX_BYTES:
        problems.append(f"it's {size:,} bytes, past {EMAIL_MAX_BYTES:,}: Gmail would cut it off before its unsubscribe link")
    if UNSUBSCRIBE_TAG not in body:
        problems.append(f"it has no unsubscribe link ({UNSUBSCRIBE_TAG})")
    if MAILING_ADDRESS not in body:
        problems.append("it has no postal address (MAILING_ADDRESS)")
    sections = re.split(r"<h2\b", body)[1:]
    for section in sections:
        heading = re.sub(r"<[^>]+>", "", section.split("</h2>", 1)[0].split(">", 1)[-1]).strip()
        if f'href="{base_url}/meetings/' not in section:
            problems.append(f'its section "{heading}" links no meeting')
    for attr, value in re.findall(r'\b(href|src)="([^"]*)"', body):
        url = html.unescape(value)
        if url == UNSUBSCRIBE_TAG or url.startswith("mailto:"):
            continue
        if not url.startswith(base_url + "/"):
            if not url.startswith("https://"):
                problems.append(f"a link that isn't in full: {url}")
            continue
        parts = urlparse(url)
        target = site_dir / parts.path.lstrip("/")
        if parts.path.endswith("/"):
            target = target / "index.html"
        if not target.is_file():
            problems.append(f"a link to a page the site doesn't have: {url}")
        elif parts.fragment and f'id="{parts.fragment}"' not in target.read_text(encoding="utf-8"):
            problems.append(f"a link to an anchor its page doesn't have: {url}")
    return problems
