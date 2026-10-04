"""Why something a page could show isn't there, so the page can say so.

A reader can't tell "the city hasn't posted it" from "this site missed it", and
a gap with no reason reads as a broken site even when the data is right. Every
gap has one of a few reasons, and the build already knows which:

- not posted yet, and for how long (minutes after a meeting);
- the source doesn't include it (a listing that links no agenda);
- this site doesn't collect it (a board whose minutes no source reads, a
  meeting before a collection's start date, a summary before [summaries] since);
- held back by a check (pipeline/factcheck.py, shown on the meeting page);
- this site's copy is behind (the calendar couldn't be read; pipeline/freshness.py);
- it doesn't apply to the town.

This module works out the reason for a meeting's missing minutes, agenda and
summary, whether the meetings calendar is behind, which of a section's sources
are behind, and which sections a town doesn't have and why. Each reason is a small
dict the templates turn into one sentence, written and translated once, so
every town gets the same wording with nothing in its config.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pipeline.fetch_minutes import LINKED

# Statuses of a meeting that wasn't held as listed: it has no minutes to wait for.
NOT_HELD = ("cancelled", "postponed", "rescheduled")
# Sources that never carry a meeting's documents: a school district's calendar
# feed, and a page of the year's dates ([schedule_meetings]).
NO_DOCUMENTS = ("ical", "schedule")
# How many days the meetings calendar may go unread before it's called behind,
# when the town's [freshness] table doesn't say.
CALENDAR_MAX_DAYS = 2


def sources(m: dict) -> set[str]:
    """Every source a meeting is listed in: its own, and those of the listings shown with it."""
    return {m.get("source", "calendar"), *(x.get("source", "calendar") for x in m.get("listings", []))}


def minutes_source(config: dict, m: dict) -> tuple[str, str] | None:
    """Where this site collects a meeting's minutes from, as (source, since date), or None if
    no source it's listed in carries its minutes. A city calendar's meeting gets its minutes from
    the Archive Center ([archive]), or from the Agenda Center's posting of the same meeting, which
    the build shows with it; a Manchester calendar board's are read from nowhere."""
    meetings = config.get("meetings", {})
    if not meetings.get("documents", True):
        return None
    found = []
    for source in sources(m) - set(NO_DOCUMENTS):
        if source in LINKED and LINKED[source] in meetings:
            found.append((source, str(meetings[LINKED[source]]["since"])))
        elif source in ("finalsite", "drive") and f"{source}_meetings" in config:
            found.append((source, str(config[f"{source}_meetings"]["since"])))
        elif source in ("archive", "calendar") and "archive" in config:
            found.append(("archive", str(config["archive"]["minutes_since"])))
        elif source == "calendar" and "agenda_center" in meetings:
            found.append(("agendacenter", str(meetings["agenda_center"]["since"])))
    return min(found, key=lambda f: f[1]) if found else None


def minutes_gap(config: dict, m: dict, today: date) -> dict | None:
    """Why a meeting that has taken place has no minutes here, or None when there's nothing to
    say: it has minutes, it's upcoming, or it wasn't held (cancelled, or no longer listed, which
    its page already says). One of:
    {"reason": "not_collected"}: no source this site reads carries this board's minutes;
    {"reason": "before", "since": date}: before the date this site collects minutes from;
    {"reason": "linked", "url": ...}: the city links the minutes, and no copy is saved yet;
    {"reason": "not_posted", "days": n, "source": ...}: not posted yet, n days after the meeting."""
    if m.get("minutes") or m["date"] >= today.isoformat():
        return None
    if m.get("status", "scheduled") in NOT_HELD or not m.get("listed", True):
        return None
    found = minutes_source(config, m)
    if found is None:
        return {"reason": "not_collected"}
    source, since = found
    if m["date"] < since:
        return {"reason": "before", "since": since}
    if m.get("minutes_url"):
        return {"reason": "linked", "url": m["minutes_url"]}
    return {"reason": "not_posted", "days": (today - date.fromisoformat(m["date"])).days, "source": source}


