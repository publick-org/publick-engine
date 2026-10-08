"""Every date a meeting can have fits its column, in every language.

A meeting's day and time sit in a fixed column beside its name: on the home
page the day above the time ("MON, OCT 12"), on a board's page and the
digest's list of issues the date alone ("Oct 12"). The site checks catch a
date wider than its column, but only once a town has a meeting on it: on
2026-10-06 Lewiston's first meeting on a two-digit day held that morning's
engine update (#83). Here every weekday, month, and day is laid out in a
real browser with the site's own stylesheet and fonts, in English and each
other language, so a release never carries one."""

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import serve
from jinja2 import Environment, FileSystemLoader

from pipeline import build_site, i18n

playwright_api = pytest.importorskip("playwright.sync_api")

# The calendar's 28-year cycle: every month and day, Feb 29 too, falls on every weekday, so every
# date as it's shown, "Wed, Sep 30" to "Thu, Feb 29", is here.
FIRST = date(2024, 1, 1)
DAYS = (date(2052, 1, 1) - FIRST).days
# The column's width is fixed in rem from 22rem up; narrower, a meeting's row is one column.
VIEWPORTS = {"desktop": {"width": 1280, "height": 900}, "narrowest with a column": {"width": 360, "height": 640}}

PAGE = """{% from "macros.html" import meeting_item %}<!doctype html>
<html lang="{{ lang }}"><head><meta charset="utf-8"><link rel="stylesheet" href="/static/css/site.css"></head>
<body><main>
<ul class="meeting-list" id="home">{% for m in by_day %}{{ meeting_item(m, show_day=true) }}{% endfor %}</ul>
<ul class="meeting-list compact" id="home-more">{% for m in by_day %}{{ meeting_item(m, preview=false, show_day=true) }}{% endfor %}</ul>
<ul class="meeting-list" id="board">{% for m in by_date %}{{ meeting_item(m, show_date=true, show_body=false) }}{% endfor %}</ul>
<ul class="meeting-list" id="digest">{% for m in by_date %}<li class="meeting-item"><span class="meeting-when">
  <time datetime="{{ m.date }}">{{ m.date | date("mon_day") }}</time></span><div class="meeting-main">Week</div></li>{% endfor %}</ul>
</main></body></html>"""

# The rows whose date is wider than its column, by list.
CROWDED = """() => [...document.querySelectorAll('.meeting-when')]
    .filter(e => e.scrollWidth > e.clientWidth + 1)
    .map(e => e.closest('ul').id + ': ' + e.innerText.trim().replace(/\\s*\\n\\s*/g, ' / '))"""


def meeting(d: date) -> dict:
    # Noon is the widest time ("12:00 PM", "12:00 p. m."), the line under the day.
    return {"date": d.isoformat(), "start_time": "12:00", "url": "/meetings/x/", "body": "City Council",
            "title": "City Council", "preview_line": "", "minutes_doc": None, "agenda": None, "status": "scheduled",
            "remote_url": None, "special": False, "listed": True}


def dates(key) -> list[date]:
    """The first date for each distinct way a date is shown (key)."""
    seen = {}
    for n in range(DAYS):
        d = FIRST + timedelta(days=n)
        seen.setdefault(key(d), d)
    return list(seen.values())


def page(lang: str) -> str:
    env = Environment(loader=FileSystemLoader(build_site.SITE_DIR / "templates"), autoescape=True,
                      trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.i18n"])
    env.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True,
                                  pgettext=i18n.pgettext, npgettext=i18n.npgettext)
    env.filters.update(date=build_site.format_date, time=build_site.format_time)
    # A global, as the build has it: the macros are imported without the page's context.
    env.globals["today"] = FIRST.isoformat()
    with i18n.use(lang):
        return env.from_string(PAGE).render(
            lang=lang,
            by_day=[meeting(d) for d in dates(lambda d: (d.weekday(), d.month, d.day))],
            by_date=[meeting(d) for d in dates(lambda d: (d.month, d.day))])


@pytest.fixture(scope="module")
def site():
    folder = Path(tempfile.mkdtemp())
    shutil.copytree(build_site.STATIC_DIR, folder / "static")
    for lang in i18n.LANGUAGES:
        (folder / lang).mkdir()
        (folder / lang / "index.html").write_text(page(lang), encoding="utf-8")
    with serve(folder) as url:
        yield url
    shutil.rmtree(folder)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_every_weekday_month_and_day_is_shown():
    assert len(dates(lambda d: (d.weekday(), d.month, d.day))) == 7 * 366
    assert len(dates(lambda d: (d.month, d.day))) == 366


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("lang", i18n.LANGUAGES)
def test_every_date_fits_its_column(browser, site, lang, viewport):
    context = browser.new_context(viewport=VIEWPORTS[viewport])
    tab = context.new_page()
    tab.goto(f"{site}/{lang}/")
    # Measured in the site's own fonts, not the fallback shown while they load.
    assert tab.evaluate("document.fonts.ready.then(() => [...document.fonts].some(f => f.status === 'loaded'))")
    rows = tab.evaluate("document.querySelectorAll('.meeting-when').length")
    crowded = tab.evaluate(CROWDED)
    context.close()
    assert rows == 2 * (7 * 366) + 2 * 366
    assert not crowded, f"{len(crowded)} dates wider than their column ({lang}, {viewport}), such as {crowded[:5]}"
