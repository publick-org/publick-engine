"""Summaries in the site's other languages, translated from the English summary.

A town with Spanish pages ([site] languages) gets each English summary of an
agenda or minutes in Spanish too: translated from the English summary (never
from the PDF again) by Claude Sonnet 5.5 at low effort, unless [summaries]
translation_model says otherwise. (Claude Haiku 4.5 did it until version 4,
and more of its translations failed the checks than passed them, so it cost
more per translation shown.) The rules are the summaries' own: only what
the summary says, neutral, plain, with names, addresses, amounts, dates, and
vote counts kept.

No person checks the translations, so two checks do. One without AI, entry by
entry: each list (agenda items, decisions) has as many entries, every number
is kept and none added (however Spanish writes it), amounts keep their million
or billion, times their a.m. or p.m., names are kept as written, and what
happened isn't turned round (a "not" lost, approved as denied, tabled as
approved, unanimous changed). Then, for a translation that passes, a second
request (Claude Sonnet 5.5 unless [summaries] translation_review_model says
otherwise) reviews its meaning against the English, knowing the translator's
rules and words: adjourned shown as dissolved, a guessed gender, a
mistranslated board. A translation that fails either is made again once
(ATTEMPTS), as a correction: the model gets its first translation and what
was wrong with it. Then it's kept, so it isn't paid for again, but not shown:
the page shows the English summary, and says so.

Each translation is its own record, data/summaries/<language>/<document's
SHA-256>.json, holding the hash of the English it came from. Turning a
language on never regenerates an English summary, and a translation is made
again only when its English summary changes (or this module's prompt does:
VERSION). summarize.py runs the translations within the same budget as the
summaries, new documents first, and older documents' before the older English
summaries waiting, which cost about ten times as much; their cost is in the
month's ledger as translation_cost.

A town's own text (its config's tagline, section summaries, officials' seats)
and the names in its data (boards, 311 categories) come from the town's
[strings.<language>] when it has them, else from the engine's own Spanish for
what many towns share ("Planning Board", "Ward 3"). Whatever is still missing
is drafted here by the same small model, a batch per run: draft_texts(),
checked without AI (every number and placeholder kept) and reviewed by the
larger model, and saved in data/strings/<language>.json. A draft that passes
is shown, so a new town or a new board needs no one's translation; one that
fails twice stays in English. `python -m pipeline.translate drafts` prints the
drafts as [strings.<language>] lines, for anyone who wants to correct them in
the town's config, which then wins.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from pipeline.files import write_atomic

VERSION = 4
DEFAULT_MODEL = "claude-sonnet-5-5"
# Claude Sonnet 5.5's prices, dollars per million tokens, for a town that doesn't give the model's own.
DEFAULT_PRICES = {"input_price": 2.0, "output_price": 10.0}
# Translating a short summary needs little thinking.
DEFAULT_EFFORT = "low"
MAX_TOKENS = 8000

# The fields of each kind of summary that are shown, and so translated.
FIELDS = {"agenda": ("headline", "summary", "items"), "minutes": ("headline", "summary", "decisions")}

LANGUAGE_NAMES = {"es": "Spanish"}
# Each language's readers, and the words the sites use for what summaries talk about
# (site/strings/es-guide.md has the full glossary).
READERS = {"es": """Write plain Spanish as residents of a New England city or town read it every day, most of them Puerto Rican, Dominican, or from elsewhere in Latin America: natural, not formal, not word for word, and not Spain's Spanish. Address no one directly.
Use these words: meeting = reunión; minutes = actas; agenda = agenda; public hearing = audiencia pública; motion = moción; vote = votación; executive session = sesión ejecutiva; councilor = concejal; fiscal year = año fiscal; property tax = impuesto a la propiedad; budget = presupuesto; building permit = permiso de construcción; ward = distrito.
Words that are easy to get wrong: adjourn = levantar la sesión (never "disolver"); appoint = nombrar, reappoint = volver a nombrar (never "reelegir": an appointment isn't an election); elect = elegir; table a motion = posponer; sign (on a building or road) = letrero (a "señal" is a traffic sign); name a street after someone = ponerle a una calle el nombre de alguien; an all-alcoholic beverages license = licencia para todo tipo de bebidas alcohólicas; an underage operative (a minor sent into a business in a compliance check) = un menor que colabora con la policía.
Write every date with its month's name ("22 de octubre de 2026", "17 de octubre"), never in figures like 10/17, which a Spanish reader takes as 10 July. Times as "7:00 p. m.", and numbers and money as the English does ("$1,500", "$3 millones", "4.5%")."""}

SYSTEM = """You translate short summaries of a city government's public meeting agendas and minutes from English into {language}, for residents.

Rules:
- Translate only what the English says. Add nothing, leave nothing out, and don't explain.
- Keep the neutral tone. No words that judge.
- Short sentences, about an 8th-grade reading level.
- Keep every name of a person, business, street, address, and place exactly as written, and keep every number: dollar amounts, dates, times, vote counts ("5-0"), and case, application, and order numbers.
- Board and committee names: use the translations given below when there are any; otherwise keep the English name.
- Never guess anyone's gender. Use the gender the English gives ("he", "she", "Mr.", "Ms."); when it gives none, put the name first and the role after it ("Scott Houseman, presidente del comité"), or use the role without an article, rather than "el presidente" or "la presidenta".
- Every word in Spanish except names: no English words left in a Spanish sentence.
- Return the same fields, and each list with as many entries, in the same order.

{readers}"""

PROMPT = """Translate this summary of the {kind} of a meeting ({title}, {date}) into {language}.
{boards}
{summary}"""

# Added to the prompt for a second try: the first translation, and what the checks found wrong with it.
CORRECT = """

An earlier translation of it didn't pass a check:
{previous}

What was wrong: {problems}

Translate it again, fixing these, and keep what was right."""


def path(data_dir: Path, lang: str, sha256: str) -> Path:
    return data_dir / "summaries" / lang / f"{sha256}.json"


def english(record: dict, kind: str) -> dict:
    """The parts of an English summary that are shown, and so translated."""
    return {field: record.get(field) or ([] if field in ("items", "decisions") else "") for field in FIELDS[kind]}


def source_hash(record: dict, kind: str) -> str:
    """Which English a translation was made from."""
    return hashlib.sha256(json.dumps(english(record, kind), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def saved(data_dir: Path, lang: str, sha256: str) -> dict | None:
    file = path(data_dir, lang, sha256)
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None


# A translation that fails its checks is made again once, then left (and the English shown).
ATTEMPTS = 2


def current(data_dir: Path, lang: str, sha256: str, record: dict, kind: str) -> dict | None:
    """The saved translation of this English summary, made with the current prompt, when there's
    no need to make it again: it passed its checks, or it's had its ATTEMPTS."""
    found = saved(data_dir, lang, sha256)
    if found and found.get("source_hash") == source_hash(record, kind) and found.get("prompt_version") == VERSION and (
            passes(data_dir, found, record, kind, lang) or found.get("attempts", 1) >= ATTEMPTS):
        return found
    return None


def passes(data_dir: Path, found: dict, record: dict, kind: str, lang: str) -> bool:
    """Whether a saved translation passes both checks: the one without AI, run again here (so a
    translation the check once wrongly failed is shown once the check is fixed, without paying for
    it again), and the AI's review of its meaning, as saved."""
    return (found.get("review") == "ok"
            and check(english(record, kind), found, kind, lang, town_words(data_dir)) == "ok")


def shown(data_dir: Path, lang: str, sha256: str, record: dict, kind: str) -> dict | None:
    """The translation to show for an English summary: one made from this English, with the
    current prompt, that passes both checks."""
    found = saved(data_dir, lang, sha256)
    if found and found.get("source_hash") == source_hash(record, kind) and found.get("prompt_version") == VERSION \
            and passes(data_dir, found, record, kind, lang):
        return found
    return None


def what_failed(data_dir: Path, found: dict, record: dict, kind: str, lang: str) -> str | None:
    """What was wrong with a saved translation, for the model to correct: the check's finding,
    else the review's. None when it passed, or when the review couldn't be made (nothing to correct)."""
    checked = check(english(record, kind), found, kind, lang, town_words(data_dir))
    if checked != "ok":
        return checked
    review = found.get("review", "")
    return None if review == "ok" or review.startswith(UNREVIEWED) else review


def failed(data_dir: Path, lang: str, sha256: str, record: dict, kind: str) -> bool:
    """Whether this English summary's translation was made and failed its checks for good."""
    found = current(data_dir, lang, sha256, record, kind)
    return bool(found) and not passes(data_dir, found, record, kind, lang)


# A number as the summaries write it: "1,500", "4.5", "7:00", "2026", the 5 and the 0 of "5-0".
NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")


# A date in figures, as agendas write it: "9/23/2026", "9/23/26", "9/23".
DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b")
# Each language's month names, January first, for a date the translation writes out.
MONTHS = {"es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                 "septiembre|setiembre", "octubre", "noviembre", "diciembre")}


def canonical(n: str) -> str:
    """A number as a value, however it's written: "1,500", "1.500", and "1500" are 1500; "5,8" and
    "5.8" are 5.8; "7:00" is 7."""
    n = n.rstrip(".,:")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", n):
        return re.sub(r"[.,]", "", n)
    if re.fullmatch(r"\d+[.,]\d{1,2}", n):
        return n.replace(",", ".")
    return re.sub(r":00$", "", n)


def numbers(text: str, ordinals: bool = False) -> Counter:
    """The numbers in a text, as values. Ordinals ("2nd", "6th") only if asked: a translation may
    write them as words ("segunda") or as plain numbers ("el 6 de agosto")."""
    return Counter(canonical(m.group(0)) for m in NUMBER.finditer(text or "")
                   if ordinals or not re.match(r"(?:st|nd|rd|th)\b", text[m.end():], re.I))


# A number with its scale: "$3 million", "3 millones", "$1.2 billion", "1,2 mil millones".
SCALES = {"en": [(r"billion|bn\b|B\b", "B"), (r"million|M\b", "M"), (r"thousand|K\b", "K")],
          "es": [(r"mil\s+millones|millardos?|B\b", "B"), (r"mill[oó]n(?:es)?|M\b", "M"), (r"mil\b|K\b", "K")]}


def scaled(text: str, lang: str) -> Counter:
    """Each number written with a scale word, with its scale: {("3", "M"): 1}."""
    found = Counter()
    for m in NUMBER.finditer(text or ""):
        after = text[m.end():m.end() + 16]
        for pattern, scale in SCALES[lang]:
            if re.match(rf"\s*(?:{pattern})", after, re.I):
                found[(canonical(m.group(0)), scale)] += 1
                break
    return found


# A clock time with a.m. or p.m.: "7 pm", "7:00 p.m.", "7:00 p. m.".
TIME = re.compile(r"\b(\d{1,2}(?::\d{2})?)\s*([ap])\.?\s?m\b\.?", re.I)


def times(text: str) -> Counter:
    return Counter((canonical(t), half.lower()) for t, half in TIME.findall(text or ""))


@lru_cache(maxsize=1)
def english_words() -> frozenset:
    """Ordinary English words (GCIDE, lowercased), so a name is a capitalized word that isn't one."""
    from english_words import get_english_words_set
    return frozenset(get_english_words_set(["gcide"], lower=True, alpha=True))


# Forms of verbs the dictionary lacks, which a decision often starts with ("Withdrew DOC #348/19").
IRREGULAR = {"began", "brought", "dealt", "forgiven", "overridden", "overrode", "oversaw", "withdrawn", "withdrew",
             "withheld"}
# Ordinary words the dictionary lacks that summaries capitalize in names of projects and items
# ("Geothermal Study", "Riverfront Plan"), which no town has written in lowercase yet.
NEWER_WORDS = {"coordinator", "daycare", "defibrillator", "forecourt", "geothermal", "microenterprise", "pickleball",
               "preschool", "riverfront", "roundtable", "victualler", "wastewater", "waterfront", "wellness", "workforce"}
WEEKDAYS_MONTHS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
                   "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"}


def summary_dirs(data_dir: Path) -> list[Path]:
    """The folders of English summaries whose words count: the town's, and in a network
    (towns/<town>/data), every town's, so a word one town writes in lowercase ("wastewater",
    "coordinator") is ordinary in all of them."""
    towns = data_dir.parent.parent
    if towns.name == "towns":
        return sorted(t / "data" / "summaries" for t in towns.iterdir() if (t / "data" / "summaries").is_dir())
    return [data_dir / "summaries"]


@lru_cache(maxsize=32)
def town_words(data_dir: Path | None) -> frozenset:
    """The words English summaries use in lowercase, which are no one's name: newer words the
    dictionary lacks ("input", "tourism"). Whole words only: "Bryan" doesn't make "ryan" a word."""
    words: set[str] = set()
    for folder in summary_dirs(data_dir) if data_dir else []:
        for file in sorted(folder.glob("*.json")):
            words |= summary_words(json.loads(file.read_text(encoding="utf-8")))
    return frozenset(words)


def summary_words(record: dict) -> set[str]:
    """One summary's words in lowercase, as town_words() counts them."""
    words: set[str] = set()
    for field in ("headline", "summary", "items", "decisions"):
        value = record.get(field) or ""
        for text in value if isinstance(value, list) else [value]:
            words.update(re.findall(r"\b[a-z]+\b", text))
    return words


def is_word(word: str, also: frozenset = frozenset()) -> bool:
    w = word.lower()
    known = english_words()
    if w in WEEKDAYS_MONTHS or w in IRREGULAR or w in NEWER_WORDS or w in known or w in also:
        return True
    stems = [w[:-len(end)] + add for end, add in (("s", ""), ("es", ""), ("ies", "y"), ("ied", "y"), ("ed", ""), ("ed", "e"),
                                                  ("ing", ""), ("ing", "e"), ("ary", ""), ("ism", ""), ("al", ""))
             if w.endswith(end)]
    # Doubled before an ending: "planning", "referred".
    stems += [stem[:-1] for stem in stems if len(stem) > 2 and stem[-1] == stem[-2]]
    return any(stem in known or stem in also or stem in NEWER_WORDS for stem in stems)


# Abbreviations, with their period, that a translation may write out ("Ch." as "Cap." or "capítulo"):
# words, not names. Business suffixes (Inc., Corp.) aren't here: a business's name is kept.
ABBREVIATIONS = {"Art", "Ave", "Blvd", "Ch", "Chap", "Dir", "Est", "Ext", "No", "Rd", "Sec", "Secs", "St", "Vol"}
# Those that are never anyone's name, with or without a period ("Asst City Clerk").
ALWAYS_ABBREVIATIONS = {"Admin", "Approx", "Asst", "Dept", "Govt", "Mgr", "Misc", "Supt"}


def names(text: str, also: frozenset = frozenset()) -> set[str]:
    """The names in an English text: capitalized words that aren't ordinary English words
    ("Houseman" is one, "Collector" isn't), which a translation keeps as written. also: more
    ordinary words (town_words()). An abbreviation in ABBREVIATIONS with its period, or in
    ALWAYS_ABBREVIATIONS, isn't one."""
    return {m.group(1) for m in re.finditer(r"\b([A-Z][a-z]+)\b(\.?)", text or "")
            if not (m.group(2) and m.group(1) in ABBREVIATIONS) and m.group(1) not in ALWAYS_ABBREVIATIONS
            and not is_word(m.group(1), also)}


def plain(text: str) -> str:
    """Text without its accents, so "América" keeps "America"."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


# What a decision says happened, in each language, to catch a translation that turns it round.
NOT = {"en": re.compile(r"\b(?:not|never)\b|n't\b", re.I), "es": re.compile(r"\b(?:no|nunca|ni|sin)\b", re.I)}
SAYS_NO = {"en": re.compile(r"\b(?:not|never|no|none|nothing|without|den(?:y|ied|ies|ial|ying)|reject\w*|fail\w*|defeat\w*|"
                            r"disapprov\w*|declin\w*|against|oppos\w*)\b|n't\b", re.I)}
APPROVE = {"en": re.compile(r"\b(?:approv\w*|adopt\w*|grant\w*|pass(?:ed|es)?|carried|endors\w*|ratif\w*)\b", re.I),
           "es": re.compile(r"\b(?:aprob\w*|aprueb\w*|adopt\w*|otorg\w*|conced\w*|concedi\w*|ratific\w*)", re.I)}
DENY = {"en": re.compile(r"\b(?:den(?:y|ied|ies|ial|ying)|reject\w*|fail(?:ed|s)?|defeat\w*|disapprov\w*|declin(?:e|ed|es|ing))\b",
                        re.I),
        "es": re.compile(r"\b(?:neg(?:ó|aron|ada|adas|ado|ados|ar|ación)|deneg\w*|rechaz\w*|desaprob\w*|fracas\w*)", re.I)}
TABLED = {"en": re.compile(r"\b(?:tabled|tabling|postpon\w*)\b", re.I),
          "es": re.compile(r"\b(?:posterg\w*|aplaz\w*|pospu\w*|pospon\w*|archiv\w*|tabl\w*|suspend\w*|sobre la mesa|difiri\w*)",
                           re.I)}
# "No" in a translation, but not as a prefix ("no conforme" is nonconforming), and "sin" ("not to exceed": "sin exceder").
NOES = {"es": re.compile(r"\bsin\b|\b(?:no|nunca)\b(?!\s+(?:conform|residencial|lucrativ|profesional|elegible|permitid|esencial|"
                         r"emergencia|vinculante|aplicable|incluid|deseable|existente|asignad|binari|reembolsable|exent|"
                         r"tradicional|oficial|relacionad|municipal|pagad))", re.I)}
# "No" with a decision's verb: "no se aprobó", "no fue aprobada", "no recomendó".
NO_DECISION = {"es": re.compile(r"\bno\s+(?:se\s+)?(?:(?:fue|fueron)\s+)?(?:aprob|aprueb|adopt|otorg|conced|pas|acept|recomend|vot)",
                                re.I)}
UNANIMOUS = {"en": re.compile(r"\bunanim\w*", re.I), "es": re.compile(r"\bun[aá]nim\w*|\bsin oposici[oó]n", re.I)}


def outcome(en: str, tr: str, lang: str) -> str | None:
    """What a translation got wrong about what happened, if anything: a "not" dropped or added,
    approved turned into denied or back, tabled lost, or unanimous changed."""
    # As many noes (not, never, denied, failed) in each, so one can't stand in for another.
    noes_en = len(NOT["en"].findall(en)) + len(DENY["en"].findall(en))
    noes_tr = len(NOES[lang].findall(tr)) + len(DENY[lang].findall(tr))
    if noes_en > noes_tr:
        return "a \"not\", denied, or failed in the English isn't in the translation"
    if noes_tr > noes_en and (NO_DECISION[lang].search(tr) or DENY[lang].search(tr)):
        return "the translation says no where the English doesn't"
    if DENY["en"].search(en) and not (DENY[lang].search(tr) or NOT[lang].search(tr)):
        return "denied or failed in the English, not in the translation"
    if DENY[lang].search(tr) and not SAYS_NO["en"].search(en):
        return "denied in the translation, not in the English"
    if (APPROVE["en"].search(en) and not SAYS_NO["en"].search(en) and not TABLED["en"].search(en)
            and not APPROVE[lang].search(tr) and TABLED[lang].search(tr)):
        return "approved in the English, tabled in the translation"
    if TABLED["en"].search(en) and not TABLED[lang].search(tr):
        return "tabled in the English, not in the translation"
    if bool(UNANIMOUS["en"].search(en)) != bool(UNANIMOUS[lang].search(tr)):
        return "unanimous in one and not the other"
    return None


def dates_kept(en: str, tr: str, lang: str) -> tuple[str, str]:
    """The two texts without the English's dates in figures that the translation has too, in
    figures (month and day in either order) or written out ("23 de septiembre de 2026")."""
    for m in DATE.finditer(en):
        month, day, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        full_year = year if not year or len(year) == 4 else "20" + year
        in_figures = rf"\b0?(?:{month}/0?{day}|{day}/0?{month})" + (rf"/(?:{full_year}|{full_year[2:]})" if year else "") + r"\b"
        found = re.search(in_figures, tr)
        if not found and lang in MONTHS:
            written = rf"\b0?{day}\s+de\s+(?:{MONTHS[lang][month - 1]})" + (rf"\s+del?\s+{full_year}" if year else "")
            found = re.search(written, tr, re.I)
        if found:
            en = en.replace(m.group(0), " ", 1)
            tr = tr[:found.start()] + " " + tr[found.end():]
    return en, tr


# The English's ways of writing what a translation writes out, made the same before comparing:
# "FY27" is "fiscal year 2027" ("año fiscal 2027"); "not to exceed" is a cap, not a "not";
# "9:00-11:00 A.M." is two morning times; "1.28.26" is a date in figures.
FISCAL_YEAR = re.compile(r"\bFY\s?'?(\d{2})\b")
NOT_TO_EXCEED = re.compile(r"\bnot\s+to\s+exceed\b", re.I)
TIME_RANGE = re.compile(r"\b(\d{1,2}(?::\d{2})?)\s*[-–]\s*(\d{1,2}(?::\d{2})?)\s*([ap])\.?\s?m\b\.?", re.I)
DOTTED_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b")


def comparable(en: str, tr: str) -> tuple[str, str]:
    """The English and its translation, each written as the other would write it (above)."""
    en = FISCAL_YEAR.sub(r"FY 20\1", en)
    en = NOT_TO_EXCEED.sub("up to", en)
    en = TIME_RANGE.sub(lambda m: f"{m.group(1)} {m.group(3)}.m.-{m.group(2)} {m.group(3)}.m.", en)
    en = DOTTED_DATE.sub(r"\1/\2/\3", en)
    # A translation that writes the fiscal year out and keeps the English after it: "año fiscal 2027 (FY27)".
    tr = re.sub(r"\s*\(FY\s?'?\d{2,4}\)", "", tr)
    tr = FISCAL_YEAR.sub(r"FY 20\1", tr)
    tr = re.sub(r"\b(año fiscal)\s+(\d{2})\b", r"\1 20\2", tr, flags=re.I)
    return en, tr


def check(source: dict, translated: dict, kind: str, lang: str = "es", words: frozenset = frozenset()) -> str:
    """ "ok", or what's wrong, entry by entry, so a list put in another order fails too: a field
    missing or empty, a list of another length, a number lost or added (however the language
    writes it, and a date in figures may be written out), an amount's million or billion, a.m. or
    p.m., a name not kept as written (words: the town's own ordinary words, town_words()), or what
    happened turned round (outcome())."""
    for field in FIELDS[kind]:
        en, tr = source[field], translated.get(field)
        if isinstance(en, list):
            if not isinstance(tr, list) or len(tr) != len(en):
                return f"{field}: {len(tr) if isinstance(tr, list) else 'no'} entries for {len(en)}"
            pairs = list(zip(en, tr))
        else:
            if not isinstance(tr, str) or (en.strip() and not tr.strip()):
                return f"{field}: missing"
            pairs = [(en, tr)]
        for i, (a, b) in enumerate(pairs):
            where = f"{field} {i + 1}" if isinstance(en, list) else field
            a, b = comparable(a, b)
            if scaled(a, "en") != scaled(b, lang):
                return f"{where}: amounts' scale differs (million, billion)"
            if times(a) != times(b):
                return f"{where}: a.m. or p.m. differs"
            lost_names = sorted(n for n in names(a, words) if not re.search(rf"\b{n}\b", plain(b)))
            if lost_names:
                return f"{where}: {', '.join(lost_names)} not in the translation"
            wrong = outcome(a, b, lang)
            if wrong:
                return f"{where}: {wrong}"
            a, b = dates_kept(a, b, lang)
            lost = numbers(a) - numbers(b)
            if lost:
                return f"{where}: {', '.join(sorted(lost))} not in the translation"
            added = numbers(b) - numbers(a, ordinals=True)
            if added:
                return f"{where}: {', '.join(sorted(added))} added in the translation"
    return "ok"


def settings(config: dict) -> dict:
    """The translation model, its prices, and its effort: [summaries] translation_model,
    translation_input_price, translation_output_price, and translation_effort, or Claude Sonnet
    5.5's at low effort. Another model has no effort unless the town gives one (Claude Haiku 4.5
    takes none)."""
    s = config.get("summaries", {})
    model = s.get("translation_model", DEFAULT_MODEL)
    prices = DEFAULT_PRICES if model == DEFAULT_MODEL else {}
    return {"model": model,
            "input_price": s.get("translation_input_price", prices.get("input_price")),
            "output_price": s.get("translation_output_price", prices.get("output_price")),
            "effort": s.get("translation_effort", DEFAULT_EFFORT if model == DEFAULT_MODEL else None)}


def output_config(config: dict, schema: dict) -> dict:
    """The request's output_config: the schema, and the translation model's effort if it has one."""
    effort = settings(config)["effort"]
    return {**({"effort": effort} if effort else {}), "format": {"type": "json_schema", "schema": schema}}


def languages(config: dict) -> list[str]:
    """The languages besides English that this town's summaries are translated into."""
    return [lang for lang in config["site"].get("languages", ["en"]) if lang != "en" and lang in LANGUAGE_NAMES]


def board_names(config: dict, lang: str, board: str) -> str:
    """The board's name in the language, from the town's [strings.<language>], for the prompt."""
    name = config.get("strings", {}).get(lang, {}).get(board)
    return f'The board is "{board}"; in {LANGUAGE_NAMES[lang]}, "{name}".\n' if name else ""


def schema(kind: str) -> dict:
    props = {field: ({"type": "array", "items": {"type": "string"}} if field in ("items", "decisions") else {"type": "string"})
             for field in FIELDS[kind]}
    return {"type": "object", "properties": props, "required": list(FIELDS[kind]), "additionalProperties": False}


def translate(client, config: dict, lang: str, kind: str, record: dict, meeting: dict,
              previous: dict | None = None, problems: str = "") -> tuple[dict, dict]:
    """The model's translation of one English summary, and the tokens it used. previous: an
    earlier translation that failed a check, to correct, and problems: what was wrong with it."""
    source = english(record, kind)
    prompt = PROMPT.format(kind=kind, title=meeting["title"], date=meeting["date"], language=LANGUAGE_NAMES[lang],
                           boards=board_names(config, lang, meeting["body"]),
                           summary=json.dumps(source, ensure_ascii=False, indent=1))
    if previous:
        prompt += CORRECT.format(previous=json.dumps({f: previous.get(f) for f in FIELDS[kind]}, ensure_ascii=False, indent=1),
                                 problems=problems)
    response = client.messages.create(
        model=settings(config)["model"],
        max_tokens=MAX_TOKENS,
        system=SYSTEM.format(language=LANGUAGE_NAMES[lang], readers=READERS[lang]),
        messages=[{"role": "user", "content": prompt}],
        output_config=output_config(config, schema(kind)),
    )
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    if response.stop_reason != "end_turn":
        from pipeline.summarize import StoppedEarly
        raise StoppedEarly(response.stop_reason, usage)
    return json.loads(next(b.text for b in response.content if b.type == "text")), usage


def save(data_dir: Path, lang: str, sha256: str, record: dict) -> None:
    file = path(data_dir, lang, sha256)
    write_atomic(file, json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def make(client, config: dict, data_dir: Path, lang: str, kind: str, meeting: dict, doc: dict, record: dict,
         now: datetime, cost) -> tuple[dict, float]:
    """Translate one summary, check it, have the AI review its meaning if it passes, and save it.
    A second try corrects the first: the model gets it and what was wrong with it.
    Returns the saved record and what it cost."""
    source = english(record, kind)
    before = saved(data_dir, lang, doc["sha256"])
    again = bool(before and before.get("source_hash") == source_hash(record, kind) and before.get("prompt_version") == VERSION)
    wrong = what_failed(data_dir, before, record, kind, lang) if again else None
    result, usage = translate(client, config, lang, kind, record, meeting, before if wrong else None, wrong or "")
    paid = cost(usage, settings(config))
    checked = check(source, result, kind, lang, town_words(data_dir))
    reviewed, review_cost = "not reviewed: the check failed", 0.0
    if checked == "ok":
        problems, review_cost = review_meaning(client, config, lang, [(f, source[f], result.get(f)) for f in FIELDS[kind]], cost)
        reviewed = "; ".join(f"{key}: {problem}" for key, problem in problems.items()) or "ok"
    out = {
        **{field: result.get(field) for field in FIELDS[kind]},
        "language": lang,
        "kind": kind,
        "source_sha256": doc["sha256"],
        "source_hash": source_hash(record, kind),
        "check": checked,
        "review": reviewed,
        "attempts": before.get("attempts", 1) + 1 if again else 1,
        "model": settings(config)["model"],
        "prompt_version": VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "usage": usage,
        "cost": round(paid + review_cost, 6),
    }
    save(data_dir, lang, doc["sha256"], out)
    return out, paid + review_cost


# ---- The AI's review of a translation's meaning ----------------------------------

DEFAULT_REVIEW_MODEL = "claude-sonnet-5-5"
# Claude Sonnet 5.5's prices, dollars per million tokens.
DEFAULT_REVIEW_PRICES = {"input_price": 2.0, "output_price": 10.0}

REVIEW_SYSTEM = """You check translations from English into {language} of short texts from a US city or town government's website: summaries of meeting agendas and minutes, and names of boards, roles, and services. No person checks them after you, and residents rely on them.

For each English text and its translation, report every error that changes the meaning or would mislead a reader:
- what happened changed: adjourned shown as dissolved, reappointed shown as re-elected, appointed shown as elected, approved shown as denied or tabled, a "not" lost or added, unanimous changed, a recommendation shown as a decision;
- a person's gender stated where the English doesn't give it ("la presidenta" for "Chair Houseman");
- a name, place, street, business, amount, date, time, or number changed, or anything added or left out;
- a mistranslation ("Conservation Commission" as "Comisión de Conversación", "Mayor" as "Gobernador", a business sign as a traffic sign);
- English words left in a {language} sentence (names of people, places, businesses, and programs excepted), or words that don't exist.

Don't report style, or a different word choice that keeps the meaning. Report nothing for a faithful translation: if, once you've looked, a translation is right, leave it out, or mark it is_error false.

The translator was given these words and rules, so a translation that follows them isn't wrong for it:
{readers}"""

REVIEW_PROMPT = """Check these translations. Each has an id, the English, and the {language}:
{pairs}"""


def review_settings(config: dict) -> dict:
    """The reviewing model and its prices: [summaries] translation_review_model,
    translation_review_input_price, and translation_review_output_price, or Claude Sonnet 5.5's."""
    s = config.get("summaries", {})
    model = s.get("translation_review_model", DEFAULT_REVIEW_MODEL)
    prices = DEFAULT_REVIEW_PRICES if model == DEFAULT_REVIEW_MODEL else {}
    return {"model": model,
            "input_price": s.get("translation_review_input_price", prices.get("input_price")),
            "output_price": s.get("translation_review_output_price", prices.get("output_price"))}


# The key a review's problem is under when nothing could be reviewed.
UNREVIEWED = "not reviewed"


def review_meaning(client, config: dict, lang: str, pairs: list[tuple[str, object, object]], cost) -> tuple[dict[str, str], float]:
    """The AI's review of translations' meaning. pairs: (id, English, translation), where a text
    may be a list of texts (its entries' ids are "<id> 1", "<id> 2", ...). Returns the problems
    found, {id: problem}, empty if none, and what the review cost. A review that can't be made
    returns {UNREVIEWED: why}, so nothing it didn't review is shown."""
    rsettings = review_settings(config)
    if rsettings["input_price"] is None or rsettings["output_price"] is None:
        return {UNREVIEWED: "[summaries] needs translation_review_input_price and translation_review_output_price"}, 0.0
    rows = []
    for key, en, tr in pairs:
        if isinstance(en, list):
            rows += [{"id": f"{key} {i + 1}", "english": a, "translation": b}
                     for i, (a, b) in enumerate(zip(en, tr if isinstance(tr, list) else []))]
        elif en:
            rows.append({"id": key, "english": en, "translation": tr})
    if not rows:
        return {}, 0.0
    try:
        response = client.messages.create(
            model=rsettings["model"],
            max_tokens=4000,
            system=REVIEW_SYSTEM.format(language=LANGUAGE_NAMES[lang], readers=READERS[lang]),
            messages=[{"role": "user", "content": REVIEW_PROMPT.format(
                language=LANGUAGE_NAMES[lang], pairs=json.dumps(rows, ensure_ascii=False, indent=1))}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": {
                "type": "object", "additionalProperties": False, "required": ["problems"],
                "properties": {"problems": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["id", "problem", "is_error"],
                    "properties": {"id": {"type": "string"}, "problem": {"type": "string"},
                                   "is_error": {"type": "boolean"}}}}}}}},
        )
    except Exception as e:
        if "credit balance" in str(e).lower():
            raise
        return {UNREVIEWED: str(e)}, 0.0
    paid = cost({"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}, rsettings)
    if response.stop_reason != "end_turn":
        return {UNREVIEWED: f"stopped ({response.stop_reason})"}, paid
    problems: dict[str, str] = {}
    for p in json.loads(next(b.text for b in response.content if b.type == "text"))["problems"]:
        # An entry that, written out, found nothing wrong ("this is faithful") isn't a problem.
        if not p["is_error"]:
            continue
        problems[p["id"]] = f"{problems[p['id']]}; {p['problem']}" if p["id"] in problems else p["problem"]
    return problems, paid


def month_counts(data_dir: Path) -> dict[str, dict]:
    """What the saved translations cost, by the month they were made: {month: {"cost", "translations"}}.
    A batch of drafted texts counts as one translation."""
    counted: dict[str, dict] = {}
    records = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((data_dir / "summaries").glob("*/*.json"))]
    for file in sorted((data_dir / "strings").glob("*.json")):
        records += json.loads(file.read_text(encoding="utf-8")).get("batches", [])
    for record in records:
        at = record.get("generated_at", "")[:7]
        if at:
            row = counted.setdefault(at, {"cost": 0.0, "translations": 0})
            row["cost"] += record.get("cost", 0.0)
            row["translations"] += 1
    return counted


# ---- A town's own text and names, drafted ------------------------------------

NAMES_VERSION = 2
# Texts per request: one batch covers a new town's config and boards.
NAMES_BATCH = 80

NAMES_SYSTEM = """You translate short texts from a US city or town government's website from English into {language}, for residents: names of boards and committees, officials' seats and roles, 311 request categories, section titles and descriptions, and glossary definitions.

Rules:
- Translate each text on its own, and return exactly one translation for each, in the same order.
- Boards and committees: the natural {language} name a resident would understand ("Planning Board" = "Junta de Planificación"). Keep the names of people, places, businesses, and programs, and acronyms, as written.
- Role titles (Chair, Vice Chair, Secretary) label whoever holds them: use the generic form ("Presidente", "Vicepresidente", "Secretario"). Never guess anyone's gender.
- A ward is "distrito"; a precinct is "precinto"; at-large or citywide seats are "toda la ciudad" (a town's: "todo el municipio").
- Keep every number, and every {{placeholder}} and %(placeholder)s, exactly.
- Plain and short, about an 8th-grade reading level. Neutral: no words that judge.

{readers}"""

NAMES_PROMPT = """The website of {town}, {state}. Translate these {n} texts into {language}:
{texts}"""

# {name} and %(name)s in a text, which its translation must keep.
PLACEHOLDER = re.compile(r"%\((\w+)\)s|\{(\w*)\}")


def strings_path(data_dir: Path, lang: str) -> Path:
    return data_dir / "strings" / f"{lang}.json"


def saved_drafts(data_dir: Path, lang: str) -> dict:
    """The town's drafts file: {"drafts": {English: {"text", "check", ...}}, "batches": [...]}."""
    file = strings_path(data_dir, lang)
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"drafts": {}, "batches": []}


