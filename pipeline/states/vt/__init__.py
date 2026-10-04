"""Vermont: the Department of Taxes' Division of Property Valuation and Review
(PVR) for tax rates, grand lists, and taxes raised, VCGI's statewide parcel data
for homestead values, and the Agency of Education (AOE) for schools. Pages are in
site/states/vt/.

PVR's figures and the AOE's spending report are yearly statewide workbooks,
saved into figures/ by extract.py; each town's run reads its rows from there.
Graduation, attendance, and test results come from data.vermont.gov at each run.
Names are the state's own, as in its files.

    [finance]  pvr_town (the town's name in PVR's files: "Burlington", "Rutland City"); optional parcels_town
               (its name in the parcel data, if different) and budget_documents_url
    [schools]  aoe_org (the district's organization on the Education Dashboard: "SU015"), aoe_lea (its school
               district code in the per pupil spending report: "T037"), district_name
"""

from pipeline.i18n import N_
from pipeline.states import Source, State

STATE = State(
    code="VT",
    name="Vermont",
    sources={
        "tax_bill": Source("finance", ("pvr_town",), "pipeline.states.vt.tax_bill"),
        "budget": Source("finance", ("pvr_town",), "pipeline.states.vt.budget"),
        "schools": Source("schools", ("aoe_org", "aoe_lea", "district_name"), "pipeline.states.vt.schools"),
    },
    tax_source=N_("Calculated by Publick from state figures"),
    tax_label=N_("Average homestead tax bill"),
    pages="pipeline.states.vt.pages",
)
