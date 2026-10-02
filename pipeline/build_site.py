"""Build the static site into _site/.

Each file under site/pages/ is a Jinja template that extends the shared
layout. A page at site/pages/<path>/index.html is served at /<path>/, so every
section and sub-page has a real, linkable URL on GitHub Pages. Pages for
individual records (one per meeting, one per board) are generated from the
JSON in data/.

Usage:
    python -m pipeline.build_site [--town gloucester] [--out _site]
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

import markdown
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

from pipeline.config import DATA_DIR, DEFAULT_TOWN, ENGINE_DIR, TOWN_DIR, TOWN_STATIC_DIR, colors, load_config
from pipeline.documents import open_documents
from pipeline import freshness
from pipeline import common_strings
from pipeline import i18n
from pipeline import officials as officials_mod
from pipeline import states
from pipeline import streets as streets_mod
from pipeline import summarize
from pipeline import translate
from pipeline.fetch_meetings import slugify
from pipeline.i18n import N_, _, month_name, month_year, ngettext, pgettext, plain_date, weekday_name
from pipeline.seeclickfix import short_address

SITE_DIR = ENGINE_DIR / "site"
PAGES_DIR = SITE_DIR / "pages"
# Each state's own pages and page parts: site/states/<state>/ (see pipeline/states/).
STATES_DIR = SITE_DIR / "states"
STATIC_DIR = SITE_DIR / "static"

# Built but kept out of the sitemap.
UNLISTED_PAGES = {"/404.html"}
# Page folders built for every town, whether or not they are in the navigation.
SHARED_FOLDERS = {"streets"}


def url_for(rel_path: Path) -> str:
    """Map a page file path to its public URL path, e.g. 311/index.html -> /311/."""
    parts = rel_path.parts
    if parts[-1] == "index.html":
        parts = parts[:-1]
        return "/" + "/".join(parts) + ("/" if parts else "")
    return "/" + "/".join(parts)


# ---- Links that leave the site ---------------------------------------------

LINK_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S)


def opens_new_tab(href: str, own_hosts: set[str]) -> bool:
    """Links to other sites and to PDFs open in a new tab, so visitors keep their place here."""
    url = urlparse(href)
    if url.scheme in ("http", "https") and url.hostname not in own_hosts:
        return True
    return url.path.lower().endswith(".pdf")


def mark_new_tab_links(html: str, own_hosts: set[str]) -> str:
    """Add target=_blank, rel=noopener, an arrow icon, and screen-reader text to outbound links."""
    note = _("(opens in new tab)")
    def fix(match: re.Match) -> str:
        attrs, text = match.group(1), match.group(2)
        href = re.search(r'href="([^"]*)"', attrs)
        if not href or "target=" in attrs or not opens_new_tab(href.group(1), own_hosts):
            return match.group(0)
        if 'class="' in attrs:
            attrs = attrs.replace('class="', 'class="external ', 1)
        else:
            attrs += ' class="external"'
        return f'<a{attrs} target="_blank" rel="noopener">{text}<span class="visually-hidden"> {note}</span></a>'
    return LINK_RE.sub(fix, html)


# A tag's links to this site's own pages ("/meetings/", "/311/#open"), as opposed to
# files ("/static/...", "/311/data/monthly.csv", saved PDFs, "/feed.xml").
TAG_RE = re.compile(r"<(?:a|form)\b[^>]*>")
OWN_PAGE_RE = re.compile(r'\b(href|action)="(/(?!/|static/)(?:[^"#?]*/)?)([#?][^"]*)?"')


def localize_links(html: str, prefix: str) -> str:
    """Point a page's links to the site's own pages at the same language's pages
    (/meetings/ -> /es/meetings/). A link marked with hreflang, the language switch,
    goes where it says."""
    def fix(tag: re.Match) -> str:
        if "hreflang=" in tag.group(0):
            return tag.group(0)
        return OWN_PAGE_RE.sub(lambda m: f'{m.group(1)}="{prefix}{m.group(2)}{m.group(3) or ""}"', tag.group(0))
    return TAG_RE.sub(fix, html)


# ---- Template filters ------------------------------------------------------

def format_date(value: str | date, fmt: str = "long") -> str:
    d = date.fromisoformat(value) if isinstance(value, str) else value
    if fmt == "short":
        # Translators: a date in a list, such as "Thu, Oct 1".
        return _("{weekday}, {month} {day}").format(weekday=weekday_name(d, short=True), month=month_name(d.month, short=True), day=d.day)
    if fmt == "month":
        return month_year(d)
    if fmt == "plain":
        return plain_date(d)
    if fmt == "mon":
        return month_name(d.month, short=True)
    if fmt == "mon_day":
        # Translators: a short date without the year, such as "Oct 1".
        return pgettext("short date", "{month} {day}").format(month=month_name(d.month, short=True), day=d.day)
    if fmt == "mon_day_year":
        # Translators: a short date, such as "Oct 1, 2026".
        return pgettext("short date", "{month} {day}, {year}").format(month=month_name(d.month, short=True), day=d.day, year=d.year)
    if fmt == "day":
        return str(d.day)
    if fmt == "weekday":
        return weekday_name(d)
    # Translators: a full date, such as "Thursday, October 1, 2026".
    return _("{weekday}, {month} {day}, {year}").format(weekday=weekday_name(d), month=month_name(d.month), day=d.day, year=d.year)


def format_time(value: str | None) -> str:
    if not value:
        return ""
    h, m = (int(x) for x in value.split(":"))
    time = f"{(h % 12) or 12}:{m:02d}"
    # Translators: a time of day, such as "7:00 PM".
    return _("{time} AM").format(time=time) if h < 12 else _("{time} PM").format(time=time)


def format_bytes(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def format_duration(days: float | None) -> str:
    if days is None:
        return _("Not enough data")
    hours = days * 24
    if hours < 1:
        return _("Under 1 hour")
    if hours < 36:
        n = round(hours)
        return ngettext("{n} hour", "{n} hours", n).format(n=n)
    # Always 1.5 days or more here.
    return _("{days} days").format(days=f"{days:.1f}" if days < 10 else f"{days:.0f}")


def format_duration_cell(days: float | None) -> str:
    """Duration for a table cell: a dash when there are too few requests."""
    return "–" if days is None else format_duration(days)


def model_name(model_id: str) -> str:
    """'claude-sonnet-5' -> 'Claude Sonnet 5'."""
    return " ".join(part.capitalize() for part in model_id.split("-"))


def school_year(year: int) -> str:
    """DESE labels a school year by the year it ends: 2026 -> '2025–26'."""
    return f"{year - 1}–{str(year)[2:]}"


def format_number(n: float | int | None) -> str:
    return "–" if n is None else f"{n:,}"


def format_money(n: float | int | None, style: str = "long") -> str:
    """140559783 -> '$140.6 million' (long) or '$140.6M' (short). Short rounds
    thousands too ('$139K'); long gives amounts under a million in full."""
    if n is None:
        return "–"
    if n < 0:
        return "−" + format_money(-n, style)
    if abs(n) >= 1_000_000:
        amount = f"{n / 1_000_000:,.1f}"
        return f"${amount}M" if style == "short" else _("${amount} million").format(amount=amount)
    if style == "short" and abs(n) >= 10_000:
        return f"${n / 1000:,.0f}K"
    return f"${n:,.0f}"


def format_month_long(value: str) -> str:
    """'2028-01' -> 'January 2028'."""
    return month_year(date.fromisoformat(value + "-01"))


def format_month(value: str) -> str:
    """'2028-01' -> 'Jan 2028'."""
    return month_year(date.fromisoformat(value + "-01"), short=True)


SAFE_HREF = re.compile(r"(https?://|mailto:)", re.I)


def render_markdown(text: str) -> Markup:
    """Render model-written Markdown safely: escape any HTML first, keep only
    web and mail links (no javascript: and the like), drop images (they would
    load from other sites), and demote headings below the page's own h2."""
    html = markdown.markdown(escape(text), extensions=["sane_lists"])
    html = re.sub(r'<a href="([^"]*)"[^>]*>(.*?)</a>',
                  lambda m: m.group(0) if SAFE_HREF.match(m.group(1)) else m.group(2), html, flags=re.S)
    html = re.sub(r'<img [^>]*?alt="([^"]*)"[^>]*>|<img [^>]*>', lambda m: m.group(1) or "", html)
    for level in (3, 2, 1):
        html = html.replace(f"<h{level}>", f"<h{level + 2}>").replace(f"</h{level}>", f"</h{level + 2}>")
    return Markup(html)


