"""What a town's requests to the model cost: a line for each one, and each month's totals made from them.

Every request the model answers is paid for, whether what it made is saved or not. Each one is
written, as soon as it's answered, to the month's log, data/costs/<YYYY-MM>.jsonl (the month in the
town's time zone, as the run's), one JSON object a line:

    {"at": "2026-10-15T09:12:03-04:00", "kind": "summary", "key": "msg_01...", "doc": "<SHA-256>",
     "model": "...", "usage": {"input_tokens": 41200, "output_tokens": 1650}, "cost": 0.06}

kind is what the request was for: summary (a summary saved), transcript (some pages of a scan
transcribed), translation (a summary translated), draft (a batch of the town's own text drafted in
another language), review (a translation's or a draft's meaning reviewed), failed (one that stopped
early, or whose result couldn't be read or saved), or seed (the month's costs from before its log,
below). A translation's, draft's, or review's line also has its language, "lang". key names the
request: its response's id, or for one request of a batch, the batch's id and the request's
custom_id. A key the month's log already has isn't written again, so each request counts once
however often it's recorded: a batch collected again after a run was stopped partway, or a write
tried again.

Each line is one append, flushed to disk (fsync) before the run goes on, so a run stopped at any
moment has counted every request but the one in flight. A line cut short by the stop is skipped
when the log is read, and the next one starts on a line of its own.

The month's row of data/summary-costs.json, which the network reads for its monthly budget, is made
from the month's log after each line, in the form it has always had: cost and documents for each
document's latest summary this month, replaced_cost for the earlier summaries of a document made
again this month, failed_cost, transcript_cost, and translation_cost and translations (translations,
their reviews, and drafts; each document's translation into a language counts once, and a batch of
drafts as one). Making it reads the one month's log, never the summaries. A month whose log has no
lines keeps its row as it is, as do the months before the town had a log.

A town from before the log (no data/costs/ folder) starts one with the current month, once: a line
for each summary and translation saved this month, and each batch of drafts, at the cost its file
records, and one seed line with the rest of the month's row (failed, transcript, and replaced costs,
which no file records). The month's total goes on from what it was.

The network commits each town's data, putting its commit after any other run's for the same town
(.github/scripts/commit-data.sh). Two runs that both added lines to a month's log would conflict on
the same lines, so the folder's .gitattributes merges the logs with git's union driver, which keeps
both runs' lines; a line in both is read once, by its key.

pipeline.summarize's `ledger --check` compares the logs with the costs the summary and translation
files record, and lists every difference without changing either.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.files import write_atomic

FOLDER = "costs"
# What each month cost, made from the logs; the network reads it (pipeline/network.py's SUMMARY_LEDGER).
LEDGER = "summary-costs.json"
# What a month's row of the ledger counts as paid for.
COSTS = ("cost", "failed_cost", "transcript_cost", "translation_cost", "replaced_cost")
# The kinds of line that count as translation_cost.
TRANSLATING = ("translation", "draft", "review")
KINDS = ("summary", "transcript", "failed", "seed") + TRANSLATING
# Kept with the logs: two runs' lines added to the same log are both kept when their commits are merged.
GITATTRIBUTES = "*.jsonl merge=union\n"


def path(data_dir: Path, month: str) -> Path:
    return data_dir / FOLDER / f"{month}.jsonl"


def months(data_dir: Path) -> list[str]:
    """The months the town has a log for."""
    folder = data_dir / FOLDER
    return sorted(f.stem for f in folder.glob("*.jsonl")) if folder.is_dir() else []


def read(file: Path) -> list[dict]:
    """A month's lines, in the order written, each key once (its first line). A line that can't be
    read (cut short by a stop) is skipped."""
    entries, keys = [], set()
    if not file.exists():
        return entries
    for line in file.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("kind") not in KINDS or entry.get("key") in keys:
            continue
        keys.add(entry.get("key"))
        entries.append(entry)
    return entries


def append(file: Path, entries: list[dict]) -> None:
    """Add lines to a log in one append, on disk (fsync) when this returns."""
    file.parent.mkdir(parents=True, exist_ok=True)
    attributes = file.parent / ".gitattributes"
    if not attributes.exists():
        write_atomic(attributes, GITATTRIBUTES)
    data = "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in entries).encode("utf-8")
    fd = os.open(file, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        # After a line cut short, a line of its own.
        if size and os.pread(fd, 1, size - 1) != b"\n":
            data = b"\n" + data
        while data:
            data = data[os.write(fd, data):]
        os.fsync(fd)
    finally:
        os.close(fd)


def totals(entries: list[dict]) -> dict:
    """A month's row of the ledger, from its lines (see the module docstring)."""
    paid = dict.fromkeys(COSTS, 0.0)
    # Each document's latest summary this month, and each document's translations, by language.
    latest: dict[str, float] = {}
    translated, drafts = set(), 0
    for entry in entries:
        kind, cost = entry["kind"], entry.get("cost", 0.0)
        if kind == "seed":
            for key, value in entry.get("totals", {}).items():
                if key in paid:
                    paid[key] += value
        elif kind == "summary":
            if entry["doc"] in latest:
                paid["replaced_cost"] += latest[entry["doc"]]
            latest[entry["doc"]] = cost
        elif kind == "failed":
            paid["failed_cost"] += cost
        elif kind == "transcript":
            paid["transcript_cost"] += cost
        else:
            paid["translation_cost"] += cost
            if kind == "translation":
                translated.add((entry.get("lang"), entry.get("doc")))
            elif kind == "draft":
                drafts += 1
    row = {"cost": round(sum(latest.values()), 4), "documents": len(latest)}
    if translated or drafts or paid["translation_cost"]:
        row.update(translation_cost=round(paid["translation_cost"], 4), translations=len(translated) + drafts)
    for key in ("failed_cost", "transcript_cost", "replaced_cost"):
        if paid[key]:
            row[key] = round(paid[key], 4)
    return row


