"""What a town's requests to the model cost: a line for each one, and each month's totals made from them.

Every request the model answers is paid for, whether what it made is saved or not. Each one is
written, as soon as it's answered, to the run's log for the month (the month in the town's time
zone, as the run's), one JSON object a line:

    {"at": "2026-10-15T09:12:03-04:00", "kind": "summary", "key": "msg_01...", "doc": "<SHA-256>",
     "model": "...", "usage": {"input_tokens": 41200, "output_tokens": 1650}, "cost": <dollars>}

kind is what the request was for: summary (a summary saved), transcript (some pages of a scan
transcribed), translation (a summary translated), draft (a batch of the town's own text drafted in
another language), review (a translation's or a draft's meaning reviewed), failed (one that stopped
early, or whose result couldn't be read or saved), or seed (the month's costs from before its log,
below). A translation's, draft's, or review's line also has its language, "lang". key names the
request: its response's id, or for one request of a batch, the batch's id and the request's
custom_id. A key the month's lines already have isn't written again, so each request counts once
however often it's recorded: a batch collected again after a run was stopped partway, or a write
tried again.

Where the costs are kept
------------------------

Not in git: the town's data folder is public, and so is everything committed to it. With the
private store's keys (PRIVATE_KEYS: an S3-compatible bucket with no public address, Cloudflare R2
for the Publick network), a town's costs are kept in that bucket under costs/<town>/, where <town>
is its [storage] prefix, or else its config's name (town_prefix):

    costs/<town>/<YYYY-MM>/<run>.jsonl   one run's lines for the month, one object a run
    costs/<town>/<YYYY-MM>/seed.jsonl    the month's lines from before the town had a log (below)
    costs/<town>/summary-costs.json      each month's totals, made from the lines (the ledger)
    costs/<town>/started                 the town's log has begun (it has been seeded)

A bucket has no append, so each run writes an object of its own, named for the run (a time and a
random part), and a month's lines are all of its objects' lines, read in the order of their names
(seed first), each key once. Each line is first appended to a local copy of the run's object,
data/costs/pending/<YYYY-MM>/<run>.jsonl, flushed to disk (fsync), then the whole copy is uploaded
in place of the run's object, and the ledger with it. So a run stopped at any moment has counted
every request but the one in flight, even if its machine is lost. A copy whose upload failed, or
that a stopped run left behind, is uploaded by the next run that opens the town's log on the same
machine (the next step of the same job), and deleted once uploaded.

Without the keys (the tests, a town run on its own, a run without the private store), the same
layout is kept in the town's own folder: each run's lines in data/costs/<YYYY-MM>/<run>.jsonl,
written in place, and the ledger in data/summary-costs.json, where the engine has always kept it.
data/costs/ has a .gitignore that leaves all of it out of git, so its lines are never committed
whatever the repository's own .gitignore says.

A line cut short by a stop is skipped when the lines are read, and the next one starts on a line of
its own.

The ledger
----------

The ledger's month row (summary-costs.json), which the network reads for its monthly budget, is
made from the month's lines after each line, in the form it has always had: cost and documents for
each document's latest summary this month, replaced_cost for the earlier summaries of a document
made again this month, failed_cost, transcript_cost, and translation_cost and translations
(translations, their reviews, and drafts; each document's translation into a language counts once,
and a batch of drafts as one). Making it reads the one month's lines, never the summaries. A month
with no lines keeps its row as it is, as do the months before the town had a log.

A town from before the log starts one with the current month, once: a seed object with a line for
each summary and translation saved this month, and each batch of drafts, at the cost its file
records, and one seed line with the rest of the month's row (failed, transcript, and replaced costs,
which no file records). The month's total goes on from what it was. The row comes from the ledger
the town had: the private store's, which the network's budget step imports from the town's
data/summary-costs.json before any town runs (import_ledger), or else that file itself. With the
private store, the local data/summary-costs.json is deleted once the store has the town's log, so
the run's data commit takes it out of git.

pipeline.summarize's `ledger --check` compares the lines with the costs the summary and translation
files record, and lists every difference without changing either.

Nothing here prints what anything cost: the network's run logs are public.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.files import write_atomic

FOLDER = "costs"
# What each month cost, made from the lines; the network reads it (pipeline/network.py's town_ledger).
LEDGER = "summary-costs.json"
# What a month's row of the ledger counts as paid for.
COSTS = ("cost", "failed_cost", "transcript_cost", "translation_cost", "replaced_cost")
# The kinds of line that count as translation_cost.
TRANSLATING = ("translation", "draft", "review")
KINDS = ("summary", "transcript", "failed", "seed") + TRANSLATING
# In data/costs/: nothing in the folder is committed, this file included.
GITIGNORE = "*\n"
# The run copies waiting to be uploaded to the private store, under data/costs/.
PENDING = "pending"
SEED = "seed"
STARTED = "started"
# The private store: an S3-compatible endpoint (https://<account id>.r2.cloudflarestorage.com), its
# bucket, and a key that can read and write that bucket. Every step that reads or writes costs gets
# them; no other step does (pipeline/network.py's keyed, pipeline/update.py's step_env).
PRIVATE_KEYS = ("PRIVATE_ENDPOINT", "PRIVATE_BUCKET", "PRIVATE_ACCESS_KEY_ID", "PRIVATE_SECRET_ACCESS_KEY")
# The network's monthly budget for summaries and translations, and a month with its own
# (YYYY-MM=amount, several separated by commas). Secrets, so the run logs never show them.
BUDGET_KEYS = ("SUMMARY_BUDGET", "SUMMARY_BUDGET_MONTH")


class StoreError(RuntimeError):
    """The private store couldn't be read or written."""


