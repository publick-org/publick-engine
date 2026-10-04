"""What differs from state to state: where a town's tax bill, budget, and school figures come from.

Every state publishes these differently (Massachusetts through its Division of
Local Services and DESE, New Hampshire through its Department of Revenue
Administration and Department of Education, Connecticut through OPM's datasets
and EdSight, Vermont, Maine, and Rhode Island through their tax and education
departments), so each state has a package here,
pipeline/states/<code>/, and page templates in site/states/<code>/. A town names
its state in [town] state_abbr, and everything state-specific goes through that
state's package: adding a town in a state that already has one needs only its
config.

A state's package defines STATE, a State below. Its sources each read one
table of the town's config ([finance], [schools]); what goes in the table is
the state's own, listed in the source's keys, and checked when the config is
loaded. The fetch commands (pipeline.fetch_finance, fetch_budget,
fetch_schools) run the town's state's source, and do nothing for a town whose
state has none. A town in a state with no package here still gets everything
that isn't state-specific: meetings, 311, unemployment, housing, permits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from types import ModuleType

# States with a package here, by their two-letter code.
PACKAGES = {"CT": "pipeline.states.ct", "MA": "pipeline.states.ma", "ME": "pipeline.states.me", "NH": "pipeline.states.nh",
            "RI": "pipeline.states.ri", "VT": "pipeline.states.vt"}

# The kinds of state source, with the plain name used in messages.
KINDS = {"tax_bill": "Tax bill", "budget": "Budget figures", "schools": "School figures"}
# Sections whose page is the state's own (site/states/<state>/<section>.html), and the source each shows.
SECTIONS = {"schools": "schools", "budget": "budget"}


@dataclass(frozen=True)
class Source:
    """One of a state's sources.

    module has client(config), the HTTP client to fetch with, and
    run(config, client, data_dir, now=None, force=False), which writes the
    source's data file and returns a short summary to print.
    """
    table: str
    keys: tuple[str, ...]
    module: str

    def load(self) -> ModuleType:
        return import_module(self.module)


@dataclass(frozen=True)
class State:
    code: str
    name: str
    # Kind (a key of KINDS) -> Source.
    sources: dict = field(default_factory=dict)
    # The short credit under the home page's tax bill figure.
    tax_source: str = ""
    # The figure's name there, when it isn't the average single-family bill (Vermont's is for homesteads).
    tax_label: str = ""
    # Extra housing figures from state sources: a module with keys (the housing.json keys it adds),
    # parts(config) (those this town has), client(config), and sources(config, client, state_client, now).
    housing: str | None = None
    # What the state's pages need from the build: a module with TEMPLATE_GLOBALS (helpers for
    # site/states/<state>/ templates) and write_files(out_dir, data), for their downloadable tables.
    pages: str | None = None

    @property
    def templates(self) -> str:
        """The state's folder under site/states/."""
        return self.code.lower()

    def source(self, kind: str, config: dict) -> Source | None:
        """The state's source of this kind, if the state has one and the town's config has its table."""
        source = self.sources.get(kind)
        return source if source and source.table in config else None

    def pages_module(self) -> ModuleType | None:
        return import_module(self.pages) if self.pages else None

    def housing_module(self) -> ModuleType | None:
        return import_module(self.housing) if self.housing else None

    def housing_parts(self, config: dict) -> set[str]:
        module = self.housing_module()
        return module.parts(config) if module else set()


def for_town(config: dict) -> State:
    code = config["town"]["state_abbr"].upper()
    if code in PACKAGES:
        return import_module(PACKAGES[code]).STATE
    return State(code=code, name=config["town"]["state"])


def check(config: dict) -> None:
    """Stop with a clear message when a state source's table is missing a key it needs."""
    state = for_town(config)
    for kind, source in state.sources.items():
        table = config.get(source.table)
        if table is None:
            continue
        missing = [k for k in source.keys if k not in table]
        if missing:
            raise SystemExit(f"[{source.table}] in config/{config['slug']}.toml needs {', '.join(missing)} for "
                             f"{state.name}'s {KINDS[kind].lower()} (see pipeline/states/{state.templates}/).")


def run(config: dict, kind: str, client, data_dir: Path, now=None, force: bool = False) -> dict:
    """Fetch one kind of state figures for the town. The fetch commands and the tests call this."""
    source = for_town(config).source(kind, config)
    if source is None:
        raise LookupError(f"no {KINDS[kind].lower()} source for this town")
    return source.load().run(config, client, data_dir, now=now, force=force)


def main(config: dict, kind: str, data_dir: Path, force: bool = False) -> int:
    """A fetch command's body: run the town's state's source, or say why there's nothing to run."""
    from pipeline.http import FetchError

    state = for_town(config)
    source = state.source(kind, config)
    if source is None:
        why = (f"no [{state.sources[kind].table}] in config/{config['slug']}.toml" if kind in state.sources
               else f"no {KINDS[kind].lower()} source for {state.name} yet")
        print(f"::notice::{KINDS[kind]}: {why}; skipping.")
        return 0
    module = source.load()
    try:
        print(json.dumps(run(config, kind, module.client(config), data_dir, force=force), indent=2))
    except (FetchError, ValueError, KeyError) as e:
        print(f"::error::{KINDS[kind]} could not be fetched: {e}")
        return 1
    return 0
