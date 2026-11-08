"""The meeting sources a town can have besides its city's own listings, each with a config table of its own.

A town's city calendar, Agenda Center, CivicClerk, Legistar, PrimeGov and file lists are read by
pipeline/fetch_meetings.py from [meetings]. The sources here are the others: a school district's
website or portal, the state's Open Meetings portal, a city's events calendar, a calendar feed, or a
page of the year's dates. Each is a [<source>_meetings] table, and each meeting it lists carries its
`source`.

This list is the one place a source is named. The daily update's steps (pipeline/update.py), the
config's tables and the checks run as it's loaded (pipeline/config.py), the order a meeting's listings
are linked and shown in (pipeline/listings.py), which listings carry minutes (pipeline/absences.py),
and the About page's list of who posts the town's meetings (pipeline/build_site.py) are all read from
it. A new source is its module(s) and one entry here, in the place its listings rank.

The order is the order the board's own sources rank in: where a meeting's listings disagree on a link,
the one listed later here is linked (listings.LISTING_ORDER), and where more than one has its minutes,
they're named in this order (absences.MINUTES_ORDER). Sources that only date a meeting (documents=False)
rank below every source of documents, wherever they're listed here.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass


def N_(message: str) -> str:
    """Marks a string for translation, as pipeline.i18n.N_ does: that module imports pipeline.config,
    which reads this one's tables as it loads."""
    return message


@dataclass(frozen=True)
class MeetingSource:
    # The `source` each meeting it lists carries ("diligent").
    source: str
    # Where it lists a meeting, for "Not listed on the ..." on a meeting's page. None: the city
    # calendar's wording (build_site.CALENDAR).
    listing: str | None
    # The daily update's step that reads it, and the module the step runs (python -m), for a source
    # with a reader of its own; None for one pipeline/fetch_meetings.py reads with the city's.
    step: str | None = None
    module: str | None = None
    # The module whose check(config) stops a table that can't work as the config is loaded.
    checker: str | None = None
    # Whether its listings carry a meeting's documents; a calendar feed and a page of dates don't.
    documents: bool = True
    # The table's key for the page the About page links as who posts these meetings.
    page_key: str = "page_url"

    @property
    def table(self) -> str:
        return f"{self.source}_meetings"


SOURCES = (
    MeetingSource("drive", None, "Fetch School Committee documents", "pipeline.fetch_drive_meetings"),
    MeetingSource("finalsite", N_("school district's website"), "Fetch school board meetings",
                  "pipeline.fetch_finalsite_meetings", "pipeline.finalsite"),
    MeetingSource("page", N_("school district's website"), "Fetch school board documents",
                  "pipeline.fetch_page_meetings", "pipeline.linkpage"),
    MeetingSource("diligent", N_("school district's meeting portal"), "Fetch Diligent Community meetings",
                  "pipeline.fetch_diligent_meetings", "pipeline.diligent"),
    MeetingSource("boardbook", N_("school district's meeting portal"), "Fetch BoardBook meetings",
                  "pipeline.fetch_boardbook_meetings", "pipeline.boardbook"),
    MeetingSource("opengov", N_("state's Open Meetings portal"), "Fetch Open Meetings portal meetings",
                  "pipeline.fetch_opengov_meetings", "pipeline.opengov"),
    MeetingSource("meetingdocs", N_("city's events calendar"), "Fetch the city's meeting documents",
                  "pipeline.fetch_meetingdocs_meetings", "pipeline.meetingdocs"),
    MeetingSource("sharpschool", N_("school district's website"), "Fetch the school district's minutes library",
                  "pipeline.fetch_sharpschool_meetings", "pipeline.sharpschool"),
    # Read by pipeline/fetch_meetings.py, with the city's listings: they only date a meeting.
    MeetingSource("ical", N_("school district's website"), checker="pipeline.ical", documents=False),
    MeetingSource("schedule", N_("published schedule"), checker="pipeline.schedule", documents=False, page_key="url"),
)

BY_SOURCE = {s.source: s for s in SOURCES}
TABLES = tuple(s.table for s in SOURCES)
# The sources whose listings carry documents, in the order they rank, and those that only date a meeting.
DOCUMENTS = tuple(s.source for s in SOURCES if s.documents)
DATES_ONLY = tuple(s.source for s in SOURCES if not s.documents)
# Where each lists a meeting, for those that say it some other way than the city calendar's wording.
LISTINGS = {s.source: s.listing for s in SOURCES if s.listing}
# The sources with a reader of their own: one update step each.
READERS = tuple(s for s in SOURCES if s.module)


def check(config: dict) -> None:
    """Stop a meeting source's table that can't work, as the config is loaded, not partway through a run."""
    for source in SOURCES:
        if source.checker:
            importlib.import_module(source.checker).check(config)


def posted_by(config: dict, leave_out: tuple[str, ...] = ()) -> list[dict]:
    """Who posts the town's meetings from each of these sources it has but those left out, as its table names
    them (source_name, and the page to link), in the order they rank."""
    return [{"name": config[s.table]["source_name"], "url": config[s.table][s.page_key]}
            for s in SOURCES if s.table in config and s.source not in leave_out]
