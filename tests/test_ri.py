"""Rhode Island: the yearly extract of the Division of Municipal Finance's tables, school figures from
RIDE's report card data files and assessment data portal, and the pages they fill."""

import copy
import json
import re
import shutil
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from conftest import BUILT_AT, FIXTURES
from fakes import FakeResponse
from test_site import parse
from test_site import test_internal_links_resolve as check_links

from pipeline import build_site, fetch_budget, fetch_schools, rhythms, states
from pipeline.config import load_config
from pipeline.http import FetchError
from pipeline.states.ri import extract, figures
from pipeline.states.ri import schools as ri_schools

RI = FIXTURES / "ri"
NOW = datetime(2026, 10, 4, 8, tzinfo=ZoneInfo("America/New_York"))
# Real DMF files, small enough to keep: fiscal year 2020's three tables (the levy with the DMF's own
# per capita column), and fiscal year 2024's rates (the first with no motor vehicle rate).
DOWNLOADS = ["2019-Tax-Rates-12-31-18-FINAL.pdf", "2023-Tax-Rates-12-31-22 Final.pdf",
             "Statewide-Net-Assessed-Value-by-Class-of-Property-12-31-18-FINAL.pdf",
             "Statewide-Tax-Levy-by-Class-of-Property-12-31-18-FINAL.pdf", "sub-est2025_44.csv"]
# Fiscal year 2026's files are too big to keep, so their text, as pdfplumber reads it, stands in.
TEXTS = {"rates": "rates-fy2026.txt", "assessed": "assessed-fy2026.txt", "levy": "levy-fy2026.txt"}


def run_extract(monkeypatch, folder, files=DOWNLOADS):
    monkeypatch.setattr(extract, "FIGURES_DIR", folder)
    monkeypatch.setattr("sys.argv", ["extract", *(str(RI / f) for f in files)])
    figures.load.cache_clear()
    assert extract.main() == 0
    figures.load.cache_clear()


def add_text_year(kind: str, name: str) -> None:
    """Save a year read from a file's text, as the extract saves one read from the file."""
    text = (RI / name).read_text()
    saved = extract.load(kind)
    parse = extract.rates if kind == "rates" else extract.by_class
    saved["years"][str(extract.fiscal_year(text, name))] = parse(text)
    extract.save(kind, saved, NOW)


@pytest.fixture
def ri_figures(tmp_path, monkeypatch):
    """The fixture files' figures, in place of the engine's."""
    run_extract(monkeypatch, tmp_path / "figures")
    for kind, name in TEXTS.items():
        add_text_year(kind, name)
    figures.load.cache_clear()
    yield tmp_path / "figures"
    figures.load.cache_clear()


def south_kingstown() -> dict:
    """Gloucester's fixture config, moved to South Kingstown, Rhode Island."""
    config = copy.deepcopy(load_config("gloucester"))
    config["town"].update(name="South Kingstown", state="Rhode Island", state_abbr="RI")
    config["finance"] = {"dmf_municipality": "South Kingstown",
                         "budget_documents_url": "https://www.southkingstownri.com/171/Finance"}
    config["schools"] = {"ride_district": 32, "district_name": "South Kingstown Public Schools"}
    return config


class FakeRIDE:
    """RIDE's Data Files page (a few years' files), those files, and the assessment portal."""

    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        parsed = urlparse(url)
        if url == ri_schools.DATA_FILES:
            return FakeResponse((RI / "datafiles.html").read_bytes())
        if parsed.netloc == "reportcard.ride.ri.gov" and "/datafiles/" in parsed.path:
            return FakeResponse((RI / parsed.path.rsplit("/", 1)[1]).read_bytes())
        query = parse_qs(parsed.query)
        if url.startswith(ri_schools.ADP_YEARS):
            return FakeResponse((RI / "adp-years.json").read_bytes())
        if url.startswith(ri_schools.ADP_EXPORT):
            assert query["lea"] == ["32"] and query["compareWith"] == ["00"]
            assert query["schYear"] == ["2017-18,2018-19,2020-21,2021-22,2022-23,2023-24,2024-25"]
            subject = {"5": "ela", "6": "math"}[query["assessment"][0]]
            return FakeResponse((RI / f"adp-{subject}-32.tsv").read_bytes())
        raise AssertionError(f"unexpected URL {url}")


