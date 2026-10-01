"""Parsers for a school board's meetings posted on a Finalsite website.

Finalsite is the website platform of many school districts. A district can
post each board meeting as a post in a list on its board's page: Wallingford,
Connecticut's Board of Education does, on
/board-of-education/board-of-education-meetings. The list holds the current
school year's posts, newest first, each a title only, typed by the district:
"September 28, 2026 - Board of Education Meeting", "Canceled - September 22,
2026 - Special Board of Education Meeting", "September 14, 2026 - Operations
Committee Meeting", "Canceled - September 3, 2026 - Special Board of Education
Meeting Agenda".

A post's body isn't in the page. Clicking a title opens it in a popup, which
the page's script loads from the list's element address,
/fs/elements/<element>?is_popup=true&post_id=<post>&show_post=true: a small
HTML fragment with the title, when the post was published (not when the
meeting is), and the body. The element and post numbers are in each title's
markup (id="fsArticle_<element>_<post>"), so nothing is configured but the
page's address.

The body links the meeting's documents, each labelled by the district:
"Agenda" and "Minutes" as Google Docs, a YouTube recording, a Google Drive
folder of backup materials, presentations as Drive files, and in Wallingford
"Motions", a Google Doc of the motions made. A district edits a Google Doc in
place, so draft minutes become approved minutes at the same address. A public
Doc exports as a PDF with a text layer (/export?format=pdf), with its title as
the file name.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from pipeline.filelist import DATE_IN_TITLE, date_of
from pipeline.meeting_names import find_board, parse_name

SOURCE = "finalsite"

ARTICLE = re.compile(r"<article\b[^>]*\bdata-post-id=\"(\d+)\"[^>]*>(.*?)</article>", re.S)
ELEMENT = re.compile(r'id="fsArticle_(\d+)_\d+"')
POST_LINK = re.compile(r'<a\b[^>]*class="fsPostLink"[^>]*>(.*?)</a>', re.S)
SLUG = re.compile(r'data-slug="([^"]*)"')
FRAGMENT_TITLE = re.compile(r'<div class="fsTitle[^"]*"[^>]*>(.*?)</div>', re.S)
BODY = re.compile(r'<div class="fsBody">(.*?)</div>\s*</article>', re.S)
LINK = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
PAGE_TITLE = re.compile(r"<title>(.*?)</title>", re.S)

GOOGLE_DOC = re.compile(r"^https?://docs\.google\.com/document/d/([\w-]{20,})")
DRIVE_FILE = re.compile(r"^https?://drive\.google\.com/(?:file/d/([\w-]{20,})|open\?id=([\w-]{20,}))")
YOUTUBE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?|live/|embed/)|youtu\.be/)")


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# ---- The board's page ---------------------------------------------------------------------

def parse_board(page: str) -> list[dict]:
    """Every post listed on the page: its element and post numbers, title, and slug."""
    posts = []
    for post_id, article in ARTICLE.findall(page):
        element, link = ELEMENT.search(article), POST_LINK.search(article)
        if not element or not link:
            continue
        slug = SLUG.search(link.group(0))
        posts.append({"element": element.group(1), "post_id": post_id, "title": text(link.group(1)),
                      "slug": html.unescape(slug.group(1)) if slug else ""})
    return posts


def more_pages(page: str) -> bool:
    """Whether the list is split into pages, of which only the first is read."""
    return "fsPagination" in page


def site_name(page: str) -> str:
    """'Board of Education Meetings - Wallingford Public Schools' -> 'Wallingford Public Schools'."""
    found = PAGE_TITLE.search(page)
    return text(found.group(1)).rpartition(" - ")[2] if found else ""


def post_url(page_url: str, element: str, post_id: str) -> str:
    """The address the page's script loads a post's body from."""
    return urljoin(page_url, f"/fs/elements/{element}?is_popup=true&post_id={post_id}&show_post=true&is_draft=false")


def parse_title(title: str, bodies: dict) -> dict | None:
    """The meeting a post is for: its date, board, status and whether it is special, or None
    for a title without a date or a board. `bodies` maps names in titles to the site's board
    names ("Operations Committee" -> "Board of Education Operations Committee"); the longest
    one in the title wins, as for calendars (pipeline.meeting_names)."""
    day = date_of(title)
    name = DATE_IN_TITLE.sub(" ", title)
    body = find_board(name, [], bodies) if day else None
    if not body:
        return None
    found = parse_name(name, [], bodies)
    return {"date": day, "body": body, "status": found["status"], "special": found["special"]}


# ---- A post ------------------------------------------------------------------------------

def parse_post(fragment: str, page_url: str) -> dict:
    """A post's title and the links in its body, each with its label."""
    title, body = FRAGMENT_TITLE.search(fragment), BODY.search(fragment)
    return {"title": text(title.group(1)) if title else "",
            "links": [{"url": urljoin(page_url, html.unescape(url).strip()), "text": text(label)}
                      for url, label in LINK.findall(body.group(1) if body else "")]}


