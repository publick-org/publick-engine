"""New Hampshire: the yearly statewide extract, the town sources that read it, and the pages they fill."""

import copy
import json
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import BUILT_AT, FIXTURES
from fakes import FakeJSONResponse, FakeResponse
from test_site import parse
from test_site import test_internal_links_resolve as check_links

from pipeline import build_site, fetch_budget, fetch_finance, fetch_schools, states
from pipeline.config import load_config
from pipeline.states.nh import budget as nh_budget
from pipeline.states.nh import extract, figures
from pipeline.states.nh import tax_bill as nh_tax_bill

NH = FIXTURES / "nh"
NOW = datetime(2026, 9, 29, 8, tzinfo=ZoneInfo("America/New_York"))
DOWNLOADS = ["2021-municipal-and-village-tax-rates.xlsx", "2024-municipal-and-village-tax-rates.xlsx",
             "2025-municipal-and-village-district-tax-rates.xlsx", "2025-tax-rate-calculation-data.xlsx",
             "cohort2024.csv", "cohort2025.csv", "assess2019.csv", "assess2026.csv", "cpp2025.csv",
             "sub-est2025_33.csv"]


def run_extract(monkeypatch, folder, files=DOWNLOADS):
    monkeypatch.setattr(extract, "FIGURES_DIR", folder)
    monkeypatch.setattr("sys.argv", ["extract", *(str(NH / f) for f in files)])
    figures.load.cache_clear()
    assert extract.main() == 0
    figures.load.cache_clear()


@pytest.fixture
def nh_figures(tmp_path, monkeypatch):
    """The fixture files' figures, in place of the engine's."""
    run_extract(monkeypatch, tmp_path / "figures")
    yield tmp_path / "figures"
    figures.load.cache_clear()


def manchester() -> dict:
    """Gloucester's fixture config, moved to Manchester, New Hampshire."""
    config = copy.deepcopy(load_config("gloucester"))
    config["town"].update(name="Manchester", state="New Hampshire", state_abbr="NH")
    config["finance"] = {"dra_municipality": "Manchester",
                         "budget_table_url": "https://www.manchesternh.gov/Departments/Finance/Budget-Info",
                         "budget_table_name": "the city's Budget Info page"}
    config["schools"] = {"doe_district": "Manchester", "district_name": "Manchester School District"}
    return config


class FakeParcels:
    """The parcel map's statistics: every parcel in the town, or its single-family homes. scale stands in
    for a revaluation the DRA's figures don't have yet."""

    def __init__(self, scale=1.0):
        self.scale, self.urls = scale, []

    def get(self, url):
        self.urls.append(url)
        if "SLU" in url:
            stats = {"count": 17212, "sum": 5629663000 * self.scale, "avg": 327078.0 * self.scale}
        else:
            stats = {"count": 30098, "sum": 14544227929 * self.scale, "avg": 483229.0 * self.scale}
        return FakeJSONResponse({"features": [{"attributes": stats}]})


class FakeCity:
    def get(self, url):
        return FakeResponse((NH / "budget-info.html").read_bytes())


# ---- The extract ----

def test_extract_reads_each_kind_of_file(nh_figures):
    tax = json.loads((nh_figures / "tax.json").read_text())
    m25 = tax["years"]["2025"]["Manchester"]
    assert (m25["municipal"], m25["county"], m25["state_education"], m25["local_education"], m25["total"]) == \
        (10.04, 1.36, 1.51, 7.33, 20.24)
    assert m25["valuation"] == 12982280203 and m25["commitment"] == 268469989 and m25["set_on"] == "2025-11-17"
    # The calculation file fills in the same record.
    assert m25["town_tax_effort"] == 134173125 and m25["local_school_tax_effort"] == 97905599
    assert m25["cooperative_school_apportionment"] == 0 and m25["veterans_credits"] == 1345625
    # 2021's workbook spreads its columns out; each is found by its header.
    m21 = tax["years"]["2021"]["Manchester"]
    assert m21["total"] == 17.68 and m21["municipal"] == 8.26 and m21["valuation_with_utilities"] == 13128827277
    # Acworth is in a cooperative school district.
    assert tax["years"]["2025"]["Acworth"]["cooperative_school_apportionment"] == 2408401

    grad = json.loads((nh_figures / "graduation.json").read_text())["years"]
    assert grad["2025"]["Manchester"] == {"cohort": 965, "graduated": 730, "rate": 75.65}
    assert grad["2025"]["State"]["rate"] == 87.54 and grad["2024"]["Manchester"]["rate"] == 74.89

    sas = json.loads((nh_figures / "assessment.json").read_text())["years"]
    assert sas["2026"]["Manchester"]["rea"]["proficient"] == 31 and sas["2026"]["State"]["mat"]["proficient"] == 44
    assert sas["2019"]["Manchester"]["rea"]["proficient"] == 34, "the 2019 file's layout, and its mangled rows, are read"
    assert set(sas) == {"2019", "2026"}

    cost = json.loads((nh_figures / "cost_per_pupil.json").read_text())["years"]["2025"]
    assert cost["Manchester"] == 18020.9 and cost["State"] == 22699.85 and cost["Albany"] is None

    people = json.loads((nh_figures / "population.json").read_text())["years"]
    assert people["2025"]["Manchester"] == 116818 and "Hale's" not in " ".join(people["2025"])