def month_cost(row: dict) -> float:
    """Everything a month's row counts as paid for."""
    return sum(row.get(key, 0.0) for key in COSTS)


def load_ledger(data_dir: Path) -> dict:
    file = data_dir / LEDGER
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}


def save_ledger(data_dir: Path, rows: dict[str, dict]) -> dict:
    """The ledger with these months' rows replaced, saved."""
    ledger = {**load_ledger(data_dir), **rows}
    ledger = dict(sorted(ledger.items()))
    write_atomic(data_dir / LEDGER, json.dumps(ledger, indent=2) + "\n")
    return ledger


def rebuild(data_dir: Path) -> dict:
    """The ledger with every month that has a log made again from it; other months kept as they are."""
    rows = {month: totals(entries) for month in months(data_dir) if (entries := read(path(data_dir, month)))}
    return save_ledger(data_dir, rows)


def request_key(response) -> str:
    """The key of a request answered on its own: its response's id (one without, a key of its own)."""
    rid = getattr(response, "id", None)
    return rid if isinstance(rid, str) and rid else f"local-{uuid.uuid4().hex}"


def batch_key(batch_id: str, custom_id: str) -> str:
    return f"{batch_id}:{custom_id}"


def usage_line(usage: dict) -> dict:
    """A request's tokens as a line keeps them: the counts the API gave, without zeros."""
    return {k: v for k, v in usage.items() if k != "requests" and v}


class Log:
    """A town's log for one month, and the month's row of the ledger made from it.

    seed: for a town with no log yet, a function giving the lines the month starts with (the module
    docstring); called once, when the log is first opened."""

    def __init__(self, data_dir: Path, month: str, tz: ZoneInfo | None = None, seed=None):
        self.data_dir, self.month, self.tz = data_dir, month, tz
        self.file = path(data_dir, month)
        started = (data_dir / FOLDER).is_dir()
        self.entries = read(self.file)
        self.keys = {e["key"] for e in self.entries}
        if not started:
            lines = [e for e in (seed() if seed else []) if e["key"] not in self.keys]
            # The folder, with its .gitattributes, says the town has a log from now on, though no line is in it yet.
            (data_dir / FOLDER).mkdir(parents=True, exist_ok=True)
            write_atomic(data_dir / FOLDER / ".gitattributes", GITATTRIBUTES)
            if lines:
                append(self.file, lines)
                self.entries += lines
                self.keys |= {e["key"] for e in lines}
        self.row = totals(self.entries)
        ledger = load_ledger(data_dir)
        # Months with a log whose row is missing (the ledger lost, or made before the log was), and this one.
        rows = {m: totals(read(path(data_dir, m))) for m in months(data_dir) if m not in ledger and m != month}
        if self.entries and ledger.get(month) != self.row:
            rows[month] = self.row
        if rows:
            save_ledger(data_dir, rows)

    def add(self, kind: str, key: str, cost: float, **fields) -> bool:
        """Log one request paid for, and save the month's row; False (and nothing written) when the
        month's log already has its key. fields: doc, lang, model, usage, and anything else to keep."""
        if key in self.keys:
            return False
        entry = {"at": datetime.now(self.tz).isoformat(timespec="seconds"), "kind": kind, "key": key,
                 **{k: v for k, v in fields.items() if v is not None}, "cost": round(cost, 6)}
        if "usage" in entry:
            entry["usage"] = usage_line(entry["usage"])
        append(self.file, [entry])
        self.entries.append(entry)
        self.keys.add(key)
        self.row = totals(self.entries)
        save_ledger(self.data_dir, {self.month: self.row})
        return True

    def request(self, response, kind: str, cost: float, **fields) -> bool:
        """Log a request answered on its own, by its response's id."""
        return self.add(kind, request_key(response), cost, **fields)

    def month_cost(self) -> float:
        return month_cost(self.row)
