"""Maine: the yearly statewide extract (MRS's valuation summary, Census estimates, the ESSA
Dashboard's crosstabs), the town sources that read it, and the pages they fill."""

import copy
import json
import shutil
import statistics
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
from pipeline.states.me import budget as me_budget
from pipeline.states.me import extract, figures
from pipeline.states.me import tax_bill as me_tax_bill

ME = FIXTURES / "me"
NOW = datetime(2026, 10, 4, 8, tzinfo=ZoneInfo("America/New_York"))
# Section 1 of the 2024 MVR summary as pdfplumber reads it, a page per form feed (from MRS's PDF).
SECTION_1 = (ME / "mvr2024-section1.txt").read_text().rstrip("\n").split("\f")
CROSSTABS = ["grad.csv", "absent.csv", "asmt.csv", "spending.csv"]


def pdf(pages: list[str]) -> bytes:
    """A PDF with each page's lines as text, in a standard font: what pdfplumber reads from MRS's summary."""
    def escape(line):
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for text in pages:
        stream = "BT /F1 7 Tf 9 TL 20 770 Td " + " ".join(f"({escape(line)}) '" for line in text.splitlines()) + " ET"
        objects.append(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                       f"/Contents {len(objects)} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def mvr_pdf(folder, pages=SECTION_1):
    """The summary's cover, Section 1, and a page of Section 2, which isn't read."""
    path = folder / "2024 Municipal Valuation Return Statistical Summary Report.pdf"
    path.write_bytes(pdf(["STATE OF MAINE\nMAINE REVENUE SERVICES\n2024\nMunicipal Valuation Return", *pages,
                          "2024 Municipal Valuation Return Statistical Summary - Personal Property Valuation\n"
                          "LEWISTON $1 $2 $3 $4 $5 $6 $7"]))
    return path


def run_extract(monkeypatch, folder, files):
    monkeypatch.setattr(extract, "FIGURES_DIR", folder)
    monkeypatch.setattr("sys.argv", ["extract", *(str(f) for f in files)])
    figures.load.cache_clear()
    assert extract.main() == 0
    figures.load.cache_clear()


@pytest.fixture
def me_figures(tmp_path, monkeypatch):
    """The fixture files' figures, in place of the engine's."""
    files = [mvr_pdf(tmp_path), ME / "sub-est2025_23.csv", *(ME / f for f in CROSSTABS)]
    run_extract(monkeypatch, tmp_path / "figures", files)
    yield tmp_path / "figures"
    figures.load.cache_clear()


def lewiston() -> dict:
    """Gloucester's fixture config, moved to Lewiston, Maine."""
    config = copy.deepcopy(load_config("gloucester"))
    config["town"].update(name="Lewiston", state="Maine", state_abbr="ME")
    config["finance"] = {"mrs_municipality": "Lewiston", "megis_geocode": "01050", "single_family_use": ["101"]}
    config["schools"] = {"doe_district": "Lewiston Public Schools", "district_name": "Lewiston Public Schools"}
    return config


class FakeParcels:
    """The parcel table's statistics for Lewiston, as it answered on 2026-10-04: every parcel, or its
    single-family homes. scale stands in for values from another year than the MVR's."""

    def __init__(self, scale=1.0, homes=6510):
        self.scale, self.homes, self.urls = scale, homes, []

    def get(self, url):
        self.urls.append(url)
        where = parse_qs(urlparse(url).query)["where"][0]
        assert "GEOCODE = '01050'" in where
        if "LAND_USE IN ('101')" in where:
            stats = {"count": self.homes, "sum": 704122200 * self.scale if self.homes else None,
                     "avg": 108160.09216589862 * self.scale if self.homes else None}
        else:
            stats = {"count": 12359, "sum": 2298250095 * self.scale, "avg": 185957.60943442027 * self.scale}
        return FakeJSONResponse({"features": [{"attributes": stats}]})


# ---- The extract ----

def test_extract_reads_each_kind_of_file(me_figures):
    tax = json.loads((me_figures / "tax.json").read_text())["years"]
    assert set(tax) == {"2024"}
    assert tax["2024"]["LEWISTON"] == {"county": "Androscoggin", "ratio": 54, "rate": 0.03177, "commitment": 69289125,
                                       "valuation": 2180960818, "land": 489861819, "buildings": 1599848782,
                                       "land_buildings": 2089710601}
    assert tax["2024"]["STATE TOTAL"]["commitment"] == 3412323170
    assert len(tax["2024"]) == 483, "every municipality and the state's total, not the counties'"
    assert tax["2024"]["CUMBERLAND"]["county"] == "Cumberland" and tax["2024"]["CYR PLT"]["county"] == "Aroostook"

    people = json.loads((me_figures / "population.json").read_text())["years"]["2024"]
    assert people["LEWISTON"] == 38821
    # Census names in MRS's form; unorganized territories and reservations aren't municipalities.
    assert {"SAINT AGATHA", "CYR PLT", "VERONA"} <= set(people)
    assert not any("UT" in name.split() or "RESERVATION" in name for name in people)

    grad = json.loads((me_figures / "graduation.json").read_text())["years"]
    assert grad["2025"]["Lewiston Public Schools"] == {"cohort": 461, "graduated": 309, "rate": 67.03}
    assert grad["2025"]["Statewide"]["rate"] == 89.09 and set(grad) == {"2021", "2022", "2023", "2024", "2025"}

    absent = json.loads((me_figures / "absenteeism.json").read_text())["years"]
    assert absent["2025"]["Lewiston Public Schools"] == {"absent": 1769, "students": 5198, "rate": 34.03}

    tests = json.loads((me_figures / "assessment.json").read_text())["years"]
    assert set(tests) == {"2023", "2024", "2025"}
    lew = tests["2025"]["Lewiston Public Schools"]
    assert lew["ela"] == {"proficient": 37.96, "tested": 2416, "participation": 92.67} and lew["math"]["proficient"] == 20.17
    assert tests["2025"]["Statewide"]["ela"]["proficient"] == 63.84 and "science" not in lew

    spending = json.loads((me_figures / "spending.json").read_text())["years"]
    assert spending["2025"]["Lewiston Public Schools"] == {"per_pupil": 19303.51, "total": 120589023.87}
    assert spending["2025"]["Statewide"]["per_pupil"] == 21717.26


def test_extract_keeps_saved_years_and_writes_a_line_per_district(me_figures, tmp_path, monkeypatch):
    # A newer download of one year, saved as the dashboard's exports are (UTF-8).
    text = extract.decode((ME / "grad.csv").read_bytes())
    lines = [line for line in text.splitlines() if "\t2024-2025\t" in line or line.startswith("Index")]
    newer = tmp_path / "grad-2025.csv"
    newer.write_text("\n".join(lines).replace("67.03%", "68.00%") + "\n", encoding="utf-8")
    run_extract(monkeypatch, me_figures, [newer])
    text = (me_figures / "graduation.json").read_text()
    years = json.loads(text)["years"]
    assert set(years) == {"2021", "2022", "2023", "2024", "2025"}
    assert years["2025"]["Lewiston Public Schools"]["rate"] == 68.0 and years["2024"]["Lewiston Public Schools"]
    assert sum(1 for line in text.splitlines() if line.lstrip().startswith('"Lewiston Public Schools"')) == 5


def test_extract_refuses_a_file_it_doesnt_know(tmp_path, monkeypatch):
    stray = tmp_path / "notes.csv"
    stray.write_text("a\tb\n1\t2\n")
    monkeypatch.setattr(extract, "FIGURES_DIR", tmp_path / "figures")
    monkeypatch.setattr("sys.argv", ["extract", str(stray)])
    with pytest.raises(SystemExit, match="not a file this command knows"):
        extract.main()


def test_the_mvr_reader_checks_what_it_read(tmp_path):
    year, rows = extract.mvr_text(SECTION_1)
    assert year == 2024 and rows["GARFIELD PLT"]["rate"] == 0.0009
    # A row the reader can't make out is caught by the state's total.
    garbled = [page.replace("LEWISTON 54% $69,289,125", "LEWISTON 54% 69,289,125") for page in SECTION_1]
    with pytest.raises(SystemExit, match="a row wasn't read"):
        extract.mvr_text(garbled)
    # Part of a summary isn't taken for one.
    with pytest.raises(SystemExit, match="not a full Municipal Valuation Return"):
        extract.read(mvr_pdf(tmp_path, SECTION_1[:2] + SECTION_1[-1:]))


def test_mvr_summaries_are_found_by_their_links(monkeypatch):
    html = (ME / "mvr-page.html").read_text()
    links = extract.mvr_links(html)
    assert links[2024] == ("https://www.maine.gov/revenue/sites/maine.gov.revenue/files/inline-files/"
                           "2024%20Municipal%20Valuation%20Return%20Statistical%20Summary%20Report.pdf")
    assert links[2020].endswith("/2020mvrstats.pdf"), "a non-breaking space in the link's text"
    assert min(links) == 2013 and len(links) == 12

    class Site:
        def __init__(self):
            self.urls = []

        def get(self, url, timeout=None):
            self.urls.append(url)
            response = FakeResponse(html.encode() if url == extract.MRS_PAGE else b"%PDF")
            response.raise_for_status = lambda: None
            return response

    # Every summary read says it's 2024's.
    monkeypatch.setattr(extract, "mvr", lambda content, name: (2024, {"X": {}}))
    site = Site()
    saved = {"years": {str(year): {} for year in range(2015, 2024)}}
    found = extract.fetch_mvr(saved, refetch=False, http=site)
    assert list(found) == [2024] and len(site.urls) == 2, "only the year not saved yet is fetched"
    with pytest.raises(SystemExit, match="the link says 2023, the summary says 2024"):
        extract.fetch_mvr(saved, refetch=True, http=Site())


def test_census_names_in_mrs_form():
    assert extract.census_name("Lewiston city") == "LEWISTON"
    assert extract.census_name("St. Agatha town") == "SAINT AGATHA"
    assert extract.census_name("Cyr plantation") == "CYR PLT"
    assert extract.census_name("Verona Island town") == "VERONA"
    assert extract.census_name("Connor UT") is None and extract.census_name("Penobscot Indian Island Reservation") is None


class FakeDashboard:
    """The ESSA Dashboard's Data Download, answering each export with the fixture crosstab's rows for that
    year, decoded as the export is. Assessments for 2021-22 have no sheet, as on the dashboard."""

    def __init__(self):
        self.years = ["2021-2022", "2022-2023", "2023-2024", "2024-2025"]
        self.filters, self.exports = [], []

    def filter(self, field, values=None):
        self.filters.append((field, values))

    def export(self, measure, sheet, year):
        self.exports.append((measure, year))
        name = {"Graduation Rate": "grad.csv", "Chronic Absenteeism": "absent.csv",
                "Assessments (2 level)": "asmt.csv", "Per Pupil Spending": "spending.csv"}[measure]
        lines = extract.decode((ME / name).read_bytes()).splitlines()
        rows = [line for line in lines[1:] if f"\t{year}\t" in line]
        return "\n".join([lines[0], *rows]) if rows else ""


def test_schools_are_exported_from_the_dashboard(tmp_path, monkeypatch):
    board = FakeDashboard()
    monkeypatch.setattr(extract, "Dashboard", lambda: board)
    monkeypatch.setattr(extract, "FIGURES_DIR", tmp_path)
    monkeypatch.setattr("sys.argv", ["extract", "--schools"])
    assert extract.main() == 0
    assert board.filters == [("District Name", None)], "every district"
    assert len(board.exports) == 16
    assert set(json.loads((tmp_path / "assessment.json").read_text())["years"]) == {"2023", "2024", "2025"}
    grad = json.loads((tmp_path / "graduation.json").read_text())
    assert set(grad["years"]) == {"2022", "2023", "2024", "2025"} and "ESSA Dashboard" in grad["source"]
    assert not (tmp_path / "tax.json").exists(), "--schools alone doesn't fetch the MVR summaries"


def test_the_dashboards_years_are_read_from_its_opening_state():
    boot = ('{"caption":"Year","collation":{"f":0},"tuples":[{"s":true,"t":[{"t":"s","v":"2024-2025"}]},'
            '{"s":false,"t":[{"t":"s","v":"2023-2024"}]}]},{"caption":"District Name","v":"1999-2000"}')
    assert extract.Dashboard.year_values(boot.replace('"', '\\"')) == ["2023-2024", "2024-2025"]


def test_the_engines_figures_cover_lewiston():
    figures.load.cache_clear()
    tax = figures.rows("tax", "LEWISTON")
    assert [y for y, _ in tax][:1] == [2015] and tax[-1][1]["rate"]
    assert figures.rows("population", "LEWISTON")
    for kind in ("graduation", "absenteeism", "assessment", "spending"):
        assert figures.rows(kind, "Lewiston Public Schools"), kind


# ---- The town's sources ----

def test_a_maine_town_gets_maines_sources():
    config = lewiston()
    states.check(config)
    assert states.for_town(config).source("tax_bill", config).module == "pipeline.states.me.tax_bill"
    assert {r.file for r in rhythms.for_town(config)} >= {"finance/tax_bill.json", "finance/budget.json",
                                                         "schools/schools.json"}
    del config["finance"]["single_family_use"], config["finance"]["megis_geocode"]
    with pytest.raises(SystemExit, match="needs megis_geocode, single_family_use for Maine's tax bill"):
        states.check(config)
    del config["schools"]["doe_district"]
    config["finance"] = {"mrs_municipality": "Lewiston", "megis_geocode": "01050", "single_family_use": ["101"]}
    with pytest.raises(SystemExit, match="needs doe_district for Maine's school figures"):
        states.check(config)


def test_tax_bill_is_calculated_and_says_how(me_figures, tmp_path):
    parcels = FakeParcels()
    result = fetch_finance.run(lewiston(), parcels, tmp_path, now=NOW)
    assert result == {"tax_year": 2024, "value_ratio": 1.1, "average_bill": 3436}
    assert "LAND_VAL + BLDG_VAL" in parse_qs(urlparse(parcels.urls[-1]).query)["outStatistics"][0]
    saved = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())
    year = saved["years"][-1]
    assert year == {"tax_year": 2024, "period": "Tax year 2024", "average_bill": 3436, "average_value": 108160,
                    "parcels": 6510, "rate": 31.77, "certified_ratio": 54,
                    "calculated": me_tax_bill.method(2024, 6510, ("101",))}
    assert "6,510 single-family homes" in year["calculated"] and "2024 tax rate" in year["calculated"]
    assert saved["figures_extracted_at"] and saved["parcels_url"].startswith("https://www.arcgis.com/")


