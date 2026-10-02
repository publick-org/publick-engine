"""The sites' wording in languages other than English.

The page templates and the phrases built in Python are written in English, as
they read, with each piece of wording marked for translation: `{{ _("...") }}`
or `{% trans %}...{% endtrans %}` in a template, `_("...")` or
`ngettext(...)` in Python. Each other language has one string file,
site/strings/<language>.po, giving each English string's translation. A string
with no translation yet (or one marked fuzzy, to be checked) is shown in
English.

English pages are built from the English text itself, so marking a string
never changes them.

The wording is written about a city ("the city's Agenda Center", "la ciudad").
For a town ([town] kind, or the Census Bureau's word for the place, in
data/place.json; pipeline/fetch_place.py), every string is worded for a town
as it's shown: "the town's Agenda Center", "el pueblo", "del pueblo"
(TOWN_WORDING). Only the site's own wording changes, never a name or a link
in it, nor "cities and towns", which is about other places too.

Usage:
    python -m pipeline.i18n update    # add new strings to each string file, drop removed ones
    python -m pipeline.i18n check     # fails when a string file is out of date
    python -m pipeline.i18n missing   # lists the strings each language has no translation for
"""

from __future__ import annotations

import argparse
import functools
import io
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.pofile import read_po, write_po

from pipeline.config import ENGINE_DIR

STRINGS_DIR = ENGINE_DIR / "site" / "strings"
# Every language a site can be built in, by its code (the page's lang attribute) and its own name.
LANGUAGES = {"en": "English", "es": "Español"}

_language: ContextVar[str] = ContextVar("language", default="en")
_kind: ContextVar[str] = ContextVar("kind", default="city")
KINDS = ("city", "town")


def language() -> str:
    """The language being built."""
    return _language.get()


def kind() -> str:
    """Whether the site being built is a city's or a town's."""
    return _kind.get()


@contextmanager
def use(lang: str, kind: str = "city"):
    """Build in this language, for a city or a town, until the block ends."""
    if lang not in LANGUAGES:
        raise ValueError(f"Unknown language {lang!r}: the engine has {', '.join(LANGUAGES)}.")
    if kind not in KINDS:
        raise ValueError(f"Unknown kind {kind!r}: a place is a {' or a '.join(KINDS)}.")
    token, kind_token = _language.set(lang), _kind.set(kind)
    try:
        yield
    finally:
        _language.reset(token)
        _kind.reset(kind_token)


# The wording for a town, in place of a city's, in each language, in the order applied. Plurals
# ("cities and towns", "ciudades") are about places in general, and stay. Spanish says "el pueblo",
# so the article and its contractions change with it ("de la ciudad" -> "del pueblo").
TOWN_WORDING = {
    "en": [(r"\bCitywide\b", "Townwide"), (r"\bcitywide\b", "townwide"), (r"\bCity\b", "Town"), (r"\bcity\b", "town")],
    "es": [(r"\bde la Ciudad\b", "del Pueblo"), (r"\bde la ciudad\b", "del pueblo"),
           (r"\ba la Ciudad\b", "al Pueblo"), (r"\ba la ciudad\b", "al pueblo"),
           (r"\bToda la ciudad\b", "Todo el pueblo"), (r"\btoda la ciudad\b", "todo el pueblo"),
           (r"\bLa Ciudad\b", "El Pueblo"), (r"\bLa ciudad\b", "El pueblo"),
           (r"\bla Ciudad\b", "el Pueblo"), (r"\bla ciudad\b", "el pueblo"),
           (r"\buna ciudad\b", "un pueblo"), (r"\bCiudad\b", "Pueblo"), (r"\bciudad\b", "pueblo")],
}
# What a string's wording doesn't include: its HTML, and the values put in it.
NOT_WORDING = re.compile(r"(<[^>]*>|%\([^)]*\)[sd]|%%|\{[^}]*\})")


@functools.cache
def for_town(text: str, lang: str) -> str:
    """A string written about a city, worded for a town."""
    parts = NOT_WORDING.split(text)
    for i in range(0, len(parts), 2):
        for pattern, replacement in TOWN_WORDING[lang]:
            parts[i] = re.sub(pattern, replacement, parts[i])
    return "".join(parts)


def worded(text: str) -> str:
    """A string as the site being built says it: for a town, if it's a town's."""
    return for_town(text, language()) if _kind.get() == "town" else text


@functools.cache
def strings(lang: str) -> dict:
    """A language's translations: {(context, English): translation}, where a
    plural's English is (singular, plural) and its translation a tuple of forms.
    Strings not translated yet, or marked fuzzy, are left out."""
    path = STRINGS_DIR / f"{lang}.po"
    if lang == "en" or not path.exists():
        return {}
    with path.open("rb") as f:
        catalog = read_po(f, locale=lang)
    return {(m.context, m.id): m.string for m in catalog
            if m.id and not m.fuzzy and (all(m.string) if isinstance(m.string, tuple) else m.string)}


def pgettext(context: str | None, message: str) -> str:
    return worded(strings(language()).get((context, message), message))


def label(message: str) -> str:
    """A label from data saved in English (a budget function), translated, and never reworded."""
    return strings(language()).get((None, message), message)


def gettext(message: str) -> str:
    return pgettext(None, message)


def npgettext(context: str | None, singular: str, plural: str, n: int) -> str:
    forms = strings(language()).get((context, (singular, plural)))
    if forms:
        return worded(forms[0 if n == 1 else 1])
    return worded(singular if n == 1 else plural)


def translated(message: str, context: str | None = None) -> bool:
    """Whether the language being built has a translation of this string (which may read the same)."""
    return language() == "en" or (context, message) in strings(language())


