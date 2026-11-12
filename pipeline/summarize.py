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
long document is never cut off, and once: a summary made again keeps its
document's transcription (kept_transcription()). Any other PDF has text a screen reader can
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
Batches API), at half the price, instead of one at a time (PUBLICK_SUMMARY_BATCH=1),
with the translations that need making or reviewing, but not those of a document
whose summary is in the batch. The run that collects the batch sends the next
one, with the reviews of the translations it saved and the translations of the
summaries it saved, for a later collecting run or the town's next run to collect
(translate.py's "Translations in a batch"): a translation isn't shown until its
review passes, so a new document's Spanish comes about a day after its English.
A translation the batch didn't make waits for the town's next batch.
The batch's id and documents are kept in data/summary-batch.json, and the job
collects its results once its other towns are done (PUBLICK_SUMMARY_COLLECT=1,
from pipeline.network run --collect), up to BATCH_WAIT after it was sent;
between its towns, the job collects each batch already ended
(PUBLICK_SUMMARY_COLLECT=ready, from run --collect --ready), so a town's summaries
don't wait for the job's last town. One
not done by then is cancelled, and what it didn't finish is sent one at a time,
as without a batch, from the same run's budget: it never costs more than
sending them one at a time, and comes at most about BATCH_WAIT later. A batch a
job didn't collect (it was stopped) is collected by the town's next run.

Needs ANTHROPIC_API_KEY. Without it, the step is skipped. The step fails (exit
status 1) only when it tried documents and every one failed (exit_status()).

Each request paid for is written to the month's cost log as it's answered,
and each month's totals are made from it (the ledger), which the network reads
to keep every town's summaries within one monthly budget. Both are kept out of
git: in the private store with its keys (PRIVATE_*), else in the town's
data/costs/ and data/summary-costs.json (pipeline/cost_log.py). The records
saved in data/summaries/ don't say what they cost or the tokens they took
(cost_log.without_costs()): the log's lines do.
A network run gives each town its share for the run in two environment
variables, each a fraction of the month's budget: PUBLICK_SUMMARY_ALLOWANCE_SHARE,
for any document, and PUBLICK_SUMMARY_BACKLOG_SHARE, for older documents, with
the budget itself in SUMMARY_BUDGET (and SUMMARY_BUDGET_MONTH), secrets, so no
amount is in the run's public log. (PUBLICK_SUMMARY_ALLOWANCE and
PUBLICK_SUMMARY_BACKLOG_ALLOWANCE, in dollars, are read when no share is given.)
New documents (upcoming agendas, and those fetched in the last two weeks for a
recent meeting) go first, and the backlog gets what the second allows. Without
them, only the town's own run limits apply. What the run printed leaves out
what anything cost.

Usage:
    python -m pipeline.summarize [--town gloucester] [--limit 20]
    python -m pipeline.summarize ledger [--check [--month 2026-10]]

ledger makes the ledger's rows again from the cost logs (and prints which
months it made, not what they cost); with --check, it compares each month's log
with the summary, transcription, and translation files saved in it (and the
cost a file records, for one saved when files recorded them), lists each
difference (by document, without amounts), and changes nothing (exit status 1
if any).
"""

from __future__ import annotations

import argparse
import base64
import functools
import io
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pypdf import PdfReader

from pipeline import cost_log, factcheck, fetch_addresses, glossary, listings, pdftext, transcribe, translate, votes
from pipeline.config import DATA_DIR, DEFAULT_TOWN, configured, load_config
from pipeline.documents import open_documents
from pipeline.files import write_atomic
from pipeline.http import FetchError
from pipeline.streets import street_key

TRANSCRIPT_RULES = """Transcript rules:
- Copy the document's text character for character. Do not reword, correct, modernize, or change the spelling of anything (for example, keep "Councilor" if that is how it is written).
- Take particular care with digits and similar-looking characters (0 and O, 1 and l and I, 5 and S) in ZIP codes, phone numbers, meeting IDs, web addresses, dollar amounts, dates, vote counts, and case numbers.
- If a word or number cannot be read with confidence, write "[unreadable]" instead of guessing."""

SUMMARY_RULES = """Summary rules:
- Use only what the document says. Do not add background, predictions, opinions, likely outcomes, or categories the document does not use.
- Neutral tone. No adjectives that judge (such as important, controversial, significant).
- Plain English at about an 8th-grade reading level. Short sentences, one idea each.
- Keep names, dollar amounts, dates, and case or application numbers exactly as written.
- Write the way a local reporter writes a brief: say what happened, in the words residents use. Vary how sentences start; don't open each one with "The board", "Members", or "Also".
- Don't say what didn't happen (such as "No votes were taken" or "The meeting was informational") unless the board was expected to act and didn't.
- Name at most three things in a row. For more, give the most important ones, or a count ("approved six permits").
- The summary adds what the headline leaves out. Don't say again what the headline says."""

# What each decision of minutes or a recording is about, for the decisions page to filter by
# (pipeline/build_site.py TOPIC_NAMES says each one).
TOPICS = ("budget", "housing", "schools", "safety", "streets", "environment", "government", "other")

# What minutes' and a recording's summaries give for each decision, besides the decision itself: a short
# title for it, whether it's one residents look for or routine, and its topic. The pages show a main decision
# by its title, and the routine ones after them, one line each.
DECISION_LABELS = """
  - title: the matter decided, as a short heading of at most 10 words, without the outcome or the vote: what residents would call it, with its street address, name, or dollar amount where the decision gives one (for example "$200,000 for soil cleanup at 448 High Street", "Ban on black plastic takeout containers", "Site plan for 12 Elm Street"). Use only names, places, and numbers that are in the decision.
  - main: true for a decision residents are likely to look for: a project or development; a new rule, ordinance, or moratorium; taxes, fees, a budget, spending, or a contract; schools; or any vote that was not unanimous. false for routine business, such as approving minutes or reports, proclamations and resolutions honoring someone, appointments, licenses and permits renewed as usual, small settlements, and scheduling.
  - topic: what the decision is about, in one word: budget (taxes, fees, budgets, spending, contracts), housing (housing, zoning, development, building), schools, safety (police, fire, emergency services), streets (streets, traffic, parking, utilities), environment (parks, open space, water, sewer, climate), government (how the city runs: elections, appointments, the charter, its own rules), or other."""
DECISION_LABEL_PROPERTIES = {"title": {"type": "string"}, "main": {"type": "boolean"},
                             "topic": {"type": "string", "enum": list(TOPICS)}}

# Run limits for a town whose [summaries] table leaves them out.
DEFAULT_MAX_PER_RUN = 50
DEFAULT_MAX_COST_PER_RUN = 5.0