# ---- The extract ----

def test_extract_reads_each_kind_of_file(ri_figures):
    rates = json.loads((ri_figures / "rates.json").read_text())["years"]
    assert set(rates) == {"2020", "2024", "2026"}
    assert rates["2020"]["South Kingstown"] == {"residential": 14.45, "commercial": 14.45, "personal_property": 14.45,
                                                "motor_vehicles": 18.71, "revalued": True}
    # From fiscal year 2024 the car tax is gone: the file leaves the column blank, later files say 0.
    assert rates["2024"]["South Kingstown"] == {"residential": 11.05, "commercial": 11.05, "personal_property": 11.05,
                                                "motor_vehicles": None}
    assert rates["2026"]["South Kingstown"] == {"residential": 8.94, "commercial": 8.94, "personal_property": 11.05,
                                                "motor_vehicles": 0.0, "revalued": True}
    assert len(rates["2026"]) == len(rates["2020"]) == 39 and "State" not in rates["2026"]

    assessed = json.loads((ri_figures / "assessed.json").read_text())["years"]
    assert assessed["2020"]["South Kingstown"] == {"residential": 4390145043, "commercial": 545424264,
                                                   "tangible": 114091164, "motor_vehicles": 168826026,
                                                   "total": 5218486497}
    assert assessed["2026"]["State"]["total"] == 209433731444
    # A footnote mark stuck to a name is dropped.
    assert assessed["2026"]["Cumberland"]["total"] == 5776522430

    levy = json.loads((ri_figures / "levy.json").read_text())["years"]
    # The DMF's levy per capita, at the end of each row in fiscal year 2020's file, isn't kept.
    assert levy["2020"]["South Kingstown"] == {"residential": 63437397, "commercial": 7881382, "tangible": 1648609,
                                               "motor_vehicles": 3157922, "total": 76125309}
    assert levy["2026"]["South Kingstown"]["total"] == 79180320 and levy["2026"]["State"]["total"] == 2763801732

    people = json.loads((ri_figures / "population.json").read_text())["years"]
    assert people["2025"]["South Kingstown"] == 32009 and len(people["2025"]) == 39


def test_the_year_is_the_fiscal_year_the_file_names():
    text = (RI / "rates-fy2026.txt").read_text()
    assert text.startswith("FY 2026") and "December 31, 2024" in text[:200]
    assert extract.fiscal_year(text, "rates") == 2026
    # The oldest files have no "FY" in their heading: the fiscal year is the assessment year plus two.
    assert extract.fiscal_year("Statewide Net Assessed Value by Class of Property\nAs assessed on December 31, 2013",
                               "old") == 2015
    with pytest.raises(SystemExit, match="heading says FY 2025"):
        extract.fiscal_year("FY 2025 Rhode Island Tax Rates by Class of Property\nAssessment Date December 31, 2024",
                            "odd")


def test_a_rate_sent_to_a_note_is_left_blank():
    # West Warwick's commercial rate, until fiscal year 2017.
    text = "NOTES:\n2) Municipality had a revaluation or statistical update effective 12/31/15\n" \
           "WEST WARWICK 2, 4 25.84 see note 4 41.03 28.47\nSOUTH KINGSTOWN 2 15.09 15.09 15.09 18.71\n"
    rows = extract.rates(text)
    assert rows["West Warwick"] == {"residential": 25.84, "commercial": None, "personal_property": 41.03,
                                    "motor_vehicles": 28.47, "revalued": True}
    assert rows["South Kingstown"]["revalued"] is True


def test_extract_keeps_saved_years_and_writes_a_line_per_town(ri_figures, monkeypatch):
    run_extract(monkeypatch, ri_figures, ["2019-Tax-Rates-12-31-18-FINAL.pdf"])
    text = (ri_figures / "rates.json").read_text()
    assert set(json.loads(text)["years"]) == {"2020", "2024", "2026"}
    assert sum(1 for line in text.splitlines() if line.lstrip().startswith('"South Kingstown"')) == 3


