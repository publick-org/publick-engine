"""The same data built by the same engine gives the same site, byte for byte, whatever
Python's hash seed (PYTHONHASHSEED), which changes the order of a set of strings from
one process to the next. Built in subprocesses, each with its own seed, from a copy of
the fixture data with a school committee meeting listed on both the city calendar and
the district's Drive folders, whose page once named either as where its minutes were
awaited, by the order of a set."""

import filecmp
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conftest import BUILT_AT, DATA_DIR, ROOT

SEEDS = ("0", "1", "2", "3")
BUILD = """
import sys
from datetime import datetime
from pathlib import Path
from pipeline import build_site
build_site.build("gloucester", Path(sys.argv[1]), data_dir=Path(sys.argv[2]), now=datetime.fromisoformat(sys.argv[3]))
"""


def with_school_meeting_in_two_places(data_dir: Path) -> None:
    """A past School Committee meeting on the city calendar (whose minutes would come from the Archive
    Center) and in the district's Drive folders, both collected from the same date, with no minutes yet."""
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text(encoding="utf-8"))
    seen = "2026-09-01T07:00:00-04:00"
    common = {"body": "School Committee", "date": "2026-09-02", "end_time": None, "first_seen": seen, "last_seen": seen,
              "listed": True, "special": False, "status": "scheduled", "variant": ""}
    store["90001"] = {**common, "id": "90001", "slug": "2026-09-02-school-committee", "start_time": "19:00",
                      "title": "School Committee Meeting", "source_url": "https://www.gloucester-ma.gov/Calendar.aspx?EID=90001"}
    store["drive-sc-2026-09-02"] = {**common, "id": "drive-sc-2026-09-02", "slug": "2026-09-02-school-committee-2",
                                    "start_time": None, "title": "School Committee", "source": "drive",
                                    "source_name": "Gloucester Public Schools",
                                    "source_url": "https://schoolcommittee.gloucesterschools.com/"}
    path.write_text(json.dumps(store, indent=1), encoding="utf-8")


def build(seed: str, out: Path, data_dir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-c", BUILD, str(out), str(data_dir), BUILT_AT.isoformat()],
                          env=env, cwd=ROOT, capture_output=True, text=True, timeout=600)


def differences(a: Path, b: Path) -> list[str]:
    """Files in one tree and not the other, or in both and different."""
    found = []
    compared = filecmp.dircmp(a, b)
    found += [str(Path(compared.left, n).relative_to(a)) for n in compared.left_only + compared.right_only]
    found += [str(Path(compared.left, n).relative_to(a)) for n in compared.diff_files + compared.funny_files]
    for name, sub in compared.subdirs.items():
        found += [f"{name}/{d}" for d in differences(Path(sub.left), Path(sub.right))]
    return found


def test_the_same_site_whatever_the_hash_seed(tmp_path):
    data_dir = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_dir)
    with_school_meeting_in_two_places(data_dir)
    outs = [tmp_path / f"site-{seed}" for seed in SEEDS]
    with ThreadPoolExecutor(len(SEEDS)) as pool:
        runs = list(pool.map(build, SEEDS, outs, [data_dir] * len(SEEDS)))
    for seed, run in zip(SEEDS, runs):
        assert run.returncode == 0, f"PYTHONHASHSEED={seed}: {run.stderr[-2000:]}"
    for seed, out in zip(SEEDS[1:], outs[1:]):
        assert differences(outs[0], out) == [], f"PYTHONHASHSEED={SEEDS[0]} and {seed} built different files"
    # The meeting waits for its minutes on the district's website, where the committee posts them.
    page = (outs[0] / "meetings" / "2026-09-02-school-committee" / "index.html").read_text(encoding="utf-8")
    assert 'data-gap="not_posted">Minutes haven\'t been posted on the <a href="https://schoolcommittee.gloucesterschools.com/"' in page
