"""A town's data/README.md, written from its config and data folder (pipeline/data_readme.py)."""

import json
import shutil
from pathlib import Path

from pipeline import data_readme, states
from pipeline.config import load_config

FIXTURE_DATA = Path(__file__).parent / "fixtures" / "town" / "data"


def data_folder(tmp_path: Path, *files: str) -> Path:
    """A data folder with the fixture's ward file and the files named, each an empty JSON object."""
    data = tmp_path / "data"
    shutil.copytree(FIXTURE_DATA, data)
    for name in files:
        (data / name).parent.mkdir(parents=True, exist_ok=True)
        (data / name).write_text("{}", encoding="utf-8")
    return data


def paths(config: dict, data: Path) -> list[str]:
    return [path for path, _, _ in data_readme.rows(config, data)]


def test_a_row_for_each_kind_of_file_there(tmp_path):
    config = load_config("gloucester")
    data = data_folder(tmp_path, "meetings/meetings.json", "meetings/status.json", "finance/budget.json",
                       "labor/unemployment.json", "run.json")
    listed = paths(config, data)
    assert listed == ["`meetings/meetings.json`", "`meetings/status.json`", "Agendas and minutes", "`finance/budget.json`",
                      "`labor/unemployment.json`", "`static/gloucester-precincts-2022.geojson`", "`run.json`"]
    # Files the folder doesn't have get no row.
    assert not any("311" in p or "schools" in p or "housing" in p for p in listed)


def test_sources_come_from_the_config(tmp_path):
    config = load_config("gloucester")
    data = data_folder(tmp_path, "meetings/meetings.json", "311/status.json", "housing/housing.json")
    sources = {path: source for path, _, source in data_readme.rows(config, data)}
    # The meetings' sources, as the site links them: the city's calendar and Archive Center, then the school board's.
    assert sources["`meetings/meetings.json`"].startswith(
        "The city's [calendar](https://www.gloucester-ma.gov/calendar.aspx) and "
        "[Archive Center](https://www.gloucester-ma.gov/Archive.aspx); ")
    assert "SeeClickFix" in sources["`311/requests.json`, `311/scorecard.json`"]
    assert config["housing"]["census_geo"] in sources["`housing/housing.json`"]
    # The ward file's credit from [seeclickfix], as the 311 and About pages give it.
    assert sources["`static/gloucester-precincts-2022.geojson`"].startswith("[MassGIS (2022)]")
    # Documents kept in the bucket say where.
    contents = {path: c for path, c, _ in data_readme.rows(config, data)}
    assert "`files.publick.org/gloucester-ma/`" in contents["Agendas and minutes"]
    assert "`files.publick.org/gloucester-ma/data/311/`" in contents["`311/requests.json`, `311/scorecard.json`"]


def test_without_storage_documents_are_in_the_folder(tmp_path):
    config = load_config("gloucester")
    config.pop("storage")
    data = data_folder(tmp_path, "meetings/meetings.json")
    contents = {path: c for path, c, _ in data_readme.rows(config, data)}
    assert contents["Agendas and minutes"].endswith("in `meetings/`")


def test_place_word_from_the_census(tmp_path):
    config = load_config("gloucester")
    config["town"].pop("kind", None)
    data = data_folder(tmp_path, "labor/unemployment.json")
    (data / "place.json").write_text(json.dumps({"kind": "town"}), encoding="utf-8")
    contents = {path: c for path, c, _ in data_readme.rows(config, data)}
    assert contents["`labor/unemployment.json`"] == "Monthly unemployment rate for the town, with the statewide rate"


def test_every_state_package_has_its_sources():
    """A state added under pipeline/states/ needs its rows here, or its towns' figures go uncredited."""
    assert set(data_readme.STATE_SOURCES) == set(states.PACKAGES)


def test_written_once_unless_forced(tmp_path):
    config = load_config("gloucester")
    data = data_folder(tmp_path, "meetings/meetings.json")
    path = data_readme.write(config, data)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Data\n") and "| `meetings/meetings.json` |" in text
    # Every row is a row of the table: three cells, whatever its text holds.
    table = [line for line in text.splitlines() if line.startswith("| ")]
    assert all(line.count(" | ") == 2 for line in table[2:])
    path.write_text("Written by hand.\n", encoding="utf-8")
    assert data_readme.write(config, data) is None
    assert path.read_text(encoding="utf-8") == "Written by hand.\n"
    assert data_readme.write(config, data, force=True) == path
    assert path.read_text(encoding="utf-8") == text


def test_capitalized():
    assert data_readme.capitalized("[the City of Bristol (2022)](https://example.org)") == \
        "[The City of Bristol (2022)](https://example.org)"
    assert data_readme.capitalized("the Internet Archive") == "The Internet Archive"
