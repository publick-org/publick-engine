"""A second town builds from its own config alone: sections it doesn't list are
left out, even when data for them is on disk, and every link still resolves."""

import copy

import pytest
from conftest import BUILT_AT
from test_site import parse
from test_site import test_internal_links_resolve as check_links

from pipeline import build_site
from pipeline.config import configured, load_config
from pipeline.seeclickfix import street_address

OPTIONAL = ["seeclickfix", "finance", "labor", "schools", "housing", "permits", "summaries", "freshness",
            "analytics", "participation", "glossary", "drive_meetings"]


def meetings_only(config: dict) -> dict:
    town = copy.deepcopy(config)
    for table in OPTIONAL:
        town.pop(table, None)
    town["slug"] = "newtown"
    town["site"].update(name="Newtown Record", name_prefix="Newtown ", name_suffix="Record", domain="newtown.example",
                        masthead="An independent guide to city government in Newtown",
                        repo_url="https://github.com/example/newtown")
    town["town"]["name"] = "Newtown"
    town["site"].pop("network", None)
    town["sections"] = [s for s in town["sections"] if s["slug"] in ("meetings", "about")]
    return town


@pytest.fixture(scope="module")
def new_town_site(tmp_path_factory, data_dir, monkeypatch_module):
    town = meetings_only(load_config("gloucester"))
    monkeypatch_module.setattr(build_site, "load_config", lambda slug: town)
    out = tmp_path_factory.mktemp("newtown") / "site"
    build_site.build("newtown", out, data_dir=data_dir, now=BUILT_AT)
    return out


@pytest.fixture(scope="module")
def monkeypatch_module():
    with pytest.MonkeyPatch.context() as mp:
        yield mp


def test_only_listed_sections_are_built(new_town_site):
    for slug in ("311", "schools", "budget", "housing"):
        assert not (new_town_site / slug).exists(), slug
    assert (new_town_site / "meetings" / "index.html").exists()
    assert (new_town_site / "about" / "index.html").exists()
    assert (new_town_site / "streets" / "index.html").exists()
    assert (new_town_site / "CNAME").read_text().strip() == "newtown.example"


def test_pages_name_the_new_town_only(new_town_site):
    home = (new_town_site / "index.html").read_text()
    assert "Part of " not in home  # a town outside any network names none
    assert "Newtown" in home
    assert "/311/" not in home and "tax bill" not in home.lower()
    about = (new_town_site / "about" / "index.html").read_text()
    assert "SeeClickFix" not in about and "Division of Local Services" not in about
    assert "goatcounter" not in about.lower()
    # The street lookup offers only what the town has.
    assert "Agenda items on any street" in home
    streets = (new_town_site / "streets" / "index.html").read_text()
    assert 'data-sources="meetings"' in streets and "311" not in streets


def test_gloucester_street_lookup_names_every_source(site_dir):
    assert "Agenda items, building permits, and 311 requests on any street" in (site_dir / "index.html").read_text()
    assert 'data-sources="meetings permits requests"' in (site_dir / "streets" / "index.html").read_text()


def test_new_town_links_resolve(new_town_site):
    check_links(new_town_site, sorted(new_town_site.rglob("*.html")))
    for path in new_town_site.rglob("*.html"):
        assert parse(path).tags.count("h1") == 1, path


def test_fetchers_skip_sources_the_town_does_not_have(capsys):
    town = meetings_only(load_config("gloucester"))
    assert configured(town, "meetings")
    assert not configured(town, "seeclickfix")
    assert "No [seeclickfix] in config/newtown.toml; skipping." in capsys.readouterr().out


def test_town_name_is_removed_from_addresses():
    assert street_address("12 Essex St Salem, Massachusetts, 01970", "Salem") == "12 Essex St"
    assert street_address("12 Essex St Salem MA 01970", "Salem") == "12 Essex St"
    # Another town's name in a street name is kept.
    assert street_address("5 Gloucester St Salem, MA", "Salem") == "5 Gloucester St"



def indicators_only(config: dict) -> dict:
    """A town with no meetings source yet, like Manchester: numbers and housing only."""
    town = meetings_only(config)
    for table in ("meetings", "archive", "storage"):
        town.pop(table, None)
    town["labor"], town["housing"] = config["labor"], {k: v for k, v in config["housing"].items() if k != "shi_url"}
    town["sections"] = [s for s in config["sections"] if s["slug"] in ("housing", "about")]
    return town


@pytest.fixture(scope="module")
def no_meetings_site(tmp_path_factory, data_dir):
    town = indicators_only(load_config("gloucester"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: town)
        out = tmp_path_factory.mktemp("nomeetings") / "site"
        build_site.build("newtown", out, data_dir=data_dir, now=BUILT_AT)
    return out


def test_town_without_meetings_leaves_them_out(no_meetings_site):
    for path in ("meetings", "streets", "311", "feed.xml"):
        assert not (no_meetings_site / path).exists(), path
    assert (no_meetings_site / "housing" / "index.html").exists()
    home = (no_meetings_site / "index.html").read_text()
    assert "feed.xml" not in home and "street-box" not in home and "Coming up" not in home
    assert "Unemployment rate" in home
    about = (no_meetings_site / "about" / "index.html").read_text()
    assert "Archive Center" not in about and "RSS" not in about and "Subsidized Housing Inventory" not in about
    housing = (no_meetings_site / "housing" / "index.html").read_text()
    assert "#affordable" not in housing and "affordable housing" not in housing


def test_town_without_meetings_links_resolve(no_meetings_site):
    check_links(no_meetings_site, sorted(no_meetings_site.rglob("*.html")))
    for path in no_meetings_site.rglob("*.html"):
        assert parse(path).tags.count("h1") == 1, path



def test_town_static_files_replace_the_engines(tmp_path, data_dir, monkeypatch):
    """A town's site/static/ is laid over the engine's: its own icon wins, its
    share image is added, and the page links the versions it actually serves."""
    town = indicators_only(load_config("gloucester"))
    monkeypatch.setattr(build_site, "load_config", lambda slug: town)
    static = tmp_path / "town_static"
    (static / "share").mkdir(parents=True)
    (static / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (static / "share" / "newtown.png").write_bytes(b"png")
    out = tmp_path / "site"
    build_site.build("newtown", out, data_dir=data_dir, now=BUILT_AT, town_static=static)
    assert (out / "static" / "favicon.svg").read_text() == '<svg xmlns="http://www.w3.org/2000/svg"/>'
    assert (out / "static" / "css" / "site.css").exists()
    assert "/static/share/newtown.png?v=" in (out / "index.html").read_text()


def test_share_image_names_the_street_lookup_only_when_built():
    from pipeline.make_share_image import share_html
    assert "Your street" in share_html(load_config("gloucester"))
    assert "Your street" not in share_html(indicators_only(load_config("gloucester")))
