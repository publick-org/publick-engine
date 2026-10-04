"""Connecticut: the tax bill and budget from OPM's datasets on data.ct.gov, school figures from
EdSight's exports, and the pages they fill."""

import copy
import re
import json
import shutil
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from conftest import BUILT_AT, FIXTURES
from fakes import FakeJSONResponse, FakeResponse
from test_site import parse
from test_site import test_internal_links_resolve as check_links

from pipeline import build_site, fetch_budget, fetch_finance, fetch_schools, rhythms, states
from pipeline.config import load_config
from pipeline.http import FetchError
from pipeline.states.ct import budget as ct_budget
from pipeline.states.ct import opendata
from pipeline.states.ct import schools as ct_schools
from pipeline.states.ct import tax_bill as ct_tax_bill

EDSIGHT = FIXTURES / "edsight"
# data.ct.gov's answers to the package's queries for Wallingford, recorded on 2026-10-04.
OPEN_DATA = json.loads((FIXTURES / "ct" / "opendata.json").read_text())
NOW = datetime(2026, 10, 1, 8, tzinfo=ZoneInfo("America/New_York"))
DISTRICT = "Wallingford School District"
PROGRAMS = {"GraduationExport": "graduation", "ChronicAbsenteeismExport": "absenteeism",
            "SmarterBalancedAssessmentExport": "tests"}


def wallingford() -> dict:
    """Gloucester's fixture config, moved to Wallingford, Connecticut."""
    config = copy.deepcopy(load_config("gloucester"))
    config["town"].update(name="Wallingford", state="Connecticut", state_abbr="CT")
    config["finance"] = {"opm_town": "Wallingford", "opm_code": 148, "single_family_use": ["1010"]}
    config["schools"] = {"edsight_district": DISTRICT, "district_name": "Wallingford Public Schools"}
    return config


class FakeOpenData:
    """data.ct.gov and its catalog, from the recorded answers. scale multiplies the 2026 parcel file's values,
    standing in for a file from another year than its grand list."""

    def __init__(self, scale=1.0):
        self.scale, self.urls = scale, []

    def get(self, url):
        self.urls.append(url)
        data = copy.deepcopy(OPEN_DATA[url])
        if self.scale != 1.0 and "/ibe8-9i3q.json" in url:
            for row in data:
                row["total"] = str(float(row["total"]) * self.scale)
                row["average"] = str(float(row["average"]) * self.scale)
        return FakeJSONResponse(data)


class FakeEdSight:
    """EdSight's exports: the saved trend files for the district and the state, and
    spending for 2024-25 only (every other year has no results)."""

    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        program = urlparse(url).query.split("&", 1)[0].rsplit("/", 1)[1]
        query = parse_qs(urlparse(url).query)
        place = "district" if query["_district"] == [DISTRICT] else "state"
        if program == "EFSDistrictLevelbyFunctionExport":
            name = f"spending-2024-25-{place}.csv" if query["_year"] == ["2024-25"] else "spending-no-results.csv"
        else:
            assert query["_year"] == ["Trend"]
            name = f"{PROGRAMS[program]}-{place}.csv"
        return FakeResponse((EDSIGHT / name).read_bytes())


# ---- The exports ----

def test_trend_exports_for_the_district_and_the_state():
    # The district's files have a District Code column; the state's don't, and one starts "Organization".
    for place in ("district", "state"):
        grad = ct_schools.parse_trend((EDSIGHT / f"graduation-{place}.csv").read_text())
        absent = ct_schools.parse_trend((EDSIGHT / f"absenteeism-{place}.csv").read_text())
        tests = ct_schools.parse_trend((EDSIGHT / f"tests-{place}.csv").read_text())
        assert sorted(grad[None]) == [2021, 2022, 2023, 2024, 2025]
        assert sorted(absent[None]) == sorted(tests["ELA"]) == sorted(tests["Math"]) == [2022, 2023, 2024, 2025, 2026]
    district = ct_schools.parse_trend((EDSIGHT / "tests-district.csv").read_text())
    assert district["ELA"][2026] == 54.7 and district["Math"][2026] == 50.0
    # The counts beside each percentage aren't read as figures.
    assert ct_schools.parse_trend((EDSIGHT / "absenteeism-district.csv").read_text())[None][2026] == 11.4


