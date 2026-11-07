"""The starter town (starter/): the folder a new town's repository begins from, as the README's
"Start a site for your town" says. Its config must load and build with no data, its commented
examples must too once uncommented, it must leave something to fill in so a copy isn't published
as it is, and its workflow must be the one docs/stand-alone.md describes."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pipeline.new_town import left_to_fill_in

ENGINE = Path(__file__).parent.parent
STARTER = ENGINE / "starter"
CONFIG = STARTER / "config" / "town.toml"
WORKFLOW = STARTER / ".github" / "workflows" / "site.yml"
# A commented-out line of TOML in the starter's examples: a table, a key's value, a row of an
# inline-table array, or the array's end. Prose comments never start so.
EXAMPLE_LINE = re.compile(r"^# (\[|[a-z_]+ = |  \{|\])")


def build(town_dir: Path, out: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PUBLICK_TOWN_DIR": str(town_dir), "PYTHONPATH": str(ENGINE)}
    env.pop("TOWN", None)
    return subprocess.run([sys.executable, "-m", "pipeline.build_site", "--out", str(out), "--no-share-cards"],
                          cwd=town_dir, env=env, capture_output=True, text=True, timeout=300)


def test_starter_builds_with_no_data(tmp_path):
    town = tmp_path / "town"
    shutil.copytree(STARTER, town)
    result = build(town, tmp_path / "site")
    assert result.returncode == 0, result.stderr[-2000:]
    assert (tmp_path / "site" / "about" / "index.html").exists()


def test_starter_examples_build_once_uncommented(tmp_path):
    town = tmp_path / "town"
    shutil.copytree(STARTER, town)
    config = town / "config" / "town.toml"
    lines = config.read_text(encoding="utf-8").splitlines()
    uncommented = [line[2:] if EXAMPLE_LINE.match(line) else line for line in lines]
    assert uncommented != lines
    config.write_text("\n".join(uncommented) + "\n", encoding="utf-8")
    result = build(town, tmp_path / "site")
    assert result.returncode == 0, result.stderr[-2000:]
    for page in ("meetings", "officials", "about"):
        assert (tmp_path / "site" / page / "index.html").exists(), page


def test_starter_is_not_ready_to_publish():
    """The site checks fail on a "To fill in:" comment (site_checks, test_nothing_left_to_fill_in), so a copy of the
    starter isn't published until each is done: the site name, the town, and its sections at least."""
    left = left_to_fill_in(CONFIG.read_text(encoding="utf-8"))
    assert len(left) >= 3
    # Each is an item to do, which a person deletes once it's done; nothing else in the file counts as one.
    assert all(re.match(r"line \d+: # To fill in: ", item) for item in left), left


def test_starter_workflow_is_the_documented_one():
    """The starter's workflow is docs/stand-alone.md's, with the town set to the starter's config."""
    doc = (ENGINE / "docs" / "stand-alone.md").read_text(encoding="utf-8")
    documented = re.search(r"```yaml\n(.*?)```", doc, re.S).group(1)
    workflow = WORKFLOW.read_text(encoding="utf-8")
    body = workflow[workflow.index("\nname: ") + 1:]  # after its own heading comment
    town = re.search(r"^      town: (\S+)", body, re.M).group(1)
    assert town == CONFIG.stem
    assert re.sub(r"(?m)^(      town: )\S+.*$", r"\1<town>", body) == documented
