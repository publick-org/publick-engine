"""The Massachusetts Division of Local Services (DLS) Municipal Databank: its
report exports, and a statewide store so a network of towns asks DLS for each
export once, for every municipality, instead of once per town.

The tax bill, budget, and parcel readers (tax_bill.py, budget.py, housing.py)
ask for their rows through table(). On their own, as a single town's site
runs them, table() asks DLS for the town's rows. When PUBLICK_STATE_DIR names
a folder (the network repository's states/, set for each town's steps by
pipeline.network), table() reads the rows from the export saved there, for
every municipality, and picks the town's; it asks DLS itself only if the
export isn't saved.

The network fills the store with refresh(), once per run, before the towns'
steps: it runs the readers for each of the network's Massachusetts towns, and
each export they ask for that isn't saved, or was saved more than MAX_AGE_DAYS
ago, is fetched once without a municipality (DLS then returns all 351) and
saved. So a run where nothing is due asks DLS for nothing, and adding a town
costs no requests for the exports the others already use.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import openpyxl

from pipeline.files import write_atomic
from pipeline.http import FetchError

REPORT_URL = "https://dls-gw.dor.state.ma.us/reports/rdPage.aspx"
# Waits, in seconds, before asking again when DLS answers 202 with nothing (dls_get).
REFUSED_WAITS = (30, 60, 120)
# Response headers worth keeping in an error: the server, and anything that says why a request was refused.
REFUSAL_HEADERS = ("Server", "Content-Type", "Content-Length", "Location", "Retry-After", "x-amzn-waf-action",
                   "x-amzn-ErrorType", "x-amzn-RequestId", "X-Cache", "Via")

# The store: <PUBLICK_STATE_DIR>/ma/dls/, one JSON file of rows per export, and index.json with when each
# was fetched. Exports older than MAX_AGE_DAYS are fetched again by refresh().
STORE_ENV = "PUBLICK_STATE_DIR"
MAX_AGE_DAYS = 7
# Who the network's statewide requests say they're from.
USER_AGENT = "publick.org (+https://publick.org/status/)"

# Set by refresh(): the store being filled, and the exports it has used so far.
_refresh: dict | None = None


def page_url(report: str) -> str:
    return f"{REPORT_URL}?rdReport={report}"


def export_url(report: str, table: str, **params) -> str:
    return REPORT_URL + "?" + urlencode({
        "rdReport": report, "rdReportFormat": "NativeExcel", "rdExportTableID": table,
        "rdExcelOutputFormat": "Excel2007", **params,
    })


def not_a_workbook(content: bytes) -> FetchError:
    """An error that shows the start of what DLS sent back, to tell a block page from an error page."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content[:3000].decode("utf-8", "replace"))).strip()
    return FetchError(f"DLS returned {len(content)} bytes that are not a workbook: {text[:200]!r}")


def dls_get(client, url: str) -> bytes:
    """A DLS export. The report builds the file, then redirects to a download link;
    a fast client can reach the link before the file is written and get an empty
    reply, so the link is tried again after a pause.

    DLS's gateway also sometimes answers the report itself with HTTP 202 and
    nothing else, as it did for GitHub's runners on 2026-09-29, while serving
    the same report to other addresses. That's asked again after longer
    waits (REFUSED_WAITS); if it keeps refusing, the error lists the headers
    that say why (a load balancer or bot filter names itself in them)."""
    for wait in (*REFUSED_WAITS, None):
        response = client.get(url)
        for attempt in range(3):
            download = getattr(response, "url", "") or ""
            if response.content.startswith(b"PK") or "rdDownload" not in download:
                break
            time.sleep(2 * (attempt + 1))
            response = client.get(download)
        if response.content.startswith(b"PK"):
            return response.content
        if getattr(response, "status_code", None) != 202 or response.content or wait is None:
            break
        print(f"DLS answered 202 with nothing; asking again in {wait} seconds.", flush=True)
        time.sleep(wait)
    headers = getattr(response, "headers", {}) or {}
    detail = ", ".join(f"{k}: {headers.get(k)}" for k in REFUSAL_HEADERS if headers.get(k))
    status = getattr(response, "status_code", "?")
    raise FetchError(f"{not_a_workbook(response.content)} (HTTP {status} from {getattr(response, 'url', url)}; {detail})")


def rows(content: bytes) -> list[dict]:
    """Workbook rows as dicts keyed by the header row (the first row with 'DOR Code')."""
    if not content.startswith(b"PK"):
        raise not_a_workbook(content)
    sheet = openpyxl.load_workbook(io.BytesIO(content), read_only=True).worksheets[0]
    table = list(sheet.iter_rows(values_only=True))
    start = next((i for i, r in enumerate(table) if str(r[0] or "").strip().upper() == "DOR CODE"), None)
    if start is None:
        return []
    header = [str(h or "").strip() for h in table[start]]
    return [dict(zip(header, r)) for r in table[start + 1:] if str(r[0] or "").strip() not in ("", "Totals:")]


