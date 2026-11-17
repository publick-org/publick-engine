"""A city's meetings from The Events Calendar, a WordPress plugin, through its REST API.

The Events Calendar (by The Events Calendar, formerly Modern Tribe; its routes are named "tribe") is a
widely used WordPress plugin for a site's calendar of events. Its REST API lists them at
/wp-json/tribe/events/v1/events, read through pipeline/wordpress.py. The API is the plugin's own and the
same on every site that runs it, so this module knows no city: a city's boards and the names it uses are
its [wordpress_meetings] settings. (The calendar's iCal feed, /events/?ical=1, lists only the calendar's
first page of events, so it isn't read.)

The listing. `start_date` and `end_date` (the site's local time) choose the events, `per_page` at most 50
(the plugin gives no more, whatever is asked), and the X-TEC-TotalPages header gives the number of pages.
Asking past the last page is an HTTP 404. Each event has:
- `id` (a recurring event's occurrences each have their own, above 10,000,000) and `url`, its page;
- `title`, as HTML ("Library Meeting &#8211; Canceled");
- `start_date` and `end_date`, in the site's local time ("2026-09-08 18:30:00"), and `all_day`;
- `categories`, each with its `name`: a city files a board's meetings under a category of the board's
  ("City Council", "Planning", "Council on Aging"), and other events under others ("Local", "Community
  Event"), or none;
- `venue`, a place (`venue`, `address`), or an empty list for none;
- `website` and `virtual_url`, either of which can be a Zoom meeting's link.

A meeting. An event is a board's meeting when it's filed under one of the categories its settings list
([wordpress_meetings.categories] maps each to the board it's for), and under none of exclude_categories (a
"Community Event" filed under "Planning" too), and its title isn't left out by exclude_pattern. Its board
is the longest of [wordpress_meetings.bodies] its title names ("School NCN Subcommittee" for a category
that holds a board's committees, or a school council), else its category's. Words after a dash give its
status ("Library Meeting – Canceled", "City Council Meeting Postponed to ..."), and "Special" makes a
special meeting (pipeline/meeting_names.py). "Rescheduled" in a title says, as on other calendars, that the
meeting was moved away from the event's date, and it isn't held then. A city that moves a meeting by moving
its event, and marks the event "– Rescheduled" at its new date, sets `rescheduled = "held"` (Brockton's
Redevelopment Authority meeting "– Rescheduled" to 2026-11-04 has that day's agenda); even there, a title
that says where it was moved to ("Rescheduled to November 20") is a meeting moved away. Its link is kept only when it's a Zoom meeting's, as the meeting's way to
take part (pipeline/civicplus.py is_zoom_link).

The reader's run is pipeline/fetch_wordpress_meetings.py. A city's document library on the same site,
joined to these events, is pipeline/wpdocs.py.
"""

from __future__ import annotations

import re

from pipeline import dates, wordpress, wpdocs
from pipeline.civicplus import is_zoom_link
from pipeline.meeting_names import find_board, parse_name, words

SOURCE = "wordpress"
# The plugin's REST route, and the most events it gives a page.
ROUTE = "tribe/events/v1/events"
CATEGORIES_ROUTE = "tribe/events/v1/categories"
PER_PAGE = 50
# "Rescheduled" followed by where the meeting went: "Rescheduled to November 20", "rescheduled for 12/3".
MOVED_TO = re.compile(r"\brescheduled\s+(?:to|for|until)\b", re.I)
# The readings of an event titled "Rescheduled" ([wordpress_meetings] rescheduled).
RESCHEDULED = ("moved", "held")
# The namespace that says a site runs the plugin, among those /wp-json/ lists.
NAMESPACE = "tribe/events/v1"


def listing_params(start: str, end: str) -> dict:
    """The events from the start of one day to the end of another (YYYY-MM-DD, the site's local time)."""
    return {"start_date": f"{start} 00:00:00", "end_date": f"{end} 23:59:59", "per_page": PER_PAGE}


def parse_event(item: dict) -> dict:
    """An event as the listing gives it: its id, page, title as plain text, date, start and end time, place,
    Zoom link, categories' names, and when it was last changed."""
    start, end = str(item.get("start_date") or ""), str(item.get("end_date") or "")
    venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
    remote = next((u for u in (item.get("website"), item.get("virtual_url"))
                   if isinstance(u, str) and is_zoom_link(u.strip())), None)
    all_day = bool(item.get("all_day"))
    return {
        "id": str(item["id"]),
        "url": item.get("url") or "",
        "title": wordpress.rendered(item.get("title")),
        "date": start[:10],
        "start_time": None if all_day else (start[11:16] or None),
        "end_time": None if all_day or end[:10] != start[:10] else (end[11:16] or None),
        "location_name": wordpress.rendered(venue.get("venue")),
        "address": ", ".join(p for p in (wordpress.rendered(venue.get("address")), wordpress.rendered(venue.get("city")))
                             if p),
        "remote_url": remote.strip() if remote else None,
        "categories": [wordpress.rendered(c.get("name")) for c in item.get("categories") or [] if isinstance(c, dict)],
        "modified": str(item.get("modified") or ""),
    }


