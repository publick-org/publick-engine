"""Collect the average single-family tax bill from the Massachusetts Division
of Local Services (DLS) Municipal Databank.

Writes data/finance/tax_bill.json with the last several fiscal years. Run by
python -m pipeline.fetch_finance, for a town with a [finance] table.
"""

from __future__ import annotations

import io
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import openpyxl

from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient
from pipeline.rhythms import Part, Rhythm, latest_year, on

REPORT_URL = "https://dls-gw.dor.state.ma.us/reports/rdPage.aspx"
REPORT_PAGE = REPORT_URL + "?rdReport=AverageSingleTaxBill.SingleFamTaxBill_wRange"
YEARS = 5
# Waits, in seconds, before asking again when DLS answers 202 with nothing (dls_get).
REFUSED_WAITS = (30, 60, 120)
# Response headers worth keeping in an error: the server, and anything that says why a request was refused.
REFUSAL_HEADERS = ("Server", "Content-Type", "Content-Length", "Location", "Retry-After", "x-amzn-waf-action",
                   "x-amzn-ErrorType", "x-amzn-RequestId", "X-Cache", "Via")



# DLS adds a fiscal year's figures for each town once its tax rate is approved:
# most by the end of December, the last in spring.
RHYTHM = Rhythm("Average tax bill (Mass. DLS)", "finance/tax_bill.json", "Fetch tax bill", "yearly", (
    Part(latest_year("years", "fiscal_year"), on(2), lambda y: f"Fiscal year {y}"),
))


def report_url(municipality: str, fiscal_year: int) -> str:
    return REPORT_URL + "?" + urlencode({
        "rdReport": "AverageSingleTaxBill.SingleFamTaxBill_wRange",
        "rdReportFormat": "NativeExcel",
        "rdExportTableID": "tblSinglefamtaxbill",
        "rdExcelOutputFormat": "Excel2007",
        "iclMuni": municipality,
        "iclYear": fiscal_year,
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


def parse_workbook(content: bytes) -> dict | None:
    """Read the one data row of the report. None if the year has no certified figures."""
    if not content.startswith(b"PK"):
        raise not_a_workbook(content)
    sheet = openpyxl.load_workbook(io.BytesIO(content), read_only=True).worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    if len(rows) < 2:
        return None
    header = [str(h or "").strip() for h in rows[0]]
    row = dict(zip(header, rows[1]))
    bill = row.get("Single-Family Tax Bill")
    if bill in (None, ""):
        return None
    return {
        "fiscal_year": int(row["Fiscal Year"]),
        "average_bill": int(round(float(bill))),
        "average_value": int(round(float(row["Average Single-Family Value"]))) if row.get("Average Single-Family Value") else None,
        "parcels": int(row["Single-Family Parcels"]) if row.get("Single-Family Parcels") else None,
        "state_rank": int(row["Rank"]) if row.get("Rank") not in (None, "") else None,
    }


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=2.0)


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    municipality = config["finance"]["dls_municipality"]
    # Massachusetts fiscal years start July 1.
    newest = now.year + 1 if now.month >= 7 else now.year
    # Certified years don't change, so only years not saved yet are asked for, and the newest saved one
    # again (in case it was corrected). force asks for every year.
    path = data_dir / "finance" / "tax_bill.json"
    saved = {} if force or not path.exists() else {
        y["fiscal_year"]: y for y in json.loads(path.read_text(encoding="utf-8")).get("years", [])}
    years = dict(saved)
    for fy in range(newest, newest - YEARS - 1, -1):
        if fy in saved and fy != max(saved):
            continue
        if len(years) >= YEARS and fy < min(years):
            break
        record = parse_workbook(dls_get(client, report_url(municipality, fy)))
        if record:
            years[record["fiscal_year"]] = record
    if not years:
        raise FetchError("no tax bill figures returned")
    kept = sorted(years.values(), key=lambda y: y["fiscal_year"])[-YEARS:]
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "source": "Massachusetts Department of Revenue, Division of Local Services, Municipal Databank",
        "source_url": REPORT_PAGE,
        "years": kept,
    }
    save_json(path, data)
    return {"latest": kept[-1]}