def agenda_gap(m: dict, today: date) -> dict | None:
    """Why a past meeting has no agenda here when nothing else on its page explains it: it
    wasn't held as listed ({"reason": "not_held"}), or its listing links none ({"reason":
    "none_linked"}). None for a meeting with an agenda or a link to one, or an upcoming one."""
    if m.get("agendas") or m.get("agenda_url") or m.get("documents_url") or m["date"] >= today.isoformat():
        return None
    return {"reason": "not_held" if m.get("status", "scheduled") in NOT_HELD else "none_linked"}


def summary_gap(config: dict, m: dict) -> dict | None:
    """Why a meeting's documents won't get a summary: the town has no [summaries]
    ({"reason": "off"}), or the meeting is before [summaries] since ({"reason": "before",
    "since": date}). None when a summary will come (or already has)."""
    settings = config.get("summaries") or {}
    if not settings.get("model"):
        return {"reason": "off"}
    since = settings.get("since")
    if since and m["date"] < str(since):
        return {"reason": "before", "since": str(since)}
    return None


def annotate(meetings: dict, config: dict, today: date) -> None:
    """Set each meeting's minutes_gap, agenda_gap and summary_gap for its page."""
    for m in meetings["all"]:
        m["minutes_gap"] = minutes_gap(config, m, today)
        m["agenda_gap"] = agenda_gap(m, today)
        m["summary_gap"] = summary_gap(config, m)


def calendar_behind(config: dict, status: dict | None, now: datetime) -> dict | None:
    """When the meetings calendar hasn't been read successfully within its allowed age (its
    [freshness] row for meetings/status.json, or CALENDAR_MAX_DAYS): {"since": the last good read,
    or None if never}. A page then says meetings may be missing instead of listing none."""
    if "meetings" not in config:
        return None
    max_days = next((s["max_days"] for s in config.get("freshness", {}).get("sources", [])
                     if s.get("file") == "meetings/status.json"), CALENDAR_MAX_DAYS)
    stamp = (status or {}).get("updated_at")
    updated = datetime.fromisoformat(stamp) if stamp else None
    if updated and now - updated <= timedelta(days=max_days):
        return None
    return {"since": updated.date().isoformat() if updated else None}


# The data files each section's pages show, by the start of their path under data/
# (pipeline/freshness.py names each source's file).
SECTION_FILES = {"meetings": ("meetings/", "summaries"), "311": ("311/",), "budget": ("finance/",),
                 "schools": ("schools/",), "housing": ("housing/", "permits/")}
# Said by the calendar's own note instead (calendar_behind), where meetings are listed.
CALENDAR_FILE = "meetings/status.json"


def section_behind(data_status: list[dict], section: str | None) -> list[dict]:
    """The freshness rows (pipeline/freshness.check) of a section's sources that are behind, for
    its pages to say so; none for a page outside a section."""
    prefixes = SECTION_FILES.get(section or "", ())
    return [r for r in data_status if r.get("stale") and r.get("file") != CALENDAR_FILE
            and str(r.get("file", "")).startswith(prefixes)] if prefixes else []


# The sections every town can have, in the order the About page lists what a town lacks.
SECTIONS = ("meetings", "311", "budget", "schools", "housing", "officials")
# A section whose page is a state's own (pipeline/states SECTIONS), and the source it shows.
STATE_SECTIONS = {"budget": "budget", "schools": "schools"}


def not_covered(config: dict, state) -> list[dict]:
    """The sections this town's site doesn't have, and why, for the About page: {"section": slug,
    "reason": ..., "note": the town's own sentence ([absences] <slug> in its config) or None}.
    The reason is "no_311" (311 comes only from SeeClickFix), "state" (the engine has no source of
    this kind for the town's state yet), or "not_added" (the town's config doesn't list it)."""
    notes = config.get("absences", {})
    unknown = set(notes) - set(SECTIONS)
    if unknown:
        raise SystemExit(f"[absences] in config/{config.get('slug', 'the town')}.toml names {', '.join(sorted(unknown))}; "
                         f"it takes a sentence for any of {', '.join(SECTIONS)}.")
    listed = {s["slug"] for s in config.get("sections", [])}
    gaps = []
    for slug in SECTIONS:
        if slug in listed:
            continue
        if slug == "311":
            reason = "no_311"
        elif slug in STATE_SECTIONS and not state.sources.get(STATE_SECTIONS[slug]):
            reason = "state"
        else:
            reason = "not_added"
        gaps.append({"section": slug, "reason": reason, "note": notes.get(slug)})
    return gaps
