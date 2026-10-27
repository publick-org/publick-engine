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

from pipeline.civicplus import board_part, clean_text, status_of, without_status

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
    status = status_of(title)
    title = without_status(title)
    title = re.sub(r"\s{2,}", " ", re.sub(r"^[\s\-–:*]+|[\s\-–:*]+$", "", title))

    body = find_board(title, boards or [], aliases)
    if body is None:
        body = board_part(re.sub(r"\s*\([^)]*\)", "", title))
        body = re.split(r"\s+[-–:]\s+", body)[0]
        body = SPECIAL_MEETING_OF.sub("", body)
        body = KIND_SUFFIX.sub("", body)
        body = re.sub(r"^special\s+", "", body, flags=re.I).strip() or title
        body = next((target for name, target in aliases.items() if name.lower() == body.lower()), body)
    special = " special " in words(title).replace(words(body), " ")
    return {"title": title, "body": body, "status": status, "special": special}


# The kinds of meeting a meeting's page names after its board ("Planning Board public hearing"),
# in the order looked for: a listing's "Public Hearing" before its "Hearing".
KINDS = {"public_hearing": "public hearing", "hearing": "hearing", "workshop": "workshop", "work_session": "work session"}
# Words of a listing's name that say nothing its page's name doesn't: "Remote Rent Board Meeting",
# "City Council Meeting (Revised Agenda)", or its date.
PLAIN_WORDS = frozenset("""a an and the of meeting mtg regular public remote hybrid online virtual special revised updated
    agenda board commission committee subcommittee session january february march april may june july august september
    october november december jan feb mar apr jun jul aug sep sept oct nov dec""".split())
# An abbreviation of the board's name after it: "Technical Review Committee (TRC)".
ABBREVIATION = re.compile(r"\s*\([A-Z]{2,6}\)")


def kind(title: str, body: str, special: bool) -> str | None:
    """What kind of meeting a listing's name says it is, besides its board's: a key of KINDS, "special"
    for another special meeting, or None. Not one the board's own name has (Bangor's "City Council
    Workshop" is a board), nor one held with a regular meeting ("City Council Workshop & Meeting")."""
    text, board = words(title), words(body)
    for key, phrase in KINDS.items():
        for form in (phrase + "s", phrase):
            if f" {form} " in text and f" {form} " not in board:
                if f" {form} and meeting " in text or f" meeting and {form} " in text:
                    return "special" if special else None
                return key
    return "special" if special else None


def says_more(title: str, body: str, town: str) -> bool:
    """Whether a listing's name says more than its page's name (the board, and the kind of meeting): a
    topic, a place, the board it meets with. "Special Meeting of the Board of Mayor and Aldermen: Public
    Hearing on the FY27 Budget" does; "Gloucester School Committee Meeting" doesn't."""
    known = set(words(body).split()) | set(words(town).split()) | PLAIN_WORDS
    found = kind(title, body, False)
    if found:
        known |= set(KINDS[found].split())
    return any(not w.isdigit() and w not in known and w.removesuffix("s") not in known and w + "s" not in known
               for w in words(ABBREVIATION.sub("", title)).split())