def categories(settings: dict) -> dict[str, str]:
    """[wordpress_meetings.categories]: each category's name, lowercase, and the board it's for."""
    return {name.strip().lower(): board for name, board in settings.get("categories", {}).items()}


def category_board(event: dict, settings: dict) -> str | None:
    """The board of the first category of [wordpress_meetings.categories], in the config's order, that the
    event is filed under; None if it's under none, or under one of exclude_categories."""
    listed = categories(settings)
    excluded = {c.strip().lower() for c in settings.get("exclude_categories", [])}
    names = {c.strip().lower() for c in event["categories"]}
    if names & excluded:
        return None
    return next((board for name, board in listed.items() if name in names), None)


def excluded(title: str, settings: dict) -> bool:
    """Whether exclude_pattern leaves a title out."""
    exclude = settings.get("exclude_pattern")
    return bool(exclude and re.search(exclude, title, re.I))


def board_named(title: str, settings: dict) -> str | None:
    """The longest of [wordpress_meetings.bodies] a title names, or None."""
    return find_board(dates.without_dates(title), [], settings.get("bodies", {}))


def meeting_info(event: dict, settings: dict) -> dict | None:
    """An event's board, status, whether it's a special meeting, and whether its board came from its category
    alone (by_category); None for an event that isn't a listed board's meeting."""
    category = category_board(event, settings)
    if category is None or excluded(event["title"], settings):
        return None
    named = board_named(event["title"], settings)
    body = named or category
    found = parse_name(event["title"], [body], {})
    status = found["status"]
    if status == "rescheduled" and held_when_rescheduled(event["title"], settings):
        status = "scheduled"
    return {"body": body, "status": status, "special": found["special"], "by_category": not named}


def held_when_rescheduled(title: str, settings: dict) -> bool:
    """Whether an event titled "Rescheduled" is its meeting, held at the event's date: only where the city moves
    a meeting's event and marks it so (rescheduled = "held"), and its title doesn't say where it was moved to."""
    if settings.get("rescheduled", "moved") != "held":
        return False
    after = re.split(r"\brescheduled\b", title, maxsplit=1, flags=re.I)[-1]
    return not (MOVED_TO.search(title) or dates.find_date(after))


def name_of(title: str) -> str:
    """A title without its status: "Library Meeting – Canceled" -> "Library Meeting"."""
    return parse_name(title)["title"]


def says_board(name: str, body: str) -> bool:
    """Whether a name has every word of a board's: "Finance Committee Meeting" does "Finance Committee",
    "COA Meeting" doesn't "Council on Aging"."""
    return set(words(body).split()) <= set(words(name).split())


def page_url(settings: dict) -> str:
    """The site's calendar, linked as where the city posts its meetings."""
    return settings.get("page_url") or f"{settings['site_url'].rstrip('/')}/events/"


# ---- The town's config --------------------------------------------------------------------

def check(config: dict) -> None:
    """A town's [wordpress_meetings] table, checked when its config is loaded."""
    settings = config.get("wordpress_meetings")
    if settings is None:
        return
    where = f"[wordpress_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("site_url", "source_name", "since", "categories") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the website's address, who posts it, the first "
                         "meeting date to collect, and the calendar's categories of boards' meetings.")
    wordpress.check_site_url(settings["site_url"], where)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-07-01".')
    for key, example in (("categories", '"Planning" = "Planning Board"'), ("bodies", '"COA" = "Council on Aging"')):
        table = settings.get(key, {})
        if not isinstance(table, dict) or not all(isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
                                                  for k, v in table.items()):
            raise SystemExit(f"{where}: {key} must map names to board names, like {example}.")
    exclude = settings.get("exclude_categories", [])
    if not isinstance(exclude, list) or not all(isinstance(c, str) and c.strip() for c in exclude):
        raise SystemExit(f'{where}: exclude_categories must be a list of category names, like ["Community Event"].')
    if "exclude_pattern" in settings:
        try:
            re.compile(settings["exclude_pattern"])
        except (re.error, TypeError) as e:
            raise SystemExit(f"{where}: exclude_pattern isn't a regular expression ({e}).") from None
    for key in ("days_ahead", "recheck_days", "max_documents_per_run"):
        if key in settings and not (isinstance(settings[key], int) and not isinstance(settings[key], bool)
                                    and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
    if not isinstance(settings.get("minutes", True), bool):
        raise SystemExit(f"{where}: minutes must be true or false.")
    if settings.get("rescheduled", "moved") not in RESCHEDULED:
        raise SystemExit(f'{where}: rescheduled must be "moved" (an event titled "Rescheduled" isn\'t held at its date, '
                         'the default) or "held" (the city moves the event, and marks it "Rescheduled" at its new date).')
    if "page_url" in settings and not str(settings["page_url"]).startswith("https://"):
        raise SystemExit(f"{where}: page_url must be the calendar's address, starting https://.")
    if "documents" in settings:
        wpdocs.check(settings["documents"], f"[wordpress_meetings.documents] in config/{config['slug']}.toml")
