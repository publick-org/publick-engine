"""The Massachusetts DLS exports fetched once for every town (pipeline/states/ma/dls.py), the Subsidized
Housing Inventory and DESE's school figures likewise (pipeline/states/ma/statewide.py), and the network's
statewide step (pipeline.network states)."""

import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from conftest import FIXTURES
from fakes import FakeBudgetDLS, FakeDESE, FakeHousing, FakeResponse, shi_pdf_text
from test_network import make_root

from pipeline import network
from pipeline.config import load_config
from pipeline.http import FetchError
from pipeline.states.ma import budget, dls, housing, schools, statewide, tax_bill

NOW = datetime(2026, 9, 26, 12, tzinfo=ZoneInfo("America/New_York"))
TOWN_PARAMS = ("iclMuni", "iclMuni2")


class StatewideDLS:
    """Serves the saved DLS workbooks for any request (they hold Gloucester's rows), and records each one."""

    def __init__(self):
        self.urls = []
        self.budget = FakeBudgetDLS()

    def get(self, url):
        self.urls.append(url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if q["rdReport"] == tax_bill.REPORT:
            # Only FY2026 has certified figures in this stand-in.
            name = "dls_tax_bill.xlsx" if q["iclYear"] == "2026" else "dls_tax_bill_empty_year.xlsx"
            return FakeResponse((FIXTURES / name).read_bytes())
        if q["rdReport"] == housing.PARCELS_REPORT:
            return FakeResponse((FIXTURES / "housing" / "dls_parcels.xlsx").read_bytes())
        return self.budget.get(url)

    def towns_asked_for(self):
        return [u for u in self.urls if any(p in parse_qs(urlparse(u).query) for p in TOWN_PARAMS)]


class NoRequests:
    def get(self, url):
        raise AssertionError(f"asked DLS for {url}")


def gloucester():
    return {**load_config("gloucester"), "slug": "gloucester"}


def test_refresh_fetches_each_export_once_for_every_town(tmp_path):
    client = StatewideDLS()
    result = dls.refresh(tmp_path, [gloucester(), {**gloucester(), "slug": "another"}], client, NOW)
    # Every export was asked for without a municipality, so DLS returns all of them, and the second
    # town's readers reused the first's.
    assert client.towns_asked_for() == []
    assert result["fetched"] == len(client.urls) == result["exports"]
    store = tmp_path / "ma" / "dls"
    index = json.loads((store / "index.json").read_text())
    assert sorted(index) == sorted(p.name for p in store.glob("*.json") if p.name != "index.json")


def test_refresh_fetches_only_what_is_missing_or_old(tmp_path):
    dls.refresh(tmp_path, [gloucester()], StatewideDLS(), NOW)
    again = StatewideDLS()
    assert dls.refresh(tmp_path, [gloucester()], again, NOW + timedelta(days=3))["fetched"] == 0
    assert again.urls == []
    later = StatewideDLS()
    assert dls.refresh(tmp_path, [gloucester()], later, NOW + timedelta(days=dls.MAX_AGE_DAYS + 1))["fetched"] > 0


def test_towns_read_their_rows_from_the_store(tmp_path, monkeypatch):
    dls.refresh(tmp_path, [gloucester()], StatewideDLS(), NOW)
    monkeypatch.setenv(dls.STORE_ENV, str(tmp_path))
    config = gloucester()
    data = tmp_path / "data"
    tax_bill.run(config, NoRequests(), data, now=NOW, force=True)
    budget.run(config, NoRequests(), data, now=NOW, force=True)
    assert housing.parcels(NoRequests(), config, NOW)["types"]
    # The same figures as asking DLS for the town itself.
    alone = tmp_path / "alone"
    monkeypatch.delenv(dls.STORE_ENV)
    tax_bill.run(config, StatewideDLS(), alone, now=NOW, force=True)
    budget.run(config, StatewideDLS(), alone, now=NOW, force=True)
    for name in ("tax_bill.json", "budget.json"):
        stored, asked = (json.loads((d / "finance" / name).read_text()) for d in (data, alone))
        assert {k: v for k, v in stored.items() if k != "updated_at"} == {k: v for k, v in asked.items() if k != "updated_at"}


def test_a_town_not_in_an_export_gets_no_rows(tmp_path, monkeypatch):
    dls.refresh(tmp_path, [gloucester()], StatewideDLS(), NOW)
    monkeypatch.setenv(dls.STORE_ENV, str(tmp_path))
    rows = dls.table(NoRequests(), tax_bill.REPORT, tax_bill.TABLE, ("iclMuni", "Nowhere"), iclYear=2026)
    assert rows == []
    assert dls.table(NoRequests(), tax_bill.REPORT, tax_bill.TABLE, ("iclMuni", "gloucester"), iclYear=2026)


def test_an_export_missing_from_the_store_is_asked_for_the_town(tmp_path, monkeypatch):
    monkeypatch.setenv(dls.STORE_ENV, str(tmp_path))
    client = StatewideDLS()
    assert dls.table(client, tax_bill.REPORT, tax_bill.TABLE, ("iclMuni", "Gloucester"), iclYear=2026)
    assert client.towns_asked_for() == client.urls


def test_towns_match_by_dls_name_or_dor_code():
    row = {"DOR Code": 107, "Municipality": "Gloucester "}
    assert dls.is_town(row, "Gloucester") and dls.is_town(row, "107") and dls.is_town(row, "0107".lstrip("0"))
    assert not dls.is_town(row, "Malden") and not dls.is_town(row, "165")
    # Some reports label the columns differently (bond ratings).
    assert dls.is_town({"DOR CODE": 107, "Name": "Gloucester"}, "Gloucester")
    assert dls.is_town({"DOR CODE": 107, "Name": "Gloucester"}, "107")


def test_a_refused_refresh_keeps_what_was_fetched(tmp_path):
    class RefusesBudget(StatewideDLS):
        def get(self, url):
            if "ScheduleA" in url:
                raise FetchError("DLS returned 0 bytes that are not a workbook")
            return super().get(url)

    with pytest.raises(FetchError):
        dls.refresh(tmp_path, [gloucester()], RefusesBudget(), NOW)
    index = json.loads((tmp_path / "ma" / "dls" / "index.json").read_text())
    assert index and all(tax_bill.REPORT in k or "ScheduleA" not in k for k in index)
    # The next refresh asks only for what's still missing.
    client = StatewideDLS()
    dls.refresh(tmp_path, [gloucester()], client, NOW)
    assert client.urls and not any(tax_bill.REPORT in u for u in client.urls)


def test_the_network_refreshes_its_states_and_reports_repeated_failures(tmp_path, monkeypatch):
    root = make_root(tmp_path, ())
    (root / "towns" / "gloucester-ma" / "config").mkdir(parents=True)
    (root / "towns" / "gloucester-ma" / "config" / "gloucester.toml").write_text(
        '[town]\nstate_abbr = "MA"\n[finance]\ndls_municipality = "Gloucester"\ndls_code = "107"\n'
        '[site]\ntimezone = "America/New_York"\nuser_agent = "test"\n')
    calls = []

    def refuse(state_dir, configs, client, now):
        calls.append([c["slug"] for c in configs])
        raise FetchError("HTTP 202 with nothing")

    monkeypatch.setattr(dls, "refresh", refuse)
    for day in range(network.STATE_FAILURES):
        status = network.refresh_states(root, NOW + timedelta(days=day))["MA"]
    assert calls[0] == ["gloucester"] and status["failures"] == network.STATE_FAILURES and not status["ok"]
    rows = network.failing_states(root)
    assert rows[0]["folder"] == "MA statewide sources" and "HTTP 202" in rows[0]["problems"][0]

    monkeypatch.setattr(dls, "refresh", lambda state_dir, configs, client, now: {"exports": 3, "fetched": 1})
    assert network.refresh_states(root, NOW)["MA"]["failures"] == 0
    assert network.failing_states(root) == []
    # A run that fetches nothing new, with nothing wrong, leaves the status file alone: nothing to commit.
    status_file = root / "states" / "ma" / "status.json"
    before = status_file.read_text()
    monkeypatch.setattr(dls, "refresh", lambda state_dir, configs, client, now: {"exports": 3, "fetched": 0})
    network.refresh_states(root, NOW + timedelta(days=1))
    assert status_file.read_text() == before


def test_town_steps_are_told_where_the_states_are(tmp_path):
    root = make_root(tmp_path)
    assert network.STATE_DIR_ENV not in network.town_env(root, "salem-ma")
    (root / "states").mkdir()
    assert network.town_env(root, "salem-ma")[network.STATE_DIR_ENV] == str(root / "states")
    assert network.STATE_DIR_ENV == dls.STORE_ENV


# ---- The Subsidized Housing Inventory and DESE's school figures ----

def test_the_inventory_is_read_once_for_every_town(tmp_path, monkeypatch):
    monkeypatch.setattr(housing, "pdf_text", shi_pdf_text)
    client = FakeHousing()
    monkeypatch.setattr(housing, "client", lambda config: client)
    towns = [gloucester(), {**gloucester(), "slug": "another"}]
    assert housing.refresh(tmp_path, towns, NoRequests(), NOW) == {"exports": 1, "fetched": 1}
    assert len(client.urls) == 1
    saved = json.loads((tmp_path / "ma" / housing.SHI_FILE).read_text())
    assert saved["as_of"] == "2025-09-30" and saved["rows"]["Gloucester"]["shi_units"] == 1117
    # Within the week, nothing is asked for again.
    assert housing.refresh(tmp_path, towns, NoRequests(), NOW + timedelta(days=6))["fetched"] == 0
    assert housing.refresh(tmp_path, towns, NoRequests(), NOW + timedelta(days=8))["fetched"] == 1


def test_a_town_reads_its_inventory_row_from_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(housing, "pdf_text", shi_pdf_text)
    monkeypatch.setattr(housing, "client", lambda config: FakeHousing())
    housing.refresh(tmp_path, [gloucester()], NoRequests(), NOW)
    monkeypatch.setenv(dls.STORE_ENV, str(tmp_path))
    found = housing.shi(NoRequests(), gloucester())
    assert found == housing.shi(FakeHousing(), {**gloucester()}) and found["percent"] == 8.04
    # A town the saved inventory doesn't list asks for the PDF itself.
    (tmp_path / "ma" / housing.SHI_FILE).write_text(statewide.dumps({"as_of": "2025-09-30", "census_year": 2020}, {}))
    client = FakeHousing()
    assert housing.shi(client, gloucester())["percent"] == 8.04 and len(client.urls) == 1


def test_school_figures_are_fetched_once_for_every_district(tmp_path, monkeypatch):
    client = FakeDESE()
    towns = [gloucester(), {**gloucester(), "slug": "another"}]
    assert schools.refresh(tmp_path, towns, client, NOW) == {"exports": 4, "fetched": 4}
    assert len(client.urls) == 4 and all("LIKE" in u for u in client.urls)
    assert schools.refresh(tmp_path, towns, NoRequests(), NOW + timedelta(days=1))["fetched"] == 0

    # A town's figures from the store are the ones it would have fetched itself, with no request.
    district = gloucester()["schools"]["district_code"]
    assert district in json.loads((tmp_path / "ma" / "dese" / "graduation.json").read_text())["rows"]
    schools.run(gloucester(), FakeDESE(), tmp_path / "direct", now=NOW)
    monkeypatch.setenv(dls.STORE_ENV, str(tmp_path))
    schools.run(gloucester(), NoRequests(), tmp_path / "stored", now=NOW)
    direct, stored = (json.loads((tmp_path / d / "schools" / "schools.json").read_text())["measures"]
                      for d in ("direct", "stored"))
    assert stored == direct

    # A district the store doesn't have is asked for directly.
    other = {**gloucester(), "schools": {**gloucester()["schools"], "district_code": "01650000"}}
    client = FakeDESE()
    with pytest.raises(FetchError):  # the stand-in has only Gloucester's figures
        schools.run(other, client, tmp_path / "other", now=NOW)
    assert client.urls and "01650000" in client.urls[0]


def test_one_failing_source_does_not_stop_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(dls, "refresh", lambda state_dir, configs, client, now: {"exports": 3, "fetched": 0})
    monkeypatch.setattr(housing, "refresh", lambda *a: (_ for _ in ()).throw(FetchError("HTTP 403")))
    asked = []
    monkeypatch.setattr(schools, "refresh", lambda state_dir, configs, client, now: asked.append(1) or
                        {"exports": 4, "fetched": 4})
    with pytest.raises(FetchError, match="Subsidized Housing Inventory: HTTP 403"):
        statewide.refresh(tmp_path, [gloucester()], NoRequests(), NOW)
    assert asked == [1]
    monkeypatch.setattr(housing, "refresh", lambda state_dir, configs, client, now: {"exports": 1, "fetched": 0})
    assert statewide.refresh(tmp_path, [gloucester()], NoRequests(), NOW) == {"exports": 8, "fetched": 4}
    # A town without the source's table isn't given to it.
    bare = {k: v for k, v in gloucester().items() if k not in ("finance", "schools", "housing")}
    assert statewide.refresh(tmp_path, [bare], NoRequests(), NOW) == {"exports": 0, "fetched": 0}
