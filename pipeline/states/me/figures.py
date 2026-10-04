"""The statewide figures saved by pipeline.states.me.extract, and one municipality's or district's rows in them."""

from __future__ import annotations

import json
from functools import cache

from pipeline.http import FetchError
from pipeline.states.me import extract


@cache
def load(kind: str) -> dict:
    path = extract.FIGURES_DIR / f"{kind}.json"
    if not path.exists():
        raise FetchError(f"no {kind} figures saved yet; run python -m pipeline.states.me.extract")
    return json.loads(path.read_text(encoding="utf-8"))


def municipality(config: dict) -> str:
    """The town's name as MRS writes it, in capitals ("Lewiston" -> "LEWISTON")."""
    return config["finance"]["mrs_municipality"].strip().upper()


def rows(kind: str, name: str) -> list[tuple[int, dict]]:
    """[(year, row)] for every saved year that has this name, oldest first."""
    found = [(int(year), rows[name]) for year, rows in load(kind)["years"].items() if name in rows]
    if not found:
        raise FetchError(f"{name!r} is not in the saved {kind} figures (names are the state's, as in its files)")
    return found


def with_state(kind: str, name: str, state: str = extract.STATEWIDE) -> list[tuple[int, dict, dict | None]]:
    """[(year, the municipality's or district's row, the state's row)], oldest first."""
    years = load(kind)["years"]
    return [(year, row, years[str(year)].get(state)) for year, row in rows(kind, name)]


def extracted_at(*kinds: str) -> str:
    """When the oldest of these figures files was last saved: how current the state's figures are here."""
    return min(load(kind)["extracted_at"] for kind in kinds)