def in_english(value):
    """Text written in English (an AI summary not translated yet), marked as English on another
    language's page so screen readers pronounce it as English."""
    if i18n.language() == "en" or value in (None, ""):
        return value
    return Markup('<span lang="en">{}</span>').format(value)


def in_english_block(html: Markup) -> Markup:
    """A block of English (a document's full text), marked as English on another language's page."""
    return html if i18n.language() == "en" else Markup('<div lang="en">{}</div>').format(html)


def format_timestamp(value: str) -> str:
    return plain_date(datetime.fromisoformat(value).date())


# ---- Data ------------------------------------------------------------------

# Sorting recorded decisions for residents. Fixed rules, checked against the
# real minutes: a committee's recommendation is not a final decision, and
# procedural steps (approving minutes, continuing or closing a hearing,
# referring an item) are folded away.
RECOMMENDED = re.compile(r"\brecommend(?:s|ed|ing)?\b(?!\s+by\b)", re.I)
AS_RECOMMENDED = re.compile(r"\bas recommended\b", re.I)
PROCEDURAL = [re.compile(p, re.I) for p in (
    r"\b(?:approv|accept)\w*\b[^.;]*\bminutes\b",
    r"^\s*(?:the \w+(?: \w+){0,5} )?(?:voted (?:\d+[-–]\d+ |unanimously )?to )?(?:continu|table|postpon|defer)\w*\b",
    r"\bwithdr[ae]w\w*\b",
    r"\bno (?:committee )?recommendation\b",
    r"^\s*it was determined that a\b[^.;]*\bmeeting\b",
    r"\b(?:enter|go|went|convene)\w*\b[^.;]*\bexecutive session\b",
    r"\bclosed the public hearing\b",
    r"^\s*(?:the \w+(?: \w+){0,5} )?(?:voted (?:\d+[-–]\d+ )?to )?refer(?:red)?\b",
    r"\badjourn",
)]
PUBLIC_HEARING = re.compile(r"\bpublic hearing", re.I)
MONEY = re.compile(r"\$(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s(?:million|billion|thousand))?")


# A committee's recommendation, without the wording its heading already says:
# "Voted 3 in favor, 0 opposed to recommend that the City Council approve X"
# becomes "Approve X. (3–0)". Only standard wording is trimmed; anything else,
# including a vote not to recommend, is shown exactly as written.
VOTE = r"(?:(\d{1,2}) in favor, (\d{1,2}) opposed|\b(\d{1,2})[-–](\d{1,2})\b(?![\d/]))"
REC_LEAD = re.compile(
    r"^(?:the [\w&'.,\s]{3,60}? committee )?(?:voted (?:by roll call )?(?:" + VOTE + r"|unanimously)?,? ?to recommend"
    r"|recommend(?:ed|s)?)(?:,? (?:" + VOTE + r"),?)?(?: that)?(?: the city council| to the city council)?(?: to)?(?: the city)? ", re.I)
REC_TAIL = re.compile(r",?\s*(?:—\s*)?(?:voted )?(?:" + VOTE + r")(?:[^.;]{0,40})?\.?$", re.I)
REC_NEGATIVE = re.compile(r"\bnot to recommend|\bno (?:committee )?recommendation|\brecommend(?:ed|s)? against\b", re.I)
PLAIN_VERBS = {"approving": "approve", "accepting": "accept", "appointing": "appoint", "amending": "amend",
               "confirming": "confirm", "allowing": "allow", "adopting": "adopt", "granting": "grant",
               "authorizing": "authorize", "permitting": "permit", "denying": "deny", "transferring": "transfer"}


def _vote(match: re.Match) -> str | None:
    counts = [g for g in match.groups() if g is not None][:2]
    return f"{counts[0]}–{counts[1]}" if len(counts) == 2 else None


def tidy_recommendation(text: str) -> str:
    """The recommended action first, with the vote at the end, when the wording is standard."""
    lead = REC_LEAD.match(text)
    if not lead or REC_NEGATIVE.search(text):
        return text
    vote, rest = _vote(lead), text[lead.end():]
    tail = REC_TAIL.search(rest)
    if tail:
        vote, rest = vote or _vote(tail), rest[:tail.start()]
    rest = rest.strip().rstrip(".,;")
    if not rest:
        return text
    first, _, after = rest.partition(" ")
    first = PLAIN_VERBS.get(first.lower(), first)
    return f"{first[:1].upper()}{first[1:]}{' ' + after if after else ''}." + (f" ({vote})" if vote else "")


def decision_text(text: str) -> Markup:
    """A decision for reading: "TTE" (term to expire) spelled out, dollar amounts in bold."""
    return emphasize_money(re.sub(r"\bTTE\b", "term ends", text))


def decision_kind(text: str) -> str:
    """ "recommended", "procedural", or "decided"."""
    if RECOMMENDED.search(text) and not AS_RECOMMENDED.search(text):
        return "recommended"
    if any(p.search(text) for p in PROCEDURAL):
        return "procedural"
    return "decided"


def emphasize_money(text: str) -> Markup:
    """Dollar amounts in bold, so money stands out when scanning."""
    return Markup(MONEY.sub(lambda m: f"<strong>{m.group(0)}</strong>", str(escape(text))))


def glossary_for(meeting: dict, entries: list[dict]) -> list[dict]:
    """Glossary entries whose term appears in a meeting's summaries or document text.

    Acronyms match exactly; other terms ignore case. An entry with "bodies"
    applies only to those boards, for terms that mean different things elsewhere.
    """
    texts = []
    for doc in (meeting.get("preview"), meeting.get("minutes_summary")):
        if doc:
            texts += [doc.get("summary") or "", doc_text(doc), *doc.get("items", []), *doc.get("decisions", [])]
    text = "\n".join(texts)
    found = []
    for e in entries:
        if e.get("bodies") and meeting["body"] not in e["bodies"]:
            continue
        pattern = rf"(?<![\w.]){re.escape(e['term'])}(?!\w)"
        if re.search(pattern, text, 0 if e["term"].isupper() or "." in e["term"] else re.I):
            found.append(e)
    return found


# Where each source lists a meeting, for sentences like "Not listed on the city calendar".
LISTINGS = {"civicclerk": N_("city's meeting portal"), "agendacenter": N_("city's Agenda Center"),
            "finalsite": N_("school district's website")}
CALENDAR = N_("city calendar")


def from_agenda(m: dict) -> None:
    """Fill in a meeting's time and place from its agenda summary when its listing has neither,
    as an Agenda Center's doesn't. m["from_agenda"] says so, for the page to say where they're from."""
    preview = m.get("preview") or {}
    m["from_agenda"] = False
    if not m["start_time"] and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", preview.get("start_time") or ""):
        m["start_time"] = preview["start_time"]
        m["from_agenda"] = True
    if not (m["location_name"] or m["address"] or m["location"]) and (preview.get("location") or "").strip():
        m["location"] = preview["location"].strip()
        m["from_agenda"] = True


def in_language(data_dir: Path, record: dict | None, kind: str, doc: dict | None) -> dict | None:
    """A summary as the language being built shows it: its translation, when one passed the
    check (pipeline/translate.py), or the English, marked "english" for the page to say so."""
    lang = i18n.language()
    if not record or lang == "en":
        return record
    translated = translate.shown(data_dir, lang, doc["sha256"], record, kind)
    if translated:
        return {**record, **{f: translated[f] for f in translate.FIELDS[kind]}, "translated": True}
    return {**record, "english": True}


def english_attr(record: dict | None) -> Markup:
    """' lang="en"' for an element holding a summary shown in English on another language's page."""
    return Markup(' lang="en"') if record and record.get("english") else Markup("")