def test_extract_keeps_saved_years_and_writes_a_line_per_town(nh_figures, monkeypatch):
    run_extract(monkeypatch, nh_figures, ["cohort2025.csv"])
    text = (nh_figures / "graduation.json").read_text()
    assert set(json.loads(text)["years"]) == {"2024", "2025"}
    assert sum(1 for line in text.splitlines() if line.lstrip().startswith('"Manchester"')) == 2


def test_extract_refuses_a_file_it_doesnt_know(tmp_path, monkeypatch):
    stray = tmp_path / "notes.csv"
    stray.write_text("a,b\n1,2\n")
    monkeypatch.setattr(extract, "FIGURES_DIR", tmp_path / "figures")
    monkeypatch.setattr("sys.argv", ["extract", str(stray)])
    with pytest.raises(SystemExit, match="not a file this command knows"):
        extract.main()


def test_the_engines_figures_cover_manchester():
    figures.load.cache_clear()
    assert figures.rows("tax", "Manchester")[-1][1]["total"]
    assert figures.rows("graduation", "Manchester") and figures.rows("population", "Manchester")


# ---- The town's sources ----

def test_a_new_hampshire_town_gets_new_hampshires_sources():
    config = manchester()
    states.check(config)
    assert states.for_town(config).source("tax_bill", config).module == "pipeline.states.nh.tax_bill"
    del config["finance"]["dra_municipality"]
    with pytest.raises(SystemExit, match="needs dra_municipality for New Hampshire's"):
        states.check(config)


def test_tax_bill_is_calculated_and_says_how(nh_figures, tmp_path):
    result = fetch_finance.run(manchester(), FakeParcels(), tmp_path, now=NOW)
    assert result == {"tax_year": 2025, "value_ratio": 1.12, "average_bill": 6620}
    saved = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())
    year = saved["years"][-1]
    assert year == {"tax_year": 2025, "period": "Tax year 2025", "average_bill": 6620, "average_value": 327078,
                    "parcels": 17212, "rate": 20.24, "calculated": nh_tax_bill.method(2025, 17212)}
    assert "17,212 single-family homes" in year["calculated"] and saved["figures_extracted_at"]


def test_tax_bill_is_kept_when_parcel_values_are_a_newer_years(nh_figures, tmp_path, capsys):
    fetch_finance.run(manchester(), FakeParcels(), tmp_path, now=NOW)
    before = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())["years"]
    # A revaluation raises every value 42% before the DRA has the new year's rate.
    result = fetch_finance.run(manchester(), FakeParcels(scale=1.42), tmp_path, now=NOW)
    assert "average_bill" not in result and result["kept"] == [2025]
    assert json.loads((tmp_path / "finance" / "tax_bill.json").read_text())["years"] == before
    assert "may have revalued" in capsys.readouterr().out


