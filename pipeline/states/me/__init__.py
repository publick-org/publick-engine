"""Maine: Maine Revenue Services (MRS) for tax rates and valuations, the
Department of Education's ESSA Dashboard for schools, the Census Bureau for
population, and the Maine GeoLibrary's parcel assessment table for
single-family values. Pages are in site/states/me/.

MRS's and the Department of Education's figures are statewide, saved once a
year into figures/ by extract.py (a 150-page PDF and a Tableau dashboard, which
a town's daily run shouldn't read); each town's run reads its rows from there.
Names are the state's own, as in its files.

    [finance]  mrs_municipality (the town's name as MRS writes it, "Lewiston"), megis_geocode (its GEOCODE in
               the Maine GeoLibrary's parcel table, "01050"), single_family_use (a list of the land use codes the
               town gives its single-family homes in that table, ["101"]; towns code differently, so there's no
               default); optional budget_documents_url
    [schools]  doe_district (the district's name in the ESSA Dashboard, "Lewiston Public Schools"), district_name

The tax bill needs all three [finance] keys; the budget figures need only mrs_municipality. A town
whose parcel table gives single-family homes no land use code leaves out megis_geocode and
single_family_use, and has no tax bill.
"""

from pipeline.i18n import N_
from pipeline.states import Source, State

STATE = State(
    code="ME",
    name="Maine",
    sources={
        "tax_bill": Source("finance", ("mrs_municipality", "megis_geocode", "single_family_use"),
                           "pipeline.states.me.tax_bill", optional=True),
        "budget": Source("finance", ("mrs_municipality",), "pipeline.states.me.budget"),
        "schools": Source("schools", ("doe_district", "district_name"), "pipeline.states.me.schools"),
    },
    tax_source=N_("Calculated by Publick from state figures"),
    pages="pipeline.states.me.pages",
)
