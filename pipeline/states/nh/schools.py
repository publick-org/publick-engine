"""New Hampshire school district figures for the schools page, from the state's
statewide files (figures/, from the yearly extract). All are as published:

- the four-year cohort graduation rate,
- the share of students scoring proficient or above (levels 3 and 4) on the
  New Hampshire Statewide Assessment System (NH SAS) in English and math, for
  all grades tested together (grades 3 to 8, and 11th graders' SAT),
- cost per pupil (current operating spending per student),

each for the district and the state. A district with no high school of its own takes the
graduation rate of the school its students go to, as the files name it ([schools]
graduation_school: Pinkerton Academy, for Derry Cooperative); one with neither has none, and
its page says so.
Writes data/schools/schools.json. Run by
python -m pipeline.fetch_schools.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import PoliteClient
from pipeline.i18n import N_, _
from pipeline.states.nh import figures
from pipeline.rhythms import Part, Rhythm, latest_year, on

YEARS_KEPT = 8
# NH SAS subject codes.
SUBJECTS = {"ela": "rea", "math": "mat"}


# The Department of Education posts state test results in September, a class's
# graduation rate the next spring, and cost per pupil the January after the
# fiscal year. They reach the engine's figures when they're extracted (extract.py).
RHYTHM = Rhythm(N_("School figures"), "schools/schools.json", "Fetch school figures", "yearly", (
    Part(latest_year("measures.graduation.years", "year"), on(6, years_after=1),
         lambda y: _("Class of {year} graduation rate").format(year=y)),
    Part(latest_year("measures.sas_ela.years", "year"), on(10), lambda y: _("Spring {year} state test results").format(year=y)),
    Part(latest_year("measures.cost_per_pupil.years", "year"), on(2, years_after=1),
         lambda y: _("{years} cost per pupil").format(years=f"{y - 1}–{y % 100:02d}")),
))


def client(config: dict) -> PoliteClient:
    """Nothing is fetched: the figures are in the engine."""
    return PoliteClient(config["site"]["user_agent"])


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    schools = config["schools"]
    district = schools["doe_district"]
    # A district with no high school of its own (Derry Cooperative's students go to
    # Pinkerton Academy): the graduation rate is that school's, named on the page.
    high_school = schools.get("graduation_school")

    def series(kind: str, value, name: str = district) -> list[dict]:
        years = [{"year": y, "town": value(row), "state": value(state) if state else None}
                 for y, row, state in figures.with_state(kind, name)]
        return [y for y in years if y["town"] is not None][-YEARS_KEPT:]

    # A district with no high school and no graduation_school has no graduation rate in the state's
    # files: the page says why, rather than showing none or another district's.
    no_rate = not high_school and not figures.has("graduation", district)
    grad = {} if no_rate else {y: row for y, row in figures.rows("graduation", high_school or district)}
    measures = {
        "graduation": {"source_url": figures.load("graduation")["source_url"],
                       "school": high_school,
                       **({"none_published": True} if no_rate else {}),
                       "years": [] if no_rate else
                       [{**y, "cohort": grad[y["year"]]["cohort"], "graduated": grad[y["year"]]["graduated"]}
                        for y in series("graduation", lambda r: r["rate"], high_school or district)]},
        **{f"sas_{key}": {"source_url": figures.load("assessment")["source_url"],
                          "years": series("assessment", lambda r, s=subject: (r.get(s) or {}).get("proficient"))}
           for key, subject in SUBJECTS.items()},
        "cost_per_pupil": {"source_url": figures.load("cost_per_pupil")["source_url"],
                           "years": series("cost_per_pupil", lambda r: r)},
    }
    result = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("graduation", "assessment", "cost_per_pupil"),
        "source": "New Hampshire Department of Education",
        "district": schools["district_name"],
        "measures": measures,
    }
    save_json(data_dir / "schools" / "schools.json", result)
    return {name: m["years"][-1] if m["years"] else None for name, m in measures.items()}