# What summaries cost, by month, in the town's data/, made from the cost logs (pipeline/cost_log.py).
LEDGER = cost_log.LEDGER
# The network's share for this run, as a fraction of the month's budget (cost_log.monthly_budget), or
# in dollars (see the module docstring).
ALLOWANCE_SHARE_ENV = "PUBLICK_SUMMARY_ALLOWANCE_SHARE"
BACKLOG_SHARE_ENV = "PUBLICK_SUMMARY_BACKLOG_SHARE"
ALLOWANCE_ENV = "PUBLICK_SUMMARY_ALLOWANCE"
BACKLOG_ALLOWANCE_ENV = "PUBLICK_SUMMARY_BACKLOG_ALLOWANCE"
# What a run reports that main() doesn't print: the run's log is public.
COST_FIELDS = ("estimated_cost", "month_cost")
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
# version are regenerated on the next run. Another [summaries] model (or effort)
# doesn't regenerate any: the new documents get it, and a summary already made is
# made again only when its kind's version changes (cached()). An agenda's
# start_time and location were added without a bump, so older agendas aren't all
# made again: only an upcoming meeting's agenda, when the meeting's listing has
# no time (an Agenda Center's doesn't), is (needs_time). A kind's remake_since
# keeps what an older version made for meetings before that date: only later
# meetings' documents are made again (kept()).
KINDS = {
    "agenda": {
        # Version 5: the headline leads with what residents look for (projects and their addresses,
        # new rules, money), for search results and the cards shared on social media. Version 6: the
        # summary rules on voice, lists, and repeating the headline (SUMMARY_RULES). Agendas of meetings
        # before remake_since keep the version they have.
        "version": 6,
        "remake_since": "2026-11-04",
        "folder": "agendas",
        # Room for a model's thinking at a high effort as well as the summary, which has run to
        # about 2,100 tokens at most; only what's written is paid for.
        "max_tokens": 8000,
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
        # for, as the agenda's (version 5) does. Version 5: each decision with a short title, whether it's
        # main or routine, and its topic (DECISION_LABELS). Minutes of meetings before remake_since keep the
        # version they have. Version 6: the summary rules on voice, lists, and repeating the headline
        # (SUMMARY_RULES).
        "version": 6,
        "remake_since": "2026-11-04",
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
  - quote: the minutes' own words that record this decision, copied exactly, character for character: the item or motion, with its number, address, or amount if the minutes give one, and how it ended. One passage of at most 60 words; if the motion and how it ended are far apart, give both, joined by "...". Never reword, shorten, or correct the minutes' words.""" + DECISION_LABELS,
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
                        **DECISION_LABEL_PROPERTIES,
                    },
                    "required": ["decision", "outcome", "quote", *DECISION_LABEL_PROPERTIES],
                    "additionalProperties": False,
                }},
            },
            "required": ["headline", "summary", "is_minutes", "decisions"],
            "additionalProperties": False,
        },
    },
    "recording": {
        # A meeting summarized from the machine transcript of its recording (pipeline/recordings.py), the day
        # after, until its minutes come and are summarized instead. Its decisions as the minutes' are, each
        # with the transcript's own words for it, checked against the transcript (pipeline/factcheck.py).
        # Version 2: each decision with a title, main or routine, and a topic, as the minutes' (version 5).
        # Version 3: each matter discussed starts with a short name for it ("Law Department funding: ...").
        # Version 4: the summary rules on voice, lists, and repeating the headline (SUMMARY_RULES). Meetings
        # before remake_since keep the version they have.
        "version": 4,
        "remake_since": "2026-11-04",
        "folder": "transcripts",
        # The document is text (the transcript), not a PDF.
        "text": True,
        # A four-hour meeting's transcript is about 60,000 tokens; its summary, a fraction of what's left.
        "max_tokens": 32000,
        "system": "You summarize public meetings of a city government for residents, from a machine transcript of the meeting's recording. The transcript doesn't say who is speaking, and it mishears some words, names most of all.\n\n"
                  + SUMMARY_RULES + """
- Report decisions only where the transcript records how they ended: a vote announced, or the chair saying a motion carried or failed. Give the vote count when it is announced or a roll call is read. Never say how a named member voted.
- Use the meeting's own verb for each outcome (approved, recommended, referred, continued, tabled, denied). A vote to recommend is not an approval.
- A motion that failed was not approved, whatever it proposed: report it as failed.
- Do not say which member said or argued what: the transcript does not show reliably who is speaking. Do not name members of the public.
- Spell a member's name as the list of members gives it where the transcript mishears it; other names as the transcript has them.""",
        "prompt": """This is a machine transcript of the recording of: {title}, {date}. Each paragraph starts with its time in the recording, in brackets.{members}

Return:
- headline: one sentence of at most 25 words on what the meeting decided, or what it discussed if it decided nothing. Lead with the decisions residents are most likely to look for, named as they would search for them: projects and developments, with their street addresses or names; new rules, ordinances, or moratoriums; taxes, fees, budgets, and contracts, with their dollar amounts; schools. Put routine decisions after these, or leave them out. Start with the outcome itself, not with who met. Do not mention the board, the date, the time, or the place.
- summary: 1 to 3 short sentences on what the meeting covered and what was decided. Do not repeat the board's name, the date, the time, or the place.
- decisions: each motion or vote whose result the transcript records, in order. Skip procedural motions such as adjourning, accepting the agenda, or approving minutes. For each:
  - decision: a short plain-English sentence that includes the outcome (for example "Approved ... 7-3" or "Referred ... to the Finance Subcommittee").
  - outcome: what happened, in one word: approved (also adopted, accepted, granted, or passed), denied (also rejected, or any motion that failed), tabled, continued, referred, recommended, withdrawn, or other.
  - quote: the transcript's own words that record this decision, copied exactly, character for character, without the times in brackets: the motion and how it ended. One passage of at most 60 words; if the motion and how it ended are far apart, give both, joined by "...". Never reword, shorten, or correct the transcript's words.""" + DECISION_LABELS + """
- discussion: the matters the meeting spent the most time on, or that members of the public spoke on, at most 5, each in one or two sentences: what was discussed and the views raised, without saying who raised them. Start each with a short name for the matter, of at most 5 words, then a colon (for example "Law Department funding: Members debated whether ..."). An empty list if there was little discussion.""",
        "schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "decisions": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "decision": {"type": "string"},
                        "outcome": {"type": "string", "enum": list(factcheck.OUTCOMES)},
                        "quote": {"type": "string"},
                        **DECISION_LABEL_PROPERTIES,
                    },
                    "required": ["decision", "outcome", "quote", *DECISION_LABEL_PROPERTIES],
                    "additionalProperties": False,
                }},
                "discussion": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["headline", "summary", "decisions", "discussion"],
            "additionalProperties": False,
        },
    },
}

# The kinds of document a meeting has, and the field of its record that lists them.
DOCUMENT_FIELDS = (("agenda", "agendas"), ("minutes", "minutes"), ("recording", "transcripts"))

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
            "output": {"agenda": (2500, 0), "minutes": (2000, 1200), "recording": (3000, 300)}}
# A transcript is counted in pages of this many characters (about 3,000 tokens of English, ESTIMATE's page).
TEXT_PAGE_CHARS = 12000
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


# Documents whose request stopped early (cut off at max_tokens, or refused), or whose result couldn't
# be saved (its PDF couldn't be read), by hash, in the town's data/: for each thing asked of the model
# ("summary" or "transcript"), the model and prompt version asked and how many tries stopped early.
# Each try is paid for though nothing is saved, so after MAX_TRIES the document is left out of what's
# waiting, and its page links the original, until the model or the prompt changes.
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


def cached(data_dir: Path, sha256: str, kind: str = "agenda", current: bool = True) -> dict | None:
    """The saved result for a document, if it was made with the kind's current prompt, by whichever model.

    A summary is made again when its prompt changes, not when the model does: a town switched to
    another model would otherwise have every summary it has made again at once, at that run's cost.
    With current=False, any saved result of this kind is returned, so the site keeps showing a
    summary while a newer prompt's version is waiting to be made.
    """
    path = summaries_dir(data_dir) / f"{sha256}.json"
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    version = KINDS[kind]["version"]
    if record.get("kind", "agenda") != kind:
        return None
    if current and record.get("prompt_version") != version:
        return None
    return record


def kept(data_dir: Path, sha256: str, kind: str, meeting: dict) -> bool:
    """Whether a summary made with an earlier version of the prompt stays: its meeting is before
    the kind's remake_since."""
    since = KINDS[kind].get("remake_since")
    if not since or meeting["date"] >= since:
        return False
    return cached(data_dir, sha256, kind, current=False) is not None


# Lists kept beside a summary's decisions, one entry each (split_decisions()), from DECISION_LABELS.
DECISION_LISTS = {"title": "decision_titles", "main": "decision_main", "topic": "decision_topics"}


def split_decisions(result: dict) -> dict:
    """A minutes summary as saved: its decisions as sentences, as every page and translation reads
    them, and each one's outcome and quote beside them, in "decision_evidence"; and where the summary
    gives them, each one's title, whether it's main, and its topic, in "decision_titles",
    "decision_main" and "decision_topics"."""
    if not result.get("decisions") or not isinstance(result["decisions"][0], dict):
        return result
    entries = result["decisions"]
    labels = {field: [d.get(key) for d in entries] for key, field in DECISION_LISTS.items()
              if all(key in d for d in entries)}
    return {**result, "decisions": [d["decision"] for d in entries],
            "decision_evidence": [{"outcome": d["outcome"], "quote": d["quote"]} for d in entries], **labels}


def needs_time(kind: str, meeting: dict, record: dict, today: str) -> bool:
    """An upcoming meeting with no time listed, whose agenda summary is from before agendas gave one."""
    return (kind == "agenda" and meeting["date"] >= today and not meeting.get("start_time")
            and "start_time" not in record)


def pending_documents(data_dir: Path, today: str, model: str, since: str | None = None) -> list[tuple[str, dict, dict]]:
    """Latest agenda and minutes of each meeting without a current summary, for meetings on or
    after since ([summaries] since; None for all). model is the one a run sends: a document given up
    on (MAX_TRIES) with it isn't waiting, while one given up on with another model is tried again.

    Order: agendas for upcoming meetings, then minutes and recordings' transcripts (newest meeting
    first), then agendas for past meetings."""
    # A meeting listed in more than one place is summarized once, from its current agenda.
    store, _ = listings.read(data_dir)
    # Older meetings keep their records and documents, without a summary.
    meetings = [m for m in store.values() if not since or m["date"] >= since]
    # One file, one summary: a summary is saved by its file's hash, so a file one meeting has as its agenda
    # and another as its minutes would be made as each kind in turn, every run. It's made as the first kind
    # (DOCUMENT_FIELDS) any meeting has it as.
    first = {}
    for kind, field in DOCUMENT_FIELDS:
        for meeting in meetings:
            if meeting.get(field):
                first.setdefault(meeting[field][-1]["sha256"], kind)
    todo, seen = [], set()
    for meeting in meetings:
        for kind, field in DOCUMENT_FIELDS:
            # A recording is summarized only until the minutes come.
            if not meeting.get(field) or (kind == "recording" and meeting.get("minutes")):
                continue
            doc = meeting[field][-1]
            # Several meetings can share one document; process it once.
            if (doc["sha256"] in seen or first[doc["sha256"]] != kind or too_large(doc) or too_long(data_dir, doc)
                    or summary_gave_up(data_dir, doc, kind, model)):
                continue
            record = cached(data_dir, doc["sha256"], kind)
            if record and not needs_time(kind, meeting, record, today):
                continue
            if not record and kept(data_dir, doc["sha256"], kind, meeting):
                continue
            seen.add(doc["sha256"])
            todo.append((kind, meeting, doc))
    date = lambda t: t[1]["date"]
    upcoming = sorted((t for t in todo if t[0] == "agenda" and date(t) >= today), key=date)
    minutes = sorted((t for t in todo if t[0] in ("minutes", "recording")), key=date, reverse=True)
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


def record_cost(record: dict, settings: dict) -> float | None:
    """What a saved summary cost, for one saved when records carried it: as recorded, or from its token
    counts. None for a record that carries neither (every record saved now: cost_log.without_costs())."""
    if "cost" in record:
        return record["cost"]
    return cost(record["usage"], settings) if "usage" in record else None


def translation_cost(record: dict) -> float | None:
    """What a saved translation, or batch of drafts, cost with the ones it replaced that month, for one saved
    when records carried it; None for one that doesn't."""
    if "cost" not in record:
        return None
    return record["cost"] + record.get("replaced_cost", 0.0)


def saved_summaries(data_dir: Path) -> dict[str, dict]:
    """Every English summary the town has saved, by its document's hash."""
    folder = summaries_dir(data_dir)
    return {file.stem: json.loads(file.read_text(encoding="utf-8"))
            for file in (sorted(folder.glob("*.json")) if folder.is_dir() else [])}


