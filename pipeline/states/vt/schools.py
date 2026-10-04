"""Vermont school district figures for the schools page, for the district and the
state, from the Agency of Education (AOE):

- the four-year graduation rate, as published (the Vermont Education
  Dashboard's student information, on data.vermont.gov);
- the share of students chronically absent (missing 10% or more of the school
  days they were enrolled), from the AOE's counts of students who were and
  weren't, on data.vermont.gov: the dataset suppresses the percentage itself
  for all students, so Publick divides. Calculated by Publick;
- the share of students in grades 3 to 9 proficient or above on the Vermont
  Comprehensive Assessment Program (VTCAP) in English and math: the AOE
  publishes each grade's share and the number of students tested, so Publick
  adds the grades together, weighted by students tested. Calculated by Publick.
  Each spring's results are a dataset of their own on data.vermont.gov, found
  by name ("Vermont Education Dashboard: General Assessment 2025"); years
  before 2025 aren't there, so the history grows from 2025;
- the district's budget per pupil as voted, and its education spending per
  pupil (the budget less its own other revenues: what the education tax pays),
  per long-term weighted pupil, from the AOE's yearly per pupil spending report
  (figures/spending.json, from the yearly extract). Published.

A district is named by two codes: its organization on the dashboard (the
supervisory district, "SU015" for Burlington) and its school district in the
spending report ("T037"). Years are school years' ending years (2024-25 is
2025). Writes data/schools/schools.json. Run by python -m pipeline.fetch_schools.
"""

from __future__ import annotations

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
from pipeline.states.vt import figures

PORTAL = "https://data.vermont.gov"
CATALOG = "https://api.us.socrata.com/api/catalog/v1"
STUDENT_INFORMATION = "fjrz-5mmw"
ABSENTEEISM = "u4te-7p3s"
ASSESSMENT_NAME = re.compile(r"^Vermont Education Dashboard: General Assessment (\d{4})$")
STATE_ORG = "VT001"
YEARS_KEPT = 8
SUBJECTS = {"tests_ela": "English Language Arts Grade", "tests_math": "Math Grade"}


# The dashboard's graduation rates and attendance reach data.vermont.gov in the summer after the school year;
# test results the next spring; the spending report in February, for the fiscal year under way.
RHYTHM = Rhythm(N_("School figures"), "schools/schools.json", "Fetch school figures", "yearly", (
    Part(latest_year("measures.graduation.years", "year"), on(8), lambda y: _("Class of {year} graduation rate").format(year=y)),
    Part(latest_year("measures.absenteeism.years", "year"), on(8), lambda y: _("{years} attendance").format(years=f"{y - 1}–{y % 100:02d}")),
    Part(latest_year("measures.tests_ela.years", "year"), on(5, years_after=1),
         lambda y: _("Spring {year} state test results").format(year=y)),
    Part(latest_year("measures.budget_per_pupil.years", "year"), on(3),
         lambda y: _("{years} spending per pupil").format(years=f"{y - 1}–{y % 100:02d}")),
))


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=90)


def page(dataset: str) -> str:
    return f"{PORTAL}/d/{dataset}"


def query(client, dataset: str, **params) -> list[dict]:
    params.setdefault("limit", 5000)
    data = client.get(f"{PORTAL}/resource/{dataset}.json?" + urlencode({f"${k}": v for k, v in params.items()})).json()
    if isinstance(data, dict):
        raise FetchError(f"data.vermont.gov ({dataset}): {data.get('message') or data}")
    return data


def number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def graduation(client, org: str) -> list[dict]:
    """The four-year rate by class, as a percentage. The dashboard lists it on each of the district's high schools'
    rows, labeled "Four-year" in some years and "4-year Graduation Rate" in others."""
    rows = query(client, STUDENT_INFORMATION, select="schoolyear, supervisoryunionpercentage, statepercentage",
                 where=f"supervisoryunionidentifier='{org}' AND studentinformationgroup='Graduation Rate' AND "
                       "studentinformationlabel IN ('Four-year', '4-year Graduation Rate')")
    years = {}
    for r in rows:
        town, state = number(r.get("supervisoryunionpercentage")), number(r.get("statepercentage"))
        if town:
            years[int(r["schoolyear"])] = {"year": int(r["schoolyear"]), "town": round(town * 100, 1),
                                           "state": round(state * 100, 1) if state else None}
    return [years[y] for y in sorted(years)][-YEARS_KEPT:]


def absenteeism(client, org: str) -> list[dict]:
    """The share of all students chronically absent, from the counts, for the district and the state."""
    rows = query(client, ABSENTEEISM, select="schoolyear, organizationidentifier, chronicallyabsentcount, "
                                             "notchronicallyabsentcount",
                 where=f"organizationidentifier IN ('{org}', '{STATE_ORG}') AND "
                       "organizationtype IN ('Authorizing District', 'State') AND upper(grouptype) = 'ALL STUDENTS'")
    shares: dict[int, dict] = {}
    for r in rows:
        absent, present = number(r.get("chronicallyabsentcount")), number(r.get("notchronicallyabsentcount"))
        if absent is None or present is None or not absent + present:
            continue   # suppressed ("***")
        place = "state" if r["organizationidentifier"] == STATE_ORG else "town"
        shares.setdefault(int(r["schoolyear"]), {})[place] = round(absent / (absent + present) * 100, 1)
    years = [{"year": y, "town": s["town"], "state": s.get("state")} for y, s in sorted(shares.items()) if "town" in s]
    return years[-YEARS_KEPT:]


