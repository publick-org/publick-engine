"""Summaries in the site's other languages, translated from the English summary.

A town with Spanish pages ([site] languages) gets each English summary of an
agenda or minutes in Spanish too: translated from the English summary (never
from the PDF again) by Claude Sonnet 5.5 at low effort, unless [summaries]
translation_model says otherwise. (Claude Haiku 4.5 did it until version 4,
and more of its translations failed the checks than passed them, so it cost
more per translation shown.) The rules are the summaries' own: only what
the summary says, neutral, plain, with names, addresses, amounts, dates, and
vote counts kept.

The translator is given the town's glossary (pipeline/glossary.py): each civic
term its state and the network define, as the language writes it ("warrant
article" = "artículo de la convocatoria (warrant article)"), in its cached
system prompt, after the words every translation uses (READERS).

No person checks the translations, so two checks do. One without AI, entry by
entry: each list (agenda items, decisions) has as many entries, every number
is kept and none added (however Spanish writes it), amounts keep their million
or billion, times their a.m. or p.m., names are kept as written (a word of the
glossary is no one's name), what happened isn't turned round (a "not" lost,
approved as denied, tabled as approved, unanimous changed), and each glossary
term the English uses is written as the glossary gives it. Then, for a translation that passes, a second
request (Claude Sonnet 5.5 unless [summaries] translation_review_model says
otherwise) reviews its meaning against the English, knowing the translator's
rules and words: adjourned shown as dissolved, a guessed gender, a
mistranslated board. A translation that fails either is made again once
(ATTEMPTS), as a correction: the model gets its first translation and what
was wrong with it. A translation that passes the check but has no review
(it couldn't be made, an API error, or the check failed when it was made
and passes now) has its review asked again by itself, the translation kept,
up to REVIEW_RETRIES times; one the review then flags is made again as any
other. Then it's kept, so it isn't paid for again, but not shown:
the page shows the English summary, and says so.

Each translation is its own record, data/summaries/<language>/<document's
SHA-256>.json, holding the hash of the English it came from. Turning a
language on never regenerates an English summary. A translation made from
the English summary as it is now is shown, whichever version of this
module's prompt (VERSION) made it, as long as it passes the checks as they
are now: the one without AI, run again with the town's glossary as it is
now, and the AI's review, as saved. It's made again only when its English
summary changes, when it fails those checks (a glossary change it no longer
writes as given, say), with ATTEMPTS of its own for each glossary, or when
it was made with a prompt older than REMAKE_BELOW, which a prompt change
raises on purpose when what the older prompts made is known to be wrong in
a way the checks can't see. Raising VERSION alone makes nothing again; a
translation that failed for good with a prompt older than RETRY_BELOW, which
a prompt change raises when it fixes what made translations fail, gets its
ATTEMPTS again. While
a translation is made again, the one it replaces stays shown if it passed,
until a new one passes ("previous"). summarize.py runs the translations within the same budget as the
summaries, new documents first, and older documents' before the older English
summaries waiting, which cost about ten times as much; each request is in the
month's cost log as it's answered (pipeline/cost_log.py), and in the ledger as
translation_cost. Their system prompts are the same for
every request, so they're cached: within a run, each request after the first
reads its system prompt at a tenth of the price.

A town's own text (its config's tagline, section summaries, officials' seats)
and the names in its data (boards, 311 categories) come from the town's
[strings.<language>] when it has them, else from the engine's own Spanish for
what many towns share ("Planning Board", "Ward 3"). Whatever is still missing
is drafted here by the same small model, a batch per run: draft_texts(),
checked without AI (every number and placeholder kept) and reviewed by the
larger model, and saved in data/strings/<language>.json. A draft that passes
is shown, whichever version of the drafting prompt (NAMES_VERSION) made it,
so a new town or a new board needs no one's translation; one that fails
twice stays in English. As with the summaries, a draft is made again only
when it fails, or when it's older than NAMES_REMAKE_BELOW. `python -m pipeline.translate drafts` prints the
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

from pipeline import glossary
from pipeline.files import write_atomic

# The translation prompt's version, saved with each translation. Raise it when the prompt changes
# (tests/test_prompt_versions.py); that alone makes nothing again (current()).
VERSION = 7
# Translations made with a prompt version below this are made again, even those that pass the checks:
# raise it, with VERSION, only when what the older prompts made is known to be wrong in a way the checks
# can't catch. Each stays shown until its new translation passes.
REMAKE_BELOW = 0
# Translations that failed their checks with a prompt version below this get their ATTEMPTS again; those that
# pass stay. Raise it, with VERSION, when the prompt fixes what made translations fail: 6 gave the translator
# the Spanish of each board a summary names, where version 5 kept a board's English name, which the review
# fails as English left in a Spanish sentence; 7 gave the translator and the review the same words for a person
# whose gender the English doesn't give, where version 6 told the translator to write "Scott Houseman,
# presidente del comité", which the review fails as a guessed gender, and the words the review found wrong
# most often.
RETRY_BELOW = 7
DEFAULT_MODEL = "claude-sonnet-5-5"
# Claude Sonnet 5.5's prices, dollars per million tokens, for a town that doesn't give the model's own.
DEFAULT_PRICES = {"input_price": 2.0, "output_price": 10.0}
# Translating a short summary needs little thinking.
DEFAULT_EFFORT = "low"
# Room for a model's thinking at a high effort as well as the translation; only what's written is paid for.
MAX_TOKENS = 16000

# The fields of each kind of summary that are shown, and so translated.
FIELDS = {"agenda": ("headline", "summary", "items"), "minutes": ("headline", "summary", "decisions"),
          "recording": ("headline", "summary", "decisions", "discussion")}

LANGUAGE_NAMES = {"es": "Spanish"}
# Each language's readers, and the words the sites use for what summaries talk about
# (site/strings/es-guide.md has the full glossary).
READERS = {"es": """Write plain Spanish as residents of a New England city or town read it every day, most of them Puerto Rican, Dominican, or from elsewhere in Latin America: natural, not formal, not word for word, and not Spain's Spanish. Address no one directly.
Use these words: meeting = reunión; minutes = actas; agenda = agenda; public hearing = audiencia pública; motion = moción; vote = votación; executive session = sesión ejecutiva; councilor = concejal; fiscal year = año fiscal; property tax = impuesto a la propiedad; budget = presupuesto; building permit = permiso de construcción; ward = distrito.
Words that are easy to get wrong: adjourn = levantar la sesión (never "disolver"); appoint = nombrar, reappoint = volver a nombrar (never "reelegir": an appointment isn't an election); elect = elegir; table a motion = posponer; tabling at an event, and a Tabling Policy = poner mesas informativas, política sobre mesas informativas (not "posponer"); sign (on a building or road) = letrero (a "señal" is a traffic sign); name a street after someone = ponerle a una calle el nombre de alguien; an all-alcoholic beverages license = licencia para todo tipo de bebidas alcohólicas; an underage operative (a minor sent into a business in a compliance check) = un menor que colabora con la policía; an Enforcement Order = orden de cumplimiento (never "orden de ejecución"); a personnel matter = un asunto de personal, but a personal matter = un asunto personal; redevelopment = reurbanización (not "nuevo desarrollo"); a fire department's Private = bombero (not "soldado raso"); withdrawn without prejudice = retirada con la opción de volver a presentarla. Abatement is a tax abatement only when it's about a tax: lead or asbestos abatement = eliminación de plomo, de asbesto; opioid abatement funds = fondos para combatir la crisis de opioides.
People whose gender the English doesn't give ("Chair Houseman", "the Treasurer", "Mayor Cahill", "Director Bagley", with no he, she, Mr., Ms., or Mrs.): never guess it from a name, and in a sentence write no word that gives one: not "el presidente", "la presidenta", "nuevo Presidente", "del Alcalde", "el director", "tesorero", "secretario", or "fue elegido". Write the office, or what the person does, with the article the sentence needs:
- the Chair = la presidencia ("the Chair's report" = "el informe de la presidencia", "the Chair will sign" = "la presidencia firmará"); elected Chair = "se eligió a Susan Faloon para presidir el comité"; a new Chair and Vice Chair = "nueva presidencia y vicepresidencia";
- the Mayor = la alcaldía ("the Mayor's budget" = "el presupuesto de la alcaldía"); Mayor Cahill = "Cahill, titular de la alcaldía";
- a director, treasurer, clerk, or other official = the office, when the office acts or reports ("the Director's report" = "el informe de la dirección"), or the name, then "a cargo de" and the office ("Treasurer Mulholland" = "Mulholland, a cargo de la tesorería"; "Economic Development Director Jodie Nazaka" = "Jodie Nazaka, a cargo de Desarrollo Económico"); named to a role = "se nombró a Joe Smith para la subdirección de Operaciones".
Words that are the same for a man or a woman stay as they are: concejal ("Councilor Martel" = "Martel, concejal"), integrante, representante, gerente, agente, asistente. Where the English gives a gender, use it ("Mr. Hall" = "el Sr. Hall"). A role's label on its own, as a list of a board's seats shows it, keeps its generic form ("Presidente").
Write every date with its month's name ("22 de octubre de 2026", "17 de octubre"), never in figures like 10/17 or 6-2-26, which a Spanish reader takes as 10 July and 6 February. Write a fiscal or school year out: FY27 = año fiscal 2027, SY26-27 = año escolar 2026-27, never "FY" or "SY" in a Spanish sentence. Times as "7:00 p. m.", and numbers and money as the English does ("$1,500", "$3 millones", "4.5%")."""}

SYSTEM = """You translate short summaries of a city government's public meeting agendas and minutes from English into {language}, for residents.

Rules:
- Translate only what the English says. Add nothing, leave nothing out, and don't explain.
- Keep the neutral tone. No words that judge.
- Short sentences, about an 8th-grade reading level.
- Keep every name of a person, business, street, address, and place exactly as written, and keep every number: dollar amounts, dates, times, vote counts ("5-0"), and case, application, and order numbers.
- Board and committee names: use the translations given below. A board with none given: its natural {language} name a resident would understand ("Planning Board" = "Junta de Planificación"), never its English name.
- Never guess anyone's gender: write people as the words below say, so a person whose gender the English doesn't give has no word that gives one. Never leave out an article Spanish needs: not "Director presentó datos" or "pidió una opinión a Abogado de la Ciudad".
- Name a board as the English does: "the board" is "la junta", "the commission" "la comisión", without adding which one. Add no name, title, or detail the English doesn't give, even one you can tell from the meeting.
- Every word in Spanish except names: no English words left in a Spanish sentence.
- Return the same fields, and each list with as many entries, in the same order.

{readers}{glossary}"""

# The town's civic terms (pipeline/glossary.py), after the words every translation uses; nothing for a town
# whose glossary has none in the language.
GLOSSARY = """

Civic terms. Wherever the English uses one of these, in the singular or the plural, write it in {language} as given here, in the singular or the plural as the sentence needs. Where the {language} has the English after it in parentheses, there is no {language} word for it: write the English in parentheses the first time the term comes up in a text; after that, the {language} alone.
{terms}"""


def glossary_text(terms: list[dict], lang: str) -> str:
    """The prompt's civic terms: each term of the town's glossary (glossary.entries()) that has a wording in the
    language, "English = wording", alphabetically. Entries for some boards only are left out: the prompt is the
    same for every board."""
    lines = sorted({f"- {glossary.english(e)} = {glossary.wording(e, lang)}" for e in terms
                    if glossary.wording(e, lang) and not e.get("bodies")}, key=str.lower)
    return GLOSSARY.format(language=LANGUAGE_NAMES[lang], terms="\n".join(lines)) if lines else ""


def glossary_key(terms: list[dict], lang: str) -> str:
    """Which glossary a translation was made with: a hash of its prompt's civic terms."""
    return hashlib.sha256(glossary_text(terms, lang).encode()).hexdigest()[:16]


def glossary_words(terms: list[dict]) -> frozenset:
    """The words of the glossary's English (each term, the other ways it's written, its definition), in
    lowercase: words of local government, so no one's name ("Selectboard", "Councillor", "abatement")."""
    words: set[str] = set()
    for e in terms:
        column = e.get("languages", {}).get("en", {})
        for text in (glossary.english(e), *column.get("also", []), e.get("definition", "")):
            words.update(w.lower() for w in re.findall(r"[A-Za-z]+", text))
    return frozenset(words)


def terms_kept(en: str, tr: str, lang: str, terms: list[dict]) -> str | None:
    """A glossary term the English uses that the translation doesn't write as given, if any."""
    for e in terms:
        if e.get("bodies") or not glossary.wording(e, lang):
            continue
        if glossary.found(e, en) and not glossary.found(e, tr, lang):
            return f'"{glossary.english(e)}" isn\'t written "{glossary.wording(e, lang)}"'
    return None


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


# Lists of a summary: translated entry by entry.
LISTS = ("items", "decisions", "discussion", "decision_titles")


def fields(kind: str, record: dict | None = None) -> tuple:
    """The parts of a summary of this kind that are translated: FIELDS, and its decisions' titles where it has
    them (summarize.DECISION_LABELS), so a summary made before them is translated as it was."""
    return FIELDS[kind] + (("decision_titles",) if kind != "agenda" and (record or {}).get("decision_titles") else ())


def english(record: dict, kind: str) -> dict:
    """The parts of an English summary that are shown, and so translated."""
    return {field: record.get(field) or ([] if field in LISTS else "") for field in fields(kind, record)}


def source_hash(record: dict, kind: str) -> str:
    """Which English a translation was made from."""
    return hashlib.sha256(json.dumps(english(record, kind), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def saved(data_dir: Path, lang: str, sha256: str) -> dict | None:
    file = path(data_dir, lang, sha256)
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None


# A translation that fails its checks is made again once, then left (and the English shown, or the
# translation it replaced, if that passes).
ATTEMPTS = 2
# A translation with no review (UNREVIEWED) that passes the check has its review asked again by itself up to
# this many times, before it's made again as any translation that fails (review_due()).
REVIEW_RETRIES = 2


def made_from(found: dict | None, record: dict, kind: str) -> bool:
    """Whether a saved translation was made from this English summary as it is now."""
    return bool(found) and found.get("source_hash") == source_hash(record, kind)


def retry_due(found: dict) -> bool:
    """Whether a saved translation's failed tries don't count: made with a prompt older than RETRY_BELOW."""
    return (found.get("prompt_version") or 0) < RETRY_BELOW


def to_remake(found: dict) -> bool:
    """Whether a saved translation is made again whether or not it passes: its prompt is older than
    REMAKE_BELOW."""
    return (found.get("prompt_version") or 0) < REMAKE_BELOW


def current(data_dir: Path, lang: str, sha256: str, record: dict, kind: str, terms: list[dict] = ()) -> dict | None:
    """The saved translation of this English summary when there's no need to make it again,
    whichever prompt version made it (unless it's older than REMAKE_BELOW): it passes the checks as
    they are now, or it's had its ATTEMPTS with this glossary (terms: the town's, glossary.entries()) and a
    prompt from RETRY_BELOW on. One that fails now only because the glossary changed, or that failed with
    an older prompt, is made again, with tries of its own."""
    found = saved(data_dir, lang, sha256)
    if review_due(data_dir, found, record, kind, lang, terms):
        return None
    if made_from(found, record, kind) and not to_remake(found) and (
            passes(data_dir, found, record, kind, lang, terms) or (
                found.get("attempts", 1) >= ATTEMPTS and found.get("glossary", "") == glossary_key(terms, lang)
                and not retry_due(found))):
        return found
    return None


def passes(data_dir: Path, found: dict, record: dict, kind: str, lang: str, terms: list[dict] = ()) -> bool:
    """Whether a saved translation passes both checks: the one without AI, run again here with the
    town's glossary as it is now (so a translation the check once wrongly failed is shown once the
    check is fixed, without paying for it again), and the AI's review of its meaning, as saved."""
    return (found.get("review") == "ok"
            and check(english(record, kind), found, kind, lang, town_words(data_dir), terms) == "ok")


def review_due(data_dir: Path, found: dict | None, record: dict, kind: str, lang: str, terms: list[dict] = ()) -> bool:
    """Whether a saved translation waits only for its review: made from this English, not one to make again
    (REMAKE_BELOW), it passes the check without AI as it is now, it has no review (UNREVIEWED: one couldn't be
    made, or the check failed when it was made), and it has review-only tries left (REVIEW_RETRIES). Its tries
    (ATTEMPTS) don't count these: no translation is made."""
    return (made_from(found, record, kind) and not to_remake(found)
            and found.get("review", "").startswith(UNREVIEWED)
            and found.get("review_retries", 0) < REVIEW_RETRIES
            and check(english(record, kind), found, kind, lang, town_words(data_dir), terms) == "ok")


def shown(data_dir: Path, lang: str, sha256: str, record: dict, kind: str, terms: list[dict] = ()) -> dict | None:
    """The translation to show for an English summary: one made from this English that passes both
    checks as they are now, whichever prompt version made it. The latest one made, or, while that
    one fails, the one it replaced ("previous"), if that passes."""
    found = saved(data_dir, lang, sha256)
    for candidate in (found, (found or {}).get("previous")):
        if made_from(candidate, record, kind) and passes(data_dir, candidate, record, kind, lang, terms):
            return candidate
    return None


# What a translation that's replaced keeps, as "previous", to be shown until its replacement passes.
PREVIOUS = ("source_hash", "check", "review", "model", "prompt_version", "glossary", "generated_at")


def previous_of(found: dict, kind: str) -> dict:
    return {key: found[key] for key in (*FIELDS[kind], "decision_titles", *PREVIOUS) if key in found}


def what_failed(data_dir: Path, found: dict, record: dict, kind: str, lang: str, terms: list[dict] = ()) -> str | None:
    """What was wrong with a saved translation, for the model to correct: the check's finding,
    else the review's. None when it passed, or when the review couldn't be made (nothing to correct)."""
    checked = check(english(record, kind), found, kind, lang, town_words(data_dir), terms)
    if checked != "ok":
        return checked
    review = found.get("review", "")
    return None if review == "ok" or review.startswith(UNREVIEWED) else review


def failed(data_dir: Path, lang: str, sha256: str, record: dict, kind: str, terms: list[dict] = ()) -> bool:
    """Whether this English summary's translation was made and failed its checks for good, with no
    translation to show."""
    return bool(current(data_dir, lang, sha256, record, kind, terms)) and not shown(data_dir, lang, sha256, record, kind, terms)


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


# A number with its scale: "$3 million", "3 millones", "$1.2 billion", "1,2 mil millones". A letter that begins an
# abbreviation isn't one: "1 M.R.S.A. § 405" is title 1 of Maine's statutes.
SCALES = {"en": [(r"billion|bn\b|B\b(?!\.\w)", "B"), (r"million|M\b(?!\.\w)", "M"), (r"thousand|K\b(?!\.\w)", "K")],
          "es": [(r"mil\s+millones|millardos?|B\b(?!\.\w)", "B"), (r"mill[oó]n(?:es)?|M\b(?!\.\w)", "M"),
                 (r"mil\b|K\b(?!\.\w)", "K")]}


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
NEWER_WORDS = {"campground", "coordinator", "cosmetology", "daycare", "defibrillator", "districtwide", "dropout", "epoxy",
               "forecourt", "geothermal", "healthcare", "microenterprise", "onboarding", "passbook", "pickleball", "preschool",
               "riverfront", "roundtable", "sitcom", "takeback", "taser", "telematic", "victualler", "wastewater", "waterfront",
               "wellness", "workforce", "yeah"}
# Days and months, and the days as a schedule shortens them ("Mon-Fri 8am-6pm").
WEEKDAYS_MONTHS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
                   "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
                   "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun"}


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
    for field in ("headline", "summary", "items", "decisions", "discussion"):
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
                                                  ("ing", ""), ("ing", "e"), ("ary", ""), ("ism", ""), ("al", ""),
                                                  # "Revitalization", "Generalist", "Workmen".
                                                  ("ization", "ize"), ("ist", ""), ("men", "man"))
             if w.endswith(end)]
    # "Sustainability", "accessibility".
    stems += [w[:-len(end)] + add for end, add in (("ability", "able"), ("ibility", "ible")) if w.endswith(end)]
    # Doubled before an ending: "planning", "referred".
    stems += [stem[:-1] for stem in stems if len(stem) > 2 and stem[-1] == stem[-2]]
    if any(stem in known or stem in also or stem in NEWER_WORDS for stem in stems):
        return True
    # "Unaudited", "unexpended": "un" before a word of five letters or more; "Reconfirmation", "Substation":
    # "re" or "sub" before one of six or more ("Resnick" isn't "snick" again).
    return any(w.startswith(prefix) and len(w) - len(prefix) >= least and is_word(w[len(prefix):], also)
               for prefix, least in (("un", 5), ("re", 6), ("sub", 6)))


