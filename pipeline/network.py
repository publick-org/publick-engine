"""Run many towns from one repository: the network's daily update, build, and deploy.

The network repository holds each town in its own folder, laid out as a town
repository is (config/, data/, site/static/):

  towns/gloucester-ma/config/gloucester.toml
  towns/gloucester-ma/data/...

plan picks the towns for one run and splits them into batches, printed as a
GitHub Actions matrix. The batches are balanced by how long each town's last
fetching run took, so the slow towns (a long 311 history) don't all land in
one job. run takes one batch and, for each town in turn, fetches
new data (pipeline.update), checks its freshness, builds the site, checks it
(site_checks/; with --sample-checks, the browser checks run on a sample of
pages, as the daily runs do), and publishes it (pipeline.deploy). Every step runs in its own
process, so one town's failure never stops the next. A run that fetches also
writes its result to the town's data/run.json, committed with the data, which
the network's status page reads; one that publishes without fetching (a push,
a rebuild) records that it did in the same file. report writes one table of every town in the
run. A run that fetches doesn't fail when a town does: behind lists the towns
without a good update (published, with fresh data) in the last day or so, and
those whose figure checks keep failing, for the one daily alert. A run that only builds, as for a pull request, fails if
any town does.

canaries picks the fewest towns that between them have every state, every
config table, and every kind of meeting source the network's towns have: the
towns whose every page an engine update's pull request checks, while the rest
are checked on a sample (publick.org's network.yml), so the run's time grows
with the kinds of town, not their number.

budget splits what's left of the month's summary budget among the towns in a
run, from each town's data/summary-costs.json (pipeline.summarize), keeping
back every other town's floor for the rest of the month.

states fetches the statewide sources the network's towns use, once per state
for all of them, into states/ (for Massachusetts, the DLS exports: see
pipeline/states/ma/dls.py); each town's steps read their rows from there. A
state's statewide checks that keep failing are listed by behind.

    python -m pipeline.network plan   [--root .] [--due-hours 18 | --slots 4 --slot N] [--towns a,b] [--changed FILE]
                                      [--batch-size 4]
    python -m pipeline.network run    [--root .] --towns a,b [--fetch [--sources all|meetings|figures|311]] [--deploy]
                                      [--sample-checks] [--reports DIR]
    python -m pipeline.network report DIR
    python -m pipeline.network behind [--root .] [--hours 30]
    python -m pipeline.network budget [--root .] --monthly 50 --towns-in-run N
    python -m pipeline.network canaries [--root .]
    python -m pipeline.network states [--root .]

A daily run takes the towns that are due (--due-hours): those whose last run
that fetched their data finished more than that many hours ago, or never ran,
oldest first. So a run that starts late, twice, or not at all does no harm: a
second start finds nothing due, and the next run makes up a missed one. (Towns
can also be spread over --slots runs a day by a stable hash of their folder
name.)
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.config import ENGINE_DIR
from pipeline.files import write_atomic

TOWNS = "towns"
# Statewide sources, fetched once per state for every town (states/<state>/), and the environment
# variable that tells a town's steps where they are (pipeline.states.ma.dls.STORE_ENV).
STATES = "states"
STATE_DIR_ENV = "PUBLICK_STATE_DIR"
# The states with statewide sources, and the module that fetches them (refresh(state_dir, configs, client)).
STATEWIDE = {"MA": "pipeline.states.ma.dls"}
# A state's statewide checks count as failing, in the daily alert, after this many in a row.
STATE_FAILURES = 3# A change to the engine version or the workflows rebuilds every town.
SHARED_FILES = ("engine-version",)
SHARED_FOLDERS = (".github/",)
# Seconds before one fetch step is stopped. fetch_311 spends up to 40 minutes
# on lookups by default (time_budget_seconds), so this leaves it room.
STEP_TIMEOUT = 3000
# Seconds for a town's build, checks, or publish.
BUILD_TIMEOUT = 1800
# The run's summary page: report writes one table for every town, so town steps don't write to it.
SUMMARY_ENV = "GITHUB_STEP_SUMMARY"
# A fetching run's result, in the town's data/, for the status page.
RUN_RECORD = "run.json"
# Agendas waiting for a summary kept in that record; the rest are counted.
WAITING_KEPT = 10
# Hours without a good update before a town is listed as behind.
BEHIND_HOURS = 30
# Hours after a town's last fetching run before a daily run takes it again. Under a day, so each
# morning's runs take every town once, even one that finished late the day before.
DUE_HOURS = 18
# A town's expected time in a batch when it has no run record to go by, in seconds.
UNKNOWN_RUN_SECONDS = 30 * 60
# The part of the monthly summary budget older documents can't use, kept for new ones.
NEW_DOCUMENTS_RESERVE = 0.2
# Each town's floor: the part of its even share of each day's budget (the monthly budget over the
# month's days and the network's towns) that's kept for it for every day left in the month, so no
# other town's spending, a launch's history or a big week of agendas, can take it.
TOWN_FLOOR_SHARE = 0.5
# Each town's summary costs by month, as pipeline.summarize keeps them. Not imported from
# there: the network's plan and report jobs run without the engine's packages installed.
SUMMARY_LEDGER = "summary-costs.json"
# Towns file each summary's cost under the month in their own time zone, so the budget's month is
# the network's (every town so far is Eastern), not UTC's, which starts a month 4 or 5 hours early.
NETWORK_TZ = ZoneInfo("America/New_York")


def town_dirs(root: Path) -> list[str]:
    """Every town folder under towns/ that has exactly one config file."""
    base = root / TOWNS
    return sorted(d.name for d in base.iterdir() if d.is_dir() and len(list((d / "config").glob("*.toml"))) == 1) \
        if base.is_dir() else []


def slug(root: Path, name: str) -> str:
    """The town's config name (towns/gloucester-ma/config/gloucester.toml -> gloucester)."""
    return next((root / TOWNS / name / "config").glob("*.toml")).stem


