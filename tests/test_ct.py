"""Connecticut: school figures from EdSight's exports, and the schools page they fill."""

import copy
import re
import json
import shutil
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from conftest import BUILT_AT, FIXTURES
from fakes import FakeResponse
from test_site import parse
from test_site import test_internal_links_resolve as check_links

from pipeline import build_site, fetch_schools, rhythms, states
from pipeline.config import load_config
from pipeline.http import FetchError
from pipeline.states.ct import schools as ct_schools

EDSIGHT = FIXTURES / "edsight"
NOW = datetime(2026, 10, 1, 8, tzinfo=ZoneInfo("America/New_York"))
DISTRICT = "Wallingford School District"
PROGRAMS = {"GraduationExport": "graduation", "ChronicAbsenteeismExport": "absenteeism",
            "SmarterBalancedAssessmentExport": "tests"}


def wallingford() -> dict:
    """Gloucester's fixture config, moved to Wallingford, Connecticut: no budget section, since
    Connecticut has no budget source yet."""
    config = copy.deepcopy(load_config("gloucester"))
    config["town"].update(name="Wallingford", state="Connecticut", state_abbr="CT")
    config["sections"] = [s for s in config["sections"] if s["slug"] != "budget"]
    config.pop("finance", None)
    config["schools"] = {"edsight_district": DISTRICT, "district_name": "Wallingford Public Schools"}
    return config


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


# ---- The site ----

@pytest.fixture
def ct_site(tmp_path, data_dir, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    for name in ("finance", "schools"):
        shutil.rmtree(data / name, ignore_errors=True)
    config = wallingford()
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
    assert not (ct_site / "budget").exists()


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
