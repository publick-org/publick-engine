"""Collect the average single-family tax bill from the Massachusetts Division
of Local Services (DLS) Municipal Databank.

Writes data/finance/tax_bill.json with the last several fiscal years. Run by
python -m pipeline.fetch_finance, for a town with a [finance] table.
"""

from __future__ import annotations

import json
import time  # noqa: F401 (tests pause dls_get's waits through it)
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient
from pipeline.rhythms import Part, Rhythm, latest_year, on
from pipeline.states.ma import dls
from pipeline.states.ma.dls import REFUSED_WAITS, REPORT_URL, dls_get, export_url, not_a_workbook  # noqa: F401

REPORT = "AverageSingleTaxBill.SingleFamTaxBill_wRange"
TABLE = "tblSinglefamtaxbill"
REPORT_PAGE = dls.page_url(REPORT)
YEARS = 5


# DLS adds a fiscal year's figures for each town once its tax rate is approved:
# most by the end of December, the last in spring.
RHYTHM = Rhythm("Average tax bill (Mass. DLS)", "finance/tax_bill.json", "Fetch tax bill", "yearly", (
    Part(latest_year("years", "fiscal_year"), on(2), lambda y: f"Fiscal year {y}"),
))


def report_url(municipality: str, fiscal_year: int) -> str:
    return export_url(REPORT, TABLE, iclMuni=municipality, iclYear=fiscal_year)


def parse_row(row: dict) -> dict | None:
    """A town's row of the report. None if the year has no certified figures."""
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


def parse_workbook(content: bytes) -> dict | None:
    """Read the one data row of a town's report. None if the year has no certified figures."""
    found = dls.rows(content)
    return parse_row(found[0]) if found else None


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
        found = dls.table(client, REPORT, TABLE, ("iclMuni", municipality), iclYear=fy)
        record = parse_row(found[0]) if found else None
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
