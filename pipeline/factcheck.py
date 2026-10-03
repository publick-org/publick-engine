"""Summaries checked against their documents, without AI.

A summary, its headline, and each of its decisions or agenda items are
written by a model from the city's PDF, and nothing else checks them. This
module checks what can be checked mechanically: every number, amount, date,
and name in them is in the document's own text.

- A number counts however the document writes it ("$55,000.00", "55,000",
  "#014" and "#14"); an amount of money only as money in the document (after
  "$", or with cents), and a reference number in parts ("DEP File #5-1458")
  only whole. A total the model added up ("two grants totaling
  $463,733") counts when two to four of the document's amounts sum to it, and
  a rounded amount ("about $5.8 million") when one of the document's amounts
  rounds to it. A total counts only where the summary says it's one. An
  amount written out ("$8,000") counts when the document gives it with a
  scale ("$8K", "$1.2 million"), and the other way round.
- A date written out ("June 4, 2026") counts when the document has that
  date, written out or in figures ("6/4/2026"); its year when the document
  gives one for that day.
- A vote count ("7-0", "8-1") counts when the document writes it, as a
  count or in words ("3 in favor, 0 opposed"), or, for a
  count with no one against, when the document says the vote was unanimous
  or all in favor. A count the model made by counting names in a roll call
  can't be checked this way, so it's noted ("tally"), not failed.
- A name is a capitalized word that isn't an ordinary English word
  (pipeline/translate.py's names()), looked for with the document's spacing
  ignored, since some PDFs' text breaks words up ("T u renne").

The evidence is the PDF's own text layer, never the model's. A document whose
every page has text is checked in full ("pdf"). One with pages that have no
text (scanned pages in a typed document) can only be checked on what has text
("partial"), and a scan only against the model's transcription ("ai"), which
is weaker, since both come from the model: in both, what isn't found is
listed, but the result is "weak", not "failed".

Each summary's record keeps the result under "fact_check" ({"version",
"source", "result", "problems"}), made when the run reads the document's text
(pipeline/summarize.py) and again when VERSION changes. python -m
pipeline.factcheck lists the problems for the maintainer.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
from pathlib import Path

from pipeline import translate

VERSION = 2

FIELDS = {"agenda": ("headline", "summary", "items"), "minutes": ("headline", "summary", "decisions")}

# A page with less text than this is taken to be an image (a scanned page).
PAGE_MIN_CHARS = 40

# A vote count: "7-0", "4–1", "5-0-1".
TALLY = re.compile(r"(?<![\d$#.,/-])(\d{1,2})\s*[-–]\s*(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?(?![\d/-]|[.,]\d)")
UNANIMOUS = re.compile(r"unanim|all\s+(?:members\s+)?(?:present\s+)?(?:voted\s+)?in\s+favor|all\s+present\s+in\s+favor|"
                       r"all\s+voted\s+(?:yes|aye|in\s+favor)|without\s+objection|all\s+ayes", re.I)
# Words that make an amount approximate, or a total.
APPROXIMATE = re.compile(r"\b(?:about|approximately|nearly|almost|over|more\s+than|less\s+than|under|roughly|around)\s*\$?$", re.I)
# A US state's name: places a summary may spell out where the document abbreviates ("MA", "Mass.").
STATES = {"Massachusetts", "Connecticut", "Hampshire", "Maine", "Vermont", "Rhode", "Island", "York", "Jersey"}


def value(n: str) -> str:
    """A number as a value, however it's written: "$55,000.00", "55,000" and "55000" are 55000;
    "#014" is 14; "5,8" is 5.8 (translate.canonical()); "7:00" is 7."""
    n = n.rstrip(".,:")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", n):
        n = n.replace(",", "")
    else:
        n = translate.canonical(n)
    n = re.sub(r"(?<=\d)\.0+$", "", n)
    if re.fullmatch(r"\d+", n):
        n = n.lstrip("0") or "0"
    return n


def numbers(text: str) -> list[tuple[str, int, int]]:
    """Each number in a text: (value, start, end)."""
    return [(value(m.group(0)), m.start(), m.end()) for m in translate.NUMBER.finditer(text or "")]


def scaled_amounts(text: str) -> list[float]:
    """The amounts a text gives with a scale ("$8K" is 8000, "$1.2 million" 1200000)."""
    found = (scaled_value(text, s, e, v) for v, s, e in numbers(text))
    return sorted({x for x in found if x is not None})


def amounts(text: str) -> list[float]:
    """The document's amounts, for totals and rounding: every number with a value of 10 or more,
    with its scale word if it has one ("$14 million")."""
    found = []
    for v, s, e in numbers(text):
        try:
            x = float(v)
        except ValueError:
            continue
        big = scaled_value(text, s, e, v)
        if big is not None:
            found.append(big)
        if x >= 10:
            found.append(x)
    return sorted(set(found))


MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
# "June 4, 2026", "June 4th", "4 June 2026".
WRITTEN_DATE = re.compile(rf"\b{MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)
FIGURE_DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b")
# A date as documents write it, typos and all: "August, 3 2026", and "July 323\n14, 2026" in minutes
# that number their lines.
LOOSE_DATE = re.compile(rf"\b{MONTH}[\s,.]*(?:\d{{3}}\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\b(?:[\s,.]*(?:\d{{3}}\s+)?(\d{{4}}))?", re.I)


def month_number(name: str) -> int:
    return next(i + 1 for i, m in enumerate(MONTHS) if m.startswith(name.lower().rstrip(".")[:3]))


def dates(text: str) -> list[tuple[int, int, int | None, int, int]]:
    """The dates written out in a text: (month, day, year or None, start, end)."""
    found = []
    for m in WRITTEN_DATE.finditer(text or ""):
        day = int(m.group(2))
        if 1 <= day <= 31:
            found.append((month_number(m.group(1)), day, int(m.group(3)) if m.group(3) else None, m.start(), m.end()))
    return found


def document_dates(text: str) -> set[tuple[int, int, int | None]]:
    """Every date in the document, written out or in figures, with and without its year."""
    found = set()
    for m in LOOSE_DATE.finditer(text or ""):
        month, day, year = month_number(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None
        if 1 <= day <= 31:
            found |= {(month, day, year), (month, day, None)}
    for m in FIGURE_DATE.finditer(text or ""):
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year = year + 2000 if year < 100 else year
        if 1 <= month <= 12 and 1 <= day <= 31:
            found |= {(month, day, year), (month, day, None)}
    return found


def date_found(month: int, day: int, year: int | None, doc_dates: set) -> bool:
    """A date with a year is found if the document has it with that year, or without any year
    (minutes often write "June 4th"); one without a year, if the document has the month and day."""
    if year is None:
        return (month, day, None) in doc_dates
    return (month, day, year) in doc_dates or ((month, day, None) in doc_dates
                                                and not any(d[:2] == (month, day) and d[2] for d in doc_dates))


# A reference number in parts: "028-3125", "DEP File #5-1458", "Paper #208-26", "26-02.0726". Not a
# vote count (two numbers of one or two digits) or a date in figures.
REFERENCE = re.compile(r"(?<![\w.,/-])\d{1,5}(?:[-–]\d{1,6})+(?:\.\d+)?(?![\w/-]|[.,]\d)")


def is_reference(m: re.Match) -> bool:
    parts = re.split(r"[-–.]", m.group(0))
    return not (len(parts) in (2, 3) and all(len(p) <= 2 for p in parts))


def money(text: str) -> set[str]:
    """The document's amounts of money: numbers after "$", with cents, or before "dollars"."""
    found = set()
    for m in translate.NUMBER.finditer(text or ""):
        before, after = text[max(0, m.start() - 3):m.start()], text[m.end():m.end() + 9]
        if "$" in before or re.fullmatch(r"\d{1,3}(?:,\d{3})*\.\d\d", m.group(0)) or re.match(r"\s*dollars", after, re.I):
            found.add(value(m.group(0)))
    return found


