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
issue to another. A meeting summarized from its recording is listed in the issue of the week it was
transcribed; when its minutes come in a later week, that issue says so in a line ("minutes_posted")
rather than list its decisions again.

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
import json
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


# A decision that is routine whatever its summary says: a consent agenda, minutes, bills or payroll approved, by its
# title (or its text, for one without). Titles start with what was decided ("Consent agenda as presented", "Minutes
# of October 21, 2026 meeting"); a decision that only mentions minutes ("Release of executive session minutes on
# Flock cameras") isn't one.
ROUTINE = re.compile(r"^(?:approv\w*\s+)?(?:of\s+)?(?:the\s+)?(?:consent agenda|(?:prior |previous |meeting )?minutes\b|"
                     r"warrants?\b|bills\b|payrolls?\b|(?:the\s+)?agenda as\b|adjourn)", re.I)


def main_decisions(m: dict) -> tuple[list, int]:
    """A meeting's decisions residents look for, those that settled a matter first, then those that put one off
    (PUT_OFF), each in the order taken; and how many routine ones there were besides: those its summary labels
    routine (Decision.main False) and those ROUTINE names. A summary that labels none of them routine, or all of
    them, hasn't told them apart, so only ROUTINE does; and a meeting whose every decision is routine has them all
    as main."""
    decided = m["decisions"]["decided"]
    labeled = [d for d in decided if getattr(d, "main", None) is False]
    told_apart = 0 < len(labeled) < len(decided)
    routine = [d for d in decided if (told_apart and getattr(d, "main", None) is False)
               or ROUTINE.match(getattr(d, "title", None) or str(d))]
    if len(routine) == len(decided):
        routine = []
    main = [d for d in decided if not any(d is r for r in routine)]
    settled = [d for d in main if getattr(d, "outcome", None) not in PUT_OFF]
    return settled + [d for d in main if getattr(d, "outcome", None) in PUT_OFF], len(routine)


# How recent a meeting must be to lead an issue, before its Sunday: minutes come 2 to 6 weeks after a meeting, and one
# older than this is listed after the week's newer ones, and never leads.
LEAD_MAX_AGE = timedelta(days=21)
# Words that say a meeting's headline reports what was decided ("Approved ...", "... tabled", "Council adopts ..."),
# rather than what was discussed or said ("Neighbors ask the city to ...", "Superintendent estimates ..."). A headline
# without one isn't shown under "What was decided"; the meeting's first main decision is, as a row.
DECIDED_WORDS = re.compile(
    r"\b(?:approv\w*|adopt(?:s|ed)|pass(?:es|ed)|den(?:ies|ied)|reject(?:s|ed)|fail(?:s|ed)|vot(?:es|ed)|tabled|referred|"
    r"continued|postponed|grant(?:s|ed)|award(?:s|ed)|accept(?:s|ed)|ratif(?:ies|ied)|confirm(?:s|ed)|elect(?:s|ed)|"
    r"appoint(?:s|ed)|recommend(?:s|ed)|authoriz(?:es|ed)|allow(?:s|ed)|amend(?:s|ed)|enact(?:s|ed)|revoked|"
    r"overr(?:ode|ides|idden)|agreed|dismissed|certified|selected|chose|withdr(?:ew|awn)|signed|supported|opposed|"
    r"declined|ordered|suspended|expelled|kept|set|sent|hired|funded|renewed|extended|sold|bought|banned|held over|tie)\b", re.I)


def decided_line(m: dict) -> str | None:
    """A meeting's headline where it reports what was decided (DECIDED_WORDS), for "What was decided"; else None."""
    line = m.get("preview_line")
    return line if line and DECIDED_WORDS.search(line) else None