# Abbreviations, with their period, that a translation may write out ("Ch." as "Cap." or "capítulo"):
# words, not names. Business suffixes (Inc., Corp.) aren't here: a business's name is kept.
ABBREVIATIONS = {"Art", "Ave", "Blvd", "Capt", "Ch", "Chap", "Clr", "Col", "Dir", "Est", "Ext", "Lieut", "Lt", "No", "Rd", "Req",
                 "Sec", "Secs", "Sgt", "St", "Vol"}
# Those that are never anyone's name, with or without a period ("Asst City Clerk").
ALWAYS_ABBREVIATIONS = {"Admin", "Approx", "Asst", "Dept", "Govt", "Mgr", "Misc", "Rec", "Supt", "Tem", "Temp",
                        "Apr", "Aug", "Dec", "Feb", "Jul", "Jun", "Nov", "Oct"}
# A name as a summary writes it: a capitalized word ("Houseman"), one with capitals inside ("McCallum",
# "LeLacheur", "DiStefano", "O'Brien"; not an acronym's plural, "CDs"), one in capitals after a title ("MR.
# CARUSO", "ALDERMAN O’BRIEN"; on its own, a word in capitals is an acronym, which a translation may write
# out: "ZBA", "HVAC"), or two or more of them joined by hyphens ("Sanchez-Parkinson").
INNER_CAPITALS = r"(?:[A-Z][a-z]+[A-Z][a-z]{2,}|[A-Z]['’][A-Z][a-z]{2,})(?:[A-Z][a-z]{2,})*"
NAME_PART = rf"(?:{INNER_CAPITALS}|[A-Z][a-z]+|[A-Z]+['’][A-Z]{{2,}}|[A-Z]{{3,}})"
# A name in quotes ("'Lowell Garden of Peace'") is one, but not the rest of "O'Brien", "D'Angelo", or "Swap'N'Drop".
NAME = re.compile(rf"(?<![\w-])(?<![A-Za-z]['’])({NAME_PART}(?:-{NAME_PART})*)(?![\w-])(\.?)")
# What makes the capitalized word after it a name, ordinary word or not: a title ("Councilor Hall", "Mayor
# Young", "Dr. Wood"), or "moved by" ("seconded by Rose").
TITLE_WORDS = r"Councill?ors?|Alderm[ae]n|Alderwom[ae]n|Mayor|Chair(?:man|woman|person)?|Commissioners?"
TITLED = re.compile(rf"\b(?:{TITLE_WORDS}|(?:Mrs?|Ms|Dr|Rep|Sen)\.|(?:moved|seconded|motion(?:ed)?(?:\s+made)?)\s+by)\s+$", re.I)
# Words after a title that aren't a name: another title ("seconded by Alderman Barry"), "Mayor Pro Tem",
# "Councilor At-Large", "Mr. President", and an agenda's headings ("Chair Report", "Commissioner Items",
# "Commissioner Stipend").
NOT_TITLED = re.compile(rf"(?:{TITLE_WORDS}|Mrs?|Ms|Dr|Rep|Sen|Chief|Vice|President|Pro|Elect|Emeritus|At|Large|The|And|Of|For|Report|Reports|"
                        r"Announcements|Items|Roles|Comments|Remarks|Update|Updates|Amendment|Amendments|Stipend|Stipends)", re.I)