def recording(url: str) -> dict | None:
    """A YouTube link's video id, and where in it the meeting starts. A channel's
    address (youtube.com/<channel>/live) has no video, so it is None."""
    if not YOUTUBE.match(url):
        return None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    video_id = query["v"][0] if "v" in query else parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[\w-]{6,}", video_id or "") or video_id in ("watch", "live", "embed"):
        return None
    start = re.fullmatch(r"(\d+)s?", query.get("t", [""])[0])
    return {"video_id": video_id, **({"video_start": int(start.group(1))} if start else {})}


def file_key(url: str) -> str | None:
    """The Google Doc's or Drive file's id, for a document that can be saved as a PDF."""
    found = GOOGLE_DOC.match(url) or DRIVE_FILE.match(url)
    return next((g for g in found.groups() if g), None) if found else None


def is_google_doc(url: str) -> bool:
    return bool(GOOGLE_DOC.match(url))


def is_document(url: str) -> bool:
    """A link to a single file that can be saved: a Google Doc, a Drive file, or a PDF.
    A Drive folder is never a document."""
    return bool(file_key(url)) or urlparse(url).path.lower().endswith(".pdf")


def download_url(url: str) -> str:
    """Where a document's PDF comes from: a Google Doc exported as a PDF, a Drive file's
    download, or the link itself."""
    doc = GOOGLE_DOC.match(url)
    if doc:
        return f"https://docs.google.com/document/d/{doc.group(1)}/export?format=pdf"
    drive = DRIVE_FILE.match(url)
    if drive:
        return f"https://drive.google.com/uc?export=download&id={drive.group(1) or drive.group(2)}"
    return url


def posted_on(url: str, source_name: str) -> str:
    """Where a document is posted, for "Agenda on Google Docs"."""
    if GOOGLE_DOC.match(url):
        return "Google Docs"
    if DRIVE_FILE.match(url):
        return "Google Drive"
    return f"the {source_name} website"


def kind_of(label: str) -> str | None:
    """'Agenda', 'Revised Agenda' -> "agenda"; 'Minutes', 'Draft Minutes' -> "minutes"; anything
    else ('Motions', 'Backup Folder', an addendum) -> None."""
    t = label.lower()
    if re.search(r"\bminutes\b", t):
        return "minutes"
    if re.search(r"\bagenda\b", t) and not re.search(r"\baddend", t):
        return "agenda"
    return None


def meeting_fields(post: dict) -> dict:
    """What a post's links say about its meeting: the agenda and minutes (agenda_url and
    minutes_url, the last of each if there are several), the recording (video_id, and
    video_start in seconds), and every other link, kept as `links`, never downloaded.
    An agenda that can't be saved (a Doc "published to the web", whose /pub page
    exports no PDF) is linked: documents_url is the agenda, saved or not."""
    fields, links, linked_agenda = {}, [], None
    for link in post["links"]:
        video = recording(link["url"])
        kind = kind_of(link["text"])
        if video:
            fields.update(video)
        elif kind and is_document(link["url"]):
            fields[f"{kind}_url"] = link["url"]
        else:
            links.append(link)
            linked_agenda = link["url"] if kind == "agenda" else linked_agenda
    if fields.get("agenda_url") or linked_agenda:
        fields["documents_url"] = fields.get("agenda_url") or linked_agenda
    fields["links"] = links
    return fields


def original_filename(disposition: str) -> str | None:
    """The file name a download gives: "filename*=UTF-8''AGENDA%20SEPTEMBER%2030.pdf" (the
    Doc's own title) when there is one, or else "filename=...". """
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    if encoded:
        return unquote(encoded.group(1)).strip()
    plain = re.search(r'filename="?([^";]+)', disposition)
    return plain.group(1).strip() if plain else None


# ---- The town's config ------------------------------------------------------------------

def check(config: dict) -> None:
    """A town's [finalsite_meetings] table, checked when its config is loaded."""
    settings = config.get("finalsite_meetings")
    if settings is None:
        return
    where = f"[finalsite_meetings] in config/{config['slug']}.toml"
    missing = [key for key in ("page_url", "source_name", "since", "bodies") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the board's page, the district's name, "
                         "the first meeting date to collect, and the boards its post titles name.")
    if not re.match(r"https?://", str(settings["page_url"])):
        raise SystemExit(f'{where}: page_url must be the board\'s page, like "https://www.example.k12.ct.us/board".')
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a date, like "2026-07-01".')
    bodies = settings["bodies"]
    if not isinstance(bodies, dict) or not all(isinstance(v, str) and v for v in bodies.values()):
        raise SystemExit(f'{where}: bodies must map names in post titles to board names, like '
                         '"Operations Committee" = "Board of Education Operations Committee".')
    for key in ("recheck_days", "max_posts_per_run"):
        if key in settings and not (isinstance(settings[key], int) and settings[key] > 0):
            raise SystemExit(f"{where}: {key} must be a whole number of at least 1.")
