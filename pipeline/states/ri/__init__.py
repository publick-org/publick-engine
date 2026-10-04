"""Rhode Island: the Division of Municipal Finance (DMF) for tax rates, assessed
values, and levies, the Census Bureau for population, and the Department of
Education (RIDE) for schools. Pages are in site/states/ri/.

The DMF's figures are statewide PDF tables saved once a year into figures/ by
extract.py (its website refuses automated requests); each town's run reads its
rows from there. RIDE's report card data files and its assessment data portal
answer automated requests, so schools.py fetches them.

There's no tax bill source. Rhode Island publishes no average single-family
tax bill, and no statewide file of assessed values to calculate one from:
the statewide parcel map has no values, and each town's assessor publishes its
own. So a Rhode Island town's home page shows no tax bill.

    [finance]  dmf_municipality (the town's name as the DMF's files write it: "South Kingstown");
               optional budget_documents_url
    [schools]  ride_district (the district's RIDE code: South Kingstown's is 32), district_name
"""

from pipeline.states import Source, State

STATE = State(
    code="RI",
    name="Rhode Island",
    sources={
        "budget": Source("finance", ("dmf_municipality",), "pipeline.states.ri.budget"),
        "schools": Source("schools", ("ride_district", "district_name"), "pipeline.states.ri.schools"),
    },
    pages="pipeline.states.ri.pages",
)
