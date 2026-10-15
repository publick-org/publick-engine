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


FOXIT = (FIXTURES / "foxit_minutes.pdf").read_bytes()   # Manchester Committee on Finance minutes, September 1, 2026


def test_a_foxit_document_is_laid_out_from_its_own_text():
    assert pdftext.style(FOXIT) == "foxit"
    text, made_with = pdftext.readable(FOXIT)
    assert made_with == "foxit"
    assert text.startswith("## COMMITTEE ON FINANCE\n")
    assert "Mayor Ruais called the meeting to order." in text


def test_a_word_split_by_its_fonts_subset_is_one_word():
    """Foxit writes "Program" as "Prog" in ArialMT and "ram" in ABCDEF+ArialMT."""
    text, _ = pdftext.readable(FOXIT)
    assert "Prog ram" not in text and "t he FY2027" not in text
    assert "“Amending the FY2027 Community Improvement Program" in text
    word = lambda text, x0, x1, font: {"text": text, "x0": x0, "x1": x1, "fontname": font, "size": 12.0}
    assert [w["text"] for w in pdftext.joined([word("Prog", 372.1, 397.3, "ArialMT"),
                                               word("ram,", 397.6, 421.6, "ABCDEF+ArialMT"),
                                               word("authorizing", 425.0, 480.0, "ABCDEF+ArialMT")])] == ["Program,", "authorizing"]
    # Words in different fonts, or apart by a space, stay apart.
    assert len(pdftext.joined([word("Present:", 72, 120, "Arial-BoldMT"), word("Aldermen", 120.2, 170, "ArialMT")])) == 2
    assert len(pdftext.joined([word("the", 72, 90, "ArialMT"), word("FY", 93.3, 105, "ABCDEF+ArialMT")])) == 2