def seed_lines(data_dir: Path, settings: dict, month: str, at: str, row: dict) -> list[dict]:
    """The lines a town's first cost log starts with (pipeline/cost_log.py): each summary and translation
    saved this month and each batch of drafts made in it, at the cost its file records (0 for one saved
    without a cost), and a seed line with the rest of the month's row of the ledger it had (row), which no
    file records, at the time given: its failed, transcript, and replaced costs, and whatever of its cost
    and translation_cost the files' costs don't account for. So the month's row goes on from the ledger's,
    whether or not the files record costs."""
    made = sorted(((sha, r) for sha, r in saved_summaries(data_dir).items() if r.get("generated_at", "")[:7] == month),
                  key=lambda item: item[1]["generated_at"])
    lines = [{"at": r["generated_at"], "kind": "summary", "key": f"seed:{sha}", "doc": sha, "model": r.get("model"),
              "cost": round(record_cost(r, settings) or 0.0, 6)} for sha, r in made]
    for i, (lang, sha, r) in enumerate(translate.made_in(data_dir, month)):
        lines.append({"at": r["generated_at"], "kind": "translation" if sha else "draft",
                      "key": f"seed:{lang}:{sha or f'drafts-{i}'}", **({"doc": sha} if sha else {}), "lang": lang,
                      "cost": round(translation_cost(r) or 0.0, 6)})
    rest = {k: row[k] for k in ("failed_cost", "transcript_cost", "replaced_cost") if row.get(k)}
    for key, kinds in (("cost", ("summary",)), ("translation_cost", ("translation", "draft"))):
        left = round(row.get(key, 0.0) - sum(line["cost"] for line in lines if line["kind"] in kinds), 6)
        if left > 0:
            rest[key] = left
    if rest:
        lines.append({"at": at, "kind": "seed", "key": "seed", "totals": rest,
                      "cost": round(sum(rest.values()), 6)})
    return [{k: v for k, v in line.items() if v is not None} for line in lines]


def open_store(config: dict | None, data_dir: Path):
    """The town's costs: the private store with its keys, else the town's own folder (pipeline/cost_log.py)."""
    return cost_log.open_store(config or {}, data_dir) if config else cost_log.LocalStore(data_dir)


def open_log(data_dir: Path, settings: dict, now: datetime, config: dict | None = None, store=None) -> cost_log.Log:
    """The cost log for now's month; a town without one yet starts it from what its files record (seed_lines)."""
    month = now.strftime("%Y-%m")
    return cost_log.Log(store or open_store(config, data_dir), month, now.tzinfo,
                        seed=lambda row: seed_lines(data_dir, settings, month, now.isoformat(timespec="seconds"), row))


# Two costs within this, in dollars, are the same: files and lines round each cost to the millionth.
SAME_COST = 1e-5


def ledger_differences(data_dir: Path, settings: dict, only: list[str] | None = None, store=None) -> list[str]:
    """Where each month's cost log (or only these months') and the town's files disagree, one line each;
    nothing is changed. Each summary saved in the month has its document's latest summary line; each
    transcription made in it (from its transcribed_at) has transcript lines; each translation has its
    translation and review lines, and each language's drafts theirs; a line with no file saved in the
    month is a difference too, unless the file was made again in a later month. A file saved when files
    recorded their cost (record_cost(), translation_cost()) is also compared at that cost: the summary
    line's, all of the transcription's lines', all of the translation's (with the earlier ones it
    replaced) or the drafts'. And the ledger's row is the one the log makes. Each difference is said
    without its amounts: the check's output may be a public run log."""
    store = store or cost_log.LocalStore(data_dir)
    records = saved_summaries(data_dir)
    ledger = cost_log.load_ledger(store)
    out = []
    for month in only or store.months():
        entries = cost_log.read_month(store, month)
        made = cost_log.totals(entries)
        if entries and ledger.get(month) != made:
            fields = sorted(k for k in set(made) | set(ledger.get(month) or {})
                            if (ledger.get(month) or {}).get(k) != made.get(k))
            out.append(f"{month}: the ledger's row isn't the one the log makes ({', '.join(fields)} differ)")
        latest, transcribed, translated = {}, {}, {}
        for e in entries:
            if e["kind"] == "summary":
                latest[e["doc"]] = e["cost"]
            elif e["kind"] == "transcript":
                transcribed[e["doc"]] = transcribed.get(e["doc"], 0.0) + e["cost"]
            elif e["kind"] in cost_log.TRANSLATING:
                key = (e.get("lang"), e.get("doc"))
                translated[key] = translated.get(key, 0.0) + e["cost"]

        def compare(what, recorded: dict, logged: dict, later) -> None:
            """recorded: each file saved in the month, at the cost it records (None if it records none)."""
            for key in sorted(set(recorded) | set(logged), key=str):
                if key not in logged:
                    out.append(f"{month}: {what(key)} has a file saved this month and isn't in the log")
                elif key not in recorded:
                    if not later(key):
                        out.append(f"{month}: {what(key)} is in the log, with no file saved this month")
                elif recorded[key] is not None and abs(recorded[key] - logged[key]) > SAME_COST:
                    out.append(f"{month}: {what(key)} has one cost in its file and another in the log")

        def made_later(at: str) -> bool:
            return at[:7] > month

        compare(lambda sha: f"the summary of {sha}",
                {sha: record_cost(r, settings) for sha, r in records.items() if r.get("generated_at", "")[:7] == month},
                latest, lambda sha: made_later(records.get(sha, {}).get("generated_at", "")))
        compare(lambda sha: f"the transcription of {sha}",
                {sha: r.get("transcript_cost") for sha, r in records.items()
                 if r.get("transcribed_at", "")[:7] == month},
                transcribed, lambda sha: made_later(records.get(sha, {}).get("transcribed_at", "")))
        recorded: dict[tuple, float | None] = {}
        for lang, sha, r in translate.made_in(data_dir, month):
            # A language's batches of drafts are one key: compared at a cost only if each records one.
            paid, before = translation_cost(r), recorded.get((lang, sha), 0.0)
            recorded[(lang, sha)] = None if paid is None or before is None else before + paid
        compare(lambda key: f"the {key[0]} translation of {key[1]}" if key[1] else f"the {key[0]} drafts",
                recorded, translated,
                lambda key: bool(key[1]) and made_later((translate.saved(data_dir, key[0], key[1]) or {}).get("generated_at", "")))
    return out


# What a month's row of the ledger counts as paid for.
LEDGER_COSTS = cost_log.COSTS


def month_cost(ledger: dict, month: str) -> float:
    """Everything paid for in a month: summaries (and those made again since), cut-off requests,
    transcriptions, and translations."""
    return cost_log.month_cost(ledger.get(month, {}))


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


def document(storage, kind: str, doc: dict) -> bytes:
    """A document as it's summarized: its PDF, or a recording's transcript, each part's in order for a meeting
    recorded in parts (pipeline/recordings.py)."""
    folder = KINDS[kind]["folder"]
    if not doc.get("parts"):
        return storage.get(folder, doc["file"])
    return "\n".join(f"Part {i}: {part.get('title') or ''}\n" + storage.get(folder, part["file"]).decode("utf-8")
                     for i, part in enumerate(doc["parts"], 1)).encode("utf-8")


def length(kind: str, content: bytes) -> int | None:
    """A document's length in pages: a PDF's, or a transcript's in pages of TEXT_PAGE_CHARS."""
    if KINDS[kind].get("text"):
        return -(-len(content.decode("utf-8")) // TEXT_PAGE_CHARS)
    return page_count(content)


def members_note(members: list[str] | None) -> str:
    """The board's members, for a transcript's summary to spell their names as the town does."""
    return f"\n\nThe board's members: {', '.join(members)}." if members else ""


def summary_request(model: str, kind: str, pdf: bytes, title: str, date: str, effort: str | None = None,
                    members: list[str] | None = None) -> dict:
    """The request for a document's summary: as a run sends it, or as one request of a batch
    (pipeline/evaluate_batch.py). Without an effort, the model's default. A recording's transcript is
    sent as text, with the board's members (votes.members_for()), whose names it mishears."""
    spec = KINDS[kind]
    source = ({"type": "text", "media_type": "text/plain", "data": pdf.decode("utf-8")} if spec.get("text") else
              {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(pdf).decode("ascii")})
    return {
        "model": model,
        "max_tokens": spec["max_tokens"],
        "system": spec["system"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "document", "source": source},
                {"type": "text", "text": spec["prompt"].format(title=title, date=date, members=members_note(members))},
            ],
        }],
        "output_config": {**({"effort": effort} if effort else {}),
                          "format": {"type": "json_schema", "schema": spec["schema"]}},
    }


def stop_reason(response) -> str:
    """Why a response stopped, with a refusal's category when the API gives one ("refusal (cyber)")."""
    reason = response.stop_reason
    category = getattr(getattr(response, "stop_details", None), "category", None)
    return f"{reason} ({category})" if reason == "refusal" and category else reason


def summary_result(response) -> tuple[dict, dict]:
    """A summary response's result and its usage; StoppedEarly if it didn't finish (cut off at
    max_tokens, or refused)."""
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    text = next((b.text for b in response.content if b.type == "text"), "")
    if response.stop_reason != "end_turn":
        raise StoppedEarly(stop_reason(response), usage, text)
    return json.loads(text), usage


def send_summary(client, model: str, kind: str, pdf: bytes, title: str, date: str, effort: str | None = None,
                 members: list[str] | None = None):
    """The model's response to a document's summary request. Without an effort ([summaries] effort), the
    model's default."""
    # Streaming avoids HTTP timeouts on long transcripts.
    with client.messages.stream(**summary_request(model, kind, pdf, title, date, effort, members)) as stream:
        return stream.get_final_message()


def summarize_pdf(client, model: str, kind: str, pdf: bytes, title: str, date: str,
                  effort: str | None = None) -> tuple[dict, dict]:
    """A document's summary and its usage. Without an effort ([summaries] effort), the model's default."""
    return summary_result(send_summary(client, model, kind, pdf, title, date, effort))


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