def test_tax_bill_is_kept_when_parcel_values_are_another_years(me_figures, tmp_path, capsys):
    with pytest.raises(FetchError, match="no tax bill yet"):
        fetch_finance.run(lewiston(), FakeParcels(scale=0.6), tmp_path, now=NOW)
    assert not (tmp_path / "finance" / "tax_bill.json").exists()
    fetch_finance.run(lewiston(), FakeParcels(), tmp_path, now=NOW)
    before = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())["years"]
    # Values sent before a revaluation, or from another year, don't add up to the MVR's.
    result = fetch_finance.run(lewiston(), FakeParcels(scale=1.42), tmp_path, now=NOW)
    assert "average_bill" not in result and result["kept"] == [2024]
    assert json.loads((tmp_path / "finance" / "tax_bill.json").read_text())["years"] == before
    assert "may be from another year" in capsys.readouterr().out


def test_tax_bill_needs_the_towns_own_codes(me_figures, tmp_path):
    with pytest.raises(FetchError, match="single_family_use"):
        fetch_finance.run(lewiston(), FakeParcels(homes=0), tmp_path, now=NOW)


def test_budget_figures(me_figures, tmp_path):
    fetch_budget.run(lewiston(), None, tmp_path, now=NOW)
    b = json.loads((tmp_path / "finance" / "budget.json").read_text())
    rate = b["rates"][-1]
    rates = sorted(r["rate"] * 1000 for name, r in figures.load("tax")["years"]["2024"].items()
                   if name != "STATE TOTAL" and r["rate"])
    assert rate["tax_year"] == 2024 and rate["rate"] == 31.77 and rate["certified_ratio"] == 54
    assert rate["municipalities"] == len(rates) and rate["state_median"] == round(statistics.median(rates), 3)
    assert rate["commitment"] == 69289125 and rate["valuation"] == 2180960818
    pr = b["per_resident"]
    assert pr["town"] == round(69289125 / 38821) and pr["population"] == 38821
    assert pr["communities"] == 17, "the municipalities in the fixture's Census rows"
    assert "July 1, 2024" in pr["calculated"] and "17 Maine towns, cities, and plantations" in pr["calculated"]
    assert me_budget.median_rate(2019) == (None, 0)


