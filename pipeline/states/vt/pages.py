"""What Vermont's own pages (site/states/vt/) need from the build: the downloadable tables behind them."""

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
              ["tax_year", "homestead_education", "nonhomestead_education", "municipal", "local_agreement",
               "state_median_homestead", "state_median_nonhomestead", "state_median_municipal"],
              [[r["tax_year"], r["homestead_rate"], r["nonhomestead_rate"], r["municipal_rate"], r["local_agreement_rate"],
                r["state_median"]["homestead_rate"], r["state_median"]["nonhomestead_rate"],
                r["state_median"]["municipal_rate"]] for r in b["rates"]])
    parts = list(dict.fromkeys(p for y in b["taxes"] for p in y["parts"]))
    write_csv(folder / "taxes-raised.csv", ["tax_year", "total", *parts],
              [[y["tax_year"], y["total"], *(y["parts"].get(p) for p in parts)] for y in b["taxes"]])
    write_csv(folder / "grand-list.csv", ["tax_year", "listed_value", "cla", "equalized_value", "parcels"],
              [[y["tax_year"], y["listed_value"], y["cla"], y["equalized_value"], y["parcels"]] for y in b["grand_list"]])
