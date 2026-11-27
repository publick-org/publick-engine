"""Minutes posted as a Word file, saved as a PDF of their text."""

import copy
import io
import zipfile

import pytest
from fakes import FakeResponse
from test_agendacenter import BASE, NOW, PAGE

from pipeline import fetch_meetings, fetch_minutes, pdftext, wordfile
from pipeline.fetch_meetings import load_store
from pipeline.http import FetchError

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(body: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>")
    return out.getvalue()


def p(*runs: str) -> str:
    return "<w:p>" + "".join(f"<w:r><w:t xml:space=\"preserve\">{r}</w:t></w:r>" for r in runs) + "</w:p>"


MINUTES = docx(
    p("Board of Appeal") + p("Minutes of July 15, 2026")
    + p("Motion to grant the variance at 12 Main Street ", "— approved 5–0.")
    + "<w:tbl><w:tr><w:tc>" + p("Member") + "</w:tc><w:tc>" + p("Vote") + "</w:tc></w:tr>"
    + "<w:tr><w:tc>" + p("José Rivera") + "</w:tc><w:tc>" + p("Yes") + "</w:tc></w:tr></w:tbl>"
    + "<w:p><w:r><w:t>Line one</w:t><w:br/><w:t>line two</w:t></w:r></w:p>"
)


@pytest.fixture
def malden(config):
    town = copy.deepcopy(config)
    town.pop("archive", None)
    town["meetings"] = {"agenda_center": {"base_url": BASE, "since": "2026-08-01"}}
    return town


def test_a_docx_is_recognized():
    assert wordfile.is_docx(MINUTES)
    assert not wordfile.is_docx(b"%PDF-1.7")
    assert not wordfile.is_docx(b"PK\x03\x04 not a zip")
    other = io.BytesIO()
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
    assert not wordfile.is_docx(other.getvalue())


def test_paragraphs_and_table_rows_in_order():
    assert wordfile.paragraphs(MINUTES) == [
        "Board of Appeal", "Minutes of July 15, 2026",
        "Motion to grant the variance at 12 Main Street — approved 5–0.",
        "Member | Vote", "José Rivera | Yes", "Line one\nline two",
    ]


def test_the_pdf_has_the_text():
    pdf = wordfile.to_pdf(MINUTES)
    assert pdf.startswith(b"%PDF")
    text = pdftext.plain_text(pdf)
    for words in ("Board of Appeal", "12 Main Street - approved 5-0.", "José Rivera | Yes", "line two"):
        assert words in text
    # Long enough to count as a document made with text: read from its text layer, not transcribed.
    long = wordfile.to_pdf(docx(p("The board voted to approve the minutes of the previous meeting.") * 10))
    assert pdftext.page_texts(long) is not None


class WordAgendaCenter:
    """An Agenda Center whose minutes are Word files."""

    def __init__(self, page: str, content: bytes):
        self.page, self.content = page, content

    def get(self, url):
        if "/AgendaCenter/Search/" in url:
            return FakeResponse(self.page.encode())
        return FakeResponse(self.content, {"content-disposition": 'attachment; filename="Minutes.docx"'})


OLD_DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(504)


def xlsx() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
    return out.getvalue()


@pytest.mark.parametrize("content", [OLD_DOC, b"{\\rtf1\\ansi agenda}", b"\xff\xd8\xff\xe0 a scan", b"II*\x00 a scan",
                                     xlsx()], ids=["doc", "rtf", "jpeg", "tiff", "xlsx"])
def test_a_file_of_another_kind_is_unreadable(content):
    """A reader can tell a file it won't ever read from a download that failed."""
    with pytest.raises(wordfile.Unreadable, match=r"^https://example.org/a: a file of a kind that isn't read") as e:
        wordfile.as_pdf(content, "https://example.org/a")
    assert isinstance(e.value, FetchError) and e.value.status is None


def test_a_word_file_that_cant_be_read_is_unreadable():
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w") as z:
        z.writestr("word/document.xml", "<w:document")
    with pytest.raises(wordfile.Unreadable, match=r"^https://example.org/a.docx: a Word \(.docx\) file that can't be read"):
        wordfile.as_pdf(broken.getvalue(), "https://example.org/a.docx")


@pytest.mark.parametrize("content", [b"<html>Sign in</html>", b"", docx(p("Agenda"))[:40]], ids=["html", "empty", "cut"])
def test_what_may_read_another_time_isnt_unreadable(content):
    """A sign-in page where a file should be, an empty body, a download cut short."""
    with pytest.raises(FetchError, match="not a PDF or a Word") as e:
        wordfile.as_pdf(content, "https://example.org/a")
    assert not isinstance(e.value, wordfile.Unreadable)
    assert wordfile.as_pdf(b"%PDF-1.7 ...", "https://example.org/a.pdf") == (b"%PDF-1.7 ...", None)


def test_word_minutes_are_saved_as_a_pdf(malden, tmp_path):
    fetch_meetings.run(malden, WordAgendaCenter(PAGE, b"%PDF-1.7"), tmp_path, now=NOW)
    summary = fetch_minutes.run_linked(malden, WordAgendaCenter(PAGE, MINUTES), tmp_path, now=NOW)
    assert summary["minutes_added"] == 3 and not summary["errors"]
    doc = load_store(tmp_path)["agendacenter-4400"]["minutes"][0]
    assert doc["converted_from"] == "docx" and doc["original_filename"] == "Minutes.docx"
    assert doc["has_text"] is True
    assert (tmp_path / "meetings" / "minutes" / "agendacenter-4400.pdf").read_bytes().startswith(b"%PDF")


def test_other_files_are_still_refused(malden, tmp_path):
    fetch_meetings.run(malden, WordAgendaCenter(PAGE, b"%PDF-1.7"), tmp_path, now=NOW)
    summary = fetch_minutes.run_linked(malden, WordAgendaCenter(PAGE, b"\xd0\xcf\x11\xe0 an old .doc"), tmp_path, now=NOW)
    assert summary["minutes_added"] == 0
    assert "not a PDF or a Word (.docx) file" in summary["errors"][0]


def test_word_agendas_are_saved_as_a_pdf(malden, tmp_path):
    fetch_meetings.run(malden, WordAgendaCenter(PAGE, MINUTES), tmp_path, now=NOW)
    finance = load_store(tmp_path)["agendacenter-4453"]
    agenda = finance["agendas"][0]
    assert agenda["converted_from"] == "docx" and agenda["has_text"] is True
    assert (tmp_path / "meetings" / "agendas" / f"{agenda['id']}.pdf").read_bytes().startswith(b"%PDF")


def test_other_agenda_files_are_still_refused(malden, tmp_path):
    fetch_meetings.run(malden, WordAgendaCenter(PAGE, b"\xd0\xcf\x11\xe0 an old .doc"), tmp_path, now=NOW)
    assert not load_store(tmp_path)["agendacenter-4453"].get("agendas")
