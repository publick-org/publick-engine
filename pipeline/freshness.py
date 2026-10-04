"""Check that every data source is still updating.

Most update steps are allowed to fail without stopping the daily run, so a
source that breaks (a moved file, a changed page, a server that stops
answering) would otherwise go stale quietly. Sources that change daily
(meetings, 311: [freshness] sources in the town's config) are judged by when
they were last updated. Figure sources (the tax bill, budget, school figures,
unemployment, housing) are judged by the period they cover: they're behind
only when a newer period should have been published by now (pipeline/rhythms.py).
A figure source whose checks keep failing is marked as failing, for the
maintainer, without being behind. This also looks for new agendas and minutes
still waiting for a summary (a sign the AI summary step is failing); older
documents are summarized a little each day, within the network's budget, so
they don't count.

In the daily workflow it runs last and fails the run when anything is stale,
so GitHub emails the site's owner; the site is still built and deployed. The
About page shows the same table.

Usage:
    python -m pipeline.freshness [--town gloucester] [--report rows.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import rhythms, summarize
from pipeline.config import DATA_DIR, DEFAULT_TOWN, load_config
from pipeline.i18n import _


def last_update(data_dir: Path, source: dict) -> datetime | None:
    path = data_dir / source["file"]
    if not path.exists():
        return None
    stamp = json.loads(path.read_text(encoding="utf-8")).get(source.get("field", "updated_at"))
    return datetime.fromisoformat(stamp) if stamp else None


def waiting_summaries(config: dict, data_dir: Path, now: datetime, grace_days: float) -> list[str]:
    """New agendas and minutes (summarize.is_new) posted more than grace_days ago with no summary at all.

    New documents are summarized first, so one still waiting means the summary step
    isn't working. Older ones are left out: they're worked through a little each day,
    within the network's budget, and waiting is expected. A summary from an older
    prompt still shows on the site while its newer version waits, so only documents
    with nothing to show are counted.
    """
    model = config.get("summaries", {}).get("model")
    if not model:
        return []
    cutoff = now - timedelta(days=grace_days)
    return [f"{doc_kind} for {meeting['body']}, {meeting['date']}"
            for doc_kind, meeting, doc in summarize.pending_documents(data_dir, now.date().isoformat(), model,
                                                                      config["summaries"].get("since"))
            if datetime.fromisoformat(doc["fetched_at"]) < cutoff
            and summarize.is_new((doc_kind, meeting, doc), now)
            and not summarize.cached(data_dir, doc["sha256"], model, doc_kind, current=False)]


def check(config: dict, data_dir: Path, now: datetime | None = None) -> list[dict]:
    """One row per source: its label, its data file, last update, and whether it is stale. A daily source's row has
    its allowed age (max_days); a figure source's has its latest period, the next and when it usually
    appears, why it's behind if it is, and failing (its run of failed checks, from three)."""
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    fresh = {"sources": [], "summary_grace_days": 2, "grace_months": rhythms.GRACE_MONTHS,
             **config.get("freshness", {})}
    checks = rhythms.load_checks(data_dir)
    figures = rhythms.for_town(config)
    rows = []
    # A config row for a file a rhythm covers (from before rhythms) is left out: the rhythm's row replaces it.
    covered = {r.file for r in figures}
    for source in (s for s in fresh["sources"] if s["file"] not in covered):
        updated = last_update(data_dir, source)
        age = (now - updated).total_seconds() / 86400 if updated else None
        rows.append({"label": source["label"], "file": source["file"], "updated_at": updated.isoformat() if updated else None,
                     "max_days": source["max_days"], "stale": age is None or age > source["max_days"]})
    rows += [rhythms.row(r, data_dir, now, fresh["grace_months"], checks) for r in figures]
    if "summaries" in config:
        waiting = waiting_summaries(config, data_dir, now, fresh["summary_grace_days"])
        rows.append({"label": _("Meeting summaries"), "file": "summaries", "updated_at": None, "max_days": fresh["summary_grace_days"],
                     "stale": bool(waiting), "waiting": waiting})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--report", type=Path, help="also write the rows to this JSON file")
    args = parser.parse_args()
    rows = check(load_config(args.town), args.data)
    if args.report:
        args.report.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    lines = ["| Source | Last checked | Expected | Status |", "|---|---|---|---|"]
    for r in rows:
        when = r["updated_at"][:16].replace("T", " ") if r["updated_at"] else "–"
        extra = f" ({len(r['waiting'])} waiting)" if r.get("waiting") else ""
        expected = f"{r['max_days']} days" if r.get("max_days") else (r.get("next") or "–")
        status = "STALE" if r["stale"] else "OK"
        if r.get("failing"):
            status += f"; last {r['failing']} checks failed"
        lines.append(f"| {r['label']} | {when} | {expected} | {status}{extra} |")
        if r["stale"]:
            detail = ("; ".join(r["waiting"][:5]) if r.get("waiting")
                      else r.get("behind") or f"last updated {when}")
            print(f"::error::{r['label']} is behind: {detail}")
        if r.get("failing"):
            print(f"::warning::{r['label']}: the last {r['failing']} checks failed; the data may be current, "
                  f"but new figures won't arrive until a check succeeds.")
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Data freshness\n\n" + report + "\n")
    return 1 if any(r["stale"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