def decided(issue: dict, main_boards: list[str]) -> dict:
    """What an issue says was decided: the meetings whose minutes, or else whose recordings, came that week, the
    strongest first: those of the last LEAD_MAX_AGE before the issue's Sunday before older ones, then a main board's
    (main_boards), then by how many of their main decisions settled a matter, then how many main decisions, then the
    newest. Meetings that decided nothing are listed apart ("quiet"). Each entry's line is its headline where that
    reports a decision (decided_line()). The lead is the first, if it's recent, and its title (lead_title) its
    headline's first clause, for the email's subject where no subject was written for it (headline())."""
    sunday = issue.get("sunday")
    since = (date.fromisoformat(sunday) - LEAD_MAX_AGE).isoformat() if sunday else ""
    entries, quiet = [], []
    for source, meetings in (("minutes", issue["minutes"]), ("recording", issue["recorded"])):
        for m in meetings:
            main, routine = main_decisions(m)
            entry = {"meeting": m, "name": meeting_name(m), "source": source, "main": main, "routine": routine,
                     "recommended": m["decisions"]["recommended"], "main_board": m["body"] in main_boards,
                     "count": len(m["decisions"]["decided"]), "recent": m["date"] >= since, "line": decided_line(m)}
            (entries if main or entry["recommended"] else quiet).append(entry)
    settled = lambda e: sum(getattr(d, "outcome", None) not in PUT_OFF for d in e["main"])
    entries.sort(key=lambda e: (e["recent"], e["main_board"], settled(e), len(e["main"]), len(e["recommended"]),
                                e["meeting"]["date"]), reverse=True)
    quiet.sort(key=lambda e: e["meeting"]["date"], reverse=True)
    lead = entries[0] if entries and entries[0]["recent"] else None
    return {"entries": entries, "quiet": quiet, "lead": lead, "lead_title": headline(lead) if lead else None}


# The most meetings a line of the email's glance names; the rest are counted ("and 2 more").
GLANCE_NAMES = 3


def glance(issue: dict) -> dict:
    """The week at a glance, for the top of the email, from what the issue already lists: the meetings that take
    place ("live") and how many fall on each day; those with a public hearing on their agenda; those that won't take
    place as listed (cancelled, postponed, or removed from the town's listing); how many still have no agenda; and the
    meetings whose decisions came in (issue["decided"], from decided())."""
    off = [m for m in issue["meetings"] if m["status"] in ("cancelled", "postponed") or not m.get("listed", True)]
    live = [m for m in issue["meetings"] if id(m) not in {id(o) for o in off}]
    return {
        "live": live,
        "per_day": [(day, sum(m["date"] == day for m in live)) for day in sorted({m["date"] for m in live})],
        "hearings": [m for m in live if m.get("public_hearing")],
        "off": off,
        # As the email's list says "No agenda posted yet" under a meeting.
        "no_agenda": sum(m["status"] == "scheduled" and not (m.get("agenda") or m.get("documents_url")) for m in live),
        "decided": issue["decided"]["entries"],
        "names": GLANCE_NAMES,
    }


# Past tenses that don't end in "ed", which a headline may start with (summarize.py asks it to start with the outcome).
PAST_TENSE = {"overrode", "held", "heard", "set", "sent", "took", "gave", "made", "chose", "kept", "put", "cut", "let",
              "paid", "sold", "bought", "began", "met", "won", "lost", "struck", "wrote", "drew", "upheld", "withdrew",
              "swore", "spent", "found", "built", "brought", "laid", "led", "told", "said", "left", "ran", "saw"}
# Where a headline's first clause ends: "Approved $200,000 soil cleanup at 448 High Street, and asked for ...".
CLAUSE_END = re.compile(r",\s|;\s|\s(?:and|after|while)\s")
# A decision's outcome (factcheck.OUTCOMES) as the verb before its title, for a headline made from it.
OUTCOME_VERBS = {"approved": "Approved", "denied": "Denied", "tabled": "Tabled", "referred": "Referred",
                 "continued": "Continued"}


def lowercase_start(title: str) -> bool:
    """Whether a decision's title can start lowercase after a verb: not a name ("Boynton Yards zoning"), an
    abbreviation ("FY27 budget", "GHS plan") or a figure ("$25,000 grant")."""
    words = title.split()
    if not words or not words[0].isalpha() or not words[0][0].isupper() or any(c.isupper() for c in words[0][1:]):
        return False
    return not (len(words) > 1 and words[1][:1].isupper())


def headline(entry: dict, limit: int = SUBJECT_LENGTH) -> str | None:
    """The lead's headline as a newspaper's: its first clause (the summary's headline leads with what residents
    look for), cut at a word to at most limit characters; after its board's name where that's at most SUBJECT_NAME
    characters ("City Council approved ..."), or alone. An entry whose headline doesn't report a decision (its line,
    from decided()) has its first main decision's instead ("City Council approved retiree cost-of-living increase").
    None for a meeting with neither."""
    line = entry["line"] if "line" in entry else entry["meeting"].get("preview_line")
    if not line and entry.get("main"):
        first = entry["main"][0]
        outcome, title = getattr(first, "outcome", None), getattr(first, "title", None)
        if outcome in OUTCOME_VERBS and title:
            line = f"{OUTCOME_VERBS[outcome]} {title[0].lower() + title[1:] if lowercase_start(title) else title}"
    line = (line or "").strip().rstrip(".")
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