def ngettext(singular: str, plural: str, n: int) -> str:
    return npgettext(None, singular, plural, n)


_ = gettext


def N_(message: str) -> str:
    """Marks a string for translation where it's defined (a module's constant),
    to be translated with _() where it's used."""
    return message


# ---- Dates ---------------------------------------------------------------------

def month_name(month: int, short: bool = False) -> str:
    """1 -> 'January' ('Jan'), in the language being built."""
    if short:
        return (pgettext("month, short", "Jan"), pgettext("month, short", "Feb"), pgettext("month, short", "Mar"),
                pgettext("month, short", "Apr"), pgettext("month, short", "May"), pgettext("month, short", "Jun"),
                pgettext("month, short", "Jul"), pgettext("month, short", "Aug"), pgettext("month, short", "Sep"),
                pgettext("month, short", "Oct"), pgettext("month, short", "Nov"), pgettext("month, short", "Dec"))[month - 1]
    return (pgettext("month", "January"), pgettext("month", "February"), pgettext("month", "March"),
            pgettext("month", "April"), pgettext("month", "May"), pgettext("month", "June"),
            pgettext("month", "July"), pgettext("month", "August"), pgettext("month", "September"),
            pgettext("month", "October"), pgettext("month", "November"), pgettext("month", "December"))[month - 1]


def weekday_name(d: date, short: bool = False) -> str:
    """The day of the week ('Thursday', or 'Thu'), in the language being built."""
    if short:
        return (pgettext("weekday, short", "Mon"), pgettext("weekday, short", "Tue"), pgettext("weekday, short", "Wed"),
                pgettext("weekday, short", "Thu"), pgettext("weekday, short", "Fri"), pgettext("weekday, short", "Sat"),
                pgettext("weekday, short", "Sun"))[d.weekday()]
    return (pgettext("weekday", "Monday"), pgettext("weekday", "Tuesday"), pgettext("weekday", "Wednesday"),
            pgettext("weekday", "Thursday"), pgettext("weekday", "Friday"), pgettext("weekday", "Saturday"),
            pgettext("weekday", "Sunday"))[d.weekday()]


def plain_date(d: date) -> str:
    """'October 1, 2026'."""
    # Translators: a date without the day of the week, such as "October 1, 2026".
    return _("{month} {day}, {year}").format(month=month_name(d.month), day=d.day, year=d.year)


def month_year(d: date, short: bool = False) -> str:
    """'October 2026' ('Oct 2026')."""
    # Translators: a month, such as "October 2026" (or "Oct 2026").
    return _("{month} {year}").format(month=month_name(d.month, short), year=d.year)


# ---- The string files --------------------------------------------------------

# The template environment's settings, which decide each {% trans %} block's text.
JINJA_OPTIONS = {"extensions": "jinja2.ext.i18n", "newstyle_gettext": "true", "trimmed": "true",
                 "trim_blocks": "true", "lstrip_blocks": "true"}
METHODS = [("pipeline/**.py", "python"), ("site/**.html", "jinja2.ext:babel_extract")]


def extract() -> Catalog:
    """Every marked string in the engine's code and templates."""
    template = Catalog(fuzzy=False)
    found = extract_from_dir(ENGINE_DIR, METHODS, {"site/**.html": JINJA_OPTIONS}, comment_tags=("Translators:",),
                             strip_comment_tags=True)
    # In file and line order, not the order the filesystem lists them, so every machine writes the same file.
    for filename, lineno, message, comments, context in sorted(found, key=lambda m: (m[0], m[1])):
        template.add(message, None, [(filename, None)], auto_comments=comments, context=context)
    return template


def updated(lang: str, template: Catalog) -> Catalog:
    """A language's string file with the current strings: new ones added
    untranslated, removed ones dropped, translations kept."""
    path = STRINGS_DIR / f"{lang}.po"
    if path.exists():
        with path.open("rb") as f:
            catalog = read_po(f, locale=lang)
    else:
        catalog = Catalog(locale=lang, project="Publick", fuzzy=False, header_comment=(
            f"# The Publick sites' wording in one language ({lang}): each English string and its translation.\n"
            "# Updated with python -m pipeline.i18n update (see pipeline/i18n.py)."))
    catalog.update(template, no_fuzzy_matching=True, update_creation_date=False)
    catalog.obsolete.clear()
    return catalog


def as_text(catalog: Catalog) -> bytes:
    out = io.BytesIO()
    # File names without line numbers, so editing a template doesn't rewrite the whole file.
    write_po(out, catalog, width=100, include_lineno=False, sort_output=True, omit_header=False)
    return out.getvalue()


def other_languages() -> list[str]:
    return [lang for lang in LANGUAGES if lang != "en"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["update", "check", "missing"])
    args = parser.parse_args()
    template = extract()
    stale = []
    for lang in other_languages():
        path = STRINGS_DIR / f"{lang}.po"
        text = as_text(updated(lang, template))
        current = path.read_bytes() if path.exists() else b""
        if args.command == "update" and text != current:
            STRINGS_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text)
            print(f"Updated {path.relative_to(ENGINE_DIR)}")
        elif args.command == "check" and text != current:
            stale.append(lang)
        elif args.command == "missing":
            done = strings(lang)
            todo = [m for m in updated(lang, template) if m.id and (m.context, m.id) not in done]
            for m in todo:
                print(f"{lang}: {m.id if isinstance(m.id, str) else m.id[0]!r}")
            print(f"{lang}: {len(todo)} of {len(template)} strings not translated")
    if stale:
        sys.exit(f"Out of date: {', '.join(f'site/strings/{lang}.po' for lang in stale)}. "
                 "Run python -m pipeline.i18n update.")


if __name__ == "__main__":
    main()
