"""Plain-English summaries of posted agendas and minutes, and their readable text.

For each saved PDF, a language model reads the document and writes a short
neutral summary: a preview for an agenda, a record of decisions for minutes.
Results are cached by the PDF's SHA-256 hash in data/summaries/, so an
unchanged document is never processed twice.

The readable text shown on each meeting page is the PDF's own words wherever
they can be used (pipeline/pdftext.py: a supported style, laid out and checked
word for word), at no cost. A scan (or a scanner's own recognized text), which
screen readers can't read, is transcribed by the model, but only after every
summary waiting, from what's left of the budget, a few pages a request so a
long document is never cut off. Any other PDF has text a screen reader can
read, so the page links it; search and the street lookup use its plain text.
Minutes of a body in the town's [officials] table also get their roll call
votes, read from the same text without AI (pipeline/votes.py).

A town with pages in another language also gets each summary translated, from
the English summary (pipeline/translate.py), within the same budget: right
after the new documents' summaries, the new documents' translations, then the
translations of older summaries already made, which cost about a tenth of a
summary, before the older documents still waiting for one. An older document
summarized in the run is translated after, if the budget allows.

A daily run of the network sends a town's summaries as one batch (the Message
Batches API), at half the price, instead of one at a time (PUBLICK_SUMMARY_BATCH=1).
The batch's id and documents are kept in data/summary-batch.json, and the job
collects its results once its other towns are done (PUBLICK_SUMMARY_COLLECT=1,
from pipeline.network run --collect), up to BATCH_WAIT after it was sent. One
not done by then is cancelled, and what it didn't finish is sent one at a time,
as without a batch, from the same run's budget: it never costs more than
sending them one at a time, and comes at most about BATCH_WAIT later. A batch a
job didn't collect (it was stopped) is collected by the town's next run.

Needs ANTHROPIC_API_KEY. Without it, the step is skipped.

What each month's summaries cost is kept in data/summary-costs.json, which
the network reads to keep every town's summaries within one monthly budget.
A network run gives each town its share for the run in two environment
variables: PUBLICK_SUMMARY_ALLOWANCE, for any document, and
PUBLICK_SUMMARY_BACKLOG_ALLOWANCE, for older documents. New documents
(upcoming agendas, and those fetched in the last two weeks for a recent
meeting) go first, and the backlog gets what the second allows. Without
them, only the town's own run limits apply.

Usage:
    python -m pipeline.summarize [--town gloucester] [--limit 20]
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pypdf import PdfReader

from pipeline import factcheck, listings, pdftext, translate, votes
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.files import write_atomic
from pipeline.http import FetchError

TRANSCRIPT_RULES = """Transcript rules:
- Copy the document's text character for character. Do not reword, correct, modernize, or change the spelling of anything (for example, keep "Councilor" if that is how it is written).
- Take particular care with digits and similar-looking characters (0 and O, 1 and l and I, 5 and S) in ZIP codes, phone numbers, meeting IDs, web addresses, dollar amounts, dates, vote counts, and case numbers.
- If a word or number cannot be read with confidence, write "[unreadable]" instead of guessing."""

SUMMARY_RULES = """Summary rules:
- Use only what the document says. Do not add background, predictions, opinions, likely outcomes, or categories the document does not use.
- Neutral tone. No adjectives that judge (such as important, controversial, significant).
- Plain English at about an 8th-grade reading level. Short sentences, one idea each.
- Keep names, dollar amounts, dates, and case or application numbers exactly as written."""

# Run limits for a town whose [summaries] table leaves them out.
DEFAULT_MAX_PER_RUN = 50
DEFAULT_MAX_COST_PER_RUN = 5.0

# What summaries cost, by month, in the town's data/.
LEDGER = "summary-costs.json"
# The network's share for this run, in dollars (see the module docstring).
ALLOWANCE_ENV = "PUBLICK_SUMMARY_ALLOWANCE"
BACKLOG_ALLOWANCE_ENV = "PUBLICK_SUMMARY_BACKLOG_ALLOWANCE"
# Dollars a run may spend drafting a town's own text in another language, even with its share
# spent: a batch of texts costs about a cent, and an undrafted text shows in English.
DRAFT_FLOOR = 0.05
# A document is new if it was fetched in the last NEW_DAYS for a meeting in
# the last RECENT_MEETING_DAYS (minutes are often posted weeks after the
# meeting). Everything else but upcoming agendas is backlog.
NEW_DAYS = 14
RECENT_MEETING_DAYS = 60

# Each kind of document has its own instructions and output. Bump a kind's
# version when its prompt or schema changes; cached results from an older
# version (or another model) are regenerated on the next run. An agenda's
# start_time and location were added without a bump, so older agendas aren't all
# made again: only an upcoming meeting's agenda, when the meeting's listing has
# no time (an Agenda Center's doesn't), is (needs_time). A kind's remake_since
# keeps what an older version made for meetings before that date: only later
# meetings' documents are made again (kept()).
KINDS = {
    "agenda": {
        # Version 5: the headline leads with what residents look for (projects and their addresses,
        # new rules, money), for search results and the cards shared on social media. Agendas of
        # meetings before remake_since keep version 4's: only those a share card shows are made again.
        "version": 5,
        "remake_since": "2026-09-08",
        "folder": "agendas",
        "max_tokens": 4000,
        "system": "You summarize public meeting agendas from a city government for residents. Agendas are often scanned images, so read every character carefully.\n\n"
                  + SUMMARY_RULES,
        "prompt": """This is the posted agenda for: {title}, {date}.

Return:
- headline: one sentence of at most 25 words saying what the meeting will take up. Lead with the business residents are most likely to look for, named as they would search for it: public hearings; projects and developments, with their street addresses or names; new rules, ordinances, or moratoriums; taxes, fees, budgets, and contracts, with their dollar amounts; schools. Put routine business (such as accepting a road, approving minutes, or hearing reports) after these, or leave it out. Start with the business itself, not with who is meeting (write "Public hearing on the 2026 Housing Compass Plan.", not "Board will hold a public hearing on ..."). Do not mention the board, the date, the time, or the place; readers already see those.
- summary: 1 or 2 short sentences on what the meeting will cover. Name the main business items. Do not repeat the board's name, the date, the time, or the place.
- items: each agenda item, in order, as short plain-English phrases. Skip routine items such as call to order, roll call, approval of minutes, and adjournment.
- start_time: when the meeting starts, as the agenda gives it, in 24-hour HH:MM form (for example 19:00 for 7:00 PM). An empty string if the agenda gives no time.
- location: where the meeting is held, as the agenda gives it, on one line: the room and building, and the street address if shown. For a meeting held only online, the service it names (for example "Zoom"). An empty string if the agenda gives no place.""",
        "schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}},
                "start_time": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["headline", "summary", "items", "start_time", "location"],
            "additionalProperties": False,
        },
    },
    "minutes": {
        # Version 3: each decision with its outcome and the minutes' own words for it, checked
        # without AI (pipeline/factcheck.py). Version 4: the headline leads with what residents look
        # for, as the agenda's (version 5) does. Minutes of meetings before remake_since keep the
        # version they have (3, or 2 before 2026-08-04), about 30 days back when version 4 shipped:
        # they're made again only if wanted, by moving it.
        "version": 4,
        "remake_since": "2026-09-08",
        "folder": "minutes",
        # Long scanned minutes take the model most of 16,000 tokens before it writes (Beverly's City
        # Council, 2026-03-16, 26 decisions): what isn't used isn't paid for.
        "max_tokens": 32000,
        "system": "You summarize the minutes of public meetings of a city government for residents. Minutes are often scanned images, so read every character carefully.\n\n"
                  + SUMMARY_RULES + """
