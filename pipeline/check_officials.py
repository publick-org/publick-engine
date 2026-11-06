"""Each body's members, as the town's config lists them, looked for on the body's own page -> data/officials_check.json.

A town's [officials] are kept by hand (pipeline/officials.py), so they go out of
date when a member resigns, a seat is filled, or an election's results aren't
recorded. Each body's `url` is its official page on the city's or the school
district's website, and nearly every such page names its members. So once a
week, each member's surname is looked for on that page. A member the page
doesn't name may have left, or be named another way there; a page that names
none of the body's members probably doesn't list them (a better `url` for the
body fixes that). Either way it's a lead for a person: nothing is changed in the
config. The network's report (pipeline/network.py officials) lists every town's
leads in one issue.

Surnames, not whole names: a page writes "Dan" for "Daniel", or leaves out a
middle initial, and both still match. A page that names none of the members as
first fetched is read again in a browser, if one is installed, since many
school districts' pages fill in their members with JavaScript.

It runs when the last check is CHECK_DAYS old, or when a body's members or page
differ from those last checked (a person fixed the list: the lead goes away
with the next run); --force checks anyway.

Usage:
    python -m pipeline.check_officials [--town gloucester] [--force]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import officials
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.files import write_atomic
from pipeline.http import FetchError, PoliteClient

RESULT_FILE = "officials_check.json"
CHECK_DAYS = 7
# Endings that aren't a surname: "David P. Beauregard, Jr." is looked for as "beauregard".
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
BROWSER_TIMEOUT_MS = 45_000


def plain(text: str) -> str:
    """Lowercase, without accents, curly quotes, or runs of spaces: "José O’Neil" -> "jose o'neil"."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "}))
    return " ".join(text.lower().split())


def page_text(page: str) -> str:
    """A page's words, as plain() writes them, without its scripts and styles."""
    page = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1\s*>", " ", page)
    return plain(html.unescape(re.sub(r"<[^>]+>", " ", page)))


def surname(name: str) -> str:
    """'Jacqueline "Nikki" O'Toole' -> "o'toole"; 'David P. "D.J." Beauregard, Jr.' -> "beauregard"."""
    words = re.findall(r"[a-z][a-z'\-]*", re.sub(r'"[^"]*"', " ", plain(name)))
    words = [w for w in words if w.strip("'-") not in SUFFIXES]
    return words[-1].strip("'-") if words else plain(name)


def named(name: str, text: str) -> bool:
    """Whether the page's text (page_text()) names the member: their surname, as a word of its own ("Dexter"
    isn't named by "Dexter-Smith" or "O'Dexter"; "Dexter's" names Dexter)."""
    return re.search(rf"(?<![a-z'\-]){re.escape(surname(name))}(?![a-z\-])", text) is not None


def members(body: dict, where: str, today: date) -> list[str]:
    """The names of the body's members today: its incoming members once they've taken office."""
    coming = officials.incoming(body, where)
    sitting = coming["members"] if coming and today >= coming["takes_office"] else body.get("members", [])
    return [m["name"] for m in sitting]


def rendered(url: str, user_agent: str) -> str | None:
    """The page's text once its scripts have run, from a browser; None if no browser is installed."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=user_agent)
                page.goto(url, wait_until="networkidle", timeout=BROWSER_TIMEOUT_MS)
                return plain(page.inner_text("body"))
            finally:
                browser.close()
    except Error as e:
        raise FetchError(f"{url}: the browser couldn't load it: {str(e).splitlines()[0]}") from e


def due(config: dict, previous: dict, now: datetime) -> bool:
    """Whether to check: the last check is CHECK_DAYS old, or a body's members or page changed since."""
    if not previous.get("checked_at"):
        return True
    if now - datetime.fromisoformat(previous["checked_at"]) >= timedelta(days=CHECK_DAYS):
        return True
    where, today = f"config/{config['slug']}.toml", now.date()
    listed = {(b["name"], b["url"], tuple(members(b, where, today)))
              for b in config["officials"].get("bodies", []) if b.get("url")}
    return listed != {(b["name"], b["url"], tuple(b["members"])) for b in previous.get("bodies", [])}


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False,
        browser=rendered) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    path = data_dir / RESULT_FILE
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not force and not due(config, previous, now):
        return {"skipped": f"checked {previous['checked_at']}"}
    where, user_agent = f"config/{config['slug']}.toml", config["site"]["user_agent"]
    bodies, pages = [], {}
    for body in config["officials"].get("bodies", []):
        if not body.get("url"):
            continue
        names = members(body, where, now.date())
        result = {"name": body["name"], "url": body["url"], "members": names}
        try:
            # A page shared by two bodies (a council's page with its mayor) is read once.
            if body["url"] not in pages:
                pages[body["url"]] = page_text(client.get(body["url"]).text)
            text = pages[body["url"]]
            result["read"] = "page"
            if not any(named(n, text) for n in names) and (shown := browser(body["url"], user_agent)) is not None:
                text, result["read"] = shown, "browser"
            result["missing"] = [n for n in names if not named(n, text)]
        except FetchError as e:
            result["error"] = str(e)
        bodies.append(result)
    data = {"checked_at": now.isoformat(timespec="seconds"), "bodies": bodies}
    write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


def leads(data: dict) -> list[dict]:
    """The bodies a person should look at, from a check's result: a page that couldn't be read
    (error), names none of the members (none), or doesn't name some of them (missing)."""
    out = []
    for b in data.get("bodies", []):
        lead = {"name": b["name"], "url": b["url"]}
        if b.get("error"):
            out.append({**lead, "error": b["error"]})
        elif b["missing"] and len(b["missing"]) == len(b["members"]):
            out.append({**lead, "none": True})
        elif b["missing"]:
            out.append({**lead, "missing": b["missing"]})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="check even if checked this week")
    args = parser.parse_args()
    config = load_config(args.town)
    if not configured(config, "officials"):
        return 0
    client = PoliteClient(config["site"]["user_agent"], delay=2.0, timeout=60.0)
    result = run(config, client, args.data, force=args.force)
    if "skipped" in result:
        print(f"::notice::Officials not checked: {result['skipped']}.")
        return 0
    for lead in leads(result):
        what = (f"couldn't be read: {lead['error']}" if "error" in lead else "names none of its members"
                if lead.get("none") else f"doesn't name {', '.join(lead['missing'])}")
        print(f"::notice::The {lead['name']}'s page {what} ({lead['url']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
