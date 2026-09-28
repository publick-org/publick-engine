"""Which public body a calendar entry is for, from its name.

CivicPlus titles follow one pattern (see civicplus.parse_title). Other
calendars don't: Manchester's lists one Board of Mayor and Aldermen meeting as
"Board of Mayor and Aldermen", "PH-1 Board of Mayor and Aldermen", "Special
Meeting-Board of Mayor and Aldermen" or "Road Hearing - Board of Mayor and
Aldermen". So a town lists its boards in [meetings] boards, and a name is
matched to the longest listed board it contains. [meetings.aliases] adds other
spellings ("ZBA" for the Zoning Board of Adjustment). A name that matches none
is cleaned up by rule instead.
"""

from __future__ import annotations

import re

from pipeline.civicplus import STATUS_WORDS, clean_text

# What a meeting is, as opposed to who is meeting: "Planning Board Public Hearings".
KIND_SUFFIX = re.compile(r"\s+(?:(?:regular|business|organizational)\s+)?"
                         r"(?:meeting|public hearings?|hearing|work session|workshop)$", re.I)
SPECIAL_MEETING_OF = re.compile(r"^(?:special\s+)?meeting\s+of\s+(?:the\s+)?", re.I)


def words(text: str) -> str:
    """'Committee on Lands & Buildings' -> ' committee on lands and buildings ', for matching."""
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", " and ")).strip() + " "


def find_board(name: str, boards: list[str], aliases: dict) -> str | None:
    """The longest listed board (or alias) named in a calendar entry's name."""
    text = words(name)
    candidates = [(b, b) for b in boards] + list(aliases.items())
    found = [(len(words(k)), target) for k, target in candidates if words(k) in text]
    return max(found)[1] if found else None


def parse_name(raw: str, boards: list[str] | None = None, aliases: dict | None = None) -> dict:
    """Title, public body, status, and whether it is a special meeting.

    'CANCELED - Committee on Joint School Buildings' -> body 'Committee on
    Joint School Buildings', status 'cancelled'. A board whose own name starts
    with "Special" ('Special Committee on Airport Activities') is not a special
    meeting of it."""
    aliases = aliases or {}
    title = clean_text(raw)
    status = next((s for s, rx in STATUS_WORDS.items() if rx.search(title)), "scheduled")
    for rx in STATUS_WORDS.values():
        title = rx.sub("", title)
    title = re.sub(r"\s{2,}", " ", re.sub(r"^[\s\-–:*]+|[\s\-–:*]+$", "", title))

    body = find_board(title, boards or [], aliases)
    if body is None:
        body = re.sub(r"\s*\([^)]*\)", "", title)
        body = re.split(r"\s+[-–:]\s+", body)[0]
        body = SPECIAL_MEETING_OF.sub("", body)
        body = KIND_SUFFIX.sub("", body)
        body = re.sub(r"^special\s+", "", body, flags=re.I).strip() or title
        body = next((target for name, target in aliases.items() if name.lower() == body.lower()), body)
    special = " special " in words(title).replace(words(body), " ")
    return {"title": title, "body": body, "status": status, "special": special}
