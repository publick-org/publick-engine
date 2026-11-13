"""A city website's WordPress REST API, as the readers of its post types and plugins ask it.

A WordPress site answers at /wp-json/. Its core routes list each post type the site publishes,
/wp-json/wp/v2/<type>: posts and pages, and the types its plugins add, such as ProudCity's meetings
(/wp-json/wp/v2/meetings, pipeline/proudcity.py) or a document library's public-documents. A plugin can
add routes of its own: The Events Calendar's is /wp-json/tribe/events/v1/events. This module is what
their readers share; nothing here knows a post type or a plugin.

Paging. A listing gives at most per_page items a page (100 at most), and page=2 and on give the rest.
How many pages there are is a header of the first: X-WP-TotalPages on a core route, X-TEC-TotalPages on
The Events Calendar's (which also gives total_pages in its body). Asking past the last page is an error
on both (HTTP 400 on a core route, 404 on The Events Calendar's), so pages are read up to the count the
first gives, never until one fails. A listing of more pages than a reader allows (max_pages) is an
error, not read in part as if it were all of it.

The items. A core route's body is a list of them. The Events Calendar's is an object with them under a
key ("events"): read_all(key="events").

Fields. A core route returns only the fields named in _fields (`_fields=id,link,title,modified`), which
keeps a page of 100 small. The Events Calendar's route doesn't filter its items' fields:
`_fields=events.id` answers with no events at all. So a reader passes `fields` only to a core route.

Reading only what changed. A core route takes modified_after (WordPress 5.7 and later): the posts
modified after a time, in the site's local time, as each post's `modified` is. changed_since() gives it
from when the previous run started, two days earlier, which covers a site's clock in another time zone
and a post saved while that run was reading. With orderby=modified, a post modified between two pages
being read can move to a page already read: it was modified after this run started, so the next run
lists it.

Text. A title comes as HTML ("title": {"rendered": ...}), with entities ("Gas &#038; Electric",
"&#8211;") and, where it was typed in an editor, zero-width spaces ("​H​olyoke");
rendered() gives it as plain text.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

from pipeline.http import FetchError

# The most items WordPress gives a page.
PER_PAGE = 100
# The header with the number of pages: a core route's, then The Events Calendar's.
TOTAL_PAGES = ("x-wp-totalpages", "x-tec-totalpages")
# Spaces an editor leaves in text that show as nothing: zero-width ones and the byte-order mark.
INVISIBLE = re.compile("[​‌‍⁠﻿]")
# How much earlier than the previous run's start a run reads changes from.
MARGIN = timedelta(days=2)


def api_url(site_url: str, route: str, params: dict | None = None) -> str:
    """'https://www.holyoke.org', 'wp/v2/meetings', {...} -> 'https://www.holyoke.org/wp-json/wp/v2/meetings?...'."""
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    return f"{site_url.rstrip('/')}/wp-json/{route.strip('/')}" + (f"?{query}" if query else "")


def total_pages(headers, data=None) -> int | None:
    """How many pages a listing has: its X-WP-TotalPages or X-TEC-TotalPages header, or the
    total_pages its body gives; None if it gives neither."""
    for name, value in headers.items():
        if name.lower() in TOTAL_PAGES and str(value).strip().isdigit():
            return int(value)
    if isinstance(data, dict) and isinstance(data.get("total_pages"), int):
        return data["total_pages"]
    return None


def items_of(data, key: str | None = None) -> list:
    """A page's items: the body itself (a core route's) or the list under `key` (The Events Calendar's
    "events")."""
    found = data.get(key) if key and isinstance(data, dict) else data
    if not isinstance(found, list):
        raise ValueError(f"no list of items{f' under {key!r}' if key else ''}")
    return found


def parsed(response, url: str):
    """A response's JSON. A site that answers a REST address with a page (a security plugin's, or the
    API turned off) is an error naming the address."""
    try:
        return json.loads(response.text)
    except ValueError:
        raise FetchError(f"{url}: not JSON (is the site's REST API turned off?)") from None


def read_all(client, site_url: str, route: str, params: dict | None = None, *, key: str | None = None,
             fields: tuple[str, ...] = (), max_pages: int = 50) -> list:
    """Every item of a listing, page by page: per_page 100 unless params give another, and `fields`
    as _fields (a core route only). Raises FetchError for a listing of more than max_pages pages."""
    params = {"per_page": PER_PAGE, **(params or {}), **({"_fields": ",".join(fields)} if fields else {})}
    found, page, pages = [], 1, 1
    while page <= pages:
        url = api_url(site_url, route, {**params, **({"page": page} if page > 1 else {})})
        response = client.get(url)
        data = parsed(response, url)
        if page == 1:
            pages = total_pages(response.headers, data) or 1
            if pages > max_pages:
                raise FetchError(f"{url}: {pages} pages, more than the {max_pages} read in one run")
        try:
            found += items_of(data, key)
        except ValueError as e:
            raise FetchError(f"{url}: {e}") from None
        page += 1
    return found


def changed_since(started: str | None, margin: timedelta = MARGIN) -> str | None:
    """modified_after for a run whose previous run started at `started` (an ISO time, with or without
    its offset): that time less the margin, as the site's local time without an offset
    ('2026-11-13T06:05:00-05:00' -> '2026-11-11T06:05:00'). None for a first run."""
    if not started:
        return None
    return (datetime.fromisoformat(started).replace(tzinfo=None) - margin).isoformat(timespec="seconds")


def rendered(value) -> str:
    """A rendered field ({"rendered": "Gas &#038; Electric"}) or a string of HTML, as plain text."""
    text = value.get("rendered", "") if isinstance(value, dict) else str(value or "")
    return " ".join(INVISIBLE.sub("", html.unescape(re.sub(r"<[^>]+>", " ", text))).split())


def check_site_url(value, where: str) -> None:
    """A site_url setting: the site's address, with no path ("https://www.holyoke.org")."""
    if not re.fullmatch(r"https://[\w.-]+/?", str(value or "")):
        raise SystemExit(f'{where}: site_url must be the website\'s address, with no path, like "https://www.holyoke.org".')
