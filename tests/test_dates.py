"""Tests for reading dates in text (pipeline/dates.py): every way the readers' titles, file names,
labels and pages write one, and figures that only look like one."""

from datetime import date

import pytest

from pipeline import dates


def iso(text, **kwargs):
    day = dates.find_date(text, **kwargs)
    return day.isoformat() if day else None


@pytest.mark.parametrize("text, expected", [
    # Year, month, day: with or without zeros, and with dashes, underscores or dots.
    ("2026-10-27 City Council", "2026-10-27"), ("2026-9-16 BOSC Agenda", "2026-09-16"),
    ("2026-08-17*", "2026-08-17"), ("Minutes_2015.03.26", "2015-03-26"), ("Agenda_2026_10_27.pdf", "2026-10-27"),
    # Month, day, year in figures, with slashes, dashes, dots or underscores, a two-digit year as 20xx.
    ("City Council Meeting 10/27/2026", "2026-10-27"), ("10/13/26", "2026-10-13"),
    ("9/8/26 Board of Education Meeting", "2026-09-08"), ("City Council 10-27-26", "2026-10-27"),
    ("Agenda 10-14-2026 WSD Regular Board Meeting", "2026-10-14"), ("Minutes 9-9-2026", "2026-09-09"),
    ("01.14.26 – Special BoE Meeting Minutes", "2026-01-14"), ("05.06.2026-Regular Board of Education", "2026-05-06"),
    ("SC Governance Workshop Agenda 4_8_26.pdf", "2026-04-08"), ("11-17-25", "2025-11-17"),
    # Run into a file name's words, and followed by its extension.
    ("BOAAminutes3.11.26.pdf", "2026-03-11"), ("IWWCAgenda9-2-26.pdf", "2026-09-02"),
    # A copy's number after it, and in a link's path or a file's version stamp.
    ("Amended Regular SC Agenda 10-19-26_1.pdf", "2026-10-19"), ("Call for the Week 10-19-2026_0.pdf", "2026-10-19"),
    ("/Agendas2/2026-09-03_PB_AGENDA.PDF", "2026-09-03"), ("Agenda.pdf?ver=2026-09-21-111637-000", "2026-09-21"),
    ("/meetings/2026-09-28-historical-commission", "2026-09-28"), ("/files/10-13-2026-agenda.pdf", "2026-10-13"),
    # A month's name, in full or short, with or without a dot, an ordinal or a space.
    ("City Council - October 27, 2026", "2026-10-27"), ("Feb 11, 2026", "2026-02-11"),
    ("School Committee Sept. 8th 2026", "2026-09-08"), ("Sept 9 2026", "2026-09-09"),
    ("Sept.9, 2026", "2026-09-09"), ("June15, 2026 REVISED", "2026-06-15"), ("November 10,2025", "2025-11-10"),
    ("June 10 2026 Special Meeting Minutes", "2026-06-10"), ("MARCH 19, 2024 -NOTICE OF RECOUNT", "2024-03-19"),
    ("Nov. 9th, 2026 - Finance", "2026-11-09"), ("Dec 1st 2026", "2026-12-01"), ("July 4 , 2026", "2026-07-04"),
    # A period after the day (a station's recordings, a city's notices).
    ("Brockton City Council Meeting November 9. 2026", "2026-11-09"), ("August 11. 2026", "2026-08-11"),
    ("Nov. 9th. 2026", "2026-11-09"),
    # A misspelt month, read by its first letters.
    ("Febuary 11, 2026", "2026-02-11"),
    # The day first.
    ("Planning Board 21 October 2026", "2026-10-21"), ("21st Oct. 2026", "2026-10-21"), ("3 May, 2026", "2026-05-03"),
    # Month, day and two-digit year with spaces, as a title's last word (a station's recordings).
    ("Board of Assessors 11 5 26", "2026-11-05"), ("Conservation Commission 11 2 26", "2026-11-02"),
    ("Tax Increment Financing Board 10 28 26", "2026-10-28"), ("City Council 03 03 26 ", "2026-03-03"),
])
def test_forms(text, expected):
    assert iso(text) == expected