def test_school_figures(me_figures, tmp_path):
    latest = fetch_schools.run(lewiston(), None, tmp_path, now=NOW)
    assert latest["graduation"] == {"year": 2025, "town": 67.03, "state": 89.09, "cohort": 461, "graduated": 309}
    assert latest["absenteeism"] == {"year": 2025, "town": 34.03, "state": 23.37}
    assert latest["tests_ela"] == {"year": 2025, "town": 37.96, "state": 63.84}
    assert latest["tests_math"] == {"year": 2025, "town": 20.17, "state": 49.38}
    assert latest["spending"] == {"year": 2025, "town": 19303.51, "state": 21717.26}
    saved = json.loads((tmp_path / "schools" / "schools.json").read_text())
    assert [y["year"] for y in saved["measures"]["tests_ela"]["years"]] == [2023, 2024, 2025]


# ---- The site ----

@pytest.fixture
def me_site(me_figures, tmp_path, data_dir, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    for name in ("finance", "schools"):
        shutil.rmtree(data / name, ignore_errors=True)
    config = lewiston()
    config["site"]["languages"] = ["en", "es"]   # as every town is
    fetch_finance.run(config, FakeParcels(), data, now=NOW)
    fetch_budget.run(config, None, data, now=NOW)
    fetch_schools.run(config, None, data, now=NOW)
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    return out


def test_maine_pages(me_site):
    budget = (me_site / "budget" / "index.html").read_text()
    assert "Calculated by Publick." in budget and "6,510 single-family homes" in budget
    assert "$31.77" in budget and "$3,436" in budget and "54% of market value" in budget
    assert "Maine Revenue Services" in budget and "Division of Local Services" not in budget
    assert (me_site / "budget" / "data" / "tax-rates.csv").read_text().startswith("tax_year,tax_rate,")
    assert "2024,108160,31.77,3436,6510" in (me_site / "budget" / "data" / "tax-bill.csv").read_text()
    schools = (me_site / "schools" / "index.html").read_text()
    assert "Maine Through Year Assessment" in schools and "MCAS" not in schools
    assert "67.0%" in schools and "34.0%" in schools and "$19,304" in schools and "Class of 2025" in schools
    home = (me_site / "index.html").read_text()
    assert "Tax year 2024 · Calculated by Publick from state figures" in home and 'href="/budget/#tax-bill"' in home
    about = (me_site / "about" / "index.html").read_text()
    assert "Municipal Valuation Return Statistical Summary" in about and "ESSA Dashboard" in about
    assert "Division of Local Services" not in about


def test_every_calculated_figure_says_so_on_its_page(me_site):
    """Each figure the data marks as calculated shows its method, under "Calculated by Publick"."""
    data = me_site.parent / "data"
    marked = [y["calculated"] for y in json.loads((data / "finance" / "tax_bill.json").read_text())["years"]]
    marked.append(json.loads((data / "finance" / "budget.json").read_text())["per_resident"]["calculated"])
    marked.append(json.loads((data / "finance" / "budget.json").read_text())["rates_median_calculated"])
    budget = (me_site / "budget" / "index.html").read_text()
    for how in marked:
        assert how.replace("'", "&#39;").replace('"', "&#34;") in budget, how
    assert budget.count("Calculated by Publick.</strong>") == len(marked)


def test_maine_links_resolve(me_site):
    pages = sorted(me_site.rglob("*.html"))
    check_links(me_site, pages)
    for path in (me_site / "budget" / "index.html", me_site / "schools" / "index.html"):
        assert parse(path).tags.count("h1") == 1, path


def test_maine_pages_in_spanish(me_site):
    for section in ("budget", "schools"):
        page = (me_site / "es" / section / "index.html").read_text()
        assert '<html lang="es"' in page and "%%" not in page, section
    budget = (me_site / "es" / "budget" / "index.html").read_text()
    assert "Proporción certificada" in budget and "Calculado por Publick" in budget
