"""A posted PDF's own text, for reading and for search.

Agendas and minutes are shown on each meeting page as readable text. Where
the PDF has a text layer, its own words are used, never an AI copy: for a
document from a supported style (STYLES, the software that made it), the text
is laid out as Markdown from the PDF's fonts and positions (headings, lists,
agenda items, and a letterhead), and checked word for word against the text
before it's used. Anything else (another style, a scan with no text, a check
that fails) gets no readable text here; pipeline.summarize then has the AI
transcribe it, within the summary budget, or the page links the original.

plain_text() is every word of the text layer, for search and the street
lookup, whatever the style.

A style is added once per document software, not per town: every city whose
clerk uses Legistar posts minutes that look the same.
"""

from __future__ import annotations

import io
import re
from collections import Counter, defaultdict

# Bump when the layout rules change, so documents are laid out again.
VERSION = 1

# Supported styles: the PDF's Producer (or Creator), as a pattern, and a name.
STYLES = [
    # Granicus Legistar's agendas and minutes ("Meeting Minutes - Final"), made with Crystal Reports.
    (re.compile(r"Crystal", re.I), "legistar"),
]

# A word-for-word check: the laid-out text must hold every word kept from the
# PDF, in order, and hold this share of the letters and digits a second,
# independent reading of the PDF (pypdf's) finds, so neither library's mistake
# can drop text unnoticed. (Letters, not words: the two readings split some
# words differently.)
SECOND_READING_MIN = 0.99
WORD = re.compile(r"[A-Za-z0-9]+")


# Scanners and copiers that recognize the text of what they scan: their text is
# often wrong, so their PDFs are treated as scans and read by the model.
SCANNERS = re.compile(r"\b(RICOH|KONICA|MINOLTA|bizhub|Canon|Xerox|SHARP|Kyocera|EPSON|Brother|Lexmark|"
                      r"Hewlett-Packard|HP|Fujitsu|ScanSnap|Paperport|scan)\b", re.I)


def made_by(pdf: bytes) -> str:
    from pypdf import PdfReader
    try:
        meta = PdfReader(io.BytesIO(pdf)).metadata or {}
    except Exception:
        return ""
    return f"{meta.get('/Producer') or ''} {meta.get('/Creator') or ''}".strip()


def page_texts(pdf: bytes) -> list[str] | None:
    """The text of each page, running headers and footers left out, for a PDF made
    with text (not a scan, nor a scanner's own recognized text); None otherwise."""
    if SCANNERS.search(made_by(pdf)):
        return None
    try:
        rows = lines(pdf)
    except Exception:
        return None
    if sum(len(r["text"]) for r in rows) < 200:
        return None
    kept, _ = _drop_running(rows)
    pages = max(r["page"] for r in rows) + 1
    return ["\n".join(r["text"] for r in kept if r["page"] == p) for p in range(pages)]


def style(pdf: bytes) -> str | None:
    """The supported style the PDF was made with, or None."""
    software = made_by(pdf)
    return next((name for pattern, name in STYLES if pattern.search(software)), None)


def plain_text(pdf: bytes) -> str:
    """Every word of the PDF's text layer, page by page ("" for a scan)."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(pdf))
        return "\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except Exception:
        return ""


def lines(pdf: bytes) -> list[dict]:
    """Each line of text, top to bottom, with its main font and size, where it starts,
    where its main text starts (after a label in another font), and where each word starts. A line with two pieces far
    apart (a letterhead's left and right columns) is two lines."""
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        for p, page in enumerate(doc.pages):
            rows = defaultdict(list)
            for w in page.extract_words(extra_attrs=["fontname", "size"]):
                key = next((k for k in rows if abs(k - w["top"]) < 2.0), w["top"])
                rows[key].append(w)
            for top in sorted(rows):
                words = sorted(rows[top], key=lambda w: w["x0"])
                pieces, piece = [], [words[0]]
                for w in words[1:]:
                    if w["x0"] - piece[-1]["x1"] > 3 * w["size"]:
                        pieces.append(piece)
                        piece = [w]
                    else:
                        piece.append(w)
                pieces.append(piece)
                for ws in pieces:
                    fonts = Counter((w["fontname"].split("+")[-1], round(w["size"], 1)) for w in ws)
                    main = max(fonts, key=lambda f: sum(len(w["text"]) for w in ws
                                                        if (w["fontname"].split("+")[-1], round(w["size"], 1)) == f))
                    tx = next(w["x0"] for w in ws if (w["fontname"].split("+")[-1], round(w["size"], 1)) == main)
                    out.append({"page": p, "y": -top, "x": ws[0]["x0"], "tx": tx, "font": main[0], "size": main[1],
                                "bold": "Bold" in main[0], "text": " ".join(w["text"] for w in ws),
                                "xs": [w["x0"] for w in ws]})
    return out


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text)]


