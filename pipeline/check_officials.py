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
middle initial, and both still match; of a name of three words, either of the
last two, since a page often gives one of two surnames. Images' alt text counts,
since a page of members' photos often names them only there. A page that names
none of the members as first fetched is read again in a browser, if one is
installed, with its collapsed sections opened: many school districts' pages
fill in their members with JavaScript, some only once a section is opened.

It runs when the last check is CHECK_DAYS old, or when a body's members or page
differ from those last checked (a person fixed the list: the lead goes away
with the next run); --force checks anyway.

A page that can't be read (a timeout, a server that refuses the runners for a
while) is tried again by the next run, at least RETRY_HOURS later, rather than a
week later, and until then its body keeps what its last read found. It's a lead
for a person only once it has failed for UNREAD_DAYS running: a city's site that
is down for a morning isn't something to look at, and the network's issue stays
the length of what's really wrong.

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
# A page that couldn't be read is tried again by the first run this many hours later...
RETRY_HOURS = 20
# ...and is a lead once it has failed this many days running.
UNREAD_DAYS = 7
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
    """A page's words, as plain() writes them, without its scripts and styles, and with its images' alt
    text: a page of members' photos often names them only there ("Chair Dorian Rave")."""
    page = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1\s*>", " ", page)
    page = re.sub(r"""(?is)<img\b[^>]*?\balt\s*=\s*("([^"]*)"|'([^']*)')[^>]*>""",
                  lambda m: f" {m.group(2) or m.group(3) or ''} ", page)
    return plain(html.unescape(re.sub(r"<[^>]+>", " ", page)))


# Words that begin a surname of two ("De Jesus", "Van Gorder", "St. Hilaire"), not a name of their own.
PARTICLES = {"de", "del", "della", "da", "di", "du", "la", "le", "van", "von", "st", "ste", "mac", "bin"}


def name_words(name: str) -> list[str]:
    """The words of a member's name, without a nickname in quotes or parentheses, initials, or suffixes."""
    words = re.findall(r"[a-z][a-z'\-]*", re.sub(r'"[^"]*"|\([^)]*\)', " ", plain(name)))
    return [w.strip("'-") for w in words if len(w.strip("'-")) > 1 and w.strip("'-") not in SUFFIXES]


def surname(name: str) -> str:
    """'Jacqueline "Nikki" O'Toole' -> "o'toole"; 'David P. "D.J." Beauregard, Jr.' -> "beauregard"."""
    words = name_words(name)
    return words[-1] if words else plain(name)


def surnames(name: str) -> list[str]:
    """The words a page may name the member by: the surname, and, for a name of three words or more, the
    one before it, since a page often gives one of two surnames ("Lizandra Paneto" for Lizsandra Paneto
    Gullon, "Amanda Sexton" for Amanda Grady Sexton). A middle name written out counts too, so a member
    whose middle name is a word elsewhere on the page reads as named."""
    words = name_words(name)
    if len(words) >= 3 and words[-2] not in PARTICLES:
        return [words[-1], words[-2]]
    return [surname(name)]


def named(name: str, text: str) -> bool:
    """Whether the page's text (page_text()) names the member by one of their surnames(), as a word of its
    own ("Dexter" isn't named by "Dexter-Smith" or "O'Dexter"; "Dexter's" names Dexter)."""
    return any(re.search(rf"(?<![a-z'\-]){re.escape(word)}(?![a-z\-])", text) for word in surnames(name))


def members(body: dict, where: str, today: date) -> list[str]:
    """The names of the body's members today: its incoming members once they've taken office."""
    coming = officials.incoming(body, where)
    sitting = coming["members"] if coming and today >= coming["takes_office"] else body.get("members", [])
    return [m["name"] for m in sitting]


# The page's own collapsed sections (an accordion of members, as Apptegy's sites have), as accessible markup
# marks them, opened before it's read: some load what's in them only then. Links that go elsewhere are left.
COLLAPSED = ':is(button, summary, [role="button"], a[href="#"], a:not([href]))[aria-expanded="false"]'
MOST_OPENED = 30
# The page's text as a reader sees it, with its images' alt text (page_text()).
READ = """() => document.body.innerText + " " + [...document.images].map(i => i.alt || "").join(" ")"""


def rendered(url: str, user_agent: str) -> str | None:
    """The page's text once its scripts have run and its collapsed sections are open, from a browser; None
    if no browser is installed."""
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
                collapsed = page.locator(COLLAPSED)
                for i in range(min(collapsed.count(), MOST_OPENED)):
                    try:
                        collapsed.nth(i).click(timeout=3_000)
                    except Error:
                        continue    # hidden, or covered: what's in it stays unread
                page.wait_for_load_state("networkidle", timeout=BROWSER_TIMEOUT_MS)
                return plain(page.evaluate(READ))
            finally:
                browser.close()
    except Error as e:
        raise FetchError(f"{url}: the browser couldn't load it: {str(e).splitlines()[0]}") from e


def due(config: dict, previous: dict, now: datetime) -> bool:
    """Whether to check: the last check is CHECK_DAYS old, or a body's members or page changed since."""
    if not previous.get("checked_at"):
        return True
    since = now - datetime.fromisoformat(previous["checked_at"])
    if since >= timedelta(days=CHECK_DAYS):
        return True
    if since >= timedelta(hours=RETRY_HOURS) and any(b.get("error") for b in previous.get("bodies", [])):
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
    stamp = now.isoformat(timespec="seconds")
    last = {(b["name"], b["url"]): b for b in previous.get("bodies", [])}
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
            result["read_at"] = stamp
        except FetchError as e:
            result["error"] = str(e)
            before = last.get((body["name"], body["url"]), {})
            result["failing_since"] = (before.get("failing_since") or previous["checked_at"]) if before.get("error") else stamp
            # What the page's last read found stands until it's read again, if the members are the same.
            if "missing" in before and before["members"] == names:
                result.update({k: before[k] for k in ("read", "missing", "read_at") if k in before})
        bodies.append(result)
    data = {"checked_at": stamp, "bodies": bodies}
    write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


def unread_too_long(body: dict, checked_at: str) -> bool:
    """Whether the body's page has failed for UNREAD_DAYS running, as of the check at checked_at."""
    since = body.get("failing_since") or checked_at
    return datetime.fromisoformat(checked_at) - datetime.fromisoformat(since) >= timedelta(days=UNREAD_DAYS)


def leads(data: dict) -> list[dict]:
    """The bodies a person should look at, from a check's result: a page that couldn't be read for
    UNREAD_DAYS (error, since), names none of the members (none), or doesn't name some of them
    (missing). A page that couldn't be read for less time leads by what its last read found."""
    out = []
    for b in data.get("bodies", []):
        lead = {"name": b["name"], "url": b["url"]}
        if b.get("error") and unread_too_long(b, data["checked_at"]):
            out.append({**lead, "error": b["error"], "since": b.get("failing_since") or data["checked_at"]})
        elif not b.get("missing"):
            continue
        elif len(b["missing"]) == len(b["members"]):
            out.append({**lead, "none": True})
        else:
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
    for body in result["bodies"]:
        if body.get("error") and not unread_too_long(body, result["checked_at"]):
            print(f"::notice::The {body['name']}'s page couldn't be read, since {body['failing_since'][:10]}; "
                  f"the next run tries again ({body['error']}).")
    for lead in leads(result):
        what = (f"couldn't be read: {lead['error']}" if "error" in lead else "names none of its members"
                if lead.get("none") else f"doesn't name {', '.join(lead['missing'])}")
        print(f"::notice::The {lead['name']}'s page {what} ({lead['url']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
