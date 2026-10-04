"""Vermont: the yearly statewide extract, the town sources that read it and the state's open data, and the pages
they fill."""

import copy
import json
import shutil
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import BUILT_AT, FIXTURES
from fakes import FakeJSONResponse
from test_site import parse
from test_site import test_internal_links_resolve as check_links

from pipeline import build_site, fetch_budget, fetch_finance, fetch_schools, rhythms, states
from pipeline.config import load_config
from pipeline.states.vt import extract, figures
from pipeline.states.vt import schools as vt_schools
from pipeline.states.vt import tax_bill as vt_tax_bill

VT = FIXTURES / "vt"
NOW = datetime(2026, 10, 4, 8, tzinfo=ZoneInfo("America/New_York"))
# Trimmed from the state's files: a few towns each, Burlington among them.
WORKBOOKS = ["2025-tax-rates-taxes-and-tax-rates-by-county.xlsx", "TaxRates2023.xlsx",
             "2025-education-and-municipal-listed-and-equalized-grand-list-by-town.xlsx",
             "edu-fy26-cohort-spending-by-school-type.xlsx"]
# VCGI's parcel data and data.vermont.gov's answers for Burlington, recorded on 2026-10-04.
ANSWERS = json.loads((VT / "answers.json").read_text())


def run_extract(monkeypatch, folder, files):
    monkeypatch.setattr(extract, "FIGURES_DIR", folder)
    monkeypatch.setattr("sys.argv", ["extract", *(str(VT / f) for f in files)])
    figures.load.cache_clear()
    assert extract.main() == 0
    figures.load.cache_clear()


@pytest.fixture
def vt_figures(tmp_path, monkeypatch):
    """The fixture files' figures, in place of the engine's."""
    run_extract(monkeypatch, tmp_path / "figures", WORKBOOKS + ["sub-est2025_50.csv"])
    yield tmp_path / "figures"
    figures.load.cache_clear()


def burlington() -> dict:
    """Gloucester's fixture config, moved to Burlington, Vermont."""
    config = copy.deepcopy(load_config("gloucester"))
    config["town"].update(name="Burlington", state="Vermont", state_abbr="VT")
    config["finance"] = {"pvr_town": "Burlington"}
    config["schools"] = {"aoe_org": "SU015", "aoe_lea": "T037", "district_name": "Burlington School District"}
    return config


class FakeVermont:
    """The parcel data and data.vermont.gov, from the recorded answers. scale multiplies the parcel data's
    homestead grand list, standing in for parcel data from another year than the PVR figures'."""

    def __init__(self, scale=1.0):
        self.scale, self.urls = scale, []

    def get(self, url):
        self.urls.append(url)
        data = copy.deepcopy(ANSWERS[url])
        if self.scale != 1.0 and "homestead_list" in url:
            data["features"][0]["attributes"]["homestead_list"] *= self.scale
        return FakeJSONResponse(data)


# ---- The extract ----

def test_extract_reads_each_kind_of_workbook(vt_figures):
    tax = json.loads((vt_figures / "tax.json").read_text())["years"]
    b25 = tax["2025"]["Burlington"]
    assert (b25["homestead_rate"], b25["nonhomestead_rate"], b25["municipal_rate"], b25["local_agreement_rate"]) == \
        (1.5264, 1.5072, 0.8557, 0.0004)
    assert b25["homestead_grand_list"] == 23631453.13 and b25["municipal_taxes"] == 50322985.52
    # Spreadsheet floats are kept as the state wrote them.
    assert tax["2025"]["Castleton"]["municipal_rate"] == 0.3796
    # 2023's headers differ ("Homestead Education GL", "Municipal Actual Tax Rate").
    assert tax["2023"]["Burlington"]["homestead_grand_list"] == 23018370 and tax["2023"]["Burlington"]["municipal_rate"] == 0.7523
    grand = json.loads((vt_figures / "grand_list.json").read_text())["years"]["2025"]["Burlington"]
    assert grand["cla"] == 76.25 and grand["parcels"] == 10451 and grand["equalized_value"] == 7545223000
    spending = json.loads((vt_figures / "spending.json").read_text())["years"]["2026"]
    assert spending["T037"] == {"name": "Burlington", "pupils": 6972.03, "budget": 19331.5, "education_spending": 14825.07}
    assert spending["State"] == {"name": "State of Vermont", "budget": 16650.0, "education_spending": 13947.0}


def test_a_towns_districts_are_left_out(vt_figures):
    # Castleton's two fire districts have rows of their own, with only their own rates.
    castleton = json.loads((vt_figures / "tax.json").read_text())["years"]["2025"]["Castleton"]
    assert castleton["municipal_taxes"] == 3174009.78


def test_population_is_matched_to_pvrs_names(vt_figures):
    people = json.loads((vt_figures / "population.json").read_text())["years"]["2025"]
    assert people["Burlington"] == 44019 and people["Rutland City"] and people["Rutland Town"]
    assert people["Essex Jct."], "the Census's Essex Junction city"
    assert people["Rutland City"] != people["Rutland Town"]


