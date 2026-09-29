"""Figure sources on their own rhythm: behind only when a newer period is overdue, checked only when due."""

import json
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pipeline import freshness, network, rhythms, update
from pipeline.config import load_config
from pipeline.states.ma import tax_bill as ma_tax_bill

TZ = ZoneInfo("America/New_York")


def at(y, m, d):
    return datetime(y, m, d, 9, 0, tzinfo=TZ)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def tax_bill(data_dir, latest_year, checked):
    write(data_dir / "finance" / "tax_bill.json",
          {"updated_at": checked.isoformat(), "years": [{"fiscal_year": y} for y in range(latest_year - 2, latest_year + 1)]})


def test_add_months_keeps_the_day_within_the_month():
    assert rhythms.add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)
    assert rhythms.add_months(date(2027, 2, 1), -2) == date(2026, 12, 1)


def test_yearly_source_is_behind_only_after_its_usual_date_and_the_grace(tmp_path):
    # FY2027's tax bill usually appears by February 1, 2027; with two months' grace, it's behind from April 1.
    tax_bill(tmp_path, 2026, at(2027, 3, 1))
    row = rhythms.row(ma_tax_bill.RHYTHM, tmp_path, at(2027, 3, 31))
    assert not row["stale"] and row["latest"] == "Fiscal year 2026"
    assert row["next"] == "Fiscal year 2027 usually by February 1, 2027"
    row = rhythms.row(ma_tax_bill.RHYTHM, tmp_path, at(2027, 4, 1))
    assert row["stale"] and "Fiscal year 2027 is later than usual" in row["behind"]
    # A longer grace, from the town's [freshness] grace_months.
    assert not rhythms.row(ma_tax_bill.RHYTHM, tmp_path, at(2027, 4, 1), grace_months=3)["stale"]


def test_a_source_with_no_data_is_behind(tmp_path):
    row = rhythms.row(ma_tax_bill.RHYTHM, tmp_path, at(2026, 9, 29))
    assert row["stale"] and row["behind"] == "no data yet"


def test_yearly_source_is_checked_monthly_and_weekly_near_its_usual_date(tmp_path):
    tax_bill(tmp_path, 2026, at(2026, 9, 1))
    # Far from February: monthly.
    assert not rhythms.due(ma_tax_bill.RHYTHM, tmp_path, at(2026, 9, 20))[0]
    assert rhythms.due(ma_tax_bill.RHYTHM, tmp_path, at(2026, 10, 1))[0]
    # From two months before the usual date: weekly.
    tax_bill(tmp_path, 2026, at(2026, 12, 1))
    assert not rhythms.due(ma_tax_bill.RHYTHM, tmp_path, at(2026, 12, 5))[0]
    assert rhythms.due(ma_tax_bill.RHYTHM, tmp_path, at(2026, 12, 8))[0]


def test_a_source_never_checked_is_due(tmp_path):
    assert rhythms.due(ma_tax_bill.RHYTHM, tmp_path, at(2026, 9, 29)) == (True, "")


def test_monthly_source_reads_the_latest_month(tmp_path):
    from pipeline import fetch_labor
    write(tmp_path / "labor" / "unemployment.json", {"updated_at": at(2026, 9, 29).isoformat(),
          "months": [{"year": 2026, "month": 6}, {"year": 2026, "month": 7}]})
    row = rhythms.row(fetch_labor.RHYTHM, tmp_path, at(2026, 9, 29))
    assert row["latest"] == "July 2026 rate" and row["next"] == "August 2026 rate usually by October 10, 2026"
    assert rhythms.row(fetch_labor.RHYTHM, tmp_path, at(2026, 12, 10))["stale"]


def test_town_gets_its_states_rhythms_and_the_national_ones():
    labels = [r.label for r in rhythms.for_town(load_config("gloucester"))]
    assert labels == ["Average tax bill (Mass. DLS)", "City budget (Mass. DLS)", "School figures (DESE)",
                      "Unemployment rate (BLS)", "Housing figures"]


def test_freshness_replaces_a_config_row_for_a_file_a_rhythm_covers(tmp_path):
    config = load_config("gloucester")
    config["freshness"] = {"sources": [
        {"label": "Old tax bill row", "file": "finance/tax_bill.json", "max_days": 7},
        {"label": "Meetings", "file": "meetings/status.json", "max_days": 2},
    ]}
    tax_bill(tmp_path, 2026, at(2026, 9, 1))
    rows = {r["label"]: r for r in freshness.check(config, tmp_path, now=at(2026, 9, 29))}
    assert "Old tax bill row" not in rows
    # A month since the last check, but FY2026 is the latest published: not behind.
    assert not rows["Average tax bill (Mass. DLS)"]["stale"]
    assert rows["Meetings"]["stale"]