def transcribe_pdf(client, model: str, kind: str, pdf: bytes, title: str, date: str,
                   effort: str | None = None, on_response=None) -> tuple[str, dict]:
    """The model's transcription of a whole document, a few pages a request, and its usage: the
    requests' tokens together, and each request's under "requests". Without an effort, the model's
    default. on_response, if given, is called with each request's response and tokens as it's
    answered, before anything else is done with it (so it's counted however the rest goes). Under "pages",
    where each request's text starts in the transcription, with its first and last page, so a quote found in
    it can be given its page where a request read one page alone (pipeline/factcheck.py's page_at())."""
    pages = page_count(pdf) or 1
    step = TRANSCRIBE["pages"]
    # Each request's tokens too, under "requests", since a request is priced by its own prompt's length (cost()).
    parts, usage = [], {"input_tokens": 0, "output_tokens": 0, "requests": [], "pages": []}
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
            output_config={**({"effort": effort} if effort else {}),
                           "format": {"type": "json_schema", "schema": TRANSCRIBE["schema"]}},
        ) as stream:
            response = stream.get_final_message()
        if on_response:
            on_response(response, {"input_tokens": response.usage.input_tokens,
                                   "output_tokens": response.usage.output_tokens})
        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens
        usage["requests"].append({"input_tokens": response.usage.input_tokens,
                                  "output_tokens": response.usage.output_tokens})
        if response.stop_reason != "end_turn":
            raise StoppedEarly(stop_reason(response), usage)
        parts.append(json.loads(next(b.text for b in response.content if b.type == "text"))["transcript"].strip())
        usage["pages"].append([sum(len(p) + 2 for p in parts[:-1]), first, last])
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
        record.pop("transcript_pages", None)
    elif not record.get("transcript"):
        record["plain_text"] = pdftext.plain_text(pdf)
        record["text_note"] = why
        record["needs_transcript"] = pdftext.page_texts(pdf) is None
    return record


def save_record(data_dir: Path, sha256: str, record: dict) -> None:
    """Save a document's record, without any cost or token counts an older one carried: the folder is
    public, and the cost log has each request's (cost_log.without_costs())."""
    path = summaries_dir(data_dir) / f"{sha256}.json"
    write_atomic(path, json.dumps(cost_log.without_costs(record), indent=2, ensure_ascii=False) + "\n")


def summarized_documents(data_dir: Path) -> list[tuple[str, dict, dict, dict]]:
    """(kind, meeting, document, saved record) for each meeting's latest agenda and minutes, and its
    recording's transcript until the minutes come, that have a summary, newest meeting first."""
    store, _ = listings.read(data_dir)
    out, seen = [], set()
    for meeting in store.values():
        for kind, field in DOCUMENT_FIELDS:
            # A recording's summary is shown only until the minutes come: not translated or checked after.
            if not meeting.get(field) or (kind == "recording" and meeting.get("minutes")):
                continue
            doc = meeting[field][-1]
            if doc["sha256"] in seen:
                continue
            record = cached(data_dir, doc["sha256"], kind, current=False)
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


# What cached input costs, as a share of the input price: written to the cache (5 minutes), and read from it.
CACHE_WRITE, CACHE_READ = 1.25, 0.1
# The settings' prices, dollars per million tokens. A model priced higher for a long prompt has all three
# long_ keys: a request whose prompt (its input, cached or not) is over long_prompt_tokens is priced, input
# and output, at long_input_price and long_output_price. Without them, every request is priced the same.
PRICES = ("input_price", "output_price", "long_input_price", "long_output_price")
LONG_PROMPT = ("long_prompt_tokens", "long_input_price", "long_output_price")


def cost(usage: dict, settings: dict) -> float:
    """What one request's tokens cost at the settings' prices (or several requests' together, at a price that
    doesn't depend on the prompt's length). input_tokens doesn't count cached input, which is priced apart.
    A usage with "requests" (transcribe_pdf()'s) is several requests, each priced by its own prompt."""
    if "requests" in usage:
        return sum(cost(request, settings) for request in usage["requests"])
    written, read = usage.get("cache_creation_input_tokens", 0), usage.get("cache_read_input_tokens", 0)
    paid_input = usage["input_tokens"] + CACHE_WRITE * written + CACHE_READ * read
    input_price, output_price = settings["input_price"], settings["output_price"]
    if (all(settings.get(k) is not None for k in LONG_PROMPT)
            and usage["input_tokens"] + written + read > settings["long_prompt_tokens"]):
        input_price, output_price = settings["long_input_price"], settings["long_output_price"]
    return (paid_input * input_price + usage["output_tokens"] * output_price) / 1e6


def summary_settings(config: dict) -> dict:
    """The town's [summaries] table, with the run limits filled in when it leaves them out."""
    return {"max_per_run": DEFAULT_MAX_PER_RUN, "max_cost_per_run": DEFAULT_MAX_COST_PER_RUN, **config["summaries"]}


def missing_prices(settings: dict) -> str | None:
    """What the [summaries] table lacks for its spending limit to be kept, or None: its prices, and all
    three long-prompt keys if it has any (with only some, a long prompt would be priced too low)."""
    if "input_price" not in settings or "output_price" not in settings:
        return "[summaries] needs input_price and output_price for the spending limit"
    given = [k for k in LONG_PROMPT if k in settings]
    if given and len(given) < len(LONG_PROMPT):
        return f"[summaries] has {', '.join(given)} but needs all of {', '.join(LONG_PROMPT)} to price a long prompt"
    return None


# What a model's transcription of a scan leaves in its document's record, with the plain text and note saved
# before it (own_text()).
TRANSCRIPTION = ("transcript", "transcript_source", "transcript_pages", "transcribed_at", "plain_text", "text_note")


def kept_transcription(data_dir: Path, sha256: str) -> dict:
    """The model's transcription in the document's saved record, if it has one, for a summary of the same
    document made again (a newer prompt, an agenda's start time) to keep rather than pay for again. A record
    is saved by its PDF's hash, so a scan that changed is a new document, with no record and nothing kept:
    it's transcribed again as any new scan is."""
    path = summaries_dir(data_dir) / f"{sha256}.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if old.get("transcript_source") != "ai" or not old.get("transcript") or old.get("source_sha256", sha256) != sha256:
        return {}
    return {key: old[key] for key in TRANSCRIPTION if key in old}


def save_summary(config: dict, data_dir: Path, kind: str, meeting: dict, doc: dict, pdf: bytes, result: dict,
                 usage: dict, paid: float, settings: dict, now: datetime, words: set[str], log: cost_log.Log,
                 key: str) -> bool:
    """Save a summary just made, with the document's own text, its roll calls, and its fact check, once
    its request (key) is in the cost log as a summary. Returns whether the log didn't have it yet. If the
    record can't be made (its PDF can't be read), it raises before anything is logged or saved."""
    record = {
        **split_decisions(result),
        "kind": kind,
        "source_url": doc["source_url"],
        "source_sha256": doc["sha256"],
        "model": settings["model"],
        "prompt_version": KINDS[kind]["version"],
        "generated_at": now.isoformat(timespec="seconds"),
        # A scan's transcription, kept: the document's own text still replaces it where it can be used.
        **kept_transcription(data_dir, doc["sha256"]),
    }
    if KINDS[kind].get("text"):
        # A recording's transcript: no roll calls are read from it (pipeline/votes.py reads the minutes').
        record = check_recording(config, meeting, record, pdf, words, data_dir)
    else:
        record = votes.read(own_text(record, pdf), pdf, votes.members_for(config, meeting["body"]))
        if record.get("is_minutes") is not False:
            # Checked as it's saved, so no summary is ever shown unchecked (pipeline/factcheck.py).
            words |= translate.summary_words(record)
            record["fact_check"] = factcheck.check(record, kind, factcheck.pages(pdf), words)
    # Logged before its file is written: a run stopped between the two has paid for it, and the next run
    # makes it again (the log counts the first as replaced).
    new = log.add("summary", key, paid, doc=doc["sha256"], model=settings["model"], usage=usage)
    save_record(data_dir, doc["sha256"], record)
    return new


def town_streets(data_dir: Path | None) -> frozenset[str]:
    """The town's streets (pipeline.streets.street_key()s), from its address ranges (pipeline/fetch_addresses.py),
    for a recording's misheard street names; none for a town without them."""
    path = data_dir / fetch_addresses.FILE if data_dir else None
    if not path or not path.exists():
        return frozenset()
    return _streets(path, path.stat().st_mtime_ns)


@functools.lru_cache(maxsize=4)
def _streets(path: Path, _mtime: int) -> frozenset[str]:
    names = json.loads(path.read_text(encoding="utf-8")).get("streets") or {}
    return frozenset(k for name in names if (k := street_key(name)))


def check_recording(config: dict, meeting: dict, record: dict, transcript: bytes, words, data_dir: Path | None = None) -> dict:
    """A recording's summary with its fact check against the transcript, without its times
    (pipeline/factcheck.py). The board's members' names, which the summary spells from their list where the
    transcript mishears them, are taken as words, not checked as names; its addresses are held to the town's
    street names, where the town has them (town_streets())."""
    members = {w.lower() for name in votes.members_for(config, meeting["body"]) for w in re.findall(r"[\w'’-]+", name)}
    text = transcribe.untimed(transcript.decode("utf-8"))
    taken = frozenset(set(words) | translate.summary_words(record) | members)
    return {**record, "transcript_source": "recording",
            "fact_check": factcheck.check(record, "recording", [text], taken, town_streets(data_dir))}


def unsaved(data_dir: Path, kind: str, doc: dict, model: str, error: Exception) -> str:
    """The error for a summary paid for that couldn't be saved (its PDF couldn't be read for its text,
    roll calls, or check), counted as a try (STOPPED_EARLY), so a document that fails this way every
    time isn't paid for every run."""
    tries = record_stopped_early(data_dir, doc["sha256"], "summary", model, KINDS[kind]["version"])
    return (f"{kind} {doc['id']}: the summary couldn't be saved ({error})"
            + (f"; not tried again after {tries} tries" if tries >= MAX_TRIES else ""))


