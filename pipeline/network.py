"""Run many towns from one repository: the network's daily update, build, and deploy.

The network repository holds each town in its own folder, laid out as a town
repository is (config/, data/, site/static/):

  towns/gloucester-ma/config/gloucester.toml
  towns/gloucester-ma/data/...

plan picks the towns for one run and splits them into batches, printed as a
GitHub Actions matrix. run takes one batch and, for each town in turn, fetches
new data (pipeline.update), checks its freshness, builds the site, checks it
(site_checks/; with --sample-checks, the browser checks run on a sample of
pages, as the daily runs do), and publishes it (pipeline.deploy). Every step runs in its own
process, so one town's failure never stops the next. A run that fetches also
writes its result to the town's data/run.json, committed with the data, which
the network's status page reads. report writes one table of every town in the
run. A run that fetches doesn't fail when a town does: behind lists the towns
without a good update (published, with fresh data) in the last day or so, and
those whose figure checks keep failing, for the one daily alert. A run that only builds, as for a pull request, fails if
any town does.

budget splits what's left of the month's summary budget among the towns in a
run, from each town's data/summary-costs.json (pipeline.summarize).

    python -m pipeline.network plan   [--root .] [--slots 4 --slot N] [--towns a,b] [--changed FILE] [--batch-size 4]
    python -m pipeline.network run    [--root .] --towns a,b [--fetch [--sources all|meetings|figures|311]] [--deploy]
                                      [--sample-checks] [--reports DIR]
    python -m pipeline.network report DIR
    python -m pipeline.network behind [--root .] [--hours 30]
    python -m pipeline.network budget [--root .] --monthly 50 --towns-in-run N

Towns are spread over --slots runs a day by a stable hash of their folder
name, so a town keeps its slot as others are added.
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

from pipeline.config import ENGINE_DIR

TOWNS = "towns"
# A change to the engine version or the workflows rebuilds every town.
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
# The part of the monthly summary budget older documents can't use, kept for new ones.
NEW_DOCUMENTS_RESERVE = 0.2
# Each town's summary costs by month, as pipeline.summarize keeps them. Not imported from
# there: the network's plan and report jobs run without the engine's packages installed.
SUMMARY_LEDGER = "summary-costs.json"


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


def plan(root: Path, slots: int = 1, run_slot: int | None = None, only: list[str] | None = None,
         changed: list[str] | None = None, batch_size: int = 4) -> dict:
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
    batches = [towns[i:i + batch_size] for i in range(0, len(towns), batch_size)]
    return {"include": [{"towns": " ".join(b), "name": b[0] + (f" +{len(b) - 1}" if len(b) > 1 else "")}
                        for b in batches]}


def town_env(root: Path, name: str) -> dict:
    # The check step sets PUBLICK_CHECK_PAGES itself, so a full run is never sampled by accident.
    env = {k: v for k, v in os.environ.items() if k not in (SUMMARY_ENV, "PUBLICK_CHECK_PAGES")}
    env.update(PUBLICK_TOWN_DIR=str(root / TOWNS / name), TOWN=slug(root, name),
               PYTHONPATH=os.pathsep.join(filter(None, [str(ENGINE_DIR), os.environ.get("PYTHONPATH")])))
    return env


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
                          env, town_dir, None))
        if update_report.exists():
            result["update"] = json.loads(update_report.read_text())
            update_report.unlink()
        # As in town.yml: without a scorecard, the 311 data isn't committed.
        if not steps[-1]["ok"] and (town_dir / "data" / "311").is_dir():
            restore(town_dir / "data" / "311")
        freshness_report = town_dir / ".freshness-report.json"
        freshness_report.unlink(missing_ok=True)
        freshness = step("Check data freshness", [python, "-m", "pipeline.freshness", "--town", town,
                                                  "--report", str(freshness_report)], env, town_dir, BUILD_TIMEOUT)
        result["stale"] = not freshness["ok"]
        if freshness_report.exists():
            result["sources"] = trim_sources(json.loads(freshness_report.read_text()))
            freshness_report.unlink()
            # Figure sources whose checks keep failing: not behind yet, but the maintainer should know.
            result["failing"] = [r["label"] for r in result["sources"] if r.get("failing")]

    steps.append(step("Build site", [python, "-m", "pipeline.build_site", "--town", town, "--out", str(site)],
                      env, town_dir, BUILD_TIMEOUT))
    if steps[-1]["ok"]:
        # The browser checks take most of a town's time, so they run on every core (pytest-xdist).
        steps.append(step("Check site", [python, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-n", "auto",
                                         str(ENGINE_DIR / "site_checks")],
                          {**env, "PUBLICK_SITE_DIR": str(site),
                           **({"PUBLICK_CHECK_PAGES": "sample"} if sample_checks else {})}, town_dir, BUILD_TIMEOUT))
    checked = any(s["name"] == "Check site" and s["ok"] for s in steps)
    if deploy and checked:
        steps.append(step("Publish site", [python, "-m", "pipeline.deploy", "publish", "--town", town,
                                           "--site", str(site)], env, town_dir, BUILD_TIMEOUT))
        result["deployed"] = steps[-1]["ok"]
    result["ok"] = all(s["ok"] for s in steps)
    result["finished_at"] = now()
    if fetch and (town_dir / "data").is_dir():
        record = town_dir / "data" / RUN_RECORD
        previous = json.loads(record.read_text()) if record.exists() else {}
        result["last_good_at"] = result["finished_at"] if good(result) else last_good(previous)
        # What the town's data takes up, and how much this run added, for the status page.
        result["data_bytes"] = folder_bytes(town_dir / "data")
        result["data_bytes_added"] = result["data_bytes"] - data_before
        record.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if reports:
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


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


def summary_budget(root: Path, monthly: float, towns_in_run: int, today: date | None = None) -> dict:
    """Each town's share, for one run, of what's left of the month's summary budget.

    allowance is what's left, split among the run's towns, which run side by side.
    backlog_allowance paces older documents over the rest of the month: what's
    left beyond a reserve for new documents, spread over the days left and every
    town in the network (each has one daily run). Both are rounded down to the cent."""
    today = today or datetime.now(timezone.utc).date()
    month = today.strftime("%Y-%m")
    towns = town_dirs(root)
    spent = 0.0
    for name in towns:
        path = root / TOWNS / name / "data" / SUMMARY_LEDGER
        if path.exists():
            row = json.loads(path.read_text()).get(month, {})
            spent += row.get("cost", 0.0) + row.get("failed_cost", 0.0)
    left = max(monthly - spent, 0.0)
    allowance = left / max(towns_in_run, 1)
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
    backlog = max(left - monthly * NEW_DOCUMENTS_RESERVE, 0.0) / days_left / max(len(towns), 1)
    cents = lambda x: math.floor(x * 100) / 100
    return {"month": month, "budget": monthly, "spent": round(spent, 2), "left": round(left, 2),
            "allowance": cents(allowance), "backlog_allowance": cents(min(backlog, allowance))}


def report(reports: Path) -> tuple[str, bool]:
    """A table of every town in the run, and whether the run succeeded: a run that fetches always
    does (the daily alert lists the towns behind); one that only builds fails if a town does."""
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
    heading = f"{len(results)} towns; {len(bad)} need attention" if results else "No towns ran"
    failed = [r for r in results if not r["ok"] and not fetched(r)]
    return f"## {heading}\n\n" + "\n".join(lines) + "\n", not failed


def fetched(record: dict) -> bool:
    # Records from before "fetched" was kept: a fetching run's has the update's result.
    return record.get("fetched", record.get("update") is not None)


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
    m = sub.add_parser("budget")
    m.add_argument("--root", type=Path, default=Path.cwd())
    m.add_argument("--monthly", type=float, required=True, help="the network's monthly summary budget, in dollars")
    m.add_argument("--towns-in-run", type=int, required=True)
    args = parser.parse_args()

    if args.command == "plan":
        if args.slot is not None and not 0 <= args.slot < args.slots:
            raise SystemExit(f"--slot must be between 0 and {args.slots - 1}")
        changed = args.changed.read_text().split() if args.changed else None
        only = args.towns.split(",") if args.towns else None
        print(json.dumps(plan(args.root.resolve(), args.slots, args.slot, only, changed, max(args.batch_size, 1))))
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
        print(behind_text(behind(args.root.resolve(), args.hours), args.hours), end="")
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