def assessment_datasets(client) -> dict[int, str]:
    url = CATALOG + "?" + urlencode({"domains": "data.vermont.gov", "q": "General Assessment", "limit": 50})
    return {int(m.group(1)): r["resource"]["id"] for r in client.get(url).json().get("results", [])
            if (m := ASSESSMENT_NAME.match(r.get("resource", {}).get("name", "").strip()))}


def proficiency(rows: list[dict], subject: str, column: str) -> float | None:
    """The share proficient and above across grades, weighted by students tested, as a percentage."""
    tested, proficient = {}, {}
    for r in rows:
        if not r["testname"].startswith(subject) or (value := number(r.get(column))) is None:
            continue
        if r["indicatorlabel"] == "Number of Students Tested":
            tested[r["testname"]] = value
        elif r["indicatorlabel"] == "Total Proficient and Above":
            proficient[r["testname"]] = value
    grades = [g for g in tested if g in proficient and tested[g]]
    total = sum(tested[g] for g in grades)
    return round(sum(tested[g] * proficient[g] for g in grades) / total * 100, 1) if total else None


def tests(client, org: str, saved: dict) -> tuple[dict, dict]:
    """{measure: {year: {"town", "state"}}} for the spring results not saved yet (and the newest), and their sources."""
    found: dict[str, dict] = {name: {} for name in SUBJECTS}
    sources = {}
    have = {y["year"] for y in saved.get("tests_ela", {}).get("years", [])}
    datasets = assessment_datasets(client)
    for year, dataset in sorted(datasets.items()):
        if year in have and year != max(datasets):
            continue
        rows = query(client, dataset, select="testname, indicatorlabel, value_w_susd, value_w_st",
                     where=f"schoolidentifier='{org}' AND assessgroup='All Students' AND indicatorlabel IN "
                           "('Number of Students Tested', 'Total Proficient and Above')")
        for name, subject in SUBJECTS.items():
            town = proficiency(rows, subject, "value_w_susd")
            if town is not None:
                found[name][year] = {"year": year, "town": town, "state": proficiency(rows, subject, "value_w_st")}
        sources[year] = page(dataset)
    return found, sources


def spending(lea: str, key: str) -> list[dict]:
    """A spending figure by fiscal year, for the district and the state."""
    state = {int(y): r[figures.STATE] for y, r in figures.load("spending")["years"].items() if figures.STATE in r}
    years = [{"year": y, "town": round(row[key]), "state": round(state[y][key]) if y in state else None}
             for y, row in figures.rows("spending", lea) if row.get(key) is not None]
    return years[-YEARS_KEPT:]


def merged(saved: list[dict], found: dict[int, dict]) -> list[dict]:
    years = {y["year"]: y for y in saved}
    years.update(found)
    return [years[y] for y in sorted(years)][-YEARS_KEPT:]


TESTS_CALCULATED = ("The AOE publishes each grade's results; this is the share proficient or above across grades 3 "
                    "to 9 together, each grade weighted by the number of its students tested.")


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    schools = config["schools"]
    org, lea = schools["aoe_org"], schools["aoe_lea"]
    path = data_dir / "schools" / "schools.json"
    saved = json.loads(path.read_text(encoding="utf-8"))["measures"] if path.exists() else {}

    grad = graduation(client, org)
    absent = absenteeism(client, org)
    if not grad:
        raise FetchError(f"no graduation rates for {org}")
    found, test_sources = tests(client, org, saved)
    measures = {
        "graduation": {"source_url": page(STUDENT_INFORMATION), "years": grad},
        "absenteeism": {"source_url": page(ABSENTEEISM), "years": absent,
                        "calculated": "The AOE publishes how many students were chronically absent and how many "
                                      "weren't; this is the first over both."},
    }
    for name in SUBJECTS:
        old = saved.get(name, {})
        measures[name] = {"source_url": test_sources[max(test_sources)] if test_sources else old.get("source_url"),
                          "years": merged(old.get("years", []), found[name]), "calculated": TESTS_CALCULATED}
    spending_url = figures.load("spending")["source_url"]
    measures["budget_per_pupil"] = {"source_url": spending_url, "years": spending(lea, "budget")}
    measures["education_spending_per_pupil"] = {"source_url": spending_url, "years": spending(lea, "education_spending")}
    result = {
        "updated_at": now.isoformat(timespec="seconds"),
        "figures_extracted_at": figures.extracted_at("spending"),
        "source": "Vermont Agency of Education",
        "district": schools["district_name"],
        "measures": measures,
    }
    save_json(path, result)
    return {name: m["years"][-1] if m["years"] else None for name, m in measures.items()}
