"""What Massachusetts's own pages (site/states/ma/) need from the build: helpers for their templates, and the
downloadable tables behind the budget page."""

from __future__ import annotations

from pathlib import Path


def reserve_rows(b: dict) -> list[tuple[int, tuple]]:
    """[(fiscal_year, (free_cash, stabilization))], oldest first; None where not reported."""
    free = {y["fiscal_year"]: y["amount"] for y in b["free_cash"]}
    stab = {y["fiscal_year"]: y["amount"] for y in b["stabilization"]}
    return [(y, (free.get(y), stab.get(y))) for y in sorted(set(free) | set(stab))]


TEMPLATE_GLOBALS = {"reserve_rows": reserve_rows}


def write_files(out_dir: Path, data: dict) -> None:
    if data["budget"]:
        write_budget_csvs(out_dir / "budget" / "data", data["budget"])


def write_budget_csvs(folder: Path, b: dict) -> None:
    """Downloadable tables behind the budget page. Amounts are in dollars."""
    from pipeline.build_site import write_csv

    functions = list(b["spending"][-1]["functions"]) if b["spending"] else []
    write_csv(folder / "spending.csv", ["fiscal_year", "total", *functions],
              [[y["fiscal_year"], y["total"], *(y["functions"].get(f) for f in functions)] for y in b["spending"]])
    sources = list(b["revenue"][-1]["sources"]) if b["revenue"] else []
    write_csv(folder / "revenue.csv", ["fiscal_year", "total", *sources],
              [[y["fiscal_year"], y["total"], *(y["sources"].get(s) for s in sources)] for y in b["revenue"]])
    write_csv(folder / "levy.csv", ["fiscal_year", "levy", "max_allowable_levy", "unused_levy_capacity", "levy_ceiling", "assessed_value"],
              [[y["fiscal_year"], y["levy"], y["max_levy"], y["excess_capacity"], y["levy_ceiling"], y["assessed_value"]]
               for y in b["levy"]])
    write_csv(folder / "reserves.csv", ["fiscal_year", "free_cash", "stabilization_fund"],
              [[y, *rv] for y, rv in reserve_rows(b)])
