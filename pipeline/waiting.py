"""What a meetings reader leaves for a later run, for its status file.

A reader saves at most so many documents a run (max_documents_per_run), newest meeting first, and counts what it
leaves: meetings or documents, by its status file's "<unit>_waiting". It also gives the newest meeting date it left
("waiting_newest"): everything waiting is that day or older. The network's report of meetings missing documents
(pipeline/document_gaps.py) counts a reader's waiting only when that day falls in the report's window; older ones
are a new town's first reading of its past meetings, counted apart, which shrinks every run.
"""

from __future__ import annotations


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
