"""The helper for adding a town (pipeline/new_town.py), from saved pages: what it finds, the
config it writes (which loads and builds with no data), and where it stops."""

import json
import re
import shutil
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
import requests
from conftest import BUILT_AT

from pipeline import build_site, civicplus, new_town
from pipeline import config as config_mod
from pipeline.http import FetchError
from pipeline.states.ma import lookups

FIXTURES = Path(__file__).parent / "fixtures"
SAVED = FIXTURES / "new_town"
TODAY = date(2026, 10, 13)
BASE = "https://www.beverlyma.gov"


class Page:
    def __init__(self, body, url: str = "", status: int = 200, headers: dict | None = None):
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", errors="replace")
        self.url, self.status_code, self.headers = url, status, headers or {}

    def json(self):
        return json.loads(self.text)


class FakeClient:
    """Serves saved pages by address; any other address is a page the site doesn't have."""

    def __init__(self, pages: list):
        self.pages, self.urls, self.resets = pages, [], 0

    def get(self, url):
        self.urls.append(url)
        for pattern, page in self.pages:
            if re.search(pattern, url):
                if isinstance(page, Exception):
                    raise page
                return page(url) if callable(page) else page
        raise FetchError(f"{url}: HTTP 404", 404)


def saved(name: str, url: str = "") -> Page:
    return Page((SAVED / name).read_bytes() if (SAVED / name).exists() else (FIXTURES / name).read_bytes(), url)


def calendar_page() -> str:
    """A calendar page whose boxes name the calendars in the saved month's list view."""
    listed = {e["calendar_id"]: e["calendar"] for e in
              civicplus.parse_list((FIXTURES / "civicplus_list_2026-10.html").read_text(), BASE)}
    return "<ul>" + "".join(f'<li><label for="chkCalendarID_{cid}">\n<input type="checkbox" id="chkCalendarID_{cid}" '
                            f'name="chkCalendarID" value="{cid}">\n{name}\n</label></li>' for cid, name in listed.items()) + "</ul>"


def city_pages(home: str | None = None) -> list:
    """Beverly's homepage and stylesheet, Malden's Agenda Center listing, a month of a city
    calendar, a scanned minutes file, and the state and federal sources' answers for Beverly."""
    return [
        (r"^https://www\.beverlyma\.gov/?$", Page(home or (SAVED / "home_civicplus.html").read_text(), BASE + "/")),
        (r"/AgendaCenter/Search/", saved("agendacenter_search.html")),
        (r"/Calendar\.aspx$", Page(calendar_page())),
        (r"Calendar\.aspx\?CID=0&view=list", saved("civicplus_list_2026-10.html")),
        (r"/AgendaCenter/ViewFile/Minutes/", saved("civicplus_agenda_scanned.pdf")),
        (r"beverlyma\.gov/[-0-9]+\.css$", saved("theme.css")),
        (r"seeclickfix\.com/api/v2/places", saved("seeclickfix_places_beverly.json")),
        (r"la\.area$", saved("la.area")),
        (r"censusreporter\.org/1\.0/geo/latest/16000US2505595", saved("censusreporter_beverly.json")),
        (r"tigerweb\.geo\.census\.gov/.*/4/query", saved("tigerweb_beverly.json")),
        (r"educationtocareer\.data\.mass\.gov", saved("dese_beverly.json")),
        (r"WardsPrecincts2022/MapServer/0/query", saved("massgis_beverly.json")),
    ]


@pytest.fixture
def network(tmp_path):
    """A network repository with one live town (the engine's Gloucester) and a DLS report."""
    root = tmp_path / "network"
    shutil.copytree(FIXTURES / "town" / "config", root / "towns" / "gloucester-ma" / "config")
    (root / "states" / "ma" / "dls").mkdir(parents=True)
    for report in SAVED.glob("AverageSingleTaxBill.*.json"):
        shutil.copy(report, root / "states" / "ma" / "dls" / report.name)
    return root


def beverly(tmp_path, network, pages=None):
    town = new_town.NewTown("Beverly", "MA", BASE, today=TODAY)
    out = tmp_path / "beverly-ma"
    code = new_town.run(town, FakeClient(pages or city_pages()), network, out, checks=False)
    return town, out, code