def test_spending_export():
    assert ct_schools.parse_spending((EDSIGHT / "spending-2024-25-district.csv").read_text()) == 24427
    assert ct_schools.parse_spending((EDSIGHT / "spending-2024-25-state.csv").read_text()) == 22721
    assert ct_schools.parse_spending((EDSIGHT / "spending-no-results.csv").read_text()) is None


def test_the_sign_in_page_is_an_error():
    # What EdSight answers when the session doesn't keep its cookies.
    page = '<html class="bg"><head><title>SAS&reg; Logon Manager</title></head></html>'
    with pytest.raises(FetchError, match="sign-in page"):
        ct_schools.parse_trend(page)


def test_export_addresses():
    url = ct_schools.export_url("GraduationExport", DISTRICT, ct_schools.TRENDS["graduation"][1])
    assert url == ("https://edsight.ct.gov/SASStoredProcess/guest?_program=/CTDOE/EdSight/Release/Reporting/Public/"
                   "Reports/StoredProcesses/GraduationExport&_year=Trend&_district=Wallingford+School+District"
                   "&_subgroup=All+Students&_school=+&_rate=+&_gradcat=grad")
    assert ct_schools.spending_url("State of Connecticut", 2025).endswith(
        "EFSDistrictLevelbyFunctionExport&_year=2024-25&_district=State+of+Connecticut")


# ---- The source ----

def test_school_figures(tmp_path):
    client = FakeEdSight()
    latest = fetch_schools.run(wallingford(), client, tmp_path, now=NOW)
    assert latest["graduation"] == {"year": 2025, "town": 92.5, "state": 91.2}
    assert latest["absenteeism"] == {"year": 2026, "town": 11.4, "state": 16.4}
    assert latest["tests_ela"] == {"year": 2026, "town": 54.7, "state": 51.0}
    assert latest["spending"] == {"year": 2025, "town": 24427, "state": 22721}
    saved = json.loads((tmp_path / "schools" / "schools.json").read_text())
    assert saved["district"] == "Wallingford Public Schools"
    # Three trend exports for each place, then spending from 2018 through this year: a state
    # request only for a year the district has.
    assert len(client.urls) == 6 + 9 + 1


def test_saved_years_are_kept_and_not_asked_for_again(tmp_path):
    config, client = wallingford(), FakeEdSight()
    fetch_schools.run(config, client, tmp_path, now=NOW)
    path = tmp_path / "schools" / "schools.json"
    saved = json.loads(path.read_text())
    # A year the trend exports no longer cover stays on the page.
    saved["measures"]["graduation"]["years"].insert(0, {"year": 2020, "town": 93.0, "state": 88.0})
    path.write_text(json.dumps(saved))
    client.urls.clear()
    fetch_schools.run(config, client, tmp_path, now=NOW)
    years = json.loads(path.read_text())["measures"]["graduation"]["years"]
    assert [y["year"] for y in years] == [2020, 2021, 2022, 2023, 2024, 2025]
    # 2024-25 spending is saved, so it isn't asked for again.
    assert not any("_year=2024-25" in url for url in client.urls)


def test_a_town_in_connecticut_has_the_school_rhythm():
    assert states.for_town(wallingford()).name == "Connecticut"
    assert "School figures (EdSight)" in [r.label for r in rhythms.for_town(wallingford())]


# ---- The tax bill and budget ----

