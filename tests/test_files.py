"""Data files are written whole or not at all, so a step stopped mid-write leaves the last good file."""

import json
from pathlib import Path

import pytest

from pipeline.files import TEMP_SUFFIX, write_atomic


def test_writes_text_and_bytes_creating_the_folder(tmp_path):
    path = tmp_path / "data" / "meetings" / "meetings.json"
    write_atomic(path, '{"é": 1}\n')
    assert path.read_text(encoding="utf-8") == '{"é": 1}\n'
    write_atomic(path, b"%PDF-1.7")
    assert path.read_bytes() == b"%PDF-1.7"
    assert list(path.parent.iterdir()) == [path]


def test_a_write_stopped_partway_leaves_the_last_whole_file(tmp_path, monkeypatch):
    path = tmp_path / "requests.json"
    write_atomic(path, json.dumps({"requests": [1, 2]}))
    write_bytes = Path.write_bytes

    def stopped(self, data):
        write_bytes(self, data[: len(data) // 2])
        raise KeyboardInterrupt  # as when update.py stops a step at its timeout

    monkeypatch.setattr(Path, "write_bytes", stopped)
    with pytest.raises(KeyboardInterrupt):
        write_atomic(path, json.dumps({"requests": [1, 2, 3]}))
    monkeypatch.undo()
    assert json.loads(path.read_text(encoding="utf-8")) == {"requests": [1, 2]}

    # The next write replaces the stray half-written file too.
    write_atomic(path, json.dumps({"requests": [1, 2, 3]}))
    assert json.loads(path.read_text(encoding="utf-8")) == {"requests": [1, 2, 3]}
    assert not path.with_name(path.name + TEMP_SUFFIX).exists()