# Words that say an amount is a sum of others.
TOTAL = re.compile(r"\b(?:total(?:s|ing|ling|ed)?|combined|together|in\s+all|sum)\b", re.I)


def squashed(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "k": 1e3, "m": 1e6, "b": 1e9}


def scaled_value(text: str, start: int, end: int, v: str) -> float | None:
    """The number at text[start:end] times its scale word, if it has one ("$5.8 million")."""
    m = re.match(r"\s*(thousand|million|billion|[KMB])\b", text[end:end + 12], re.I)
    if not m:
        return None
    try:
        return float(v) * SCALE[m.group(1).lower()]
    except ValueError:
        return None


def is_total(x: float, doc_amounts: list[float]) -> bool:
    """Whether two to four of the document's amounts add up to x (to the cent)."""
    parts = [a for a in doc_amounts if a < x]
    if len(parts) > 120:
        parts = parts[-120:]
    for k in (2, 3):
        for combo in itertools.combinations(parts, k):
            if abs(sum(combo) - x) < 0.005:
                return True
    if len(parts) <= 40:
        for combo in itertools.combinations(parts, 4):
            if abs(sum(combo) - x) < 0.005:
                return True
    return False


def rounds_to(x: float, doc_amounts: list[float]) -> bool:
    """Whether one of the document's amounts is within 5% of x: a rounded or approximate amount."""
    return any(abs(a - x) <= 0.05 * x for a in doc_amounts)


