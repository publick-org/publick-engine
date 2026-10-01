"""Massachusetts: the Division of Local Services (DLS) Municipal Databank for the
tax bill and budget, and the Department of Elementary and Secondary Education
(DESE) for schools. Pages are in site/states/ma/.

    [finance]  dls_municipality, dls_code (the DLS name and code); optional budget_documents_url
    [schools]  district_code, district_name, portal; optional report_name and report_url
"""

from pipeline.i18n import N_
from pipeline.states import Source, State

STATE = State(
    code="MA",
    name="Massachusetts",
    sources={
        "tax_bill": Source("finance", ("dls_municipality",), "pipeline.states.ma.tax_bill"),
        "budget": Source("finance", ("dls_municipality", "dls_code"), "pipeline.states.ma.budget"),
        "schools": Source("schools", ("district_code", "district_name", "portal"), "pipeline.states.ma.schools"),
    },
    tax_source=N_("Mass. Division of Local Services"),
    housing="pipeline.states.ma.housing",
    pages="pipeline.states.ma.pages",
)
