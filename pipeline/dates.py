"""Dates written in text: meetings' titles, file names, link labels and pages.

Every reader that finds a date in words a person wrote finds it here, so a way of writing one
that a reader meets is taught once and read by all of them:

  2026-10-27, 2026-9-7, 2026_10_27, 2015.03.26        year, month, day
  10/27/2026, 10/27/26, 10-27-2026, 10-27-26,         month, day, year; one separator throughout,
  10.27.26, 9_9_26                                    a two-digit year taken as 20xx
  October 27, 2026; Oct. 27th 2026; Sept 8 2026;      a month's name or its first letters, the
  November 10,2025; June15, 2026; Febuary 11, 2026    day, with st/nd/rd/th or not, and the year
  27 October 2026; 21st Oct 2026                      the day first

A run of figures that only looks like a date isn't one: a date inside a longer run of figures
and separators ("1/2/3/4", "Item 4-10/15/2026"), a month and day without a year ("8/10", "1/2
day"), and a date that doesn't exist ("2/30/2026", "13/1/26"), which is passed over for the next.

  find_dates(text)    every date in the text, in order, with where it's written
  find_date(text)     the first date, or None
  without_dates(text) the text with its dates taken out ("Planning Board Meeting, December 17,
                      2026" -> "Planning Board Meeting, ")

A date with no year ("December 17", or "to 10-14" after a status word) is read by find_dates and
find_date only given `near`, the day it's written about (a page's date, or today): its year is
the one that puts it nearest that day, so "January 5" written on December 30 is next year's.
without_dates takes year-less dates out with yearless=True, without reading them.

A dashed date with a two-digit year ("11-11-26") is a date by default, as in titles and file
names. A reader of a whole page passes short_dashed_years=False, where figures with dashes are
as often something else's.
"""

import re
from datetime import date
from typing import NamedTuple

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# The ways each month is written in full or short: a date without a year is read only with one of
# these, since a word that only starts like a month ("Marshall 3", "Mayor 2") is more often a name.
MONTH_FORMS = frozenset(("january", "february", "march", "april", "may", "june", "july", "august", "september",
                         "october", "november", "december", "sept") + MONTHS)
# A month's name or any word that starts with its first three letters (a misspelling, "Febuary").
MONTH = r"\b(?P<month>(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\.?"
ORDINAL = r"(?:st|nd|rd|th)?"
# A day and month in figures not inside a longer run of figures and separators: no figure, or figure
# and separator, just before ("Item 4-10/15/2026", "v1.10-14-2026"), where a dash, slash or space
# after a word is a separator ("Council -10/19/2026", "/10-13-2026-agenda.pdf")...
BEFORE = r"(?<!\d)(?<!\d[-_/.])"
# ...nor a figure, or its separator again and a figure, just after ("1/2/3/4", "1.2.26.4"); ".pdf",
# "-Regular" or a copy's number ("10-19-26_1.pdf") may follow.
AFTER = r"(?!\d)(?!(?P=sep)\d)"

FORMS = (
    # 2026-10-27, 2026-9-7, 2026_10_27, 2015.03.26; a year first is a date wherever it's written, in a
    # path or a stamp ("/Agendas2/2026-09-03_PB_AGENDA.PDF", "?ver=2026-09-21-111637").
    re.compile(r"(?<!\d)(?P<year>\d{4})(?P<sep>[-_.])(?P<mm>\d{1,2})(?P=sep)(?P<day>\d{1,2})(?!\d)"),
    # 10/27/2026, 10/27/26, 10-27-2026, 10.27.26, 9_9_26
    re.compile(BEFORE + r"(?P<mm>\d{1,2})(?P<sep>[-/._])(?P<day>\d{1,2})(?P=sep)(?P<year>\d{4}|\d{2})" + AFTER),
    # October 27, 2026; Oct. 27th 2026; June15, 2026; November 10,2025
    re.compile(MONTH + r"\s*(?P<day>\d{1,2})" + ORDINAL + r"(?:\s*,\s*|\s+)(?P<year>\d{4})(?!\d)", re.I),
    # 27 October 2026; 21st Oct. 2026
    re.compile(BEFORE + r"(?P<day>\d{1,2})" + ORDINAL + r"\s+" + MONTH + r",?\s+(?P<year>\d{4})(?!\d)", re.I),
)
YEARLESS = (
    # December 17; Dec. 17th
    re.compile(MONTH + r"\s*(?P<day>\d{1,2})" + ORDINAL + r"\b", re.I),
    # 10-14, 10/14 after "on", "for" or "to" ("RESCHEDULED to 10-14"): without those words, figures
    # like these are as often a vote or a fraction.
    re.compile(r"\b(?:on|for|to)\s+(?P<date>(?P<mm>\d{1,2})[-/](?P<day>\d{1,2}))(?![\d/])", re.I),
)
# The words that lead into a date in a name, taken out with it: "Meeting on Tuesday, December 17".
LEAD_IN = re.compile(r"(?:\b(?:on|for|to)\s+)?(?:\b(?:mon|tue|tues|wed|wednes|thu|thur|thurs|fri|sat|satur|sun)"
                     r"(?:day)?\.?,?\s+)?$", re.I)


