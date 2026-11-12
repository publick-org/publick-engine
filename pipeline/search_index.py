"""The site's search: a Pagefind index of its pages, with each decision a result of its own.

Pagefind (https://pagefind.app) reads the built pages and writes /pagefind/: an index split into
small files, one set per language (from each page's lang), that the search page
(site/pages/search/, site/static/js/search.js) loads a few at a time. A search downloads only the
parts of the index for its words, so it stays quick however many meetings a town has, and each
town's index is built with its own site, from nothing but that site's pages.

What a page gives the index (site/templates/base.html): its <main>, when the build marks the page
searchable (searchable()), with its kind ("meeting" or "page") as a filter and, for a page, where
it sits in the menu ("crumb", such as "Data › Budget & taxes") as metadata. Lists of meetings and
of decisions are left out (EXCLUDED), so a page that lists many meetings isn't found by their
headlines. Each decision is a record of its own instead (decision_record()), kind "decision",
linking its meeting's decisions, so a search lists what was decided first, and a meeting's page is
found by the rest of its text: its summaries, agenda, and minutes.

Decisions and meetings are listed by date, not by how often they use the words: each has a sort key,
"date" (sort_key()), without which a sorted search leaves it out, and a meeting's page a filter, "when", "upcoming" or "past" as of the build, as the
meetings page has it. The search page lists upcoming meetings first, the soonest first, then past
meetings and decisions, the newest first; pages, which have no date, are listed by how well they match.

A search loads the language's metadata and filters, the WebAssembly that reads them, and the index
chunks for its words. check() fails a build where that, for any one word, passes LOAD_LIMIT
(site_checks run it on every built site), long before a town has enough meetings for a search to
feel slow.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from pagefind.index import IndexConfig, PagefindIndex

# The folder the index is written to, at the site's root; the search page loads /pagefind/pagefind.js.
FOLDER = "pagefind"
# Parts of a page the index leaves out: lists of meetings (their headlines are found on each meeting's page),
# lists of decisions (each decision is a record of its own), forms, and the notes about the page itself (how it
# was made and corrected, which repeat what it says).
EXCLUDED = [".meeting-list", ".later-list", ".decision-rows", ".decision-list", ".all-votes", ".board-list",
            ".tabs", ".breadcrumb", "form", ".signup-after", ".notice", ".about-page", ".about-notes", "#about"]
# The most a search may download before its first results, in bytes: Pagefind's script and its worker, the
# language's WebAssembly, metadata, and filters, and the largest index chunk a word can need. Gloucester's, with
# 283 pages and 390 decisions a language, is about 190 KB, of which the metadata, the part that grows with the
# number of results (with each meeting's and decision's sort key, a few bytes each), is 5 KB.
LOAD_LIMIT = 600_000
# The files Pagefind writes for its own ready-made search interfaces, which the site doesn't use (its search page
# has its own, site/static/js/search.js): deleted after writing.
UNUSED = ("pagefind-ui.js", "pagefind-ui.css", "pagefind-component-ui.js", "pagefind-component-ui.css",
          "pagefind-modular-ui.js", "pagefind-modular-ui.css", "pagefind-highlight.js")


def searchable(url: str, template: str, unlisted: set[str]) -> bool:
    """Whether a page is in the index: not the home page (which only gathers what other pages say), a moved
    page, the search page, a page that isn't listed, or the digest's and the alerts' pages (in English only,
    and about the week's meetings, which are found by their own pages)."""
    return (url != "/" and template != "moved.html" and url not in unlisted
            and not url.startswith(("/search/", "/digest/", "/alerts/")))


def decision_url(page: str, text: str, taken: set[str]) -> str:
    """Where a decision's result leads: its meeting's page (page, with the language's prefix), at the decisions
    (#minutes), and with a text fragment of its first words (:~:text=), which most browsers scroll to and mark;
    one that can't find them, or doesn't read fragments, stays at the decisions or the top. Each decision's
    address is its own (taken: the meeting's so far): the index keeps one result per address."""
    words = str(text).split()
    for n in range(min(6, len(words)), len(words) + 1):
        url = f"{page}#minutes:~:text={quote(' '.join(words[:n]), safe='')}"
        if url not in taken:
            break
    else:
        url = f"{page}#minutes:~:text={quote(' '.join(words), safe='')}&{len(taken)}"
    taken.add(url)
    return url


