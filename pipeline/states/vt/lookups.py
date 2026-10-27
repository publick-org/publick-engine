"""Vermont's answers for a new town (pipeline/new_town.py; pipeline/states/ma/lookups.py says what a lookups
module gives):

- PVR's name for the town ([finance] pvr_town: "Burlington", "Rutland City"), from figures/;
- its district's code in the AOE's per pupil spending report ([schools] aoe_lea, "T037"), from figures/;
- its district's organization on the Education Dashboard ([schools] aoe_org, "SU015"), the supervisory
  union or district named for the town ("BURLINGTON SD") on data.vermont.gov. A town in a union of several
  gets the unions named for it to choose from.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.http import FetchError
from pipeline.states.lookups import BUDGET_DOCUMENTS, figure_names, figures_source, naming, result, same, todo
from pipeline.states.vt import figures
from pipeline.states.vt.schools import STUDENT_INFORMATION, page, query

FINANCE = ("Tax rates, property tax, and the average homestead tax bill (pipeline/states/vt/), from the Vermont "
           "Department of Taxes' Property Valuation and Review (PVR) figures and VCGI's statewide parcel data.")
SCHOOLS = "School figures from the Vermont Agency of Education."


def lea(names: list[str]) -> tuple[str, str] | None:
    """(code, name) of the district the spending report names as one of names."""
    rows = {row["name"]: code for year in figures.load("spending")["years"].values() for code, row in year.items()}
    for name in names:
        found = same(name, rows)
        if found:
            return rows[found], found
    return None


def unions(client) -> dict[str, str]:
    """{name: organization} of every supervisory union and district on the Education Dashboard."""
    rows = query(client, STUDENT_INFORMATION, select="supervisoryunionidentifier, supervisoryunionname",
                 group="supervisoryunionidentifier, supervisoryunionname")
    return {r["supervisoryunionname"]: r["supervisoryunionidentifier"] for r in rows if r.get("supervisoryunionname")}


def codes(name: str, client, network: Path) -> dict:
    out = result()
    towns = figure_names("vt", "tax")
    town = same(name, towns) or same(f"{name} City", towns)
    if town:
        out["finance"] = {"pvr_town": town}
        todo(out, "finance", BUDGET_DOCUMENTS.format(kind="city"))
        out["found"].append(("PVR name", town, figures_source("vt", "tax")))
    names = [n for n in (town, name) if n]
    schools: dict = {}
    # An incorporated district is the town's name and "ID" (Winooski's).
    found_lea = lea([*names, f"{name} ID"])
    if found_lea:
        schools["aoe_lea"] = found_lea[0]
        out["found"].append(("Spending report district", f"{found_lea[0]} ({found_lea[1]})",
                             figures_source("vt", "spending")))
    try:
        listed = unions(client)
    except (FetchError, ValueError) as e:
        listed = {}
        out["notes"].append(f"The Education Dashboard's districts could not be read ({e}).")
    union = next((u for n in names for u in (same(f"{n} SD", listed), same(f"{n} School District", listed)) if u), None)
    if union:
        schools["aoe_org"] = listed[union]
        out["found"].append(("Education Dashboard district", f"{listed[union]} ({union})", page(STUDENT_INFORMATION)))
    if "aoe_org" in schools and "aoe_lea" in schools:
        out["schools"] = {"aoe_org": schools["aoe_org"], "aoe_lea": schools["aoe_lea"],
                          "district_name": f"{name} School District"}
        out["notes"].append(f"district_name is \"{name} School District\": check it's the district's own name.")
    else:
        named = "; ".join(f'{listed[u]} "{u}"' for u in naming(name, listed))
        out["schools_todo"] = (
            "[schools] aoe_org (the district or supervisory union on the Education Dashboard), aoe_lea (the "
            "district's code in the per pupil spending report, pipeline/states/vt/figures/spending.json), and "
            "district_name" + (f"; found {', '.join(f'{k} = {v!r}' for k, v in schools.items())}" if schools else "")
            + (f"; the dashboard names {named}." if named else "."))
    return out