def test_extract_refuses_a_file_it_doesnt_know(tmp_path, monkeypatch):
    stray = tmp_path / "notes.csv"
    stray.write_text("a,b\n1,2\n")
    monkeypatch.setattr(extract, "FIGURES_DIR", tmp_path / "figures")
    monkeypatch.setattr("sys.argv", ["extract", str(stray)])
    with pytest.raises(SystemExit, match="not a file this command knows"):
        extract.main()
    # A PDF that isn't one of the DMF's tables.
    monkeypatch.setattr("sys.argv", ["extract", str(FIXTURES / "finalsite_doc.pdf")])
    with pytest.raises(SystemExit, match="not a file this command knows"):
        extract.main()
    assert not (tmp_path / "figures").exists()


def test_the_engines_figures_cover_south_kingstown():
    figures.load.cache_clear()
    assert figures.rows("rates", "South Kingstown")[-1] == (2026, {
        "residential": 8.94, "commercial": 8.94, "personal_property": 11.05, "motor_vehicles": 0.0, "revalued": True})
    assert figures.rows("levy", "South Kingstown") and figures.rows("population", "South Kingstown")
    for kind in ("rates", "assessed", "levy", "population"):
        assert all(len([n for n in rows if n != "State"]) == 39 for rows in figures.load(kind)["years"].values()), kind


# ---- The town's sources ----

def test_a_rhode_island_town_gets_rhode_islands_sources():
    config = south_kingstown()
    states.check(config)
    state = states.for_town(config)
    assert state.source("budget", config).module == "pipeline.states.ri.budget"
    assert state.source("tax_bill", config) is None and not state.tax_source
    del config["finance"]["dmf_municipality"]
    with pytest.raises(SystemExit, match="needs dmf_municipality for Rhode Island's"):
        states.check(config)
    config = south_kingstown()
    del config["schools"]["ride_district"]
    with pytest.raises(SystemExit, match="needs ride_district for Rhode Island's school figures"):
        states.check(config)


def test_a_town_in_rhode_island_has_its_rhythms():
    labels = [r.label for r in rhythms.for_town(south_kingstown())]
    assert labels[:2] == ["Tax rates and levy", "School figures"]


def test_budget_figures(ri_figures, tmp_path):
    result = fetch_budget.run(south_kingstown(), None, tmp_path, now=NOW)
    assert result == {"rates": 2026, "levy": 2026, "per_resident": 2474}
    b = json.loads((tmp_path / "finance" / "budget.json").read_text())
    assert [r["fiscal_year"] for r in b["rates"]] == [2020, 2024, 2026]
    fy26 = b["rates"][-1]
    assert (fy26["residential"], fy26["personal_property"], fy26["revalued"]) == (8.94, 11.05, True)
    # The median of the 39 residential rates.
    rates = json.loads((ri_figures / "rates.json").read_text())["years"]["2026"]
    assert fy26["state_median"] == sorted(r["residential"] for r in rates.values())[19] == 12.70
    assert fy26["communities"] == 39 and "residential rates" in b["rates_median_calculated"]
    assert b["rates"][1]["motor_vehicles"] is None and b["rates"][1]["revalued"] is False
    assert [y["fiscal_year"] for y in b["levy"]] == [2020, 2026] and b["levy"][-1]["state_total"] == 2763801732
    assert b["assessed"][-1]["residential"] == 8045614399
    pr = b["per_resident"]
    assert pr["town"] == round(79180320 / 32009) and pr["population"] == 32009
    assert (pr["fiscal_year"], pr["state_median"], pr["communities"]) == (2026, 2597, 39)
    assert "July 1, 2025" in pr["calculated"] and "39 Rhode Island cities and towns" in pr["calculated"]


def test_a_name_the_dmf_doesnt_use_is_an_error(ri_figures, tmp_path):
    config = south_kingstown()
    config["finance"]["dmf_municipality"] = "Town of South Kingstown"
    with pytest.raises(FetchError, match="names are the DMF's"):
        fetch_budget.run(config, None, tmp_path, now=NOW)


