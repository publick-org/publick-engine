"""Summaries in the site's other languages, translated from the English summary.

A town with Spanish pages ([site] languages) gets each English summary of an
agenda or minutes in Spanish too: translated from the English summary (never
from the PDF again) by a small model, Claude Haiku 4.5 unless [summaries]
translation_model says otherwise. The rules are the summaries' own: only what
the summary says, neutral, plain, with names, addresses, amounts, dates, and
vote counts kept.

A check without AI then confirms every number in the English is in the
Spanish, and that each list (agenda items, decisions) has as many entries. A
translation that fails is kept, so it isn't paid for again, but isn't shown:
the page shows the English summary.

Each translation is its own record, data/summaries/<language>/<document's
SHA-256>.json, holding the hash of the English it came from. Turning a
language on never regenerates an English summary, and a translation is made
again only when its English summary changes (or this module's prompt does:
VERSION). summarize.py runs the translations within the same budget as the
summaries, new documents first; their cost is in the month's ledger as
translation_cost.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

VERSION = 1
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
# Claude Haiku 4.5's prices, dollars per million tokens, for a town that doesn't give the model's own.
DEFAULT_PRICES = {"input_price": 1.0, "output_price": 5.0}
MAX_TOKENS = 8000

# The fields of each kind of summary that are shown, and so translated.
FIELDS = {"agenda": ("headline", "summary", "items"), "minutes": ("headline", "summary", "decisions")}

LANGUAGE_NAMES = {"es": "Spanish"}
# Each language's readers, and the words the sites use for what summaries talk about
# (site/strings/es-guide.md has the full glossary).
READERS = {"es": """Write plain Spanish as Dominican and Puerto Rican residents of a Massachusetts city read it every day: natural, not formal, not word for word, and not Spain's Spanish. Address no one directly.
Use these words: meeting = reunión; minutes = actas; agenda = agenda; public hearing = audiencia pública; motion = moción; vote = votación; executive session = sesión ejecutiva; councilor = concejal; fiscal year = año fiscal; property tax = impuesto a la propiedad; budget = presupuesto; building permit = permiso de construcción; ward = distrito.
Write dates in Spanish ("22 de octubre de 2026"), times as "7:00 p. m.", and numbers and money as the English does ("$1,500", "4.5%")."""}

SYSTEM = """You translate short summaries of a city government's public meeting agendas and minutes from English into {language}, for residents.

Rules:
- Translate only what the English says. Add nothing, leave nothing out, and don't explain.
- Keep the neutral tone. No words that judge.
- Short sentences, about an 8th-grade reading level.
- Keep every name of a person, business, street, address, and place exactly as written, and keep every number: dollar amounts, dates, times, vote counts ("5-0"), and case, application, and order numbers.
- Board and committee names: use the translations given below when there are any; otherwise keep the English name.
- Return the same fields, and each list with as many entries, in the same order.

{readers}"""

PROMPT = """Translate this summary of the {kind} of a meeting ({title}, {date}) into {language}.
{boards}
{summary}"""


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


def current(data_dir: Path, lang: str, sha256: str, record: dict, kind: str) -> dict | None:
    """The saved translation of this English summary, made with the current prompt (shown or not)."""
    found = saved(data_dir, lang, sha256)
    if found and found.get("source_hash") == source_hash(record, kind) and found.get("prompt_version") == VERSION:
        return found
    return None


def shown(data_dir: Path, lang: str, sha256: str, record: dict, kind: str) -> dict | None:
    """The translation to show for an English summary: one made from this English that passes the
    check. The check runs again here, so a translation the check once wrongly failed is shown once
    the check is fixed, without paying for it again."""
    found = saved(data_dir, lang, sha256)
    if found and found.get("source_hash") == source_hash(record, kind) and \
            check(english(record, kind), found, kind, lang) == "ok":
        return found
    return None


# A number as the summaries write it: "1,500", "4.5", "7:00", "2026", the 5 and the 0 of "5-0".
NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")


# A date in figures, as agendas write it: "9/23/2026", "9/23/26", "9/23".
DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b")
# Each language's month names, January first, for a date the translation writes out.
MONTHS = {"es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                 "septiembre|setiembre", "octubre", "noviembre", "diciembre")}


def numbers(text: str) -> Counter:
    return Counter(n.rstrip(".,:") for n in NUMBER.findall(text or ""))


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


def check(source: dict, translated: dict, kind: str, lang: str = "es") -> str:
    """ "ok", or what's wrong: a field missing or empty, a list of another length, or a number
    in the English that isn't in the translation. A date in figures may be written out."""
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
            a, b = dates_kept(a, b, lang)
            lost = numbers(a) - numbers(b)
            if lost:
                where = f"{field} {i + 1}" if isinstance(en, list) else field
                return f"{where}: {', '.join(sorted(lost))} not in the translation"
    return "ok"