def check_text(text: str, doc: str, doc_numbers: set[str], doc_amounts: list[float], doc_squashed: str,
               words: frozenset, doc_dates: set | None = None, doc_money: set | None = None,
               doc_scaled: list[float] | None = None) -> list[dict]:
    """What in one text of a summary isn't in the document."""
    problems = []
    doc_dates = document_dates(doc) if doc_dates is None else doc_dates
    doc_money = money(doc) if doc_money is None else doc_money
    doc_scaled = scaled_amounts(doc) if doc_scaled is None else doc_scaled
    tallies = [(m.start(), m.end(), m) for m in TALLY.finditer(text)]
    spans = [(s, e) for s, e, _ in tallies]
    for s, e, m in tallies:
        parts = [g for g in m.groups() if g is not None]
        if re.search(rf"(?<![\d-]){parts[0]}\s*(?:[-–]|to)\s*{parts[1]}(?![\d-])", doc):
            continue
        # "voted 3 in favor, 0 opposed", "5 yeas, 2 nays", "4 ayes and 1 no".
        if re.search(rf"(?<![\d-]){parts[0]}\s+(?:(?:votes?|members?)\s+)?(?:in\s+favou?r|yeas?|ayes?|yes)\W+(?:and\s+|with\s+)?"
                     rf"{parts[1]}\s+(?:(?:votes?|members?)\s+)?(?:opposed|nays?|noes|no|against)", doc, re.I):
            continue
        if all(p == "0" for p in parts[1:]) and UNANIMOUS.search(doc):
            continue
        problems.append({"kind": "tally", "what": m.group(0)})
    for month, day, year, s, e in dates(text):
        spans.append((s, e))
        if not date_found(month, day, year, doc_dates):
            problems.append({"kind": "date", "what": text[s:e]})
    for m in REFERENCE.finditer(text):
        if any(s <= m.start() < e for s, e in spans) or not is_reference(m):
            continue
        spans.append((m.start(), m.end()))
        if re.sub("–", "-", m.group(0)) in doc_squashed.replace("–", "-"):
            continue
        # A range ("Papers 317-320", "2027-2028"): its ends, each in the document.
        ends = re.split(r"[-–]", m.group(0))
        if len(ends) == 2 and len(ends[0]) == len(ends[1]) and ends[0] < ends[1] \
                and all(value(x) in doc_numbers for x in ends):
            continue
        problems.append({"kind": "number", "what": m.group(0)})
    taken = lambda pos: any(s <= pos < e for s, e in spans)
    for v, s, e in numbers(text):
        # Money as money; but an amount of $1,000 or more is unlikely to be in the document by chance,
        # so it counts wherever it is (a table without dollar signs).
        dollars = "$" in text[max(0, s - 2):s] and not re.fullmatch(r"\d{4,}(?:\.\d+)?", v)
        if taken(s) or (v in (doc_money if dollars else doc_numbers)):
            continue
        try:
            x = float(v)
        except ValueError:
            x = None
        big = scaled_value(text, s, e, v)
        if big is not None and rounds_to(big, doc_amounts):
            continue
        # "$8,000" where the document says "$8K".
        if x is not None and any(abs(a - x) < 0.005 for a in doc_scaled):
            continue
        if x is not None and x >= 10:
            if TOTAL.search(text[max(0, s - 40):e + 40]) and is_total(x, doc_amounts):
                continue
            # A sum of other amounts the summary itself gives, each in the document:
            # "$10,000 in council funds ($7,500 for ward councillors and $2,500 at large)".
            given = [float(n) for n, _, _ in numbers(text) if n != v and n in doc_numbers and re.fullmatch(r"[\d.]+", n)]
            if is_total(x, sorted(set(given))):
                continue
            if APPROXIMATE.search(text[max(0, s - 20):s]) and rounds_to(x, doc_amounts):
                continue
        problems.append({"kind": "number", "what": text[s:e]})
    for name in sorted(translate.names(text, words)):
        if name in STATES or squashed(name) in doc_squashed:
            continue
        problems.append({"kind": "name", "what": name})
    return problems


