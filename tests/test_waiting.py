"""What a town's meetings readers leave for a later run (pipeline/waiting.py), and the total a new town's first
fetches run again until it's 0."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import config, waiting

ENGINE = Path(__file__).resolve().parent.parent


@pytest.fixture
def data_dir(tmp_path):
    meetings = tmp_path / "meetings"
    meetings.mkdir()
    # Holyoke's first ProudCity run: 60 documents saved, the rest of the year's posts left for later runs.
    (meetings / "proudcity_status.json").write_text(json.dumps(
        {"meetings_waiting": 397, "waiting_newest": "2026-11-10", "to_read": {"1": {}}}))
    (meetings / "wordpress_status.json").write_text(json.dumps({"documents_waiting": 41, "waiting_newest": None}))
    (meetings / "finalsite_status.json").write_text(json.dumps({"posts_waiting": 12, "waiting_newest": "2026-09-01"}))
    # Caught up, and a run's own status with no count: nothing waiting.
    (meetings / "page_status.json").write_text(json.dumps({"documents_waiting": 0, "waiting_newest": None}))
    (meetings / "status.json").write_text(json.dumps({"failed_calendars": [], "errors": []}))
    # A recording waiting to be transcribed isn't a document waiting.
    (meetings / "recordings_status.json").write_text(json.dumps({"waiting": ["telvue-1"], "recordings_waiting": 3}))
    return tmp_path


def test_each_reader_with_something_waiting_is_counted_with_its_unit(data_dir):
    assert waiting.readers(data_dir) == {
        "finalsite_status": {"count": 12, "unit": "posts", "newest": "2026-09-01"},
        "proudcity_status": {"count": 397, "unit": "meetings", "newest": "2026-11-10"},
        "wordpress_status": {"count": 41, "unit": "documents", "newest": None},
    }


def test_the_total_adds_every_reader_and_unit(data_dir):
    assert waiting.total(data_dir) == 397 + 41 + 12


def test_nothing_is_waiting_once_every_reader_has_caught_up(data_dir):
    for name in ("proudcity", "wordpress", "finalsite"):
        path = data_dir / "meetings" / f"{name}_status.json"
        status = json.loads(path.read_text())
        path.write_text(json.dumps({k: 0 if k.endswith("_waiting") else v for k, v in status.items()}))
    assert waiting.readers(data_dir) == {} and waiting.total(data_dir) == 0


def test_a_town_with_no_meetings_folder_yet_has_nothing_waiting(tmp_path):
    assert waiting.total(tmp_path) == 0


def test_a_status_file_that_cant_be_read_counts_nothing(data_dir):
    (data_dir / "meetings" / "wordpress_status.json").write_text("{not json")
    (data_dir / "meetings" / "finalsite_status.json").write_text("[]")
    (data_dir / "meetings" / "page_status.json").write_text(json.dumps({"documents_waiting": True}))
    assert waiting.total(data_dir) == 397


def test_the_command_prints_the_total_alone_on_stdout_and_each_reader_on_stderr(data_dir, capsys):
    assert waiting.main(["--data", str(data_dir)]) == 0
    out, err = capsys.readouterr()
    assert out == "450\n"
    assert err.splitlines() == ["finalsite: 12 posts waiting", "proudcity: 397 meetings waiting",
                                "wordpress: 41 documents waiting"]


def test_the_command_reads_the_folder_it_is_given_without_a_town_config(data_dir, tmp_path_factory):
    """As add-town.yml runs it: the status files alone, copied from main, with no town config anywhere."""
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    result = subprocess.run([sys.executable, "-m", "pipeline.waiting", "--data", str(data_dir)], capture_output=True,
                            text=True, check=True, cwd=elsewhere, env={"PYTHONPATH": str(ENGINE)})
    assert result.stdout == "450\n"


def test_the_command_reads_the_town_folders_data_by_default(data_dir, monkeypatch, capsys):
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    assert waiting.main([]) == 0
    assert capsys.readouterr().out == "450\n"