def names(text: str, also: frozenset = frozenset()) -> set[str]:
    """The names in an English text (NAME), which a translation keeps as written: capitalized words
    that aren't ordinary English words ("Houseman" is one, "Collector" isn't), with capitals inside
    ("McCallum", "O'Brien"), and any capitalized word after a title or "moved by" ("Councilor Hall",
    "seconded by Wood", "MR. CARUSO"); hyphenated, when a part that isn't an acronym is one ("LRA-Owned"
    isn't). also: more ordinary words (town_words()). An abbreviation in ABBREVIATIONS with its period, or
    in ALWAYS_ABBREVIATIONS, isn't one."""
    found = set()
    for m in NAME.finditer(text or ""):
        word, dot = m.group(1), m.group(2)
        # A title ("Councillor", "Aldermen") is no one's name, wherever it is in the sentence.
        if (dot and word in ABBREVIATIONS) or word in ALWAYS_ABBREVIATIONS or re.fullmatch(TITLE_WORDS, word, re.I):
            continue
        if NOT_TITLED.fullmatch(word) is None and TITLED.search(text[max(0, m.start() - 30):m.start()]):
            found.add(word)
        elif word != word.upper() and any(
                not is_word(re.sub(r"['’]", "", part), also) and part != part.upper() for part in word.split("-")):
            found.add(word)
    return found