- Report decisions only as the minutes record them. Include the vote count or roll call result when the minutes give one. If the minutes do not say how a matter ended, do not list it as a decision.
- Use the minutes' own verb for each outcome (approved, recommended, referred, continued, tabled, denied). A vote to recommend is not an approval.
- A motion that failed was not approved, whatever it proposed (to approve, to table, to refer): report it as failed.""",
        "prompt": """These are the posted minutes for: {title}, {date}.

Return:
- headline: one sentence of at most 25 words on what the meeting decided, or what it discussed if it decided nothing. Lead with the decisions residents are most likely to look for, named as they would search for them: projects and developments, with their street addresses or names; new rules, ordinances, or moratoriums; taxes, fees, budgets, and contracts, with their dollar amounts; schools. Put routine decisions (such as accepting a road, approving minutes, or accepting reports) after these, or leave them out. Start with the outcome itself, not with who met (write "Approved seven board appointments ...", not "Committee approved seven board appointments ..."). Do not mention the board, the date, the time, or the place; readers already see those.
- summary: 1 to 3 short sentences on what the meeting covered and what was decided. Do not repeat the board's name, the date, the time, or the place.
- is_minutes: true if this document is minutes of a meeting that took place; false if it is something else, such as an agenda or notice filed under minutes.
- decisions: each motion, vote, or other decision the minutes record, in order. Skip procedural motions such as adjourning or accepting the agenda. For each:
  - decision: a short plain-English sentence that includes the outcome (for example "Approved ... 5-0" or "Continued ... to October 22, 2026").
  - outcome: what happened, in one word: approved (also adopted, accepted, granted, or passed), denied (also rejected, or any motion that failed), tabled, continued, referred, recommended, withdrawn, or other (anything else, such as placed on file or no action taken).
  - quote: the minutes' own words that record this decision, copied exactly, character for character: the item or motion, with its number, address, or amount if the minutes give one, and how it ended. One passage of at most 60 words; if the motion and how it ended are far apart, give both, joined by "...". Never reword, shorten, or correct the minutes' words.""",
        "schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "is_minutes": {"type": "boolean"},
                "decisions": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "decision": {"type": "string"},
                        "outcome": {"type": "string", "enum": list(factcheck.OUTCOMES)},
                        "quote": {"type": "string"},
                    },
                    "required": ["decision", "outcome", "quote"],
                    "additionalProperties": False,
                }},
            },
            "required": ["headline", "summary", "is_minutes", "decisions"],
            "additionalProperties": False,
        },
    },
}

# Readable text by the model, for a document whose own text can't be used:
# a few pages a request, so no response is cut off however long the document.
TRANSCRIBE = {
    "version": 1,
    "max_tokens": 32000,
    "pages": 6,
    "system": "You transcribe public meeting agendas and minutes from a city government into accessible text for residents. They are often scanned images, so read every character carefully.\n\n"
              + TRANSCRIPT_RULES,
    "prompt": """These are pages {first} to {last} of {pages} of the {kind} for: {title}, {date}.