# ---- The email's subject -------------------------------------------------------------------------------------------
#
# A language model chooses each issue's top story and writes the email's subject and preheader (the preview line an
# inbox shows beside the subject), once the issue is built (pipeline/digest_subject.py), from candidates(): the week's
# meetings to come, by their agenda's headline and items, and the recent meetings whose decisions came in, by their
# headline and decisions, all as the site already shows them. Its answer is kept in data/digest/<Monday>.json, and
# the build uses it where problems() finds nothing wrong: a candidate picked, a subject short enough, and every number
# and name in it found in the candidate's own words, so it says nothing the site doesn't. Otherwise, as for a town
# without a model, the subject is the week's lead decision as a headline (headline()).

# Candidates: a main board's and those with a public hearing first, then by day; and the strongest recent decisions.
AHEAD_CANDIDATES = 8
DECIDED_CANDIDATES = 6
AGENDA_ITEMS = 4
DECISION_TITLES = 5
# The longest a subject may be, after the site's name worker/digest.js puts before it, and a preheader.
SUBJECT_MAX = 60
PREHEADER_MAX = 90
# Where the model's answers are kept, in the town's data folder.
SUBJECTS_DIR = "digest"
# Words a subject or preheader may use without the candidate's words having them: days, and a preheader's lead-ins.
DAY_WORDS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
             "Mon", "Tue", "Tues", "Wed", "Thu", "Thurs", "Fri", "Sat", "Sun"}
LEAD_INS = {"Also", "Plus", "Coming", "Last", "This", "Next", "Earlier"}
NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def live_meetings(issue: dict) -> list[dict]:
    """The week's meetings that take place as listed: not cancelled, postponed, or taken off the town's listing."""
    return [m for m in issue["meetings"] if m["status"] not in ("cancelled", "postponed") and m.get("listed", True)]


def candidates(issue: dict, main_boards: list[str]) -> list[dict]:
    """What an issue's subject may be about, each with an id for the model to pick it by and its page's address,
    which identifies it from one build to the next: the week's meetings with an agenda's headline (AHEAD_CANDIDATES),
    a main board's (main_boards) and those with a public hearing first, then by day and time; and the recent meetings whose decisions
    came in (issue["decided"], from decided()), the strongest first (DECIDED_CANDIDATES). Each carries the words the
    site shows for it, which are all the model is given, and all a subject's facts may come from."""
    ahead = [m for m in live_meetings(issue) if m.get("preview_line") and m.get("preview_from", "agenda") == "agenda"]
    ahead.sort(key=lambda m: (m["body"] not in main_boards, not m.get("public_hearing"), m["date"], m.get("start_time") or ""))
    found = []
    for n, m in enumerate(ahead[:AHEAD_CANDIDATES], 1):
        found.append({
            "id": f"A{n}", "kind": "ahead", "url": m["url"], "board": meeting_name(m), "date": m["date"],
            "day": date.fromisoformat(m["date"]).strftime("%A"), "time": m.get("start_time"),
            "main_board": m["body"] in main_boards, "hearing": bool(m.get("public_hearing")), "line": m["preview_line"],
            "items": [str(i) for i in ((m.get("preview") or {}).get("items") or [])[:AGENDA_ITEMS]],
        })
    recent = [e for e in issue["decided"]["entries"] if e["recent"] and e["main"]]
    for n, e in enumerate(recent[:DECIDED_CANDIDATES], 1):
        m = e["meeting"]
        found.append({
            "id": f"D{n}", "kind": "decided", "url": m["url"], "board": e["name"], "date": m["date"],
            "day": date.fromisoformat(m["date"]).strftime("%A"), "main_board": e["main_board"], "source": e["source"],
            "line": e["line"] or "",
            "decisions": [f"{getattr(d, 'outcome', None) or 'decided'}: {getattr(d, 'title', None) or d}"
                          for d in e["main"][:DECISION_TITLES]],
        })
    return found


def candidate_words(c: dict) -> str:
    """Everything a candidate says, which a subject about it may use."""
    return " ".join([c["board"], c["day"], c["line"], *c.get("items", []), *c.get("decisions", [])])