def test_helper_writes_a_town_from_saved_pages(tmp_path, network):
    town, out, code = beverly(tmp_path, network)
    assert code == 0 and not town.stopped
    text = (out / "config" / "beverly.toml").read_text()
    config = tomllib.loads(text)
    # Step 1: the Agenda Center, the calendar whose events are meetings, and the scans.
    meetings = config["meetings"]
    assert meetings["agenda_center"] == {"base_url": BASE, "since": "2026-01-01"}
    assert meetings["civicplus"]["calendars"] == ["Boards and Commissions Meetings"]
    assert meetings["archive_name"] == "city's Agenda Center"
    assert any(a.what == "Transcription" for a in town.answers)
    # Step 2: the codes, each from its source.
    assert config["finance"] == {"dls_municipality": "Beverly", "dls_code": "30"}
    assert config["schools"] == {"district_code": "00300000", "district_name": "Beverly Public Schools",
                                 "portal": "https://educationtocareer.data.mass.gov"}
    assert config["labor"]["local_series"] == "LAUCT250559500000003"
    assert config["housing"]["bps_place"] == "05595" and config["housing"]["census_geo"] == "16000US2505595"
    # Step 3: the ward file, named in [officials].
    assert config["officials"]["wards_file"] == "beverly-precincts-2022.geojson"
    assert (out / "data" / "static" / "beverly-precincts-2022.geojson").exists()
    # Step 5: what is the town's own, the network's settings from the town started from, and colors.
    assert config["site"]["domain"] == "beverly-ma.publick.org"
    assert config["site"]["contact_email"] == "beverly-ma@publick.org"
    assert config["analytics"]["prefix"] == config["storage"]["prefix"] == "beverly-ma"
    assert config["summaries"]["since"] == "2026-07-01"
    assert config["site"]["colors"] == {"primary": "#005287", "primary_dark": "#041e41", "primary_soft": "#e6eef3",
                                        "accent": "#803e3e"}
    assert [s["label"] for s in config["freshness"]["sources"]] == ["Meetings (Agenda Center)"]


def test_helper_leaves_officials_and_the_digest_to_a_person(tmp_path, network):
    town, out, _ = beverly(tmp_path, network)
    text = (out / "config" / "beverly.toml").read_text()
    config = tomllib.loads(text)
    # Officials: a skeleton only, never filled in, and no Officials page until it is.
    assert "bodies" not in config["officials"] and "next_election" not in config["officials"]
    assert "officials" not in [s["slug"] for s in config["sections"]]
    assert re.search(r"# To fill in: next_election", text)
    # A town joins the digest two full weeks after launch.
    assert "digest" not in config
    # Everything left is marked, and listed in the report, with the committees the council posts.
    left = new_town.left_to_fill_in(text)
    assert len(left) == len(town.todo) > 5
    report = new_town.report(town, town.todo)
    assert "## Left for a person" in report
    assert any('"Finance Committee Agenda"' in todo for _, todo in town.todo)
    assert all(text in report for _, text in town.todo)


def test_helper_config_loads_and_builds_with_no_data(tmp_path, network, monkeypatch):
    _, out, _ = beverly(tmp_path, network)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", out / "config")
    config = config_mod.load_config("beverly")
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    site = tmp_path / "site"
    build_site.build("beverly", site, data_dir=out / "data", now=BUILT_AT, share_cards=False)
    assert (site / "meetings" / "index.html").exists() and not (site / "officials").exists()


def test_helper_finds_a_civicclerk_portal(tmp_path, network):
    home = '<html><body><a href="https://manchesternh.portal.civicclerk.com/">Agendas</a></body></html>'
    pages = [(r"^https://www\.manchesternh\.gov/?$", Page(home, "https://www.manchesternh.gov/")),
             (r"api\.civicclerk\.com/v1/Events\?\$filter", saved("civicclerk_events_1.json")),
             (r"api\.civicclerk\.com", saved("civicclerk_events_2.json"))]
    town = new_town.NewTown("Manchester", "NH", "https://www.manchesternh.gov", today=TODAY)
    out = tmp_path / "manchester-nh"
    assert new_town.run(town, FakeClient(pages), network, out, checks=False) == 0
    config = tomllib.loads((out / "config" / "manchester.toml").read_text())
    assert config["meetings"]["civicclerk"]["api_url"] == "https://manchesternh.api.civicclerk.com/v1"
    assert config["meetings"]["governing_body"] == "Board of Mayor and Aldermen"
    assert "Board of Mayor and Aldermen" in config["meetings"]["boards"]
    # New Hampshire's own codes wait for its lookups: marked, not guessed.
    assert "finance" not in config and "schools" not in config


def test_helper_stops_at_a_browser_check(tmp_path, network):
    pages = [(r"beverlyma\.gov", new_town.BrowserCheck(BASE + "/"))]
    town, out, code = beverly(tmp_path, network, pages)
    assert code == 2 and "browser check" in town.stopped and "never work around" in town.stopped
    assert not out.exists()
    assert "**Stopped:**" in new_town.report(town, [])