# What a run says when the API account is out of credit (translate.out_of_credit).
OUT_OF_CREDIT = "stopped: the Anthropic account is out of credit; summaries resume when credit is added"


def at_batch_price(settings: dict) -> dict:
    return {**settings, **{k: settings[k] * BATCH_PRICE for k in PRICES if settings.get(k) is not None}}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_batch(data_dir: Path) -> dict | None:
    path = data_dir / BATCH_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# What the run that sent the batch had spent before it, for the run that collects it (the same job): in
# data/costs/, out of git, since the batch's file is committed.
BATCH_SPENT = "batch-spent.json"


def batch_spent(data_dir: Path, pending: dict) -> tuple[float, float]:
    """What the run that sent this batch had spent, and spent on older documents, before it: 0 if that
    isn't known here (a batch from an earlier job). A batch file from before kept them itself."""
    if "spent_before" in pending:
        return pending["spent_before"], pending.get("backlog_before", 0.0)
    spent = cost_log.read_json(data_dir / cost_log.FOLDER / BATCH_SPENT) or {}
    if spent.get("id") != pending["id"]:
        return 0.0, 0.0
    return spent.get("spent_before", 0.0), spent.get("backlog_before", 0.0)


def send_batch(client, data_dir: Path, settings: dict, queued: list[tuple[str, dict, dict, bytes]], older: set[str],
               sent_at: datetime, spent_before: float, backlog_before: float,
               translations: list[tuple[str, dict, dict, dict, str, dict]] = ()) -> dict:
    """Send the documents' summaries, and the translations' requests (kind, meeting, document, English
    record, language, and translate.batch_request()), as one batch and keep what it needs to be collected
    in data/: each document's meeting and file, and each translation's language, what it is (a translation
    or its review), the English it's of, and its model's prices; and, out of git (BATCH_SPENT), what the
    run had spent before it, so the run that collects it goes on from there."""
    created = client.messages.batches.create(requests=[
        {"custom_id": f"doc-{i}", "params": summary_request(settings["model"], kind, pdf, meeting["title"], meeting["date"],
                                                            settings.get("effort"), meeting.get("members"))}
        for i, (kind, meeting, doc, pdf) in enumerate(queued)] + [
        {"custom_id": f"tr-{i}", "params": request["params"]} for i, (*_, request) in enumerate(translations)])
    pending = {
        "id": created.id, "sent_at": sent_at.isoformat(timespec="seconds"), "model": settings["model"],
        # The prices the batch's model was sent at, so it's priced so when collected, whatever the settings by then.
        "prices": {k: settings[k] for k in PRICES + ("long_prompt_tokens",) if settings.get(k) is not None},
        "documents": [{"custom_id": f"doc-{i}", "kind": kind, "older": doc["sha256"] in older,
                       "meeting": {k: meeting[k] for k in ("title", "date", "body") if k in meeting},
                       "doc": {k: doc[k] for k in ("id", "sha256", "file", "source_url", "parts") if k in doc}}
                      for i, (kind, meeting, doc, _) in enumerate(queued)] + [
            {"custom_id": f"tr-{i}", "type": request["type"], "lang": lang, "kind": kind, "older": doc["sha256"] in older,
             "source_hash": translate.source_hash(record, kind), "model": request["model"], "prices": request["prices"],
             "meeting": {k: meeting[k] for k in ("title", "date", "body") if k in meeting},
             "doc": {k: doc[k] for k in ("id", "sha256") if k in doc}}
            for i, (kind, meeting, doc, record, lang, request) in enumerate(translations)],
    }
    write_atomic(cost_log.ignored(data_dir) / BATCH_SPENT, json.dumps(
        {"id": created.id, "spent_before": round(spent_before, 6), "backlog_before": round(backlog_before, 6)}) + "\n")
    write_atomic(data_dir / BATCH_FILE, json.dumps(pending, indent=2) + "\n")
    return pending


def collect_batch(config: dict, client, data_dir: Path, storage, settings: dict, now: datetime, words: set[str],
                  log: cost_log.Log, clock=utc_now, sleep=time.sleep, wait: bool = True) -> dict:
    """The town's batch of summaries, waited for until BATCH_WAIT after it was sent (cancelled if it isn't
    done by then), and each summary it made saved as one sent alone would be. What it didn't make (an
    error, cancelled) is left waiting, for the run to send one at a time. A batch that can't be collected
    (the API can't be reached, or a cancelled one doesn't end) is kept for the next run, and its documents
    are listed in "waiting", so they aren't sent twice. Without wait, a batch not ended yet is left as it
    is, and "not_ready" says so.

    Each request paid for is logged under the batch's id and its custom_id, so a batch collected again
    (a run stopped before it was done with it) counts only what it hadn't yet, in the log and in "paid".

    A translation or review the batch made is saved as translate.collected() says, and counted in
    "translated"; one it didn't make is left for the town's next batch."""
    pending = load_batch(data_dir)
    out = {"done": 0, "failed": 0, "paid": 0.0, "paid_backlog": 0.0, "translated": 0,
           "tokens": {"input_tokens": 0, "output_tokens": 0}, "errors": [], "waiting": set()}
    if not pending:
        return out
    batches = client.messages.batches
    shas = {d["doc"]["sha256"] for d in pending["documents"]}
    try:
        deadline = datetime.fromisoformat(pending["sent_at"]) + BATCH_WAIT
        batch = batches.retrieve(pending["id"])
        if not wait and batch.processing_status != "ended":
            out["not_ready"] = True
            out["waiting"] = shas
            return out
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
    prices = at_batch_price(pending.get("prices") or settings)
    model = pending["model"]
    for entry in pending["documents"]:
        kind, meeting, doc = entry["kind"], entry["meeting"], entry["doc"]
        key = cost_log.batch_key(pending["id"], entry["custom_id"])
        if entry.get("type"):
            continue

        def paid_for(new: bool, paid: float) -> bool:
            """Count a request this collecting logged (new); one already logged was counted before."""
            if new:
                out["paid"] += paid
                if entry["older"]:
                    out["paid_backlog"] += paid
            return new

        def failed(paid: float, usage: dict | None) -> bool:
            return paid_for(log.add("failed", key, paid, doc=doc["sha256"], model=model, usage=usage), paid)

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
            out["failed"] += 1
            # Counted as a try once, when it's first logged.
            tries = (record_stopped_early(data_dir, doc["sha256"], "summary", model, KINDS[kind]["version"])
                     if failed(cost(e.usage, prices), e.usage) else 0)
            out["errors"].append(f"{kind} {doc['id']}: {e}" + (f"; not tried again after {tries} tries"
                                                               if tries >= MAX_TRIES else ""))
            continue
        except Exception as e:
            # A response that can't be read (not JSON): paid for, and sent again one at a time.
            used = getattr(result.message, "usage", None)
            usage = {"input_tokens": used.input_tokens, "output_tokens": used.output_tokens} if used else None
            failed(cost(usage, prices) if usage else 0.0, usage)
            out["failed"] += 1
            out["errors"].append(f"{kind} {doc['id']}: the batch's summary couldn't be read ({e}); sent one at a time")
            continue
        paid = cost(usage, prices)
        try:
            pdf = document(storage, kind, doc)
        except FetchError as e:
            # Paid for, but it can't be saved without its document's text and check: made again later.
            failed(paid, usage)
            out["failed"] += 1
            out["errors"].append(f"{kind} {doc['id']}: {e}")
            continue
        try:
            paid_for(save_summary(config, data_dir, kind, meeting, doc, pdf, made, usage, paid,
                                  {**settings, "model": model}, now, words, log, key), paid)
        except Exception as e:  # one bad document must not stop the rest
            failed(paid, usage)
            out["failed"] += 1
            out["errors"].append(unsaved(data_dir, kind, doc, model, e))
            continue
        for k in out["tokens"]:
            out["tokens"][k] += usage[k]
        out["done"] += 1
    collect_translations(config, data_dir, pending, results, now, log, out)
    (data_dir / BATCH_FILE).unlink()
    return out


def collect_translations(config: dict, data_dir: Path, pending: dict, results: dict, now: datetime,
                         log: cost_log.Log, out: dict) -> None:
    """The translations and reviews a collected batch made (its entries with a "type"), each saved as
    translate.collected() says, at half its model's prices, into out (collect_batch()'s). One already in
    the cost log was saved by an earlier collecting of the same batch, and is left. One the batch didn't
    make, or made for an English summary that has changed since, is left for the town's next batch."""
    entries = [e for e in pending["documents"] if e.get("type")]
    if not entries:
        return
    documents = {doc["sha256"]: (kind, meeting, doc, record) for kind, meeting, doc, record in summarized_documents(data_dir)}
    for entry in entries:
        lang, what, sha = entry["lang"], entry["type"], entry["doc"]["sha256"]
        key = cost_log.batch_key(pending["id"], entry["custom_id"])
        if key in log.keys:
            continue
        name = f"{lang} {what} of {entry['kind']} {entry['doc']['id']}"
        result = results.get(entry["custom_id"])
        if result is None or result.type != "succeeded":
            # Not made, and not paid for.
            out["errors"].append(f"{name}: not made in the batch ({result.type if result else 'missing'}); "
                                 "sent in the town's next batch")
            continue
        usage = translate.usage_of(result.message)
        paid = cost(usage, at_batch_price(entry["prices"]))

        def failed(why: str) -> None:
            """Paid for, though nothing is saved."""
            if log.add("failed", key, paid, doc=sha, lang=lang, model=entry["model"], usage=usage):
                out["paid"] += paid
                if entry["older"]:
                    out["paid_backlog"] += paid
            out["failed"] += 1
            out["errors"].append(f"{name}: {why}")

        found = documents.get(sha)
        if not found or translate.source_hash(found[3], found[0]) != entry["source_hash"]:
            failed("its English summary has changed since it was sent; made again from the new one")
            continue
        kind, meeting, doc, record = found
        try:
            translate.collected(config, data_dir, entry, record, kind, meeting, doc, result.message, now)
        except Exception as e:  # cut off (StoppedEarly), or an answer that can't be read: made again later
            failed(str(e))
            continue
        if log.add(what, key, paid, doc=sha, lang=lang, model=entry["model"], usage=usage):
            out["paid"] += paid
            if entry["older"]:
                out["paid_backlog"] += paid
        out["translated"] += 1