def ungrounded(text: str, source: str) -> list[str]:
    """The numbers and capitalized words in text that source doesn't have: each number as written, and each name
    with its capitals (a sentence's first word, or a preheader's after its lead-in, in any case). Days of the week
    and a preheader's lead-ins ("Also:", "Coming up:") are allowed."""
    missing = []
    for n in NUMBER.findall(text):
        if not re.search(rf"(?<![\d]){re.escape(n)}(?![\d])", source):
            missing.append(n)
    for match in WORD.finditer(text):
        word = re.sub(r"['’]s$", "", match.group()).rstrip("'’-")
        if not word[0].isupper() or word in DAY_WORDS or word in LEAD_INS:
            continue
        before = text[:match.start()].rstrip()
        starts = not before or before[-1] in ":;.!?"
        if not re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", source, 0 if not starts else re.I):
            missing.append(word)
    return missing


def problems(answer: dict, cands: list[dict]) -> list[str]:
    """What's wrong with a model's answer ({"pick", "subject", "preheader"}) for these candidates, one line each;
    empty when the build may use it. The subject is checked against the candidate it picked, the preheader against
    every candidate (it may name a second story)."""
    out = []
    pick = next((c for c in cands if c["id"] == answer.get("pick")), None)
    subject, preheader = str(answer.get("subject") or "").strip(), str(answer.get("preheader") or "").strip()
    if not pick:
        out.append(f"it picked {answer.get('pick')!r}, which isn't a candidate")
    if not subject:
        out.append("it has no subject")
    if len(subject) > SUBJECT_MAX:
        out.append(f"its subject is {len(subject)} characters, past {SUBJECT_MAX}")
    if len(preheader) > PREHEADER_MAX:
        out.append(f"its preheader is {len(preheader)} characters, past {PREHEADER_MAX}")
    for name, text in (("subject", subject), ("preheader", preheader)):
        if "…" in text or "..." in text or "\n" in text:
            out.append(f"its {name} is cut off or runs over a line")
    if pick:
        out += [f"its subject's {w!r} isn't in what the site says of {pick['id']}"
                for w in ungrounded(subject, candidate_words(pick))]
    every = " ".join(candidate_words(c) for c in cands)
    out += [f"its preheader's {w!r} isn't in what the site says of any candidate" for w in ungrounded(preheader, every)]
    return out


def stored(data_dir: Path, issue: dict) -> dict | None:
    """The model's answer for an issue, as pipeline/digest_subject.py kept it, or None."""
    path = data_dir / SUBJECTS_DIR / f"{issue['monday']}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def subject(issue: dict, main_boards: list[str], record: dict | None, now: datetime) -> dict:
    """An issue's subject, preheader, and top story ("pick", a candidate, or None), and whether a model wrote them
    ("written"). The model's answer (record, from stored()) is used where it has a subject and, for an issue not yet
    due, its candidate is still one of the issue's (by its page's address) and problems() finds nothing wrong with it
    there; an issue already due keeps the answer it was sent with. Otherwise the subject is the week's lead decision
    as a headline (decided()'s lead_title, or None for the week's date), and the email makes its own preheader."""
    cands = candidates(issue, main_boards)
    if record and record.get("subject") and record.get("pick"):
        if now >= issue["send_at"]:
            return {"subject": record["subject"], "preheader": record.get("preheader") or None, "pick": record["pick"],
                    "written": True}
        current = next((c for c in cands if c["url"] == record["pick"].get("url")), None)
        if current:
            answer = {"pick": current["id"], "subject": record["subject"], "preheader": record.get("preheader")}
            if not problems(answer, cands):
                return {"subject": record["subject"], "preheader": record.get("preheader") or None, "pick": current,
                        "written": True}
    lead = issue["decided"]["lead"]
    pick = next((c for c in cands if lead and c["url"] == lead["meeting"]["url"]), None)
    return {"subject": issue["decided"]["lead_title"], "preheader": None, "pick": pick, "written": False}


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
        # Minutes of a meeting an earlier issue listed from its recording: said in a line, not listed again, so a
        # meeting is never an issue's news twice.
        posted = [m for m in minutes if recording_day[id(m)] and recording_day[id(m)] < start]
        minutes = [m for m in minutes if not any(m is p for p in posted)]
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
            "meetings": week, "minutes": minutes, "recorded": recorded, "minutes_posted": posted,
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