Return:
- transcript: the full text of these pages in reading order, as Markdown. Use headings for the document's own headings and lists for its lists. Leave out stamps, seals, page numbers, and running headers and footers, but keep the clerk's posting date if shown.""",
    "schema": {
        "type": "object",
        "properties": {"transcript": {"type": "string"}},
        "required": ["transcript"],
        "additionalProperties": False,
    },
}
# Documents laid out from their own text each run (no model, no cost), for summaries saved before.
TEXT_PER_RUN = 60

# Documents longer or larger than this are not sent; the page links to the original.
# The model reads PDFs of up to 600 pages; 200 keeps one request's input (each page is
# read as text and as an image) to about 400,000 tokens, under two dollars. Agendas with
# their packets run past 100 pages (Bangor's and Beverly's in October 2026: 135 and 121).
MAX_PAGES = 200
# Documents found over MAX_PAGES, by hash, with their page count, in the town's data/: not
# downloaded again each run, and not counted as waiting for a summary (pipeline/freshness.py).
TOO_LONG = "summary-too-long.json"
# What a summary is likely to cost, before it's sent, so a run's allowance isn't overshot by one large
# document: input tokens per page and fixed, and output tokens, fixed and per page (minutes write more
# the longer they are; an agenda's summary stays short). From 60 documents across the ten towns in
# October 2026: input a median 2,600 tokens a page (to 3,700); output for agendas under 2,500, and for
# minutes up to about 1,200 a page (30 pages, 36,849). Erring high: a document that waits goes on a
# run with more room, while one that overshoots is paid for anyway.
ESTIMATE = {"input_fixed": 1500, "input_per_page": 3000,
            "output": {"agenda": (2500, 0), "minutes": (2000, 1200)}}
# The API takes requests up to 32 MB, and base64 makes a PDF a third larger.
MAX_BYTES = 22_000_000


def too_large(doc: dict) -> bool:
    return (doc.get("bytes") or 0) > MAX_BYTES


def too_long(data_dir: Path, doc: dict) -> bool:
    """Whether the document was found longer than MAX_PAGES (as it is now: raising it lets them through)."""
    path = data_dir / TOO_LONG
    pages = json.loads(path.read_text(encoding="utf-8")).get(doc["sha256"]) if path.exists() else None
    return bool(pages and pages > MAX_PAGES)


def record_too_long(data_dir: Path, sha256: str, pages: int) -> None:
    path = data_dir / TOO_LONG
    found = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    found[sha256] = pages
    write_atomic(path, json.dumps(found, indent=2, sort_keys=True) + "\n")


# Documents whose request stopped early (cut off at max_tokens, or refused), by hash, in the town's
# data/: for each thing asked of the model ("summary" or "transcript"), the model and prompt version
# asked and how many tries stopped early. Each try is paid for though nothing is saved, so after
# MAX_TRIES the document is left out of what's waiting, and its page links the original, until the
# model or the prompt changes.
STOPPED_EARLY = "summary-stopped-early.json"
MAX_TRIES = 2


def stopped_early_tries(data_dir: Path, sha256: str, what: str, model: str, version: int) -> int:
    """How many requests for this document stopped early, with this model and prompt version."""
    path = data_dir / STOPPED_EARLY
    entry = (json.loads(path.read_text(encoding="utf-8")).get(sha256, {}).get(what) if path.exists() else None)
    if not entry or entry["model"] != model or entry["version"] != version:
        return 0
    return entry["tries"]


def gave_up(data_dir: Path, sha256: str, what: str, model: str, version: int) -> bool:
    return stopped_early_tries(data_dir, sha256, what, model, version) >= MAX_TRIES


def summary_gave_up(data_dir: Path, doc: dict, kind: str, model: str) -> bool:
    """Whether the document's summary, with this model and the kind's current prompt, was given up on."""
    return gave_up(data_dir, doc["sha256"], "summary", model, KINDS[kind]["version"])


def record_stopped_early(data_dir: Path, sha256: str, what: str, model: str, version: int) -> int:
    """Count a request that stopped early; returns how many have, with this model and prompt version."""
    path = data_dir / STOPPED_EARLY
    found = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    tries = stopped_early_tries(data_dir, sha256, what, model, version) + 1
    found.setdefault(sha256, {})[what] = {"model": model, "version": version, "tries": tries}
    write_atomic(path, json.dumps(found, indent=2, sort_keys=True) + "\n")
    return tries


# A town's summaries sent as one batch, at BATCH_PRICE of a request's own price (see the module docstring).
BATCH_ENV = "PUBLICK_SUMMARY_BATCH"
COLLECT_ENV = "PUBLICK_SUMMARY_COLLECT"
BATCH_FILE = "summary-batch.json"
BATCH_PRICE = 0.5
# How long after it's sent a batch is waited for before it's cancelled. Measured on the minutes set at
# the daily runs' hour (pipeline/evaluate_batch.py, 2026-10-08 to 10): 4 to 17 minutes.
BATCH_WAIT = timedelta(minutes=30)
# How long a cancelled batch is waited for, to end and give what it finished.
CANCEL_WAIT = timedelta(minutes=10)
POLL_SECONDS = 15
# The PDFs in one batch, well under the API's 256 MB once base64 adds a third; past it, one at a time.
BATCH_MAX_BYTES = 150_000_000


def summaries_dir(data_dir: Path) -> Path:
    return data_dir / "summaries"


def cached(data_dir: Path, sha256: str, model: str, kind: str = "agenda", current: bool = True) -> dict | None:
    """The saved result for a document, if it was made by this model and the current prompt.

    With current=False, any saved result of this kind is returned, so the site
    keeps showing a summary while a newer prompt's version is waiting to be made.
    """
    path = summaries_dir(data_dir) / f"{sha256}.json"
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    version = KINDS[kind]["version"]
    if record.get("kind", "agenda") != kind:
        return None
    if current and (record.get("model") != model or record.get("prompt_version") != version):
        return None
    return record


def kept(data_dir: Path, sha256: str, model: str, kind: str, meeting: dict) -> bool:
    """Whether a summary made with an earlier version of the prompt stays: its meeting is before
    the kind's remake_since, and the same model made it."""
    since = KINDS[kind].get("remake_since")
    if not since or meeting["date"] >= since:
        return False
    record = cached(data_dir, sha256, model, kind, current=False)
    return bool(record and record.get("model") == model)


def split_decisions(result: dict) -> dict:
    """A minutes summary as saved: its decisions as sentences, as every page and translation reads
    them, and each one's outcome and quote beside them, in "decision_evidence"."""
    if not result.get("decisions") or not isinstance(result["decisions"][0], dict):
        return result
    entries = result["decisions"]
    return {**result, "decisions": [d["decision"] for d in entries],
            "decision_evidence": [{"outcome": d["outcome"], "quote": d["quote"]} for d in entries]}


def needs_time(kind: str, meeting: dict, record: dict, today: str) -> bool:
    """An upcoming meeting with no time listed, whose agenda summary is from before agendas gave one."""
    return (kind == "agenda" and meeting["date"] >= today and not meeting.get("start_time")
            and "start_time" not in record)


def pending_documents(data_dir: Path, today: str, model: str, since: str | None = None) -> list[tuple[str, dict, dict]]:
    """Latest agenda and minutes of each meeting without a current summary, for meetings on or
    after since ([summaries] since; None for all).

    Order: agendas for upcoming meetings, then minutes (newest meeting first),
    then agendas for past meetings."""
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    # A meeting listed in more than one place is summarized once, from its current agenda.
    store, _ = listings.combined(store)
    todo, seen = [], set()
    for meeting in store.values():
        # Older meetings keep their records and documents, without a summary.
        if since and meeting["date"] < since:
            continue
        for kind, field in (("agenda", "agendas"), ("minutes", "minutes")):
            if not meeting.get(field):
                continue
            doc = meeting[field][-1]
            # Several meetings can share one document; process it once.
            if (doc["sha256"] in seen or too_large(doc) or too_long(data_dir, doc)
                    or summary_gave_up(data_dir, doc, kind, model)):
                continue
            record = cached(data_dir, doc["sha256"], model, kind)
            if record and not needs_time(kind, meeting, record, today):
                continue
            if not record and kept(data_dir, doc["sha256"], model, kind, meeting):
                continue
            seen.add(doc["sha256"])
            todo.append((kind, meeting, doc))
    date = lambda t: t[1]["date"]
    upcoming = sorted((t for t in todo if t[0] == "agenda" and date(t) >= today), key=date)
    minutes = sorted((t for t in todo if t[0] == "minutes"), key=date, reverse=True)
    past = sorted((t for t in todo if t[0] == "agenda" and date(t) < today), key=date, reverse=True)
    return upcoming + minutes + past


def is_new(item: tuple[str, dict, dict], now: datetime) -> bool:
    kind, meeting, doc = item
    today = now.date().isoformat()
    if kind == "agenda" and meeting["date"] >= today:
        return True
    fetched = doc.get("fetched_at")
    if not fetched or datetime.fromisoformat(fetched) < now - timedelta(days=NEW_DAYS):
        return False
    return meeting["date"] >= (now - timedelta(days=RECENT_MEETING_DAYS)).date().isoformat()


