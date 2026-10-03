"""Summaries checked against their documents, without AI.

A summary, its headline, and each of its decisions or agenda items are
written by a model from the city's PDF, and nothing else checks them. This
module checks what can be checked mechanically: every number, amount, date,
and name in them is in the document's own text.

- A number counts however the document writes it ("$55,000.00", "55,000",
  "#014" and "#14"); an amount of money only as money in the document (after
  "$", or with cents), and a reference number in parts ("DEP File #5-1458")
  only whole. A total the model added up ("two grants totaling
  $463,733") counts when two to four of the document's amounts sum to it, and
  a rounded amount ("about $5.8 million") when one of the document's amounts
  rounds to it. A total counts only where the summary says it's one. An
  amount written out ("$8,000") counts when the document gives it with a
  scale ("$8K", "$1.2 million"), and the other way round.
- A date written out ("June 4, 2026") counts when the document has that
  date, written out or in figures ("6/4/2026"); its year when the document
  gives one for that day.
- A vote count ("7-0", "8-1") counts when the document writes it, as a
  count or in words ("3 in favor, 0 opposed"), or, for a
  count with no one against, when the document says the vote was unanimous
  or all in favor. A count the model made by counting names in a roll call
  can't be checked this way, so it's noted ("tally"), not failed.
- A name is a capitalized word that isn't an ordinary English word
  (pipeline/translate.py's names()), looked for with the document's spacing
  ignored, since some PDFs' text breaks words up ("T u renne").

Each decision in minutes summarized since the minutes prompt's version 3 comes
with its outcome (approved, denied, tabled, continued, referred, recommended,
withdrawn, or other) and a quote: the minutes' own words for it
("decision_evidence", beside "decisions", one entry each). Three more checks:
- The quote is in the document, word for word, with the document's spacing,
  line breaks, and punctuation ignored, and a page header or line number
  allowed inside it ("quote").
- The decision's numbers, dates, and names are near the quote: in the
  passage from a little before it, where the motion usually is, to a little
  after ("away"), so an amount or name can't move from one motion to another.
  Listed, not held back, like a vote count: an item's heading can be pages
  before its vote, and how often a right decision is listed is measured
  first (python -m pipeline.evaluate).
- The outcome agrees with both the decision's wording and the quote
  ("outcome"): a decision that says approved, where the minutes say the
  motion failed, or a "not" dropped, fails. A count with fewer for than
  against can't be approved; one with more for can still have failed (a
  two-thirds vote), so that isn't held against a denial.

The evidence is the PDF's own text layer, never the model's. A document whose
every page has text is checked in full ("pdf"). One with pages that have no
text (scanned pages in a typed document) can only be checked on what has text
("partial"), and a scan only against the model's transcription ("ai"), which
is weaker, since both come from the model: in both, what isn't found is
listed, but the result is "weak", not "failed".

Each summary's record keeps the result under "fact_check" ({"version",
"source", "result", "problems"}), made when the run reads the document's text
(pipeline/summarize.py) and again when VERSION changes. python -m
pipeline.factcheck lists the problems for the maintainer.
"""

from __future__ import annotations

import argparse
import functools
import itertools
import json
import re
from pathlib import Path

from pipeline import translate

VERSION = 3

FIELDS = {"agenda": ("headline", "summary", "items"), "minutes": ("headline", "summary", "decisions")}

# A page with less text than this is taken to be an image (a scanned page).
PAGE_MIN_CHARS = 40

# A vote count: "7-0", "4–1", "5-0-1".
TALLY = re.compile(r"(?<![\d$#.,/-])(\d{1,2})\s*[-–]\s*(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?(?![\d/-]|[.,]\d)")
UNANIMOUS = re.compile(r"unanim|all\s+(?:members\s+)?(?:present\s+)?(?:voted\s+)?in\s+favor|all\s+present\s+in\s+favor|"
                       r"all\s+voted\s+(?:yes|aye|in\s+favor)|without\s+objection|all\s+ayes", re.I)
