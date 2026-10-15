"""A share card for each recent and upcoming meeting: the image a link to its page shows on
Facebook, in a message, or anywhere else that draws a preview.

A 1200 x 630 PNG in the site's masthead style: the board, the date and time, and what the
meeting will take up or decided (the AI summary's headline, labeled as one), or that it was
cancelled. The town's own share image (pipeline/make_share_image.py) is every other page's.
Cards are drawn for meetings from CARD_DAYS ago on, the ones people share; older meetings keep
the town's image. They're English only: another language's pages keep the town's image too.

Drawn by the build (build_site.py, --no-share-cards to skip), with Playwright: one browser for
the town, each card filled into the same page and screenshotted. Each card's address carries
a version from what it says, so a site that cached a card picks up a new one when the agenda's
headline becomes the minutes'.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, timedelta
from html import escape
from pathlib import Path

from pipeline.config import colors

# Meetings this many days back, and every later one, get a card. The summaries' remake_since
# (summarize.KINDS) was set this far back when their headlines were rewritten for the cards.
CARD_DAYS = 30
FOLDER = "share/meetings"
STATIC = Path(__file__).resolve().parent.parent / "site" / "static"
STATUS = {"cancelled": "Cancelled", "postponed": "Postponed", "rescheduled": "Rescheduled"}


def card(m: dict, today: date, domain: str, when: str) -> dict | None:
    """What a meeting's card says, or None for a meeting before the window. when is its date
    and time as its page writes them."""
    if m["date"] < (today - timedelta(days=CARD_DAYS)).isoformat():
        return None
    ms, pv = m.get("minutes_summary"), m.get("preview")
    ms = ms if ms and ms.get("is_minutes", True) and ms.get("headline") else None
    pv = pv if pv and pv.get("headline") else None
    if m["status"] in STATUS:
        kicker, headline, foot = STATUS[m["status"]], "", domain
    elif ms:
        kicker, headline, foot = "What was decided", ms["headline"], f"AI summary of the minutes · {domain}"
    elif pv:
        kicker, headline, foot = "On the agenda", pv["headline"], f"AI summary of the agenda · {domain}"
    else:
        kicker, headline, foot = ("Coming up" if m["date"] >= today.isoformat() else ""), "", domain
    return {"board": m["body"], "when": when, "kicker": kicker, "headline": headline, "foot": foot}


def version(c: dict) -> str:
    """The card's ?v=, from what it says and how it's drawn."""
    return hashlib.sha256(json.dumps([c, PAGE], sort_keys=True).encode()).hexdigest()[:10]


def alt(c: dict) -> str:
    """The card's text, for og:image:alt."""
    return ". ".join(part for part in (f"{c['board']}, {c['when']}", c["kicker"], c["headline"]) if part)


def data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


# The card's page: the masthead as at the top of every page, then the meeting. fill() puts each
# card's text in; a long board name or headline is set smaller, and a headline is cut at 3 lines.
PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: "Public Sans"; font-weight: 400; src: url("{sans}"); }}
@font-face {{ font-family: "Source Serif 4"; font-weight: 700; src: url("{serif}"); }}
html, body {{ margin: 0; width: 1200px; height: 630px; }}
body {{ font-family: "Public Sans", sans-serif; background: #fff; color: {ink}; box-sizing: border-box;
  padding: 56px 72px; display: flex; flex-direction: column; }}
.brand {{ display: flex; align-items: center; gap: 18px; font-family: "Source Serif 4", serif; font-weight: 700; font-size: 40px; }}
.brand img {{ width: 56px; height: 56px; border: 2px solid {ink}; }}
.brand span {{ color: {network}; }}
.rule {{ margin: 26px 0 34px; border-top: 2px solid {ink}; border-bottom: 6px double {ink}; height: 4px; }}
#board {{ font-family: "Source Serif 4", serif; font-weight: 700; font-size: 64px; line-height: 1.08;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
#board.long {{ font-size: 50px; }}
#when {{ margin-top: 12px; font-size: 30px; color: #3d444c; }}
#kicker {{ margin-top: 34px; font-size: 22px; letter-spacing: .08em; text-transform: uppercase; color: {network}; }}
#kicker:empty {{ display: none; }}
#headline {{ margin-top: 8px; font-size: 32px; line-height: 1.3;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
#headline.long {{ font-size: 28px; }}
#foot {{ margin-top: auto; font-size: 20px; color: #5b636b; }}
</style></head><body>
<div class="brand"><img src="{icon}" alt="">{prefix}<span>{suffix}</span></div>
<div class="rule"></div>
<div id="board"></div><div id="when"></div><div id="kicker"></div><div id="headline"></div><div id="foot"></div>
</body></html>"""

FILL = """c => {
  for (const k of ["board", "when", "kicker", "headline", "foot"]) document.getElementById(k).textContent = c[k];
  document.getElementById("board").classList.toggle("long", c.board.length > 34);
  document.getElementById("headline").classList.toggle("long", c.headline.length > 170);
}"""


def page_html(config: dict, town_static: Path) -> str:
    site = config["site"]
    icon = town_static / "favicon.svg" if (town_static / "favicon.svg").exists() else STATIC / "favicon.svg"
    return PAGE.format(
        sans=data_url(STATIC / "fonts" / "public-sans-400.woff2", "font/woff2"),
        serif=data_url(STATIC / "fonts" / "source-serif-4-700.woff2", "font/woff2"),
        ink=colors(config)["primary_dark"], network=colors(config)["network"],
        icon=data_url(icon, "image/svg+xml"), prefix=escape(site["name_prefix"]), suffix=escape(site["name_suffix"]))


def write(cards: dict[str, dict], out_static: Path, config: dict, town_static: Path) -> None:
    """Draw each card (by meeting slug) to out_static/share/meetings/<slug>.png."""
    if not cards:
        return
    from playwright.sync_api import sync_playwright

    folder = out_static / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        page.set_content(page_html(config, town_static))
        page.evaluate("document.fonts.ready")
        for slug, c in cards.items():
            page.evaluate(FILL, c)
            page.screenshot(path=str(folder / f"{slug}.png"))
        browser.close()
