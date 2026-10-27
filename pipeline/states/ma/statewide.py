"""Massachusetts's statewide sources, fetched once for every town in the network instead of once per town:

- the Division of Local Services' report exports (dls.py, with its own store under dls/);
- the Subsidized Housing Inventory, one PDF listing every municipality (shi.json, housing.py);
- DESE's school figures, each measure for every district and the state (dese/<measure>.json, schools.py).

pipeline.network states calls refresh() once per run, before the towns' steps. A source is fetched again
once what was saved is more than dls.MAX_AGE_DAYS old (fetched.json says when each was), so most runs ask
for nothing. A town's steps then read its rows from the store PUBLICK_STATE_DIR names (dls.STORE_ENV); a
town missing from what was saved, or a town run on its own with no store, asks the source itself.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.files import write_atomic
from pipeline.http import FetchError
from pipeline.states.ma import dls

USER_AGENT = dls.USER_AGENT
# When each file other than DLS's was last fetched.
INDEX = "fetched.json"


def store() -> Path | None:
    """The state's folder in the statewide store, when a town's steps are told where it is."""
    root = os.environ.get(dls.STORE_ENV)
    return Path(root) / "ma" if root else None


def read(name: str) -> dict | None:
    """A saved file from the store, or None (no store, or not saved yet)."""
    folder = store()
    path = folder / name if folder else None
    return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else None


def _index(state_dir: Path) -> dict:
    path = state_dir / "ma" / INDEX
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def due(state_dir: Path, name: str, now: datetime) -> bool:
    """Whether the file isn't saved, or was fetched more than dls.MAX_AGE_DAYS ago."""
    fetched = _index(state_dir).get(name)
    return not (fetched and (state_dir / "ma" / name).exists()
                and datetime.fromisoformat(fetched) > now - timedelta(days=dls.MAX_AGE_DAYS))


def save(state_dir: Path, name: str, text: str, now: datetime) -> None:
    path = state_dir / "ma" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, text)
    index = {**_index(state_dir), name: now.isoformat(timespec="seconds")}
    write_atomic(state_dir / "ma" / INDEX, json.dumps(dict(sorted(index.items())), indent=2) + "\n")


def dumps(meta: dict, rows: dict) -> str:
    """meta's keys, then "rows" one per line, so a refresh that changes nothing changes no lines."""
    lines = [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()]
    body = ",\n".join(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
                      for k, v in rows.items())
    return "{\n" + ",\n".join([*lines, '  "rows": {\n' + body + "\n  }"]) + "\n}\n"


def refresh(state_dir: Path, configs: list[dict], client, now: datetime | None = None) -> dict:
    """Fill the store for these towns: each source fetched once, for the towns that use it. Every
    source is tried; if any fails, what the others fetched is kept and the failures are raised together."""
    from pipeline.states.ma import housing, schools

    now = now or datetime.now(timezone.utc)
    total, errors = {"exports": 0, "fetched": 0}, []
    for label, fetch, towns in (
        ("DLS", dls.refresh, [c for c in configs if "finance" in c]),
        ("Subsidized Housing Inventory", housing.refresh, [c for c in configs if "shi_url" in c.get("housing", {})]),
        ("DESE", schools.refresh, [c for c in configs if "schools" in c]),
    ):
        if not towns:
            continue
        try:
            result = fetch(state_dir, towns, client, now)
        except Exception as e:  # the others still run; the network records the failure
            errors.append(f"{label}: {e}")
            continue
        total["exports"] += result["exports"]
        total["fetched"] += result["fetched"]
    if errors:
        raise FetchError("; ".join(errors))
    return total
