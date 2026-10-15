"""Minutes posted as a Word file (.docx), saved as a PDF of its text.

Everything after the download reads PDFs: the summary, the fact check, the
roll calls, the page's text. A clerk who uploads the Word file instead of a
PDF (Malden's Board of Appeal) gets a plain PDF of the document's paragraphs
and tables, with a text layer, made here. Formatting, headers, footers and
images are left out; the meeting page still links the city's own file.

The older binary Word format (.doc) isn't read.
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# The PDF's built-in font covers Latin-1; Word's typographic punctuation is
# written as its plain equivalent, and anything else as "?".
PLAIN = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "‑": "-", "−": "-", "…": "...", "•": "\xb7",
    " ": " ", " ": " ", " ": " ", " ": " ", "​": "",
})


def is_docx(content: bytes) -> bool:
    if not content.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            return "word/document.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def _paragraph(p) -> str:
    parts = []
    for el in p.iter():
        if el.tag == f"{W}t":
            parts.append(el.text or "")
        elif el.tag == f"{W}tab":
            parts.append("\t")
        elif el.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts)


def paragraphs(content: bytes) -> list[str]:
    """The document body's paragraphs in order, a table row as one line, its cells separated by " | "."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        body = ElementTree.fromstring(z.read("word/document.xml")).find(f"{W}body")
    found = []
    for block in body if body is not None else []:
        if block.tag == f"{W}p":
            found.append(_paragraph(block))
        elif block.tag == f"{W}tbl":
            for row in block.iter(f"{W}tr"):
                cells = [" ".join(_paragraph(p) for p in cell.iter(f"{W}p")).strip() for cell in row.iter(f"{W}tc")]
                found.append(" | ".join(c for c in cells if c))
    return found


def to_pdf(content: bytes) -> bytes:
    """A letter-size PDF of the document's text, one paragraph after another."""
    from fpdf import FPDF

    pdf = FPDF(format="letter")
    pdf.set_creator("Publick (from a Word document)")
    pdf.set_margins(25, 25)
    pdf.set_auto_page_break(True, margin=25)
    pdf.add_page()
    pdf.set_font("helvetica", size=11)
    for text in paragraphs(content):
        text = text.translate(PLAIN).replace("\t", "    ").encode("latin-1", "replace").decode("latin-1")
        if text.strip():
            pdf.multi_cell(0, 5.5, text, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2.5)
    return bytes(pdf.output())