@pytest.mark.parametrize("text, expected", [
    # A dash before the date that follows no figure is a separator (a recording's title).
    ("South Burlington City Council -10/19/2026", "2026-10-19"), ("Planning Commission-10/15/2026", "2026-10-15"),
    # A school district's labels: a two-digit year with slashes, and dashes with a four-digit year.
    ("10/13/26 Rescheduled from October 6, 2026", "2026-10-13"), ("Agenda 10-14-2026", "2026-10-14"),
])
def test_dates_readers_once_missed(text, expected):
    assert iso(text) == expected


@pytest.mark.parametrize("text", [
    # Part of a longer run of figures and separators.
    "Item 4-10/15/2026", "Policy 1/2/3/4", "Budget v1.10-14-2026", "Version 1.2.3.4", "10/13/26/27", "1.2.26.4",
    # A month and day without a year, a fraction, a section number, a phone number.
    "Rescheduled from 8/10", "1/2 day", "Policy 6114.3 revised", "Call 802-655-6410",
    "Agenda of Special Meeting - January 5", "June 4, FY26 Budget Departmental Review meeting #4",
    # No date at all, or a word that isn't a month.
    "Ward 3 Meeting", "October Additional Resources", "Annual Report - March 2026",
    "Minutes of Regular Meeting- Match 11, 2026", "", None,
    # Figures with spaces anywhere but a title's end, or after another figure: an address, a phone
    # number, an item's number.
    "03 03 26 BOH Minutes", "Room 11 5 26 City Hall", "Ward 3 11 5 26", "Call 508 12 3 26", "Item 4. 11 5 26",
    "Board of Assessors-11 5 26", "Board of Assessors 11 5 2026x", "11 5 26 7",
    # Figures run together, without a day the file was posted.
    "LYNN CITY COUNCIL MEETING MINUTES 1132026", "City Council Minutes 4142026",
    # A figure after a month's name and a period, with no space: a decimal, not a year.
    "Mar 3.1415", "June 4.5000 acres",
])
def test_not_dates(text):
    assert dates.find_date(text) is None


@pytest.mark.parametrize("text", [
    "2026-02-30", "2/30/2026", "Agenda 2-30-2026", "February 30, 2026", "30 February 2026", "13/1/26 Agenda",
    "13.02.26 Minutes", "2026-13-01", "4/31/26", "February 29, 2027", "00/10/2026", "10/00/2026",
])
def test_days_that_dont_exist(text):
    assert dates.find_date(text) is None


def test_a_day_that_doesnt_exist_isnt_read_as_its_month_and_day():
    # "February 29, 2027" isn't a day; its "February 29" isn't read as a date without a year.
    assert dates.find_date("February 29, 2027", near=date(2027, 1, 1)) is None
    assert dates.without_dates("February 29, 2027", yearless=True) == "February 29, 2027"


def test_a_leap_day():
    assert iso("February 29, 2028") == "2028-02-29"
    assert iso("2/29/24") == "2024-02-29"


def test_a_date_that_isnt_one_is_passed_over_for_the_next():
    assert iso("Item 13/40/2026 then 10/27/2026") == "2026-10-27"
    assert iso("February 30, 2026, held March 2, 2026") == "2026-03-02"


def test_the_first_date_whichever_way_it_is_written():
    assert iso("October 6, 2026 (was 9/29/26)") == "2026-10-06"
    assert iso("Planning Board October 20, 2026 rebroadcast 2026-10-27") == "2026-10-20"


def test_every_date_and_where_it_is():
    text = "Agenda 10/13/26, minutes of October 6, 2026 and 2026-09-29"
    found = dates.find_dates(text)
    assert [f.day for f in found] == [date(2026, 10, 13), date(2026, 10, 6), date(2026, 9, 29)]
    assert [text[f.start:f.end] for f in found] == ["10/13/26", "October 6, 2026", "2026-09-29"]


def test_one_date_written_over_another_is_read_once():
    # "December 17, 2026" holds a year-less "December 17": the whole date is read, once.
    found = dates.find_dates("Meeting December 17, 2026", near=date(2026, 11, 6))
    assert [(f.day, f.start, f.end) for f in found] == [(date(2026, 12, 17), 8, 25)]


