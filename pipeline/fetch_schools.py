"""Collect the town's school district figures from its state's source (pipeline/states/).

Writes data/schools/schools.json. Each state's source is its own; a town whose state has
none, or whose config has no table for it, is skipped.

Usage:
    python -m pipeline.fetch_schools [--town gloucester] [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import states
from pipeline.config import DATA_DIR, DEFAULT_TOWN, load_config

KIND = "schools"


def run(config: dict, client, data_dir: Path, now=None, force: bool = False) -> dict:
    return states.run(config, KIND, client, data_dir, now=now, force=force)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="fetch even if the saved file is recent")
    args = parser.parse_args()
    return states.main(load_config(args.town), KIND, args.data, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
