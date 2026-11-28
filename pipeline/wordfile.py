"""Minutes posted as a Word file (.docx), saved as a PDF of its text.

Everything after the download reads PDFs: the summary, the fact check, the
roll calls, the page's text. A clerk who uploads the Word file instead of a
PDF (Malden's Board of Appeal) gets a plain PDF of the document's paragraphs
and tables, with a text layer, made here. Formatting, headers, footers and
images are left out; the meeting page still links the city's own file.

text_pdf() makes the PDF from any list of paragraphs, so a document posted
as a web page's text (pipeline/htmlfile.py) is saved the same way. The same paragraphs make the
same bytes: the PDF's creation date is a fixed one (MADE), not the time it
was made, so a document fetched again unchanged isn't saved again as a new
version.

The bytes are the same for one version of fpdf2 only: another can write the
same text as other bytes (the numbers of its page size, a creation date, its
/ID). So a reader that converts a document asks unchanged() whether it's the
same as the latest saved: the same bytes, or, for a document converted from
the same kind of file, the same words in its PDF and in the saved one
(same_text()). Only a converted document whose hash differs is read for its
text, so a city's own PDF is still compared by its hash alone.

The older binary Word format (.doc) isn't read. as_pdf() raises Unreadable for
it and for the other kinds of file it can tell aren't read (an RTF file, an
image, a zip that isn't a .docx), so a reader can tell a file it will never
read from a download that failed or a page served where a file should be.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
import zlib
from datetime import datetime, timezone
from xml.etree import ElementTree

from pipeline import pdftext
from pipeline.http import FetchError


class Unreadable(FetchError):
    """A downloaded file of a kind that isn't read (other_kind), such as an old Word .doc. Asking for the same
    address again gets the same file, so a reader reports it rather than trying it again."""


# The first bytes of files of a kind that isn't read: an old Word, Excel or PowerPoint file (OLE2), an RTF file
# (with or without a byte order mark), a WordPerfect file, and a PNG, JPEG, GIF or TIFF image.
OTHER_KINDS = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"{\\rtf", b"\xef\xbb\xbf{\\rtf", b"\xffWPC",
               b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*")


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# The creation date written in every PDF made here, in place of the time it was made: the PDF's /ID is
# a hash of its bytes and this date, so both stay the same for the same text. (fpdf2 2.9 needs a date.)
MADE = datetime(1970, 1, 1, tzinfo=timezone.utc)

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


def other_kind(content: bytes) -> bool:
    """Whether a file that isn't a PDF or a .docx is a file of another kind (OTHER_KINDS, or a whole zip, such as an
    .xlsx or .odt), not an HTML page, an empty body or a download cut short, which asking again may mend."""
    if content.startswith(OTHER_KINDS):
        return True
    if not content.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)):
            return True
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


def text_pdf(texts: list[str], creator: str) -> bytes:
    """A letter-size PDF of paragraphs of text, one after another, made by `creator` ("Publick (from a Word
    document)")."""
    from fpdf import FPDF

    pdf = FPDF(format="letter")
    pdf.set_creator(creator)
    # A fixed creation date, so the same paragraphs always make the same file.
    pdf.set_creation_date(MADE)
    pdf.set_margins(25, 25)
    pdf.set_auto_page_break(True, margin=25)
    pdf.add_page()
    pdf.set_font("helvetica", size=11)
    for text in texts:
        text = text.translate(PLAIN).replace("\t", "    ").encode("latin-1", "replace").decode("latin-1")
        if text.strip():
            pdf.multi_cell(0, 5.5, text, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2.5)
    return bytes(pdf.output())


def as_pdf(content: bytes, url: str) -> tuple[bytes, str | None]:
    """A downloaded file as a PDF, and what it was converted from: a Word file's text ("docx"), or a PDF as
    it is (None). Anything else is a FetchError naming the address: Unreadable for a file of another kind (a .doc)
    or a Word file that can't be read, a plain FetchError for what may be read another time (a sign-in page where
    a file should be)."""
    if is_docx(content):
        try:
            return to_pdf(content), "docx"
        except (KeyError, zipfile.BadZipFile, zlib.error, ElementTree.ParseError) as e:
            raise Unreadable(f"{url}: a Word (.docx) file that can't be read ({e})") from None
    if not content.startswith(b"%PDF"):
        if other_kind(content):
            raise Unreadable(f"{url}: a file of a kind that isn't read (not a PDF or a Word .docx file)")
        raise FetchError(f"{url}: not a PDF or a Word (.docx) file")
    return content, None


def to_pdf(content: bytes) -> bytes:
    """A letter-size PDF of the Word document's text, one paragraph after another."""
    return text_pdf(paragraphs(content), "Publick (from a Word document)")


def same_text(one: bytes, other: bytes) -> bool:
    """Whether two PDFs hold the same words in the same order, whatever the spacing and line breaks between
    them (pipeline/pdftext.py's plain_text, from pypdf). A PDF with no text, or one that can't be read, is the
    same as no other."""
    words = [" ".join(pdftext.plain_text(pdf).split()) for pdf in (one, other)]
    return bool(words[0]) and words[0] == words[1]


def unchanged(latest: dict | None, content: bytes, converted: str | None, storage, folder: str) -> bool:
    """Whether a document fetched again, as a PDF (`content`, converted from `converted`: "docx", "html", or
    None for a city's own PDF), is the latest saved one, `latest`, a record kept in `folder` of `storage`
    (pipeline/documents.py). It is if the bytes are the same. A document converted here, from the same kind of
    file as the latest, is also unchanged if its text is the same as the saved copy's (same_text()): a new
    version of fpdf2 can write the same text as other bytes. A saved copy that can't be read counts as changed,
    so the document is saved again."""
    if latest is None:
        return False
    if latest["sha256"] == hashlib.sha256(content).hexdigest():
        return True
    if not converted or latest.get("converted_from") != converted or not latest.get("file"):
        return False
    try:
        saved = storage.get(folder, latest["file"])
    except (OSError, FetchError):
        return False
    return same_text(content, saved)