def test_a_page_reads_dashes_only_with_a_full_year():
    assert dates.find_date("11-11-26 Board Meeting", short_dashed_years=False) is None
    # Nor figures with spaces.
    assert dates.find_date("Board of Assessors 11 5 26", short_dashed_years=False) is None
    assert iso("11-11-2026 Regular Board Meeting", short_dashed_years=False) == "2026-11-11"
    # Slashes and dots with a two-digit year are still dates.
    assert iso("10/26/26 BOE Meeting", short_dashed_years=False) == "2026-10-26"
    assert iso("10.07.26 Minutes", short_dashed_years=False) == "2026-10-07"


# ---- Dates without a year ---------------------------------------------------------------------

def test_a_date_without_a_year_is_read_only_near_a_day():
    assert dates.find_date("Planning Board, December 17") is None
    assert iso("Planning Board, December 17", near=date(2026, 11, 6)) == "2026-12-17"
    assert iso("Meeting rescheduled to 10-14", near=date(2026, 10, 1)) == "2026-10-14"


@pytest.mark.parametrize("text, near, expected", [
    # Near New Year, the year that puts the date nearest the day it's written about.
    ("January 5", date(2026, 12, 30), "2027-01-05"),
    ("December 30", date(2027, 1, 2), "2026-12-30"),
    ("Dec. 28th", date(2027, 1, 4), "2026-12-28"),
    ("on 1/4", date(2026, 12, 29), "2027-01-04"),
    ("June 4", date(2026, 11, 6), "2026-06-04"),
    # February 29 only in a leap year, the nearest one within a year.
    ("February 29", date(2027, 11, 6), "2028-02-29"),
    ("February 29", date(2026, 6, 1), None),
    ("February 30", date(2028, 1, 1), None),
])
def test_the_nearest_year(text, near, expected):
    assert iso(text, near=near) == expected


def test_a_date_without_a_year_needs_a_month_by_name():
    near = date(2026, 11, 6)
    # A word that only starts like a month, figures without "on", "for" or "to", a vote.
    for text in ("Marshall 3 Committee", "Mayor 2", "Ward 3/4", "Voted 5-2", "Article 10/14"):
        assert dates.find_date(text, near=near) is None, text


# ---- Figures run together ------------------------------------------------------------------

MINUTES, AGENDA = (-365, 0), (0, 30)


@pytest.mark.parametrize("text, posted, window, expected", [
    # 1/13 is 107 days before minutes posted on April 30; 11/3 is after, so it isn't in the window.
    ("LYNN CITY COUNCIL MEETING MINUTES 1132026", date(2026, 4, 30), MINUTES, "2026-01-13"),
    # 41/4 isn't a day: one reading.
    ("City Council Minutes 4142026", date(2026, 4, 30), MINUTES, "2026-04-14"),
    ("CITY COUNCIL MEETING MINUTES 152026", date(2026, 2, 3), MINUTES, "2026-01-05"),
    # An agenda posted before its meeting; two-figure months and days.
    ("ENVIRONMENTAL AFFAIRS COMMITTEE CANCELED 982026", date(2026, 9, 3), AGENDA, "2026-09-08"),
    ("Planning Board Agenda 10132026.pdf", date(2026, 10, 6), AGENDA, "2026-10-13"),
    # 1/13 and 11/3 are both in a year of minutes posted in December: not guessed.
    ("LYNN CITY COUNCIL MEETING MINUTES 1132026", date(2026, 12, 1), MINUTES, None),
    # A year typed wrong: an agenda for February 24, 2025 posted in February 2026 is reported, not corrected.
    ("LYNN CITY COUNCIL AGENDA 2242025 AMENDED", date(2026, 2, 20), AGENDA, None),
    # A scanner's name, and figures in a longer run, aren't a day.
    ("20260220111947665", date(2026, 2, 20), MINUTES, None), ("Item 4-1132026", date(2026, 4, 30), MINUTES, None),
    ("Agenda_01022026-639", date(2026, 1, 1), AGENDA, None), ("Minutes 4142026.pdf", date(2026, 4, 30), MINUTES, "2026-04-14"),
])
def test_figures_run_together_in_a_window(text, posted, window, expected):
    assert iso(text, near=posted, window=window) == expected