def test_report_card_files_are_found_by_name_and_year():
    files = ri_schools.report_files((RI / "datafiles.html").read_text())
    assert files["graduation"] == {"202223": "https://reportcard.ride.ri.gov/202223/datafiles/GraduationRates_202223.xlsx",
                                   "202425": "https://reportcard.ride.ri.gov/202425/datafiles/GraduationRates_202425.xlsx"}
    assert set(files["spending"]) == {"201718", "202425"}
    assert files["absenteeism"]["202425"].endswith("/Accountability_202425_v1.1.xlsx")


def test_report_card_files_layouts():
    # 2022-23's state sheet has a row for each group of districts; the state's is "Rhode Island".
    assert ri_schools.graduation((RI / "GraduationRates_202223.xlsx").read_bytes(), "32") == ({2022: 92.41}, {2022: 83.45})
    assert ri_schools.graduation((RI / "GraduationRates_202425.xlsx").read_bytes(), "32") == ({2024: 92.54}, {2024: 84.13})
    # 2017-18's sheet is "LEAs", and its school year is written "2016-2017".
    assert ri_schools.spending((RI / "Finance_201718.xlsx").read_bytes(), "32") == ({2017: 19414.94}, {2017: 16659.6})
    assert ri_schools.spending((RI / "Finance_202425.xlsx").read_bytes(), "32") == ({2024: 28443.89}, {2024: 23571.06})
    # 2021-22's header rows are the other way around, and it has a group code but no group name.
    assert ri_schools.absenteeism((RI / "Accountability_202122.xlsx").read_bytes(), "32") == pytest.approx(19.305472)
    assert ri_schools.absenteeism((RI / "Accountability_202425_v1.1.xlsx").read_bytes(), "32") == pytest.approx(15.558474)
    assert ri_schools.absenteeism((RI / "Finance_202425.xlsx").read_bytes(), "32") is None


def test_ricas_export():
    town, state = ri_schools.parse_adp((RI / "adp-ela-32.tsv").read_text())
    assert town[2025] == 53.3 and state[2025] == 33.7 and sorted(town) == [2018, 2019, 2021, 2022, 2023, 2024, 2025]
    with pytest.raises(FetchError, match="no results table"):
        ri_schools.parse_adp("<html>Error</html>")


def test_school_figures(tmp_path):
    client = FakeRIDE()
    latest = fetch_schools.run(south_kingstown(), client, tmp_path, now=NOW)
    assert latest["graduation"] == {"year": 2024, "town": 92.54, "state": 84.13}
    assert latest["spending"] == {"year": 2024, "town": 28443.89, "state": 23571.06}
    assert latest["ricas_ela"] == {"year": 2025, "town": 53.3, "state": 33.7}
    assert latest["ricas_math"] == {"year": 2025, "town": 51.1, "state": 31.4}
    assert latest["absenteeism"]["year"] == 2025 and latest["absenteeism"]["state"] is None
    saved = json.loads((tmp_path / "schools" / "schools.json").read_text())
    assert saved["district"] == "South Kingstown Public Schools"
    assert [y["year"] for y in saved["measures"]["graduation"]["years"]] == [2022, 2024]
    assert [y["year"] for y in saved["measures"]["absenteeism"]["years"]] == [2022, 2025]
    # The Data Files page, six files, and the portal's years and export for each subject.
    assert len(client.urls) == 1 + 6 + 4


def test_saved_years_are_kept_and_not_asked_for_again(tmp_path):
    config, client = south_kingstown(), FakeRIDE()
    fetch_schools.run(config, client, tmp_path, now=NOW)
    path = tmp_path / "schools" / "schools.json"
    saved = json.loads(path.read_text())
    # A year no longer in the files read stays on the page.
    saved["measures"]["graduation"]["years"].insert(0, {"year": 2019, "town": 89.4, "state": 83.9})
    path.write_text(json.dumps(saved))
    client.urls.clear()
    fetch_schools.run(config, client, tmp_path, now=NOW)
    years = json.loads(path.read_text())["measures"]["graduation"]["years"]
    assert [y["year"] for y in years] == [2019, 2022, 2024]
    # Only the newest report card year's files are read again.
    files = [u for u in client.urls if "/datafiles/" in u]
    assert files and all("/202425/" in u for u in files)