def test_a_connecticut_town_gets_connecticuts_finance_sources():
    config = wallingford()
    states.check(config)
    assert states.for_town(config).source("tax_bill", config).module == "pipeline.states.ct.tax_bill"
    del config["finance"]["opm_code"]
    with pytest.raises(SystemExit, match="needs opm_code for Connecticut's"):
        states.check(config)


def test_mill_rates_are_the_towns_own():
    # OPM's rate column changed in fiscal year 2021; a blank or zero rate is a missing figure.
    assert ct_tax_bill.mill_rate({"mill_rate": "29.19"}) == 29.19
    assert ct_tax_bill.mill_rate({"mill_rate_real_personal": "24.57", "mill_rate": ""}) == 24.57
    assert ct_tax_bill.mill_rate({"mill_rate_real_personal": "0.026"}) is None
    assert ct_tax_bill.mill_rate({}) is None


def test_the_parcel_files_are_found_in_the_catalog():
    files = opendata.cama_datasets(FakeOpenData())
    assert files == {2024: "pqrn-qghw", 2025: "rny9-6ak2", 2026: "ibe8-9i3q"}


def test_tax_bill_is_calculated_and_says_how(tmp_path):
    result = fetch_finance.run(wallingford(), FakeOpenData(), tmp_path, now=NOW)
    assert result["latest"] == 2027 and result["average_bill"] == 7414
    assert result["value_ratios"] == {2025: 1.185, 2026: 1.155, 2027: 1.16}
    saved = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())
    assert [(y["fiscal_year"], y["average_bill"]) for y in saved["years"]] == [(2025, 6392), (2026, 7253), (2027, 7414)]
    year = saved["years"][-1]
    assert (year["average_value"], year["parcels"], year["rate"], year["parcel_file"]) == (301753, 10492, 24.57, 2026)
    assert year["calculated"] == ct_tax_bill.method(2027, 10492) and "10,492 single-family homes" in year["calculated"]
    assert saved["parcels_url"] == "https://data.ct.gov/d/ibe8-9i3q"


def test_a_parcel_file_that_doesnt_match_its_grand_list_is_left_out(tmp_path, capsys):
    result = fetch_finance.run(wallingford(), FakeOpenData(scale=1.5), tmp_path, now=NOW)
    assert result["left_out"] == [2027] and result["latest"] == 2026
    assert "fiscal year 2027 left out" in capsys.readouterr().out


def test_saved_years_are_kept_and_not_asked_for_again(tmp_path):
    client = FakeOpenData()
    fetch_finance.run(wallingford(), client, tmp_path, now=NOW)
    client.urls.clear()
    fetch_finance.run(wallingford(), client, tmp_path, now=NOW)
    # Only the newest saved year's file is asked for again.
    assert not any("/pqrn-qghw.json" in u or "/rny9-6ak2.json" in u for u in client.urls)
    assert any("/ibe8-9i3q.json" in u for u in client.urls)


def test_budget_figures(tmp_path):
    result = fetch_budget.run(wallingford(), FakeOpenData(), tmp_path, now=NOW)
    assert result == {"rates": 2027, "adopted": 2026, "per_resident": 3052}
    b = json.loads((tmp_path / "finance" / "budget.json").read_text())
    assert b["rates"][-1] == {"fiscal_year": 2027, "rate": 24.57, "state_median": 27.47, "towns": 168}
    # OPM's file has every town's rate in one column only from fiscal year 2021, so earlier years have no median.
    assert [r["state_median"] for r in b["rates"] if r["fiscal_year"] < 2021] == [None] * 3
    fy26 = b["adopted"][-1]
    assert fy26["total"] == 204050153 and fy26["spending"]["Education"] == 121722102 and fy26["adopted_on"] == "2025-05-13"
    assert fy26["revenue"]["Property tax"] == 147972729
    assert b["levy"][-1]["total"] == 150279563 and b["grand_list"][-1]["total"] == 6131825502
    pr = b["per_resident"]
    assert (pr["fiscal_year"], pr["town"], pr["state_median"], pr["communities"]) == (2023, 3052, 3308, 169)