def kept(name: str, text: str) -> bool:
    """Whether a name is in a text as written, whichever apostrophe it's written with, or all in lowercase
    when only its first letter is a capital: Spanish writes some so ("Ebola" is "ébola"), but "McCallum"
    is never "Mccallum"."""
    name, text = name.replace("’", "'"), text.replace("’", "'")
    forms = [name] + ([name.lower()] if name[1:] == name[1:].lower() else [])
    return any(re.search(rf"(?<![\w-])(?<!\w'){re.escape(form)}(?![\w-])", text) for form in forms)


def plain(text: str) -> str:
    """Text without its accents, so "América" keeps "America"."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


# What a decision says happened, in each language, to catch a translation that turns it round.
# "No" in English: not, never, and what Spanish writes with a "no" or a "sin": "without permits" and "with no
# conditions" ("sin permiso", "sin condiciones"), and a "no" with a decision ("took no vote", "no option adopted":
# "no se votó", "no se adoptó"), but not "voting no" ("en contra") or "no parking" ("prohibido estacionar").
NOT = {"en": re.compile(r"\b(?:not|never|without|with\s+no)\b|n't\b|"
                        r"\bno\s+(?:[\w-]+\s+){0,2}?(?:votes?|action|decisions?|motions?)\b|"
                        r"\bno\s+(?:[\w-]+\s+){1,5}?(?:(?:was|were)\s+)?(?:adopted|approved|voted|taken|made|discussed)\b",
                        re.I),
       "es": re.compile(r"\b(?:no|nunca|ni|sin)\b", re.I)}
SAYS_NO = {"en": re.compile(r"\b(?:not|never|no|none|nothing|without|den(?:y|ied|ies|ial|ying)|reject\w*|fail\w*|defeat\w*|"
                            r"disapprov\w*|declin\w*|against|oppos\w*|vot\w*\s+down)\b|n't\b", re.I)}
# Approved: "granted" a permit, not a "grant" of money.
APPROVE = {"en": re.compile(r"\b(?:approv\w*|adopt\w*|grant(?:ed|ing)|pass(?:ed|es)?|carried|endors\w*|ratif\w*)\b", re.I),
           "es": re.compile(r"\b(?:aprob\w*|aprueb\w*|adopt\w*|otorg\w*|conced\w*|concedi\w*|ratific\w*)", re.I)}
# Denied, failed, or voted down ("votaron en contra de una moción", not a member's "voto en contra"); not "declines"
# in a count ("declines after adding a worker") or a "Negative Determination" ("Determinación Negativa"), but "a
# denial" ("una negativa").
DENY = {"en": re.compile(r"\b(?:den(?:y|ied|ies|ials?|ying)|reject\w*|fail(?:ed|s)?|defeat\w*|disapprov\w*|declin(?:e|ed|ing)|"
                        r"declines(?!\s+(?:in|after|of|since|from)\b)|vot(?:e|ed|es|ing)\s+down|"
                        r"turn(?:ed|s|ing)?\s+(?:away|down))\b", re.I),
        "es": re.compile(r"\b(?:neg(?:ó|aron|ada|adas|ado|ados|ar|ación)|nieg\w*|deneg\w*|rechaz\w*|desaprob\w*|"
                         r"fracas\w*|declin\w*|derrot\w*|vot(?:ó|aron|ar)\s+en\s+contra\s+de\b|(?<=una\s)negativa|"
                         r"(?<=la\s)negativa)", re.I)}
# Tabled, postponed, or deferred: a motion or a matter, not "tabling" at an event or "deferred maintenance".
TABLED = {"en": re.compile(r"\b(?:tabled|tabling\s+(?:of|the|it|a|an|all)|tables\s+(?:the|a|an|all|(?-i:[A-Z]))|to\s+table|"
                           r"postpon\w*|deferr(?:ed|ing)\s+(?:the|a|an|it|all|action|consideration|until|to)|to\s+defer)\b",
                           re.I),
          "es": re.compile(r"\b(?:posterg\w*|aplaz\w*|pospu\w*|pospon\w*|posposici\w*|archiv\w*|tabled\b|suspend\w*|"
                           r"sobre la mesa|difiri\w*)", re.I)}
# "No" in a translation, but not as a prefix ("no conforme" is nonconforming, "no pública" non-public), "sin" ("not to
# exceed": "sin exceder"), and "por falta de quórum" ("without a quorum").
NOES = {"es": re.compile(r"\bsin\b|\b(?:no|nunca)\b(?!\s+(?:conform|residencial|lucrativ|profesional|elegible|permitid|esencial|"
                         r"emergencia|vinculante|aplicable|incluid|deseable|existente|asignad|binari|reembolsable|exent|"
                         r"tradicional|oficial|relacionad|municipal|pagad|públic|public|resident|sindicaliz))|"
                         r"\bpor\s+falta\s+de\s+qu[oó]rum\b", re.I)}
# "No" with a decision's verb: "no se aprobó", "no fue aprobada", "no recomendó".
NO_DECISION = {"es": re.compile(r"\bno\s+(?:se\s+)?(?:(?:fue|fueron)\s+)?(?:aprob|aprueb|adopt|otorg|conced|pas|acept|recomend|vot)",
                                re.I)}
UNANIMOUS = {"en": re.compile(r"\bunanim\w*", re.I), "es": re.compile(r"\bun[aá]nim\w*|\bsin oposici[oó]n", re.I)}


def outcome(en: str, tr: str, lang: str) -> str | None:
    """What a translation got wrong about what happened, if anything: a "not" dropped or added,
    approved turned into denied or back, tabled lost, or unanimous changed."""
    # As many noes (not, without, denied, failed) in each, so one can't stand in for another.
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
    figures (month and day in either order) or written out ("23 de septiembre de 2026"), or both, one
    after the other in parentheses ("3 de septiembre de 2026 (9/3/2026)")."""
    for m in DATE.finditer(en):
        month, day, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        full_year = year if not year or len(year) == 4 else "20" + year
        in_figures = rf"\b0?(?:{month}/0?{day}|{day}/0?{month})" + (rf"/(?:{full_year}|{full_year[2:]})" if year else "") + r"\b"
        forms = [in_figures]
        if lang in MONTHS:
            forms.append(rf"\b0?{day}\s+de\s+(?:{MONTHS[lang][month - 1]})" + (rf"\s+del?\s+{full_year}" if year else ""))
        either = "|".join(f"(?:{f})" for f in forms)
        found = (re.search(rf"(?:{either})\s*\(\s*(?:{either})\s*\)", tr, re.I) or re.search(in_figures, tr)
                 or (re.search(forms[1], tr, re.I) if len(forms) > 1 else None))
        if found:
            en = en.replace(m.group(0), " ", 1)
            tr = tr[:found.start()] + " " + tr[found.end():]
    return en, tr


