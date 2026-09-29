"""New Hampshire school district figures for the schools page, from the state's
statewide files (figures/, from the yearly extract). All are as published:

- the four-year cohort graduation rate,
- the share of students scoring proficient or above (levels 3 and 4) on the
  New Hampshire Statewide Assessment System (NH SAS) in English and math, for
  all grades tested together (grades 3 to 8, and 11th graders' SAT),
- cost per pupil (current operating spending per student),

each for the district and the state. Writes data/schools/schools.json. Run by
python -m pipeline.fetch_schools.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import PoliteClient
from pipeline.states.nh import figures

YEARS_KEPT = 8
# NH SAS subject codes.
SUBJECTS = {"ela": "rea", "math": "mat"}


def client(config: dict) -> PoliteClient:
    """Nothing is fetched: the figures are in the engine."""
    return PoliteClient(config["site"]["user_agent"])


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    schools = config["schools"]
    district = schools["doe_district"]

    def series(kind: str, value) -> list[dict]:
        years = [{"year": y, "town": value(row), "state": value(state) if state else None}
                 for y, row, state in figures.with_state(kind, district)]
        return [y for y in years if y["town"] is not None][-YEARS_KEPT:]

    grad = {y: row for y, row in figures.rows("graduation", district)}
    measures = {
        "graduation": {"source_url": figures.load("graduation")["source_url"],
                       "years": [{**y, "cohort": grad[y["year"]]["cohort"], "graduated": grad[y["year"]]["graduated"]}
                                 for y in series("graduation", lambda r: r["rate"])]},
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
