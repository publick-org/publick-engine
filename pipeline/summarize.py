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
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pypdf import PdfReader

from pipeline import pdftext, votes
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
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
# no time (an Agenda Center's doesn't), is (needs_time).
KINDS = {
    "agenda": {
        "version": 4,
        "folder": "agendas",
        "max_tokens": 4000,
        "system": "You summarize public meeting agendas from a city government for residents. Agendas are often scanned images, so read every character carefully.\n\n"
                  + SUMMARY_RULES,
        "prompt": """This is the posted agenda for: {title}, {date}.

Return:
- headline: one sentence of at most 25 words saying what the meeting will take up, naming the main business. Start with the business itself, not with who is meeting (write "Public hearing on the 2026 Housing Compass Plan.", not "Board will hold a public hearing on ..."). Do not mention the board, the date, the time, or the place; readers already see those.
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
        "version": 2,
        "folder": "minutes",
        "max_tokens": 8000,
        "system": "You summarize the minutes of public meetings of a city government for residents. Minutes are often scanned images, so read every character carefully.\n\n"
                  + SUMMARY_RULES + """
- Report decisions only as the minutes record them. Include the vote count or roll call result when the minutes give one. If the minutes do not say how a matter ended, do not list it as a decision.
- Use the minutes' own verb for each outcome (approved, recommended, referred, continued, tabled, denied). A vote to recommend is not an approval.""",
        "prompt": """These are the posted minutes for: {title}, {date}.

Return:
- headline: one sentence of at most 25 words on what the meeting decided, or what it discussed if it decided nothing. Start with the outcome itself, not with who met (write "Approved seven board appointments ...", not "Committee approved seven board appointments ..."). Do not mention the board, the date, the time, or the place; readers already see those.
- summary: 1 to 3 short sentences on what the meeting covered and what was decided. Do not repeat the board's name, the date, the time, or the place.
- is_minutes: true if this document is minutes of a meeting that took place; false if it is something else, such as an agenda or notice filed under minutes.
- decisions: each motion, vote, or other decision the minutes record, in order, as a short plain-English sentence that includes the outcome (for example "Approved ... 5-0" or "Continued ... to October 22, 2026"). Skip procedural motions such as adjourning or accepting the agenda.""",
        "schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "is_minutes": {"type": "boolean"},
                "decisions": {"type": "array", "items": {"type": "string"}},
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
MAX_PAGES = 60
# The API takes requests up to 32 MB, and base64 makes a PDF a third larger.
MAX_BYTES = 22_000_000


def too_large(doc: dict) -> bool:
    return (doc.get("bytes") or 0) > MAX_BYTES


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


def needs_time(kind: str, meeting: dict, record: dict, today: str) -> bool:
    """An upcoming meeting with no time listed, whose agenda summary is from before agendas gave one."""
    return (kind == "agenda" and meeting["date"] >= today and not meeting.get("start_time")
            and "start_time" not in record)


def pending_documents(data_dir: Path, today: str, model: str) -> list[tuple[str, dict, dict]]:
    """Latest agenda and minutes of each meeting without a current summary.

    Order: agendas for upcoming meetings, then minutes (newest meeting first),
    then agendas for past meetings."""
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo, seen = [], set()
    for meeting in store.values():
        for kind, field in (("agenda", "agendas"), ("minutes", "minutes")):
            if not meeting.get(field):
                continue
            doc = meeting[field][-1]
            # Several meetings can share one document; process it once.
            if doc["sha256"] in seen or too_large(doc):
                continue
            record = cached(data_dir, doc["sha256"], model, kind)
            if record and not needs_time(kind, meeting, record, today):
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
                  transcript_cost: float = 0.0) -> dict:
    """Recount the summaries saved this month (and any month the ledger doesn't have yet) and save the ledger.

    Earlier months are kept as they were: a summary made again replaces its file,
    so counting them again would lose what the first one cost. failed_cost is
    what this run paid for requests that were cut off, which leave no file, and
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
    for key, paid in (("failed_cost", failed_cost), ("transcript_cost", transcript_cost)):
        if paid:
            row = ledger.setdefault(month, {"cost": 0.0, "documents": 0})
            row[key] = round(row.get(key, 0.0) + paid, 4)
    ledger = dict(sorted(ledger.items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return ledger


def month_cost(ledger: dict, month: str) -> float:
    row = ledger.get(month, {})
    return row.get("cost", 0.0) + row.get("failed_cost", 0.0) + row.get("transcript_cost", 0.0)


class StoppedEarly(RuntimeError):
    """A response that ended before it was complete. Its tokens are still paid for."""

    def __init__(self, reason: str, usage: dict):
        super().__init__(f"stopped early: {reason}")
        self.usage = usage


def page_count(pdf: bytes) -> int | None:
    try:
        return len(PdfReader(io.BytesIO(pdf)).pages)
    except Exception:
        return None


def summarize_pdf(client, model: str, kind: str, pdf: bytes, title: str, date: str) -> tuple[dict, dict]:
    spec = KINDS[kind]
    # Streaming avoids HTTP timeouts on long transcripts.
    with client.messages.stream(
        model=model,
        max_tokens=spec["max_tokens"],
        system=spec["system"],
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": base64.standard_b64encode(pdf).decode("ascii")}},
                {"type": "text", "text": spec["prompt"].format(title=title, date=date)},
            ],
        }],
        output_config={"format": {"type": "json_schema", "schema": spec["schema"]}},
    ) as stream:
        response = stream.get_final_message()
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    if response.stop_reason != "end_turn":
        raise StoppedEarly(response.stop_reason, usage)
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), usage


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def summarized_documents(data_dir: Path) -> list[tuple[str, dict, dict, dict]]:
    """(kind, meeting, document, saved record) for each meeting's latest agenda and minutes
    that have a summary, newest meeting first."""
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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


def cost(usage: dict, settings: dict) -> float:
    return (usage["input_tokens"] * settings["input_price"] + usage["output_tokens"] * settings["output_price"]) / 1e6


def summary_settings(config: dict) -> dict:
    """The town's [summaries] table, with the run limits filled in when it leaves them out."""
    return {"max_per_run": DEFAULT_MAX_PER_RUN, "max_cost_per_run": DEFAULT_MAX_COST_PER_RUN, **config["summaries"]}


def run(config: dict, client, data_dir: Path, limit: int, now: datetime | None = None,
        allowance: float | None = None, backlog_allowance: float | None = None) -> dict:
    """Summarize up to limit documents, new ones first, within the town's run limits and the
    network's allowances for this run (None: no network limit)."""
    settings = summary_settings(config)
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    todo = pending_documents(data_dir, now.date().isoformat(), settings["model"])
    new = [t for t in todo if is_new(t, now)]
    backlog = [t for t in todo if not is_new(t, now)]
    older = {doc["sha256"] for _, _, doc in backlog}
    storage = open_documents(config, data_dir)
    done, errors, tokens, spent, spent_backlog, failed_cost = 0, [], {"input_tokens": 0, "output_tokens": 0}, 0.0, 0.0, 0.0
    stopped = None
    batch = (new + backlog)[:limit]
    if "input_price" not in settings or "output_price" not in settings:
        # Without prices the spending limit can't be enforced, so nothing is sent.
        batch = []
        errors.append("[summaries] needs input_price and output_price for the spending limit; nothing summarized")
    for kind, meeting, doc in batch:
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
            errors.append(f"{kind} {doc['id']}: {pages} pages, over the {MAX_PAGES}-page limit")
            continue
        try:
            result, usage = summarize_pdf(client, settings["model"], kind, pdf, meeting["title"], meeting["date"])
        except StoppedEarly as e:
            # Paid for, though nothing is saved.
            paid = cost(e.usage, settings)
            spent += paid
            failed_cost += paid
            if doc["sha256"] in older:
                spent_backlog += paid
            errors.append(f"{kind} {doc['id']}: {e}")
            continue
        except Exception as e:  # one bad document must not stop the rest
            if "credit balance" in str(e).lower():
                # Out of API credit: every remaining request would fail the same way.
                errors.append("stopped: the Anthropic account is out of credit; summaries resume when credit is added")
                break
            errors.append(f"{kind} {doc['id']}: {e}")
            continue
        paid = cost(usage, settings)
        record = {
            **result,
            "kind": kind,
            "source_url": doc["source_url"],
            "source_sha256": doc["sha256"],
            "model": settings["model"],
            "prompt_version": KINDS[kind]["version"],
            "generated_at": now.isoformat(timespec="seconds"),
            "usage": usage,
            "cost": round(paid, 6),
        }
        save_record(data_dir, doc["sha256"], votes.read(own_text(record, pdf), pdf,
                                                        votes.members_for(config, meeting["body"])))
        for k in tokens:
            tokens[k] += usage[k]
        spent += paid
        if doc["sha256"] in older:
            spent_backlog += paid
        done += 1
    # Readable text. First the documents' own text, which costs nothing: summaries saved
    # before it was used, or before the layout rules last changed. Roll call votes, also
    # free, are read with it, and again when the rules or the body's members change.
    laid_out = 0
    later = summarized_documents(data_dir)
    for kind, meeting, doc, record in later:
        if laid_out >= TEXT_PER_RUN:
            break
        members = votes.members_for(config, meeting["body"])
        text_current = record.get("text_version") == pdftext.VERSION
        if text_current and votes.current(record, members):
            continue
        try:
            pdf = storage.get(KINDS[kind]["folder"], doc["file"])
        except FetchError as e:
            errors.append(f"{kind} {doc['id']}: {e}")
            continue
        save_record(data_dir, doc["sha256"], votes.read(record if text_current else own_text(record, pdf), pdf, members))
        laid_out += 1
    # Then the model's transcription of scans, which screen readers can't read, only once
    # every summary waiting is done, and from what's left of this run's budget, as for the backlog.
    transcribed, transcript_cost = 0, 0.0
    summaries_left = len(todo) - done
    for kind, meeting, doc, record in ([] if summaries_left or stopped else summarized_documents(data_dir)):
        if not record.get("needs_transcript") or record.get("transcript"):
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
            errors.append(f"transcript of {kind} {doc['id']}: {e}")
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
        save_record(data_dir, doc["sha256"], record)
        for k in tokens:
            tokens[k] += usage[k]
        spent += paid
        spent_backlog += paid
        transcript_cost += paid
        transcribed += 1
    month = now.strftime("%Y-%m")
    ledger = update_ledger(data_dir, settings, month, failed_cost, transcript_cost)
    return {"summarized": done, "laid_out": laid_out, "transcribed": transcribed,
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
    summary = run(config, client, args.data, args.limit or summary_settings(config)["max_per_run"],
                  allowance=dollars(ALLOWANCE_ENV), backlog_allowance=dollars(BACKLOG_ALLOWANCE_ENV))
    print(json.dumps(summary, indent=2))
    for error in summary["errors"]:
        print(f"::warning::{error}")
    if summary["stopped"]:
        print(f"::notice::{summary['stopped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
