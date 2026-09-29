"""New Hampshire: the Department of Revenue Administration (DRA) for tax rates,
the Department of Education for schools, the Census Bureau for population, and
NH GRANIT's parcel map for single-family values. Pages are in site/states/nh/.

The DRA's and the Department of Education's figures are statewide files saved
once a year into figures/ by extract.py (their websites refuse automated
requests); each town's run reads its rows from there. Names are the state's own,
as in its files.

    [finance]  dra_municipality (the town's name in the DRA's files); optional parcels_town (its name on
               the parcel map, if different), budget_table_url and budget_table_name (the city's budget
               table; see budget.py), and budget_documents_url
    [schools]  doe_district (the district's name in the Department of Education's files), district_name
"""

from pipeline.states import Source, State

STATE = State(
    code="NH",
    name="New Hampshire",
    sources={
        "tax_bill": Source("finance", ("dra_municipality",), "pipeline.states.nh.tax_bill"),
        "budget": Source("finance", ("dra_municipality",), "pipeline.states.nh.budget"),
        "schools": Source("schools", ("doe_district", "district_name"), "pipeline.states.nh.schools"),
    },
    tax_source="Calculated by Publick from state figures",
    pages="pipeline.states.nh.pages",
)