def load_meetings(data_dir: Path, today: date, summary_model: str | None = None, glossary: list[dict] | None = None) -> dict:
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    status_path = data_dir / "meetings" / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else None

    meetings = sorted(store.values(), key=lambda m: (m["date"], m.get("start_time") or "", m["body"], m["id"]))
    optional = dict(start_time=None, end_time=None, location="", location_name="", address="",
                    remote_url=None, agendas=[], history=[], status="scheduled", special=False, listed=True,
                    source="calendar", source_url=None)
    for m in meetings:
        for key, default in optional.items():
            m.setdefault(key, default)
        m.setdefault("documents_url", None)
        # Where the meeting is listed, in sentences like "Removed from the city calendar".
        m["listing"] = _(LISTINGS.get(m["source"], CALENDAR))
        m["url"] = f"/meetings/{m['slug']}/"
        # The board's name as the city writes it; m["body"] is the one shown, translated for another language.
        m["body_en"] = m["body"]
        m["body_slug"] = slugify(m["body"])
        m["body_url"] = f"/meetings/boards/{m['body_slug']}/"
        m["agenda"] = m["agendas"][-1] if m.get("agendas") else None
        # The latest saved summary is shown, even one from an older prompt or
        # model; the next summarize run replaces those.
        m["preview"] = summarize.cached(data_dir, m["agenda"]["sha256"], summary_model, "agenda", current=False) if m["agenda"] and summary_model else None
        from_agenda(m)
        m.setdefault("minutes", [])
        m["minutes_doc"] = m["minutes"][-1] if m["minutes"] else None
        m["minutes_summary"] = (
            summarize.cached(data_dir, m["minutes_doc"]["sha256"], summary_model, "minutes", current=False)
            if m["minutes_doc"] and summary_model else None
        )
        m["minutes_too_large"] = bool(m["minutes_doc"]) and summarize.too_large(m["minutes_doc"])
        # Decisions are sorted, hearings found, and glossary terms matched in the English;
        # another language's pages show its translation where there is one.
        english = {"preview": m["preview"], "minutes_summary": m["minutes_summary"], "body": m["body"]}
        m["preview"] = in_language(data_dir, m["preview"], "agenda", m["agenda"])
        m["minutes_summary"] = in_language(data_dir, m["minutes_summary"], "minutes", m["minutes_doc"])
        ms_en, ms = english["minutes_summary"], m["minutes_summary"]
        sorted_decisions = {"decided": [], "recommended": [], "procedural": []}
        if ms_en and ms_en.get("is_minutes", True):
            for d, shown in zip(ms_en.get("decisions", []), ms.get("decisions", [])):
                sorted_decisions[decision_kind(d)].append(shown)
        m["decisions"] = sorted_decisions
        # Whether the decisions are shown in English on another language's page.
        m["decisions_english"] = bool(ms and ms.get("english"))
        pv_en = english["preview"]
        m["public_hearing"] = bool(pv_en) and bool(PUBLIC_HEARING.search(
            " ".join([pv_en.get("summary") or "", doc_text(pv_en), *pv_en.get("items", [])])))
        m["preview_line"] = preview_line(m)
        m["glossary"] = glossary_for(english, glossary or [])

    # A board that meets more than once in a day (a hearing, then its regular
    # meeting) needs each meeting told apart in page titles and lists: by start
    # time when each has its own, or else by order ("1 of 2"), as for a town
    # whose listings have no times.
    per_day = defaultdict(list)
    for m in meetings:
        per_day[(m["date"], m["body_slug"])].append(m)
    for group in per_day.values():
        times = [m.get("start_time") for m in group]
        by_time = all(times) and len(set(times)) == len(group)
        for i, m in enumerate(sorted(group, key=lambda m: (m.get("start_time") or "", len(m["id"]), m["id"])), 1):
            m["same_day"] = len(group) > 1
            # Translators: which of a board's meetings on one day, such as "1 of 2".
            m["day_part"] = _("{i} of {n}").format(i=i, n=len(group)) if m["same_day"] and not by_time else None

    today_s = today.isoformat()
    upcoming = [m for m in meetings if m["date"] >= today_s]
    past = [m for m in meetings if m["date"] < today_s][::-1]

    boards = defaultdict(list)
    for m in meetings:
        boards[m["body_slug"]].append(m)
    board_list = sorted(
        ({"slug": s, "name": ms[-1]["body"], "name_en": ms[-1]["body"], "url": ms[0]["body_url"], "meetings": ms[::-1],
          "next": next((m for m in ms if m["date"] >= today_s), None)} for s, ms in boards.items()),
        key=lambda b: b["name"].lower(),
    )
    week_end = (today + timedelta(days=7)).isoformat()
    # Meetings whose minutes record decisions, newest first. Minutes that turned
    # out to be another document (an agenda filed as minutes) are left out.
    decided = [m for m in past if m["decisions"]["decided"] or m["decisions"]["recommended"]]
    return {
        "all": meetings,
        "upcoming": upcoming,
        "this_week": [m for m in upcoming if m["date"] < week_end],
        "past": past,
        "decided": decided,
        # For the home page: meetings that made at least one final decision.
        "final": [m for m in decided if m["decisions"]["decided"]],
        "boards": board_list,
        "status": status,
        "tracking_since": min((m["first_seen"] for m in meetings), default=None),
    }


def meeting_links(config: dict) -> dict:
    """The city's own meeting pages, linked from this site's, and whether the
    town collects agendas and minutes ([meetings] documents, default true). The
    defaults are a CivicPlus site's addresses."""
    m = config.get("meetings", {})
    base = m.get("base_url", "").rstrip("/")
    return {
        "calendar": m.get("calendar_url") or (f"{base}/calendar.aspx" if base else None),
        "portal": m.get("civicclerk", {}).get("portal_url"),
        "archive": m.get("archive_url") or (f"{base}/Archive.aspx" if base else None),
        "archive_name": m.get("archive_name", _("city's Archive Center")),
        "notify": m.get("notify_url") or (f"{base}/list.aspx" if base else None),
        "governing_body": m.get("governing_body", "City Council"),
        "documents": m.get("documents", True),
    }


# Pages about agendas and minutes, left out for a town that doesn't collect them yet.
DOCUMENT_PAGES = {"meetings/decisions/index.html", "meetings/search/index.html"}


def doc_text(doc: dict | None) -> str:
    """A document's text for search, the street lookup, and the glossary: its readable
    text, or, before it has one, its PDF's plain text."""
    return (doc or {}).get("transcript") or (doc or {}).get("plain_text") or ""


def plain_text(transcript: str | None) -> str:
    """Markdown transcript -> plain lines, for search."""
    lines = (re.sub(r"\s+", " ", re.sub(r"[*_`#>|]+", " ", line)).strip() for line in (transcript or "").splitlines())
    return "\n".join(line for line in lines if line)


def search_index(meetings: list[dict], prefix: str = "") -> list[dict]:
    """Every meeting's board, date, and document text, for /meetings/search/. prefix is
    the language's (/es) for its links."""
    rows = []
    for m in meetings:
        docs = []
        if m["preview"]:
            docs.append({"kind": _("Agenda"), "text": plain_text(doc_text(m["preview"]))})
        ms = m["minutes_summary"]
        if ms:
            docs.append({"kind": _("Minutes") if ms.get("is_minutes", True) else _("Agenda"),
                         "text": plain_text(doc_text(ms))})
        # Another language's search also finds the meeting by its translated summaries.
        for kind, record in (("agenda", m["preview"]), ("minutes", m["minutes_summary"])):
            if record and record.get("translated"):
                text = "\n".join(line for f in translate.FIELDS[kind]
                                  for line in ([record[f]] if isinstance(record[f], str) else record[f]) if line)
                docs.append({"kind": _("Summary"), "text": text})
        rows.append({"url": prefix + m["url"], "board": m["body"], "date": m["date"],
                     "date_text": format_date(m["date"]), "docs": [d for d in docs if d["text"]]})
    return rows[::-1]


def group_by(meetings: list[dict], period: str) -> list[tuple]:
    """Group meetings (already in order) by "date" (YYYY-MM-DD) or "month" (YYYY-MM-01)."""
    groups: dict = {}
    for m in meetings:
        key = m["date"] if period == "date" else m["date"][:7] + "-01"
        groups.setdefault(key, []).append(m)
    return list(groups.items())


