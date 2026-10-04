"""Connecticut's average single-family tax bill, calculated by Publick.

Connecticut doesn't publish an average bill, so Publick calculates one, as for
New Hampshire: the average assessed value of the town's single-family homes, from
the statewide Parcel and CAMA file on data.ct.gov, times the town's mill rate
(dollars per $1,000 of assessed value) from the Office of Policy and Management
(OPM). Connecticut assesses property at 70% of its market value, and the bill is
before exemptions and credits.

Each year's parcel file is collected in the spring from the town assessors'
records, which are those of the grand list of the October before: the 2026 file
has the values of the October 2025 grand list, taxed in fiscal year 2027 (July
2026 to June 2027). So file year N is paired with fiscal year N + 1's mill rate.
Before a pair is used, the file's total for the town is checked against OPM's
net real property grand list for that October (VALUE_CHECK): a revaluation or a
file from another year puts it far outside.

The single-family homes are the parcels whose state use code is one of
[finance] single_family_use. Towns code their parcels differently ("101",
"1010", or only "100" for every home), so the codes are the town's own, as in
its rows of the parcel file. Years already saved are kept and not asked for
again, except the newest. Writes data/finance/tax_bill.json. Run by
python -m pipeline.fetch_finance.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.fetch_meetings import save_json
from pipeline.http import FetchError, PoliteClient
from pipeline.i18n import N_, _
from pipeline.rhythms import Part, Rhythm, latest_year, on
from pipeline.states.ct import opendata
from pipeline.states.ct.opendata import number, quoted

# The single-family codes most towns use, when the config doesn't list the town's.
SINGLE_FAMILY = ("101", "1010")
# The parcel file's total for the town, over OPM's net real property grand list. The file includes exempt
# property, which the grand list leaves out, so it's a little over 1 (Wallingford's are 1.16 to 1.19).
VALUE_CHECK = (1.0, 1.35)
YEARS_KEPT = 8


# OPM publishes the year's parcel file in September, after the fiscal year's mill rates.
RHYTHM = Rhythm(N_("Tax bill (calculated)"), "finance/tax_bill.json", "Fetch tax bill", "yearly", (
    Part(latest_year("years", "fiscal_year"), on(11, years_after=-1), lambda y: _("Fiscal year {year}").format(year=y)),
))


def client(config: dict) -> PoliteClient:
    return PoliteClient(config["site"]["user_agent"], delay=1.0, timeout=90)


def mill_rate(row: dict) -> float | None:
    """The town's rate on real estate and personal property. OPM filled mill_rate through fiscal year 2020, then
    mill_rate_real_personal. A blank or a rate under 1 is a missing figure (a few towns' rows have 0)."""
    rate = number(row.get("mill_rate_real_personal")) or number(row.get("mill_rate"))
    return rate if rate and rate >= 1 else None


def mill_rates(client, town: str, code: str) -> dict[int, float]:
    """{fiscal year: mill rate} for the town itself. Its fire and tax districts have rows of their own, named
    after it ("Wallingford - Fire District"), so the town's row is the one with its exact name."""
    rows = opendata.query(client, opendata.MILL_RATES, where=f"town_code={int(code)} AND municipality={quoted(town)}")
    return {int(r["fiscal_year"]): rate for r in rows if (rate := mill_rate(r)) is not None}


def parcel_stats(client, dataset: str, code: str, uses: tuple[str, ...] | None = None) -> dict:
    """Count, total, and average assessed value of the town's parcels in one year's file (with uses, only those)."""
    where = f"town_id={int(code)} AND assessed_total > 0"
    if uses:
        where += f" AND state_use IN ({', '.join(quoted(u) for u in uses)})"
    rows = opendata.query(client, dataset, select="count(*) AS count, sum(assessed_total) AS total, "
                                                  "avg(assessed_total) AS average", where=where)
    found = rows[0] if rows else {}
    if not number(found.get("count")):
        return {"count": 0, "total": None, "average": None}
    return {"count": int(number(found["count"])), "total": number(found["total"]), "average": number(found["average"])}


def grand_list(client, code: str) -> dict[int, float]:
    """{grand list year: net real property} for the town, from OPM."""
    rows = opendata.query(client, opendata.GRAND_LIST, select="year, net_real_property", where=f"town_code={int(code)}")
    return {int(r["year"]): value for r in rows if (value := number(r.get("net_real_property")))}


def method(fiscal_year: int, parcels: int) -> str:
    return (f"The average assessed value of {parcels:,} single-family homes in the state's {fiscal_year - 1} parcel "
            f"file, times the fiscal year {fiscal_year} mill rate. Connecticut assesses homes at 70% of market "
            "value. It's the bill before exemptions and credits.")


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    fin = config["finance"]
    town, code = fin["opm_town"], str(fin["opm_code"])
    uses = fin.get("single_family_use", SINGLE_FAMILY)
    # One code may be written without a list: "1010", not ["1010"].
    uses = (str(uses),) if isinstance(uses, (str, int)) else tuple(str(u) for u in uses)
    path = data_dir / "finance" / "tax_bill.json"
    saved = {} if force or not path.exists() else {
        y["fiscal_year"]: y for y in json.loads(path.read_text(encoding="utf-8")).get("years", [])}
    years = dict(saved)

    rates = mill_rates(client, town, code)
    if not rates:
        raise FetchError(f"no mill rates for {town} (OPM code {code})")
    files = opendata.cama_datasets(client)
    lists = None
    checked, kept = {}, []
    for file_year in sorted(files):
        fiscal_year = file_year + 1
        if fiscal_year not in rates or (fiscal_year in saved and fiscal_year != max(saved)):
            continue
        everything = parcel_stats(client, files[file_year], code)
        if not everything["count"]:
            continue
        lists = lists if lists is not None else grand_list(client, code)
        net_real = lists.get(file_year - 1)
        ratio = everything["total"] / net_real if net_real else None
        checked[fiscal_year] = ratio and round(ratio, 3)
        if ratio is None or not VALUE_CHECK[0] <= ratio <= VALUE_CHECK[1]:
            print(f"::warning::{town}'s {file_year} parcel file adds up to "
                  f"{'no grand list' if ratio is None else f'{ratio:.2f} times its grand list'} of October "
                  f"{file_year - 1}, outside {VALUE_CHECK[0]}–{VALUE_CHECK[1]}; fiscal year {fiscal_year} "
                  + ("kept as saved." if fiscal_year in years else "left out."))
            kept.append(fiscal_year)
            continue
        homes = parcel_stats(client, files[file_year], code, uses)
        if not homes["count"]:
            raise FetchError(f"no parcels with state use {', '.join(uses)} for {town} in the {file_year} parcel file; "
                             "set [finance] single_family_use to the town's single-family codes")
        rate = rates[fiscal_year]
        years[fiscal_year] = {
            "fiscal_year": fiscal_year, "period": f"Fiscal year {fiscal_year}",
            "average_bill": round(homes["average"] * rate / 1000), "average_value": round(homes["average"]),
            "parcels": homes["count"], "rate": rate, "parcel_file": file_year,
            "calculated": method(fiscal_year, homes["count"]),
        }
    if not years:
        raise FetchError(f"no year with both a parcel file and a mill rate for {town}")
    kept_years = [years[y] for y in sorted(years)][-YEARS_KEPT:]
    newest_file = kept_years[-1].get("parcel_file", max(files))
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "source": "Calculated by Publick from the Connecticut Office of Policy and Management's mill rates "
                  "and the statewide Parcel and CAMA file",
        "source_url": opendata.page(opendata.MILL_RATES),
        "parcels_url": opendata.page(files.get(newest_file, files[max(files)])),
        "years": kept_years,
    }
    save_json(path, data)
    return {"latest": kept_years[-1]["fiscal_year"], "average_bill": kept_years[-1]["average_bill"],
            "value_ratios": checked, **({"left_out": kept} if kept else {})}
