"""Connecticut's answers for a new town (pipeline/new_town.py; pipeline/states/ma/lookups.py says what a
lookups module gives): OPM's name and code for the town, from its mill rates dataset on data.ct.gov, and the
town's district in EdSight, "<Town> School District", confirmed by EdSight's graduation export answering for
it. A town in a regional district (Regional School District 13) is left for a person.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.http import FetchError
from pipeline.states.ct import opendata, schools
from pipeline.states.lookups import BUDGET_DOCUMENTS, result, todo

FINANCE = ("Budget, mill rate, and property tax (pipeline/states/ct/), from the Office of Policy and Management's "
           "statewide datasets on data.ct.gov; the average single-family bill is calculated from the state's yearly "
           "Parcel and CAMA file.")
SCHOOLS = "School district figures from EdSight, the State Department of Education's data portal."


def codes(name: str, client, network: Path) -> dict:
    out = result()
    try:
        rows = opendata.query(client, opendata.MILL_RATES, select="municipality, town_code",
                              where=f"upper(municipality) = {opendata.quoted(name.upper())}", limit=1)
    except (FetchError, ValueError) as e:
        rows = []
        out["notes"].append(f"OPM's mill rates on data.ct.gov could not be read ({e}).")
    if rows:
        town, code = rows[0]["municipality"], int(rows[0]["town_code"])
        out["finance"] = {"opm_town": town, "opm_code": code}
        todo(out, "finance", BUDGET_DOCUMENTS.format(kind="town"))
        out["found"].append(("OPM name and town code", f"{town}, {code}", opendata.page(opendata.MILL_RATES)))
        out["notes"].append("The tax bill counts parcels with state use code 101 or 1010 as single-family homes; a "
                            "town that codes them otherwise needs [finance] single_family_use (pipeline/states/ct/"
                            "tax_bill.py).")
    district = f"{name} School District"
    program, settings, page = schools.TRENDS["graduation"]
    url = schools.export_url(program, district, settings)
    try:
        answered = any(schools.parse_trend(client.get(url).text).values())
    except (FetchError, ValueError):
        answered = False
    if answered:
        out["schools"] = {"edsight_district": district, "district_name": f"{name} Public Schools"}
        out["found"].append(("EdSight district", district, page))
        out["notes"].append(f"district_name is \"{name} Public Schools\": check it's the district's own name.")
    else:
        out["schools_todo"] = (f"[schools] edsight_district, the town's district as EdSight names it (EdSight has "
                               f"no graduation rates for \"{district}\": a regional district, or one without a "
                               "high school), and district_name.")
    return out