# The English's ways of writing what a translation writes out, made the same before comparing:
# "FY27", "fiscal year 27", and "SY26" are "fiscal year 2027" ("año fiscal 2027", or "27") and "school year
# 2026" ("año escolar 26"); "August '26" is August 2026; "Q2" and "Quarter Two" are the second quarter
# ("segundo trimestre"), its number; "not to exceed" is a cap, not a "not"; "9:00-11:00 A.M." is two morning
# times; "1.28.26" and "1-28-26" are dates in figures.
FISCAL_YEAR = re.compile(r"\bFY\s?'?(\d{2})\b")
FISCAL_YEAR_WORDS = re.compile(r"\b(fiscal\s+years?)\s+'?(\d{2})\b", re.I)
SCHOOL_YEAR = re.compile(r"\bSY\s?'?(\d{2})\b")
MONTH_YEAR = re.compile(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)"
                        r"|(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?)\s+'(\d{2})\b")
QUARTER = re.compile(r"\bQ([1-4])\b")
QUARTER_WORDS = re.compile(r"\b(?:(first|second|third|fourth)[\s-]+quarter|quarter\s+(one|two|three|four))\b", re.I)
QUARTER_NUMBERS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "one": 1, "two": 2, "three": 3, "four": 4}
# A quarter in Spanish, "segundo trimestre", or one of several, "del tercer y cuarto trimestre".
TRIMESTRES = {"primer": 1, "segundo": 2, "tercer": 3, "cuarto": 4}
QUARTERS = re.compile(r"\b(primer|segundo|tercer|cuarto)o?\b(?=(?:\s*(?:,|y|e|o)\s*(?:primer|segundo|tercer|cuarto)o?\b)*"
                      r"\s+trimestres?\b)", re.I)
NOT_TO_EXCEED = re.compile(r"\bnot\s+to\s+exceed\b", re.I)
TIME_RANGE = re.compile(r"\b(\d{1,2}(?::\d{2})?)\s*[-–]\s*(\d{1,2}(?::\d{2})?)\s*([ap])\.?\s?m\b\.?", re.I)
DOTTED_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b")
DASHED_DATE = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4}|\d{2})\b")
# A fiscal year the translation keeps in English after it's written out: "año fiscal 2027 (FY27)", which the
# English may write too ("Fiscal Year 2027 (FY27)").
FISCAL_YEAR_REPEATED = re.compile(r"\s*\(FY\s?'?\d{2,4}\)")


def comparable(en: str, tr: str) -> tuple[str, str]:
    """The English and its translation, each written as the other would write it (above)."""
    en = FISCAL_YEAR_REPEATED.sub("", en)
    en = FISCAL_YEAR.sub(r"FY 20\1", en)
    en = FISCAL_YEAR_WORDS.sub(r"\1 20\2", en)
    en = SCHOOL_YEAR.sub(r"SY 20\1", en)
    en = MONTH_YEAR.sub(r"\1 20\2", en)
    en = QUARTER.sub(r"quarter \1", en)
    en = QUARTER_WORDS.sub(lambda m: f"quarter {QUARTER_NUMBERS[(m.group(1) or m.group(2)).lower()]}", en)
    en = NOT_TO_EXCEED.sub("up to", en)
    en = TIME_RANGE.sub(lambda m: f"{m.group(1)} {m.group(3)}.m.-{m.group(2)} {m.group(3)}.m.", en)
    tr = FISCAL_YEAR_REPEATED.sub("", tr)
    tr = FISCAL_YEAR.sub(r"FY 20\1", tr)
    tr = SCHOOL_YEAR.sub(r"SY 20\1", tr)
    tr = re.sub(r"\b(años?\s+(?:fiscal(?:es)?|escolar(?:es)?))\s+'?(\d{2})\b", r"\1 20\2", tr, flags=re.I)
    tr = QUARTERS.sub(lambda m: str(TRIMESTRES[m.group(1).lower()]), tr)
    en, tr = (DASHED_DATE.sub(r"\1/\2/\3", DOTTED_DATE.sub(r"\1/\2/\3", text)) for text in (en, tr))
    return en, tr


