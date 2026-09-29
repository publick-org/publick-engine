"""The stale-data check: sources that stop updating are reported."""

import json
from datetime import timedelta

from conftest import FETCHED_AT
from pipeline import freshness
from pipeline.config import load_config


def write(path, **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields))


def test_stale_and_missing_sources_are_reported(tmp_path):
    config = load_config("gloucester")
    config["summaries"] = {}
    config["freshness"] = {"summary_grace_days": 2, "sources": [
        {"label": "Fresh", "file": "a.json", "max_days": 2},
        {"label": "Old", "file": "b.json", "max_days": 2},
        {"label": "Missing", "file": "c.json", "max_days": 2},
        {"label": "Other field", "file": "d.json", "field": "generated_at", "max_days": 2},
    ]}
    write(tmp_path / "a.json", updated_at=(FETCHED_AT - timedelta(days=1)).isoformat())
    write(tmp_path / "b.json", updated_at=(FETCHED_AT - timedelta(days=3)).isoformat())
    write(tmp_path / "d.json", generated_at=FETCHED_AT.isoformat())
    rows = {r["label"]: r for r in freshness.check(config, tmp_path, now=FETCHED_AT)}
    assert not rows["Fresh"]["stale"] and not rows["Other field"]["stale"]
    assert rows["Old"]["stale"] and rows["Missing"]["stale"]


def test_report_writes_the_rows(tmp_path, monkeypatch):
    report = tmp_path / "rows.json"
    monkeypatch.setattr("sys.argv", ["freshness", "--town", "gloucester", "--data", str(tmp_path / "none"),
                                     "--report", str(report)])
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert freshness.main() == 1
    rows = json.loads(report.read_text())
    assert rows[0] == {"label": "Meetings calendar", "updated_at": None, "max_days": 2, "stale": True}
    assert all(r["updated_at"] is None for r in rows)


def test_about_page_shows_data_status(site_dir):
    about = (site_dir / "about" / "index.html").read_text()
    assert 'id="data-status"' in about and "Meetings calendar" in about
