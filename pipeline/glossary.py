"""The glossary: the civic terms a town's pages and its translator use, each in every language.

A town's glossary is its own [[glossary]] entries, in their order, then every term its config's
[terms] gives as a table of languages (pipeline/config.py): the network's and its state's, which a
new town inherits without listing them. A [[glossary]] entry with its own definition wins over a
term of the same name ("warrant" is a list of payments to a retirement board). Terms given only as
an English definition are there for [[glossary]] entries to take, as before.

Each entry (entries()) has its English term and definition, the boards it's limited to (bodies, if
any), and its other languages' wording, for:

- the meeting pages' "Terms used here" (pipeline/build_site.py glossary_for), and the first use
  of each term on a page linked to its definition (Linker);
- the glossary page of each site, /meetings/glossary/, in each language;
- the translator (pipeline/translate.py), which is given each term's wording in the language it
  writes and checks without AI that it wrote it.

A term is found in English by its own words, the other ways it's written (also), or a pattern of
its own: an acronym (ZBA, M.G.L.) as written, any other term in any case and in the plural ("special
permits"). In another language, by its wording there without the English after it in parentheses
("junta de gobierno" for "Junta de Gobierno (Select Board)"), in any case, without minding accents,
each word in the singular or the plural ("permisos especiales"); or by that language's own also or
pattern.
"""

from __future__ import annotations

import html
import re
import unicodedata

from markupsafe import Markup, escape

# Where the glossary's page is, in each language's folder.
PAGE = "/meetings/glossary/"


def slug(term: str) -> str:
    """A term's anchor on the glossary page, the same in every language: "select-board"."""
    return re.sub(r"[^a-z0-9]+", "-", plain(term).lower().replace("&", "and")).strip("-")