# Words that make an amount approximate, or a total.
APPROXIMATE = re.compile(r"\b(?:about|approximately|nearly|almost|over|more\s+than|less\s+than|under|roughly|around)\s*\$?$", re.I)
# A US state's name: places a summary may spell out where the document abbreviates ("MA", "Mass.").
STATES = {"Massachusetts", "Connecticut", "Hampshire", "Maine", "Vermont", "Rhode", "Island", "York", "Jersey"}


def value(n: str) -> str:
    """A number as a value, however it's written: "$55,000.00", "55,000" and "55000" are 55000;
    "#014" is 14; "5,8" is 5.8 (translate.canonical()); "7:00" is 7."""
    n = n.rstrip(".,:")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", n):
        n = n.replace(",", "")
    else:
        n = translate.canonical(n)
    n = re.sub(r"(?<=\d)\.0+$", "", n)
    if re.fullmatch(r"\d+", n):
        n = n.lstrip("0") or "0"
    return n


def numbers(text: str) -> list[tuple[str, int, int]]:
    """Each number in a text: (value, start, end)."""
    return [(value(m.group(0)), m.start(), m.end()) for m in translate.NUMBER.finditer(text or "")]


def scaled_amounts(text: str) -> list[float]:
    """The amounts a text gives with a scale ("$8K" is 8000, "$1.2 million" 1200000)."""
    found = (scaled_value(text, s, e, v) for v, s, e in numbers(text))
    return sorted({x for x in found if x is not None})


def amounts(text: str) -> list[float]:
    """The document's amounts, for totals and rounding: every number with a value of 10 or more,
    with its scale word if it has one ("$14 million")."""
    found = []
    for v, s, e in numbers(text):
        try:
            x = float(v)
        except ValueError:
            continue
        big = scaled_value(text, s, e, v)
        if big is not None:
            found.append(big)
        if x >= 10:
            found.append(x)
    return sorted(set(found))


MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
# "June 4, 2026", "June 4th", "4 June 2026".
WRITTEN_DATE = re.compile(rf"\b{MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)
FIGURE_DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b")
# A date as documents write it, typos and all: "August, 3 2026", and "July 323\n14, 2026" in minutes
# that number their lines.
LOOSE_DATE = re.compile(rf"\b{MONTH}[\s,.]*(?:\d{{3}}\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\b(?:[\s,.]*(?:\d{{3}}\s+)?(\d{{4}}))?", re.I)


def month_number(name: str) -> int:
    return next(i + 1 for i, m in enumerate(MONTHS) if m.startswith(name.lower().rstrip(".")[:3]))


def dates(text: str) -> list[tuple[int, int, int | None, int, int]]:
    """The dates written out in a text: (month, day, year or None, start, end)."""
    found = []
    for m in WRITTEN_DATE.finditer(text or ""):
        day = int(m.group(2))
        if 1 <= day <= 31:
            found.append((month_number(m.group(1)), day, int(m.group(3)) if m.group(3) else None, m.start(), m.end()))
    return found


def document_dates(text: str) -> set[tuple[int, int, int | None]]:
    """Every date in the document, written out or in figures, with and without its year."""
    found = set()
    for m in LOOSE_DATE.finditer(text or ""):
        month, day, year = month_number(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None
        if 1 <= day <= 31:
            found |= {(month, day, year), (month, day, None)}
    for m in FIGURE_DATE.finditer(text or ""):
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year = year + 2000 if year < 100 else year
        if 1 <= month <= 12 and 1 <= day <= 31:
            found |= {(month, day, year), (month, day, None)}
    return found


def date_found(month: int, day: int, year: int | None, doc_dates: set) -> bool:
    """A date with a year is found if the document has it with that year, or without any year
    (minutes often write "June 4th"); one without a year, if the document has the month and day."""
    if year is None:
        return (month, day, None) in doc_dates
    return (month, day, year) in doc_dates or ((month, day, None) in doc_dates
                                                and not any(d[:2] == (month, day) and d[2] for d in doc_dates))


# A reference number in parts: "028-3125", "DEP File #5-1458", "Paper #208-26", "26-02.0726". Not a
# vote count (two numbers of one or two digits) or a date in figures.
REFERENCE = re.compile(r"(?<![\w.,/-])\d{1,5}(?:[-–]\d{1,6})+(?:\.\d+)?(?![\w/-]|[.,]\d)")


def is_reference(m: re.Match) -> bool:
    parts = re.split(r"[-–.]", m.group(0))
    return not (len(parts) in (2, 3) and all(len(p) <= 2 for p in parts))


def money(text: str) -> set[str]:
    """The document's amounts of money: numbers after "$", with cents, or before "dollars"."""
    found = set()
    for m in translate.NUMBER.finditer(text or ""):
        before, after = text[max(0, m.start() - 3):m.start()], text[m.end():m.end() + 9]
        if "$" in before or re.fullmatch(r"\d{1,3}(?:,\d{3})*\.\d\d", m.group(0)) or re.match(r"\s*dollars", after, re.I):
            found.add(value(m.group(0)))
    return found


# Words that say an amount is a sum of others.
TOTAL = re.compile(r"\b(?:total(?:s|ing|ling|ed)?|combined|together|in\s+all|sum)\b", re.I)


def squashed(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "k": 1e3, "m": 1e6, "b": 1e9}


def scaled_value(text: str, start: int, end: int, v: str) -> float | None:
    """The number at text[start:end] times its scale word, if it has one ("$5.8 million")."""
    m = re.match(r"\s*(thousand|million|billion|[KMB])\b", text[end:end + 12], re.I)
    if not m:
        return None
    try:
        return float(v) * SCALE[m.group(1).lower()]
    except ValueError:
        return None


def is_total(x: float, doc_amounts: list[float]) -> bool:
    """Whether two to four of the document's amounts add up to x (to the cent)."""
    parts = [a for a in doc_amounts if a < x]
    if len(parts) > 120:
        parts = parts[-120:]
    for k in (2, 3):
        for combo in itertools.combinations(parts, k):
            if abs(sum(combo) - x) < 0.005:
                return True
    if len(parts) <= 40:
        for combo in itertools.combinations(parts, 4):
            if abs(sum(combo) - x) < 0.005:
                return True
    return False


def rounds_to(x: float, doc_amounts: list[float]) -> bool:
    """Whether one of the document's amounts is within 5% of x: a rounded or approximate amount."""
    return any(abs(a - x) <= 0.05 * x for a in doc_amounts)


def check_text(text: str, doc: str, doc_numbers: set[str], doc_amounts: list[float], doc_squashed: str,
               words: frozenset, doc_dates: set | None = None, doc_money: set | None = None,
               doc_scaled: list[float] | None = None) -> list[dict]:
    """What in one text of a summary isn't in the document."""
    problems = []
    doc_dates = document_dates(doc) if doc_dates is None else doc_dates
    doc_money = money(doc) if doc_money is None else doc_money
    doc_scaled = scaled_amounts(doc) if doc_scaled is None else doc_scaled
    tallies = [(m.start(), m.end(), m) for m in TALLY.finditer(text)]
    spans = [(s, e) for s, e, _ in tallies]
    for s, e, m in tallies:
        parts = [g for g in m.groups() if g is not None]
        if re.search(rf"(?<![\d-]){parts[0]}\s*(?:[-–]|to)\s*{parts[1]}(?![\d-])", doc):
            continue
        # "voted 3 in favor, 0 opposed", "5 yeas, 2 nays", "4 ayes and 1 no".
        if re.search(rf"(?<![\d-]){parts[0]}\s+(?:(?:votes?|members?)\s+)?(?:in\s+favou?r|yeas?|ayes?|yes)\W+(?:and\s+|with\s+)?"
                     rf"{parts[1]}\s+(?:(?:votes?|members?)\s+)?(?:opposed|nays?|noes|no|against)", doc, re.I):
            continue
        if all(p == "0" for p in parts[1:]) and UNANIMOUS.search(doc):
            continue
        # "In Favor: 2 In Opposition: 3", "Yea: 10 ... Nay: 1", "2 yea (names) to 11 nay".
        if (int(parts[0]), int(parts[1])) in document_votes(doc):
            continue
        problems.append({"kind": "tally", "what": m.group(0)})
    for month, day, year, s, e in dates(text):
        spans.append((s, e))
        if not date_found(month, day, year, doc_dates):
            problems.append({"kind": "date", "what": text[s:e]})
    for m in REFERENCE.finditer(text):
        if any(s <= m.start() < e for s, e in spans) or not is_reference(m):
            continue
        spans.append((m.start(), m.end()))
        if re.sub("–", "-", m.group(0)) in doc_squashed.replace("–", "-"):
            continue
        # A range ("Papers 317-320", "2027-2028"): its ends, each in the document.
        ends = re.split(r"[-–]", m.group(0))
        if len(ends) == 2 and len(ends[0]) == len(ends[1]) and ends[0] < ends[1] \
                and all(value(x) in doc_numbers for x in ends):
            continue
        problems.append({"kind": "number", "what": m.group(0)})
    taken = lambda pos: any(s <= pos < e for s, e in spans)
    for v, s, e in numbers(text):
        # Money as money; but an amount of $1,000 or more is unlikely to be in the document by chance,
        # so it counts wherever it is (a table without dollar signs).
        dollars = "$" in text[max(0, s - 2):s] and not re.fullmatch(r"\d{4,}(?:\.\d+)?", v)
        if taken(s) or (v in (doc_money if dollars else doc_numbers)):
            continue
        try:
            x = float(v)
        except ValueError:
            x = None
        big = scaled_value(text, s, e, v)
        if big is not None and rounds_to(big, doc_amounts):
            continue
        # "$8,000" where the document says "$8K".
        if x is not None and any(abs(a - x) < 0.005 for a in doc_scaled):
            continue
        if x is not None and x >= 10:
            if TOTAL.search(text[max(0, s - 40):e + 40]) and is_total(x, doc_amounts):
                continue
            # A sum of other amounts the summary itself gives, each in the document:
            # "$10,000 in council funds ($7,500 for ward councillors and $2,500 at large)".
            given = [float(n) for n, _, _ in numbers(text) if n != v and n in doc_numbers and re.fullmatch(r"[\d.]+", n)]
            if is_total(x, sorted(set(given))):
                continue
            if APPROXIMATE.search(text[max(0, s - 20):s]) and rounds_to(x, doc_amounts):
                continue
        problems.append({"kind": "number", "what": text[s:e]})
    for name in sorted(translate.names(text, words)):
        if name in STATES or squashed(name) in doc_squashed:
            continue
        problems.append({"kind": "name", "what": name})
    return problems


def check(record: dict, kind: str, pages: list[str] | None, words: frozenset = frozenset()) -> dict:
    """The fact check of a summary against its document's text, page by page (None for a PDF
    with no text at all). A scan is checked against the model's transcription, if it has one."""
    texted = [p for p in pages or [] if len(p.strip()) >= PAGE_MIN_CHARS]
    if pages and len(texted) == len(pages):
        source, doc = "pdf", "\n".join(pages)
    elif texted:
        source, doc = "partial", "\n".join(pages)
    elif record.get("transcript") and record.get("transcript_source") == "ai":
        source, doc = "ai", record["transcript"]
    else:
        return {"version": VERSION, "source": "none", "result": "unchecked", "problems": []}
    found = facts(doc)
    problems = []
    for field in FIELDS[kind]:
        entries = record.get(field) or ([] if field in ("items", "decisions") else "")
        for i, text in enumerate(entries if isinstance(entries, list) else [entries]):
            for p in check_text(text, doc, *found[:3], words, *found[3:]):
                problems.append({"field": field, **({"entry": i + 1} if isinstance(entries, list) else {}), **p})
    if kind == "minutes" and "decision_evidence" in record:
        located = Located(doc)
        for i, (text, evidence) in enumerate(zip(record.get("decisions") or [], record["decision_evidence"])):
            whole = {p["what"] for p in problems if p.get("entry") == i + 1 and p["field"] == "decisions"}
            for p in anchored(text, evidence or {}, located, words, whole):
                problems.append({"field": "decisions", "entry": i + 1, **p})
    # A vote count the minutes don't give, and a number or name far from its decision's quote, are noted.
    hard = [p for p in problems if p["kind"] not in ("tally", "away")]
    result = "ok" if not hard else ("failed" if source == "pdf" else "weak")
    return {"version": VERSION, "source": source, "result": result, "problems": problems}


def facts(doc: str) -> tuple:
    """What check_text() looks for in a document (or a passage of one): its numbers, amounts,
    text without spaces, dates, money, and scaled amounts."""
    # Numbers some PDFs' text breaks up ("$14,000,0 00") are read joined too.
    joined = re.sub(r"(\d,\d{1,2})\s+(\d)", r"\1\2", doc)
    return ({v for v, _, _ in numbers(doc)} | {v for v, _, _ in numbers(joined)},
            sorted(set(amounts(doc)) | set(amounts(joined))), squashed(doc), document_dates(doc),
            money(doc) | money(joined), scaled_amounts(doc))


# ---- Decisions anchored to the minutes -----------------------------------------------

OUTCOMES = ("approved", "denied", "tabled", "continued", "referred", "recommended", "withdrawn", "other")
# Characters of the minutes before a quote, and after, that a decision's numbers and names may be in:
# the motion usually comes before the vote.
BEFORE, AFTER = 3000, 300
# What a quote may skip of the document's text in one place (a page header, a line number), and in all;
# and how far after a motion a quote cut with "..." may find how it ended (a few pages of discussion).
GAP, GAPS, LATER = 150, 300, 12000
# A motion that didn't carry, in the minutes' words.
FAILED = re.compile(r"\b(?:fail(?:s|ed|ing)?|defeat\w*|reject\w*)\b|\bnot\s+(?:carr|pass|adopt|approv|accept|grant|recommend|prevail)\w*|"
                    r"\bvoted\s+not\b|\bdid\s+not\s+(?:carr|pass)\w*", re.I)
# A decision or quote that says no: a failed motion, a denial, a decision's verb with "not". A "not"
# elsewhere is part of what was decided ("there is not an increase in the nonconformity", "not to exceed").
SAYS_NO = re.compile(FAILED.pattern + r"|\bden(?:y|ied|ies|ying)\b|\bdisapprov\w*|\bdeclin(?:e|ed|ing)\b|"
                     r"\b(?:not|never)\s+(?:to\s+)?(?:approv|adopt|accept|grant|pass|carr|recommend|support|allow|issu|deem|"
                     r"endors|award|appoint|confirm|move|proceed)\w*|n't\s+(?:approv|adopt|accept|grant|pass|carr|recommend)\w*", re.I)
# What the decision and the quote each say, for an outcome that has words of its own.
SAYS = {
    "tabled": re.compile(r"\b(?:tabl(?:e|ed|ing)|postpon\w*)\b|on\s+the\s+table", re.I),
    "continued": re.compile(r"\b(?:continu\w*|postpon\w*|reschedul\w*)", re.I),
    "referred": re.compile(r"\b(?:refer\w*|remand\w*)|\bsen[dt]\s+(?:it\s+)?(?:back\s+)?to\b", re.I),
    "recommended": re.compile(r"\b(?:recommend\w*|favou?rabl\w*)", re.I),
    "withdrawn": re.compile(r"\bwithdr\w*", re.I),
}
# A vote's count in the minutes' words: "5-0", "3 in favor, 2 opposed", "Yea: 10 ... Nay: 1",
# "In Favor: 2 ... In Opposition: 3", "2 yea to 11 nay".
COUNTS = (
    re.compile(r"(\d{1,2})\s+(?:votes?\s+)?(?:yeas?|yeah|ayes?|yes|in\s+favou?r)(?:\s*\([^)]{0,300}\))?\W+(?:and\s+|to\s+|with\s+)?(\d{1,2})\s+"
               r"(?:votes?\s+)?(?:nays?|noes|no|opposed|against|in\s+opposition)\b", re.I),
    re.compile(r"\b(?:yeas?|ayes?|in\s+favou?r)\s*:\s*(\d{1,2})\b.{0,600}?\b(?:nays?|noes|opposed|(?:in\s+)?opposition)\s*:\s*(\d{1,2})\b",
               re.I | re.S),
)


# A vote's words right before a bare count ("voted 5-0", "carried (4-1)", "failed 3-8", "by a vote of 3-8",
# "roll call 9-5"), or right after it ("a 5-0 vote").
BEFORE_COUNT = re.compile(r"\b(?:vot\w*|carri\w*|pass(?:ed|es)?|fail\w*|roll\s+call|tally|count)\b[^.;:\n\d]{0,15}$", re.I)
AFTER_COUNT = re.compile(r"^\s*\)?\s*(?:vote|roll\s+call)\b", re.I)


def votes_for_against(quote: str) -> tuple[int, int] | None:
    """The first count in a quote, for and against."""
    for pattern in COUNTS:
        m = pattern.search(quote)
        if m:
            return int(m.group(1)), int(m.group(2))
    # A bare count ("7-0") only beside a vote's words, since "3-4 bedroom units" and "ages 5-12" aren't votes.
    for m in TALLY.finditer(quote):
        if BEFORE_COUNT.search(quote[max(0, m.start() - 30):m.start()]) or AFTER_COUNT.search(quote[m.end():m.end() + 15]):
            return int(m.group(1)), int(m.group(2))
    return None


@functools.lru_cache(maxsize=4)
def document_votes(doc: str) -> set[tuple[int, int]]:
    """Every count, for and against, the document writes out in words."""
    return {(int(m.group(1)), int(m.group(2))) for pattern in COUNTS for m in pattern.finditer(doc)}


class Located:
    """A document's text without spaces or punctuation, lowercased, with where each of its
    characters came from, to find a quote however the PDF's text broke it up."""

    def __init__(self, doc: str):
        self.doc = doc
        kept = [(c.lower(), i) for i, c in enumerate(doc) if c.isalnum()]
        self.text = "".join(c for c, _ in kept)
        self.at = [i for _, i in kept]

    def find(self, quote: str) -> list[tuple[int, int]]:
        """Where the quote is in the document, as (start, end) in its text: each of its words in
        order, with nothing between them but what the document adds (a page header, a line
        number), up to GAP characters at a time and GAPS in all. A quote cut with "..." is found
        part by part, in order."""
        parts = [re.findall(r"[a-z0-9]+", p.lower()) for p in re.split(r"\.\.\.|…", quote)]
        parts = [p for p in parts if p]
        if not parts or not self.text:
            return []
        found = []
        first = "".join(parts[0][:3])
        start = self.text.find(first)
        while start != -1 and len(found) < 20:
            end = self.match(parts, start)
            if end is not None:
                found.append((self.at[start], self.at[end - 1] + 1))
            start = self.text.find(first, start + 1)
        return found

    def match(self, parts: list[list[str]], start: int) -> int | None:
        """Where the quote's words, starting at start, end in the text (None if they don't match)."""
        pos, skipped = start, 0
        for n, part in enumerate(parts):
            if n:
                # A quote cut with "...": the next part, anywhere in the next stretch of the minutes.
                nxt = self.text.find("".join(part[:3]), pos, pos + LATER)
                if nxt == -1:
                    return None
                pos = nxt
            i = 0
            while i < len(part):
                if self.text.startswith(part[i], pos):
                    pos += len(part[i])
                    i += 1
                    continue
                # Something of the document's own between two of the quote's words: the rest of the
                # quote's words must carry on within GAP characters.
                if i == 0:
                    return None
                nxt = self.text.find("".join(part[i:i + 2]), pos, pos + GAP + 1)
                if nxt == -1 or skipped + (nxt - pos) > GAPS:
                    return None
                skipped += nxt - pos
                pos = nxt
        return pos


def anchored(text: str, evidence: dict, located: Located, words: frozenset, whole: set) -> list[dict]:
    """What's wrong with one decision's evidence: its quote isn't in the minutes, its numbers or
    names aren't near the quote (whole: what isn't in the minutes at all, already a problem),
    or its outcome doesn't agree with its wording or the quote."""
    quote, outcome = (evidence.get("quote") or "").strip(), evidence.get("outcome") or ""
    problems = []
    places = located.find(quote) if quote else []
    if not places:
        return [{"kind": "quote", "what": quote[:200] or "(no quote)"}]
    doc = located.doc
    away = None
    for s, e in places:
        near = doc[max(0, s - BEFORE):e + AFTER]
        found = facts(near)
        missing = {p["what"] for p in check_text(text, near, *found[:3], words, *found[3:]) if p["kind"] != "tally"} - whole
        away = missing if away is None else away & missing
        if not away:
            break
    problems += [{"kind": "away", "what": w} for w in sorted(away or ())]
    if outcome not in OUTCOMES:
        problems.append({"kind": "outcome", "what": f"{outcome or '(none)'}: not an outcome"})
    elif outcome in ("approved", *SAYS):
        if FAILED.search(quote):
            problems.append({"kind": "outcome", "what": f"{outcome}, but the minutes say the motion didn't carry"})
        elif outcome == "approved" and (SAYS_NO.search(text) or re.search(r"\bden(?:y|ied|ies)\b", quote, re.I)):
            problems.append({"kind": "outcome", "what": "approved, but the decision or the minutes say no"})
        counted = votes_for_against(quote)
        if counted and counted[0] < counted[1] and not FAILED.search(quote):
            problems.append({"kind": "outcome", "what": f"{outcome}, but the vote was {counted[0]} for and {counted[1]} against"})
        if outcome in SAYS and not (SAYS[outcome].search(text) and SAYS[outcome].search(quote)):
            problems.append({"kind": "outcome", "what": f"{outcome}, but the decision or the minutes don't say so"})
    elif outcome == "denied":
        counted = votes_for_against(quote)
        if not SAYS_NO.search(text) or not (SAYS_NO.search(quote) or (counted and counted[0] < counted[1])):
            problems.append({"kind": "outcome", "what": "denied, but the decision or the minutes don't say no"})
    return problems


def pages(pdf: bytes) -> list[str] | None:
    """The text layer of each page of a PDF (None if it can't be read, or has no text at all)."""
    import io

    from pypdf import PdfReader
    try:
        texts = [page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages]
    except Exception:
        return None
    return texts if any(t.strip() for t in texts) else None


def current(record: dict) -> bool:
    """Whether the record's fact check is up to date: made with this VERSION, and, for a scan
    checked before it had the model's transcription, not since given one."""
    fc = record.get("fact_check") or {}
    return fc.get("version") == VERSION and not (fc.get("source") == "none" and record.get("transcript_source") == "ai")


# ---- What's shown ------------------------------------------------------------------

def without_tally(text: str, tally: str) -> str:
    """The text without a vote count, and the words that only introduce it: "Approved the plan,
    5-0." is "Approved the plan."; "voted 2-0-1 to recommend" is "voted to recommend"; and the
    Spanish "con votación 5-0" goes as well."""
    t = re.escape(tally)
    for pattern in (
        rf"\s*\(\s*{t}\s*\)",                                                       # "(4-0)"
        rf",\s*(?:approved|passed|passing)\s+{t}(?=\s*(?:[.;]|$))",                     # ", approved 5-0." at the end
        rf"[,;]?\s*(?:by|on|with)\s+an?\s+{t}\s+vote\b",                                # "by a 5-0 vote"
        rf"[,;]?\s*(?:(?:con|por)\s+(?:una\s+)?)?votaci[oó]n\s+(?:de\s+)?{t}",           # "con votación 5-0"
        rf"[,;]?\s*(?:by\s+a\s+)?vote\s+(?:of\s+)?{t}",                                 # "vote 4-1", "by a vote of 4-1"
        rf"[,;]?\s*{t}\s+(?:vote|votos?)\b",                                           # ", 3-2 vote"
        rf"[,;]?\s*(?:por\s+)?{t}(?=\s*(?:[.;,)]|$))",                                  # ", 5-0." at the end
        rf"\s+{t}(?=\s)",                                                              # "approved 5-0 with"
    ):
        new = re.sub(pattern, "", text, count=1, flags=re.I)
        if new != text:
            text = new
            break
    text = re.sub(r"\s+([,.;)])", r"\1", text)
    text = re.sub(r",\s*\(", " (", text)
    text = re.sub(r"([,;])\s*([.;])", r"\2", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def sentences(text: str) -> list[str]:
    """A summary's sentences (a period, question, or exclamation mark, then a capital letter)."""
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])", text or "") if s]


def shown(display: dict | None, record: dict | None, kind: str) -> dict | None:
    """A summary as the site shows it (display: the record, or its translation, entry by entry),
    after its fact check (record's "fact_check"):
    - checked against the document's full text, a decision or agenda item with something not
      in the document isn't shown, nor is such a headline, or such a sentence of the summary
      (the whole summary, in another language, where its sentences can't be matched);
      "not_shown" counts the entries left out, for the page to say so;
    - wherever it was checked, a vote count the document doesn't give is left out of the text.
    Weaker checks (scanned pages, a transcription) hide nothing else: what they can't find may be
    on a page they can't read."""
    if not display or not record or not record.get("fact_check"):
        return display
    fc = record["fact_check"]
    out = dict(display)
    english = display is record or display.get("headline") == record.get("headline")
    hidden: dict[str, set] = {}
    for p in fc["problems"]:
        field = p["field"]
        if field not in out or not out[field]:
            continue
        if p["kind"] == "tally":
            if isinstance(out[field], list):
                i = p["entry"] - 1
                if i < len(out[field]):
                    out[field] = [without_tally(t, p["what"]) if j == i else t for j, t in enumerate(out[field])]
            else:
                out[field] = without_tally(out[field], p["what"])
        elif fc["result"] == "failed":
            hidden.setdefault(field, set()).add(p.get("entry"))
    for field, entries in hidden.items():
        if isinstance(out[field], list):
            out[field] = [t for j, t in enumerate(out[field]) if j + 1 not in entries]
            out["not_shown"] = out.get("not_shown", 0) + len(entries)
        elif field == "summary" and english:
            whats = [p["what"] for p in fc["problems"] if p["field"] == "summary" and p["kind"] != "tally"]
            out[field] = " ".join(s for s in sentences(out[field]) if not any(w in s for w in whats))
        else:
            out[field] = ""
    return out


def kind_of(record: dict) -> str:
    return record.get("kind") or ("minutes" if "decisions" in record else "agenda")


def report(data_dir: Path) -> str:
    """Every summary whose check found something, for the maintainer."""
    lines, counts = [], {}
    for file in sorted((data_dir / "summaries").glob("*.json")):
        record = json.loads(file.read_text(encoding="utf-8"))
        fc = record.get("fact_check")
        result = fc["result"] if fc else "not checked yet"
        counts[result] = counts.get(result, 0) + 1
        if not fc or not fc["problems"]:
            continue
        lines.append(f"{file.stem[:12]} ({kind_of(record)}, {fc['source']}, {result}): {record.get('source_url', '')}")
        for p in fc["problems"]:
            where = p["field"] + (f" {p['entry']}" if "entry" in p else "")
            lines.append(f"  {where}: {p['kind']} {p['what']!r}")
    head = ", ".join(f"{n} {r}" for r, n in sorted(counts.items()))
    return f"Summaries: {head}\n" + "\n".join(lines) + ("\n" if lines else "")


def main() -> None:
    from pipeline.config import DATA_DIR
    parser = argparse.ArgumentParser(description="Summaries whose facts weren't found in their documents")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    print(report(args.data), end="")


if __name__ == "__main__":
    main()