def without_repeats(tr: str, en: str) -> str:
    """A translation without what it gives again in parentheses, as the English writes it: the parentheses
    whose numbers are all the English's ("capítulos 17 y 1 (Ch. 17 y Ch. 1)"), which add none."""
    have = set(numbers(en, ordinals=True))
    return re.sub(r"\([^()]*\d[^()]*\)", lambda m: "" if set(numbers(m.group(0))) <= have else m.group(0), tr)


def check(source: dict, translated: dict, kind: str, lang: str = "es", words: frozenset = frozenset(),
          terms: list[dict] = ()) -> str:
    """ "ok", or what's wrong, entry by entry, so a list put in another order fails too: a field
    missing or empty, a list of another length, a number lost or added (however the language
    writes it, and a date in figures may be written out), an amount's million or billion, a.m. or
    p.m., a name not kept as written (words: the town's own ordinary words, town_words(); the
    glossary's words are ordinary too), what happened turned round (outcome()), or a term of the
    town's glossary (terms: glossary.entries()) not written as it gives it (terms_kept())."""
    words = words | glossary_words(terms)
    for field in source:
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
            lost_names = sorted(n for n in names(a, words) if not kept(n, plain(b)))
            if lost_names:
                return f"{where}: {', '.join(lost_names)} not in the translation"
            wrong = outcome(a, b, lang)
            if wrong:
                return f"{where}: {wrong}"
            a, b = dates_kept(a, b, lang)
            lost = numbers(a) - numbers(b)
            if lost:
                return f"{where}: {', '.join(sorted(lost))} not in the translation"
            added = numbers(without_repeats(b, a)) - numbers(a, ordinals=True)
            if added:
                return f"{where}: {', '.join(sorted(added))} added in the translation"
            unkept = terms_kept(a, b, lang, terms)
            if unkept:
                return f"{where}: {unkept}"
    return "ok"


def cached(system: str) -> list[dict]:
    """A system prompt, cached: it's the same for every request of its kind, so the requests after the
    first within a few minutes read it from the cache at a tenth of the input price."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def usage_of(response) -> dict:
    """A response's tokens: input and output, and the cached input written and read, when there was any."""
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        if getattr(response.usage, key, None):
            usage[key] = getattr(response.usage, key)
    return usage


def out_of_credit(error: Exception) -> bool:
    """Whether a request failed because the API account has no credit left: every request after it
    would fail the same way, so a run stops sending them."""
    return "credit balance" in str(error).lower()


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


def town_names(config: dict, data_dir: Path, lang: str) -> dict[str, str]:
    """Names in the language as the town's site writes them, by the same rules (build_site.TownStrings):
    its config's [strings.<language>], else the engine's own for what many towns share
    (pipeline/common_strings.py), else a draft that passes (drafts())."""
    from pipeline import common_strings, i18n
    from pipeline.build_site import town_kind
    names = drafts(data_dir, lang)
    with i18n.use(lang, town_kind(config, data_dir)):
        names.update({text: i18n.gettext(text) for text in common_strings.TEXTS if i18n.translated(text)})
    names.update(config.get("strings", {}).get(lang, {}))
    return names


def board_names(names: dict[str, str], lang: str, board: str, source: dict) -> str:
    """For the prompt: the meeting's board in the language, and each other name of the town's (names:
    town_names()) the English summary has, as the site writes it, so a board isn't left in English."""
    text = json.dumps(source, ensure_ascii=False)
    lines = [f'The board is "{board}"; in {LANGUAGE_NAMES[lang]}, "{names[board]}".'] if board in names else []
    named = sorted((en for en in names if en != board and en != names[en]
                    and re.search(rf"(?<!\w){re.escape(en)}(?!\w)", text)), key=len, reverse=True)
    if named:
        lines.append(f"Names in this summary, as the town's site writes them in {LANGUAGE_NAMES[lang]}:")
        lines += [f'- "{en}" = "{names[en]}"' for en in named]
    return "".join(f"{line}\n" for line in lines)


def schema(kind: str, names: tuple | None = None) -> dict:
    """What the model returns: the summary's translated parts (names, or FIELDS)."""
    names = names or FIELDS[kind]
    props = {field: ({"type": "array", "items": {"type": "string"}} if field in LISTS else {"type": "string"})
             for field in names}
    return {"type": "object", "properties": props, "required": list(names), "additionalProperties": False}


def translate(client, config: dict, lang: str, kind: str, record: dict, meeting: dict,
              previous: dict | None = None, problems: str = "", on_response=None,
              names: dict[str, str] | None = None) -> tuple[dict, dict]:
    """The model's translation of one English summary, and the tokens it used. previous: an
    earlier translation that failed a check, to correct, and problems: what was wrong with it.
    on_response, if given, is called with the response and its tokens as soon as it's answered.
    names: the town's names in the language (town_names()); without them, its config's [strings]."""
    source = english(record, kind)
    names = config.get("strings", {}).get(lang, {}) if names is None else names
    prompt = PROMPT.format(kind=kind, title=meeting["title"], date=meeting["date"], language=LANGUAGE_NAMES[lang],
                           boards=board_names(names, lang, meeting["body"], source),
                           summary=json.dumps(source, ensure_ascii=False, indent=1))
    if previous:
        prompt += CORRECT.format(previous=json.dumps({f: previous.get(f) for f in source}, ensure_ascii=False, indent=1),
                                 problems=problems)
    response = client.messages.create(
        model=settings(config)["model"],
        max_tokens=MAX_TOKENS,
        system=cached(SYSTEM.format(language=LANGUAGE_NAMES[lang], readers=READERS[lang],
                                    glossary=glossary_text(glossary.entries(config), lang))),
        messages=[{"role": "user", "content": prompt}],
        output_config=output_config(config, schema(kind, tuple(source))),
    )
    usage = usage_of(response)
    if on_response:
        on_response(response, usage)
    if response.stop_reason != "end_turn":
        from pipeline.summarize import StoppedEarly, stop_reason
        raise StoppedEarly(stop_reason(response), usage)
    return json.loads(next(b.text for b in response.content if b.type == "text")), usage