def nothing() -> dict:
    """A run's counts when it made and sent nothing."""
    return {"summarized": 0, "collected": 0, "queued": 0, "queued_translations": 0, "laid_out": 0, "transcribed": 0,
            "translated": 0, "drafted_texts": 0, "remaining": 0, "failed": 0, "errors": [], "stopped": None,
            "estimated_cost": 0.0, "month_cost": None, "input_tokens": 0, "output_tokens": 0}


class SummaryRun:
    """One run of a town's summaries: what it has spent of its limits, what it made, sent and left, and
    one method for each kind of work, which run() calls in turn. Each step spends from the same budget,
    so a step stops when it's spent, and the steps after it find it so. The steps take run()'s order:
    start() opens the cost log every request is logged in, and choose() finds what's waiting, which
    transcribe_scans() waits for."""

    def __init__(self, config: dict, client, data_dir: Path, now: datetime | None = None,
                 allowance: float | None = None, backlog_allowance: float | None = None, clock=utc_now,
                 sleep=time.sleep):
        self.config, self.client, self.data_dir = config, client, data_dir
        self.allowance, self.backlog_allowance = allowance, backlog_allowance
        self.clock, self.sleep = clock, sleep
        self.settings = summary_settings(config)
        self.now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
        self.storage = open_documents(config, data_dir)
        # The words the fact check takes as no one's name, as the town's summaries use them.
        self.words = translate.town_words(data_dir)
        # The batch the town has waiting, if any.
        self.pending = load_batch(data_dir)
        # Without prices the spending limit can't be enforced, so nothing is sent.
        self.unpriced = missing_prices(self.settings)
        # Each request paid for is logged as it's answered (start()).
        self.log = None
        self.done, self.errors, self.tokens = 0, [], {"input_tokens": 0, "output_tokens": 0}
        self.spent, self.spent_backlog = 0.0, 0.0
        self.stopped, self.too_costly = None, []
        # Documents whose summary, transcription, or translation was tried and failed (for main()'s exit status).
        self.failed = 0
        self.collected, self.waiting, self.collected_translations = 0, set(), 0
        self.translated, self.drafted_texts, self.laid_out, self.transcribed = 0, 0, 0, 0
        # What choose() picks: the documents and translations to make, in order, and the older documents among them.
        self.work, self.older, self.resummarized, self.waiting_count = [], set(), set(), 0
        self.batching, self.batching_translations = False, False
        # What goes in this run's batch, counted at its estimate until the batch is collected.
        self.queued, self.queued_bytes, self.queued_cost, self.queued_backlog = [], 0, {}, 0.0
        self.queued_translations, self.queued_translation_keys = [], set()
        self.queued_translation_cost, self.queued_translation_backlog = 0.0, 0.0

    def out_of_credit(self, e: Exception) -> bool:
        """Whether a request failed for want of credit; if so, the run stops sending them."""
        if not translate.out_of_credit(e):
            return False
        self.errors.append(OUT_OF_CREDIT)
        self.stopped = self.stopped or "out of credit"
        return True

    def over_budget(self, older: bool) -> bool:
        """Whether this run's limits are spent: the town's, the network's, and, for older documents, the
        network's share for them."""
        return (self.spent >= self.settings["max_cost_per_run"]
                or (self.allowance is not None and self.spent >= self.allowance)
                or (older and self.backlog_allowance is not None and self.spent_backlog >= self.backlog_allowance))

    def room(self, older: bool) -> float:
        """What's left of this run's limits, as over_budget() counts them."""
        room = [self.settings["max_cost_per_run"] - self.spent]
        if self.allowance is not None:
            room.append(self.allowance - self.spent)
        if older and self.backlog_allowance is not None:
            room.append(self.backlog_allowance - self.spent_backlog)
        return min(room)

    def start(self) -> None:
        """Open the cost log: each request paid for is logged as it's answered, and the month's ledger made
        from the log (pipeline/cost_log.py), so a run stopped at any point has counted all it paid for but
        the request in flight."""
        self.log = open_log(self.data_dir, self.settings, self.now, self.config)

    def collect(self, collecting: bool, wait: bool) -> bool:
        """Collect the batch of summaries the town has waiting, if any; False when it's still running."""
        if not self.pending or self.unpriced:
            return True
        got = collect_batch(self.config, self.client, self.data_dir, self.storage, self.settings, self.now,
                            self.words, self.log, self.clock, self.sleep, wait=wait)
        if got.get("not_ready"):
            self.errors += got["errors"]
            return False
        self.collected, self.waiting, self.failed = got["done"], got["waiting"], got["failed"]
        self.collected_translations = got.get("translated", 0)
        self.errors += got["errors"]
        for k in self.tokens:
            self.tokens[k] += got["tokens"][k]
        if collecting:
            # The same run's budget, as its first part left it.
            before, backlog_before = batch_spent(self.data_dir, self.pending)
            self.spent, self.spent_backlog = before + got["paid"], backlog_before + got["paid_backlog"]
        else:
            # An earlier run's batch, paid for now.
            self.spent, self.spent_backlog = got["paid"], got["paid_backlog"]
        return True

    def still_running(self) -> dict:
        """The result of a run that found its batch not ended yet: a later collecting run takes it."""
        self.log.close()
        return {**nothing(), "errors": self.errors, "stopped": "the batch isn't done yet"}

    def choose(self, limit: int, batch: bool, collecting: bool) -> None:
        """Choose the documents to summarize, up to limit, new ones first, with the translations between
        them, and whether they and the translations go in a batch."""
        todo = [t for t in pending_documents(self.data_dir, self.now.date().isoformat(), self.settings["model"],
                                             self.settings.get("since"))
                if doc_key(t) not in self.waiting]
        # Every document still waiting, for the run's count of what's left.
        self.waiting_count = len(todo)
        # Documents whose summary is to be made again, whose translations of the summary they have now wait for it.
        self.resummarized = {doc_key(t) for t in todo}
        if collecting:
            # Only what the batch didn't make is sent one at a time; any other document waiting goes in the next
            # run's batch, at half the price, rather than at the full price now.
            retry = {d["doc"]["sha256"] for d in self.pending["documents"] if not d.get("type")}
            todo = [t for t in todo if doc_key(t) in retry]
        new = [t for t in todo if is_new(t, self.now)]
        backlog = [t for t in todo if not is_new(t, self.now)]
        self.older = {doc["sha256"] for _, _, doc in backlog}
        # With batch, the documents that fit in one; and while an earlier batch is still out, none.
        self.batching = batch and not self.waiting and not collecting
        # The translations that need it go in the batch too: in the daily run's, with its summaries, and in the
        # collecting run's, sent once it has collected the batch, with the reviews of the translations it made
        # and the translations of the summaries it saved (translate.py's "Translations in a batch"). Not while a
        # batch is still out: one that couldn't be collected, or wasn't (a run without prices).
        self.batching_translations = (batch or collecting) and not self.waiting and not (self.data_dir / BATCH_FILE).exists()
        work = (new + backlog)[:limit]
        # Translations come right after the new documents' summaries, new documents' first: an older
        # summary already on the site waits for its translation no longer than a new one.
        work.insert(sum(1 for item in work if doc_key(item) not in self.older), TRANSLATE)
        if self.unpriced:
            work = []
            self.errors.append(f"{self.unpriced}; nothing summarized")
        self.work = work

    def draft_texts(self) -> None:
        """Draft the town's own text and names that its pages in another language would show in English
        (a new board, a new 311 category, a new town's config), first: they cost a fraction of a summary,
        and the build that follows shows them."""
        for lang in translate.languages(self.config) if not self.unpriced else []:
            tsettings = translate.settings(self.config)
            if tsettings["input_price"] is None or tsettings["output_price"] is None:
                break
            from pipeline.build_site import needed_texts
            try:
                needed = needed_texts(self.config, self.data_dir, lang, self.now)
                texts = needed["config"] + needed["data"] + translate.drafts_to_remake(self.data_dir, lang)
                if texts:
                    # Past the budget too, up to DRAFT_FLOOR: a few texts cost a fraction of a cent, and
                    # without them a page shows English.
                    left = None if self.allowance is None else max(self.allowance - self.spent, DRAFT_FLOOR)
                    count, paid = translate.draft_texts(self.client, self.config, self.data_dir, lang, texts, self.now,
                                                        cost, left, self.log)
                    self.drafted_texts += count
                    self.spent += paid
            except Exception as e:
                self.failed += 1
                if self.out_of_credit(e):
                    self.work = []
                    break
                self.errors.append(f"{lang} drafts of the town's text: {e}")

    def summaries(self) -> None:
        """Summarize the chosen documents in order, and translate summaries where choose() put them."""
        for item in self.work:
            if item is TRANSLATE:
                self.translate_summaries(only_new=True)
                self.translate_summaries(only_new=False)
                continue
            if not self.summarize(*item):
                break
        if self.too_costly:
            self.stopped = self.stopped or (f"{len(self.too_costly)} waiting for a run with room for them: "
                                            + ", ".join(self.too_costly[:5]) + (", ..." if len(self.too_costly) > 5 else ""))

    def summarize(self, kind: str, meeting: dict, doc: dict) -> bool:
        """Summarize one document, now or in the batch, if it fits this run's limits; False when nothing
        more can be sent."""
        if self.spent >= self.settings["max_cost_per_run"]:
            self.errors.append("stopped at the town's spending limit for one run ([summaries] max_cost_per_run)")
            return False
        if self.allowance is not None and self.spent >= self.allowance:
            self.stopped = "stopped at this run's share of the network's monthly summary budget"
            return False
        older = doc["sha256"] in self.older
        if self.backlog_allowance is not None and self.spent_backlog >= self.backlog_allowance and older:
            # The backlog comes last, so nothing after this is new.
            self.stopped = "older documents wait: this run's share for them is spent"
            return False
        try:
            pdf = document(self.storage, kind, doc)
        except FetchError as e:
            self.failed += 1
            self.errors.append(f"{kind} {doc['id']}: {e}")
            return True
        if KINDS[kind].get("text"):
            # A transcript's names are spelled from the board's list of members.
            meeting = {**meeting, "members": votes.members_for(self.config, meeting["body"])}
        pages = length(kind, pdf)
        if pages and pages > MAX_PAGES:
            # Said once: from the next run it's left out of what's waiting, and its page says why.
            record_too_long(self.data_dir, doc["sha256"], pages)
            self.errors.append(f"{kind} {doc['id']}: {pages} pages, over the {MAX_PAGES}-page limit")
            return True
        # Sent only if what it's likely to cost fits what's left of this run's limits; otherwise it waits
        # for a run with more room, and smaller documents after it may still go.
        in_batch = self.batching and self.queued_bytes + len(pdf) <= BATCH_MAX_BYTES
        estimate = estimated_cost(kind, pages or 1, self.settings) * (BATCH_PRICE if in_batch else 1)
        if estimate > self.room(older):
            self.too_costly.append(f"{kind} {doc['id']}")
            return True
        if in_batch:
            # Counted at its estimate until the batch is collected, so the rest of the run stays in budget.
            self.queued.append((kind, meeting, doc, pdf))
            self.queued_bytes += len(pdf)
            self.queued_cost[doc["sha256"]] = estimate
            self.spent += estimate
            if older:
                self.spent_backlog += estimate
                self.queued_backlog += estimate
            return True
        return self.send_one(kind, meeting, doc, pdf)

    def send_one(self, kind: str, meeting: dict, doc: dict, pdf: bytes) -> bool:
        """Summarize one document now, on its own; False when nothing more can be sent (out of credit)."""
        settings = self.settings
        try:
            response = send_summary(self.client, settings["model"], kind, pdf, meeting["title"], meeting["date"],
                                    settings.get("effort"), meeting.get("members"))
        except Exception as e:  # one bad document must not stop the rest
            self.failed += 1
            if self.out_of_credit(e):
                # Every remaining request would fail the same way.
                return False
            self.errors.append(f"{kind} {doc['id']}: {e}")
            return True
        key = cost_log.request_key(response)
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        paid = cost(usage, settings)
        self.spent += paid
        if doc["sha256"] in self.older:
            self.spent_backlog += paid

        def paid_for_nothing() -> None:
            """Paid for, though nothing is saved."""
            self.log.add("failed", key, paid, doc=doc["sha256"], model=settings["model"], usage=usage)
            self.failed += 1

        try:
            result, usage = summary_result(response)
        except StoppedEarly as e:
            paid_for_nothing()
            tries = record_stopped_early(self.data_dir, doc["sha256"], "summary", settings["model"], KINDS[kind]["version"])
            self.errors.append(f"{kind} {doc['id']}: {e}" + (f"; not tried again after {tries} tries" if tries >= MAX_TRIES else ""))
            return True
        except Exception as e:  # a response that can't be read: made again by a later run
            paid_for_nothing()
            self.errors.append(f"{kind} {doc['id']}: {e}")
            return True
        try:
            save_summary(self.config, self.data_dir, kind, meeting, doc, pdf, result, usage, paid, settings, self.now,
                         self.words, self.log, key)
        except Exception as e:  # one bad document must not stop the rest
            paid_for_nothing()
            self.errors.append(unsaved(self.data_dir, kind, doc, settings["model"], e))
            return True
        for k in self.tokens:
            self.tokens[k] += usage[k]
        self.done += 1
        return True

    def translate_summaries(self, only_new: bool) -> None:
        """Translate the summaries that need it, new documents' or the rest, within this run's budget."""
        langs = translate.languages(self.config)
        if not langs:
            return
        tsettings = translate.settings(self.config)
        if tsettings["input_price"] is None or tsettings["output_price"] is None:
            self.errors.append("[summaries] needs translation_input_price and translation_output_price for its "
                               "translation_model; nothing translated")
            return
        # The town's glossary, whose terms each translation writes as it gives them (pipeline/glossary.py).
        terms = glossary.entries(self.config)
        # Documents that wait in a batch not collected, and, when translations go in a batch, those whose
        # summary is to be made again (in this batch, or a later one): their translations wait for them.
        held = self.waiting | (self.resummarized if self.batching_translations else set())
        for kind, meeting, doc, record in summarized_documents(self.data_dir):
            if is_new((kind, meeting, doc), self.now) != only_new or doc["sha256"] in held:
                continue
            for lang in langs:
                if (doc["sha256"], lang) in self.queued_translation_keys:
                    continue
                if translate.current(self.data_dir, lang, doc["sha256"], record, kind, terms):
                    continue
                if self.over_budget(older=not only_new):
                    self.stopped = self.stopped or "translations wait: this run's budget for them is spent"
                    return
                request = (translate.batch_request(self.config, self.data_dir, lang, kind, meeting, doc, record)
                           if self.batching_translations else None)
                if request:
                    # Counted at its estimate until the batch is collected, as the batch's summaries are.
                    estimate = translate.estimated_cost(request, request["prices"], cost) * BATCH_PRICE
                    if estimate > self.room(older=not only_new):
                        self.stopped = self.stopped or "translations wait: this run's budget for them is spent"
                        return
                    self.queued_translations.append((kind, meeting, doc, record, lang, request))
                    self.queued_translation_keys.add((doc["sha256"], lang))
                    self.queued_translation_cost += estimate
                    self.spent += estimate
                    if not only_new:
                        self.spent_backlog += estimate
                        self.queued_translation_backlog += estimate
                    continue
                try:
                    _, paid = translate.make(self.client, self.config, self.data_dir, lang, kind, meeting, doc, record,
                                             self.now, cost, self.log)
                except StoppedEarly as e:
                    # Logged as failed by make().
                    paid = cost(e.usage, tsettings)
                    self.failed += 1
                    self.errors.append(f"{lang} translation of {kind} {doc['id']}: {e}")
                except Exception as e:
                    self.failed += 1
                    if self.out_of_credit(e):
                        return
                    self.errors.append(f"{lang} translation of {kind} {doc['id']}: {e}")
                    continue
                else:
                    self.translated += 1
                self.spent += paid
                if not only_new:
                    self.spent_backlog += paid

    def lay_out_texts(self) -> None:
        """Readable text, from the documents' own text, which costs nothing: summaries saved before it was
        used, or before the layout rules last changed. Roll call votes, also free, are read with it, and
        again when the rules or the body's members change; and the summary is checked against the same
        text (pipeline/factcheck.py). Up to TEXT_PER_RUN documents."""
        for kind, meeting, doc, record in summarized_documents(self.data_dir):
            if self.laid_out >= TEXT_PER_RUN:
                break
            if KINDS[kind].get("text"):
                # A recording's summary has no text of its own to lay out: only its check is made again, when it changes.
                if factcheck.current(record):
                    continue
                try:
                    save_record(self.data_dir, doc["sha256"],
                                check_recording(self.config, meeting, record, document(self.storage, kind, doc),
                                                self.words, self.data_dir))
                except Exception as e:  # free, and tried again next run; the rest go on
                    self.errors.append(f"{kind} {doc['id']}: its transcript couldn't be read ({e})")
                    continue
                self.laid_out += 1
                continue
            members = votes.members_for(self.config, meeting["body"])
            text_current = record.get("text_version") == pdftext.VERSION
            if text_current and votes.current(record, members) and factcheck.current(record):
                continue
            try:
                pdf = self.storage.get(KINDS[kind]["folder"], doc["file"])
            except FetchError as e:
                self.errors.append(f"{kind} {doc['id']}: {e}")
                continue
            try:
                record = votes.read(record if text_current else own_text(record, pdf), pdf, members)
                if record.get("is_minutes") is not False:
                    record["fact_check"] = factcheck.check(record, kind, factcheck.pages(pdf), self.words)
                save_record(self.data_dir, doc["sha256"], record)
            except Exception as e:  # free, and tried again next run; the rest go on
                self.errors.append(f"{kind} {doc['id']}: its text couldn't be read ({e})")
                continue
            self.laid_out += 1

    def send_queued(self) -> None:
        """Send this run's batch, if anything is queued for one. If it can't be sent, the summaries are sent one
        at a time instead, within the run's limits at their own price; the translations wait for the next batch."""
        if not (self.queued or self.queued_translations):
            return
        try:
            send_batch(self.client, self.data_dir, self.settings, self.queued, self.older, self.clock(),
                       self.spent - sum(self.queued_cost.values()) - self.queued_translation_cost,
                       self.spent_backlog - self.queued_backlog - self.queued_translation_backlog,
                       self.queued_translations)
        except Exception as e:
            self.errors.append(f"the batch of {len(self.queued)} summaries and {len(self.queued_translations)} "
                               f"translations couldn't be sent ({e}); the summaries sent one at a time")
            self.spent -= sum(self.queued_cost.values()) + self.queued_translation_cost
            self.spent_backlog -= self.queued_backlog + self.queued_translation_backlog
            self.queued_translations = []
            for kind, meeting, doc, pdf in self.queued:
                estimate = self.queued_cost[doc["sha256"]] / BATCH_PRICE
                if estimate > self.room(older=False):
                    self.too_costly.append(f"{kind} {doc['id']}")
                    continue
                if not self.send_one(kind, meeting, doc, pdf):
                    break
            self.queued = []

    def transcribe_scans(self) -> None:
        """The model's transcription of scans, which screen readers can't read, only once every summary
        waiting is done, and from what's left of this run's budget, as for the backlog."""
        # Those in this run's batch are still waiting, as are those in a batch not yet collected.
        summaries_left = self.waiting_count - self.done + len(self.waiting)
        if summaries_left or self.stopped:
            return
        settings = self.settings
        for kind, meeting, doc, record in summarized_documents(self.data_dir):
            if not record.get("needs_transcript") or record.get("transcript"):
                continue
            if gave_up(self.data_dir, doc["sha256"], "transcript", settings["model"], TRANSCRIBE["version"]):
                continue
            if self.over_budget(older=True):
                self.stopped = self.stopped or "transcriptions wait: this run's budget for them is spent"
                break
            try:
                pdf = self.storage.get(KINDS[kind]["folder"], doc["file"])
            except FetchError as e:
                self.failed += 1
                self.errors.append(f"{kind} {doc['id']}: {e}")
                continue
            pages = page_count(pdf)
            if pages and pages > MAX_PAGES:
                continue

            def log_pages(response, usage: dict) -> None:
                """Each request logged as it's answered: some pages transcribed, or one that stopped early."""
                self.log.request(response, "transcript" if response.stop_reason == "end_turn" else "failed",
                                 cost(usage, settings), doc=doc["sha256"], model=settings["model"], usage=usage)
            try:
                text, usage = transcribe_pdf(self.client, settings["model"], kind, pdf, meeting["title"], meeting["date"],
                                             settings.get("effort"), log_pages)
            except StoppedEarly as e:
                paid = cost(e.usage, settings)
                self.spent += paid
                self.spent_backlog += paid
                self.failed += 1
                tries = record_stopped_early(self.data_dir, doc["sha256"], "transcript", settings["model"],
                                             TRANSCRIBE["version"])
                self.errors.append(f"transcript of {kind} {doc['id']}: {e}"
                                   + (f"; not tried again after {tries} tries" if tries >= MAX_TRIES else ""))
                continue
            except Exception as e:
                self.failed += 1
                if self.out_of_credit(e):
                    break
                self.errors.append(f"transcript of {kind} {doc['id']}: {e}")
                continue
            paid = cost(usage, settings)
            self.spent += paid
            self.spent_backlog += paid
            record = {**record, "transcript": text, "transcript_source": "ai", "transcript_pages": usage["pages"],
                      "transcribed_at": self.now.isoformat(timespec="seconds")}
            record.pop("needs_transcript", None)
            try:
                if record.get("is_minutes") is not False:
                    # A scan's summary can now be checked, against the transcription (weaker: both are the model's).
                    record["fact_check"] = factcheck.check(record, kind, factcheck.pages(pdf), self.words)
                save_record(self.data_dir, doc["sha256"], record)
            except Exception as e:  # one bad document must not stop the rest
                self.failed += 1
                tries = record_stopped_early(self.data_dir, doc["sha256"], "transcript", settings["model"],
                                             TRANSCRIBE["version"])
                self.errors.append(f"transcript of {kind} {doc['id']}: it couldn't be saved ({e})"
                                   + (f"; not tried again after {tries} tries" if tries >= MAX_TRIES else ""))
                continue
            for k in self.tokens:
                self.tokens[k] += usage[k]
            self.transcribed += 1

    def result(self) -> dict:
        """Close the cost log, and say what the run made, sent, and left."""
        self.log.close()
        return {"summarized": self.done + self.collected, "collected": self.collected, "queued": len(self.queued),
                "queued_translations": len(self.queued_translations), "laid_out": self.laid_out,
                "transcribed": self.transcribed, "translated": self.translated + self.collected_translations,
                "drafted_texts": self.drafted_texts, "remaining": max(self.waiting_count - self.done, 0),
                "failed": self.failed, "errors": self.errors, "stopped": self.stopped,
                "estimated_cost": round(self.spent, 2), "month_cost": round(self.log.month_cost(), 2), **self.tokens}


