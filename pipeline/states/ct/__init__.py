"""Connecticut: the Office of Policy and Management (OPM) for the tax bill and
budget, through its statewide datasets on data.ct.gov, and the State Department
of Education's EdSight for schools. Pages are in site/states/ct/.

    [finance]  opm_town (the town's name in OPM's datasets), opm_code (its OPM town code, 1 to 169: Wallingford's
               is 148); optional single_family_use (the state use codes of the town's single-family homes in the
               parcel file, if not "101" or "1010"; see tax_bill.py) and budget_documents_url
    [schools]  edsight_district (the district's name in EdSight), district_name
"""

from pipeline.i18n import N_
from pipeline.states import Source, State

STATE = State(
    code="CT",
    name="Connecticut",
    sources={
        "tax_bill": Source("finance", ("opm_town", "opm_code"), "pipeline.states.ct.tax_bill"),
        "budget": Source("finance", ("opm_town", "opm_code"), "pipeline.states.ct.budget"),
        "schools": Source("schools", ("edsight_district", "district_name"), "pipeline.states.ct.schools"),
    },
    tax_source=N_("Calculated by Publick from state figures"),
    pages="pipeline.states.ct.pages",
)