def test_a_browser_check_is_seen_whatever_the_status():
    challenge = requests.Response()
    challenge.status_code, challenge._content, challenge.url = 403, b"<title>Just a moment...</title>", BASE
    with pytest.raises(new_town.BrowserCheck):
        new_town.Client._check(challenge)
    challenge.status_code, challenge.headers["cf-mitigated"] = 200, "challenge"
    with pytest.raises(new_town.BrowserCheck):
        new_town.Client._check(challenge)
    page = requests.Response()
    page.status_code, page._content = 404, b"<title>Page not found</title>"
    new_town.Client._check(page)


def test_helper_stops_at_a_platform_the_engine_does_not_read(tmp_path, network):
    home = '<html><head><meta name="Generator" content="Drupal 10"></head><body>Govstack</body></html>'
    town, out, code = beverly(tmp_path, network, [(r"^https://www\.beverlyma\.gov/?$", Page(home, BASE + "/"))])
    assert code == 2 and "Govstack" in town.stopped and "Drupal" in town.stopped and "reader" in town.stopped
    assert not out.exists()


def test_checks_run_in_a_copy_and_leave_data_empty(tmp_path, network):
    town, out, _ = beverly(tmp_path, network)
    commands = []

    def runner(command, cwd, env, **kwargs):
        commands.append((command, Path(cwd), env))
        if "pipeline.fetch_meetings" in command:
            (Path(cwd) / "data" / "meetings").mkdir(parents=True)
            (Path(cwd) / "data" / "meetings" / "meetings.json").write_text(json.dumps({"a": {"body": "City Council"}}))
            assert "2026-09-15" in (Path(cwd) / "config" / "beverly.toml").read_text()  # four weeks, not since
        return type("Done", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    new_town.run_checks(town, out, full=False, runner=runner)
    assert [c["name"] for c in town.checks] == ["Build with no data", "Fetch meetings (four weeks)", "Build with meetings",
                                               "Site checks", "Meetings shown as one", "Share image"]
    assert all(c["ok"] for c in town.checks) and town.site["boards"] == Counter({"City Council": 1})
    site_checks = next(c for c in commands if "pytest" in c[0])
    assert "not test_nothing_left_to_fill_in" in site_checks[0] and site_checks[2]["DOCUMENTS_LOCAL"] == "1"
    assert all(c[1] != out for c in commands[:5]) and commands[5][2]["PUBLICK_TOWN_DIR"] == str(out.resolve())
    assert sorted(p.name for p in (out / "data").rglob("*") if p.is_file()) == ["beverly-precincts-2022.geojson"]
    assert "2026-01-01" in (out / "config" / "beverly.toml").read_text()


# ---- Each lookup ---------------------------------------------------------------------------

def test_ward_file_is_laid_out_as_every_massachusetts_towns():
    text, wards = lookups.ward_file("Beverly", json.loads((SAVED / "massgis_beverly.json").read_text()))
    data = json.loads(text)
    assert wards and data["name"] == "Beverly wards and precincts (2022)"
    assert data["source"] == ("MassGIS, Wards and Precincts (2022), https://arcgisserver.digital.mass.gov/arcgisserver/"
                              "rest/services/AGOL/WardsPrecincts2022/FeatureServer/0")
    assert data["features"][0]["properties"] == {"ward": "1", "precinct": "1", "district": "1-1", "population_2020": 3454}
    assert text == json.dumps(data, separators=(",", ":"))  # compact, as the network's files are
    coordinates = data["features"][0]["geometry"]["coordinates"][0]
    assert all(len(str(x).split(".")[1]) <= 6 for point in coordinates for x in point)


def test_a_town_without_wards_has_voting_precincts():
    saved_file = json.loads((SAVED / "massgis_beverly.json").read_text())
    for f in saved_file["features"]:
        f["properties"]["WARD"] = None
    text, wards = lookups.ward_file("Methuen", saved_file)
    data = json.loads(text)
    assert not wards and data["name"] == "Methuen voting precincts (2022)"
    assert all(f["properties"]["ward"] == f["properties"]["precinct"] for f in data["features"])
    assert lookups.ward_file("Nowhere", {"features": []}) is None


def test_district_is_the_towns_own():
    rows = json.loads((SAVED / "dese_lowell.json").read_text())
    assert lookups.district("Lowell", "160", rows)["district_code"] == "01600000"
    assert lookups.district("Lowell", "999", rows) is None
    assert "Sea%27%27s" in lookups.districts_url("Manchester-by-the-Sea's")


def test_dls_name_and_code(tmp_path):
    shutil.copy(next(SAVED.glob("AverageSingleTaxBill.*.json")), tmp_path)
    assert lookups.dls_town("lowell", tmp_path)["dls_code"] == "160"
    assert lookups.dls_town("Nowhere", tmp_path) is None


def test_bls_area_is_a_city_or_a_town():
    text = (SAVED / "la.area").read_text()
    assert new_town.bls_area("Beverly", "MA", text) == ("CT2505595000000", "Beverly city, MA")
    assert new_town.bls_area("Andover", "MA", text)[0].startswith("CS25")
    assert new_town.bls_area("Lowell", "ME", text)[1] == "Lowell town, ME"
    assert new_town.bls_area("Nowhere", "MA", text) is None


def test_calendar_names_from_the_calendar_page():
    names = new_town.calendar_names((SAVED / "calendar_picker.html").read_text())
    assert names["23"] == "City Meetings" and "Veterans Services" in names.values() and len(names) == 6


# Each town's most used colors in its website's stylesheets, as counted on 2026-10-13, and the
# colors its config was given by hand.
STYLESHEETS = {
    "Lowell": ({"#0098f7": 54, "#0f3c5b": 54, "#215367": 36, "#437d6a": 31, "#26fbb6": 14, "#ff0000": 13, "#1c818d": 13},
               {"primary": "#215367", "primary_dark": "#0f3c5b", "primary_soft": "#e9eef0", "accent": "#437d6a"}),
    "Waltham": ({"#0098f7": 54, "#0d204a": 41, "#7d0818": 23, "#0d41b5": 16, "#1c2d53": 14, "#ff0000": 13, "#cfe0e3": 13},
                {"primary": "#0d41b5", "primary_dark": "#0d204a", "primary_soft": "#e7ecf8", "accent": "#7d0818"}),
    "Beverly": ({"#041e41": 196, "#0098f7": 54, "#005287": 36, "#264036": 35, "#450909": 26, "#803e3e": 20, "#8d7349": 14},
                {"primary": "#005287", "primary_dark": "#041e41", "accent": "#803e3e"}),
}


@pytest.mark.parametrize("town", STYLESHEETS)
def test_colors_from_the_stylesheet(town):
    counts, chosen = STYLESHEETS[town]
    picked, how = new_town.pick_colors(Counter(counts))
    assert {k: picked[k] for k in chosen} == chosen
    assert all(new_town.contrast(v) >= 4.5 for k, v in picked.items() if k != "primary_soft")


def test_a_color_too_light_is_darkened_until_it_passes():
    # Methuen's orange (3.1 to 1), darkened only as far as it must be (its config has #a95c0a, 5.0 to 1).
    picked, how = new_town.pick_colors(Counter({"#362b80": 99, "#dc780d": 13}))
    assert picked["primary"] == "#362b80" and picked["primary_soft"] == "#ebeaf2"
    assert picked["accent"] == "#b2610b" and 4.5 <= new_town.contrast(picked["accent"]) < 4.6
    assert new_town.contrast(picked["primary_dark"]) >= 15
    assert any("darkened" in h for h in how)
    assert new_town.pick_colors(Counter({"#0098f7": 54, "#333333": 40})) is None


def test_a_platform_the_engine_reads_but_the_helper_does_not_set_up(tmp_path, network):
    home = '<html><body><div id="dnn_ctr3737_ModuleContent">DotNetNuke</div></body></html>'
    town, out, code = beverly(tmp_path, network, [(r"^https://www\.beverlyma\.gov/?$", Page(home, BASE + "/"))])
    assert code == 2 and "[meetings.dnn]" in town.stopped and "by hand" in town.stopped and "reader" not in town.stopped


def test_a_census_place_without_figures_is_left_to_a_person():
    # BLS lists Methuen under place 40675, which Census Reporter has no figures for (its own is "Methuen Town").
    town = new_town.NewTown("Methuen", "MA", "https://www.methuen.gov", today=TODAY)
    housing = {"bps_state": "25", "bps_place": "40675", "census_geo": "16000US2540675"}
    client = FakeClient([(r"censusreporter", saved("censusreporter_methuen.json")), (r"tigerweb", saved("tigerweb_beverly.json"))])
    new_town.check_census(town, client, housing)
    assert housing["census_geo_unknown"]
    town.codes["housing"] = housing
    config = new_town.Config()
    new_town.config_codes(config, town)
    text = config.text()
    assert "census_geo = " not in text.replace("# census_geo = ", "") and new_town.left_to_fill_in(text)
    assert tomllib.loads(text)["housing"]["bps_place"] == "40675"