def plain(text: str) -> str:
    """Text without its accents: "votación" is "votacion"."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def is_acronym(term: str) -> bool:
    """An acronym or an abbreviation (ZBA, M.G.L.), found only as written."""
    return term.isupper() or "." in term


def entries(config: dict) -> list[dict]:
    """The town's glossary: its own [[glossary]] entries, then [terms] given by language, alphabetically,
    each {"term", "slug", "definition", "bodies", "languages"}. languages: each language's column
    ({"es": {"term", "definition", ...}}, English's too), empty for an entry with only its English."""
    out, seen = [], set()
    for e in config.get("glossary", []):
        # An entry that took its definition from a term given by language has the term's languages too
        # (pipeline/config.py define_glossary); one with its own definition has only its English.
        out.append({"term": e["term"], "slug": slug(e["term"]), "definition": e["definition"],
                    "bodies": e.get("bodies", []), "languages": e.get("languages") or {}})
        seen.add(e["term"].lower())
    for term, value in sorted(config.get("terms", {}).items(), key=lambda t: t[0].lower()):
        if isinstance(value, dict) and term.lower() not in seen:
            out.append({"term": term, "slug": slug(term), "definition": value["en"]["definition"], "bodies": [],
                        "languages": value})
            seen.add(term.lower())
    return out


def english(entry: dict) -> str:
    """The term in English: an entry shown in another language (shown()) keeps it as term_en."""
    return entry.get("term_en", entry["term"])


def wording(entry: dict, lang: str) -> str | None:
    """The term as a language writes it ("Junta de Gobierno (Select Board)"); English's is the term itself.
    None for a language the entry has no wording in."""
    if lang == "en":
        return english(entry)
    return entry.get("languages", {}).get(lang, {}).get("term")


def core(text: str) -> str:
    """A term's wording without the English after it in parentheses: "junta de gobierno"."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()


def _english_pattern(entry: dict) -> re.Pattern:
    column = entry.get("languages", {}).get("en", {})
    if column.get("pattern"):
        return re.compile(column["pattern"], re.I)
    words = [english(entry), *column.get("also", [])]
    acronyms = [w for w in words if is_acronym(w)]
    others = [w for w in words if not is_acronym(w)]
    parts = []
    if acronyms:
        parts.append(rf"(?-i:{'|'.join(re.escape(w) for w in acronyms)})")
    if others:
        # The plural too: "variances", "special permits", "abatements".
        parts.append(rf"(?:{'|'.join(re.escape(w) for w in sorted(others, key=len, reverse=True))})(?:s|es)?")
    return re.compile(rf"(?<![\w.])(?:{'|'.join(parts)})(?![\w])", re.I)


def _plural_words(text: str) -> str:
    """A phrase's words, each in the singular or the plural (Spanish adds -s or -es), as a pattern over
    accentless text; the spaces between them any space."""
    words = plain(text).split()
    return r"\s+".join(rf"{re.escape(w)}(?:e?s)?" for w in words)


def _other_pattern(entry: dict, lang: str) -> re.Pattern | None:
    column = entry.get("languages", {}).get(lang)
    if not column:
        return None
    if column.get("pattern"):
        return re.compile(column["pattern"], re.I)
    forms = [core(column["term"]), *column.get("also", [])]
    # An acronym kept as written counts as the term ("ZBA" in a Spanish sentence names the board too).
    if is_acronym(english(entry)):
        forms.append(english(entry))
    alternatives = "|".join(_plural_words(f) for f in sorted(forms, key=len, reverse=True) if f)
    return re.compile(rf"(?<![\w.])(?:{alternatives})(?!\w)", re.I)


def pattern(entry: dict, lang: str = "en") -> re.Pattern | None:
    """How the term is found in a text in the language (see the module's docstring). A pattern for
    another language than English is to be matched against plain() text. None: the entry has no
    wording in the language."""
    return _english_pattern(entry) if lang == "en" else _other_pattern(entry, lang)


def found(entry: dict, text: str, lang: str = "en") -> re.Match | None:
    """Where the term is first used in a text in the language, if it is."""
    p = pattern(entry, lang)
    if p is None:
        return None
    return p.search(text if lang == "en" else plain(text))


def shown(config: dict, lang: str, tr=None) -> list[dict]:
    """The town's glossary as a language's pages show it: each entry's term and definition in the language
    ("term", "definition"; the English in "term_en"), and whether they're the language's own wording
    ("translated"). A term with no wording in the language keeps its English; a definition with none is
    tr's (the town's [strings], the engine's, or a draft: pipeline/build_site.py TownStrings), else English."""
    out = []
    for e in entries(config):
        column = e["languages"].get(lang, {}) if lang != "en" else {}
        definition = column.get("definition") or (tr(e["definition"]) if tr and lang != "en" else e["definition"])
        out.append({**e, "term_en": e["term"], "term": column.get("term") or e["term"], "definition": definition,
                    "lang": lang, "translated": lang == "en" or bool(column),
                    # A definition shown in English on another language's page, for its lang attribute.
                    "definition_english": lang != "en" and definition == e["definition"]})
    return out


def sorted_for_page(shown_entries: list[dict]) -> list[dict]:
    """A glossary page's order: alphabetical in its language, without minding case or accents."""
    return sorted(shown_entries, key=lambda e: (plain(e["term"]).lower(), e["slug"]))


# Linking a term's first use on a page to its definition.

# A tag, or the text between tags, of HTML already escaped.
_TAGS = re.compile(r"(<[^>]+>)")


class Linker:
    """Links the first use of each term on a page to its definition on the glossary page, the first time
    the page uses it and only then. One for each page: it remembers which terms the page has linked.
    entries: the page's glossary entries (shown()); lang: the language of the text it's given."""

    def __init__(self, entries: list[dict], lang: str = "en"):
        self.entries = entries
        self.lang = lang
        self.linked: set[str] = set()

    def __call__(self, text, english: bool = False) -> Markup:
        """The text, as HTML, with the first use of each term not yet linked on the page made a link.
        english: the text is in English on another language's page (a summary not yet translated)."""
        if text is None:
            return Markup("")
        source = text if isinstance(text, Markup) else escape(text)
        lang = "en" if english else self.lang
        parts = _TAGS.split(str(source))
        inside_link = 0
        for i, part in enumerate(parts):
            if part.startswith("<"):
                inside_link += 1 if re.match(r"<a[\s>]", part, re.I) else -1 if re.match(r"</a\s*>", part, re.I) else 0
                continue
            if inside_link or not part:
                continue
            parts[i] = self._link(part, lang)
        return Markup("".join(parts))

    def for_meeting(self, meeting: dict):
        """For a page of many meetings (the decisions): a linker of the terms one meeting uses (its
        "glossary", which minds the boards a term is limited to), remembering what the page has linked."""
        slugs = {e["slug"] for e in meeting.get("glossary", [])}

        def link(text, english: bool = False) -> Markup:
            entries, self.entries = self.entries, [e for e in self.entries if e["slug"] in slugs]
            try:
                return self(text, english)
            finally:
                self.entries = entries
        return link

    def _link(self, part: str, lang: str) -> str:
        # Matched on the text as it reads (entities undone, accents composed, so it's as long as its
        # plain() form), then put back escaped, one stretch at a time.
        text = unicodedata.normalize("NFC", html.unescape(part))
        spans = []
        for e in self.entries:
            if e["slug"] in self.linked:
                continue
            m = found(e, text, lang) or (found(e, text, "en") if lang != "en" else None)
            if not m or any(m.start() < b and a < m.end() for a, b, _ in spans):
                continue
            spans.append((m.start(), m.end(), e))
            self.linked.add(e["slug"])
        if not spans:
            return part
        out, at = [], 0
        for start, end, e in sorted(spans, key=lambda s: s[0]):
            out.append(str(escape(text[at:start])))
            out.append(f'<a class="term" href="{PAGE}#{e["slug"]}">{escape(text[start:end])}</a>')
            at = end
        out.append(str(escape(text[at:])))
        return "".join(out)