def save(data_dir: Path, lang: str, sha256: str, record: dict) -> None:
    file = path(data_dir, lang, sha256)
    write_atomic(file, json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def make(client, config: dict, data_dir: Path, lang: str, kind: str, meeting: dict, doc: dict, record: dict,
         now: datetime, cost, log=None) -> tuple[dict, float]:
    """Translate one summary, check it, have the AI review its meaning if it passes, and save it.
    A second try corrects the first: the model gets it and what was wrong with it.
    Returns the saved record and what it cost. log: the month's cost log (pipeline/cost_log.py), which
    gets each request as it's answered: the translation (failed, if it stopped early or can't be read)
    and its review."""
    source = english(record, kind)
    terms = glossary.entries(config)
    key = glossary_key(terms, lang)
    before = saved(data_dir, lang, doc["sha256"])
    if review_due(data_dir, before, record, kind, lang, terms):
        return review_again(client, config, data_dir, lang, kind, doc, record, before, now, cost, log)
    # Made again because it failed (whichever prompt version made it): corrected, and counted as another try.
    again = made_from(before, record, kind) and not to_remake(before)
    wrong = what_failed(data_dir, before, record, kind, lang, terms) if again else None
    # A translation made again because the glossary changed, or after failing with a prompt older than
    # RETRY_BELOW, has its own tries.
    retry = again and before.get("glossary", "") == key and not retry_due(before)
    # What's shown now, which stays shown until a new translation passes.
    showing = shown(data_dir, lang, doc["sha256"], record, kind, terms)
    # A translation made again in the month the one before it was (a correction, a changed summary) replaces
    # its file, so what that one cost is carried in this one, for the files to still record it (summarize.recount).
    earlier = (before.get("cost", 0.0) + before.get("replaced_cost", 0.0)
               if before and before.get("generated_at", "")[:7] == now.strftime("%Y-%m") else 0.0)
    answered = []
    try:
        result, usage = translate(client, config, lang, kind, record, meeting, before if wrong else None, wrong or "",
                                  lambda response, usage: answered.append((response, usage)),
                                  town_names(config, data_dir, lang))
    except Exception:
        if answered and log:
            response, usage = answered[0]
            log.request(response, "failed", cost(usage, settings(config)), doc=doc["sha256"], lang=lang,
                        model=settings(config)["model"], usage=usage)
        raise
    paid = cost(usage, settings(config))
    if log:
        log.request(answered[0][0], "translation", paid, doc=doc["sha256"], lang=lang, model=settings(config)["model"],
                    usage=usage)
    checked = check(source, result, kind, lang, town_words(data_dir), terms)
    reviewed, review_cost = "not reviewed: the check failed", 0.0
    if checked == "ok":
        problems, review_cost = review_meaning(client, config, lang, [(f, source[f], result.get(f)) for f in source],
                                               cost, log, doc["sha256"])
        reviewed = "; ".join(f"{key}: {problem}" for key, problem in problems.items()) or "ok"
    out = {
        **{field: result.get(field) for field in source},
        "language": lang,
        "kind": kind,
        "source_sha256": doc["sha256"],
        "source_hash": source_hash(record, kind),
        "check": checked,
        "review": reviewed,
        "attempts": before.get("attempts", 1) + 1 if retry else 1,
        "model": settings(config)["model"],
        "prompt_version": VERSION,
        "glossary": key,
        "generated_at": now.isoformat(timespec="seconds"),
        "usage": usage,
        "cost": round(paid + review_cost, 6),
        **({"replaced_cost": round(earlier, 6)} if earlier else {}),
    }
    if showing and not (checked == "ok" and reviewed == "ok"):
        out["previous"] = previous_of(showing, kind)
    save(data_dir, lang, doc["sha256"], out)
    return out, paid + review_cost


def review_again(client, config: dict, data_dir: Path, lang: str, kind: str, doc: dict, record: dict, before: dict,
                 now: datetime, cost, log=None) -> tuple[dict, float]:
    """Ask for the review of a saved translation that has none (review_due()),
    keeping the translation: no new one is paid for. Saved with what the review found (ok, its problems,
    or why it couldn't be made, again), one more review_retries, and what it cost; a translation saved
    earlier in the same month carries what that one cost (replaced_cost), as make() does."""
    source = english(record, kind)
    problems, paid = review_meaning(client, config, lang, [(f, source[f], before.get(f)) for f in source],
                                    cost, log, doc["sha256"])
    earlier = (before.get("cost", 0.0) + before.get("replaced_cost", 0.0)
               if before.get("generated_at", "")[:7] == now.strftime("%Y-%m") else 0.0)
    out = {key: value for key, value in before.items() if key != "replaced_cost"}
    out.update({
        "review": "; ".join(f"{key}: {problem}" for key, problem in problems.items()) or "ok",
        "review_retries": before.get("review_retries", 0) + 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "cost": round(paid, 6),
        **({"replaced_cost": round(earlier, 6)} if earlier else {}),
    })
    save(data_dir, lang, doc["sha256"], out)
    return out, paid


# ---- The AI's review of a translation's meaning ----------------------------------

DEFAULT_REVIEW_MODEL = "claude-sonnet-5-5"
# Claude Sonnet 5.5's prices, dollars per million tokens.
DEFAULT_REVIEW_PRICES = {"input_price": 2.0, "output_price": 10.0}
# Reviewing a few short texts needs little thinking.
DEFAULT_REVIEW_EFFORT = "low"

REVIEW_SYSTEM = """You check translations from English into {language} of short texts from a US city or town government's website: summaries of meeting agendas and minutes, and names of boards, roles, and services. No person checks them after you, and residents rely on them.

For each English text and its translation, report every error that changes the meaning or would mislead a reader:
- what happened changed: adjourned shown as dissolved, reappointed shown as re-elected, appointed shown as elected, approved shown as denied or tabled, a "not" lost or added, unanimous changed, a recommendation shown as a decision;
- a person's gender stated where the English doesn't give it ("la presidenta" for "Chair Houseman");
- a name, place, street, business, amount, date, time, or number changed, or anything added or left out;
- a mistranslation ("Conservation Commission" as "Comisión de Conversación", "Mayor" as "Gobernador", a business sign as a traffic sign);
- English words left in a {language} sentence (names of people, places, businesses, and programs excepted), or words that don't exist.

Don't report style, or a different word choice that keeps the meaning. Report nothing for a faithful translation: if, once you've looked, a translation is right, leave it out, or mark it is_error false.

The translator was given these words and rules, so a translation that follows them isn't wrong for it:
{readers}{glossary}"""

REVIEW_PROMPT = """Check these translations. Each has an id, the English, and the {language}:
{pairs}"""


def review_settings(config: dict) -> dict:
    """The reviewing model, its prices, and its effort: [summaries] translation_review_model,
    translation_review_input_price, translation_review_output_price, and translation_review_effort,
    or Claude Sonnet 5.5's prices, and low effort for any model."""
    s = config.get("summaries", {})
    model = s.get("translation_review_model", DEFAULT_REVIEW_MODEL)
    prices = DEFAULT_REVIEW_PRICES if model == DEFAULT_REVIEW_MODEL else {}
    return {"model": model,
            "input_price": s.get("translation_review_input_price", prices.get("input_price")),
            "output_price": s.get("translation_review_output_price", prices.get("output_price")),
            "effort": s.get("translation_review_effort", DEFAULT_REVIEW_EFFORT)}


# The key a review's problem is under when nothing could be reviewed.
UNREVIEWED = "not reviewed"


def review_meaning(client, config: dict, lang: str, pairs: list[tuple[str, object, object]], cost, log=None,
                   doc: str | None = None) -> tuple[dict[str, str], float]:
    """The AI's review of translations' meaning. pairs: (id, English, translation), where a text
    may be a list of texts (its entries' ids are "<id> 1", "<id> 2", ...). Returns the problems
    found, {id: problem}, empty if none, and what the review cost. A review that can't be made
    returns {UNREVIEWED: why}, so nothing it didn't review is shown. log: the month's cost log, which
    gets the request as it's answered, with doc, the hash of the document whose translation it is."""
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
            system=cached(REVIEW_SYSTEM.format(language=LANGUAGE_NAMES[lang], readers=READERS[lang],
                                               glossary=glossary_text(glossary.entries(config), lang))),
            messages=[{"role": "user", "content": REVIEW_PROMPT.format(
                language=LANGUAGE_NAMES[lang], pairs=json.dumps(rows, ensure_ascii=False, indent=1))}],
            output_config={"effort": rsettings["effort"], "format": {"type": "json_schema", "schema": {
                "type": "object", "additionalProperties": False, "required": ["problems"],
                "properties": {"problems": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["id", "problem", "is_error"],
                    "properties": {"id": {"type": "string"}, "problem": {"type": "string"},
                                   "is_error": {"type": "boolean"}}}}}}}},
        )
    except Exception as e:
        if out_of_credit(e):
            raise
        return {UNREVIEWED: str(e)}, 0.0
    paid = cost(usage_of(response), rsettings)
    if log:
        log.request(response, "review", paid, doc=doc, lang=lang, model=rsettings["model"], usage=usage_of(response))
    if response.stop_reason != "end_turn":
        return {UNREVIEWED: f"stopped ({response.stop_reason})"}, paid
    problems: dict[str, str] = {}
    for p in json.loads(next(b.text for b in response.content if b.type == "text"))["problems"]:
        # An entry that, written out, found nothing wrong ("this is faithful") isn't a problem.
        if not p["is_error"]:
            continue
        problems[p["id"]] = f"{problems[p['id']]}; {p['problem']}" if p["id"] in problems else p["problem"]
    return problems, paid


