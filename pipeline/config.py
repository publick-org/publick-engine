"""Load per-town configuration from config/<town>.toml.

The engine (this repository) holds the code, page templates, and shared static
files. Each town has its own repository with its config/, its data/, and
optionally site/static/ files that replace or add to the engine's (its share
image, or its own icon). Commands run from the town's repository, or with
PUBLICK_TOWN_DIR pointing at it.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
TOWN_DIR = Path(os.environ.get("PUBLICK_TOWN_DIR") or Path.cwd()).resolve()
CONFIG_DIR = TOWN_DIR / "config"
DATA_DIR = TOWN_DIR / "data"
# Files that replace or add to the engine's site/static/ for this town.
TOWN_STATIC_DIR = TOWN_DIR / "site" / "static"


def only_town() -> str:
    """The town's config name when its repository has exactly one config file."""
    towns = sorted(CONFIG_DIR.glob("*.toml")) if CONFIG_DIR.is_dir() else []
    return towns[0].stem if len(towns) == 1 else ""


# The town every command uses unless given --town. The workflow sets TOWN once.
DEFAULT_TOWN = os.environ.get("TOWN") or only_town()


def load_config(town: str) -> dict:
    path = CONFIG_DIR / f"{town}.toml"
    if not town or not path.exists():
        raise SystemExit(f"No town config at {path}. Run from the town's repository, "
                         "or set PUBLICK_TOWN_DIR, and pass --town or set TOWN.")
    with path.open("rb") as f:
        config = tomllib.load(f)
    config["slug"] = town
    return config


def configured(config: dict, table: str) -> bool:
    """Whether the town has a source. A town without one leaves its table out of
    the config file, and the command that fetches it does nothing."""
    if table in config:
        return True
    print(f"::notice::No [{table}] in config/{config['slug']}.toml; skipping.")
    return False