def run(config: dict, client, data_dir: Path, limit: int, now: datetime | None = None,
        allowance: float | None = None, backlog_allowance: float | None = None, batch: bool = False,
        collecting: bool = False, ready_only: bool = False, clock=utc_now, sleep=time.sleep) -> dict:
    """Summarize up to limit documents, new ones first, within the town's run limits and the
    network's allowances for this run (None: no network limit).

    A batch of summaries the town has waiting is collected first. With batch, the documents are sent as
    one batch, left for the job to collect. collecting is the job's run that collects the batch its first
    run sent: it goes on from what that run had spent, and sends one at a time what the batch didn't make.
    With ready_only too, a batch not ended yet is left for a later collecting run, and nothing else is done."""
    steps = SummaryRun(config, client, data_dir, now, allowance, backlog_allowance, clock, sleep)
    if collecting and not steps.pending:
        # Nothing to collect: the run that sent nothing has already done the rest.
        return nothing()
    steps.start()
    if not steps.collect(collecting, wait=not (collecting and ready_only)):
        return steps.still_running()
    steps.choose(limit, batch, collecting)
    steps.draft_texts()
    # The documents' summaries, with the translations after the new ones'.
    steps.summaries()
    steps.lay_out_texts()
    # The translations of older documents summarized in this run, from what's left of the budget for them.
    if not steps.stopped:
        steps.translate_summaries(only_new=False)
    steps.send_queued()
    steps.transcribe_scans()
    return steps.result()