def _drop_running(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Running headers and footers (the same text, numbers aside, in the same place among
    the first or last three lines of most pages) and page numbers: (kept, dropped)."""
    pages = max((r["page"] for r in rows), default=0) + 1
    by_page = defaultdict(list)
    for i, r in enumerate(rows):
        by_page[r["page"]].append(i)
    edge = {i for idx in by_page.values() for i in idx[:3] + idx[-3:]}
    # The same words in the same place: a title at the top of page 1 isn't the
    # footer that repeats its words at the bottom of every page.
    shape = lambda r: (re.sub(r"\d+", "#", r["text"]), round(r["y"] / 12))
    seen = Counter(shape(rows[i]) for i in edge)
    repeated = {t for t, n in seen.items() if pages > 1 and n >= max(2, pages // 2)}
    kept, dropped = [], []
    for i, r in enumerate(rows):
        running = i in edge and (shape(r) in repeated or re.fullmatch(r"(Page )?\d+( of \d+)?", r["text"], re.I) is not None)
        (dropped if running else kept).append(r)
    return kept, dropped


def layout(rows: list[dict]) -> str:
    """Lines (running headers and footers already removed) as Markdown. Every line's text is
    used, once, in order; only how lines are joined and marked differs."""
    if not rows:
        return ""
    body = Counter(r["size"] for r in rows if not r["bold"]).most_common(1)
    body = body[0][0] if body else rows[0]["size"]
    numbered = re.compile(r"^\d{1,2}\.\s")
    heading_size = max((r["size"] for r in rows if r["bold"] and numbered.match(r["text"])), default=None)
    first = next((i for i, r in enumerate(rows) if heading_size and r["size"] == heading_size
                  and r["bold"] and numbered.match(r["text"])), 0)
    widest = defaultdict(int)
    for r in rows:
        widest[(r["font"], r["size"])] = max(widest[(r["font"], r["size"])], len(r["text"]))

    md = []
    # The letterhead, in order: lines in large type as a title, the rest as short runs.
    run, run_font = [], None
    for r in rows[:first]:
        font = "title" if r["size"] >= body + 3 else r["font"]
        if run and font != run_font:
            md.append(("# " if run_font == "title" else "") + " · ".join(run))
            run = []
        run.append(r["text"])
        run_font = font
    if run:
        md.append(("# " if run_font == "title" else "") + " · ".join(run))

    block, kind = [], None

    def flush():
        nonlocal block, kind
        if block:
            if kind == "list":
                md.append("\n".join("- " + t for t in block))
            elif kind == "heading":
                md.append("## " + " ".join(block))
            elif kind == "subheading":
                md.append("### " + " ".join(block))
            elif kind == "item":
                number, *rest = block
                md.append(f"**{number}** " + " ".join(rest))
            else:
                md.append(" ".join(block))
        block, kind = [], None

    prev = None
    for r in rows[first:]:
        text = r["text"]
        short = len(text) < 0.75 * widest[(r["font"], r["size"])]
        is_heading = r["bold"] and r["size"] >= (heading_size or body + 1)
        sub = is_heading and heading_size is not None and r["size"] != heading_size
        is_item = re.match(r"^\d{2,4}-\d{2}\b", text) is not None
        gap = prev is not None and (r["page"] != prev["page"] or abs(prev["y"] - r["y"]) > 1.8 * r["size"])
        same_style = prev is not None and prev["font"] == r["font"] and abs(prev["tx"] - r["tx"]) < 6
        # A line that only continues the one before: a list of names, a label's value.
        continues = (prev is not None and prev["font"] == r["font"] and not gap and block
                     and block[-1].rstrip().endswith((",", "-", " to", " of", " the", " and")))
        if is_heading:
            want = "subheading" if sub else "heading"
            if kind == want and not gap:
                block.append(text)
            else:
                flush()
                block, kind = [text], want
        elif is_item:
            flush()
            block, kind = re.split(r"\s+", text, maxsplit=1), "item"
        elif continues and kind in ("para", "item"):
            block.append(text)
        elif kind == "item" and same_style:
            block.append(text)
        elif kind == "list" and same_style and text[:1].islower():
            # The last "item" wraps onto this line: it began a paragraph.
            last = block.pop()
            flush()
            block, kind = [last, text], "para"
        elif kind in ("para", "list") and same_style and not (gap and r["page"] == prev["page"]):
            ends = block[-1].rstrip()[-1:] in ".:;?!\""
            if prev["short"] and not ends:
                # A short line that doesn't end a sentence: one line of a list.
                if kind == "para" and len(block) == 1:
                    kind = "list"
                if kind == "list":
                    block.append(text)
                else:
                    flush()
                    block, kind = [text], "list"
            elif kind == "list" and not short:
                flush()
                block, kind = [text], "para"
            elif kind == "list":
                flush()
                block, kind = [text], "para"
            elif ends and prev["short"]:
                flush()
                block, kind = [text], "para"
            else:
                block.append(text)
        else:
            flush()
            block, kind = [text], "para"
        prev = {**r, "short": short}
    flush()
    return "\n\n".join(md)


def readable(pdf: bytes) -> tuple[str | None, str]:
    """(Markdown, why) for a PDF of a supported style whose layout passes the check;
    (None, why not) otherwise."""
    made_with = style(pdf)
    if not made_with:
        return None, "not a supported style"
    try:
        rows = lines(pdf)
    except Exception as e:
        return None, f"couldn't be read: {e}"
    if sum(len(r["text"]) for r in rows) < 200:
        return None, "little or no text layer"
    kept, dropped = _drop_running(rows)
    text = layout(kept)
    # Every kept word, once, in order.
    if words(text.replace("·", " ")) != words(" ".join(r["text"] for r in kept)):
        return None, "the laid-out text doesn't match the PDF's words"
    # And a second, independent reading of the PDF agrees.
    second = Counter(c for c in plain_text(pdf).lower() if c.isalnum())
    # The running headers and footers are left out on purpose; they count as kept here.
    ours = Counter(c for c in (text + " ".join(r["text"] for r in dropped)).lower() if c.isalnum())
    ratio = sum(min(n, ours[c]) for c, n in second.items()) / sum(second.values()) if second else 0.0
    if ratio < SECOND_READING_MIN:
        return None, f"a second reading of the PDF differs ({ratio:.0%} the same)"
    return text, made_with