def test_a_town_in_connecticut_has_the_finance_rhythms():
    labels = [r.label for r in rhythms.for_town(wallingford())]
    assert "Tax bill (calculated)" in labels and "Town budget" in labels


# ---- The site ----

@pytest.fixture
def ct_site(tmp_path, data_dir, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    for name in ("finance", "schools"):
        shutil.rmtree(data / name, ignore_errors=True)
    config = wallingford()
    fetch_finance.run(config, FakeOpenData(), data, now=NOW)
    fetch_budget.run(config, FakeOpenData(), data, now=NOW)
    fetch_schools.run(config, FakeEdSight(), data, now=NOW)
    # The Census Bureau names Wallingford a town (pipeline/fetch_place.py).
    (data / "place.json").write_text('{"name": "Wallingford town", "lsad": "43", "kind": "town"}\n')
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    return out


def test_connecticut_schools_page(ct_site):
    schools = (ct_site / "schools" / "index.html").read_text()
    assert "Connecticut State Department of Education" in schools and "Smarter Balanced" in schools
    assert "54.7%" in schools and "$24,427" in schools and "2024–25" in schools and "DESE" not in schools
    assert parse(ct_site / "schools" / "index.html").tags.count("h1") == 1
    about = (ct_site / "about" / "index.html").read_text()
    assert "EdSight" in about and "Elementary and Secondary Education" not in about


def test_connecticut_budget_page(ct_site):
    budget = (ct_site / "budget" / "index.html").read_text()
    assert "Office of Policy and Management" in budget and "Division of Local Services" not in budget
    assert "24.57" in budget and "27.47" in budget and "$7,414" in budget and "$204.1M" in budget
    assert "Calculated by Publick." in budget and "10,492 single-family homes" in budget
    assert parse(ct_site / "budget" / "index.html").tags.count("h1") == 1
    for name in ("mill-rates", "adopted-budget", "tax-levy", "grand-list"):
        assert (ct_site / "budget" / "data" / f"{name}.csv").read_text().startswith(("fiscal_year,", "grand_list_year,"))
    home = (ct_site / "index.html").read_text()
    assert "Fiscal year 2027 · Calculated by Publick from state figures" in home and 'href="/budget/#tax-bill"' in home
    about = (ct_site / "about" / "index.html").read_text()
    assert "Office of Policy and Management" in about and "Parcel and CAMA" in about


def test_every_calculated_figure_says_so_on_its_page(ct_site):
    """The newest tax bill's method and property tax per resident's, under "Calculated by Publick"."""
    data = ct_site.parent / "data"
    marked = [json.loads((data / "finance" / "tax_bill.json").read_text())["years"][-1]["calculated"],
              json.loads((data / "finance" / "budget.json").read_text())["per_resident"]["calculated"],
              json.loads((data / "finance" / "budget.json").read_text())["rates_median_calculated"]]
    budget = (ct_site / "budget" / "index.html").read_text()
    for how in marked:
        assert how.replace("'", "&#39;") in budget, how
    assert budget.count("Calculated by Publick.</strong>") == len(marked)


def test_connecticut_links_resolve(ct_site):
    check_links(ct_site, sorted(ct_site.rglob("*.html")))


def test_a_town_is_called_a_town(ct_site):
    """The site's wording says "town" for a town (in Spanish too: tests/test_wording.py)."""
    # The site's own wording; the test town's config text and its agendas' summaries, written
    # about Gloucester, say "city" as they were written.
    for page in ("about/index.html", "meetings/index.html"):
        text = re.sub(r"<[^>]+>", " ", (ct_site / page).read_text())
        assert not re.search(r"\bCity of\b|\bcity calendar\b|\bcity's\b|\bla ciudad\b|\bCiudad de\b", text), page
    about = (ct_site / "about" / "index.html").read_text()
    assert "the Town of Wallingford" in about