def test_extract_refuses_a_file_it_doesnt_know(tmp_path, monkeypatch):
    # An older layout: the 2022 grand list's database-style headers.
    monkeypatch.setattr(extract, "FIGURES_DIR", tmp_path / "figures")
    monkeypatch.setattr("sys.argv", ["extract", str(VT / "CLA_COD_EEGL_2022.xlsx")])
    with pytest.raises(SystemExit, match="not a workbook this command knows"):
        extract.main()


def test_the_state_pages_lead_to_the_workbooks():
    pages = {
        extract.ANNUAL_REPORT: '<a href="/data-and-statistics/pvr-annual-report-data/2025">2025 Supplemental Data</a>',
        "https://tax.vermont.gov/data-and-statistics/pvr-annual-report-data/2025":
            '<a href="/document/rates">Tax Rates: Taxes and Tax Rates by County</a>'
            '<a href="/document/rates-pdf">Tax Rates: Taxes and Tax Rates by County (PDF)</a>'
            '<a href="/document/gl">Education and Municipal Listed and Equalized Grand List by Town</a>'
            '<a href="/document/other">Current Use Acreage Enrolled by Year</a>',
        "https://tax.vermont.gov/document/rates": '<a href="/sites/tax/files/documents/rates.xlsx">x</a>',
        "https://tax.vermont.gov/document/gl": '<a href="/sites/tax/files/documents/B-2.pdf">form</a>',
        extract.SPENDING_PAGE: '<a href="https://education.vermont.gov/documents/fy26">FY 2026 Report</a>'
                               '<a href="https://education.vermont.gov/document/fy24">FY 2024 Report</a>',
        "https://education.vermont.gov/documents/fy26": '<a href="/sites/aoe/files/documents/fy26.xlsx">x</a>',
    }

    class Pages:
        def get(self, url):
            return type("Page", (), {"text": pages[url]})()

    # The grand list's page offers no workbook (only a PDF), and fiscal 2024 is before the pupil count changed.
    assert extract.state_files(Pages()) == ["https://tax.vermont.gov/sites/tax/files/documents/rates.xlsx",
                                           "https://education.vermont.gov/sites/aoe/files/documents/fy26.xlsx"]


def test_the_engines_figures_cover_burlington():
    figures.load.cache_clear()
    assert figures.rows("tax", "Burlington")[-1][1]["homestead_rate"]
    assert figures.rows("grand_list", "Burlington") and figures.rows("population", "Burlington")
    assert figures.rows("spending", "T037")


# ---- The town's sources ----

def test_a_vermont_town_gets_vermonts_sources():
    config = burlington()
    states.check(config)
    assert states.for_town(config).source("tax_bill", config).module == "pipeline.states.vt.tax_bill"
    del config["schools"]["aoe_lea"]
    with pytest.raises(SystemExit, match="needs aoe_lea for Vermont's school figures"):
        states.check(config)


def test_tax_bill_is_calculated_and_says_how(vt_figures, tmp_path):
    result = fetch_finance.run(burlington(), FakeVermont(), tmp_path, now=NOW)
    assert result == {"tax_year": 2025, "value_ratio": 1.0001, "average_bill": 10043}
    year = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())["years"][-1]
    assert (year["average_value"], year["parcels"], year["rate"]) == (421593, 5674, 2.3825)
    assert year["calculated"] == vt_tax_bill.method(2025, 5674) and "5,674 homesteads" in year["calculated"]


def test_tax_bill_is_kept_when_the_parcel_data_isnt_pvrs_year(vt_figures, tmp_path, capsys):
    fetch_finance.run(burlington(), FakeVermont(), tmp_path, now=NOW)
    before = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())["years"]
    result = fetch_finance.run(burlington(), FakeVermont(scale=1.2), tmp_path, now=NOW)
    assert "average_bill" not in result and result["kept"] == [2025]
    assert json.loads((tmp_path / "finance" / "tax_bill.json").read_text())["years"] == before
    assert "Kept the saved tax bill" in capsys.readouterr().out


def test_budget_figures(vt_figures, tmp_path):
    result = fetch_budget.run(burlington(), None, tmp_path, now=NOW)
    assert result == {"rates": 2025, "per_resident": round(135905023.25 / 44019)}
    b = json.loads((tmp_path / "finance" / "budget.json").read_text())
    assert [r["tax_year"] for r in b["rates"]] == [2023, 2025]
    # The median of the fixture's towns; a gore with no homesteads has no homestead rate that counts.
    towns = json.loads((vt_figures / "tax.json").read_text())["years"]["2025"].values()
    assert b["rates"][-1]["state_median"]["municipal_rate"] == round(statistics.median(t["municipal_rate"] for t in towns), 4)
    assert b["rates"][-1]["state_median"]["homestead_rate"] == \
        round(statistics.median(t["homestead_rate"] for t in towns if t["homestead_rate"]), 4)
    assert b["taxes"][-1]["total"] == 135905023 and b["taxes"][-1]["parts"]["City tax"] == 50322986
    assert b["grand_list"][-1] == {"tax_year": 2025, "listed_value": 5880914517, "cla": 76.25,
                                   "equalized_value": 7545223000.0, "parcels": 10451}
    pr = b["per_resident"]
    assert pr["population"] == 44019 and "July 1, 2025" in pr["calculated"] and pr["communities"] >= 6