def made_in(data_dir: Path, month: str) -> list[tuple[str, str | None, dict]]:
    """What was translated in a month, as the files record it: (language, document's hash, record) for
    each translation saved in it, and (language, None, batch) for each batch of drafts made in it."""
    out = []
    for file in sorted((data_dir / "summaries").glob("*/*.json")):
        record = json.loads(file.read_text(encoding="utf-8"))
        if record.get("generated_at", "")[:7] == month:
            out.append((file.parent.name, file.stem, record))
    for file in sorted((data_dir / "strings").glob("*.json")):
        out += [(file.stem, None, batch) for batch in json.loads(file.read_text(encoding="utf-8")).get("batches", [])
                if batch.get("generated_at", "")[:7] == month]
    return out


# ---- A town's own text and names, drafted ------------------------------------

# The drafting prompt's version, saved with each draft: raising it makes nothing again, as with VERSION.
NAMES_VERSION = 2
# Drafts made with a prompt version below this are made again, even those that pass: as REMAKE_BELOW.
NAMES_REMAKE_BELOW = 0
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

{readers}{glossary}"""

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


def draft_shown(en: str, d: dict) -> dict | None:
    """The draft of a text to show, whichever prompt version made it: the latest, or, while that one
    fails, the one it replaced ("previous"), if it passed its review and passes the check as it is now."""
    for candidate in (d, d.get("previous")):
        if candidate and candidate.get("check") == "ok" and check_text(en, candidate.get("text")) == "ok":
            return candidate
    return None


def draft_to_remake(d: dict) -> bool:
    """Whether a draft is made again whether or not it passes: its prompt is older than NAMES_REMAKE_BELOW."""
    return (d.get("prompt_version") or 0) < NAMES_REMAKE_BELOW


def drafts_to_remake(data_dir: Path, lang: str) -> list[str]:
    """The texts to draft again though a draft of them is shown: those older than NAMES_REMAKE_BELOW, and
    those whose new draft failed with tries left, the draft it replaced shown meanwhile."""
    return [en for en, d in saved_drafts(data_dir, lang)["drafts"].items() for shown in [draft_shown(en, d)]
            if shown and (draft_to_remake(d) or (shown is not d and d.get("attempts", 1) < ATTEMPTS))]


def drafts(data_dir: Path, lang: str) -> dict[str, str]:
    """The drafts that can be shown: {English: translation}, each passing the check (draft_shown())."""
    return {en: shown["text"] for en, d in saved_drafts(data_dir, lang)["drafts"].items()
            for shown in [draft_shown(en, d)] if shown}


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
                allowance: float | None = None, log=None) -> tuple[int, float]:
    """Draft the texts (a town's own text and names with no translation yet, and those to draft again:
    drafts_to_remake()), check each, and save them in data/strings/<language>.json.
    Stops before a batch once allowance (dollars) is spent. Returns how many were drafted and what
    they cost. log: the month's cost log, which gets each request, a batch's drafts and their review,
    as it's answered."""
    tsettings = settings(config)
    saved = saved_drafts(data_dir, lang)
    done, spent = 0, 0.0
    # A text whose drafts failed ATTEMPTS times is left in English, not paid for every run.
    texts = [t for t in dict.fromkeys(texts) if not (
        t in saved["drafts"] and not draft_to_remake(saved["drafts"][t]) and saved["drafts"][t].get("attempts", 1) >= ATTEMPTS)]
    for start in range(0, len(texts), NAMES_BATCH):
        if allowance is not None and spent >= allowance:
            break
        batch = texts[start:start + NAMES_BATCH]
        response = client.messages.create(
            model=tsettings["model"],
            max_tokens=MAX_TOKENS,
            system=cached(NAMES_SYSTEM.format(language=LANGUAGE_NAMES[lang], readers=READERS[lang],
                                              glossary=glossary_text(glossary.entries(config), lang))),
            messages=[{"role": "user", "content": NAMES_PROMPT.format(
                town=config["town"]["name"], state=config["town"]["state"], n=len(batch), language=LANGUAGE_NAMES[lang],
                texts=json.dumps(batch, ensure_ascii=False, indent=1))}],
            output_config=output_config(config, {
                "type": "object", "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
                "required": ["translations"], "additionalProperties": False}),
        )
        usage = usage_of(response)
        paid = cost(usage, tsettings)
        if log:
            log.request(response, "draft", paid, lang=lang, model=tsettings["model"], usage=usage)
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
            (str(i), en, tr) for i, (en, tr, c) in enumerate(zip(batch, out, checks)) if c == "ok"], cost, log)
        spent += review_cost
        saved["batches"][-1]["cost"] = round(paid + review_cost, 6)
        for i, (en, tr, checked) in enumerate(zip(batch, out, checks)):
            if checked == "ok" and (UNREVIEWED in problems or str(i) in problems):
                checked = f"review: {problems.get(str(i)) or problems[UNREVIEWED]}"
            before = saved["drafts"].get(en)
            attempts = before.get("attempts", 1) + 1 if before and not draft_to_remake(before) else 1
            saved["drafts"][en] = {"text": tr, "check": checked, "model": tsettings["model"],
                                   "prompt_version": NAMES_VERSION, "attempts": attempts, "generated_at": at}
            # A draft that fails leaves the one it replaces shown, if that one passed.
            showing = draft_shown(en, before) if before else None
            if checked != "ok" and showing:
                saved["drafts"][en]["previous"] = {k: v for k, v in showing.items() if k != "previous"}
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
        shown = draft_shown(en, d)
        line = f"{toml_string(en)} = {toml_string((shown or d)['text'])}"
        lines.append(line if shown else f"# {line}  # {d.get('check')}")
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