def check(record: dict, kind: str, pages: list[str] | None, words: frozenset = frozenset()) -> dict:
    """The fact check of a summary against its document's text, page by page (None for a PDF
    with no text at all). A scan is checked against the model's transcription, if it has one."""
    texted = [p for p in pages or [] if len(p.strip()) >= PAGE_MIN_CHARS]
    if pages and len(texted) == len(pages):
        source, doc = "pdf", "\n".join(pages)
    elif texted:
        source, doc = "partial", "\n".join(pages)
    elif record.get("transcript") and record.get("transcript_source") == "ai":
        source, doc = "ai", record["transcript"]
    else:
        return {"version": VERSION, "source": "none", "result": "unchecked", "problems": []}
    # Numbers some PDFs' text breaks up ("$14,000,0 00") are read joined too.
    doc_numbers = {v for v, _, _ in numbers(doc)} | {v for v, _, _ in numbers(re.sub(r"(\d,\d{1,2})\s+(\d)", r"\1\2", doc))}
    joined = re.sub(r"(\d,\d{1,2})\s+(\d)", r"\1\2", doc)
    doc_amounts, doc_squashed, doc_dates = sorted(set(amounts(doc)) | set(amounts(joined))), squashed(doc), document_dates(doc)
    doc_money = money(doc) | money(re.sub(r"(\d,\d{1,2})\s+(\d)", r"\1\2", doc))
    doc_scaled = scaled_amounts(doc)
    problems = []
    for field in FIELDS[kind]:
        entries = record.get(field) or ([] if field in ("items", "decisions") else "")
        for i, text in enumerate(entries if isinstance(entries, list) else [entries]):
            for p in check_text(text, doc, doc_numbers, doc_amounts, doc_squashed, words, doc_dates, doc_money, doc_scaled):
                problems.append({"field": field, **({"entry": i + 1} if isinstance(entries, list) else {}), **p})
    hard = [p for p in problems if p["kind"] != "tally"]
    result = "ok" if not hard else ("failed" if source == "pdf" else "weak")
    return {"version": VERSION, "source": source, "result": result, "problems": problems}


def pages(pdf: bytes) -> list[str] | None:
    """The text layer of each page of a PDF (None if it can't be read, or has no text at all)."""
    import io

    from pypdf import PdfReader
    try:
        texts = [page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages]
    except Exception:
        return None
    return texts if any(t.strip() for t in texts) else None


def current(record: dict) -> bool:
    """Whether the record's fact check is up to date: made with this VERSION, and, for a scan
    checked before it had the model's transcription, not since given one."""
    fc = record.get("fact_check") or {}
    return fc.get("version") == VERSION and not (fc.get("source") == "none" and record.get("transcript_source") == "ai")


# ---- What's shown ------------------------------------------------------------------

