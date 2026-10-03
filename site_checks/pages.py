"""Which pages the browser checks run on.

Every page is checked for structure and links, which is quick. The browser
checks (axe, at two widths) take a second or two a page, so a
daily run checks a sample: every page written by hand (one per file under
site/pages/), and of the pages made from each record template (a meeting, a
board, a ward, a 311 category) the first and the largest. Record pages of one
template share a parent path, and the largest is the likeliest to hold data
that breaks a layout. A full run checks every page; it's the default, and the
network runs it whenever the engine or a town's config changes.

PUBLICK_CHECK_PAGES=sample picks the sample; anything else checks every page.

A page that sends the reader on at once (a meeting's moved page, which has a
meta refresh to where it's shown now) isn't opened in the browser: the browser
leaves it while the checks run. Its structure and links are checked with every
other page, and the page it points to is checked in the browser.
"""

from __future__ import annotations

from collections.abc import Callable, Collection


def redirects(html: str) -> bool:
    """Whether a page sends the reader on at once, by a meta refresh."""
    return 'http-equiv="refresh"' in html


def parent(path: str) -> str:
    """/meetings/2026-10-05-city-council/ -> /meetings/"""
    return path.rstrip("/").rsplit("/", 1)[0] + "/"


def sample(paths: list[str], size: Callable[[str], int], hand_written: Collection[str]) -> list[str]:
    chosen = {p for p in paths if p in hand_written}
    records: dict[str, list[str]] = {}
    for path in sorted(p for p in paths if p not in hand_written):
        records.setdefault(parent(path), []).append(path)
    for members in records.values():
        chosen.add(members[0])
        chosen.add(max(members, key=lambda p: (size(p), p)))
    return sorted(chosen)
