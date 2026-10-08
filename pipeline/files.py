"""Writing the data files a run commits, whole or not at all.

update.py stops a step that runs past its timeout, and a job can be cancelled,
at any moment. A file written in place and stopped halfway is left truncated,
and the run commits it: the next run then can't read it. Written here, the new
file goes next to the old one under a temporary name and replaces it in one
step (os.replace), so a stop leaves the last whole file. A stray temporary
file is never committed (.github/scripts/commit-data.sh leaves out TEMP_SUFFIX)
and the next write of the same file replaces it.
"""

from __future__ import annotations

import os
from pathlib import Path

TEMP_SUFFIX = ".partial"


def write_atomic(path: Path, content: str | bytes) -> None:
    """Write content (text as UTF-8) to path, creating its folder, replacing the file in one step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + TEMP_SUFFIX)
    temp.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    os.replace(temp, path)
