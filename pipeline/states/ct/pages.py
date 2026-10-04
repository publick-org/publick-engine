"""What Connecticut's own pages (site/states/ct/) need from the build: the downloadable tables behind them."""

from __future__ import annotations

from pathlib import Path

TEMPLATE_GLOBALS: dict = {}


def write_files(out_dir: Path, data: dict) -> None:
    from pipeline.build_site import write_csv

    b = data["budget"]
    if not b:
        return
    folder = out_dir / "budget" / "data"
    write_csv(folder / "mill-rates.csv", ["fiscal_year", "mill_rate", "state_median"],
              [[r["fiscal_year"], r["rate"], r["state_median"]] for r in b["rates"]])
    if b["adopted"]:
        revenue = list(b["adopted"][-1]["revenue"])
        spending = list(b["adopted"][-1]["spending"])
        write_csv(folder / "adopted-budget.csv", ["fiscal_year", "total", *revenue, *spending],
                  [[y["fiscal_year"], y["total"], *(y["revenue"].get(k) for k in revenue),
                    *(y["spending"].get(k) for k in spending)] for y in b["adopted"]])
    if b["levy"]:
        write_csv(folder / "tax-levy.csv", ["fiscal_year", "real_property", "personal_property", "motor_vehicles", "total"],
                  [[y["fiscal_year"], y["real_property"], y["personal_property"], y["motor_vehicles"], y["total"]]
                   for y in b["levy"]])
    if b["grand_list"]:
        keys = ["residential", "apartments", "commercial", "industrial", "real_property", "motor_vehicles",
                "personal_property", "total"]
        write_csv(folder / "grand-list.csv", ["grand_list_year", *keys],
                  [[y["grand_list_year"], *(y[k] for k in keys)] for y in b["grand_list"]])
