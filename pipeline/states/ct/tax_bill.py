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

A town may phase in a revaluation's increases over up to five years (Connecticut General
Statutes 12-62c): the grand list, and the tax bills, then carry each parcel's assessment before
the revaluation plus a share of its increase, while the parcel file carries the full new
assessment, so its total is far over the grand list until the last year. [finance] phase_in
names the revaluation's grand list year and the number of years, from the town's own account
of its phase-in: Hamden's four years after the October 2024 revaluation, a quarter of each
increase added a year, a decrease not phased in. For a year of the phase-in, each parcel's value
is its assessment in the parcel file of the revaluation's year (the grand list before it; the
parcels matched by their pid) plus that share of any increase; a parcel not in that file, or
assessed lower, keeps its new assessment. The check against the grand list, and the average,
use those values.

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


def phase_in(config: dict) -> dict | None:
    """The town's [finance] phase_in, {"revaluation": grand list year, "years": 2 to 5}, or None."""
    value = config.get("finance", {}).get("phase_in")
    if value is None:
        return None
    where = f"[finance] phase_in in config/{config.get('slug', 'the town')}.toml"
    if not (isinstance(value, dict) and set(value) == {"revaluation", "years"}
            and isinstance(value["revaluation"], int) and 2000 <= value["revaluation"] <= 2100
            and isinstance(value["years"], int) and 2 <= value["years"] <= 5):
        raise SystemExit(f"{where} must be {{ revaluation = <the revaluation's grand list year>, years = <2 to 5> }}, "
                         "like { revaluation = 2024, years = 4 }.")
    return value


def check(config: dict) -> None:
    """The tax bill's settings in [finance], checked when the town's config is loaded."""
    phase_in(config)


def phase_in_year(phase: dict | None, file_year: int) -> int | None:
    """Which year of the phase-in a parcel file's grand list (October of file_year - 1) is, 1 for the
    revaluation's own, or None when its assessments aren't phased in (before the revaluation, or from
    the last year on, when they're the full new assessments)."""
    if not phase:
        return None
    year = file_year - phase["revaluation"]
    return year if 1 <= year < phase["years"] else None


def assessments(client, dataset: str, code: str) -> dict[str, tuple[str, float]]:
    """{parcel id: (state use, assessed value)} for the town's assessed parcels in one year's file."""
    rows = opendata.query_all(client, dataset, order="pid", select="pid, state_use, assessed_total",
                              where=f"town_id={int(code)} AND assessed_total > 0")
    return {str(r["pid"]): (str(r.get("state_use") or ""), number(r["assessed_total"]))
            for r in rows if r.get("pid") is not None and number(r.get("assessed_total"))}


def phased_stats(new: dict, before: dict, share: float, uses: tuple[str, ...]) -> tuple[dict, dict]:
    """(everything, homes) as parcel_stats gives them, from each parcel's phased-in value: its value
    before the revaluation plus share of any increase. A parcel without a value before, or assessed
    lower, keeps its new value."""
    values, homes = [], []
    for pid, (use, value) in new.items():
        old = before.get(pid, (None, None))[1]
        phased = old + share * (value - old) if old and value > old else value
        values.append(phased)
        if use in uses:
            homes.append(phased)

    def stats(found):
        return ({"count": len(found), "total": sum(found), "average": sum(found) / len(found)} if found
                else {"count": 0, "total": None, "average": None})
    return stats(values), stats(homes)


def grand_list(client, code: str) -> dict[int, float]:
    """{grand list year: net real property} for the town, from OPM."""
    rows = opendata.query(client, opendata.GRAND_LIST, select="year, net_real_property", where=f"town_code={int(code)}")
    return {int(r["year"]): value for r in rows if (value := number(r.get("net_real_property")))}


def method(fiscal_year: int, parcels: int, phased: dict | None = None) -> str:
    """How the year's bill was calculated, as its page says it (site/states/ct/budget.html). phased is
    the year's phase_in record, for a year of a revaluation's phase-in."""
    text = (f"The average assessed value of {parcels:,} single-family homes in the state's {fiscal_year - 1} parcel "
            f"file, times the fiscal year {fiscal_year} mill rate. Connecticut assesses homes at 70% of market "
            "value. It's the bill before exemptions and credits.")
    if phased:
        text += (f" Assessments are phased in over {phased['years']} years after the revaluation of October "
                 f"{phased['revaluation']}, as on the tax bills: each home's value here is its assessment before the "
                 f"revaluation, from the state's {phased['before_file']} parcel file, plus {phased['percent']}% of any "
                 f"increase (year {phased['year']} of {phased['years']}). A decrease isn't phased in.")
    return text


def run(config: dict, client, data_dir: Path, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(ZoneInfo(config["site"]["timezone"]))
    fin = config["finance"]
    town, code = fin["opm_town"], str(fin["opm_code"])
    uses = fin.get("single_family_use", SINGLE_FAMILY)
    # One code may be written without a list: "1010", not ["1010"].
    uses = (str(uses),) if isinstance(uses, (str, int)) else tuple(str(u) for u in uses)
    phase = phase_in(config)
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
        year_of_phase = phase_in_year(phase, file_year)
        phased = None
        if year_of_phase:
            # The parcel file of the revaluation's year has the assessments before it.
            before_file = phase["revaluation"]
            if before_file not in files:
                print(f"::warning::{town}'s {file_year} parcel file is in its phase-in, but the state's "
                      f"{before_file} parcel file, with the assessments before the revaluation, isn't in the "
                      f"catalog; fiscal year {fiscal_year} " + ("kept as saved." if fiscal_year in years else "left out."))
                kept.append(fiscal_year)
                continue
            phased = {"revaluation": phase["revaluation"], "years": phase["years"], "year": year_of_phase,
                      "percent": round(100 * year_of_phase / phase["years"]), "before_file": before_file}
            everything, homes = phased_stats(assessments(client, files[file_year], code),
                                             assessments(client, files[before_file], code),
                                             year_of_phase / phase["years"], uses)
        else:
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
        if not phased:
            homes = parcel_stats(client, files[file_year], code, uses)
        if not homes["count"]:
            raise FetchError(f"no parcels with state use {', '.join(uses)} for {town} in the {file_year} parcel file; "
                             "set [finance] single_family_use to the town's single-family codes")
        rate = rates[fiscal_year]
        years[fiscal_year] = {
            "fiscal_year": fiscal_year, "period": f"Fiscal year {fiscal_year}",
            "average_bill": round(homes["average"] * rate / 1000), "average_value": round(homes["average"]),
            "parcels": homes["count"], "rate": rate, "parcel_file": file_year,
            "calculated": method(fiscal_year, homes["count"], phased),
            **({"phase_in": phased} if phased else {}),
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
