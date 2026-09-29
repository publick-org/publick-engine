"""What New Hampshire's own pages (site/states/nh/) need from the build: the downloadable tables behind them."""

from __future__ import annotations

from pathlib import Path

TEMPLATE_GLOBALS: dict = {}


def write_files(out_dir: Path, data: dict) -> None:
    from pipeline.build_site import write_csv

    b = data["budget"]
    if not b:
        return
    folder = out_dir / "budget" / "data"
    write_csv(folder / "tax-rates.csv",
              ["tax_year", "total", "city", "county", "state_education", "local_education", "valuation", "commitment"],
              [[r["tax_year"], r["total"], r["municipal"], r["county"], r["state_education"], r["local_education"],
                r["valuation"], r["commitment"]] for r in b["rates"]])
    if b.get("city_budget"):
        years = b["city_budget"]["years"]
        amounts = [{**y["lines"], **{r["label"]: r["amount"] for r in y["summary"]}} for y in years]
        labels = list(dict.fromkeys(label for a in amounts for label in a))
        write_csv(folder / "city-budget.csv", ["fiscal_year", *labels],
                  [[y["fiscal_year"], *(a.get(label) for label in labels)] for y, a in zip(years, amounts)])
