"""How often each figure source publishes, and what "behind" means for it.

Meetings and 311 change every day, so they're fetched every run and judged by
when they were last checked ([freshness] sources in the town's config). The
figures don't: a tax bill is certified once a year, school results come out
once a year, unemployment once a month. For those, what matters to a reader
is whether the site has the latest period that's been published, not when it
was last asked. And there's no reason to ask daily.

So each figure source has a Rhythm, defined once where its fetcher is (a
state's package for state sources, so every Massachusetts town shares
Massachusetts's): the periods it covers, when each new period usually
appears, and so how often it's worth checking.

- A source is **behind** when the period after its latest one should have
  appeared by now: its usual date plus a grace period (GRACE_MONTHS, or
  [freshness] grace_months) has passed. Release dates slip (a federal
  shutdown, a town that certifies late), so the grace is generous.
- A source is **due** for a check when its last successful check is older
  than its interval: a week for monthly sources; a month for yearly ones, or
  a week in the months around when a new period usually appears (WINDOW).
- A check that fails is tried again the next run. Failures are counted per
  step (data/checks.json, written by pipeline.update), and three in a row are
  reported to the maintainer; they don't make the source behind.

Usage by the pipeline: pipeline.update skips a figure step that isn't due;
pipeline.freshness reports each rhythm's row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Callable

from pipeline import states

GRACE_MONTHS = 2
# How long before a new period's usual date checks move from monthly to weekly.
WINDOW_MONTHS = 2
CHECK_DAYS = {"monthly": 7, "yearly": 30}
WINDOW_CHECK_DAYS = 7
FAILING_AFTER = 3
CHECKS_FILE = "checks.json"


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30,
            31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last))


def month_period(year: int, month: int) -> int:
    """A month as one number, so the next month is period + 1."""
    return year * 12 + month - 1


def month_of(period: int) -> date:
    return date(period // 12, period % 12 + 1, 1)


@dataclass(frozen=True)
class Part:
    """One series in a source's data file, with its own periods.

    latest(data) is the newest period the data has (a year, or a month_period),
    or None if the series isn't there (a part this town doesn't have).
    usual(period) is the date that period's figures usually appear by, and
    name(period) says which period it is ("Fiscal year 2027").
    """
    latest: Callable[[dict], int | None]
    usual: Callable[[int], date]
    name: Callable[[int], str]


@dataclass(frozen=True)
class Rhythm:
    label: str
    # The data file, under the town's data folder, and the update step that writes it.
    file: str
    step: str
    # "monthly" or "yearly": how often it publishes, which sets how often it's checked.
    cadence: str
    parts: tuple[Part, ...]


def latest_year(series: str, field: str) -> Callable[[dict], int | None]:
    """The newest value of field among the records of a list in the data (dots reach into tables)."""
    def read(data: dict) -> int | None:
        records = data
        for key in series.split("."):
            records = records.get(key) if isinstance(records, dict) else None
        values = [int(r[field]) for r in records or [] if isinstance(r, dict) and r.get(field) not in (None, "")]
        return max(values) if values else None
    return read


def on(month: int, day: int = 1, years_after: int = 0) -> Callable[[int], date]:
    """A yearly period's usual date: the given day of the period's year plus years_after."""
    return lambda period: date(period + years_after, month, day)


def for_town(config: dict) -> list[Rhythm]:
    """The rhythms of the figure sources this town has: its state's, and the national ones."""
    state = states.for_town(config)
    found = []
    for kind in states.KINDS:
        source = state.source(kind, config)
        if source and getattr(source.load(), "RHYTHM", None):
            found.append(source.load().RHYTHM)
    if "labor" in config:
        found.append(import_module("pipeline.fetch_labor").RHYTHM)
    if "housing" in config:
        found.append(import_module("pipeline.fetch_housing").rhythm(state.housing_parts(config)))
    return found


def read(data_dir: Path, rhythm: Rhythm) -> dict | None:
    path = data_dir / rhythm.file
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def last_checked(data: dict | None) -> datetime | None:
    stamp = (data or {}).get("updated_at")
    return datetime.fromisoformat(stamp) if stamp else None


def upcoming(rhythm: Rhythm, data: dict) -> list[tuple[Part, int]]:
    """Each part's next period: the one after its latest."""
    return [(part, latest + 1) for part in rhythm.parts if (latest := part.latest(data)) is not None]


def due(rhythm: Rhythm, data_dir: Path, now: datetime) -> tuple[bool, str]:
    """Whether the source should be checked this run, and why not if it shouldn't."""
    data = read(data_dir, rhythm)
    checked = last_checked(data)
    if checked is None:
        return True, ""
    today = now.date()
    days = CHECK_DAYS[rhythm.cadence]
    if any(add_months(part.usual(period), -WINDOW_MONTHS) <= today for part, period in upcoming(rhythm, data)):
        days = min(days, WINDOW_CHECK_DAYS)
    age = now - checked
    if age >= timedelta(days=days) - timedelta(hours=6):
        return True, ""
    return False, f"checked {age.days} days ago; checked every {days} days"


def load_checks(data_dir: Path) -> dict:
    path = data_dir / CHECKS_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def record_checks(data_dir: Path, steps: list[dict], at: str) -> None:
    """Keep each step's run of failures, so one that keeps failing can be reported."""
    checks = load_checks(data_dir)
    for step in steps:
        if step.get("skipped"):
            continue
        entry = checks.setdefault(step["name"], {"failures": 0})
        if step["ok"]:
            entry.update(failures=0, last_ok=at)
            entry.pop("error", None)
        else:
            entry.update(failures=entry.get("failures", 0) + 1, error=step.get("error"), last_failed=at)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / CHECKS_FILE).write_text(json.dumps(checks, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def row(rhythm: Rhythm, data_dir: Path, now: datetime, grace_months: int = GRACE_MONTHS,
        checks: dict | None = None) -> dict:
    """The freshness row for one source: its latest period, the next one and when it's usual, and
    whether it's behind."""
    data = read(data_dir, rhythm)
    failures = (checks or {}).get(rhythm.step, {}).get("failures", 0)
    base = {"label": rhythm.label, "updated_at": (data or {}).get("updated_at"), "max_days": None,
            "cadence": rhythm.cadence, "failing": failures if failures >= FAILING_AFTER else 0}
    if data is None:
        return {**base, "stale": True, "latest": None, "next": None, "behind": "no data yet"}
    today = now.date()
    latest, upcoming_text, behind = [], [], []
    for part, period in upcoming(rhythm, data):
        usual = part.usual(period)
        latest.append(part.name(period - 1))
        when = f"{part.name(period)} usually by {usual:%B} {usual.day}, {usual.year}"
        upcoming_text.append(when)
        if today >= add_months(usual, grace_months):
            behind.append(f"{part.name(period)} is later than usual (usually by {usual:%B} {usual.day}, {usual.year})")
    return {**base, "stale": bool(behind), "latest": "; ".join(latest) or None,
            "next": "; ".join(upcoming_text) or None, "behind": "; ".join(behind) or None}
