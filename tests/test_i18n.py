"""The sites' wording in other languages (pipeline/i18n.py): the string files are
current, each translation keeps its English's placeholders, and a translation is
used when a page is built in its language."""

import re
from datetime import date

import pytest

from pipeline import build_site, i18n

PLACEHOLDER = re.compile(r"%\((\w+)\)s|\{(\w*)[^{}]*\}")


def placeholders(text: str) -> list[str]:
    return sorted(a or b for a, b in PLACEHOLDER.findall(text))


def test_string_files_are_current():
    """Every marked string is in each language's file, and nothing else: run
    python -m pipeline.i18n update after changing the site's wording."""
    template = i18n.extract()
    for lang in i18n.other_languages():
        path = i18n.STRINGS_DIR / f"{lang}.po"
        assert path.read_bytes() == i18n.as_text(i18n.updated(lang, template)), (
            f"site/strings/{lang}.po is out of date: run python -m pipeline.i18n update")


def test_no_wording_marked_inside_an_f_string():
    """Python 3.12 finds a _() inside an f-string and 3.11 doesn't, so the string files would
    differ by Python version: wording to translate goes in a variable first."""
    inside = re.compile(r"""\bf(["'])[^\n]*?\{[^}\n]*\b(?:_|ngettext|pgettext|npgettext)\(""")
    found = [f"{path.relative_to(i18n.ENGINE_DIR)}:{n}" for path in sorted((i18n.ENGINE_DIR / "pipeline").rglob("*.py"))
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if inside.search(line)]
    assert not found, found


@pytest.mark.parametrize("lang", i18n.other_languages())
def test_translations_keep_placeholders(lang):
    """A translation names the same values as its English, so none is lost or left unfilled."""
    for (context, english), translated in i18n.strings(lang).items():
        if isinstance(english, tuple):
            for form in translated:
                assert placeholders(form) == placeholders(english[1]), (english, form)
        else:
            assert placeholders(translated) == placeholders(english), (english, translated)


@pytest.fixture
def spanish(monkeypatch):
    """A few Spanish strings, as if from site/strings/es.po."""
    strings = {
        ("month", "October"): "octubre", ("weekday", "Thursday"): "jueves",
        (None, "{weekday}, {month} {day}, {year}"): "{weekday}, {day} de {month} de {year}",
        (None, ("{n} hour", "{n} hours")): ("{n} hora", "{n} horas"),
        (None, ("%(count)s hour", "%(count)s hours")): ("%(count)s hora", "%(count)s horas"),
        (None, "City Hall updates"): "Novedades del Ayuntamiento",
    }
    monkeypatch.setattr(i18n, "strings", lambda lang: strings if lang == "es" else {})


def test_python_phrases_in_spanish(spanish):
    with i18n.use("es"):
        assert build_site.format_date("2026-10-01") == "jueves, 1 de octubre de 2026"
        assert build_site.format_duration(1 / 24) == "1 hora"
        assert build_site.format_duration(5 / 24) == "5 horas"
        # Not translated yet: the English.
        assert build_site.format_duration(None) == "Not enough data"
    assert build_site.format_date(date(2026, 10, 1)) == "Thursday, October 1, 2026"


def test_templates_in_spanish(spanish):
    env = build_site.Environment(extensions=["jinja2.ext.i18n"], autoescape=True)
    env.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True)
    page = env.from_string('<p>{{ _("City Hall updates") }}</p><p>{% trans count=n %}{{ count }} hour'
                           '{% pluralize %}{{ count }} hours{% endtrans %}</p>')
    with i18n.use("es"):
        assert page.render(n=1) == "<p>Novedades del Ayuntamiento</p><p>1 hora</p>"
    assert page.render(n=2) == "<p>City Hall updates</p><p>2 hours</p>"


def test_unknown_language():
    with pytest.raises(ValueError):
        with i18n.use("fr"):
            pass