def private_configured(env=None) -> bool:
    env = os.environ if env is None else env
    return all(env.get(k) for k in PRIVATE_KEYS)


def private_client():
    import boto3

    return boto3.client("s3", endpoint_url=os.environ["PRIVATE_ENDPOINT"], region_name="auto",
                        aws_access_key_id=os.environ["PRIVATE_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["PRIVATE_SECRET_ACCESS_KEY"])


def town_prefix(config: dict, slug: str | None = None) -> str:
    """The town's name in the private store: its [storage] prefix (<town>-<state> in a network), or its
    config's name."""
    prefix = (config.get("storage") or {}).get("prefix") or slug or config["slug"]
    return prefix.strip("/")


def monthly_budget(month: str, env=None) -> float | None:
    """The network's budget for the month: SUMMARY_BUDGET_MONTH's, if it names the month, else
    SUMMARY_BUDGET's; None if neither is set."""
    env = os.environ if env is None else env
    for part in env.get("SUMMARY_BUDGET_MONTH", "").split(","):
        named, _, amount = part.strip().partition("=")
        if named.strip() == month and amount.strip():
            return float(amount)
    value = env.get("SUMMARY_BUDGET", "").strip()
    return float(value) if value else None


def parse(text: str) -> list[dict]:
    """The lines of one log, in order, each a request's. A line that can't be read (cut short by a stop)
    is skipped."""
    entries = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("kind") in KINDS:
            entries.append(entry)
    return entries


def unique(entries: list[dict]) -> list[dict]:
    """Each key once (its first line)."""
    out, keys = [], set()
    for entry in entries:
        if entry.get("key") in keys:
            continue
        keys.add(entry.get("key"))
        out.append(entry)
    return out


def ordered(names: list[str]) -> list[str]:
    """A month's objects in the order their lines count: the seed first, then the runs by name (by time)."""
    return sorted(names, key=lambda name: (name != SEED, name))


def append(file: Path, entries: list[dict]) -> None:
    """Add lines to a log in one append, on disk (fsync) when this returns."""
    file.parent.mkdir(parents=True, exist_ok=True)
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


def read_json(file: Path) -> dict | None:
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None


def ignored(data_dir: Path) -> Path:
    """data/costs/, with the .gitignore that keeps it out of git."""
    folder = data_dir / FOLDER
    if not (folder / ".gitignore").exists():
        write_atomic(folder / ".gitignore", GITIGNORE)
    return folder


class LocalStore:
    """The town's costs in its own folder, without the private store: data/costs/<YYYY-MM>/<name>.jsonl
    (out of git) and data/summary-costs.json."""

    private = False

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.folder = data_dir / FOLDER

    def copy(self, month: str, name: str) -> Path:
        """Where a run's lines are appended: the run's object itself."""
        return self.folder / month / f"{name}.jsonl"

    def upload(self, month: str, name: str, copy: Path) -> None:
        pass

    def put(self, month: str, name: str, text: str) -> None:
        ignored(self.data_dir)
        write_atomic(self.copy(month, name), text)

    def logs(self, month: str) -> list[str]:
        folder = self.folder / month
        names = [f.stem for f in folder.glob("*.jsonl")] if folder.is_dir() else []
        return [(folder / f"{name}.jsonl").read_text(encoding="utf-8") for name in ordered(names)]

    def months(self) -> list[str]:
        return sorted(d.name for d in self.folder.iterdir() if d.is_dir() and d.name != PENDING
                      and any(d.glob("*.jsonl"))) if self.folder.is_dir() else []

    def ledger(self) -> dict | None:
        return read_json(self.data_dir / LEDGER)

    def save_ledger(self, ledger: dict) -> None:
        write_atomic(self.data_dir / LEDGER, json.dumps(ledger, indent=2) + "\n")

    def started(self) -> bool:
        return (self.folder / STARTED).exists()

    def start(self) -> None:
        write_atomic(ignored(self.data_dir) / STARTED, "")

    def leftovers(self) -> list[tuple[str, str, Path]]:
        return []


class BucketStore:
    """The town's costs in the private store, under costs/<prefix>/ (the module docstring). client is
    boto3's S3 client, or a stand-in with the same calls."""

    private = True

    def __init__(self, client, bucket: str, prefix: str, data_dir: Path):
        self.client, self.bucket, self.data_dir = client, bucket, data_dir
        self.root = f"{FOLDER}/{prefix}"

    def key(self, name: str) -> str:
        return f"{self.root}/{name}"

    def _get(self, name: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self.key(name))["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None
        except Exception as e:  # botocore errors
            raise StoreError(f"the private store couldn't be read ({type(e).__name__})") from e

    def _put(self, name: str, body: bytes, content_type: str) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=self.key(name), Body=body, ContentType=content_type)
        except Exception as e:  # botocore errors
            raise StoreError(f"the private store couldn't be written ({type(e).__name__})") from e

    def _list(self, prefix: str, delimiter: str | None = None) -> tuple[list[str], list[str]]:
        keys, folders, token = [], [], None
        try:
            while True:
                page = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix,
                                                   **({"Delimiter": delimiter} if delimiter else {}),
                                                   **({"ContinuationToken": token} if token else {}))
                keys += [o["Key"] for o in page.get("Contents", [])]
                folders += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
                if not page.get("IsTruncated"):
                    return keys, folders
                token = page["NextContinuationToken"]
        except Exception as e:  # botocore errors
            raise StoreError(f"the private store couldn't be listed ({type(e).__name__})") from e

    def copy(self, month: str, name: str) -> Path:
        """The local copy of a run's object, appended to and then uploaded."""
        return ignored(self.data_dir) / PENDING / month / f"{name}.jsonl"

    def upload(self, month: str, name: str, copy: Path) -> None:
        self._put(f"{month}/{name}.jsonl", copy.read_bytes(), "application/x-ndjson")

    def put(self, month: str, name: str, text: str) -> None:
        self._put(f"{month}/{name}.jsonl", text.encode("utf-8"), "application/x-ndjson")

    def logs(self, month: str) -> list[str]:
        keys, _ = self._list(self.key(f"{month}/"))
        names = {k.rsplit("/", 1)[1].removesuffix(".jsonl"): k for k in keys if k.endswith(".jsonl")}
        out = []
        for name in ordered(list(names)):
            body = self._get(f"{month}/{name}.jsonl")
            if body is not None:
                out.append(body.decode("utf-8", errors="replace"))
        return out

    def months(self) -> list[str]:
        _, folders = self._list(self.key(""), "/")
        return sorted(name for f in folders if is_month(name := f.rstrip("/").rsplit("/", 1)[1]))

    def ledger(self) -> dict | None:
        body = self._get(LEDGER)
        return None if body is None else json.loads(body)

    def save_ledger(self, ledger: dict) -> None:
        self._put(LEDGER, (json.dumps(ledger, indent=2) + "\n").encode("utf-8"), "application/json")

    def started(self) -> bool:
        return self._get(STARTED) is not None

    def start(self) -> None:
        self._put(STARTED, b"", "text/plain")

    def leftovers(self) -> list[tuple[str, str, Path]]:
        """Run copies a stopped run, or a failed upload, left behind: (month, run, copy)."""
        pending = self.data_dir / FOLDER / PENDING
        return sorted((f.parent.name, f.stem, f) for f in pending.glob("*/*.jsonl")) if pending.is_dir() else []

    def import_ledger(self) -> dict:
        """The store's ledger; for a town it has none for yet, the one in the town's data/summary-costs.json
        (from before the private store), saved to the store first. Called by the network's budget step, so
        the month's total is there before any town of the run spends from it."""
        ledger = self.ledger()
        if ledger is None:
            ledger = read_json(self.data_dir / LEDGER) or {}
            if ledger:
                self.save_ledger(ledger)
        return ledger


def is_month(name: str) -> bool:
    return len(name) == 7 and name[4] == "-" and name[:4].isdigit() and name[5:].isdigit()


def open_store(config: dict, data_dir: Path, client=None):
    """The private store for this town when its keys are set, else the town's own folder."""
    if client is None and not private_configured():
        return LocalStore(data_dir)
    return BucketStore(client or private_client(), os.environ.get("PRIVATE_BUCKET", ""), town_prefix(config),
                       data_dir)


def read_month(store, month: str) -> list[dict]:
    """A month's lines, from every run's object, the seed first, each key once."""
    return unique([e for text in store.logs(month) for e in parse(text)])


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


def load_ledger(store) -> dict:
    return store.ledger() or {}


def save_ledger(store, rows: dict[str, dict], ledger: dict | None = None) -> dict:
    """The ledger with these months' rows replaced, saved."""
    ledger = {**(load_ledger(store) if ledger is None else ledger), **rows}
    ledger = dict(sorted(ledger.items()))
    store.save_ledger(ledger)
    return ledger


def rebuild(store) -> dict:
    """The ledger with every month that has lines made again from them; other months kept as they are."""
    rows = {month: totals(entries) for month in store.months() if (entries := read_month(store, month))}
    return save_ledger(store, rows)


def request_key(response) -> str:
    """The key of a request answered on its own: its response's id (one without, a key of its own)."""
    rid = getattr(response, "id", None)
    return rid if isinstance(rid, str) and rid else f"local-{uuid.uuid4().hex}"


def batch_key(batch_id: str, custom_id: str) -> str:
    return f"{batch_id}:{custom_id}"


def usage_line(usage: dict) -> dict:
    """A request's tokens as a line keeps them: the counts the API gave, without zeros."""
    return {k: v for k, v in usage.items() if k != "requests" and v}


def run_name(now: datetime | None = None) -> str:
    """A run's object name: when it started (UTC), so a month's objects sort in the order of their runs,
    and a random part, so two runs never share one."""
    return f"{(now or datetime.now(timezone.utc)).astimezone(timezone.utc):%Y%m%dT%H%M%S.%fZ}-{uuid.uuid4().hex[:8]}"


class Log:
    """A town's log for one month, and the month's row of the ledger made from it.

    store: LocalStore or BucketStore (open_store). seed: for a town with no log yet, a function of the
    month's row from the ledger it had, giving the lines the month starts with (the module docstring);
    called once, when the log is first opened. warn: where a failed upload is reported (the run goes on,
    and the copy is uploaded later)."""

    def __init__(self, store, month: str, tz: ZoneInfo | None = None, seed=None, warn=print):
        self.store, self.month, self.tz, self.warn = store, month, tz, warn
        self.uploaded = True
        # A stopped run's copies, uploaded before the month is read, so their lines count.
        for m, name, copy in store.leftovers():
            store.upload(m, name, copy)
            copy.unlink()
        self.name = run_name()
        self.copy = store.copy(month, self.name)
        self.entries = read_month(store, month)
        started = store.started()
        ledger = load_ledger(store)
        if not started and store.private and not ledger:
            # Not imported by the network's budget step (a run on its own): the ledger from before.
            ledger = read_json(store.data_dir / LEDGER) or {}
        if not started:
            seeded = any(e["kind"] == "seed" or str(e.get("key", "")).startswith("seed") for e in self.entries)
            if not seeded:
                keys = {e["key"] for e in self.entries}
                lines = [e for e in (seed(ledger.get(month, {})) if seed else []) if e["key"] not in keys]
                if lines:
                    store.put(month, SEED, "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n"
                                                   for e in lines))
                    self.entries = read_month(store, month)
        self.keys = {e["key"] for e in self.entries}
        self.row = totals(self.entries)
        # Months with lines whose row is missing (the ledger lost, or made before the lines were), and this one.
        rows = {m: totals(read_month(store, m)) for m in store.months() if m not in ledger and m != month}
        if self.entries and ledger.get(month) != self.row:
            rows[month] = self.row
        self.ledger = ledger
        if rows or (store.private and not started and ledger):
            self.ledger = save_ledger(store, rows, ledger)
        if not started:
            store.start()
        if store.private and (self.ledger or self.entries):
            # The store has the town's ledger: the copy from before, in git, goes with the run's data commit.
            (store.data_dir / LEDGER).unlink(missing_ok=True)

    def add(self, kind: str, key: str, cost: float, **fields) -> bool:
        """Log one request paid for, and save the month's row; False (and nothing written) when the
        month's lines already have its key. fields: doc, lang, model, usage, and anything else to keep."""
        if key in self.keys:
            return False
        entry = {"at": datetime.now(self.tz).isoformat(timespec="seconds"), "kind": kind, "key": key,
                 **{k: v for k, v in fields.items() if v is not None}, "cost": round(cost, 6)}
        if "usage" in entry:
            entry["usage"] = usage_line(entry["usage"])
        append(self.copy, [entry])
        self.entries.append(entry)
        self.keys.add(key)
        self.row = totals(self.entries)
        self.save()
        return True

    def save(self) -> bool:
        """The run's lines and the month's row, in the store. A failure is reported (without amounts) and
        tried again on the next line, at close, or by the next run."""
        try:
            self.store.upload(self.month, self.name, self.copy)
            # Only this run writes the town's ledger while it runs (the network runs a town's steps one at a time).
            self.ledger = save_ledger(self.store, {self.month: self.row}, self.ledger)
        except StoreError as e:
            if self.uploaded:
                self.warn(f"::warning::cost log: {e}; kept on this machine and uploaded later")
            self.uploaded = False
            return False
        self.uploaded = True
        return True

    def close(self) -> None:
        """At the end of a run: its copy uploaded (again, if the last try failed) and, once in the private
        store, deleted."""
        if not self.store.private or not self.copy.exists():
            return
        if self.save():
            self.copy.unlink()

    def request(self, response, kind: str, cost: float, **fields) -> bool:
        """Log a request answered on its own, by its response's id."""
        return self.add(kind, request_key(response), cost, **fields)

    def month_cost(self) -> float:
        return month_cost(self.row)
