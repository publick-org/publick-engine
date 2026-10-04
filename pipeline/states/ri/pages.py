"""What Rhode Island's own pages (site/states/ri/) need from the build: the downloadable tables behind them."""

from __future__ import annotations

from pathlib import Path

TEMPLATE_GLOBALS: dict = {}
CLASSES = ["residential", "commercial", "tangible", "motor_vehicles", "total"]


def write_files(out_dir: Path, data: dict) -> None:
    from pipeline.build_site import write_csv

    b = data["budget"]
    if not b:
        return
    folder = out_dir / "budget" / "data"
    write_csv(folder / "tax-rates.csv",
              ["fiscal_year", "residential", "commercial", "personal_property", "motor_vehicles", "revalued",
               "state_median_residential"],
              [[r["fiscal_year"], r["residential"], r["commercial"], r["personal_property"], r["motor_vehicles"],
                "yes" if r["revalued"] else "", r["state_median"]] for r in b["rates"]])
    for key, name in (("levy", "tax-levy"), ("assessed", "assessed-value")):
        if b.get(key):
            write_csv(folder / f"{name}.csv", ["fiscal_year", *CLASSES],
                      [[y["fiscal_year"], *(y[k] for k in CLASSES)] for y in b[key]])