def drafts(data_dir: Path, lang: str) -> dict[str, str]:
    """The drafts that can be shown: {English: translation}, each made with the current prompt
    and passing the check."""
    return {en: d["text"] for en, d in saved_drafts(data_dir, lang)["drafts"].items()
            if d.get("check") == "ok" and d.get("prompt_version") == NAMES_VERSION}


def check_text(en: str, tr) -> str:
    """ "ok", or what's wrong with one drafted text: empty, or a number or placeholder lost."""
    if not isinstance(tr, str) or not tr.strip():
        return "missing"
    lost = numbers(en) - numbers(tr)
    if lost:
        return f"{', '.join(sorted(lost))} not in the translation"
    lost = Counter(a or b for a, b in PLACEHOLDER.findall(en)) - Counter(a or b for a, b in PLACEHOLDER.findall(tr))
    if lost:
        return f"placeholder {', '.join(sorted(lost))} not in the translation"
    return "ok"


def draft_texts(client, config: dict, data_dir: Path, lang: str, texts: list[str], now: datetime, cost,
                allowance: float | None = None) -> tuple[int, float]:
    """Draft the texts (a town's own text and names with no translation yet), check each, and
    save them in data/strings/<language>.json. Stops before a batch once allowance (dollars) is
    spent. Returns how many were drafted and what they cost."""
    tsettings = settings(config)
    saved = saved_drafts(data_dir, lang)
    done, spent = 0, 0.0
    # A text whose drafts failed ATTEMPTS times is left in English, not paid for every run.
    texts = [t for t in dict.fromkeys(texts) if not (
        saved["drafts"].get(t, {}).get("prompt_version") == NAMES_VERSION and saved["drafts"][t].get("attempts", 1) >= ATTEMPTS)]
    for start in range(0, len(texts), NAMES_BATCH):
        if allowance is not None and spent >= allowance:
            break
        batch = texts[start:start + NAMES_BATCH]
        response = client.messages.create(
            model=tsettings["model"],
            max_tokens=MAX_TOKENS,
            system=NAMES_SYSTEM.format(language=LANGUAGE_NAMES[lang], readers=READERS[lang]),
            messages=[{"role": "user", "content": NAMES_PROMPT.format(
                town=config["town"]["name"], state=config["town"]["state"], n=len(batch), language=LANGUAGE_NAMES[lang],
                texts=json.dumps(batch, ensure_ascii=False, indent=1))}],
            output_config=output_config(config, {
                "type": "object", "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
                "required": ["translations"], "additionalProperties": False}),
        )
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        paid = cost(usage, tsettings)
        spent += paid
        at = now.isoformat(timespec="seconds")
        saved["batches"].append({"generated_at": at, "texts": len(batch), "usage": usage, "cost": round(paid, 6)})
        result = json.loads(next(b.text for b in response.content if b.type == "text")) if response.stop_reason == "end_turn" else {}
        out = result.get("translations") or []
        if len(out) != len(batch):
            # Misaligned, so none can be trusted; the next run tries again.
            continue
        checks = [check_text(en, tr) for en, tr in zip(batch, out)]
        # The AI reviews the ones that pass, which also catches a batch shifted by one.
        problems, review_cost = review_meaning(client, config, lang, [
            (str(i), en, tr) for i, (en, tr, c) in enumerate(zip(batch, out, checks)) if c == "ok"], cost)
        spent += review_cost
        saved["batches"][-1]["cost"] = round(paid + review_cost, 6)
        for i, (en, tr, checked) in enumerate(zip(batch, out, checks)):
            if checked == "ok" and (UNREVIEWED in problems or str(i) in problems):
                checked = f"review: {problems.get(str(i)) or problems[UNREVIEWED]}"
            before = saved["drafts"].get(en, {})
            attempts = before.get("attempts", 1) + 1 if before.get("prompt_version") == NAMES_VERSION else 1
            saved["drafts"][en] = {"text": tr, "check": checked, "model": tsettings["model"],
                                   "prompt_version": NAMES_VERSION, "attempts": attempts, "generated_at": at}
            done += 1
    if done or saved["batches"]:
        file = strings_path(data_dir, lang)
        saved["drafts"] = dict(sorted(saved["drafts"].items()))
        write_atomic(file, json.dumps(saved, indent=1, ensure_ascii=False) + "\n")
    return done, spent


def toml_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def review(config: dict, data_dir: Path, lang: str) -> str:
    """The town's drafts as [strings.<language>] lines, to check, correct, and add to its config
    (where they then win over the drafts). Drafts that failed their check are listed, commented out."""
    own = config.get("strings", {}).get(lang, {})
    lines = [f"# Machine drafts for [strings.{lang}], not yet reviewed. Correct any, then add them to the",
             f"# town's config; a text in [strings.{lang}] wins over its draft."]
    for en, d in saved_drafts(data_dir, lang)["drafts"].items():
        if en in own:
            continue
        line = f"{toml_string(en)} = {toml_string(d['text'])}"
        lines.append(line if d.get("check") == "ok" else f"# {line}  # {d.get('check')}")
    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse
    from pipeline.config import DATA_DIR, DEFAULT_TOWN, load_config
    parser = argparse.ArgumentParser(description="A town's machine-drafted text, to review")
    parser.add_argument("command", choices=["drafts"])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--language", default="es", choices=sorted(LANGUAGE_NAMES))
    args = parser.parse_args()
    print(review(load_config(args.town), args.data, args.language), end="")


if __name__ == "__main__":
    main()