def change_text(diff: float, unit: str, since: str, digits: int = 0) -> str:
    """'↑ 7 from last week' / 'No change from last week'. Neutral wording, no judgment.
    unit follows the amount ("%", " pts")."""
    if round(diff, digits) == 0:
        # Translators: {since} is a time, such as "last week" or "August 2025".
        return _("No change from {since}").format(since=since)
    arrow = "↑" if diff > 0 else "↓"
    amount = f"{abs(diff):,.{digits}f}"
    # Translators: such as "↑ 7 from last week", or "↓ 0.4 pts from August 2025".
    return _("{arrow} {amount}{unit} from {since}").format(arrow=arrow, amount=amount, unit=unit, since=since)


def list_text(items: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b, and c'; ['a', 'b'] -> 'a and b'."""
    if len(items) < 2:
        return "".join(items)
    if len(items) == 2:
        # Translators: a list of two things, such as "agenda items and building permits".
        return _("{first} and {last}").format(first=items[0], last=items[1])
    # Translators: the end of a list of three or more, such as "agenda items, building permits, and 311 requests".
    return _("{others}, and {last}").format(others=", ".join(items[:-1]), last=items[-1])


def clip(text: str, limit: int = 140) -> str:
    """Shorten to a whole word within limit characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:—– ") + "…"


def preview_line(meeting: dict) -> str | None:
    """One line for meeting lists: what the meeting is about, never the board,
    date, or time (those are already shown). Prefers the AI headline; older
    summaries without one fall back to their own items or decisions."""
    minutes, agenda = meeting.get("minutes_summary"), meeting.get("preview")
    if minutes and minutes.get("is_minutes", True):
        if minutes.get("headline"):
            return in_english(minutes["headline"]) if minutes.get("english") else minutes["headline"]
        if minutes.get("decisions"):
            final = meeting["decisions"]["decided"] if "decisions" in meeting else []
            line = clip((final or minutes["decisions"])[0])
            return in_english(line) if minutes.get("english") else line
    if agenda:
        line = agenda.get("headline") or (clip("; ".join(i.rstrip(".") for i in agenda["items"][:4]) + ".")
                                          if agenda.get("items") else None)
        return in_english(line) if line and agenda.get("english") else line
    return None


def headline_numbers(config: dict, data_dir: Path, scorecard: dict | None) -> list[dict]:
    """The home page's headline row. Each number links to where it comes from."""
    numbers = []
    if scorecard:
        backlog = scorecard["backlog"]
        no_update = backlog.get("no_update", {}).get("count")
        numbers.append({
            "label": _("Open 311 requests"), "value": f"{backlog['open']:,}", "href": "/311/#open",
            "change": change_text(backlog["open"] - backlog.get("open_week_ago", backlog["open"]), "", _("last week")),
            "note": ngettext("{n} with no update in over a year", "{n} with no update in over a year",
                             no_update).format(n=f"{no_update:,}") if no_update else "",
        })
        overall = scorecard["overall"]
        # The median leaves out requests never acknowledged, so show how many were.
        change = _("Median, past 12 months")
        if overall.get("checked"):
            change += " · " + _("{percent}% of requests were acknowledged").format(
                percent=round(overall["acknowledged"] / overall["checked"] * 100))
        numbers.append({
            "label": _("Typical time for the city to acknowledge a request"),
            "value": format_duration(overall["time_to_acknowledge"]["median"]), "href": "/311/#speed", "change": change,
        })
    tax_path = data_dir / "finance" / "tax_bill.json"
    state = states.for_town(config)
    if state.source("tax_bill", config) and tax_path.exists():
        tax = json.loads(tax_path.read_text(encoding="utf-8"))
        latest, prior = tax["years"][-1], (tax["years"][-2] if len(tax["years"]) > 1 else None)
        change = ""
        if prior:
            pct = (latest["average_bill"] - prior["average_bill"]) / prior["average_bill"] * 100
            change = change_text(pct, "%", _("last year"), 1)
        # New Hampshire's records are by tax year, Massachusetts's by fiscal year.
        period = (_("Tax year {year}").format(year=latest["tax_year"]) if "tax_year" in latest
                  else _("Fiscal year {year}").format(year=latest["fiscal_year"]))
        # A calculated figure links to the page that says how, where the town has it.
        explained = latest.get("calculated") and any(s["slug"] == "budget" for s in config["sections"])
        numbers.append({
            "label": _("Average single-family tax bill"), "value": f"${latest['average_bill']:,}",
            "href": "/budget/#tax-bill" if explained else tax["source_url"], "change": change,
            "source": period + " · " + _(state.tax_source),
        })
    labor_path = data_dir / "labor" / "unemployment.json"
    if "labor" in config and labor_path.exists():
        labor = json.loads(labor_path.read_text(encoding="utf-8"))
        latest = labor["months"][-1]
        month = date(latest["year"], latest["month"], 1)
        year_ago = next((m for m in labor["months"] if m["year"] == latest["year"] - 1 and m["month"] == latest["month"]), None)
        numbers.append({
            "label": _("Unemployment rate"), "value": f"{latest['rate']:.1f}%", "href": labor["source_url"],
            # City rates are not seasonally adjusted: compare with the same month a year earlier.
            # Translators: " pts" follows a change in percentage points, such as "↓ 0.4 pts".
            "change": change_text(latest["rate"] - year_ago["rate"], _(" pts"), month_year(month.replace(year=month.year - 1)), 1) if year_ago else "",
            "source": (_("{month} (preliminary)").format(month=month_year(month)) if latest.get("preliminary") else month_year(month))
                      + " · " + _("U.S. Bureau of Labor Statistics"),
        })
    return numbers


def map_points(sc: dict | None) -> dict:
    """Points for the 311 maps. The pages list the same places as text."""
    if not sc:
        return {"recent": [], "repeats": []}
    recent = [{
        "lat": r["lat"], "lng": r["lng"], "title": r["category"], "url": r["url"],
        "text": _("{address}. Submitted {date}.").format(address=r["address"] or _("No street address"),
                                                        date=format_date(r["created_at"][:10], "plain")),
    } for r in sc.get("recent_open", {}).get("requests", []) if r.get("lat") is not None]
    repeats = [{
        "lat": p["lat"], "lng": p["lng"], "title": p["address"] or _("No street address"),
        # Translators: a place on the map of repeated problems, such as "Potholes. 3 requests, 2 after an earlier one was closed."
        "text": ngettext("{category}. {n} request, {again} after an earlier one was closed.",
                         "{category}. {n} requests, {again} after an earlier one was closed.", p["reports"]).format(
            category=p["category"], n=f"{p['reports']:,}", again=f"{p['again_after_close']:,}"),
        "url": p["requests"][-1]["url"], "link": _("Latest request on SeeClickFix"),
        "size": 5 + min(p["again_after_close"], 8),
    } for p in sc.get("repeats", {}).get("places", [])]
    return {"recent": recent, "repeats": repeats}


# ---- A town's own text in another language ------------------------------------

# Section names every town uses, translated with the engine's own wording, so a town's
# [strings.<language>] needn't repeat them.
SECTION_NAMES = (N_("Meetings"), N_("311 Requests"), N_("311"), N_("Schools"), N_("City budget"), N_("Budget"),
                 N_("Housing"), N_("Who represents you"), N_("Officials"), N_("About"))


class TownStrings:
    """A town's own text in the language being built, from the first of:
    - its config's [strings.<language>] table (each English text and its translation);
    - the engine's own translation of what many towns share: section names, common boards,
      roles, and seats (pipeline/common_strings.py), and numbered seats ("Ward 3");
    - a machine draft, made by a town's run and not yet reviewed (drafts: pipeline/translate.py).

    Text with none of these is shown in English and noted: the config's own text in missing
    (a site in that language isn't built without it: see main()), and names that come from
    the city's data, which change as the city adds boards and 311 categories, in missing_data
    (the next run drafts them). Texts shown from drafts are noted in drafted."""

    def __init__(self, config: dict, lang: str, drafts: dict | None = None):
        self.english = lang == "en"
        self.strings = config.get("strings", {}).get(lang, {})
        self.drafts = drafts or {}
        self.missing: set[str] = set()
        self.missing_data: set[str] = set()
        self.drafted: set[str] = set()

    def translate(self, text, missing: set):
        if self.english or not isinstance(text, str) or not text:
            return text
        if text in self.strings:
            return self.strings[text]
        if (text in SECTION_NAMES or text in common_strings.TEXTS) and i18n.translated(text):
            return _(text)
        for pattern, wording in common_strings.NUMBERED:
            numbered = pattern.fullmatch(text)
            if numbered and i18n.translated(wording):
                return _(wording).format(n=numbered.group(1))
        if text in self.drafts:
            self.drafted.add(text)
            return self.drafts[text]
        missing.add(text)
        return text

    def __call__(self, text):
        """Text from the town's config."""
        return self.translate(text, self.missing)

    def data(self, text):
        """A name from the city's data, such as a 311 category."""
        return self.translate(text, self.missing_data)

    def board(self, name: str) -> str:
        """A board's name, with its official English name after it so readers can match
        it to the city's notices: "Concejo Municipal (City Council)"."""
        translated = self.translate(name, self.missing_data)
        return name if translated == name else f"{translated} ({name})"


def needed_texts(config: dict, data_dir: Path, lang: str, built_at: datetime) -> dict:
    """A town's texts that a build in this language would show in English, found without building:
    {"config": the config's own text, "data": names from the city's data}. The same steps as
    build_language() (a test keeps them so), for the run to draft them before the build."""
    tr = TownStrings(config, lang, translate.drafts(data_dir, lang))
    with i18n.use(lang):
        localize_config(config, tr)
        meetings = load_meetings(data_dir, built_at.date(), config.get("summaries", {}).get("model"), config.get("glossary", []))
        for m in meetings["all"]:
            tr.board(m["body"])
        for b in meetings["boards"]:
            tr.board(b["name"])
        if "governing_body" not in config.get("meetings", {}):
            tr.board(meeting_links(config)["governing_body"])
        folders = {s["slug"] for s in config["sections"]} | SHARED_FOLDERS
        scorecard = data_dir / "311" / "scorecard.json"
        if "311" in folders and scorecard.exists():
            localize_categories(json.loads(scorecard.read_text(encoding="utf-8")), tr)
        requests = data_dir / "311" / "requests.json"
        if "seeclickfix" in config and requests.exists():
            localize_categories(list(json.loads(requests.read_text(encoding="utf-8")).values()), tr)
        if "officials" in folders:
            localize_officials(officials_mod.load(config, data_dir, {b["name_en"]: b["url"] for b in meetings["boards"]}), tr)
    return {"config": sorted(tr.missing), "data": sorted(tr.missing_data)}


def localize_config(config: dict, tr: TownStrings) -> dict:
    """The config with the text it shows on pages in the language being built. Names the
    build matches against data (boards, glossary terms, slugs) stay as they are."""
    config = copy.deepcopy(config)
    for key in ("tagline", "masthead"):
        if key in config["site"]:
            config["site"][key] = tr(config["site"][key])
    for section in config["sections"]:
        for key in ("title", "nav", "summary"):
            if key in section:
                section[key] = tr(section[key])
    for part in config.get("participation", {}).values():
        for key in ("comment", "source_title", "video_title"):
            if key in part:
                part[key] = tr(part[key])
    for entry in config.get("glossary", []):
        entry["definition"] = tr(entry["definition"])
    for source in config.get("freshness", {}).get("sources", []):
        source["label"] = tr(source["label"])
    meetings = config.get("meetings", {})
    if "archive_name" in meetings:
        meetings["archive_name"] = tr(meetings["archive_name"])
    if "governing_body" in meetings:
        meetings["governing_body"] = tr.board(meetings["governing_body"])
    return config


def localize_officials(officials: dict, tr: TownStrings) -> None:
    """The Officials page's bodies, seats, and roles in the language being built."""
    def member(m: dict) -> None:
        for key in ("seat", "role"):
            if m.get(key):
                m[key] = tr(m[key])
    for body in officials["bodies"]:
        body["name"] = tr.board(body["name"])
        if body.get("note"):
            body["note"] = tr(body["note"])
        for m in body["members"]:
            member(m)
    for ward in officials["wards"]:
        for m in ward["members"]:
            m["body"] = tr.board(m["body"])
            member(m)


def localize_categories(value, tr: TownStrings) -> None:
    """Every 311 category name in the scorecard (or a list of requests), in the language being built."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "category" and isinstance(item, str):
                value[key] = tr.data(item)
            else:
                localize_categories(item, tr)
    elif isinstance(value, list):
        for item in value:
            localize_categories(item, tr)


# ---- Build -----------------------------------------------------------------

def languages(config: dict) -> list[str]:
    """The languages a town's site is built in ([site] languages): English, then any others."""
    langs = config["site"].get("languages", ["en"])
    unknown = [lang for lang in langs if lang not in i18n.LANGUAGES]
    if not langs or langs[0] != "en" or unknown or len(set(langs)) != len(langs):
        raise SystemExit(f"[site] languages in config/{config['slug']}.toml must start with \"en\", list each language once, "
                         f"and use only {', '.join(i18n.LANGUAGES)}" + (f" (not {', '.join(unknown)})" if unknown else "") + ".")
    return langs


def build(town: str, out_dir: Path, data_dir: Path = DATA_DIR, now: datetime | None = None,
          town_static: Path = TOWN_STATIC_DIR, missing: dict | None = None) -> list[str]:
    """Render every page in each of the town's languages, and write supporting files. Returns the page URLs built.

    English pages are at the site's root; another language's are under /<language>/
    (/es/meetings/), with the same data. Files that aren't pages (static files, downloads,
    saved PDFs, the feed) are written once, with the English. missing, if given, gets each
    other language's town texts that have no translation in the config's [strings.<language>]:
    {"config": the config's own text, "data": names from the city's data}."""
    config = load_config(town)
    built_at = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    langs = languages(config)
    urls = []
    for lang in langs:
        tr = TownStrings(config, lang, translate.drafts(data_dir, lang))
        with i18n.use(lang):
            urls += build_language(config, lang, langs, out_dir, data_dir, built_at, town_static, tr)
        if missing is not None and (tr.missing or tr.missing_data or tr.drafted):
            missing[lang] = {"config": sorted(tr.missing), "data": sorted(tr.missing_data), "drafted": sorted(tr.drafted)}
    write_support_files(out_dir, config["site"], f"https://{config['site']['domain']}", urls, built_at)
    return urls


def build_language(config: dict, lang: str, langs: list[str], out_dir: Path, data_dir: Path, built_at: datetime,
                   town_static: Path, tr: TownStrings) -> list[str]:
    """One language's pages, and with English the files every language shares."""
    town = config["slug"]
    site = config["site"]
    # Where this language's pages are: "" for English, "/es" for Spanish.
    prefix = "" if lang == "en" else f"/{lang}"
    english = lang == "en"
    if not english:
        config = localize_config(config, tr)
        site = config["site"]
    state = states.for_town(config)
    for section in config["sections"]:
        kind = states.SECTIONS.get(section["slug"])
        if kind and not (state.source(kind, config) and (STATES_DIR / state.templates / f"{section['slug']}.html").exists()):
            raise SystemExit(f"The {section['slug']} section needs {state.name}'s {states.KINDS[kind].lower()}: "
                             f"a source in pipeline/states/{state.templates}/, its table in config/{town}.toml, "
                             f"and site/states/{state.templates}/{section['slug']}.html.")
    base_url = f"https://{site['domain']}"
    meetings = load_meetings(data_dir, built_at.date(), config.get("summaries", {}).get("model"), config.get("glossary", []))
    for m in meetings["all"]:
        m["body"] = tr.board(m["body"])
    for b in meetings["boards"]:
        b["name"] = tr.board(b["name"])
    # A section folder is built only for a town that lists the section in its
    # config, and data for a section the town doesn't list is left out.
    built_folders = {s["slug"] for s in config["sections"]} | SHARED_FOLDERS
    links = meeting_links(config)
    if "governing_body" not in config.get("meetings", {}):
        # The default name, as the town's config would have been translated.
        links["governing_body"] = tr.board(links["governing_body"])
    # Agendas and minutes: saved and searchable, or (for a town with a calendar only) not yet.
    documents = "meetings" in config and links["documents"]
    # What the street lookup covers, as a phrase: "agenda items, building permits, and 311 requests".
    # A town with none of these sources has no street lookup.
    street_sources = list_text((([_("agenda items")] if documents else []) + ([_("building permits")] if "permits" in config else [])
                                + ([_("311 requests")] if "seeclickfix" in config else [])))
    if not street_sources:
        built_folders.discard("streets")

    def section_data(slug: str, path: str) -> dict | None:
        file = data_dir / path
        return json.loads(file.read_text(encoding="utf-8")) if slug in built_folders and file.exists() else None

    scorecard = section_data("311", "311/scorecard.json")
    localize_categories(scorecard, tr)
    schools = section_data("schools", "schools/schools.json")
    budget = section_data("budget", "finance/budget.json")
    tax_bill = section_data("budget", "finance/tax_bill.json")
    housing = section_data("housing", "housing/housing.json")
    # Housing figures from the town's state's own sources (pipeline/states/), shown only for a town that has them.
    state_housing = state.housing_parts(config)
    if housing and state.housing_module():
        for key in state.housing_module().keys:
            housing[key] = housing.get(key) if key in state_housing else None

    # The engine's static files, then the town's own on top (its share image, or its own icon).
    out_static = out_dir / "static"
    if english:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        shutil.copytree(STATIC_DIR, out_static)
        if town_static.is_dir():
            shutil.copytree(town_static, out_static, dirs_exist_ok=True)
        if config["site"].get("colors"):
            palette = "\n".join(f"  --{name.replace('_', '-')}: {value};" for name, value in colors(config).items())
            with (out_static / "css" / "site.css").open("a", encoding="utf-8") as f:
                f.write(f"\n/* {site['name']} colors, from [site.colors] in config/{town}.toml. */\n:root {{\n{palette}\n}}\n")

    env = Environment(
        loader=FileSystemLoader([SITE_DIR / "templates", PAGES_DIR, STATES_DIR]),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        # The site's wording, translated for a language other than English (pipeline/i18n.py).
        extensions=["jinja2.ext.i18n"],
    )
    env.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True,
                                  pgettext=i18n.pgettext, npgettext=i18n.npgettext)
    # A {% trans %} block's lines are joined with single spaces, as the browser shows them.
    env.policies["ext.i18n.trimmed"] = True
    env.filters.update(date=format_date, time=format_time, filesize=format_bytes, timestamp=format_timestamp,
                       duration=format_duration, number=format_number, money=format_money, month=format_month, month_long=format_month_long,
                       markdown=lambda t: in_english_block(render_markdown(t)), duration_cell=format_duration_cell,
                       street=lambda a: short_address(a, config["town"]["name"]),
                       model_name=model_name, capitalize_first=lambda t: Markup(t[:1].upper() + t[1:]),
                       school_year=school_year, money_bold=emphasize_money, decision=decision_text,
                       recommendation=lambda t: decision_text(tidy_recommendation(t)))
    env.globals["english_attr"] = english_attr
    # A label saved in a state's data in English (a budget function, a type of parcel), as shown.
    env.globals["data_label"] = i18n.gettext
    # Versioned asset URLs, so a browser never pairs new pages with an old cached stylesheet.
    css_version = hashlib.sha256((out_static / "css" / "site.css").read_bytes()).hexdigest()[:10]
    def versioned(path: str) -> str:
        """'/static/js/map.js' -> '/static/js/map.js?v=<hash>'."""
        digest = hashlib.sha256((out_static / path.removeprefix("/static/")).read_bytes()).hexdigest()[:10]
        return f"{path}?v={digest}"
    env.filters["versioned"] = versioned
    env.globals["report_link"] = lambda page_url, what: report_link(site, base_url + prefix, page_url, what)
    # A state's own template (site/states/<state>/<name>); a part a state doesn't have is included with "ignore missing".
    env.globals["state_template"] = lambda name: f"{state.templates}/{name}"
    # And the helpers its pages use.
    state_pages = state.pages_module()
    if state_pages:
        env.globals.update(state_pages.TEMPLATE_GLOBALS)
    # Saved agenda and minutes PDFs: in the site itself, or in the town's bucket (see pipeline/documents.py).
    env.globals["document_url"] = open_documents(config, data_dir).url
    env.globals.update(group_by=group_by, today=built_at.date().isoformat(), css_version=css_version,
                       change=lambda diff, since: change_text(diff, "", since))

    own_hosts = {site["domain"], "www." + site["domain"]}
    sections = config["sections"]
    # Newest first; the version in the URL changes whenever the text does.
    search_json = json.dumps(search_index(meetings["all"], prefix), ensure_ascii=False, separators=(",", ":"))
    search_url = f"{prefix}/meetings/search-index.json?v={hashlib.sha256(search_json.encode()).hexdigest()[:10]}"
    permits_path = data_dir / "permits" / "permits.json"
    permits = json.loads(permits_path.read_text(encoding="utf-8")) if "permits" in config and permits_path.exists() else None
    requests_path = data_dir / "311" / "requests.json"
    requests_311 = list(json.loads(requests_path.read_text(encoding="utf-8")).values()) if "seeclickfix" in config and requests_path.exists() else []
    localize_categories(requests_311, tr)
    streets = street_index(meetings["all"], (permits or {}).get("permits", []), requests_311, built_at.date(), config["town"], prefix)
    streets_json = json.dumps(streets, ensure_ascii=False, separators=(",", ":"))
    streets_url = f"{prefix}/streets/streets.json?v={hashlib.sha256(streets_json.encode()).hexdigest()[:10]}"
    # Who represents you: the Officials page, and its ward map's shapes.
    officials = wards_json = wards_url = None
    if "officials" in built_folders:
        officials = officials_mod.load(config, data_dir, {b["name_en"]: b["url"] for b in meetings["boards"]})
        localize_officials(officials, tr)
        ward_shapes = officials_mod.map_data(data_dir, config)
        if ward_shapes:
            wards_json = json.dumps(ward_shapes, separators=(",", ":"))
            wards_url = f"/officials/wards.json?v={hashlib.sha256(wards_json.encode()).hexdigest()[:10]}"
    share_path = out_static / "share" / f"{town}.png"
    # Versioned, so sites that cache link previews pick up a redrawn image.
    share_image = (f"{base_url}/static/share/{town}.png?v={hashlib.sha256(share_path.read_bytes()).hexdigest()[:10]}"
                   if share_path.exists() else None)
    # Where the ward boundaries come from, named on the 311 pages and the About page: from
    # [seeclickfix] or, for a town with only the Officials page's ward map, [officials].
    # The defaults are MassGIS's, which covers every Massachusetts municipality.
    sc = config.get("seeclickfix") or config.get("officials", {})
    wards = {"publisher": sc.get("wards_publisher", "MassGIS"), "year": sc.get("wards_year", 2022),
             "url": sc.get("wards_url", "https://gis.data.mass.gov/maps/aec5130790814ace94438d3bcf23cf9a")}
    common = dict(config=config, site=site, town=config["town"], state=state, state_housing=state_housing, sections=sections, share_image=share_image, search_url=search_url, wards=wards,
                  meeting_links=links, officials=officials, wards_url=wards_url,
                  streets_url=streets_url, street_sources=street_sources, street_example=example_street(streets), permits=permits, data_status=freshness.check(config, data_dir, built_at),
                  built_at=built_at, meetings=meetings, scorecard=scorecard, schools=schools, budget=budget, tax_bill=tax_bill, housing=housing,
                  headline=headline_numbers(config, data_dir, scorecard), map_points=map_points(scorecard))
    urls = []

    def render(template: str, url: str, **context) -> None:
        """Render a page. url is its English address (/meetings/); another language's is under its prefix."""
        section_slug = url.strip("/").split("/")[0] or None
        section = next((s for s in sections if s["slug"] == section_slug), None)
        # The same page in each of the site's languages, for hreflang links and the language switch.
        versions = [{"lang": code, "name": i18n.LANGUAGES[code], "path": ("" if code == "en" else f"/{code}") + url}
                    for code in langs] if len(langs) > 1 else []
        for v in versions:
            v["url"] = base_url + v["path"]
        html = env.get_template(template).render(
            **common, **context, section=section, page_url=url, canonical_url=base_url + prefix + url,
            lang=lang, versions=versions,
        )
        html = mark_new_tab_links(html, own_hosts)
        if prefix:
            html = localize_links(html, prefix)
        dest = out_dir / (prefix + url).lstrip("/")
        if url.endswith("/"):
            dest = dest / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        if url not in UNLISTED_PAGES:
            urls.append(prefix + url)

    for page_path in sorted(PAGES_DIR.rglob("*.html")):
        rel = page_path.relative_to(PAGES_DIR)
        if len(rel.parts) > 1 and rel.parts[0] not in built_folders:
            continue
        if rel.as_posix() in DOCUMENT_PAGES and not documents:
            continue
        render(rel.as_posix(), url_for(rel))

    if "meetings" in config:
        for m in meetings["all"]:
            render("meeting.html", m["url"], meeting=m)
        for b in meetings["boards"]:
            render("board.html", b["url"], board=b)
    if documents and english:
        write_csv(out_dir / "meetings" / "data" / "decisions.csv", ["meeting_date", "board", "kind", "decision", "meeting_url", "minutes_url"],
                  [[m["date"], m["body"], kind, d, base_url + m["url"], m["minutes_doc"]["source_url"]]
                   for m in meetings["decided"] for kind, ds in m["decisions"].items() for d in ds])
    if scorecard:
        populations = {w["ward"]: w for w in scorecard["by_ward"]}
        for w in scorecard.get("wards", []):
            if w["ward"] != "outside":
                render("ward.html", f"/311/ward/{w['ward']}/", ward={**populations.get(w["ward"], {}), **w})
        for c in scorecard.get("categories", []):
            render("category.html", f"/311/category/{c['slug']}/", category=c)
        if english:
            write_311_csvs(out_dir / "311" / "data", scorecard)
    if not english:
        # Every language's pages link the same downloads, PDFs, and feed, written with the English.
        if documents:
            (out_dir / prefix.lstrip("/") / "meetings").mkdir(parents=True, exist_ok=True)
            (out_dir / prefix.lstrip("/") / "meetings" / "search-index.json").write_text(search_json, encoding="utf-8")
        if "streets" in built_folders:
            (out_dir / prefix.lstrip("/") / "streets").mkdir(parents=True, exist_ok=True)
            (out_dir / prefix.lstrip("/") / "streets" / "streets.json").write_text(streets_json, encoding="utf-8")
        return urls
    if state_pages:
        # The downloadable tables behind the state's own pages.
        state_pages.write_files(out_dir, {"budget": budget, "schools": schools, "tax_bill": tax_bill})
    if housing and housing.get("permits"):
        write_csv(out_dir / "housing" / "data" / "permits.csv",
                  ["year", "homes", "in_1_unit_buildings", "in_2_unit_buildings", "in_3_4_unit_buildings",
                   "in_5_plus_unit_buildings", "partly_estimated"],
                  [[y["year"], y["units"], *y["by_size"].values(), y["estimated"]] for y in housing["permits"]["years"]])

    if documents:
        for folder in ("agendas", "minutes"):
            src = data_dir / "meetings" / folder
            if src.exists():
                shutil.copytree(src, out_dir / "meetings" / folder)
        (out_dir / "meetings" / "search-index.json").write_text(search_json, encoding="utf-8")
        write_feed(out_dir / "feed.xml", meetings["all"], config, base_url, built_at)
    if wards_json:
        (out_dir / "officials" / "wards.json").write_text(wards_json, encoding="utf-8")
    if "streets" in built_folders:
        (out_dir / "streets").mkdir(parents=True, exist_ok=True)
        (out_dir / "streets" / "streets.json").write_text(streets_json, encoding="utf-8")
    return urls


