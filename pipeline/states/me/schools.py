"""Maine school district figures for the schools page, from the Department of
Education's ESSA Dashboard (figures/, from the yearly extract). Each for the
district and the state ("Statewide"), as published:

- the four-year adjusted cohort graduation rate,
- the share of students chronically absent (enrolled at least 10 days and
  absent 10% or more of them, excused or not),
- the share of students at or above state expectations on the state's tests
  in English language arts and math, all grades tested together (the Maine
  Through Year Assessment in grades 3 to 8, and the SAT in the third year of
  high school), from spring 2023, the first the dashboard has,
- total spending per pupil, from federal, state, and local funds, as the
  district reports it under the Every Student Succeeds Act.

Years are the years school years end: 2024-25 is 2025, its graduation rate is
the class of 2025's, and its tests were given in spring 2025. Writes
data/schools/schools.json. Run by python -m pipeline.fetch_schools.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import PoliteClient
from pipeline.i18n import N_, _
from pipeline.rhythms import Part, Rhythm, latest_year, on
from pipeline.states.me import figures

YEARS_KEPT = 8


# The ESSA Dashboard adds a school year's figures in the winter after it ends:
# 2024-25's were there in October 2026, 2025-26's not yet. They reach the
# engine's figures when they're extracted (extract.py --schools).
RHYTHM = Rhythm(N_("School figures"), "schools/schools.json", "Fetch school figures", "yearly", (
    Part(latest_year("measures.graduation.years", "year"), on(3, years_after=1),
         lambda y: _("Class of {year} graduation rate").format(year=y)),
    Part(latest_year("measures.absenteeism.years", "year"), on(3, years_after=1),
         lambda y: _("{years} chronic absenteeism").format(years=f"{y - 1}–{y % 100:02d}")),
    Part(latest_year("measures.tests_ela.years", "year"), on(3, years_after=1),
         lambda y: _("Spring {year} state test results").format(year=y)),
    Part(latest_year("measures.spending.years", "year"), on(3, years_after=1),
         lambda y: _("{years} spending per pupil").format(years=f"{y - 1}–{y % 100:02d}")),
))


def client(config: dict) -> PoliteClient:
    """Nothing is fetched: the figures are in the engine."""
    return PoliteClient(config["site"]["user_agent"])


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    schools = config["schools"]
    district = schools["doe_district"]

    def series(kind: str, value, extra=None) -> list[dict]:
        years = [{"year": y, "town": value(row), "state": value(state) if state else None, **(extra(row) if extra else {})}
                 for y, row, state in figures.with_state(kind, district)]
        return [y for y in years if y["town"] is not None][-YEARS_KEPT:]

    def measure(kind: str, value, extra=None) -> dict:
        return {"source_url": figures.load(kind)["source_url"], "years": series(kind, value, extra)}

    measures = {
        "graduation": measure("graduation", lambda r: r.get("rate"),
                              lambda r: {"cohort": r.get("cohort"), "graduated": r.get("graduated")}),
        "absenteeism": measure("absenteeism", lambda r: r.get("rate")),
        "tests_ela": measure("assessment", lambda r: (r.get("ela") or {}).get("proficient")),
        "tests_math": measure("assessment", lambda r: (r.get("math") or {}).get("proficient")),
        "spending": measure("spending", lambda r: r.get("per_pupil")),
    }
    result = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("graduation", "absenteeism", "assessment", "spending"),
        "source": "Maine Department of Education, ESSA Dashboard",
        "district": schools["district_name"],
        "measures": measures,
    }
    save_json(data_dir / "schools" / "schools.json", result)
    return {name: m["years"][-1] if m["years"] else None for name, m in measures.items()}