def test_three_failed_checks_in_a_row_are_reported_but_not_behind(tmp_path):
    config = load_config("gloucester")
    tax_bill(tmp_path, 2026, at(2026, 9, 1))
    failed = [{"name": "Fetch tax bill", "ok": False, "error": "exited with status 1"}]
    for day in (27, 28):
        rhythms.record_checks(tmp_path, failed, at(2026, 9, day).isoformat())
    row = next(r for r in freshness.check(config, tmp_path, now=at(2026, 9, 29)) if r["label"].startswith("Average"))
    assert row["failing"] == 0
    rhythms.record_checks(tmp_path, failed, at(2026, 9, 29).isoformat())
    row = next(r for r in freshness.check(config, tmp_path, now=at(2026, 9, 29)) if r["label"].startswith("Average"))
    assert row["failing"] == 3 and not row["stale"]
    rhythms.record_checks(tmp_path, [{"name": "Fetch tax bill", "ok": True}], at(2026, 9, 30).isoformat())
    assert rhythms.load_checks(tmp_path)["Fetch tax bill"]["failures"] == 0


@pytest.fixture
def ran(monkeypatch):
    """Each step's command records that it ran and succeeds."""
    names = []

    def command(source, town):
        names.append(source.name)
        return [sys.executable, "-c", "pass"]

    monkeypatch.setattr(update, "command", command)
    return names


def test_update_skips_a_figure_step_that_is_not_due(tmp_path, ran):
    tax_bill(tmp_path, 2026, at(2026, 9, 28))
    result = update.run("gloucester", "figures", config=load_config("gloucester"), data_dir=tmp_path,
                        now=at(2026, 9, 29))
    skipped = {s["name"]: s for s in result["steps"] if s.get("skipped")}
    assert "Fetch tax bill" in skipped and "Fetch tax bill" not in ran
    assert skipped["Fetch tax bill"]["ok"] and skipped["Fetch tax bill"]["cadence"] == "yearly"
    # Sources without saved data are due; permits has no rhythm and always runs.
    assert "Fetch school figures" in ran and "Fetch building permits" in ran
    assert json.loads((tmp_path / "checks.json").read_text())["Fetch school figures"]["failures"] == 0

    ran.clear()
    update.run("gloucester", "figures", config=load_config("gloucester"), data_dir=tmp_path, force=True,
               now=at(2026, 9, 29))
    assert "Fetch tax bill" in ran


def test_run_report_flags_failing_checks(tmp_path):
    record = {"folder": "gloucester-ma", "ok": True, "deployed": True, "stale": False, "steps": [], "update": None,
              "failing": ["Average tax bill (Mass. DLS)"]}
    (tmp_path / "gloucester-ma.json").write_text(json.dumps(record))
    text, ok = network.report(tmp_path)
    assert not ok and "checks failing" in text and "1 need attention" in text


class Refused:
    """DLS answering 202 with nothing, then (optionally) the report."""

    def __init__(self, refusals, sheet=None):
        self.refusals, self.sheet, self.urls = refusals, sheet, []

    def get(self, url):
        self.urls.append(url)
        refused = len(self.urls) <= self.refusals

        class Response:
            content = b"" if refused else self.sheet
            status_code = 202 if refused else 200
            headers = {"Content-Length": "0", "x-amzn-waf-action": "challenge"} if refused else {}
        Response.url = url
        return Response()


def test_dls_refusal_is_asked_again_after_a_wait(monkeypatch):
    from fakes import FIXTURES
    waits = []
    monkeypatch.setattr(ma_tax_bill.time, "sleep", waits.append)
    sheet = (FIXTURES / "dls_tax_bill.xlsx").read_bytes()
    client = Refused(2, sheet)
    assert ma_tax_bill.dls_get(client, "https://dls-gw.dor.state.ma.us/reports/rdPage.aspx?x=1") == sheet
    assert waits == list(ma_tax_bill.REFUSED_WAITS[:2])


def test_dls_that_keeps_refusing_says_why(monkeypatch):
    from pipeline.http import FetchError
    monkeypatch.setattr(ma_tax_bill.time, "sleep", lambda s: None)
    client = Refused(99)
    with pytest.raises(FetchError, match=r"HTTP 202 .*x-amzn-waf-action: challenge"):
        ma_tax_bill.dls_get(client, "https://dls-gw.dor.state.ma.us/reports/rdPage.aspx?x=1")
    assert len(client.urls) == len(ma_tax_bill.REFUSED_WAITS) + 1


def test_tax_bill_asks_only_for_years_not_saved(tmp_path):
    from fakes import FIXTURES, FakeResponse
    from pipeline import fetch_finance
    sheet = (FIXTURES / "dls_tax_bill.xlsx").read_bytes()
    empty = (FIXTURES / "dls_tax_bill_empty_year.xlsx").read_bytes()

    class DLS:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            return FakeResponse(sheet if "iclYear=2026" in url else empty)

    now = at(2026, 9, 29)
    fetch_finance.run(load_config("gloucester"), DLS(), tmp_path, now=now)
    saved = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())
    saved["years"] = [{**saved["years"][0], "fiscal_year": y} for y in (2022, 2023, 2024, 2025)] + saved["years"]
    (tmp_path / "finance" / "tax_bill.json").write_text(json.dumps(saved))
    client = DLS()
    fetch_finance.run(load_config("gloucester"), client, tmp_path, now=now)
    # FY2027 (not certified yet) and FY2026 (the newest saved), not the four years before.
    assert sorted(u.split("iclYear=")[1] for u in client.urls) == ["2026", "2027"]
    kept = json.loads((tmp_path / "finance" / "tax_bill.json").read_text())["years"]
    assert [y["fiscal_year"] for y in kept] == [2022, 2023, 2024, 2025, 2026]