# An address alone on its line in this many meetings' documents is where they meet (street_index).
VENUE_MEETINGS = 3


def street_index(meetings: list[dict], permits: list[dict], requests: list[dict], today: date, town: dict,
                 prefix: str = "", limit: int = 30) -> dict:
    """Everything the site knows about each street: agenda and minutes mentions,
    building and demolition permits, and 311 requests from the past year. prefix is
    the language's (/es) for links to meetings."""
    streets: dict = defaultdict(lambda: {"meetings": [], "permits": [], "requests": []})

    def place(address: str) -> tuple[str, list[str]]:
        number = streets_mod.HOUSE_NUMBER.match(address.upper() + " ")
        return (number.group(0).strip() if number else ""), streets_mod.street_keys(address, town["name"])

    # Meeting places (City Hall, the high school library) head every agenda; they aren't news.
    venues = {(num, key) for m in meetings for num, keys in [place(m.get("address") or "")] for key in keys}

    def line_with(text: str, address: str) -> str:
        line = next((l for l in text.splitlines() if address in l), address)
        return clip(re.sub(r"[#*_>|]+", " ", line).strip(), 200)

    # A line naming the meeting room, or a mailing address ("2 Dale Ave, Gloucester, MA"),
    # is the document's header, not an agenda item.
    venue_line = re.compile(r"\b(?:conference room|meeting room|auditorium|council chambers?|city hall|held at)\b"
                            rf"|,\s*{re.escape(town['name'])},?\s*{re.escape(town['state_abbr'])}\b", re.I)
    # An address followed by another town and its state ("11 Azsr Ct, Halethorpe MD") is an
    # applicant's own address, not a street in this town.
    elsewhere = re.compile(r",\s*([A-Z][A-Za-z.'’]*(?:\s+[A-Z][A-Za-z.'’]*){0,2}),?\s+[A-Z]{2}\b")
    # An address alone on its line ("191 Cabot Street", "4 FAIRFIELD BOULEVARD", perhaps with the
    # town, state, and zip code) in the documents of several meetings is where a board meets or
    # its letterhead, not news, wherever else it appears.
    own_place = re.compile(rf"\b(?:{re.escape(town['name'])}|{re.escape(town['state_abbr'])})\b|[\W\d_]", re.I)
    alone = defaultdict(set)
    for m in meetings:
        for doc in (m["preview"], m["minutes_summary"]):
            text = doc_text(doc)
            for address in streets_mod.addresses_in(text):
                line, flat = (re.sub(r"\s+", " ", s) for s in (line_with(text, address), address))
                if not own_place.sub("", line.replace(flat, "", 1)):
                    num, keys = place(address)
                    for key in keys:
                        alone[(num, key)].add(m["url"])
    venues |= {where for where, urls in alone.items() if len(urls) >= VENUE_MEETINGS}

    def in_another_town(text: str, address: str) -> bool:
        line = line_with(text, address)
        after = elsewhere.match(line[line.find(address) + len(address):])
        return bool(after) and after.group(1).lower() != town["name"].lower()

    for m in meetings:
        for kind, doc in ((_("Agenda"), m["preview"]), (_("Minutes"), m["minutes_summary"])):
            text = doc_text(doc)
            for address in streets_mod.addresses_in(text):
                num, keys = place(address)
                if venue_line.search(line_with(text, address)) or in_another_town(text, address):
                    continue
                for key in keys:
                    if (num, key) in venues:
                        continue
                    entries = streets[key]["meetings"]
                    if not any(e["url"] == prefix + m["url"] and e["doc"] == kind for e in entries):
                        entries.append({"url": prefix + m["url"], "date": m["date"], "board": m["body"], "doc": kind,
                                        "line": line_with(text, address)})
    for p in permits:
        num, keys = place(p["address"])
        for key in keys:
            streets[key]["permits"].append({
                "date": p["submitted"], "address": (num + " " if num else "") + streets_mod.street_name(key),
                "type": p["type"].replace(" (2017-2023)", ""), "status": p["status"], "cost": p["cost"], "work": clip(p["work"], 160)})
    year_ago = (today - timedelta(days=365)).isoformat()
    for r in requests:
        if r.get("removed") or (r.get("created_at") or "") < year_ago:
            continue
        for key in streets_mod.street_keys(r.get("address", ""), town["name"]):
            streets[key]["requests"].append({
                "date": r["created_at"][:10], "category": r["category"], "status": r["status"],
                "address": short_address(r["address"], town["name"]), "url": f"https://seeclickfix.com/issues/{r['id']}"})
    out = {}
    for key, s in streets.items():
        entry = {"name": streets_mod.street_name(key)}
        for field, items in s.items():
            items.sort(key=lambda e: e["date"], reverse=True)
            entry[field] = items[:limit]
            entry[field + "_total"] = len(items)
        out[key] = entry
    return {"suffixes": streets_mod.SUFFIXES, "streets": dict(sorted(out.items()))}


