"""Connecticut: the State Department of Education's EdSight for schools. Pages are
in site/states/ct/.

The tax bill and budget (the Office of Policy and Management's figures on
data.ct.gov) aren't here yet, so a Connecticut town has no tax bill on its home
page and no budget section until they are.

    [schools]  edsight_district (the district's name in EdSight), district_name
"""

from pipeline.states import Source, State

STATE = State(
    code="CT",
    name="Connecticut",
    sources={
        "schools": Source("schools", ("edsight_district", "district_name"), "pipeline.states.ct.schools"),
    },
)
