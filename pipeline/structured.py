"""Structured data for search engines: schema.org JSON-LD in a page's head.

Search engines read it to name the site in their results (WebSite), show a meeting as
an event (Event), place a page in the site (BreadcrumbList), and list the site's
downloads in dataset search (Dataset). It says only what the page itself shows: a
meeting is an event only when its listing says where it is, and it isn't marked as
one that may not take place.

The templates call these through the globals build_site gives them:
{{ structured(event(...), breadcrumbs(...)) }} in a page's structured_data block.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from markupsafe import Markup

# A meeting's status (fetch_meetings.py) as schema.org's.
STATUS = {"scheduled": "EventScheduled", "cancelled": "EventCancelled",
          "postponed": "EventPostponed", "rescheduled": "EventRescheduled"}
# What Publick compiles is free to reuse under CC BY 4.0 (the About page's "Reusing what's here").
CC_BY = "https://creativecommons.org/licenses/by/4.0/"
# 311 requests keep SeeClickFix's license.
SEECLICKFIX = "https://creativecommons.org/licenses/by-nc-sa/3.0/"


def script(*items: dict | None) -> Markup:
    """Each item as a <script type="application/ld+json">, leaving out None. The JSON is
    written so it can't end the script early ("</script>") or be read as markup."""
    out = []
    for item in items:
        if item:
            text = json.dumps({"@context": "https://schema.org", **item}, ensure_ascii=False, separators=(",", ":"))
            text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            out.append(f'<script type="application/ld+json">{text}</script>')
    return Markup("\n  ".join(out))


def plain(text) -> str:
    """A rendered block (a page's title or description) as plain text: one line, entities decoded."""
    return " ".join(html.unescape(str(text)).split())


def when(day: str, time: str | None, timezone: str) -> str:
    """A date, or a date and time with the town's offset that day: "2026-10-07T09:30:00-04:00"."""
    if not time:
        return day
    return datetime.fromisoformat(f"{day}T{time}").replace(tzinfo=ZoneInfo(timezone)).isoformat()


def publisher(site: dict, home: str) -> dict:
    """Who publishes the site: its network, or the site itself."""
    if site.get("network"):
        return {"@type": "Organization", "name": site["network"], **({"url": site["network_url"]} if site.get("network_url") else {})}
    return {"@type": "Organization", "name": site["name"], "url": home}


def website(site: dict, url: str, lang: str) -> dict:
    """The site, on its homepage: the name search results show for it."""
    return {"@type": "WebSite", "name": site["name"], "url": url, "inLanguage": lang,
            "description": site["tagline"], "publisher": publisher(site, url)}


def event(m: dict, url: str, description, town: dict, timezone: str) -> dict | None:
    """A meeting as an Event, or None when its listing doesn't say where it is, it's no longer
    listed, or a correction says it may not take place."""
    if not m["listed"] or m["status"] not in STATUS or (m.get("correction") or {}).get("doubtful"):
        return None
    place = m["address"] or m["location"]
    name = m["location_name"] or place
    locations = []
    if name:
        locations.append({"@type": "Place", "name": name, "address": {
            "@type": "PostalAddress", "streetAddress": place or name, "addressLocality": town["name"],
            "addressRegion": town["state_abbr"], "addressCountry": "US"}})
    if m["remote_url"]:
        locations.append({"@type": "VirtualLocation", "url": m["remote_url"]})
    if not locations:
        return None
    mode = "Mixed" if len(locations) == 2 else "Online" if m["remote_url"] else "Offline"
    item = {
        "@type": "Event", "name": m["title"], "url": url, "description": plain(description),
        "startDate": when(m["date"], m["start_time"], timezone),
        "eventStatus": f"https://schema.org/{STATUS[m['status']]}",
        "eventAttendanceMode": f"https://schema.org/{mode}EventAttendanceMode",
        "location": locations[0] if len(locations) == 1 else locations,
        "organizer": {"@type": "GovernmentOrganization", "name": m["body"], "areaServed": f"{town['name']}, {town['state']}"},
        "isAccessibleForFree": True,
    }
    if m["start_time"] and m["end_time"]:
        item["endDate"] = when(m["date"], m["end_time"], timezone)
    return item


def breadcrumbs(trail: list[tuple]) -> dict:
    """A page's place in the site: [(name, absolute URL), ...], ending with the page itself."""
    return {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": plain(name), "item": url} for i, (name, url) in enumerate(trail, 1)]}


def dataset(name: str, description, url: str, files: list[str], site: dict, town: dict, home: str,
            license: str = CC_BY, start: str | None = None, end: str | None = None) -> dict:
    """A page's downloads as a Dataset: files are their absolute URLs, all CSV; start and end
    the period they cover ("2026-06-30", or a month, "2025-10")."""
    item = {
        "@type": "Dataset", "name": name, "description": plain(description), "url": url,
        "license": license, "isAccessibleForFree": True, "creator": publisher(site, home),
        "spatialCoverage": {"@type": "Place", "name": f"{town['name']}, {town['state']}"},
        "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": f} for f in files],
    }
    if start and end:
        item["temporalCoverage"] = f"{start}/{end}"
    return item