class Found(NamedTuple):
    """A date in a text, and where it's written: text[start:end]."""
    day: date
    start: int
    end: int


def month_number(word: str, *, exact: bool = False) -> int | None:
    """1 for 'January', 'Jan', 'jan.' or (unless exact) 'Janury'; None for a word that isn't a month."""
    word = word.lower().rstrip(".")
    if exact and word not in MONTH_FORMS:
        return None
    return MONTHS.index(word[:3]) + 1 if word[:3] in MONTHS else None


def _make(year: int, month: int | None, day: int) -> date | None:
    try:
        return date(year, month, day) if month else None
    except ValueError:
        return None


def _nearest(month: int | None, day: int, near: date) -> date | None:
    """The day of that month and day nearest `near`: in its year, or the one before or after."""
    days = [d for d in (_make(near.year + k, month, day) for k in (-1, 0, 1)) if d]
    return min(days, key=lambda d: abs((d - near).days)) if days else None


def _candidates(text: str, *, yearless: bool, short_dashed_years: bool):
    """(start, end, year or None, month, day) for each date written in the text, read or not, in
    no order; a date with no year only if yearless."""
    for pattern in FORMS:
        for m in pattern.finditer(text):
            parts = m.groupdict()
            if parts.get("sep") == "-" and len(parts["year"]) == 2 and not short_dashed_years:
                continue
            year = int(parts["year"]) + (2000 if len(parts["year"]) == 2 else 0)
            month = month_number(parts["month"]) if parts.get("month") else int(parts["mm"])
            yield m.start(), m.end(), year, month, int(parts["day"])
    if yearless:
        for pattern in YEARLESS:
            for m in pattern.finditer(text):
                parts = m.groupdict()
                month = month_number(parts["month"], exact=True) if parts.get("month") else int(parts["mm"])
                start = m.start("date") if parts.get("date") else m.start()
                yield start, m.end(), None, month, int(parts["day"])


def _written(text: str, *, yearless: bool, short_dashed_years: bool) -> list[tuple]:
    """(start, end, year or None, month, day) for each date written in the text, read or not, in order.
    Of dates written over one another, the one that starts first, then the longest: "December 17,
    2026" rather than its "December 17", and "February 29, 2027", which isn't a day, rather than its
    "February 29", which would be."""
    kept, end = [], 0
    for item in sorted(_candidates(text, yearless=yearless, short_dashed_years=short_dashed_years),
                       key=lambda c: (c[0], -c[1])):
        if item[0] >= end:
            kept.append(item)
            end = item[1]
    return kept


def find_dates(text: str | None, *, near: date | None = None, short_dashed_years: bool = True) -> list[Found]:
    """Every date in the text that is one, in the order written. Dates without a year are read
    only given `near`, each in the year that puts it nearest that day."""
    found = []
    for start, end, year, month, day in _written(text or "", yearless=near is not None,
                                                 short_dashed_years=short_dashed_years):
        value = _make(year, month, day) if year else _nearest(month, day, near)
        if value:
            found.append(Found(value, start, end))
    return found


def find_date(text: str | None, *, near: date | None = None, short_dashed_years: bool = True) -> date | None:
    """The first date in the text, or None."""
    found = find_dates(text, near=near, short_dashed_years=short_dashed_years)
    return found[0].day if found else None


def without_dates(text: str | None, *, yearless: bool = False, short_dashed_years: bool = True) -> str:
    """The text with each date in it, and the words that lead into it ("on", "for", "to", a
    weekday), replaced by a space. With yearless, dates without a year go too ("December 17")."""
    text = text or ""
    for start, end, year, month, day in reversed(_written(text, yearless=yearless,
                                                          short_dashed_years=short_dashed_years)):
        # A date without a year is one if it's a day of some year: February 29 is.
        if _make(year or 2000, month, day):
            start = LEAD_IN.search(text, 0, start).start()
            text = text[:start] + " " + text[end:]
    return text