def test_figures_run_together_that_arent_read_are_listed():
    both = dates.undecided("LYNN CITY COUNCIL MEETING MINUTES 1132026", near=date(2026, 12, 1), window=MINUTES)
    assert both == [("1132026", [date(2026, 1, 13), date(2026, 11, 3)])]
    none = dates.undecided("LYNN CITY COUNCIL AGENDA 2242025 AMENDED", near=date(2026, 2, 20), window=AGENDA)
    assert none == [("2242025", [date(2025, 2, 24)])]
    # Read, or not a day at all: nothing to report.
    assert dates.undecided("City Council Minutes 4142026", near=date(2026, 4, 30), window=MINUTES) == []
    assert dates.undecided("Scan 20260220111947665 page 1234567", near=date(2026, 2, 20), window=MINUTES) == []


def test_figures_run_together_and_written_dates_together():
    found = dates.find_dates("Minutes 1132026 (corrected 4/30/2026)", near=date(2026, 4, 30), window=MINUTES)
    assert [f.day for f in found] == [date(2026, 1, 13), date(2026, 4, 30)]


def test_a_window_needs_a_day():
    with pytest.raises(ValueError):
        dates.find_dates("Minutes 1132026", window=MINUTES)
    with pytest.raises(ValueError):
        dates.find_dates("Minutes 1132026", near=date(2026, 4, 30), window=(0, -365))


# ---- Taking dates out -------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Planning Board Meeting, December 17, 2026", "Planning Board Meeting,  "),
    ("Board of Health Meeting 10/13/2026", "Board of Health Meeting  "),
    ("10/21/2026 Commission on Disabilities Meeting", "  Commission on Disabilities Meeting"),
    # With the words that lead into it.
    ("Conservation Commission on Tuesday, October 13, 2026", "Conservation Commission  "),
    ("Meeting for Oct. 5, 2026 Agenda", "Meeting   Agenda"),
    # Every date, and nothing that isn't one.
    ("Agenda 10/13/26 (was 10/6/26)", "Agenda   (was  )"), ("Ward 3 Meeting", "Ward 3 Meeting"),
    # A period after the day, and figures with spaces at a title's end.
    ("Brockton City Council Meeting November 9. 2026", "Brockton City Council Meeting  "),
    ("Board of Assessors 11 5 26", "Board of Assessors  "), ("Ward 3 11 5 26", "Ward 3 11 5 26"),
])
def test_without_dates(text, expected):
    assert dates.without_dates(text) == expected


def test_without_dates_without_a_year():
    assert dates.without_dates("Planning Board Meeting December 17") == "Planning Board Meeting December 17"
    assert dates.without_dates("Planning Board Meeting December 17", yearless=True) == "Planning Board Meeting  "
    assert dates.without_dates("Hearing December 17th, continued", yearless=True) == "Hearing  , continued"
    assert dates.without_dates("Workshop Dec. 17-18", yearless=True) == "Workshop  -18"
    assert (dates.without_dates(" to 10-14-Beverly Regional Airport Commission", yearless=True)
            == "  -Beverly Regional Airport Commission")
    # February 29 is a day of some year; a number after a name that only starts like a month isn't a date.
    assert dates.without_dates("Budget Hearing February 29", yearless=True) == "Budget Hearing  "
    assert dates.without_dates("Marshall 3 Committee", yearless=True) == "Marshall 3 Committee"


@pytest.mark.parametrize("word, number", [
    ("January", 1), ("jan", 1), ("Jan.", 1), ("Sept", 9), ("SEPTEMBER", 9), ("Febuary", 2), ("Match", None), ("Monday", None),
])
def test_month_number(word, number):
    assert dates.month_number(word) == number


def test_an_exact_month_name():
    assert dates.month_number("Sept.", exact=True) == 9
    assert dates.month_number("Febuary", exact=True) is None
    assert dates.month_number("Marshall", exact=True) is None
