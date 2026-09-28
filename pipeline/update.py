"""Fetch a town's new data from every source, in order.

Each source runs in its own process, as its own command would, so one that
fails or hangs doesn't stop the others. A failed source is reported and the
run goes on; only a step marked required (the 311 scorecard) fails the run.
Nothing is committed: the caller commits data/ afterwards.

This is the list of sources a town's daily update runs. The engine's
town.yml runs the same steps one by one (tests/test_update.py keeps the two
in step), and the network workflow runs this command for each town.

Usage:
    python -m pipeline.update [--town gloucester] [--sources all|meetings|311]
                              [--step-timeout SECONDS] [--report report.json]
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

from pipeline.config import DEFAULT_TOWN


@dataclass(frozen=True)
class Source:
    name: str
    module: str
    # "meetings" (meetings and the indicators) or "311"; the workflow's sources input picks one or both.
    group: str
    args: tuple[str, ...] = ()
    # A required step failing fails the run; any other failure is a warning.
    required: bool = False
    # Keys only this step is given. Every other step runs without them.
    secrets: tuple[str, ...] = ()


SOURCES = [
    Source("Fetch meetings", "pipeline.fetch_meetings", "meetings"),
    Source("Fetch minutes", "pipeline.fetch_minutes", "meetings"),
    Source("Fetch School Committee documents", "pipeline.fetch_drive_meetings", "meetings"),
    Source("Summarize agendas", "pipeline.summarize", "meetings", secrets=("ANTHROPIC_API_KEY",)),
    Source("Fetch tax bill", "pipeline.fetch_finance", "meetings"),
    Source("Fetch unemployment", "pipeline.fetch_labor", "meetings", secrets=("BLS_API_KEY",)),
    Source("Fetch school figures", "pipeline.fetch_schools", "meetings"),
    Source("Fetch budget figures", "pipeline.fetch_budget", "meetings"),
    Source("Fetch housing figures", "pipeline.fetch_housing", "meetings"),
    Source("Fetch building permits", "pipeline.fetch_permits", "meetings"),
    Source("Move saved documents to storage", "pipeline.documents", "meetings", args=("upload",)),
    Source("Fetch 311 requests", "pipeline.fetch_311", "311"),
    Source("Compute 311 scorecard", "pipeline.compute_311", "311", required=True),
]

GROUPS = {"all": ("meetings", "311"), "meetings": ("meetings",), "311": ("311",)}
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


def run(town: str, sources: str = "all", timeout: float | None = None) -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    steps = []
    for source in (s for s in SOURCES if s.group in GROUPS[sources]):
        print(f"::group::{source.name}", flush=True)
        step = run_step(source, town, timeout)
        print("::endgroup::", flush=True)
        if not step["ok"]:
            level = "error" if source.required else "warning"
            print(f"::{level}::{town}: {source.name} {step['error']}", flush=True)
        steps.append(step)
    return {"town": town, "sources": sources, "started_at": started,
            "ok": all(s["ok"] for s in steps if s["required"]), "steps": steps}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--sources", choices=list(GROUPS), default="all")
    parser.add_argument("--step-timeout", type=float, help="seconds before a step is stopped (default: no limit)")
    parser.add_argument("--report", type=Path, help="write each step's result to this JSON file")
    args = parser.parse_args()
    if not args.town:
        raise SystemExit("Pass --town or set TOWN.")
    result = run(args.town, args.sources, args.step_timeout)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    failed = [s["name"] for s in result["steps"] if not s["ok"]]
    print(f"{args.town}: {len(result['steps']) - len(failed)} of {len(result['steps'])} steps succeeded"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
