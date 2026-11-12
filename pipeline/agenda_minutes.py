"""Minutes a board posts only as a link in a later meeting's agenda.

Biddeford School Committee posts no minutes of its own: each meeting's packet, a Google Doc, has an item
"Approval of Last Meeting's Minutes: 9/23/2026" whose date links the minutes of that meeting, another
Google Doc. A reader whose board does this ([page_meetings] minutes_in_agenda = true) gives its meetings to
link(), which reads each agenda that's a Google Doc as HTML and takes each link it finds as the minutes of
the same board's meeting on its date.

This is a way of posting minutes, not a platform: it's kept out of the reader of the page that lists the
meetings (pipeline/linkpage.py), so a reader of any other listing can use it the same way.
"""

from __future__ import annotations

import html
import re
from urllib.parse import unquote

from pipeline import dates, finalsite
from pipeline.http import FetchError

# A Google redirect around a link in a Google Doc's HTML export ("https://www.google.com/url?q=<link>&sa=...").
GOOGLE_REDIRECT = re.compile(r"^https://www\.google\.com/url\?q=([^&]+)")
LINK = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)


def minutes_in(agenda: str) -> list[tuple[str, str]]:
    """The minutes an agenda links, as (date, link): a paragraph of the agenda that says "minutes" and links
    a document labelled with a date (Biddeford School Committee's packets: "5. Approval of Last Meeting's
    Minutes: 9/23/2026 (Motion Needed)", "9/23/2026" linking that meeting's minutes, a Google Doc). agenda
    is the agenda as HTML (a Google Doc's export). A date with dashes and a two-digit year isn't read, as on
    a page of links (pipeline/linkpage.find_date)."""
    found = []
    for paragraph in re.split(r"</p>|</li>", agenda, flags=re.I):
        if not re.search(r"\bminutes\b", finalsite.text(paragraph), re.I):
            continue
        for href, label in LINK.findall(paragraph):
            url = html.unescape(href)
            wrapped = GOOGLE_REDIRECT.match(url)
            url = unquote(wrapped.group(1)) if wrapped else url
            dated = dates.find_dates(finalsite.text(label), short_dashed_years=False)
            if dated and finalsite.is_document(url):
                found.append((dated[0].day.isoformat(), url))
    return found


def link(client, found: list[dict], since: str, today: str, known: dict, errors: list[str]) -> None:
    """Give each meeting of `found` (each with its body, date and any agenda_url) the minutes a later
    meeting's agenda links. Each agenda that's a Google Doc, of a meeting from `since` on, is read as HTML
    until it links minutes or its meeting has passed, then not again (`known`, by the agenda's address,
    kept by the caller between runs), and each link is the minutes of the same board's meeting on its
    date, where that meeting has none of its own."""
    by_day = {(m["body"], m["date"]): m for m in found}
    for item in found:
        agenda = item.get("agenda_url")
        if item["date"] < since or not agenda or not finalsite.is_google_doc(agenda):
            continue
        if agenda not in known:
            doc = finalsite.file_key(agenda)
            try:
                page = client.get(f"https://docs.google.com/document/d/{doc}/export?format=html").text
            except FetchError as e:
                errors.append(f"{agenda}: {e}")
                continue
            links = minutes_in(page)
            if not links and item["date"] >= today:
                continue   # read again next run: the agenda may not link them yet
            known[agenda] = links
        for day, url in known[agenda]:
            earlier = by_day.get((item["body"], day))
            if earlier and day < item["date"] and not earlier.get("minutes_url"):
                earlier["minutes_url"] = url


def check(settings: dict, where: str) -> None:
    if not isinstance(settings.get("minutes_in_agenda", False), bool):
        raise SystemExit(f"{where}: minutes_in_agenda must be true or false.")