def example_street(index: dict) -> str | None:
    """The town's own street with the most on it, for the street lookup's example: a
    street every town has can't be assumed, and this one is sure to show results."""
    totals = {s["name"]: sum(v for k, v in s.items() if k.endswith("_total")) for s in index["streets"].values()}
    return max(sorted(totals), key=lambda name: totals[name]) if totals else None


def report_link(site: dict, base_url: str, page_url: str, what: str) -> str:
    """A pre-filled correction message naming the page: email when the site has a
    contact address, otherwise a new issue on the public repository."""
    subject = _("Correction: {page}").format(page=what)
    body = (_("Page: {url}").format(url=base_url + page_url) + "\n\n" + _("What's wrong:") + "\n\n\n"
            + _("What it should say, and where you saw it (if you know):") + "\n")
    if site.get("contact_email"):
        return f"mailto:{site['contact_email']}?{urlencode({'subject': subject, 'body': body}, quote_via=quote)}"
    return f"{site['repo_url']}/issues/new?{urlencode({'title': subject, 'body': body, 'labels': 'correction'}, quote_via=quote)}"


def write_feed(path: Path, meetings: list[dict], config: dict, base_url: str, built_at: datetime, limit: int = 50) -> None:
    """RSS feed of City Hall updates: each agenda and set of minutes as it is posted."""
    items = []
    for m in meetings:
        when = format_date(m["date"])
        if m["agenda"]:
            items.append((m["agenda"]["fetched_at"], "agenda", _("{board}: agenda for {date}").format(board=m["body"], date=when), m,
                          (m["preview"] or {}).get("headline") or (m["preview"] or {}).get("summary") or _("Agenda posted.")))
        if m["minutes_doc"]:
            ms = m["minutes_summary"] or {}
            items.append((m["minutes_doc"]["fetched_at"], "minutes", _("{board}: minutes of {date}").format(board=m["body"], date=when), m,
                          ms.get("headline") or ms.get("summary") or _("Minutes posted.")))
    items.sort(key=lambda i: i[0], reverse=True)
    site = config["site"]
    entries = "".join(
        f"<item><title>{xml_escape(title)}</title><link>{base_url}{m['url']}</link>"
        f"<guid isPermaLink=\"false\">{base_url}{m['url']}#{kind}-{xml_escape(posted)}</guid>"
        f"<pubDate>{format_datetime(datetime.fromisoformat(posted))}</pubDate>"
        f"<description>{xml_escape(text)}</description></item>\n"
        for posted, kind, title, m, text in items[:limit]
    )
    # Wording to translate stays out of f-strings: only Python 3.12 and later find it there.
    feed_title = _("City Hall updates")
    feed_description = _("New agendas and minutes from {town} city boards and committees.").format(town=config["town"]["name"])
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n'
        f"<title>{xml_escape(site['name'])}: {xml_escape(feed_title)}</title><link>{base_url}/</link>"
        f"<description>{xml_escape(feed_description)}</description>"
        f"<lastBuildDate>{format_datetime(built_at)}</lastBuildDate>\n{entries}</channel></rss>\n",
        encoding="utf-8",
    )


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def write_311_csvs(folder: Path, sc: dict) -> None:
    """Downloadable tables behind the 311 charts. Durations are in days."""
    def med(s):
        return s["median"]
    summary = ["requests", "closed", "still_open", "median_days_to_acknowledge", "median_days_to_close"]
    def row(x):
        return [x["received"], x["closed"], x["open"], med(x["time_to_acknowledge"]), med(x["time_to_close"])]
    write_csv(folder / "monthly.csv", ["month", *summary], [[m["month"], *row(m)] for m in sc["monthly"]])
    write_csv(folder / "by-ward.csv", ["ward", "population_2020", "per_1000_residents", *summary],
              [[w["ward"], w.get("population_2020"), w.get("per_1000_residents"), *row(w)] for w in sc["by_ward"]])
    write_csv(folder / "by-category.csv", ["category", *summary], [[c["category"], *row(c)] for c in sc["categories"]])
    write_csv(folder / "recent-open.csv", ["id", "submitted", "category", "location", "ward", "url"],
              [[r["id"], r["created_at"][:10], r["category"], r["address"], r["ward"], r["url"]]
               for r in sc.get("recent_open", {}).get("requests", [])])
    write_csv(folder / "repeat-locations.csv",
              ["location", "category", "ward", "requests", "after_a_close", "still_open", "first", "last", "request_urls"],
              [[p["address"], p["category"], p["ward"], p["reports"], p["again_after_close"], p["open"], p["first"], p["last"],
                " ".join(r["url"] for r in p["requests"])] for p in sc.get("repeats", {}).get("places", [])])
    write_csv(folder / "open-by-age.csv", ["open_for", "requests"], [[b["label"], b["count"]] for b in sc["backlog"]["buckets"]])
    for w in sc.get("wards", []):
        write_csv(folder / f"ward-{w['ward']}.csv", ["category", *summary], [[c["category"], *row(c)] for c in w["by_category"]])
    for c in sc.get("categories", []):
        write_csv(folder / f"category-{c['slug']}.csv", ["ward", *summary], [[w["ward"], *row(w)] for w in c["by_ward"]])


