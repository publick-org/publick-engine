"""The workflows' data commit (.github/scripts/commit-data.sh) takes every new file
under the paths it's given, and leaves out a step's temporary .partial file.

On 2026-10-18 a new town's first run fetched its meetings and summaries, and the
script committed none of them: git, given an exclude pathspec beside the town's
data folder, left the folder's new files out of the add. Here the script runs
against a local copy of a network repository, for a town whose data folder holds
only a tracked static file, as a new town's does."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "commit-data.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def network(tmp_path, town):
    """A bare 'origin' and a checkout of it with the town's config and ward file."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    for path in (f"towns/{town}/config/town.toml", f"towns/{town}/data/static/wards.geojson"):
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text("{}\n")
    git(work, "add", "-A")
    git(work, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "Add the town")
    git(work, "push", "-q", "origin", "HEAD:main")
    return origin, work


@pytest.mark.parametrize("town", ["salem-ma", "south-kingstown-ri", "methuen-ma"])
def test_a_new_towns_first_data_is_committed(tmp_path, town):
    origin, work = network(tmp_path, town)
    data = work / "towns" / town / "data"
    for path in ("meetings/meetings.json", "meetings/status.json", "summaries/2026-10-08-city-council.json",
                 "run.json"):
        (data / path).parent.mkdir(parents=True, exist_ok=True)
        (data / path).write_text("{}\n")
    (data / "meetings" / "meetings.json.partial").write_text("{")

    subprocess.run([str(SCRIPT), "Update data for", f"towns/{town}/data"], cwd=work, check=True,
                   capture_output=True, text=True)

    committed = set(git(origin, "ls-tree", "-r", "--name-only", "main").split())
    prefix = f"towns/{town}/data/"
    assert {f"{prefix}meetings/meetings.json", f"{prefix}meetings/status.json",
            f"{prefix}summaries/2026-10-08-city-council.json", f"{prefix}run.json"} <= committed
    assert f"{prefix}meetings/meetings.json.partial" not in committed


def test_nothing_new_commits_nothing(tmp_path):
    origin, work = network(tmp_path, "salem-ma")
    before = git(origin, "rev-parse", "main")
    result = subprocess.run([str(SCRIPT), "Update data for", "towns/salem-ma/data"], cwd=work, check=True,
                            capture_output=True, text=True)
    assert "No changes" in result.stdout
    assert git(origin, "rev-parse", "main") == before