def store_dir() -> Path | None:
    """The statewide store, when there is one: the one being refreshed, or PUBLICK_STATE_DIR's."""
    if _refresh:
        return _refresh["dir"]
    root = os.environ.get(STORE_ENV)
    return Path(root) / "ma" / "dls" if root else None


def export_key(report: str, table_id: str, params: dict) -> str:
    """The file an export is saved in: its report, table, and parameters, without the municipality."""
    parts = [report, table_id, *(f"{k}={v}" for k, v in sorted(params.items()))]
    return re.sub(r"[^A-Za-z0-9._=-]+", "_", ".".join(parts)) + ".json"


# The columns that name a row's municipality, as DLS's reports label them.
CODE_COLUMNS = ("DOR Code", "DOR CODE")
NAME_COLUMNS = ("Municipality", "Name")


def is_town(row: dict, town: str) -> bool:
    """A row for the town, named by its DLS name or its DOR code (all digits)."""
    if town.isdigit():
        code = next((row[c] for c in CODE_COLUMNS if row.get(c) not in (None, "")), "")
        return str(code).strip().zfill(3) == town.zfill(3)
    name = next((row[c] for c in NAME_COLUMNS if row.get(c) not in (None, "")), "")
    return str(name).strip().casefold() == town.strip().casefold()


def load_index(store: Path) -> dict:
    path = store / "index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_rows(store: Path, key: str, found: list[dict]) -> None:
    """One row per line, in DLS's order, so a refresh that changes nothing changes no lines."""
    store.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False, default=str) for r in found]
    write_atomic(store / key, "[\n" + ",\n".join(lines) + "\n]\n")


def table(client, report: str, table_id: str, town: tuple[str, str] | None, **params) -> list[dict]:
    """The rows of a DLS export for one municipality, or every row when town is None.

    town is the report's municipality parameter and its value (the town's DLS
    name or DOR code, as the report wants it)."""
    store = store_dir()
    if store is not None:
        key = export_key(report, table_id, params)
        path = store / key
        found = None
        if _refresh is not None:
            fetched = _refresh["index"].get(key)
            fresh = fetched and datetime.fromisoformat(fetched) > _refresh["now"] - timedelta(days=MAX_AGE_DAYS)
            if fresh and path.exists():
                found = json.loads(path.read_text(encoding="utf-8"))
            else:
                found = rows(dls_get(client, export_url(report, table_id, **params)))
                save_rows(store, key, found)
                _refresh["index"][key] = _refresh["now"].isoformat(timespec="seconds")
                _refresh["fetched"] += 1
            _refresh["used"].add(key)
        elif path.exists():
            found = json.loads(path.read_text(encoding="utf-8"))
        if found is not None:
            return found if town is None else [r for r in found if is_town(r, town[1])]
    town_params = {town[0]: town[1]} if town else {}
    return rows(dls_get(client, export_url(report, table_id, **town_params, **params)))


@contextlib.contextmanager
def refreshing(store: Path, now: datetime):
    """Within this, table() fills the store (see refresh)."""
    global _refresh
    _refresh = {"dir": store, "now": now, "index": load_index(store), "used": set(), "fetched": 0}
    try:
        yield _refresh
    finally:
        _refresh = None


def refresh(state_dir: Path, configs: list[dict], client, now: datetime | None = None) -> dict:
    """Fill the store in state_dir/ma/dls/ for these towns' configs: each export their tax bill,
    budget, and parcel readers use, fetched once for every municipality when it isn't saved or is
    older than MAX_AGE_DAYS. Once they've all run, exports none of them uses any more are removed.

    If DLS refuses partway, what was fetched is kept (and the index says when), and the error is
    raised: the next run fetches only what's still missing."""
    from pipeline.states.ma import budget, housing, tax_bill

    now = now or datetime.now(timezone.utc)
    store = state_dir / "ma" / "dls"
    finished = False
    with refreshing(store, now) as state, tempfile.TemporaryDirectory() as scratch:
        try:
            for config in configs:
                data = Path(scratch) / config["slug"]
                tax_bill.run(config, client, data, now=now, force=True)
                budget.run(config, client, data, now=now, force=True)
                housing.parcels(client, config, now)
            finished = True
        finally:
            used, index = state["used"], state["index"]
            if finished:
                for path in store.glob("*.json"):
                    if path.name != "index.json" and path.name not in used:
                        path.unlink()
                index = {k: v for k, v in index.items() if k in used}
            store.mkdir(parents=True, exist_ok=True)
            write_atomic(store / "index.json", json.dumps(dict(sorted(index.items())), indent=2) + "\n")
    return {"exports": len(used), "fetched": state["fetched"]}
