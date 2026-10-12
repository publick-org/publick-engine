"""Working data kept in the town's bucket instead of git: its 311 requests and scorecard.

A 311 town's data/311/requests.json is most of its data (12 MB for Malden in
October 2026), and every daily run rewrites much of it, so git kept a new copy
each day: 311 was four fifths of what the network's repository grew by. For a
town with a [storage] table (pipeline/documents.py) these files live in the
documents bucket instead, under <prefix>/data/, and git keeps only the small
data/311/status.json.

- Reading needs no keys: a step that reads them (the 311 steps, the build, the
  freshness check) downloads each one missing from data/ from the bucket's
  public address, so a pull request's build or a local one gets the network's
  latest. A file not in the bucket yet (a town whose files are still in git)
  is left as it is.
- Writing needs the bucket's keys: fetch_311 and compute_311 upload what they
  saved. fetch_311 first replaces any local copy with the bucket's, so a run
  never builds on an older copy, and refuses to start from nothing when the
  town's last status says it had requests (the bucket unreachable, or the
  file lost), rather than upload an empty history over it.

They're public, as they were in git: requests.json keeps SeeClickFix's full
addresses for the street lookup (decided 2026-10-03), and the pages show only
the block. Setting DOCUMENTS_LOCAL=1 (the tests do) keeps them in data/ alone.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from pipeline import documents
from pipeline.files import write_atomic
from pipeline.http import FetchError

# Under data/, the files kept in the bucket.
FILES = ("311/requests.json", "311/scorecard.json")
# Read fresh every time: each changes daily.
CACHE_CONTROL = "no-store"


def enabled(config: dict) -> bool:
    return "storage" in config and "seeclickfix" in config and not os.environ.get("DOCUMENTS_LOCAL")


def key(config: dict, name: str) -> str:
    prefix = config["storage"].get("prefix", config["slug"]).strip("/")
    return f"{prefix}/data/{name}"


def url(config: dict, name: str) -> str:
    return f"{config['storage']['public_url'].rstrip('/')}/{key(config, name)}"


def download(config: dict, name: str) -> bytes | None:
    """The bucket's copy, or None when it has none. With the keys, from the bucket itself (what a run
    just wrote); without them, from its public address."""
    if os.environ.get("STORAGE_ACCESS_KEY_ID") and os.environ.get("STORAGE_SECRET_ACCESS_KEY"):
        client = documents.make_client(config["storage"])
        try:
            return client.get_object(Bucket=config["storage"]["bucket"], Key=key(config, name))["Body"].read()
        except client.exceptions.NoSuchKey:
            return None
        except Exception as e:  # botocore errors
            raise FetchError(f"storage: could not read {name}: {e}") from e
    try:
        response = requests.get(url(config, name), timeout=120, headers={"Cache-Control": "no-cache"})
    except requests.RequestException as e:
        raise FetchError(f"storage: could not read {name}: {e}") from e
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise FetchError(f"storage: could not read {name}: HTTP {response.status_code}", response.status_code)
    return response.content


def pull(config: dict, data_dir: Path, refresh: bool = False) -> list[str]:
    """Download each file into data/ that's missing there, or every one the bucket has, with refresh.
    Returns the files written."""
    if not enabled(config):
        return []
    written = []
    for name in FILES:
        path = data_dir / name
        if path.exists() and not refresh:
            continue
        content = download(config, name)
        if content is None:
            continue
        write_atomic(path, content)
        written.append(name)
    return written


def push(config: dict, data_dir: Path, name: str) -> bool:
    """Upload one file to the bucket. False for a town that keeps it in data/ alone."""
    if not enabled(config):
        return False
    if name not in FILES:
        raise ValueError(f"{name} isn't kept in the bucket")
    try:
        documents.make_client(config["storage"]).put_object(
            Bucket=config["storage"]["bucket"], Key=key(config, name), Body=(data_dir / name).read_bytes(),
            ContentType="application/json", CacheControl=CACHE_CONTROL)
    except FetchError:
        raise
    except Exception as e:  # botocore errors
        raise FetchError(f"storage: could not save {name}: {e}") from e
    return True
