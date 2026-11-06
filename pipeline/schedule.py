"""Parser for a page that lists a board's meeting dates for the year.

Some boards publish their schedule as a plain list: Malden Public Schools' School
Committee Meetings page lists "Monday, October 5, 2026", "Monday, November 9,
2026" and so on, every year's dates under each other, with a meeting's agenda
posted elsewhere (Malden's Agenda Center) when it's ready. A date may be
written "November 9, 2026", "11/9/26" or "11-9-2026", and followed by the board's name
where the page lists more than one board.
"""

from __future__ import annotations

import html
import re

from pipeline import dates
from pipeline.meeting_names import find_board

SOURCE = "schedule"
def page_text(page: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def parse_dates(page: str) -> list[tuple[str, str]]:
    """(date, the words after it up to the next date) for every date on the page (pipeline/dates.py).
    Not "11-11-26": on a page, figures with dashes and a two-digit year are as often something else."""
    text = page_text(page)
    found = dates.find_dates(text, short_dashed_years=False)
    out = []
    for i, dated in enumerate(found):
        end = found[i + 1].start if i + 1 < len(found) else min(len(text), dated.end + 80)
        out.append((dated.day.isoformat(), text[dated.end:end].strip(" -–—,")))
    return out


def meetings(page: str, settings: dict, today: str, ahead: str) -> list[dict]:
    """The page's upcoming meetings, from today to `ahead`, as records for pipeline.fetch_meetings.
    Each is the one board in `body`, or the board `names` finds in the words after its date."""
    found = {}
    for day, words in parse_dates(page):
        body = settings.get("body") or find_board(words, [], settings.get("names", {}))
        if body and max(today, settings.get("since", today)) <= day <= ahead:
            key = f"{SOURCE}-{re.sub(r'[^a-z0-9]+', '-', body.lower()).strip('-')}-{day}"
            found[key] = {
                "id": key, "source": SOURCE, "source_url": settings["url"], "source_name": settings["source_name"],
                "date": day, "start_time": None, "end_time": None, "body": body, "title": f"{body} Meeting",
                "status": "scheduled", "special": False,
            }
    return list(found.values())


def check(config: dict) -> None:
    """A town's [schedule_meetings] table, checked when its config is loaded."""
    settings = config.get("schedule_meetings")
    if settings is None:
        return
    where = f"[schedule_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("url", "source_name") if not settings.get(key)]
    if missing or not (settings.get("body") or isinstance(settings.get("names"), dict)):
        raise SystemExit(f"{where} needs url, source_name, and the board: body, or names mapping the names after "
                         "each date to boards.")
