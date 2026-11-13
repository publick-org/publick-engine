"""Dates written in text: meetings' titles, file names, link labels and pages.

Every reader that finds a date in words a person wrote finds it here, so a way of writing one
that a reader meets is taught once and read by all of them:

  2026-10-27, 2026-9-7, 2026_10_27, 2015.03.26        year, month, day
  10/27/2026, 10/27/26, 10-27-2026, 10-27-26,         month, day, year; one separator throughout,
  10.27.26, 9_9_26                                    a two-digit year taken as 20xx
  October 27, 2026; Oct. 27th 2026; Sept 8 2026;      a month's name or its first letters, the
  November 10,2025; June15, 2026; Febuary 11, 2026;   day, with st/nd/rd/th or not, and the year,
  November 9. 2026                                    after a comma, a space, or a period and a space
  27 October 2026; 21st Oct 2026                      the day first
  Board of Assessors 11 5 26                          month, day, two-digit year with spaces: only as
                                                      the last thing in a title, not after a figure

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
as often something else's; it doesn't read figures with spaces ("11 5 26") either.

Figures with spaces are a date only as a title's last word, not after a figure, since in the middle of
a title, or after another figure, they are as often an address, a phone number or an item's
number ("Room 11 5 26", "Ward 3 11 5 26").

Figures run together ("1132026", "152026": a month, a day, and a four-digit year from 2000 on,
as a person types them in a file's name) are read only given `near`, the day the file was
posted, and `window`, the days from it the date can be: (-365, 0) for minutes posted up to a
year after their meeting, (0, 30) for an agenda posted up to a month before. "1132026" is
January 13 or November 3, and it's a date only if exactly one of those is in the window. One
with two in the window, or none (a year typed wrong: "2242025" posted in February 2026), isn't
guessed at: undecided() lists them, for the reader to report.

  undecided(text, near=, window=)   figures run together that weren't read, with their readings
"""

import re
from datetime import date, timedelta
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
    # October 27, 2026; Oct. 27th 2026; June15, 2026; November 10,2025; November 9. 2026
    re.compile(MONTH + r"\s*(?P<day>\d{1,2})" + ORDINAL + r"(?:\s*,\s*|\.\s+|\s+)(?P<year>\d{4})(?!\d)", re.I),
    # 27 October 2026; 21st Oct. 2026
    re.compile(BEFORE + r"(?P<day>\d{1,2})" + ORDINAL + r"\s+" + MONTH + r",?\s+(?P<year>\d{4})(?!\d)", re.I),
)
# Board of Assessors 11 5 26: month, day and two-digit year with spaces, as the text's last word, not
# after a figure (checked in _candidates, FIGURE_BEFORE).
SPACED = re.compile(r"(?<!\S)(?P<mm>\d{1,2})\s+(?P<day>\d{1,2})\s+(?P<year>\d{2})\s*$")
# A figure, and nothing after it but spaces or punctuation: "Ward 3 ", "Item 4. ".
FIGURE_BEFORE = re.compile(r"\d\W*$")
# 1132026, 152026: month and day run together with a four-digit year, read only in a window (find_dates).
COMPACT = re.compile(BEFORE + r"(?P<md>\d{2,4})(?P<year>20\d{2})(?![\d])(?![-_/.]\d)")
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


def _readings(md: str, year: str) -> list[date]:
    """Every day figures run together can be: '113', '2026' -> January 13 and November 3, 2026."""
    days = (_make(int(year), int(md[:k]), int(md[k:])) for k in (1, 2) if 1 <= len(md) - k <= 2)
    return [d for d in days if d]


def _compact(text: str, near: date, window: tuple[int, int]):
    """(start, end, the readings in the window, every reading) for each run of figures that is a day."""
    earliest, latest = near + timedelta(days=window[0]), near + timedelta(days=window[1])
    for m in COMPACT.finditer(text):
        readings = _readings(m["md"], m["year"])
        if readings:
            yield m.start(), m.end(), [d for d in readings if earliest <= d <= latest], readings


def _candidates(text: str, *, yearless: bool, short_dashed_years: bool, near: date | None = None,
                window: tuple[int, int] | None = None):
    """(start, end, year or None, month, day) for each date written in the text, read or not, in
    no order; a date with no year only if yearless, and figures run together only in a window."""
    spaced = (SPACED,) if short_dashed_years else ()
    for pattern in FORMS + spaced:
        for m in pattern.finditer(text):
            if pattern is SPACED and FIGURE_BEFORE.search(text, 0, m.start()):
                continue
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
    if window is not None:
        for start, end, fits, _ in _compact(text, near, window):
            if len(fits) == 1:
                yield start, end, fits[0].year, fits[0].month, fits[0].day


def _written(text: str, *, yearless: bool, short_dashed_years: bool, near: date | None = None,
             window: tuple[int, int] | None = None) -> list[tuple]:
    """(start, end, year or None, month, day) for each date written in the text, read or not, in order.
    Of dates written over one another, the one that starts first, then the longest: "December 17,
    2026" rather than its "December 17", and "February 29, 2027", which isn't a day, rather than its
    "February 29", which would be."""
    kept, end = [], 0
    for item in sorted(_candidates(text, yearless=yearless, short_dashed_years=short_dashed_years, near=near,
                                   window=window),
                       key=lambda c: (c[0], -c[1])):
        if item[0] >= end:
            kept.append(item)
            end = item[1]
    return kept


def _check_window(near: date | None, window: tuple[int, int] | None) -> None:
    if window is not None and (near is None or len(window) != 2 or window[0] > window[1]):
        raise ValueError(f"window {window!r} needs near, and is (days before or after near, a later number of days)")


def find_dates(text: str | None, *, near: date | None = None, short_dashed_years: bool = True,
               window: tuple[int, int] | None = None) -> list[Found]:
    """Every date in the text that is one, in the order written. Dates without a year are read
    only given `near`, each in the year that puts it nearest that day; figures run together
    ("1132026") only given `near` and `window`, when one reading is in it."""
    _check_window(near, window)
    found = []
    for start, end, year, month, day in _written(text or "", yearless=near is not None,
                                                 short_dashed_years=short_dashed_years, near=near, window=window):
        value = _make(year, month, day) if year else _nearest(month, day, near)
        if value:
            found.append(Found(value, start, end))
    return found


def find_date(text: str | None, *, near: date | None = None, short_dashed_years: bool = True,
              window: tuple[int, int] | None = None) -> date | None:
    """The first date in the text, or None."""
    found = find_dates(text, near=near, short_dashed_years=short_dashed_years, window=window)
    return found[0].day if found else None


def undecided(text: str | None, *, near: date, window: tuple[int, int]) -> list[tuple[str, list[date]]]:
    """Each run of figures in the text that is a day but wasn't read as one, because two of its
    readings are in the window or none is, with every reading: for the reader to report, since
    a person typed it and only a person can say which they meant."""
    _check_window(near, window)
    text = text or ""
    return [(text[start:end], readings) for start, end, fits, readings in _compact(text, near, window)
            if len(fits) != 1]


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
