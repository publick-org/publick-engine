"""What differs from state to state goes through the town's state's package (pipeline/states/)."""

import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeHousing

from pipeline import build_site, fetch_finance, fetch_housing, states
from pipeline.config import load_config

NOW = datetime(2026, 9, 26, 12, tzinfo=ZoneInfo("America/New_York"))


def in_state(config: dict, code: str, name: str) -> dict:
    town = copy.deepcopy(config)
    town["town"].update(state_abbr=code, state=name)
    return town


def test_a_town_gets_its_states_sources():
    config = load_config("gloucester")
    state = states.for_town(config)
    assert state.code == "MA" and state.templates == "ma"
    assert set(state.sources) == set(states.KINDS)
    assert state.source("tax_bill", config).module == "pipeline.states.ma.tax_bill"
    del config["finance"]
    assert state.source("tax_bill", config) is None and state.source("schools", config)


def test_a_state_without_a_package_has_no_state_sources():
    town = in_state(load_config("gloucester"), "NY", "New York")
    state = states.for_town(town)
    assert state.name == "New York" and state.sources == {} and state.housing_module() is None
    assert all(state.source(kind, town) is None for kind in states.KINDS)


def test_state_tables_are_checked_against_the_towns_state():
    config = load_config("gloucester")
    del config["finance"]["dls_code"]
    with pytest.raises(SystemExit, match=r"\[finance\].*needs dls_code for Massachusetts's budget figures"):
        states.check(config)


def test_fetch_skips_a_state_without_the_source(tmp_path, capsys):
    town = in_state(load_config("gloucester"), "NY", "New York")
    assert states.main(town, "tax_bill", tmp_path) == 0
    assert "no tax bill source for New York yet; skipping" in capsys.readouterr().out
    with pytest.raises(LookupError):
        fetch_finance.run(town, None, tmp_path)
    assert not (tmp_path / "finance").exists()


def test_fetch_skips_a_town_without_the_table(tmp_path, capsys):
    config = load_config("gloucester")
    del config["schools"]
    assert states.main(config, "schools", tmp_path) == 0
    assert "no [schools] in config/gloucester.toml; skipping" in capsys.readouterr().out


def test_housing_outside_a_state_with_its_own_figures(tmp_path):
    town = in_state(load_config("gloucester"), "NY", "New York")
    client = FakeHousing()
    result = fetch_housing.run(town, client, tmp_path, now=NOW)
    assert result["problems"] == []
    assert not any("mass.gov" in u or "Parcel_counts" in u for u in client.urls)
    h = json.loads((tmp_path / "housing" / "housing.json").read_text())
    assert h["permits"] and h["acs"] and "shi" not in h and "parcels" not in h


def test_a_section_the_state_cant_fill_stops_the_build(tmp_path, monkeypatch, data_dir):
    town = in_state(load_config("gloucester"), "NY", "New York")
    monkeypatch.setattr(build_site, "load_config", lambda slug: town)
    with pytest.raises(SystemExit, match="The schools section needs New York's school figures"):
        build_site.build("gloucester", tmp_path / "site", data_dir=data_dir)


def test_every_state_package_has_its_pages():
    for code, package in states.PACKAGES.items():
        state = states.for_town({"town": {"state_abbr": code, "state": ""}})
        assert state.code == code
        for section, kind in states.SECTIONS.items():
            if kind in state.sources:
                assert (build_site.STATES_DIR / state.templates / f"{section}.html").exists(), (code, section)
        for source in state.sources.values():
            module = source.load()
            assert callable(module.run) and callable(module.client), source.module