def slot(name: str, slots: int) -> int:
    """The run of the day a town belongs to; stable across runs and machines."""
    return int(hashlib.sha256(name.encode()).hexdigest(), 16) % slots


def changed_towns(files: list[str], towns: list[str]) -> list[str]:
    """Towns whose folders a change touched (all of them if shared files changed). Data commits aren't changes to
    rebuild for: they come from the daily run, which has already built and published the town."""
    if any(f in SHARED_FILES or f.startswith(SHARED_FOLDERS) for f in files):
        return towns
    touched = set()
    for f in files:
        parts = f.split("/")
        if len(parts) >= 3 and parts[0] == TOWNS and parts[1] in towns and parts[2] != "data":
            touched.add(parts[1])
    return sorted(touched)


def last_fetched(root: Path, name: str) -> str | None:
    """When the town's last run that fetched its data finished (its run record), or None if none has."""
    path = root / TOWNS / name / "data" / RUN_RECORD
    return json.loads(path.read_text()).get("finished_at") if path.exists() else None


def due(root: Path, towns: list[str], hours: float, at: datetime | None = None) -> list[str]:
    """The towns whose last fetching run finished more than `hours` ago, or never ran, oldest first."""
    at = at or datetime.now(timezone.utc)
    last = {t: last_fetched(root, t) for t in towns}
    waiting = [t for t in towns if not last[t] or datetime.fromisoformat(last[t]) <= at - timedelta(hours=hours)]
    return sorted(waiting, key=lambda t: (last[t] is not None, last[t] or ""))