def without_tally(text: str, tally: str) -> str:
    """The text without a vote count, and the words that only introduce it: "Approved the plan,
    5-0." is "Approved the plan."; "voted 2-0-1 to recommend" is "voted to recommend"; and the
    Spanish "con votación 5-0" goes as well."""
    t = re.escape(tally)
    for pattern in (
        rf"\s*\(\s*{t}\s*\)",                                                       # "(4-0)"
        rf",\s*(?:approved|passed|passing)\s+{t}(?=\s*(?:[.;]|$))",                     # ", approved 5-0." at the end
        rf"[,;]?\s*(?:by|on|with)\s+an?\s+{t}\s+vote\b",                                # "by a 5-0 vote"
        rf"[,;]?\s*(?:(?:con|por)\s+(?:una\s+)?)?votaci[oó]n\s+(?:de\s+)?{t}",           # "con votación 5-0"
        rf"[,;]?\s*(?:by\s+a\s+)?vote\s+(?:of\s+)?{t}",                                 # "vote 4-1", "by a vote of 4-1"
        rf"[,;]?\s*{t}\s+(?:vote|votos?)\b",                                           # ", 3-2 vote"
        rf"[,;]?\s*(?:por\s+)?{t}(?=\s*(?:[.;,)]|$))",                                  # ", 5-0." at the end
        rf"\s+{t}(?=\s)",                                                              # "approved 5-0 with"
    ):
        new = re.sub(pattern, "", text, count=1, flags=re.I)
        if new != text:
            text = new
            break
    text = re.sub(r"\s+([,.;)])", r"\1", text)
    text = re.sub(r",\s*\(", " (", text)
    text = re.sub(r"([,;])\s*([.;])", r"\2", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def sentences(text: str) -> list[str]:
    """A summary's sentences (a period, question, or exclamation mark, then a capital letter)."""
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])", text or "") if s]


def shown(display: dict | None, record: dict | None, kind: str) -> dict | None:
    """A summary as the site shows it (display: the record, or its translation, entry by entry),
    after its fact check (record's "fact_check"):
    - checked against the document's full text, a decision or agenda item with something not
      in the document isn't shown, nor is such a headline, or such a sentence of the summary
      (the whole summary, in another language, where its sentences can't be matched);
      "not_shown" counts the entries left out, for the page to say so;
    - wherever it was checked, a vote count the document doesn't give is left out of the text.
    Weaker checks (scanned pages, a transcription) hide nothing else: what they can't find may be
    on a page they can't read."""
    if not display or not record or not record.get("fact_check"):
        return display
    fc = record["fact_check"]
    out = dict(display)
    english = display is record or display.get("headline") == record.get("headline")
    hidden: dict[str, set] = {}
    for p in fc["problems"]:
        field = p["field"]
        if field not in out or not out[field]:
            continue
        if p["kind"] == "tally":
            if isinstance(out[field], list):
                i = p["entry"] - 1
                if i < len(out[field]):
                    out[field] = [without_tally(t, p["what"]) if j == i else t for j, t in enumerate(out[field])]
            else:
                out[field] = without_tally(out[field], p["what"])
        elif fc["result"] == "failed":
            hidden.setdefault(field, set()).add(p.get("entry"))
    for field, entries in hidden.items():
        if isinstance(out[field], list):
            out[field] = [t for j, t in enumerate(out[field]) if j + 1 not in entries]
            out["not_shown"] = out.get("not_shown", 0) + len(entries)
        elif field == "summary" and english:
            whats = [p["what"] for p in fc["problems"] if p["field"] == "summary" and p["kind"] != "tally"]
            out[field] = " ".join(s for s in sentences(out[field]) if not any(w in s for w in whats))
        else:
            out[field] = ""
    return out


def kind_of(record: dict) -> str:
    return record.get("kind") or ("minutes" if "decisions" in record else "agenda")


def report(data_dir: Path) -> str:
    """Every summary whose check found something, for the maintainer."""
    lines, counts = [], {}
    for file in sorted((data_dir / "summaries").glob("*.json")):
        record = json.loads(file.read_text(encoding="utf-8"))
        fc = record.get("fact_check")
        result = fc["result"] if fc else "not checked yet"
        counts[result] = counts.get(result, 0) + 1
        if not fc or not fc["problems"]:
            continue
        lines.append(f"{file.stem[:12]} ({kind_of(record)}, {fc['source']}, {result}): {record.get('source_url', '')}")
        for p in fc["problems"]:
            where = p["field"] + (f" {p['entry']}" if "entry" in p else "")
            lines.append(f"  {where}: {p['kind']} {p['what']!r}")
    head = ", ".join(f"{n} {r}" for r, n in sorted(counts.items()))
    return f"Summaries: {head}\n" + "\n".join(lines) + ("\n" if lines else "")


def main() -> None:
    from pipeline.config import DATA_DIR
    parser = argparse.ArgumentParser(description="Summaries whose facts weren't found in their documents")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    print(report(args.data), end="")


if __name__ == "__main__":
    main()