# ---- The site ----

@pytest.fixture
def ri_site(ri_figures, tmp_path, data_dir, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    for name in ("finance", "schools"):
        shutil.rmtree(data / name, ignore_errors=True)
    config = south_kingstown()
    config["site"]["languages"] = ["en", "es"]   # as every town is
    fetch_budget.run(config, None, data, now=NOW)
    fetch_schools.run(config, FakeRIDE(), data, now=NOW)
    # The Census Bureau names South Kingstown a town (pipeline/fetch_place.py).
    (data / "place.json").write_text('{"name": "South Kingstown town", "lsad": "43", "kind": "town"}\n')
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    return out


def test_rhode_island_budget_page(ri_site):
    budget = (ri_site / "budget" / "index.html").read_text()
    assert "Division of Municipal Finance" in budget and "Division of Local Services" not in budget
    assert "$8.94" in budget and "$12.70" in budget and "$11.05" in budget and "$79.2M" in budget
    assert "$2,474" in budget and "$2,597" in budget and "Fiscal year 2026" in budget
    text = re.sub(r"<[^>]+>", "", budget)
    assert "doesn't publish an average tax bill" in text and "are on the town website" in text
    for name in ("tax-rates", "tax-levy", "assessed-value"):
        assert (ri_site / "budget" / "data" / f"{name}.csv").read_text().startswith("fiscal_year,")
    assert "2026,8.94,8.94,11.05,0.0,yes,12.7" in (ri_site / "budget" / "data" / "tax-rates.csv").read_text()
    # No tax bill on the home page: Rhode Island has none.
    home = (ri_site / "index.html").read_text()
    assert 'href="/budget/#tax-bill"' not in home
    about = (ri_site / "about" / "index.html").read_text()
    assert "Division of Municipal Finance" in about and "Department of Revenue Administration" not in about


def test_rhode_island_schools_page(ri_site):
    schools = (ri_site / "schools" / "index.html").read_text()
    assert "Rhode Island Department of Education" in schools and "RICAS" in schools
    assert "92.5%" in schools and "53.3%" in schools and "$28,444" in schools and "15.6%" in schools
    assert "2024–25" in schools and "DESE" not in schools and "EdSight" not in schools
    assert "only loosely" in schools
    about = (ri_site / "about" / "index.html").read_text()
    assert "assessment data portal" in about and "Elementary and Secondary Education" not in about


def test_every_calculated_figure_says_so_on_its_page(ri_site):
    """The state's median rate and property tax per resident, under "Calculated by Publick"."""
    b = json.loads((ri_site.parent / "data" / "finance" / "budget.json").read_text())
    marked = [b["rates_median_calculated"], b["per_resident"]["calculated"]]
    budget = (ri_site / "budget" / "index.html").read_text()
    for how in marked:
        assert how.replace("'", "&#39;") in budget, how
    assert budget.count("Calculated by Publick.</strong>") == len(marked)


def test_rhode_island_links_resolve(ri_site):
    check_links(ri_site, sorted(ri_site.rglob("*.html")))
    for path in (ri_site / "budget" / "index.html", ri_site / "schools" / "index.html"):
        assert parse(path).tags.count("h1") == 1, path


def test_a_town_is_called_a_town(ri_site):
    for page in ("budget/index.html", "schools/index.html"):
        text = re.sub(r"<[^>]+>", " ", (ri_site / page).read_text())
        assert not re.search(r"\bcity's\b|\bThe city\b|\bcity website\b", text), page


def test_rhode_island_pages_in_spanish(ri_site):
    for section in ("budget", "schools"):
        page = (ri_site / "es" / section / "index.html").read_text()
        assert '<html lang="es"' in page and "%%" not in page, section
    budget = (ri_site / "es" / "budget" / "index.html").read_text()
    assert "Tasas del impuesto a la propiedad" in budget and "Calculado por Publick" in budget
