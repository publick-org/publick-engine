"""Fetch a town's new data from every source, in order.

Each source runs in its own process, as its own command would, so one that
fails or hangs doesn't stop the others. A failed source is reported and the
run goes on; only a step marked required (the 311 scorecard) fails the run.
Nothing is committed: the caller commits data/ afterwards.

This is the list of sources a town's daily update runs. The engine's
town.yml runs the same steps one by one (tests/test_update.py keeps the two
in step), and the network workflow runs this command for each town.

A figure source with a rhythm (pipeline/rhythms.py: the tax bill, budget,
school figures, unemployment, housing) is checked only when it's due, weekly
or monthly by how often it publishes; --force checks everything. Each step's
run of failures is kept in data/checks.json.

Usage:
    python -m pipeline.update [--town gloucester] [--sources all|meetings|figures|311]
                              [--step-timeout SECONDS] [--report report.json] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pipeline import rhythms
from pipeline.config import DATA_DIR, DEFAULT_TOWN, load_config


@dataclass(frozen=True)
class Source:
    name: str
    module: str
    # "meetings", "figures" (the indicators: tax, budget, schools, unemployment, housing, permits), or "311".
    # --sources picks them (GROUPS): "meetings" is everything but 311, and "figures" the indicators alone,
    # which are quick, for a run that only needs a town's figures refreshed.
    group: str
    args: tuple[str, ...] = ()
    # A required step failing fails the run; any other failure is a warning.
    required: bool = False
    # Keys only this step is given. Every other step runs without them.
    secrets: tuple[str, ...] = ()
    # The config table the step reads. A town without it skips the step without starting it.
    table: str | None = None


SOURCES = [
    Source("Fetch meetings", "pipeline.fetch_meetings", "meetings", table="meetings"),
    Source("Fetch minutes", "pipeline.fetch_minutes", "meetings", table="archive"),
    Source("Fetch School Committee documents", "pipeline.fetch_drive_meetings", "meetings", table="drive_meetings"),
    Source("Summarize agendas", "pipeline.summarize", "meetings", secrets=("ANTHROPIC_API_KEY",), table="summaries"),
    Source("Fetch tax bill", "pipeline.fetch_finance", "figures", table="finance"),
    Source("Fetch unemployment", "pipeline.fetch_labor", "figures", secrets=("BLS_API_KEY",), table="labor"),
    Source("Fetch school figures", "pipeline.fetch_schools", "figures", table="schools"),
    Source("Fetch budget figures", "pipeline.fetch_budget", "figures", table="finance"),
    Source("Fetch housing figures", "pipeline.fetch_housing", "figures", table="housing"),
    Source("Fetch building permits", "pipeline.fetch_permits", "figures", table="permits"),
    Source("Move saved documents to storage", "pipeline.documents", "meetings", args=("upload",)),
    Source("Fetch 311 requests", "pipeline.fetch_311", "311", table="seeclickfix"),
    Source("Compute 311 scorecard", "pipeline.compute_311", "311", required=True, table="seeclickfix"),
]

GROUPS = {"all": ("meetings", "figures", "311"), "meetings": ("meetings", "figures"), "figures": ("figures",),
          "311": ("311",)}
SECRETS = {key for source in SOURCES for key in source.secrets}


def command(source: Source, town: str) -> list[str]:
    return [sys.executable, "-m", source.module, *source.args, "--town", town]


def step_env(source: Source) -> dict:
    """The environment for one step: everything but the keys other steps are given."""
    return {k: v for k, v in os.environ.items() if k not in SECRETS or k in source.secrets}


def run_step(source: Source, town: str, timeout: float | None) -> dict:
    started = time.monotonic()
    try:
        code = subprocess.run(command(source, town), env=step_env(source), timeout=timeout).returncode
        error = None if code == 0 else f"exited with status {code}"
    except subprocess.TimeoutExpired:
        error = f"stopped after {timeout:g} seconds"
    return {"name": source.name, "ok": error is None, "required": source.required,
            "error": error, "seconds": round(time.monotonic() - started, 1)}


def run(town: str, sources: str = "all", timeout: float | None = None, config: dict | None = None,
        data_dir: Path | None = None, force: bool = False, now: datetime | None = None) -> dict:
    """Run the town's update steps. With its config and data folder, figure steps that aren't due are
    skipped, and each step's result is added to data/checks.json."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    now = now or datetime.now(timezone.utc)
    by_step = {r.step: r for r in rhythms.for_town(config)} if config is not None else {}
    steps = []
    for source in (s for s in SOURCES if s.group in GROUPS[sources]):
        rhythm = by_step.get(source.name)
        cadence = rhythm.cadence if rhythm else "continuous"
        if config is not None and source.table and source.table not in config:
            # The town doesn't have this source (most towns have no 311, permits file, or Drive folders).
            steps.append({"name": source.name, "ok": True, "required": source.required, "error": None,
                          "seconds": 0, "cadence": cadence, "skipped": f"no [{source.table}] in the config"})
            continue
        if rhythm and data_dir is not None and not force:
            due, why = rhythms.due(rhythm, data_dir, now)
            if not due:
                print(f"{source.name}: skipped, {why}.", flush=True)
                steps.append({"name": source.name, "ok": True, "required": source.required, "error": None,
                              "seconds": 0, "cadence": cadence, "skipped": why})
                continue
        print(f"::group::{source.name}", flush=True)
        step = {**run_step(source, town, timeout), "cadence": cadence}
        print("::endgroup::", flush=True)
        if not step["ok"]:
            level = "error" if source.required else "warning"
            print(f"::{level}::{town}: {source.name} {step['error']}", flush=True)
        steps.append(step)
    if data_dir is not None:
        rhythms.record_checks(data_dir, steps, started)
    return {"town": town, "sources": sources, "started_at": started,
            "ok": all(s["ok"] for s in steps if s["required"]), "steps": steps}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--sources", choices=list(GROUPS), default="all")
    parser.add_argument("--step-timeout", type=float, help="seconds before a step is stopped (default: no limit)")
    parser.add_argument("--report", type=Path, help="write each step's result to this JSON file")
    parser.add_argument("--force", action="store_true", help="check every figure source, due or not")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    if not args.town:
        raise SystemExit("Pass --town or set TOWN.")
    result = run(args.town, args.sources, args.step_timeout, config=load_config(args.town), data_dir=args.data,
                 force=args.force)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    failed = [s["name"] for s in result["steps"] if not s["ok"]]
    skipped = [s["name"] for s in result["steps"] if s.get("skipped")]
    print(f"{args.town}: {len(result['steps']) - len(failed)} of {len(result['steps'])} steps succeeded"
          + (f" ({len(skipped)} not due)" if skipped else "") + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