def test_budget_table_keeps_the_citys_order_and_years(nh_figures, tmp_path):
    years = nh_budget.parse_budget_table((NH / "budget-info.html").read_text())
    assert [y["fiscal_year"] for y in years] == [2024, 2025, 2026, 2027]
    fy27 = years[-1]
    assert fy27["total"] == 441900406 and fy27["lines"]["Education"] == 238492749
    assert "Health, Dental & Life Insurance" in fy27["lines"], "the footnote mark is dropped"
    assert [(r["label"], r["amount"]) for r in fy27["summary"]] == [
        ("Total Budget", 441900406), ("Less MSD Budget", -238492749), ("General Fund Budget", 203407657)]
    with pytest.raises(Exception, match="no budget table"):
        nh_budget.parse_budget_table("<table><tr><td>Nothing</td></tr></table>")


def test_budget_figures(nh_figures, tmp_path):
    path = tmp_path / "finance" / "budget.json"
    path.parent.mkdir(parents=True)
    # A year the city's page has dropped is kept.
    path.write_text(json.dumps({"city_budget": {"years": [{"fiscal_year": 2023, "total": 1, "lines": {"A": 1},
                                                           "summary": []}]}}))
    fetch_budget.run(manchester(), FakeCity(), tmp_path, now=NOW)
    b = json.loads(path.read_text())
    assert [r["tax_year"] for r in b["rates"]] == [2021, 2024, 2025] and b["rates"][-1]["total"] == 20.24
    assert b["tax_effort"]["tax_year"] == 2025 and b["tax_effort"]["parts"]["City"] == 134173125
    pr = b["per_resident"]
    assert pr["town"] == round(268469989 / 116818) and pr["communities"] == 3 and "July 1, 2025" in pr["calculated"]
    assert [y["fiscal_year"] for y in b["city_budget"]["years"]] == [2023, 2024, 2025, 2026, 2027]


def test_school_figures(nh_figures, tmp_path):
    latest = fetch_schools.run(manchester(), None, tmp_path, now=NOW)
    assert latest["graduation"]["town"] == 75.65 and latest["graduation"]["cohort"] == 965
    assert latest["sas_ela"] == {"year": 2026, "town": 31, "state": 55}
    assert latest["cost_per_pupil"] == {"year": 2025, "town": 18020.9, "state": 22699.85}


# ---- The site ----

@pytest.fixture
def nh_site(nh_figures, tmp_path, data_dir, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    for name in ("finance", "schools"):
        shutil.rmtree(data / name, ignore_errors=True)
    config = manchester()
    fetch_finance.run(config, FakeParcels(), data, now=NOW)
    fetch_budget.run(config, FakeCity(), data, now=NOW)
    fetch_schools.run(config, None, data, now=NOW)
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    return out


def test_new_hampshire_pages(nh_site):
    budget = (nh_site / "budget" / "index.html").read_text()
    assert "Calculated by Publick." in budget and "17,212 single-family homes" in budget
    assert "$20.24" in budget and "Less MSD Budget" in budget and "Division of Local Services" not in budget
    assert (nh_site / "budget" / "data" / "city-budget.csv").read_text().startswith("fiscal_year,")
    schools = (nh_site / "schools" / "index.html").read_text()
    assert "Statewide Assessment System" in schools and "MCAS" not in schools and "75.7%" in schools
    home = (nh_site / "index.html").read_text()
    assert "Tax year 2025 · Calculated by Publick from state figures" in home and 'href="/budget/#tax-bill"' in home
    about = (nh_site / "about" / "index.html").read_text()
    assert "Department of Revenue Administration" in about and "Division of Local Services" not in about


def test_every_calculated_figure_says_so_on_its_page(nh_site):
    """Each figure the data marks as calculated shows its method, under "Calculated by Publick"."""
    data = nh_site.parent / "data"
    marked = [y["calculated"] for y in json.loads((data / "finance" / "tax_bill.json").read_text())["years"]]
    marked.append(json.loads((data / "finance" / "budget.json").read_text())["per_resident"]["calculated"])
    budget = (nh_site / "budget" / "index.html").read_text()
    for how in marked:
        assert how.replace("'", "&#39;") in budget, how
    assert budget.count("Calculated by Publick.</strong>") == len(marked)


def test_new_hampshire_links_resolve(nh_site):
    pages = sorted(nh_site.rglob("*.html"))
    check_links(nh_site, pages)
    for path in (nh_site / "budget" / "index.html", nh_site / "schools" / "index.html"):
        assert parse(path).tags.count("h1") == 1, path
