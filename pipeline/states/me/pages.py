"""What Maine's own pages (site/states/me/) need from the build: the downloadable tables behind them."""

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
              ["tax_year", "tax_rate", "state_median", "certified_ratio", "commitment", "taxable_valuation"],
              [[r["tax_year"], r["rate"], r["state_median"], r["certified_ratio"], r["commitment"], r["valuation"]]
               for r in b["rates"]])
    bill = data.get("tax_bill")
    if bill and bill.get("years"):
        write_csv(folder / "tax-bill.csv", ["tax_year", "average_value", "tax_rate", "average_bill", "parcels"],
                  [[y["tax_year"], y["average_value"], y["rate"], y["average_bill"], y["parcels"]] for y in bill["years"]])