def record_cost(record: dict, settings: dict) -> float:
    """What a saved summary cost: as recorded, or from its token counts for one saved before costs were."""
    if "cost" in record:
        return record["cost"]
    return cost(record["usage"], settings) if "usage" in record else 0.0


def update_ledger(data_dir: Path, settings: dict, month: str, failed_cost: float = 0.0,
                  transcript_cost: float = 0.0, replaced_cost: float = 0.0) -> dict:
    """Recount the summaries saved this month (and any month the ledger doesn't have yet) and save the ledger.

    Earlier months are kept as they were: a summary made again replaces its file,
    so counting them again would lose what the first one cost. failed_cost is
    what this run paid for requests that were cut off, which leave no file;
    replaced_cost what summaries made earlier this month cost, which this run
    made again (their files replaced, so the recount no longer finds them); and
    transcript_cost what it paid for transcriptions, counted in the month they're made."""
    path = data_dir / LEDGER
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    counted: dict[str, dict] = {}
    folder = summaries_dir(data_dir)
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        record = json.loads(file.read_text(encoding="utf-8"))
        at = record.get("generated_at", "")[:7]
        if at and (at == month or at not in ledger):
            row = counted.setdefault(at, {"cost": 0.0, "documents": 0})
            row["cost"] += record_cost(record, settings)
            row["documents"] += 1
    for at, row in counted.items():
        ledger[at] = {**ledger.get(at, {}), "cost": round(row["cost"], 4), "documents": row["documents"]}
    # Translations, counted the same way.
    for at, row in translate.month_counts(data_dir).items():
        if at == month or "translations" not in ledger.get(at, {}):
            ledger[at] = {"cost": 0.0, "documents": 0, **ledger.get(at, {}),
                          "translation_cost": round(row["cost"], 4), "translations": row["translations"]}
    for key, paid in (("failed_cost", failed_cost), ("transcript_cost", transcript_cost), ("replaced_cost", replaced_cost)):
        if paid:
            row = ledger.setdefault(month, {"cost": 0.0, "documents": 0})
            row[key] = round(row.get(key, 0.0) + paid, 4)
    ledger = dict(sorted(ledger.items()))
    write_atomic(path, json.dumps(ledger, indent=2) + "\n")
    return ledger


# What a month's row of the ledger counts as paid for.
LEDGER_COSTS = ("cost", "failed_cost", "transcript_cost", "translation_cost", "replaced_cost")


def month_cost(ledger: dict, month: str) -> float:
    """Everything paid for in a month: summaries (and those made again since), cut-off requests,
    transcriptions, and translations."""
    row = ledger.get(month, {})
    return sum(row.get(key, 0.0) for key in LEDGER_COSTS)


# Where, among the documents a run summarizes, translations are made: after the new documents'
# summaries, before the older documents'.
TRANSLATE = object()


def doc_key(item: tuple[str, dict, dict]) -> str:
    return item[2]["sha256"]


class StoppedEarly(RuntimeError):
    """A response that ended before it was complete. Its tokens are still paid for."""

    def __init__(self, reason: str, usage: dict, text: str = ""):
        super().__init__(f"stopped early: {reason}")
        self.usage = usage
        # What the response had written when it stopped, to see why.
        self.text = text


def page_count(pdf: bytes) -> int | None:
    try:
        return len(PdfReader(io.BytesIO(pdf)).pages)
    except Exception:
        return None


def summary_request(model: str, kind: str, pdf: bytes, title: str, date: str, effort: str | None = None) -> dict:
    """The request for a document's summary: as a run sends it, or as one request of a batch
    (pipeline/evaluate_batch.py). Without an effort, the model's default."""
    spec = KINDS[kind]
    return {
        "model": model,
        "max_tokens": spec["max_tokens"],
        "system": spec["system"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": base64.standard_b64encode(pdf).decode("ascii")}},
                {"type": "text", "text": spec["prompt"].format(title=title, date=date)},
            ],
        }],
        "output_config": {**({"effort": effort} if effort else {}),
                          "format": {"type": "json_schema", "schema": spec["schema"]}},
    }


def summary_result(response) -> tuple[dict, dict]:
    """A summary response's result and its usage; StoppedEarly if it didn't finish."""
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    text = next((b.text for b in response.content if b.type == "text"), "")
    if response.stop_reason != "end_turn":
        raise StoppedEarly(response.stop_reason, usage, text)
    return json.loads(text), usage


def summarize_pdf(client, model: str, kind: str, pdf: bytes, title: str, date: str) -> tuple[dict, dict]:
    # Streaming avoids HTTP timeouts on long transcripts.
    with client.messages.stream(**summary_request(model, kind, pdf, title, date)) as stream:
        response = stream.get_final_message()
    return summary_result(response)


def page_range(pdf: bytes, first: int, last: int) -> bytes:
    """Pages first..last (counting from 1) as a PDF of their own."""
    from pypdf import PdfWriter
    reader = PdfReader(io.BytesIO(pdf))
    writer = PdfWriter()
    for page in reader.pages[first - 1:last]:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def transcribe_pdf(client, model: str, kind: str, pdf: bytes, title: str, date: str) -> tuple[str, dict]:
    """The model's transcription of a whole document, a few pages a request."""
    pages = page_count(pdf) or 1
    step = TRANSCRIBE["pages"]
    parts, usage = [], {"input_tokens": 0, "output_tokens": 0}
    for first in range(1, pages + 1, step):
        last = min(first + step - 1, pages)
        chunk = pdf if (first, last) == (1, pages) else page_range(pdf, first, last)
        with client.messages.stream(
            model=model,
            max_tokens=TRANSCRIBE["max_tokens"],
            system=TRANSCRIBE["system"],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                    "data": base64.standard_b64encode(chunk).decode("ascii")}},
                    {"type": "text", "text": TRANSCRIBE["prompt"].format(first=first, last=last, pages=pages,
                                                                         kind=kind, title=title, date=date)},
                ],
            }],
            output_config={"format": {"type": "json_schema", "schema": TRANSCRIBE["schema"]}},
        ) as stream:
            response = stream.get_final_message()
        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens
        if response.stop_reason != "end_turn":
            raise StoppedEarly(response.stop_reason, usage)
        parts.append(json.loads(next(b.text for b in response.content if b.type == "text"))["transcript"].strip())
    return "\n\n".join(parts), usage


