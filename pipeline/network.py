"""Run many towns from one repository: the network's daily update, build, and deploy.

The network repository holds each town in its own folder, laid out as a town
repository is (config/, data/, site/static/):

  towns/gloucester-ma/config/gloucester.toml
  towns/gloucester-ma/data/...

plan picks the towns for one run and splits them into batches, printed as a
GitHub Actions matrix. run takes one batch and, for each town in turn, fetches
new data (pipeline.update), checks its freshness, builds the site, checks it
(site_checks/), and publishes it (pipeline.deploy). Every step runs in its own
process, so one town's failure never stops the next. report reads every
town's result and fails once, for the whole run, if any town needs attention.

    python -m pipeline.network plan   [--root .] [--slots 4 --slot N] [--towns a,b] [--changed FILE] [--batch-size 4]
    python -m pipeline.network run    [--root .] --towns a,b [--fetch] [--deploy] [--reports DIR]
    python -m pipeline.network report DIR

Towns are spread over --slots runs a day by a stable hash of their folder
name, so a town keeps its slot as others are added.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
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
    env = {k: v for k, v in os.environ.items() if k != SUMMARY_ENV}
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


def run_town(root: Path, name: str, fetch: bool, deploy: bool, reports: Path | None,
             step_timeout: float = STEP_TIMEOUT) -> dict:
    town_dir = root / TOWNS / name
    env = town_env(root, name)
    town = env["TOWN"]
    python = sys.executable
    site = town_dir / "_site"
    result = {"town": town, "folder": name, "steps": [], "update": None, "stale": False, "deployed": False}
    steps = result["steps"]

    if fetch:
        update_report = town_dir / ".update-report.json"
        steps.append(step("Fetch new data", [python, "-m", "pipeline.update", "--town", town, "--step-timeout",
                                             str(step_timeout), "--report", str(update_report)],
                          env, town_dir, None))
        if update_report.exists():
            result["update"] = json.loads(update_report.read_text())
            update_report.unlink()
        # As in town.yml: without a scorecard, the 311 data isn't committed.
        if not steps[-1]["ok"] and (town_dir / "data" / "311").is_dir():
            restore(town_dir / "data" / "311")
        freshness = step("Check data freshness", [python, "-m", "pipeline.freshness", "--town", town],
                         env, town_dir, BUILD_TIMEOUT)
        result["stale"] = not freshness["ok"]

    steps.append(step("Build site", [python, "-m", "pipeline.build_site", "--town", town, "--out", str(site)],
                      env, town_dir, BUILD_TIMEOUT))
    if steps[-1]["ok"]:
        steps.append(step("Check site", [python, "-m", "pytest", "-p", "no:cacheprovider", "-q",
                                         str(ENGINE_DIR / "site_checks")],
                          {**env, "PUBLICK_SITE_DIR": str(site)}, town_dir, BUILD_TIMEOUT))
    checked = any(s["name"] == "Check site" and s["ok"] for s in steps)
    if deploy and checked:
        steps.append(step("Publish site", [python, "-m", "pipeline.deploy", "publish", "--town", town,
                                           "--site", str(site)], env, town_dir, BUILD_TIMEOUT))
        result["deployed"] = steps[-1]["ok"]
    result["ok"] = all(s["ok"] for s in steps)
    if reports:
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def report(reports: Path) -> tuple[str, bool]:
    """A table of every town in the run, and whether the run succeeded."""
    results = sorted((json.loads(p.read_text()) for p in reports.glob("*.json")), key=lambda r: r["folder"])
    lines = ["| Town | Result | Data | Failed steps |", "|---|---|---|---|"]
    for r in results:
        failed = [s["name"] for s in r["steps"] if not s["ok"]]
        failed += [f"{s['name']} (fetch)" for s in (r.get("update") or {}).get("steps", []) if not s["ok"]]
        status = ("published" if r["deployed"] else "built") if r["ok"] else "**failed**"
        lines.append(f"| {r['folder']} | {status} | {'**stale**' if r['stale'] else 'fresh'} | "
                     f"{', '.join(failed) or '–'} |")
    bad = [r for r in results if not r["ok"] or r["stale"]]
    heading = f"{len(results)} towns; {len(bad)} need attention" if results else "No towns ran"
    return f"## {heading}\n\n" + "\n".join(lines) + "\n", not bad


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
    r.add_argument("--deploy", action="store_true", help="publish each site that passes its checks")
    r.add_argument("--reports", type=Path, help="folder for each town's result")
    r.add_argument("--step-timeout", type=float, default=STEP_TIMEOUT)
    s = sub.add_parser("report")
    s.add_argument("reports", type=Path)
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
        results = [run_town(root, name, args.fetch, args.deploy, args.reports, args.step_timeout)
                   for name in args.towns.replace(",", " ").split()]
        for result in results:
            if not result["ok"]:
                print(f"::error::{result['folder']}: " + ", ".join(s["name"] for s in result["steps"] if not s["ok"]))
        return 0 if all(r["ok"] for r in results) else 1
    table, ok = report(args.reports)
    print(table)
    if os.environ.get(SUMMARY_ENV):
        with open(os.environ[SUMMARY_ENV], "a", encoding="utf-8") as f:
            f.write(table)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