def sort_key(meeting: dict, position: int | None = None) -> str:
    """What a meeting's page, or one of its decisions, is listed by: the meeting's date and start time, then its
    address, so two meetings at the same time keep their decisions apart; for a decision, then its place in the
    minutes (position, from 0), counted down, so that newest first lists a meeting's decisions in order.
    Pagefind compares the keys as text, so the time is written with two digits for the hour ("09:00")."""
    time = meeting.get("start_time") or "00:00"
    key = f"{meeting['date']}T{time.zfill(5)} {meeting['url']}"
    return key if position is None else f"{key} {9999 - position:04d}"


def decision_record(text: str, url: str, board: str, when: str, lang: str, sort: str) -> dict:
    """A decision as a record of the index: its text, where it leads (decision_url()), the board and the
    meeting's date (as said in the language) to show with it, and its sort key (sort_key())."""
    return {"url": url, "content": str(text), "language": lang,
            "meta": {"title": board, "date": when}, "filters": {"kind": ["decision"]}, "sort": {"date": sort}}


async def _write(site_dir: Path, records: list[dict]) -> None:
    config = IndexConfig(exclude_selectors=EXCLUDED, output_path=str(site_dir / FOLDER))
    async with PagefindIndex(config=config) as index:
        await index.add_directory(path=str(site_dir))
        for record in records:
            await index.add_custom_record(**record)


def write(site_dir: Path, records: list[dict]) -> None:
    """Index the built site's pages, and the records, into site_dir/pagefind/. Pagefind's indexer runs in an event
    loop of its own, on a thread of its own where the caller already has one running (a browser test)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_write(site_dir, records))
    else:
        with ThreadPoolExecutor(1) as pool:
            pool.submit(asyncio.run, _write(site_dir, records)).result()
    for name in UNUSED:
        (site_dir / FOLDER / name).unlink(missing_ok=True)


def results(site_dir: Path, lang: str) -> list[dict]:
    """Every result in a language's index, as Pagefind keeps it (its url, content, meta, and filters): for the tests
    and for reading what a site's search holds. Each is a fragment file, gzipped, after a signature."""
    found = []
    for path in sorted((site_dir / FOLDER / "fragment").glob(f"{lang}_*.pf_fragment")):
        data = gzip.decompress(path.read_bytes())
        found.append(json.loads(data[data.index(b"{"):]))
    return found


def load_sizes(site_dir: Path) -> dict[str, int]:
    """For each language, the most a search downloads before its first results, in bytes (LOAD_LIMIT)."""
    folder = site_dir / FOLDER
    scripts = sum((folder / name).stat().st_size for name in ("pagefind.js", "pagefind-worker.js"))
    sizes = {}
    for meta in folder.glob("pagefind.*.pf_meta"):
        lang = meta.name.split(".")[1].split("_")[0]
        filters = sum(p.stat().st_size for p in (folder / "filter").glob(f"{lang}_*.pf_filter"))
        chunk = max((p.stat().st_size for p in (folder / "index").glob(f"{lang}_*.pf_index")), default=0)
        sizes[lang] = scripts + (folder / f"wasm.{lang}.pagefind").stat().st_size + meta.stat().st_size + filters + chunk
    return sizes


def check(site_dir: Path) -> list[str]:
    """What's wrong with the site's index: missing, or a language whose search loads more than LOAD_LIMIT."""
    if not (site_dir / FOLDER / "pagefind.js").exists():
        return [f"The search index (/{FOLDER}/) wasn't written."]
    return [f"A search in {lang} loads {size:,} bytes before its first results, over {LOAD_LIMIT:,}."
            for lang, size in sorted(load_sizes(site_dir).items()) if size > LOAD_LIMIT]