def test_school_figures(vt_figures, tmp_path):
    latest = fetch_schools.run(burlington(), FakeVermont(), tmp_path, now=NOW)
    assert latest["graduation"] == {"year": 2025, "town": 71.2, "state": 82.3}
    assert latest["absenteeism"] == {"year": 2025, "town": 26.0, "state": 25.2}
    assert latest["tests_ela"] == {"year": 2025, "town": 55.9, "state": 54.6}
    assert latest["budget_per_pupil"] == {"year": 2026, "town": 19332, "state": 16650}
    assert latest["education_spending_per_pupil"] == {"year": 2026, "town": 14825, "state": 13947}


def test_test_results_are_weighted_by_students_tested():
    rows = [{"testname": "Math Grade 03", "indicatorlabel": "Number of Students Tested", "value_w_susd": "100"},
            {"testname": "Math Grade 03", "indicatorlabel": "Total Proficient and Above", "value_w_susd": "0.5"},
            {"testname": "Math Grade 04", "indicatorlabel": "Number of Students Tested", "value_w_susd": "300"},
            {"testname": "Math Grade 04", "indicatorlabel": "Total Proficient and Above", "value_w_susd": "0.3"},
            {"testname": "Math Grade 05", "indicatorlabel": "Number of Students Tested", "value_w_susd": "***"}]
    assert vt_schools.proficiency(rows, "Math Grade", "value_w_susd") == 35.0
    assert vt_schools.proficiency(rows, "English Language Arts Grade", "value_w_susd") is None


def test_saved_test_years_are_kept(vt_figures, tmp_path):
    config = burlington()
    fetch_schools.run(config, FakeVermont(), tmp_path, now=NOW)
    path = tmp_path / "schools" / "schools.json"
    saved = json.loads(path.read_text())
    saved["measures"]["tests_ela"]["years"].insert(0, {"year": 2024, "town": 50.0, "state": 51.0})
    path.write_text(json.dumps(saved))
    fetch_schools.run(config, FakeVermont(), tmp_path, now=NOW)
    assert [y["year"] for y in json.loads(path.read_text())["measures"]["tests_ela"]["years"]] == [2024, 2025]


def test_a_town_in_vermont_has_vermonts_rhythms():
    labels = [r.label for r in rhythms.for_town(burlington())]
    assert {"Tax bill (calculated)", "City budget", "School figures"} <= set(labels)


# ---- The site ----

@pytest.fixture
def vt_site(vt_figures, tmp_path, data_dir, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    for name in ("finance", "schools"):
        shutil.rmtree(data / name, ignore_errors=True)
    config = burlington()
    fetch_finance.run(config, FakeVermont(), data, now=NOW)
    fetch_budget.run(config, None, data, now=NOW)
    fetch_schools.run(config, FakeVermont(), data, now=NOW)
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    return out


def test_vermont_pages(vt_site):
    budget = (vt_site / "budget" / "index.html").read_text()
    assert "Property Valuation and Review" in budget and "Division of Local Services" not in budget
    assert "$1.5264" in budget and "$10,043" in budget and "76.25%" in budget and "5,674 homesteads" in budget
    for name in ("tax-rates", "taxes-raised", "grand-list"):
        assert (vt_site / "budget" / "data" / f"{name}.csv").read_text().startswith("tax_year,")
    schools = (vt_site / "schools" / "index.html").read_text()
    assert "VTCAP" in schools and "MCAS" in schools and "71.2%" in schools and "$19,332" in schools
    assert "Agency of Education" in schools and "Statewide Assessment System" not in schools
    home = (vt_site / "index.html").read_text()
    assert "Tax year 2025 · Calculated by Publick from state figures" in home and 'href="/budget/#tax-bill"' in home
    about = (vt_site / "about" / "index.html").read_text()
    assert "Property Valuation and Review" in about and "VCGI" in about and "VTCAP" in about


def test_every_calculated_figure_says_so_on_its_page(vt_site):
    data = vt_site.parent / "data"
    budget = (vt_site / "budget" / "index.html").read_text()
    marked = [json.loads((data / "finance" / "tax_bill.json").read_text())["years"][-1]["calculated"],
              json.loads((data / "finance" / "budget.json").read_text())["per_resident"]["calculated"]]
    for how in marked:
        assert how.replace("'", "&#39;") in budget, how
    assert budget.count("Calculated by Publick.</strong>") == len(marked)
    schools = (vt_site / "schools" / "index.html").read_text()
    measures = json.loads((data / "schools" / "schools.json").read_text())["measures"]
    for name in ("absenteeism", "tests_ela"):
        assert measures[name]["calculated"].replace("'", "&#39;") in schools, name
    assert schools.count("Calculated by Publick.</strong>") == 2


def test_vermont_links_resolve(vt_site):
    check_links(vt_site, sorted(vt_site.rglob("*.html")))
    for path in (vt_site / "budget" / "index.html", vt_site / "schools" / "index.html"):
        assert parse(path).tags.count("h1") == 1, path
