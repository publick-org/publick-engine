"""Each state's answers for a new town (pipeline/states/<st>/lookups.py, used by pipeline/new_town.py), from
the engine's saved statewide figures and stand-ins for the sources the lookups ask."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from conftest import FIXTURES
from fakes import FakeJSONResponse, FakeResponse
from test_ri import FakeRIDE

from pipeline.http import FetchError
from pipeline.states import lookups
from pipeline.states.ct import lookups as ct
from pipeline.states.ct import opendata
from pipeline.states.me import lookups as me
from pipeline.states.nh import lookups as nh
from pipeline.states.ri import lookups as ri
from pipeline.states.vt import lookups as vt

NETWORK = Path("unused")


class Answers:
    """Answers each address with the first (pattern in the address, JSON or text) that matches."""

    def __init__(self, *answers):
        self.answers, self.urls = answers, []

    def get(self, url):
        self.urls.append(url)
        for pattern, answer in self.answers:
            if pattern in url:
                return FakeResponse(answer.encode()) if isinstance(answer, str) else FakeJSONResponse(answer)
        raise FetchError(f"{url}: HTTP 404", 404)


def test_names_match_ignoring_case_and_spacing():
    assert lookups.same("rutland city", ["Rutland City", "Rutland Town"]) == "Rutland City"
    assert lookups.same("Derry", ["Derry Cooperative"]) is None
    assert lookups.naming("Derry", ["Derry  Cooperative", "Derry Cooperative", "Londonderry"]) == ["Derry  Cooperative"]
    assert lookups.naming("Warren", ["Bristol Warren", "Warwick"]) == ["Bristol Warren"]
    assert lookups.naming("Greenwich", ["Exeter-West Greenwich"]) == ["Exeter-West Greenwich"]


# ---- New Hampshire: the state's files saved in the engine ----

def test_new_hampshire_finds_the_dra_name_and_district():
    found = nh.codes("Nashua", None, NETWORK)
    assert found["finance"] == {"dra_municipality": "Nashua"}
    assert found["schools"] == {"doe_district": "Nashua", "district_name": "Nashua School District"}
    assert "budget_documents_url" in found["finance_todo"]


def test_new_hampshire_lists_a_cooperative_district_for_a_person():
    found = nh.codes("Derry", None, NETWORK)
    assert found["finance"] == {"dra_municipality": "Derry"} and "schools" not in found
    assert "Cooperative" in found["schools_todo"]


# ---- Maine: the saved files and the GeoLibrary's parcel table ----

def parcels(geocodes, uses):
    return Answers(("/10/query", {"features": [{"attributes": {"GEOCODE": g}} for g in geocodes]}),
                   ("/9/query", {"features": [{"attributes": {"parcels": n, "LAND_USE": code, "LAND_USE_D": about}}
                                              for n, code, about in uses]}))


def test_maine_finds_the_single_family_code_where_the_table_describes_it():
    client = parcels(["01050"], [(1047, "102", "TWO FAMILY"), (6510, "101", "SINGLE FAMILY")])
    found = me.codes("Lewiston", client, NETWORK)
    assert found["finance"] == {"mrs_municipality": "Lewiston", "megis_geocode": "01050", "single_family_use": ["101"]}
    assert found["schools"]["doe_district"] == "Lewiston Public Schools"
    assert "TOWN = 'Lewiston'" in parse_qs(urlparse(client.urls[0]).query)["where"][0]


def test_maine_leaves_out_the_tax_bill_where_no_parcel_has_a_land_use():
    found = me.codes("Portland", parcels(["05170"], [(24119, "0", "0")]), NETWORK)
    assert found["finance"] == {"mrs_municipality": "Portland"}
    assert "GEOCODE 05170" in found["finance_comment"] and "budget_documents_url" in found["finance_todo"]


def test_maine_leaves_undescribed_codes_to_a_person():
    found = me.codes("Auburn", parcels(["01010"], [(5411, "1", "0"), (704, "2", "0")]), NETWORK)
    assert found["finance"] == {"mrs_municipality": "Auburn"}
    assert '"1" (5,411)' in found["finance_todo"] and "budget_documents_url" in found["finance_todo"]
    assert me.codes("Auburn", Answers(), NETWORK)["notes"]  # the table unreachable: said, not guessed


# ---- Rhode Island: the saved tables and RIDE's data files ----

def test_rhode_island_finds_the_dmf_name_and_ride_code():
    found = ri.codes("South Kingstown", FakeRIDE(), NETWORK)
    assert found["finance"] == {"dmf_municipality": "South Kingstown"}
    assert found["schools"] == {"ride_district": 32, "district_name": "South Kingstown Public Schools"}


def test_rhode_island_leaves_a_district_it_cannot_find_to_a_person():
    found = ri.codes("Charlestown", FakeRIDE(), NETWORK)
    assert found["finance"] == {"dmf_municipality": "Charlestown"} and "schools" not in found
    assert "ride_district" in found["schools_todo"]


# ---- Vermont: the saved files and the Education Dashboard ----

DASHBOARD = [{"supervisoryunionidentifier": "SU015", "supervisoryunionname": "BURLINGTON SD"},
             {"supervisoryunionidentifier": "SU016", "supervisoryunionname": "SOUTH BURLINGTON SD"},
             {"supervisoryunionidentifier": "SU040", "supervisoryunionname": "RUTLAND CITY SD"},
             {"supervisoryunionidentifier": "SU045", "supervisoryunionname": "MONTPELIER SD"},
             {"supervisoryunionidentifier": "SU069", "supervisoryunionname": "MONTPELIER ROXBURY SD"}]


def test_vermont_finds_the_pvr_name_and_both_district_codes():
    found = vt.codes("Burlington", Answers(("data.vermont.gov", DASHBOARD)), NETWORK)
    assert found["finance"] == {"pvr_town": "Burlington"}
    assert found["schools"] == {"aoe_org": "SU015", "aoe_lea": "T037", "district_name": "Burlington School District"}
    found = vt.codes("Rutland", Answers(("data.vermont.gov", DASHBOARD)), NETWORK)
    assert found["finance"] == {"pvr_town": "Rutland City"} and found["schools"]["aoe_org"] == "SU040"


def test_vermont_lists_unions_for_a_person_when_a_code_is_missing():
    found = vt.codes("Montpelier", Answers(("data.vermont.gov", DASHBOARD)), NETWORK)
    assert "schools" not in found
    assert "SU069" in found["schools_todo"] and "aoe_org = 'SU045'" in found["schools_todo"]


# ---- Connecticut: OPM's mill rates and EdSight ----

def edsight(name: str) -> str:
    return (FIXTURES / "edsight" / name).read_text()


def test_connecticut_finds_the_opm_code_and_confirms_the_district():
    client = Answers((opendata.MILL_RATES, [{"municipality": "Wallingford", "town_code": "148"}]),
                     ("Wallingford+School+District", edsight("graduation-district.csv")))
    found = ct.codes("Wallingford", client, NETWORK)
    assert found["finance"] == {"opm_town": "Wallingford", "opm_code": 148}
    assert found["schools"] == {"edsight_district": "Wallingford School District",
                                "district_name": "Wallingford Public Schools"}


def test_connecticut_leaves_a_district_edsight_does_not_answer_for_to_a_person():
    client = Answers((opendata.MILL_RATES, []), ("edsight", edsight("spending-no-results.csv")))
    found = ct.codes("Woodbridge", client, NETWORK)
    assert "finance" not in found and "schools" not in found
    assert "Woodbridge School District" in found["schools_todo"]


# ---- Ward files from statewide layers, with census block population ----

def square(x, y, size=1.0):
    return [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]


def blocks(*points):
    """TIGERweb's answer: (lon, lat, population) for each block's internal point."""
    return {"features": [{"attributes": {"INTPTLON": str(x), "INTPTLAT": str(y), "POP100": n}} for x, y, n in points]}


def test_new_hampshire_wards_are_numbered_merged_and_counted():
    layer = {"features": [
        {"properties": {"district": "Somersworth Ward 2"}, "geometry": {"type": "Polygon", "coordinates": square(1, 0)}},
        {"properties": {"district": "Somersworth Ward 10"}, "geometry": {"type": "Polygon", "coordinates": square(3, 0)}},
        {"properties": {"district": "Somersworth Ward 2"}, "geometry": {"type": "Polygon", "coordinates": square(5, 0)}},
        {"properties": {"district": "Somersworth- entire"}, "geometry": {"type": "Polygon", "coordinates": square(9, 9, 0.1)}},
        {"properties": {"district": "Somersworth Ward 1"},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 0.0000001], [1, 1], [0, 1], [0, 0]]]}}]}
    client = Answers(("FeatureServer/11", layer), ("tigerweb", blocks((0.5, 0.5, 10), (1.5, 0.5, 20), (5.5, 0.5, 5),
                                                                       (3.5, 0.5, 7), (8, 8, 100))))
    made = nh.wards("Somersworth", client)
    data = json.loads(made["text"])
    assert [f["properties"]["ward"] for f in data["features"]] == ["1", "2", "10"]
    assert [f["properties"]["population_2020"] for f in data["features"]] == [10, 25, 7]
    # Ward 2's two pieces are one MultiPolygon; a point repeated after rounding is dropped.
    assert data["features"][1]["geometry"]["type"] == "MultiPolygon"
    assert len(data["features"][0]["geometry"]["coordinates"][0]) == 5
    assert data["name"] == "Somersworth wards (2022)" and made["publisher"] == "NH GRANIT"
    assert "name = 'Somersworth'" in parse_qs(urlparse(client.urls[0]).query)["where"][0]


def test_a_new_hampshire_town_without_wards_has_no_ward_file():
    layer = {"features": [{"properties": {"district": "Derry- entire"}, "geometry": {"type": "Polygon", "coordinates": square(0, 0)}}]}
    assert nh.wards("Derry", Answers(("FeatureServer/11", layer))) is None


def ri_precinct(code, ward, x):
    return {"properties": {"DISTRICT": code, "Ward": ward}, "geometry": {"type": "Polygon", "coordinates": square(x, 0)}}


def test_rhode_island_precincts_with_and_without_wards():
    client = Answers(("Voting_Precincts", {"features": [ri_precinct("3202", 0, 1), ri_precinct("3201", 0, 0)]}),
                     ("tigerweb", blocks((0.5, 0.5, 3), (1.5, 0.5, 4))))
    made = ri.wards("South Kingstown", client)
    data = json.loads(made["text"])
    assert not made["wards"] and data["name"] == "South Kingstown voting precincts (2022)"
    assert [f["properties"] for f in data["features"]] == [
        {"ward": "3201", "precinct": "3201", "district": "3201", "population_2020": 3},
        {"ward": "3202", "precinct": "3202", "district": "3202", "population_2020": 4}]

    client = Answers(("Voting_Precincts", {"features": [ri_precinct("3602", 2, 1), ri_precinct("3601", 1, 0),
                                                        ri_precinct("28XX", 0, 5)]}),
                     ("tigerweb", blocks((0.5, 0.5, 3), (1.5, 0.5, 4))))
    made = ri.wards("Warwick", client)
    data = json.loads(made["text"])
    assert made["wards"] and data["name"] == "Warwick wards and precincts (2022)"
    assert [(f["properties"]["ward"], f["properties"]["precinct"]) for f in data["features"]] == [("1", "3601"), ("2", "3602")]