def write_support_files(out_dir: Path, site: dict, base_url: str, urls: list[str], built_at: datetime) -> None:
    # Custom domain for GitHub Pages. With Actions deploys the domain is also set
    # in the repo's Pages settings; the file keeps the build self-describing.
    (out_dir / "CNAME").write_text(site["domain"] + "\n", encoding="utf-8")
    # Serve files as-is; don't run Jekyll over the output.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    lastmod = built_at.date().isoformat()
    entries = "\n".join(
        f"  <url><loc>{base_url}{u}</loc><lastmod>{lastmod}</lastmod></url>" for u in sorted(urls)
    )
    (out_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n",
        encoding="utf-8",
    )
    (out_dir / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {base_url}/sitemap.xml\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN, help="config/<town>.toml to build")
    parser.add_argument("--out", type=Path, default=TOWN_DIR / "_site", help="output directory")
    parser.add_argument("--data", type=Path, default=DATA_DIR, help="data directory")
    args = parser.parse_args()
    missing: dict = {}
    urls = build(args.town, args.out, args.data, missing=missing)
    print(f"Built {len(urls)} pages into {args.out}")
    failed = False
    for lang, texts in missing.items():
        if texts.get("drafted"):
            print(f"::notice::{i18n.LANGUAGES[lang]}: {len(texts.get('drafted', []))} texts are shown from machine drafts not yet "
                  f"reviewed; python -m pipeline.translate drafts lists them for [strings.{lang}].")
        # New boards and 311 categories appear in the city's data any day: shown in English until the
        # next run drafts them (or the config has them).
        for text in texts["data"]:
            print(f"::warning::{i18n.LANGUAGES[lang]}: {text!r} is shown in English until the next run drafts it, "
                  f"or [strings.{lang}] in config/{args.town}.toml has it.")
        # The config's own text is the town's to give in every language it's built in.
        if texts["config"]:
            failed = True
            print(f"::error::{i18n.LANGUAGES[lang]}: the site isn't built in {i18n.LANGUAGES[lang]} until "
                  f"[strings.{lang}] in config/{args.town}.toml, or a run's drafts, have these texts from the config:")
            for text in texts["config"]:
                print(f"  {text!r}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
