"""A meeting's video, from the docket its clerk posts on ClerkBase (clerkshq.com).

ClerkBase hosts a city clerk's dockets (a meeting's agenda) as pages saved
from Word, one a meeting, in a folder for its board, each named by the
meeting's date: <folder>/<yyyy>/<mon><dd>_<yy>dk.htm. Pawtucket's City
Council's of October 21, 2026 is
https://clerkshq.com/Content/Pawtucket-ri/council/2026/oct21_26dk.htm.

Once the meeting's video is indexed, about a week after the meeting, the
docket links it: "Click [video] to view the entire meeting", then a video
icon by each item, each the YouTube video at the item's time, through
OpenGov Video's player
(vid.opengovideo.com/playvideo.asp?sFileName=https://www.youtube.com/embed/<id>?start=<seconds>).
The meeting's video is the one most of the docket's links play: the
whole-meeting link is sometimes left from an earlier docket (Pawtucket's of
February 11, 2026 plays January 7's meeting). Where the meeting starts isn't
taken from it either: its start is often left from an earlier docket too
(4410 seconds on four of Pawtucket's in 2026), so the whole video is the
meeting's.

This module has the parts that need no network; pipeline/fetch_opengov_meetings.py
reads the dockets of the boards its [opengov_meetings.dockets] names.
"""

from __future__ import annotations

import html
import re
from collections import Counter

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
# A YouTube video in a link: the player's embed address, or a watch, live or short link.
YOUTUBE = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|live/)|youtu\.be/)([\w-]{11})(?![\w-])")
FOLDER = re.compile(r"https://clerkshq\.com/Content/[\w.-]+/[\w.-]+")


def docket_url(folder: str, date: str) -> str:
    """The docket of a meeting on date ('2026-10-21'): <folder>/2026/oct21_26dk.htm."""
    year, month, day = date.split("-")
    return f"{folder.rstrip('/')}/{year}/{MONTHS[int(month) - 1]}{day}_{year[2:]}dk.htm"


def video(page: str) -> str | None:
    """The YouTube video most of the docket's links play, or None: no video linked yet, or two
    linked as often."""
    found = Counter(m.group(1) for href in HREF.finditer(page)
                    for m in [YOUTUBE.search(html.unescape(href.group(1) or href.group(2) or ""))] if m)
    ranked = found.most_common(2)
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def check(dockets, where: str) -> None:
    """[opengov_meetings.dockets]: each board to its dockets' folder on ClerkBase."""
    if not isinstance(dockets, dict) or not dockets or not all(
            isinstance(k, str) and k and isinstance(v, str) and FOLDER.fullmatch(v.rstrip("/"))
            for k, v in dockets.items()):
        raise SystemExit(f'{where}: dockets must map boards to the folder of their dockets on ClerkBase, like '
                         '"City Council" = "https://clerkshq.com/Content/Pawtucket-ri/council".')
