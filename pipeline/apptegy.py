"""Apptegy, the platform of many school districts' websites: what its pages hold that a page of links doesn't show.

The generic reader of a page of meeting links (pipeline/linkpage.py) reads an Apptegy page as any other;
what's here is only what the platform does its own way, read the same for every district on it:

- A page's content as its script holds it (window.clientWorkStateTemp = JSON.parse("...")): its layout
  rows, columns and content (structure()), and the columns a heading names (columns()): Biddeford
  Schools' "Agendas and Minutes" page, a column a board's ([page_meetings.columns]).
- A document folder (/documents/.../<folder id>), whose files are listed only in the page's Nuxt payload
  (__NUXT_DATA__), not as links (folder()): Manchester School District's. Its agendas and minutes are
  read as a list of links (folder_links()), and a folder of years from `since` on (year_folders()).
- A short link (aptg.co/..., SHORT_LINK), which South Burlington School District links about half its
  agendas and minutes by: the reader follows it to the file it leads to.

A page whose content is HTML escaped inside a script's JSON (Worcester Public Schools') is read back by
linkpage.readable(), since any site can do that.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin

from pipeline import finalsite

# A link that redirects to the document it names: an Apptegy short link ("https://aptg.co/Lj5CdR"), followed
# by pipeline/fetch_page_meetings.py.
SHORT_LINK = re.compile(r"^https?://(?:www\.)?aptg\.co/\w+/?$", re.I)


# An Apptegy page's content, as its script holds it: window.clientWorkStateTemp = JSON.parse("...").
STATE = re.compile(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)')


def structure(page: str) -> dict | None:
    """The tree of an Apptegy page's content (page.content.structure in the JSON its script parses):
    nodes of layout rows (LAYOUT_NODE_*), their columns (BASE_COLUMN), and content (CONTENT_NODE_*,
    with its HTML in content.html). None for a page that isn't one."""
    for found in STATE.finditer(page):
        try:
            state = json.loads(json.loads(f'"{found.group(1)}"'))
        except ValueError:
            continue
        tree = ((state.get("page") or {}).get("content") or {}).get("structure") if isinstance(state, dict) else None
        if isinstance(tree, dict):
            return tree
    return None


def node_html(node: dict) -> str:
    """A node's HTML: its own content's and its children's, in order."""
    content = node.get("content")
    own = content.get("html", "") if isinstance(content, dict) and isinstance(content.get("html"), str) else ""
    return own + "".join(node_html(child) for child in node.get("nodes") or [] if isinstance(child, dict))


def columns(page: str) -> dict[str, str]:
    """An Apptegy page's columns that a heading names, as {heading: the column's HTML}. A column's
    heading is its first node, a heading; or, for a column of a row just under a row of as many
    columns each holding only a heading, the heading above it (Biddeford's: a row of four headings,
    then a row of four accordions)."""
    found = structure(page)
    found_columns: dict[str, str] = {}

    def heading(node: dict) -> str | None:
        return finalsite.text(node_html(node)) or None if node.get("type") == "CONTENT_NODE_HEADING" else None

    def walk(node: dict) -> None:
        above: list[str] | None = None
        for child in node.get("nodes") or []:
            if not isinstance(child, dict):
                continue
            cells = [c for c in child.get("nodes") or [] if isinstance(c, dict) and c.get("type") == "BASE_COLUMN"]
            if not str(child.get("type", "")).startswith("LAYOUT_NODE") or not cells:
                above = None
                walk(child)
                continue
            contents = [[n for n in c.get("nodes") or [] if isinstance(n, dict)] for c in cells]
            names = [heading(nodes[0]) if nodes else None for nodes in contents]
            if all(len(nodes) == 1 and name for nodes, name in zip(contents, names)):
                above = names
                continue
            for i, nodes in enumerate(contents):
                name = names[i] if nodes and names[i] else above[i] if above and len(above) == len(cells) else None
                rest = nodes[1:] if nodes and names[i] else nodes
                if name:
                    found_columns[name] = "".join(node_html(n) for n in rest)
            above = None
            for nodes in contents:
                for n in nodes:
                    walk(n)

    if found:
        walk(found)
    return found_columns


# A Nuxt site's state, as the page carries it: a JSON array whose containers hold the indexes of their values.
NUXT_DATA = re.compile(r'<script\b[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S | re.I)
# The wrappers Nuxt puts around a value ("Reactive", index): the value is the index's.
NUXT_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef"}


def folder(page: str) -> list[dict] | None:
    """An Apptegy document folder's items, as its page's Nuxt payload has them
    (data["document-folder-id-<id>"].foldersAndDocuments): its files ({"file_name", "url", "position"})
    and its folders ({"folder_name", "url", "isFolder"}). None for a page that isn't one."""
    found = NUXT_DATA.search(page)
    if not found:
        return None
    try:
        data = json.loads(found.group(1))
    except ValueError:
        return None
    if not isinstance(data, list) or not data:
        return None

    def value(i, depth: int = 0):
        # An index into the payload, read back into what it stands for; a negative one is undefined or null.
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(data) or depth > 12:
            return None
        v = data[i]
        if isinstance(v, list):
            if len(v) == 2 and v[0] in NUXT_WRAPPERS:
                return value(v[1], depth + 1)
            return [value(x, depth + 1) for x in v]
        if isinstance(v, dict):
            return {k: value(x, depth + 1) for k, x in v.items()}
        return v

    # The payload's first entry is its root, inside a wrapper ("ShallowReactive", index): read only its "data".
    root, depth = data[0], 0
    while isinstance(root, list) and len(root) == 2 and root[0] in NUXT_WRAPPERS and depth < 5:
        root, depth = data[root[1]] if isinstance(root[1], int) and 0 <= root[1] < len(data) else None, depth + 1
    state = value(root["data"], 1) if isinstance(root, dict) and "data" in root else None
    if not isinstance(state, dict):
        return None
    folders = [f for key, f in state.items() if key.startswith("document-folder-id-") and isinstance(f, dict)]
    if not folders:
        return None
    return [item for f in folders for item in f.get("foldersAndDocuments") or [] if isinstance(item, dict)]


def position(item: dict) -> int:
    try:
        return int(item.get("position") or 0)
    except (TypeError, ValueError):
        return 0


def folder_links(items: list[dict], wanted) -> str:
    """A folder's files that wanted(file name) keeps (a reader's agendas and minutes) as a list of links,
    labelled with their names, newest first (the folder lists them oldest first, by position). Its other
    files (memos, handouts) are left out."""
    files = sorted((item for item in items if item.get("file_name") and item.get("url")
                    and wanted(str(item["file_name"]))), key=position, reverse=True)
    return "<ul>" + "".join(f'<li><a href="{html.escape(str(f["url"]))}">{html.escape(str(f["file_name"]))}</a></li>'
                            for f in files) + "</ul>"


def year_folders(page: str, page_url: str, since: str) -> list[str]:
    """An Apptegy document folder's folders named by a year ("2025", "2026"), from since's year on, newest
    first: a board's folder of years, each year's files in a folder of its own."""
    items = folder(page) or []
    years = [(int(item["folder_name"]), item["url"]) for item in items
             if item.get("url") and re.fullmatch(r"\d{4}", str(item.get("folder_name") or "").strip())
             and int(item["folder_name"]) >= int(str(since)[:4])]
    return [urljoin(page_url, str(url)) for _, url in sorted(set(years), reverse=True)]
