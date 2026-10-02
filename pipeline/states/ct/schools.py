"""Connecticut school district figures for the schools page, from EdSight, the
State Department of Education's public data portal (public-edsight.ct.gov). Each
for the district and the state, as published:

- the four-year graduation rate,
- the share of students chronically absent (missing 10% or more of the school
  days they were enrolled),
- the share of students in grades 3 to 8 at level 3 or 4 (met or exceeded) on
  the Smarter Balanced tests in English and math,
- spending per pupil (EdSight's per pupil expenditures, all functions).

Each of EdSight's reports has an export, a CSV of what the report shows. The
exports answer without a login (EdSight's guest service) when the session keeps
the cookies its redirects set, which the client's session does; without them
EdSight answers with its sign-in page. A trend export covers the last five school
years, so each run keeps the years already saved and adds the new ones, up to
YEARS_KEPT. Spending has an export for each school year, so only years not saved
yet are asked for. Years are school years' ending years (2024-25 is 2025).
Writes data/schools/schools.json. Run by python -m pipeline.fetch_schools.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient
from pipeline.i18n import N_, _
from pipeline.rhythms import Part, Rhythm, latest_year, on

EXPORTS = "https://edsight.ct.gov/SASStoredProcess/guest?_program=/CTDOE/EdSight/Release/Reporting/Public/Reports/StoredProcesses/"
STATE = "State of Connecticut"
YEARS_KEPT = 8
# The first school year EdSight's spending export has. Years older than YEARS_KEPT
# aren't asked for, so a year that has dropped off isn't fetched again.
FIRST_SPENDING_YEAR = 2018

# Each trend export's program, its other settings, and the EdSight page it's from.
TRENDS = {
    "graduation": ("GraduationExport", {"_subgroup": "All Students", "_school": " ", "_rate": " ", "_gradcat": "grad"},
                   "https://public-edsight.ct.gov/performance/four-year-graduation-rates"),
    "absenteeism": ("ChronicAbsenteeismExport", {"_subgroup": "All Students", "_school": " "},
                    "https://public-edsight.ct.gov/students/chronic-absenteeism"),
    "tests": ("SmarterBalancedAssessmentExport",
              {"_subgroup": "All Students", "_school": " ", "_subject": "ELA and Math", "_grade": "All Grades Combined"},
              "https://public-edsight.ct.gov/performance/smarter-balanced-achievement-participation"),
}
SPENDING = ("EFSDistrictLevelbyFunctionExport",
            "https://public-edsight.ct.gov/overview/per-pupil-expenditures-by-function---district")
# The tests export's subjects, and the measure each becomes.
SUBJECTS = {"ELA": "tests_ela", "Math": "tests_math"}

# What EdSight had on 2026-10-01: the 2025-26 school year's attendance and spring 2026
# test results, the class of 2025's graduation rate, and 2024-25 spending. The dates
# a new year usually appears are estimated from that; adjust them from EdSight's
# release history as it builds up.
RHYTHM = Rhythm(N_("School figures (EdSight)"), "schools/schools.json", "Fetch school figures", "yearly", (
    Part(latest_year("measures.graduation.years", "year"), on(6, years_after=1),
         lambda y: _("Class of {year} graduation rate").format(year=y)),
    Part(latest_year("measures.tests_ela.years", "year"), on(10), lambda y: _("Spring {year} state test results").format(year=y)),
    Part(latest_year("measures.absenteeism.years", "year"), on(10), lambda y: _("{years} attendance").format(years=f"{y - 1}–{y % 100:02d}")),
    Part(latest_year("measures.spending.years", "year"), on(10, years_after=1),
         lambda y: _("{years} spending per pupil").format(years=f"{y - 1}–{y % 100:02d}")),
))

SCHOOL_YEAR = re.compile(r"^(\d{4})-(\d{2})$")


def export_url(program: str, district: str, settings: dict) -> str:
    return EXPORTS + program + "&" + urlencode({"_year": "Trend", "_district": district, **settings})


def spending_url(district: str, year: int) -> str:
    return EXPORTS + SPENDING[0] + "&" + urlencode({"_year": f"{year - 1}-{year % 100:02d}", "_district": district})


def ending_year(label: str) -> int | None:
    """'2024-25' -> 2025."""
    m = SCHOOL_YEAR.match(label.strip())
    return int(m.group(1)) + 1 if m else None


def number(cell: str) -> float | None:
    """A figure as published; None where EdSight suppresses or has none ('*', 'N/A')."""
    try:
        return float(cell.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def table(text: str) -> tuple[list[list[str]], int]:
    """An export's rows, and the index of its header row: the one starting "District"
    ("Organization" in some of the state's exports)."""
    rows = list(csv.reader(io.StringIO(text)))
    head = next((i for i, row in enumerate(rows) if row and row[0] in ("District", "Organization")), None)
    if head is None:
        if "Logon" in text or "<html" in text[:500].lower():
            raise FetchError("EdSight answered with its sign-in page, not the export")
        raise FetchError("EdSight's export has no table: " + " ".join(" ".join(r) for r in rows[:3])[:200])
    return rows, head


def parse_trend(text: str) -> dict[str | None, dict[int, float]]:
    """A trend export's figures: {subject (None if the export has none): {year: value}}.

    The school years label the columns, in the header row (graduation) or the row
    above it, one label over each pair of count and percentage columns (attendance,
    tests). The values are the percentage columns, or the year columns themselves.
    """
    rows, head = table(text)
    header = rows[head]
    years: dict[int, int] = {}
    for row in rows[:head + 1]:
        current = None
        for col, cell in enumerate(row):
            if (year := ending_year(cell)) is not None:
                current = year
            elif cell.strip():
                current = None
            if current is not None:
                years.setdefault(col, current)
    values = [col for col, label in enumerate(header) if col in years
              and (ending_year(label) is not None or label.strip() == "%" or label.startswith("Percentage"))]
    subject = header.index("Subject") if "Subject" in header else None
    found: dict[str | None, dict[int, float]] = {}
    for row in rows[head + 1:]:
        if not any(cell.strip() for cell in row):
            continue
        key = row[subject] if subject is not None else None
        for col in values:
            if col < len(row) and (value := number(row[col])) is not None:
                found.setdefault(key, {})[years[col]] = value
    return found


def parse_spending(text: str) -> int | None:
    """Spending per pupil, all functions (the export's Total row), or None when
    EdSight has no figures for that year yet."""
    if "did not contain any results" in text:
        return None
    rows, head = table(text)
    header = rows[head]
    function, ppe = header.index("Function"), header.index("PPE")
    total = next((row for row in rows[head + 1:] if len(row) > ppe and row[function] == "Total"), None)
    value = number(total[ppe]) if total else None
    return round(value) if value is not None else None


def merged(saved: list[dict], town: dict[int, float], state: dict[int, float]) -> list[dict]:
    """The saved years with the newly fetched ones, which replace a saved year's
    figures (EdSight revises them), oldest first."""
    years = {y["year"]: y for y in saved}
    for year, value in town.items():
        years[year] = {"year": year, "town": value, "state": state.get(year)}
    return [years[y] for y in sorted(years)][-YEARS_KEPT:]


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=90)


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    schools = config["schools"]
    district = schools["edsight_district"]
    path = data_dir / "schools" / "schools.json"
    saved = json.loads(path.read_text(encoding="utf-8"))["measures"] if path.exists() else {}

    def years(name: str) -> list[dict]:
        return saved.get(name, {}).get("years", [])

    measures = {}
    for kind, (program, settings, page) in TRENDS.items():
        town = parse_trend(client.get(export_url(program, district, settings)).text)
        state = parse_trend(client.get(export_url(program, STATE, settings)).text)
        names = SUBJECTS if kind == "tests" else {None: kind}
        for key, name in names.items():
            if not town.get(key):
                raise FetchError(f"no {name} figures for {district}")
            measures[name] = {"source_url": page, "years": merged(years(name), town[key], state.get(key, {}))}

    have = {y["year"] for y in years("spending") if y["state"] is not None}
    town, state = {}, {}
    for year in range(max(FIRST_SPENDING_YEAR, now.year - YEARS_KEPT), now.year + 1):
        if year in have:
            continue
        value = parse_spending(client.get(spending_url(district, year)).text)
        if value is None:
            continue
        town[year] = value
        state_value = parse_spending(client.get(spending_url(STATE, year)).text)
        if state_value is not None:
            state[year] = state_value
    if not town and not have:
        raise FetchError(f"no spending figures for {district}")
    measures["spending"] = {"source_url": SPENDING[1], "years": merged(years("spending"), town, state)}

    result = {
        "updated_at": now.isoformat(timespec="seconds"),
        "source": "Connecticut State Department of Education, EdSight",
        "district": schools["district_name"],
        "measures": measures,
    }
    save_json(path, result)
    return {name: m["years"][-1] for name, m in measures.items()}