def last_run_seconds(root: Path, name: str) -> float:
    """How long the town's last fetching run took (its run record), or UNKNOWN_RUN_SECONDS."""
    path = root / TOWNS / name / "data" / RUN_RECORD
    try:
        record = json.loads(path.read_text())
        took = datetime.fromisoformat(record["finished_at"]) - datetime.fromisoformat(record["started_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return UNKNOWN_RUN_SECONDS
    return max(took.total_seconds(), 0.0)


def balance(towns: list[str], batch_size: int, seconds: dict[str, float]) -> list[list[str]]:
    """Split towns into as few batches of at most batch_size as they need, the longest town first into
    the batch with the least time so far. One job with every slow town took over two hours on
    2026-10-07, and its runner was lost with all four towns' work. Each batch keeps the towns' order,
    and the batches are in the order of their first town."""
    count = math.ceil(len(towns) / batch_size)
    batches: list[list[str]] = [[] for _ in range(count)]
    totals = [0.0] * count
    for town in sorted(towns, key=lambda t: -seconds[t]):
        i = min((i for i in range(count) if len(batches[i]) < batch_size), key=totals.__getitem__)
        batches[i].append(town)
        totals[i] += seconds[town]
    order = {t: i for i, t in enumerate(towns)}
    batches = [sorted(b, key=order.__getitem__) for b in batches]
    return sorted(batches, key=lambda b: order[b[0]])


def plan(root: Path, slots: int = 1, run_slot: int | None = None, only: list[str] | None = None,
         changed: list[str] | None = None, batch_size: int = 4, due_hours: float | None = None,
         at: datetime | None = None) -> dict:
    towns = town_dirs(root)
    unknown = sorted(set(only or []) - set(towns))
    if unknown:
        raise SystemExit(f"No town folder with one config for: {', '.join(unknown)}")
    if only:
        towns = [t for t in towns if t in only]
    if changed is not None:
        towns = changed_towns(changed, towns)
    if run_slot is not None:
        towns = [t for t in towns if slot(t, slots) == run_slot]
    if due_hours is not None:
        towns = due(root, towns, due_hours, at)
    batches = balance(towns, batch_size, {t: last_run_seconds(root, t) for t in towns})
    return {"include": [{"towns": " ".join(b), "name": b[0] + (f" +{len(b) - 1}" if len(b) > 1 else "")}
                        for b in batches]}


def town_env(root: Path, name: str) -> dict:
    # The check step sets PUBLICK_CHECK_PAGES itself, so a full run is never sampled by accident.
    env = {k: v for k, v in os.environ.items() if k not in (SUMMARY_ENV, "PUBLICK_CHECK_PAGES")}
    env.update(PUBLICK_TOWN_DIR=str(root / TOWNS / name), TOWN=slug(root, name),
               PYTHONPATH=os.pathsep.join(filter(None, [str(ENGINE_DIR), os.environ.get("PYTHONPATH")])))
    if (root / STATES).is_dir():
        env[STATE_DIR_ENV] = str(root / STATES)
    return env


# The keys a town's steps use: fetching (pipeline/update.py gives each of its steps only its own) and
# publishing. The freshness check, the build, and the checks get none.
FETCH_KEYS = ("ANTHROPIC_API_KEY", "BLS_API_KEY", "STORAGE_ACCESS_KEY_ID", "STORAGE_SECRET_ACCESS_KEY")
PUBLISH_KEYS = ("SITES_ENDPOINT", "SITES_BUCKET", "SITES_ACCESS_KEY_ID", "SITES_SECRET_ACCESS_KEY")


def keyed(env: dict, keys: tuple[str, ...] = ()) -> dict:
    """The environment without any step's keys but these."""
    return {k: v for k, v in env.items() if k not in FETCH_KEYS + PUBLISH_KEYS or k in keys}


def step(name: str, cmd: list[str], env: dict, cwd: Path, timeout: float | None) -> dict:
    print(f"::group::{env['TOWN']}: {name}", flush=True)
    started = time.monotonic()
    try:
        code = subprocess.run(cmd, env=env, cwd=cwd, timeout=timeout).returncode
        error = None if code == 0 else f"exited with status {code}"
    except subprocess.TimeoutExpired:
        error = f"stopped after {timeout:g} seconds"
    print("::endgroup::", flush=True)
    return {"name": name, "ok": error is None, "error": error, "seconds": round(time.monotonic() - started, 1)}


def restore(folder: Path) -> None:
    """Put a data folder back as it was committed, when the step that completes it failed."""
    subprocess.run(["git", "checkout", "HEAD", "--", "."], cwd=folder, check=False)
    subprocess.run(["git", "clean", "-fdq", "--", "."], cwd=folder, check=False)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def engine_version(root: Path) -> str | None:
    path = root / "engine-version"
    return (path.read_text().strip() or None) if path.exists() else None


def trim_sources(rows: list[dict]) -> list[dict]:
    """Freshness rows for the run record, with long lists of waiting summaries cut short."""
    return [{**r, "waiting": r["waiting"][:WAITING_KEPT], "waiting_count": len(r["waiting"])} if "waiting" in r else r
            for r in rows]


def run_town(root: Path, name: str, fetch: bool, deploy: bool, reports: Path | None,
             step_timeout: float = STEP_TIMEOUT, sample_checks: bool = False, sources: str = "all") -> dict:
    town_dir = root / TOWNS / name
    env = town_env(root, name)
    town = env["TOWN"]
    python = sys.executable
    site = town_dir / "_site"
    result = {"town": town, "folder": name, "started_at": now(), "finished_at": None,
              "engine": engine_version(root), "fetched": fetch, "steps": [], "update": None, "stale": False,
              "sources": None, "failing": [], "deployed": False}
    steps = result["steps"]
    data_before = folder_bytes(town_dir / "data")

    if fetch:
        update_report = town_dir / ".update-report.json"
        steps.append(step("Fetch new data", [python, "-m", "pipeline.update", "--town", town, "--sources", sources,
                                             "--step-timeout", str(step_timeout), "--report", str(update_report)],
                          keyed(env, FETCH_KEYS), town_dir, None))
        if update_report.exists():
            result["update"] = json.loads(update_report.read_text())
            update_report.unlink()
        # As in town.yml: without a scorecard, the 311 data isn't committed.
        if not steps[-1]["ok"] and (town_dir / "data" / "311").is_dir():
            restore(town_dir / "data" / "311")
        freshness_report = town_dir / ".freshness-report.json"
        freshness_report.unlink(missing_ok=True)
        freshness = step("Check data freshness", [python, "-m", "pipeline.freshness", "--town", town,
                                                  "--report", str(freshness_report)], keyed(env), town_dir, BUILD_TIMEOUT)
        result["stale"] = not freshness["ok"]
        if freshness_report.exists():
            result["sources"] = trim_sources(json.loads(freshness_report.read_text()))
            freshness_report.unlink()
            # Figure sources whose checks keep failing: not behind yet, but the maintainer should know.
            result["failing"] = [r["label"] for r in result["sources"] if r.get("failing")]

    steps.append(step("Build site", [python, "-m", "pipeline.build_site", "--town", town, "--out", str(site)],
                      keyed(env), town_dir, BUILD_TIMEOUT))
    if steps[-1]["ok"]:
        # The browser checks take most of a town's time, so they run on every core (pytest-xdist).
        steps.append(step("Check site", [python, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-n", "auto",
                                         str(ENGINE_DIR / "site_checks")],
                          {**keyed(env), "PUBLICK_SITE_DIR": str(site),
                           **({"PUBLICK_CHECK_PAGES": "sample"} if sample_checks else {})}, town_dir, BUILD_TIMEOUT))
    checked = any(s["name"] == "Check site" and s["ok"] for s in steps)
    if deploy and checked:
        steps.append(step("Publish site", [python, "-m", "pipeline.deploy", "publish", "--town", town,
                                           "--site", str(site)], keyed(env, PUBLISH_KEYS), town_dir, BUILD_TIMEOUT))
        if steps[-1]["ok"]:
            # What visitors get, through the Worker, is the build just published.
            steps.append(step("Check live site", [python, "-m", "pipeline.deploy", "check", "--town", town,
                                                  "--site", str(site)], keyed(env), town_dir, BUILD_TIMEOUT))
        result["deployed"] = all(s["ok"] for s in steps if s["name"] in ("Publish site", "Check live site"))
    result["ok"] = all(s["ok"] for s in steps)
    result["finished_at"] = now()
    if fetch and (town_dir / "data").is_dir():
        record = town_dir / "data" / RUN_RECORD
        previous = json.loads(record.read_text()) if record.exists() else {}
        result["last_good_at"] = result["finished_at"] if good(result) else last_good(previous)
        # What the town's data takes up, and how much this run added, for the status page.
        result["data_bytes"] = folder_bytes(town_dir / "data")
        result["data_bytes_added"] = result["data_bytes"] - data_before
        result["activity"] = activity(town_dir / "data")
        result["fact_checks"] = fact_checks(town_dir / "data")
        write_atomic(record, json.dumps(result, indent=2) + "\n")
    elif result["deployed"] and (town_dir / "data" / RUN_RECORD).is_file():
        republished(town_dir / "data" / RUN_RECORD, result)
    if reports:
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


# The steps that put a town's site up, which a later run that doesn't fetch can redo.
PUBLISH_STEPS = ("Build site", "Check site", "Publish site", "Check live site")


def republished(path: Path, result: dict) -> None:
    """Record a run that published the town without fetching (a push, a rebuild) in its run record:
    the last fetching run's, with this run's build, checks, and publish in place of that run's. So a
    site fixed and published after a daily run that couldn't publish it isn't shown as not
    published, on the status page and in the daily alert, until the next daily run. When the
    fetching run was due is unchanged (finished_at)."""
    record = json.loads(path.read_text(encoding="utf-8"))
    record["steps"] = ([s for s in record.get("steps", []) if s["name"] not in PUBLISH_STEPS]
                       + [s for s in result["steps"] if s["name"] in PUBLISH_STEPS])
    record["deployed"] = True
    record["ok"] = all(s["ok"] for s in record["steps"])
    record["published_at"] = result["finished_at"]
    record["published_engine"] = result["engine"]
    if good(record):
        record["last_good_at"] = result["finished_at"]
    write_atomic(path, json.dumps(record, indent=2) + "\n")


# Days of upcoming meetings counted for the network homepage.
ACTIVITY_DAYS = 14


def activity(data: Path, today: date | None = None) -> dict:
    """A few counts from the town's meetings for the network homepage, which reads only run
    records: the boards it follows, and how many meetings each coming day has."""
    path = data / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    # A meeting listed in more than one place counts once. Imported here: the network workflow's plan
    # and home jobs run this module with the standard library only, and listings needs the readers'.
    from pipeline import listings
    store, _ = listings.combined(store)
    today = today or datetime.now(timezone.utc).date()
    last = (today + timedelta(days=ACTIVITY_DAYS - 1)).isoformat()
    upcoming: dict[str, int] = {}
    for m in store.values():
        if today.isoformat() <= m["date"] <= last and m.get("status", "scheduled") == "scheduled" and m.get("listed", True):
            upcoming[m["date"]] = upcoming.get(m["date"], 0) + 1
    return {"boards": len({m["body"] for m in store.values()}), "meetings_by_date": dict(sorted(upcoming.items()))}


def fact_checks(data: Path) -> dict:
    """How the town's summaries fared against their documents (pipeline/factcheck.py), for the
    maintainer: how many summaries each result has ("not_yet": no check saved), how many of them
    the site holds something back from, how many decisions or agenda items it leaves out, and how
    many vote counts the documents don't give it takes out of the text."""
    # Imported here, as listings is in activity(): factcheck needs the readers' packages.
    from pipeline import factcheck
    counts = {"summaries": 0, "ok": 0, "failed": 0, "weak": 0, "unchecked": 0, "not_yet": 0,
              "held_back": 0, "entries_not_shown": 0, "vote_counts_left_out": 0}
    for path in sorted((data / "summaries").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        # A document filed as minutes that isn't any isn't shown, or checked.
        if record.get("is_minutes") is False:
            continue
        counts["summaries"] += 1
        fc = record.get("fact_check")
        if not fc:
            counts["not_yet"] += 1
            continue
        counts[fc["result"]] = counts.get(fc["result"], 0) + 1
        shown = factcheck.shown(record, record, factcheck.kind_of(record))
        counts["entries_not_shown"] += shown.get("not_shown", 0)
        counts["vote_counts_left_out"] += sum(p["kind"] == "tally" for p in fc["problems"])
        # Only a check against the document's full text fails, and what fails isn't shown.
        counts["held_back"] += fc["result"] == "failed"
    return counts


def folder_bytes(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.is_dir() else 0


def good(record: dict) -> bool:
    """A good update: the site was published, and the data isn't behind."""
    return bool(record.get("deployed")) and not record.get("stale")


def last_good(record: dict) -> str | None:
    """When a town last had a good update, from its run record (older records don't say, but may be one)."""
    if record.get("last_good_at"):
        return record["last_good_at"]
    return record.get("finished_at") if record.get("fetched", True) and good(record) else None


def behind(root: Path, hours: float = BEHIND_HOURS, at: datetime | None = None) -> list[dict]:
    """Towns without a good update in the last `hours`, or whose figure checks keep failing (not behind
    yet, but the maintainer should know), with what their last run says went wrong."""
    at = at or datetime.now(timezone.utc)
    rows = []
    for name in town_dirs(root):
        path = root / TOWNS / name / "data" / RUN_RECORD
        record = json.loads(path.read_text()) if path.exists() else {}
        last = last_good(record)
        if last and datetime.fromisoformat(last) >= at - timedelta(hours=hours) and not record.get("failing"):
            continue
        problems = [s["name"] for s in record.get("steps", []) if not s["ok"]]
        problems += [f"{r['label']} behind" for r in record.get("sources") or [] if r.get("stale")]
        problems += [f"{label}: checks failing" for label in record.get("failing") or []]
        rows.append({"folder": name, "last_good_at": last, "last_run_at": record.get("finished_at"),
                     "problems": problems})
    return rows + failing_states(root)


def town_config(root: Path, name: str) -> dict:
    import tomllib
    path = next((root / TOWNS / name / "config").glob("*.toml"))
    return {**tomllib.loads(path.read_text(encoding="utf-8")), "slug": path.stem}


def refresh_states(root: Path, now: datetime | None = None) -> dict:
    """Fetch the statewide sources for every state with towns in the network, into states/<state>/, and
    record how it went in states/<state>/status.json. A failure is recorded, not raised: the towns keep
    what was saved, and behind lists a state whose checks keep failing."""
    from importlib import import_module

    from pipeline.http import PoliteClient

    now = now or datetime.now(timezone.utc)
    configs = [town_config(root, name) for name in town_dirs(root)]
    results = {}
    for code, module_name in STATEWIDE.items():
        towns = [c for c in configs if c.get("town", {}).get("state_abbr", "").upper() == code and "finance" in c]
        if not towns:
            continue
        module = import_module(module_name)
        status_path = root / STATES / code.lower() / "status.json"
        previous = json.loads(status_path.read_text()) if status_path.exists() else {}
        status = {"state": code, "checked_at": now.isoformat(timespec="seconds")}
        try:
            result = module.refresh(root / STATES, towns, PoliteClient(module.USER_AGENT, delay=2.0), now)
            status.update(ok=True, failures=0, error=None, last_ok_at=status["checked_at"], **result)
        except Exception as e:  # recorded for the daily alert; the towns keep what was saved
            status.update(ok=False, failures=previous.get("failures", 0) + 1, error=str(e)[:300],
                          last_ok_at=previous.get("last_ok_at"))
            print(f"::warning::{code} statewide sources: {e}")
        results[code] = status
        # Nothing fetched and nothing wrong, as before: the file stays as it is, so the run commits nothing.
        if status["ok"] and status.get("fetched") == 0 and previous.get("ok") and previous.get("exports") == status.get("exports"):
            continue
        write_atomic(status_path, json.dumps(status, indent=2) + "\n")
    return results


def failing_states(root: Path) -> list[dict]:
    """Rows for the daily alert: states whose statewide checks have failed STATE_FAILURES times in a row."""
    rows = []
    for path in sorted((root / STATES).glob("*/status.json")):
        status = json.loads(path.read_text())
        if status.get("failures", 0) >= STATE_FAILURES:
            rows.append({"folder": f"{status['state']} statewide sources", "last_good_at": status.get("last_ok_at"),
                         "last_run_at": status.get("checked_at"),
                         "problems": [f"checks failing ({status['failures']} in a row): {status.get('error')}"]})
    return rows


def behind_text(rows: list[dict], hours: float) -> str:
    """The daily alert's text: one line per town behind, or nothing."""
    if not rows:
        return ""
    lines = [f"{len(rows)} of the network's towns need attention: no good update (published, with fresh data) "
             f"in the last {hours:g} hours, or a figure source whose checks keep failing. This issue is updated "
             f"after each daily run and closed when every town is caught up.", "", "| Town | Last good update | Last run | What went wrong |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['folder']} | {r['last_good_at'] or 'never'} | {r['last_run_at'] or 'never'} | "
                     f"{', '.join(r['problems']) or 'no run since'} |")
    return "\n".join(lines) + "\n"


def newly_behind_text(rows: list[dict], previous: str) -> str:
    """A comment for the daily alert naming the towns that aren't in its table yet (previous is the
    issue's text before this run): an edit to the issue sends no email, a comment does. Nothing
    when there are none, so a town already listed, or one that's caught up, sends nothing."""
    listed = {line.split("|")[1].strip() for line in previous.splitlines() if line.startswith("| ") and line.count("|") > 2}
    new = [r for r in rows if r["folder"] not in listed]
    if not new:
        return ""
    lines = ["Newly needing attention:", ""]
    lines += [f"- **{r['folder']}**: {', '.join(r['problems']) or 'no run since'}" for r in new]
    return "\n".join(lines) + "\n"


def summary_budget(root: Path, monthly: float, towns_in_run: int, today: date | None = None) -> dict:
    """Each town's share, for one run, of what's left of the month's summary budget.

    allowance is what's left beyond the other towns' floors, split among the run's
    towns, which run side by side. Every town has a floor for each day left in the
    month (TOWN_FLOOR_SHARE of its even share of a day): the floors for the days
    after today, and today's for the towns not in this run, are kept back, so one
    town can't spend what the others need for the rest of the month. When what's
    left doesn't cover the floors, each town in the run gets its floor for today.
    backlog_allowance paces older documents over the rest of the month: what's
    left beyond a reserve for new documents, spread over the days left and every
    town in the network (each has one daily run). Both are rounded down to the cent."""
    today = today or datetime.now(NETWORK_TZ).date()
    month = today.strftime("%Y-%m")
    towns = town_dirs(root)
    spent = 0.0
    for name in towns:
        path = root / TOWNS / name / "data" / SUMMARY_LEDGER
        if path.exists():
            row = json.loads(path.read_text()).get(month, {})
            # A summary (and one made again since), a cut-off request, a transcription, or a translation:
            # all paid for this month (summarize.LEDGER_COSTS).
            spent += sum(row.get(key, 0.0) for key in ("cost", "failed_cost", "transcript_cost", "translation_cost",
                                                          "replaced_cost"))
    left = max(monthly - spent, 0.0)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_left = days_in_month - today.day + 1
    in_run = max(towns_in_run, 1)
    floor = TOWN_FLOOR_SHARE * monthly / days_in_month / max(len(towns), 1)
    kept = floor * len(towns) * (days_left - 1) + floor * max(len(towns) - in_run, 0)
    allowance = min(left / in_run, max((left - kept) / in_run, floor))
    backlog = max(left - monthly * NEW_DOCUMENTS_RESERVE, 0.0) / days_left / max(len(towns), 1)
    cents = lambda x: math.floor(x * 100) / 100
    return {"month": month, "budget": monthly, "spent": round(spent, 2), "left": round(left, 2),
            "floor": cents(floor), "kept_for_floors": round(kept, 2),
            "allowance": cents(allowance), "backlog_allowance": cents(min(backlog, allowance))}


def report(reports: Path) -> tuple[str, bool]:
    """A table of every town in the run, and whether the run succeeded: a run that fetches does
    (the daily alert lists the towns behind) unless it published none of its towns; one that only
    builds fails if a town does. The towns a fetching run didn't publish are named in the heading."""
    results = sorted((json.loads(p.read_text()) for p in reports.glob("*.json")), key=lambda r: r["folder"])
    lines = ["| Town | Result | Data | Failed steps |", "|---|---|---|---|"]
    for r in results:
        failed = [s["name"] for s in r["steps"] if not s["ok"]]
        failed += [f"{s['name']} (fetch)" for s in (r.get("update") or {}).get("steps", []) if not s["ok"]]
        status = ("published" if r["deployed"] else "built") if r["ok"] else "**failed**"
        data = "**stale**" if r["stale"] else "fresh"
        if r.get("failing"):
            data += f"; **checks failing**: {', '.join(r['failing'])}"
        lines.append(f"| {r['folder']} | {status} | {data} | {', '.join(failed) or '–'} |")
    bad = [r for r in results if not r["ok"] or r["stale"] or r.get("failing")]
    unpublished = [r for r in results if fetched(r) and not r["deployed"]]
    heading = f"{len(results)} towns; {len(bad)} need attention" if results else "No towns ran"
    if unpublished:
        heading += f"; **{len(unpublished)} not published**: {', '.join(r['folder'] for r in unpublished)}"
    failed = [r for r in results if not r["ok"] and not fetched(r)]
    # A run that fetched and published none of its towns fails, so it can't look like success.
    nothing_published = bool(results) and len(unpublished) == len(results)
    if nothing_published:
        heading += "\n\nNo town in this run was published: every site still shows its version from before."
    return f"## {heading}\n\n" + "\n".join(lines) + "\n", not failed and not nothing_published


def fetched(record: dict) -> bool:
    # Records from before "fetched" was kept: a fetching run's has the update's result.
    return record.get("fetched", record.get("update") is not None)


def town_features(config: dict) -> set[str]:
    """What an engine update could break in one town but not another: its state, each table of its
    config (311, permits, Drive folders, a digest...), and each kind of source its meetings come from."""
    meetings = config.get("meetings", {})
    return ({f"state:{config.get('town', {}).get('state')}"}
            | {f"table:{key}" for key in config if key not in ("slug", "site", "town")}
            | {f"meetings:{key}" for key, value in meetings.items() if isinstance(value, dict) and key != "aliases"})


def canaries(root: Path, towns: list[str] | None = None) -> list[str]:
    """The fewest towns (picked greedily, the one adding the most first, by name on a tie) that between them
    have every feature (town_features) of the towns given, or of every town."""
    features = {name: town_features(town_config(root, name)) for name in (towns or town_dirs(root))}
    left, picked = set().union(*features.values()) if features else set(), []
    while left:
        name = min(features, key=lambda n: (-len(features[n] & left), n))
        picked.append(name)
        left -= features[name]
    return sorted(picked)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--slots", type=int, default=1, help="runs a day the towns are spread over")
    p.add_argument("--slot", type=int, help="which of those runs this is (default: all towns)")
    p.add_argument("--towns", help="only these town folders, comma-separated")
    p.add_argument("--changed", type=Path, help="a file listing changed paths; only towns they touch")
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--due-hours", type=float, help="only towns whose last fetching run finished this many hours ago")
    r = sub.add_parser("run")
    r.add_argument("--root", type=Path, default=Path.cwd())
    r.add_argument("--towns", required=True, help="town folders, comma- or space-separated")
    r.add_argument("--fetch", action="store_true", help="fetch new data first")
    r.add_argument("--sources", choices=["all", "meetings", "figures", "311"], default="all",
                   help="with --fetch, which data to fetch (pipeline.update's groups)")
    r.add_argument("--deploy", action="store_true", help="publish each site that passes its checks")
    r.add_argument("--reports", type=Path, help="folder for each town's result")
    r.add_argument("--step-timeout", type=float, default=STEP_TIMEOUT)
    r.add_argument("--sample-checks", action="store_true",
                   help="run the browser checks on a sample of pages (site_checks/pages.py), as a daily run does")
    s = sub.add_parser("report")
    s.add_argument("reports", type=Path)
    b = sub.add_parser("behind")
    b.add_argument("--root", type=Path, default=Path.cwd())
    b.add_argument("--hours", type=float, default=BEHIND_HOURS)
    b.add_argument("--new-since", type=Path, metavar="FILE",
                   help="print only a comment naming the towns not in this earlier text of the issue")
    st = sub.add_parser("states")
    st.add_argument("--root", type=Path, default=Path.cwd())
    m = sub.add_parser("budget")
    m.add_argument("--root", type=Path, default=Path.cwd())
    m.add_argument("--monthly", type=float, required=True, help="the network's monthly summary budget, in dollars")
    m.add_argument("--towns-in-run", type=int, required=True)
    c = sub.add_parser("canaries")
    c.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    if args.command == "canaries":
        print(" ".join(canaries(args.root.resolve())))
        return 0
    if args.command == "plan":
        if args.slot is not None and not 0 <= args.slot < args.slots:
            raise SystemExit(f"--slot must be between 0 and {args.slots - 1}")
        changed = args.changed.read_text().split() if args.changed else None
        only = args.towns.split(",") if args.towns else None
        print(json.dumps(plan(args.root.resolve(), args.slots, args.slot, only, changed, max(args.batch_size, 1),
                              args.due_hours)))
        return 0
    if args.command == "run":
        root = args.root.resolve()
        results = [run_town(root, name, args.fetch, args.deploy, args.reports, args.step_timeout, args.sample_checks,
                            args.sources)
                   for name in args.towns.replace(",", " ").split()]
        for result in results:
            if not result["ok"]:
                level = "warning" if args.fetch else "error"
                print(f"::{level}::{result['folder']}: " + ", ".join(s["name"] for s in result["steps"] if not s["ok"]))
        # A fetching run keeps going when a town fails: its data is committed, and the daily alert says so.
        return 0 if args.fetch or all(r["ok"] for r in results) else 1
    if args.command == "behind":
        rows = behind(args.root.resolve(), args.hours)
        if args.new_since:
            print(newly_behind_text(rows, args.new_since.read_text(encoding="utf-8")), end="")
        else:
            print(behind_text(rows, args.hours), end="")
        return 0
    if args.command == "states":
        print(json.dumps(refresh_states(args.root.resolve()), indent=2))
        return 0
    if args.command == "budget":
        print(json.dumps(summary_budget(args.root.resolve(), args.monthly, args.towns_in_run)))
        return 0
    table, ok = report(args.reports)
    print(table)
    if os.environ.get(SUMMARY_ENV):
        with open(os.environ[SUMMARY_ENV], "a", encoding="utf-8") as f:
            f.write(table)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
