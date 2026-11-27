"""What a meetings reader leaves for a later run, for its status file.

A reader saves at most so many documents a run (max_documents_per_run), newest meeting first, and counts what it
leaves: meetings or documents, by its status file's "<unit>_waiting". It also gives the newest meeting date it left
("waiting_newest"): everything waiting is that day or older. The network's report of meetings missing documents
(pipeline/document_gaps.py) counts a reader's waiting only when that day falls in the report's window; older ones
are a new town's first reading of its past meetings, counted apart, which shrinks every run.

    python -m pipeline.waiting [--data <town>/data]

prints the town's total waiting, every reader's and every unit's together, on one line (0 when nothing is), and
each reader's count on stderr. A new town's first fetches run again until it's 0 (publick.org's add-town.yml).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Status files under data/meetings/ of readers that don't save documents.
NOT_DOCUMENTS = ("recordings_status.json",)


class Waiting:
    def __init__(self) -> None:
        self.count = 0
        self.newest: str | None = None

    def add(self, day: str, count: int = 1) -> None:
        """Count left for a later run, for a meeting held on day (YYYY-MM-DD)."""
        if count:
            self.count += count
            self.newest = max(self.newest or day, day)

    def status(self, unit: str) -> dict:
        """The status file's fields: "<unit>_waiting" (meetings, documents) and "waiting_newest"."""
        return {f"{unit}_waiting": self.count, "waiting_newest": self.newest}


def readers(data_dir: Path) -> dict[str, dict]:
    """Each reader with something waiting, by its status file's name under data/meetings/ ("opengov_status"):
    its count, its unit ("<unit>_waiting": meetings, documents, posts) and its newest meeting ("waiting_newest",
    None where the reader doesn't give it). A status file that can't be read counts nothing."""
    found = {}
    for path in sorted((data_dir / "meetings").glob("*status.json")):
        if path.name in NOT_DOCUMENTS:
            continue
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(status, dict):
            continue
        counted = [(k.removesuffix("_waiting"), v) for k, v in status.items()
                   if k.endswith("_waiting") and isinstance(v, int) and not isinstance(v, bool) and v > 0]
        if counted:
            found[path.name.removesuffix(".json")] = {"count": sum(v for _, v in counted), "unit": counted[0][0],
                                                      "newest": status.get("waiting_newest")}
    return found


def total(data_dir: Path) -> int:
    """Everything the town's readers left for a later run, whatever its unit: 0 once each has caught up."""
    return sum(r["count"] for r in readers(data_dir).values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=None,
                        help="the town's data folder (default: data/ in PUBLICK_TOWN_DIR, or here)")
    args = parser.parse_args(argv)
    if args.data is None:
        from pipeline.config import DATA_DIR
        args.data = DATA_DIR
    found = readers(args.data)
    for name, reader in found.items():
        print(f"{name.removesuffix('_status')}: {reader['count']} {reader['unit']} waiting", file=sys.stderr)
    print(sum(r["count"] for r in found.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