def settings(config: dict) -> dict:
    """The translation model and its prices: [summaries] translation_model, translation_input_price,
    and translation_output_price, or Claude Haiku 4.5's."""
    s = config.get("summaries", {})
    model = s.get("translation_model", DEFAULT_MODEL)
    prices = DEFAULT_PRICES if model == DEFAULT_MODEL else {}
    return {"model": model,
            "input_price": s.get("translation_input_price", prices.get("input_price")),
            "output_price": s.get("translation_output_price", prices.get("output_price"))}


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


def translate(client, config: dict, lang: str, kind: str, record: dict, meeting: dict) -> tuple[dict, dict]:
    """The model's translation of one English summary, and the tokens it used."""
    source = english(record, kind)
    response = client.messages.create(
        model=settings(config)["model"],
        max_tokens=MAX_TOKENS,
        system=SYSTEM.format(language=LANGUAGE_NAMES[lang], readers=READERS[lang]),
        messages=[{"role": "user", "content": PROMPT.format(
            kind=kind, title=meeting["title"], date=meeting["date"], language=LANGUAGE_NAMES[lang],
            boards=board_names(config, lang, meeting["body"]),
            summary=json.dumps(source, ensure_ascii=False, indent=1))}],
        output_config={"format": {"type": "json_schema", "schema": schema(kind)}},
    )
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    if response.stop_reason != "end_turn":
        from pipeline.summarize import StoppedEarly
        raise StoppedEarly(response.stop_reason, usage)
    return json.loads(next(b.text for b in response.content if b.type == "text")), usage


def save(data_dir: Path, lang: str, sha256: str, record: dict) -> None:
    file = path(data_dir, lang, sha256)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def make(client, config: dict, data_dir: Path, lang: str, kind: str, meeting: dict, doc: dict, record: dict,
         now: datetime, cost) -> tuple[dict, float]:
    """Translate one summary, check it, and save it. Returns the saved record and what it cost."""
    result, usage = translate(client, config, lang, kind, record, meeting)
    paid = cost(usage, settings(config))
    out = {
        **{field: result.get(field) for field in FIELDS[kind]},
        "language": lang,
        "kind": kind,
        "source_sha256": doc["sha256"],
        "source_hash": source_hash(record, kind),
        "check": check(english(record, kind), result, kind, lang),
        "model": settings(config)["model"],
        "prompt_version": VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "usage": usage,
        "cost": round(paid, 6),
    }
    save(data_dir, lang, doc["sha256"], out)
    return out, paid


def month_counts(data_dir: Path) -> dict[str, dict]:
    """What the saved translations cost, by the month they were made: {month: {"cost", "translations"}}."""
    counted: dict[str, dict] = {}
    for file in sorted((data_dir / "summaries").glob("*/*.json")):
        record = json.loads(file.read_text(encoding="utf-8"))
        at = record.get("generated_at", "")[:7]
        if at:
            row = counted.setdefault(at, {"cost": 0.0, "translations": 0})
            row["cost"] += record.get("cost", 0.0)
            row["translations"] += 1
    return counted
