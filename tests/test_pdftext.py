"""A posted PDF's own text: laid out for reading where the style is supported, and
checked word for word, so text is never lost or changed."""

from pathlib import Path
from unittest import mock

from pipeline import pdftext

FIXTURES = Path(__file__).parent / "fixtures"
LEGISTAR = (FIXTURES / "legistar_minutes.pdf").read_bytes()   # Malden City Council minutes, July 28, 2026
SCANNED = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()


def test_the_style_comes_from_the_software_that_made_the_pdf():
    assert pdftext.style(LEGISTAR) == "legistar"
    assert pdftext.style(SCANNED) is None


def test_a_legistar_document_is_laid_out_from_its_own_text():
    text, made_with = pdftext.readable(LEGISTAR)
    assert made_with == "legistar"
    # Its numbered sections are headings, and nothing from a page's running header or
    # footer is left in the text.
    assert "\n## 1. CALL TO ORDER\n" in text or text.startswith("## 1. CALL TO ORDER")
    assert "Page 2" not in text and "Printed on" not in text
    # The title at the top of page 1 stays, though the footer of every page repeats its words.
    assert text.startswith("# City of Malden\n")


def test_every_word_is_kept_in_order():
    text, _ = pdftext.readable(LEGISTAR)
    kept, dropped = pdftext._drop_running(pdftext.lines(LEGISTAR))
    assert pdftext.words(text.replace("·", " ")) == pdftext.words(" ".join(r["text"] for r in kept))
    # Only running headers, footers and page numbers are left out.
    assert all(len(r["text"]) < 80 for r in dropped)


def test_a_lost_line_fails_the_check():
    real = pdftext.layout
    first_body = next(r["text"] for r in pdftext.lines(LEGISTAR) if "called the meeting to order" in r["text"])
    with mock.patch.object(pdftext, "layout", lambda rows: real([r for r in rows if r["text"] != first_body])):
        assert pdftext.readable(LEGISTAR) == (None, "the laid-out text doesn't match the PDF's words")


def test_a_lost_page_fails_the_second_reading():
    real = pdftext.lines
    with mock.patch.object(pdftext, "lines", lambda pdf: [r for r in real(pdf) if r["page"] != 1]):
        text, why = pdftext.readable(LEGISTAR)
    assert text is None and why.startswith("a second reading of the PDF differs")


def test_a_scan_or_another_style_has_no_readable_text():
    assert pdftext.readable(SCANNED) == (None, "not a supported style")
    with mock.patch.object(pdftext, "STYLES", []):
        assert pdftext.readable(LEGISTAR) == (None, "not a supported style")


def test_plain_text_is_every_word_for_search():
    assert "called the meeting to order" in pdftext.plain_text(LEGISTAR)
    assert pdftext.plain_text(SCANNED) == ""
