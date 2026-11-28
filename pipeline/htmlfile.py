"""A document posted as a web page's text, saved as a PDF of its text.

Everything after a download reads PDFs: the summary, the fact check, the roll calls, the page's text. Some
cities post an agenda or minutes as the text of a web page, with no file: an agenda typed into a ProudCity
meeting's page (pipeline/proudcity.py), or a document library's "Online/Text" post (pipeline/wpdocs.py).
Such a page is saved as a plain PDF of its paragraphs, list items, headings and table rows, with a text
layer, as a Word file is (pipeline/wordfile.py). Formatting and images are left out; the meeting page
still links the city's own page. A page fetched again with the same text isn't saved again as a new
version (wordfile.unchanged).
"""

from __future__ import annotations

import re

from pipeline import wordfile, wordpress

# Where a block of text ends: a paragraph, a list item, a heading, a table row.
BLOCK_END = re.compile(r"</(?:p|li|h[1-6]|tr|div|blockquote)>", re.I)


def paragraphs(fragment: str) -> list[str]:
    """A fragment's text, a paragraph (or list item, heading, table row) at a time, its line breaks kept,
    with tags, entities and invisible spaces taken out."""
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    found = []
    for block in BLOCK_END.split(fragment):
        lines = [" ".join(wordpress.rendered(line).split()) for line in re.sub(r"<[^>]+>", " ", block).split("\n")]
        joined = "\n".join(line for line in lines if line)
        if joined:
            found.append(joined)
    return found


def to_pdf(fragment: str, creator: str = "Publick (from a city web page)") -> bytes:
    """A letter-size PDF of a fragment's paragraphs (paragraphs())."""
    return wordfile.text_pdf(paragraphs(fragment), creator)