# What a run made, or sent to be made: a run that made any of these didn't fail.
MADE = ("summarized", "queued", "queued_translations", "transcribed", "translated", "drafted_texts")


def exit_status(summary: dict, collecting: bool = False) -> int:
    """The step's exit status: 1 when the run tried documents and every one failed (each request
    errored or stopped early, or its PDF couldn't be fetched or saved), else 0. A run with nothing to
    do, or stopped by its budget before it tried any, succeeds. pipeline.update reports a step that
    fails as a warning and goes on, so a daily run isn't stopped by it. A collecting run
    (COLLECT_ENV) gives 0 whatever happened, its errors printed as warnings: pipeline.network's report
    fails the whole network run when a step fails in a run that didn't fetch, which collecting is
    meant never to do."""
    if collecting or not summary["failed"]:
        return 0
    return 0 if any(summary.get(k) for k in MADE) else 1


def dollars(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    return float(value) if value else None


def allowances(month: str) -> tuple[float | None, float | None, str | None]:
    """This run's share of the network's budget, in dollars, and of it for older documents: from the shares
    (fractions of the month's budget) when the network gives them, else from the dollars, else None (no
    network limit). And a warning, when a share is given without the budget to take it of: then nothing
    but the town's own text is drafted."""
    shares = [os.environ.get(name, "").strip() for name in (ALLOWANCE_SHARE_ENV, BACKLOG_SHARE_ENV)]
    if not any(shares):
        return dollars(ALLOWANCE_ENV), dollars(BACKLOG_ALLOWANCE_ENV), None
    monthly = cost_log.monthly_budget(month)
    if monthly is None:
        return 0.0, 0.0, f"{ALLOWANCE_SHARE_ENV} is set but SUMMARY_BUDGET isn't; nothing summarized"
    # Down to the cent, as the network rounded the dollars the share was made from (the share is rounded too).
    cents = lambda share: math.floor(float(share) * monthly * 100 + 1e-6) / 100 if share else None
    return cents(shares[0]), cents(shares[1]), None


def ledger(config: dict, data_dir: Path, check: bool, months: list[str] | None = None) -> int:
    """`ledger`: the ledger's rows made again from the cost logs; with check, the logs compared with the files
    instead (ledger_differences), each difference printed, and nothing changed. Exit status 1 if any."""
    store = open_store(config, data_dir)
    if not check:
        rows = cost_log.rebuild(store)
        made = [m for m in store.months() if m in rows]
        print(f"The ledger's rows made again from the log: {', '.join(made) or 'none'}")
        return 0
    settings = summary_settings(config) if configured(config, "summaries") else {}
    differences = ledger_differences(data_dir, settings, months, store)
    for line in differences:
        print(f"::warning::{line}")
    logged = months or store.months()
    print(f"{len(differences)} difference(s) between the cost log and the files"
          + (f" ({', '.join(logged)})" if logged else " (no month has a log)"))
    return 1 if differences else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", choices=["ledger"],
                        help="ledger: make the ledger (summary-costs.json) again from the cost logs")
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--limit", type=int, help="max documents this run")
    parser.add_argument("--check", action="store_true",
                        help="with ledger: compare the cost logs with the files saved, and list each difference")
    parser.add_argument("--month", action="append", help="with ledger --check: only this month (YYYY-MM); repeatable")
    args = parser.parse_args()
    if args.command == "ledger":
        return ledger(load_config(args.town), args.data, args.check, args.month)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::notice::ANTHROPIC_API_KEY is not set; skipping summaries.")
        return 0
    import anthropic

    config = load_config(args.town)
    if not configured(config, "summaries"):
        return 0
    client = anthropic.Anthropic(max_retries=3)
    # "1": collect the town's batch, waiting for it; "ready": only if it has ended.
    collecting = os.environ.get(COLLECT_ENV) in ("1", "ready")
    allowance, backlog_allowance, warning = allowances(
        datetime.now(ZoneInfo(config["site"]["timezone"])).strftime("%Y-%m"))
    if warning:
        print(f"::warning::{warning}")
    try:
        summary = run(config, client, args.data, args.limit or summary_settings(config)["max_per_run"],
                      allowance=allowance, backlog_allowance=backlog_allowance,
                      batch=os.environ.get(BATCH_ENV) == "1" and not collecting, collecting=collecting,
                      ready_only=os.environ.get(COLLECT_ENV) == "ready")
    except cost_log.StoreError as e:
        # Nothing is sent that can't be counted. Said without the error's details (the store's address).
        print(f"::error::{e}; nothing summarized")
        return 1
    print(json.dumps({k: v for k, v in summary.items() if k not in COST_FIELDS}, indent=2))
    for error in summary["errors"]:
        print(f"::warning::{error}")
    if summary["stopped"]:
        print(f"::notice::{summary['stopped']}")
    return exit_status(summary, collecting)


if __name__ == "__main__":
    sys.exit(main())