def own_text(record: dict, pdf: bytes) -> dict:
    """The record with the document's own text, where it can be used: laid out as its
    readable text (replacing a transcription). Otherwise, with no transcription yet, its
    plain text for search, and whether it's a scan, which the model is to transcribe."""
    text, why = pdftext.readable(pdf)
    record = {**record, "text_version": pdftext.VERSION}
    record.pop("needs_transcript", None)
    if text:
        record.update(transcript=text, transcript_source="pdf")
        record.pop("plain_text", None)
    elif not record.get("transcript"):
        record["plain_text"] = pdftext.plain_text(pdf)
        record["text_note"] = why
        record["needs_transcript"] = pdftext.page_texts(pdf) is None
    return record


def save_record(data_dir: Path, sha256: str, record: dict) -> None:
    path = summaries_dir(data_dir) / f"{sha256}.json"
    write_atomic(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def summarized_documents(data_dir: Path) -> list[tuple[str, dict, dict, dict]]:
    """(kind, meeting, document, saved record) for each meeting's latest agenda and minutes
    that have a summary, newest meeting first."""
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    store, _ = listings.combined(store)
    out, seen = [], set()
    for meeting in store.values():
        for kind, field in (("agenda", "agendas"), ("minutes", "minutes")):
            if not meeting.get(field):
                continue
            doc = meeting[field][-1]
            if doc["sha256"] in seen:
                continue
            record = cached(data_dir, doc["sha256"], "", kind, current=False)
            if record:
                seen.add(doc["sha256"])
                out.append((kind, meeting, doc, record))
    return sorted(out, key=lambda t: t[1]["date"], reverse=True)


def estimated_cost(kind: str, pages: int, settings: dict) -> float:
    """What summarizing a document of this kind and length is likely to cost (ESTIMATE), at most what
    its max_tokens allow."""
    fixed, per_page = ESTIMATE["output"][kind]
    output = min(fixed + per_page * pages, KINDS[kind]["max_tokens"])
    return cost({"input_tokens": ESTIMATE["input_fixed"] + ESTIMATE["input_per_page"] * pages,
                 "output_tokens": output}, settings)


def cost(usage: dict, settings: dict) -> float:
    return (usage["input_tokens"] * settings["input_price"] + usage["output_tokens"] * settings["output_price"]) / 1e6


def summary_settings(config: dict) -> dict:
    """The town's [summaries] table, with the run limits filled in when it leaves them out."""
    return {"max_per_run": DEFAULT_MAX_PER_RUN, "max_cost_per_run": DEFAULT_MAX_COST_PER_RUN, **config["summaries"]}


def save_summary(config: dict, data_dir: Path, kind: str, meeting: dict, doc: dict, pdf: bytes, result: dict,
                 usage: dict, paid: float, settings: dict, now: datetime, words: set[str]) -> float:
    """Save a summary just made, with the document's own text, its roll calls, and its fact check.
    Returns what an earlier summary of the document made this month cost: its file is replaced, so the
    ledger's recount no longer finds it."""
    record = {
        **split_decisions(result),
        "kind": kind,
        "source_url": doc["source_url"],
        "source_sha256": doc["sha256"],
        "model": settings["model"],
        "prompt_version": KINDS[kind]["version"],
        "generated_at": now.isoformat(timespec="seconds"),
        "usage": usage,
        "cost": round(paid, 6),
    }
    record = votes.read(own_text(record, pdf), pdf, votes.members_for(config, meeting["body"]))
    # A summary made earlier this month and made again (an agenda that now gives its time, a new
    # prompt): its file is replaced, so what it cost is counted here.
    replaced_cost = 0.0
    earlier = summaries_dir(data_dir) / f"{doc['sha256']}.json"
    if earlier.exists():
        replaced = json.loads(earlier.read_text(encoding="utf-8"))
        if replaced.get("generated_at", "")[:7] == now.strftime("%Y-%m"):
            replaced_cost = record_cost(replaced, settings)
    if record.get("is_minutes") is not False:
        # Checked as it's saved, so no summary is ever shown unchecked (pipeline/factcheck.py).
        words |= translate.summary_words(record)
        record["fact_check"] = factcheck.check(record, kind, factcheck.pages(pdf), words)
    save_record(data_dir, doc["sha256"], record)
    return replaced_cost


def at_batch_price(settings: dict) -> dict:
    return {**settings, "input_price": settings["input_price"] * BATCH_PRICE,
            "output_price": settings["output_price"] * BATCH_PRICE}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_batch(data_dir: Path) -> dict | None:
    path = data_dir / BATCH_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def send_batch(client, data_dir: Path, settings: dict, queued: list[tuple[str, dict, dict, bytes]], older: set[str],
               sent_at: datetime, spent_before: float, backlog_before: float) -> dict:
    """Send the documents' summaries as one batch and keep what it needs to be collected in data/: each
    document's meeting and file, and what the run had spent before it, so the run that collects it goes
    on from there."""
    created = client.messages.batches.create(requests=[
        {"custom_id": f"doc-{i}", "params": summary_request(settings["model"], kind, pdf, meeting["title"], meeting["date"])}
        for i, (kind, meeting, doc, pdf) in enumerate(queued)])
    pending = {
        "id": created.id, "sent_at": sent_at.isoformat(timespec="seconds"), "model": settings["model"],
        "spent_before": round(spent_before, 6), "backlog_before": round(backlog_before, 6),
        "documents": [{"custom_id": f"doc-{i}", "kind": kind, "older": doc["sha256"] in older,
                       "meeting": {k: meeting[k] for k in ("title", "date", "body") if k in meeting},
                       "doc": {k: doc[k] for k in ("id", "sha256", "file", "source_url")}}
                      for i, (kind, meeting, doc, _) in enumerate(queued)],
    }
    write_atomic(data_dir / BATCH_FILE, json.dumps(pending, indent=2) + "\n")
    return pending


def collect_batch(config: dict, client, data_dir: Path, storage, settings: dict, now: datetime, words: set[str],
                  clock=utc_now, sleep=time.sleep) -> dict:
    """The town's batch of summaries, waited for until BATCH_WAIT after it was sent (cancelled if it isn't
    done by then), and each summary it made saved as one sent alone would be. What it didn't make (an
    error, cancelled) is left waiting, for the run to send one at a time. A batch that can't be collected
    (the API can't be reached, or a cancelled one doesn't end) is kept for the next run, and its documents
    are listed in "waiting", so they aren't sent twice."""
    pending = load_batch(data_dir)
    out = {"done": 0, "paid": 0.0, "paid_backlog": 0.0, "failed_cost": 0.0, "replaced_cost": 0.0,
           "tokens": {"input_tokens": 0, "output_tokens": 0}, "errors": [], "waiting": set()}
    if not pending:
        return out
    batches = client.messages.batches
    shas = {d["doc"]["sha256"] for d in pending["documents"]}
    try:
        deadline = datetime.fromisoformat(pending["sent_at"]) + BATCH_WAIT
        batch = batches.retrieve(pending["id"])
        while batch.processing_status != "ended" and clock() < deadline:
            sleep(POLL_SECONDS)
            batch = batches.retrieve(pending["id"])
        if batch.processing_status != "ended":
            batches.cancel(pending["id"])
            out["errors"].append(f"the batch of summaries wasn't done {BATCH_WAIT.seconds // 60} minutes after it was "
                                 "sent, so it was cancelled; what it didn't finish is sent one at a time")
            stop = clock() + CANCEL_WAIT
            while batch.processing_status != "ended" and clock() < stop:
                sleep(POLL_SECONDS)
                batch = batches.retrieve(pending["id"])
            if batch.processing_status != "ended":
                out["errors"].append(f"the cancelled batch {pending['id']} hasn't ended; the next run collects it")
                out["waiting"] = shas
                return out
        results = {r.custom_id: r.result for r in batches.results(pending["id"])}
    except Exception as e:
        out["errors"].append(f"the batch of summaries {pending['id']} couldn't be collected ({e}); the next run tries again")
        out["waiting"] = shas
        return out
    prices = at_batch_price(settings)
    for entry in pending["documents"]:
        kind, meeting, doc = entry["kind"], entry["meeting"], entry["doc"]
        result = results.get(entry["custom_id"])
        if result is None or result.type != "succeeded":
            # Not made, and not paid for: it's sent one at a time.
            error = getattr(getattr(getattr(result, "error", None), "error", None), "message", None)
            out["errors"].append(f"{kind} {doc['id']}: not made in the batch ({result.type if result else 'missing'}"
                                 + (f": {error}" if error else "") + "); sent one at a time")
            continue
        try:
            made, usage = summary_result(result.message)
        except StoppedEarly as e:
            paid = cost(e.usage, prices)
            out["paid"] += paid
            out["failed_cost"] += paid
            if entry["older"]:
                out["paid_backlog"] += paid
            tries = record_stopped_early(data_dir, doc["sha256"], "summary", pending["model"], KINDS[kind]["version"])
            out["errors"].append(f"{kind} {doc['id']}: {e}" + (f"; not tried again after {tries} tries"
                                                               if tries >= MAX_TRIES else ""))
            continue
        except Exception as e:
            # A response that can't be read (not JSON): paid for, and sent again one at a time.
            usage = getattr(result.message, "usage", None)
            paid = cost({"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}, prices) if usage else 0.0
            out["paid"] += paid
            out["failed_cost"] += paid
            out["errors"].append(f"{kind} {doc['id']}: the batch's summary couldn't be read ({e}); sent one at a time")
            continue
        paid = cost(usage, prices)
        out["paid"] += paid
        if entry["older"]:
            out["paid_backlog"] += paid
        try:
            pdf = storage.get(KINDS[kind]["folder"], doc["file"])
        except FetchError as e:
            # Paid for, but it can't be saved without its document's text and check: made again later.
            out["failed_cost"] += paid
            out["errors"].append(f"{kind} {doc['id']}: {e}")
            continue
        out["replaced_cost"] += save_summary(config, data_dir, kind, meeting, doc, pdf, made, usage, paid,
                                             {**settings, "model": pending["model"]}, now, words)
        for k in out["tokens"]:
            out["tokens"][k] += usage[k]
        out["done"] += 1
    (data_dir / BATCH_FILE).unlink()
    return out


def run(config: dict, client, data_dir: Path, limit: int, now: datetime | None = None,
        allowance: float | None = None, backlog_allowance: float | None = None, batch: bool = False,
        collecting: bool = False, clock=utc_now, sleep=time.sleep) -> dict:
    """Summarize up to limit documents, new ones first, within the town's run limits and the
    network's allowances for this run (None: no network limit).

    A batch of summaries the town has waiting is collected first. With batch, the documents are sent as
    one batch, left for the job to collect. collecting is the job's run that collects the batch its first
    run sent: it goes on from what that run had spent, and sends one at a time what the batch didn't make."""
    settings = summary_settings(config)
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    storage = open_documents(config, data_dir)
    done, errors, tokens, spent, spent_backlog, failed_cost = 0, [], {"input_tokens": 0, "output_tokens": 0}, 0.0, 0.0, 0.0
    stopped = None
    month, replaced_cost, too_costly = now.strftime("%Y-%m"), 0.0, []
    # The words the fact check takes as no one's name, as the town's summaries use them.
    words = translate.town_words(data_dir)
    pending = load_batch(data_dir)
    if collecting and not pending:
        # Nothing to collect: the run that sent nothing has already done the rest.
        return {"summarized": 0, "collected": 0, "queued": 0, "laid_out": 0, "transcribed": 0, "translated": 0,
                "drafted_texts": 0, "remaining": 0, "errors": [], "stopped": None, "estimated_cost": 0.0,
                "month_cost": None, **tokens}
    collected, waiting = 0, set()
    if pending and "input_price" in settings and "output_price" in settings:
        got = collect_batch(config, client, data_dir, storage, settings, now, words, clock, sleep)
        collected, waiting = got["done"], got["waiting"]
        errors += got["errors"]
        failed_cost += got["failed_cost"]
        replaced_cost += got["replaced_cost"]
        for k in tokens:
            tokens[k] += got["tokens"][k]
        if collecting:
            # The same run's budget, as its first part left it.
            spent, spent_backlog = pending["spent_before"] + got["paid"], pending["backlog_before"] + got["paid_backlog"]
        else:
            # An earlier run's batch, paid for now.
            spent, spent_backlog = got["paid"], got["paid_backlog"]
    todo = [t for t in pending_documents(data_dir, now.date().isoformat(), settings["model"], settings.get("since"))
            if doc_key(t) not in waiting]
    new = [t for t in todo if is_new(t, now)]
    backlog = [t for t in todo if not is_new(t, now)]
    older = {doc["sha256"] for _, _, doc in backlog}
    # With batch, the documents that fit in one; and while an earlier batch is still out, none.
    batching = batch and not waiting and not collecting
    queued, queued_bytes, queued_cost, queued_backlog = [], 0, {}, 0.0
    work = (new + backlog)[:limit]
    # Translations come right after the new documents' summaries, new documents' first: an older
    # summary already on the site waits for its translation no longer than a new one.
    work.insert(sum(1 for item in work if doc_key(item) not in older), TRANSLATE)
    if "input_price" not in settings or "output_price" not in settings:
        # Without prices the spending limit can't be enforced, so nothing is sent.
        work = []
        errors.append("[summaries] needs input_price and output_price for the spending limit; nothing summarized")
    translated, translation_cost = 0, 0.0
    # The town's own text and names that its pages in another language would show in English
    # (a new board, a new 311 category, a new town's config), drafted first: they cost a fraction
    # of a summary, and the build that follows shows them.
    drafted_texts = 0
    for lang in translate.languages(config) if "input_price" in settings and "output_price" in settings else []:
        tsettings = translate.settings(config)
        if tsettings["input_price"] is None or tsettings["output_price"] is None:
            break
        from pipeline.build_site import needed_texts
        try:
            needed = needed_texts(config, data_dir, lang, now)
            texts = needed["config"] + needed["data"]
            if texts:
                # Past the budget too, up to DRAFT_FLOOR: a few texts cost a fraction of a cent, and
                # without them a page shows English.
                left = None if allowance is None else max(allowance - spent, DRAFT_FLOOR)
                count, paid = translate.draft_texts(client, config, data_dir, lang, texts, now, cost, left)
                drafted_texts += count
                spent += paid
                translation_cost += paid
        except Exception as e:
            if "credit balance" in str(e).lower():
                errors.append("stopped: the Anthropic account is out of credit; summaries resume when credit is added")
                stopped = "out of credit"
                work = []
                break
            errors.append(f"{lang} drafts of the town's text: {e}")

    def translations(only_new: bool) -> None:
        """Translate the summaries that need it, new documents' or the rest, within this run's budget."""
        nonlocal spent, spent_backlog, failed_cost, translated, translation_cost, stopped
        langs = translate.languages(config)
        if not langs:
            return
        tsettings = translate.settings(config)
        if tsettings["input_price"] is None or tsettings["output_price"] is None:
            errors.append("[summaries] needs translation_input_price and translation_output_price for its "
                          "translation_model; nothing translated")
            return
        for kind, meeting, doc, record in summarized_documents(data_dir):
            if is_new((kind, meeting, doc), now) != only_new:
                continue
            for lang in langs:
                if translate.current(data_dir, lang, doc["sha256"], record, kind):
                    continue
                if (spent >= settings["max_cost_per_run"] or (allowance is not None and spent >= allowance)
                        or (not only_new and backlog_allowance is not None and spent_backlog >= backlog_allowance)):
                    stopped = stopped or "translations wait: this run's budget for them is spent"
                    return
                try:
                    _, paid = translate.make(client, config, data_dir, lang, kind, meeting, doc, record, now, cost)
                except StoppedEarly as e:
                    paid = cost(e.usage, tsettings)
                    failed_cost += paid
                    errors.append(f"{lang} translation of {kind} {doc['id']}: {e}")
                except Exception as e:
                    if "credit balance" in str(e).lower():
                        errors.append("stopped: the Anthropic account is out of credit; summaries resume when credit is added")
                        stopped = stopped or "out of credit"
                        return
                    errors.append(f"{lang} translation of {kind} {doc['id']}: {e}")
                    continue
                else:
                    translated += 1
                    translation_cost += paid
                spent += paid
                if not only_new:
                    spent_backlog += paid

    def one(kind: str, meeting: dict, doc: dict, pdf: bytes) -> bool:
        """Summarize one document now, on its own; False when nothing more can be sent (out of credit)."""
        nonlocal spent, spent_backlog, failed_cost, replaced_cost, done
        try:
            result, usage = summarize_pdf(client, settings["model"], kind, pdf, meeting["title"], meeting["date"])
        except StoppedEarly as e:
            # Paid for, though nothing is saved.
            paid = cost(e.usage, settings)
            spent += paid
            failed_cost += paid
            if doc["sha256"] in older:
                spent_backlog += paid
            tries = record_stopped_early(data_dir, doc["sha256"], "summary", settings["model"], KINDS[kind]["version"])
            errors.append(f"{kind} {doc['id']}: {e}" + (f"; not tried again after {tries} tries" if tries >= MAX_TRIES else ""))
            return True
        except Exception as e:  # one bad document must not stop the rest
            if "credit balance" in str(e).lower():
                # Out of API credit: every remaining request would fail the same way.
                errors.append("stopped: the Anthropic account is out of credit; summaries resume when credit is added")
                return False
            errors.append(f"{kind} {doc['id']}: {e}")
            return True
        paid = cost(usage, settings)
        replaced_cost += save_summary(config, data_dir, kind, meeting, doc, pdf, result, usage, paid, settings, now, words)
        for k in tokens:
            tokens[k] += usage[k]
        spent += paid
        if doc["sha256"] in older:
            spent_backlog += paid
        done += 1
        return True

    for item in work:
        if item is TRANSLATE:
            translations(only_new=True)
            translations(only_new=False)
            continue
        kind, meeting, doc = item
        if spent >= settings["max_cost_per_run"]:
            errors.append(f"stopped at the ${settings['max_cost_per_run']:.2f} spending limit for one run")
            break
        if allowance is not None and spent >= allowance:
            stopped = f"stopped at this run's ${allowance:.2f} share of the network's monthly summary budget"
            break
        if backlog_allowance is not None and spent_backlog >= backlog_allowance and doc["sha256"] in older:
            # The backlog comes last, so nothing after this is new.
            stopped = f"older documents wait: this run's ${backlog_allowance:.2f} for them is spent"
            break
        try:
            pdf = storage.get(KINDS[kind]["folder"], doc["file"])
        except FetchError as e:
            errors.append(f"{kind} {doc['id']}: {e}")
            continue
        pages = page_count(pdf)
        if pages and pages > MAX_PAGES:
            # Said once: from the next run it's left out of what's waiting, and its page says why.
            record_too_long(data_dir, doc["sha256"], pages)
            errors.append(f"{kind} {doc['id']}: {pages} pages, over the {MAX_PAGES}-page limit")
            continue
        # Sent only if what it's likely to cost fits what's left of this run's limits; otherwise it waits
        # for a run with more room, and smaller documents after it may still go.
        room = [settings["max_cost_per_run"] - spent]
        if allowance is not None:
            room.append(allowance - spent)
        if backlog_allowance is not None and doc["sha256"] in older:
            room.append(backlog_allowance - spent_backlog)
        in_batch = batching and queued_bytes + len(pdf) <= BATCH_MAX_BYTES
        estimate = estimated_cost(kind, pages or 1, settings) * (BATCH_PRICE if in_batch else 1)
        if estimate > min(room):
            too_costly.append(f"{kind} {doc['id']} (about ${estimate:.2f})")
            continue
        if in_batch:
            # Counted at its estimate until the batch is collected, so the rest of the run stays in budget.
            queued.append((kind, meeting, doc, pdf))
            queued_bytes += len(pdf)
            queued_cost[doc["sha256"]] = estimate
            spent += estimate
            if doc["sha256"] in older:
                spent_backlog += estimate
                queued_backlog += estimate
            continue
        if not one(kind, meeting, doc, pdf):
            break
    if queued:
        try:
            send_batch(client, data_dir, settings, queued, older, clock(),
                       spent - sum(queued_cost.values()), spent_backlog - queued_backlog)
        except Exception as e:
            # Sent one at a time instead, within the run's limits at their own price.
            errors.append(f"the batch of {len(queued)} summaries couldn't be sent ({e}); sent one at a time")
            spent -= sum(queued_cost.values())
            spent_backlog -= queued_backlog
            for kind, meeting, doc, pdf in queued:
                estimate = queued_cost[doc["sha256"]] / BATCH_PRICE
                if estimate > min([settings["max_cost_per_run"] - spent] + ([allowance - spent] if allowance is not None else [])):
                    too_costly.append(f"{kind} {doc['id']} (about ${estimate:.2f})")
                    continue
                if not one(kind, meeting, doc, pdf):
                    break
            queued = []
    if too_costly:
        stopped = stopped or (f"{len(too_costly)} waiting for a run with room for them: " + ", ".join(too_costly[:5])
                               + (", ..." if len(too_costly) > 5 else ""))
    # Readable text. First the documents' own text, which costs nothing: summaries saved
    # before it was used, or before the layout rules last changed. Roll call votes, also
    # free, are read with it, and again when the rules or the body's members change; and the
    # summary is checked against the same text (pipeline/factcheck.py).
    laid_out = 0
    later = summarized_documents(data_dir)
    for kind, meeting, doc, record in later:
        if laid_out >= TEXT_PER_RUN:
            break
        members = votes.members_for(config, meeting["body"])
        text_current = record.get("text_version") == pdftext.VERSION
        if text_current and votes.current(record, members) and factcheck.current(record):
            continue
        try:
            pdf = storage.get(KINDS[kind]["folder"], doc["file"])
        except FetchError as e:
            errors.append(f"{kind} {doc['id']}: {e}")
            continue
        record = votes.read(record if text_current else own_text(record, pdf), pdf, members)
        if record.get("is_minutes") is not False:
            record["fact_check"] = factcheck.check(record, kind, factcheck.pages(pdf), words)
        save_record(data_dir, doc["sha256"], record)
        laid_out += 1
    # The translations of older documents summarized in this run, from what's left of the budget for them.
    if not stopped:
        translations(only_new=False)
    # Then the model's transcription of scans, which screen readers can't read, only once
    # every summary waiting is done, and from what's left of this run's budget, as for the backlog.
    transcribed, transcript_cost = 0, 0.0
    # Those in this run's batch are still waiting, as are those in a batch not yet collected.
    summaries_left = len(todo) - done + len(waiting)
    for kind, meeting, doc, record in ([] if summaries_left or stopped else summarized_documents(data_dir)):
        if not record.get("needs_transcript") or record.get("transcript"):
            continue
        if gave_up(data_dir, doc["sha256"], "transcript", settings["model"], TRANSCRIBE["version"]):
            continue
        if (spent >= settings["max_cost_per_run"] or (allowance is not None and spent >= allowance)
                or (backlog_allowance is not None and spent_backlog >= backlog_allowance)):
            stopped = stopped or "transcriptions wait: this run's budget for them is spent"
            break
        try:
            pdf = storage.get(KINDS[kind]["folder"], doc["file"])
        except FetchError as e:
            errors.append(f"{kind} {doc['id']}: {e}")
            continue
        pages = page_count(pdf)
        if pages and pages > MAX_PAGES:
            continue
        try:
            text, usage = transcribe_pdf(client, settings["model"], kind, pdf, meeting["title"], meeting["date"])
        except StoppedEarly as e:
            paid = cost(e.usage, settings)
            spent += paid
            spent_backlog += paid
            failed_cost += paid
            tries = record_stopped_early(data_dir, doc["sha256"], "transcript", settings["model"], TRANSCRIBE["version"])
            errors.append(f"transcript of {kind} {doc['id']}: {e}"
                          + (f"; not tried again after {tries} tries" if tries >= MAX_TRIES else ""))
            continue
        except Exception as e:
            if "credit balance" in str(e).lower():
                errors.append("stopped: the Anthropic account is out of credit; summaries resume when credit is added")
                break
            errors.append(f"transcript of {kind} {doc['id']}: {e}")
            continue
        paid = cost(usage, settings)
        record = {**record, "transcript": text, "transcript_source": "ai", "transcript_usage": usage,
                  "transcript_cost": round(paid, 6)}
        record.pop("needs_transcript", None)
        if record.get("is_minutes") is not False:
            # A scan's summary can now be checked, against the transcription (weaker: both are the model's).
            record["fact_check"] = factcheck.check(record, kind, factcheck.pages(pdf), words)
        save_record(data_dir, doc["sha256"], record)
        for k in tokens:
            tokens[k] += usage[k]
        spent += paid
        spent_backlog += paid
        transcript_cost += paid
        transcribed += 1
    ledger = update_ledger(data_dir, settings, month, failed_cost, transcript_cost, replaced_cost)
    return {"summarized": done + collected, "collected": collected, "queued": len(queued), "laid_out": laid_out,
            "transcribed": transcribed, "translated": translated, "drafted_texts": drafted_texts,
            "remaining": max(len(todo) - done, 0), "errors": errors, "stopped": stopped,
            "estimated_cost": round(spent, 2), "month_cost": round(month_cost(ledger, month), 2), **tokens}


def dollars(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    return float(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--limit", type=int, help="max documents this run")
    args = parser.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::notice::ANTHROPIC_API_KEY is not set; skipping summaries.")
        return 0
    import anthropic

    config = load_config(args.town)
    if not configured(config, "summaries"):
        return 0
    client = anthropic.Anthropic(max_retries=3)
    collecting = os.environ.get(COLLECT_ENV) == "1"
    summary = run(config, client, args.data, args.limit or summary_settings(config)["max_per_run"],
                  allowance=dollars(ALLOWANCE_ENV), backlog_allowance=dollars(BACKLOG_ALLOWANCE_ENV),
                  batch=os.environ.get(BATCH_ENV) == "1" and not collecting, collecting=collecting)
    print(json.dumps(summary, indent=2))
    for error in summary["errors"]:
        print(f"::warning::{error}")
    if summary["stopped"]:
        print(f"::notice::{summary['stopped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
