"""Publish a built site to the sites bucket, where the network's Worker serves it.

The bucket (Cloudflare R2, or any S3-compatible store) holds:

  blobs/<sha256>                       every file, stored once by its content and
                                       shared by every site (the engine's CSS,
                                       scripts and fonts are stored once in all)
  sites/<domain>/builds/<build>.json   one build's manifest: each path, its blob,
                                       content type and size
  sites/<domain>/current.json          the live build's manifest; the Worker
                                       (worker/index.js) reads this

Publishing uploads every file the live build doesn't already use (on a daily
run, only what changed), writes the build's manifest, then replaces
current.json in one write, so a visitor sees the old site or the new one,
never a mix. Rolling back writes an earlier manifest to current.json.

    python -m pipeline.deploy publish  [--town gloucester] [--site _site] [--domain ...]
    python -m pipeline.deploy rollback [--town gloucester] [--build BUILD] [--domain ...]
    python -m pipeline.deploy prune    [--keep 10] [--dry-run]
    python -m pipeline.deploy check    [--town gloucester] [--site _site] [--domain ...]

check fetches the live homepage through the Worker until it is the index.html of
the built site, byte for byte, for up to CHECK_SECONDS: a Worker reuses a site's
manifest for a minute. (The Worker's ETag is each file's blob, but Cloudflare
drops it from HTML, so the page itself is compared.) It needs no keys.

prune deletes build manifests beyond the newest --keep for each site (never
the live one), then blobs no remaining manifest uses and that are older than
two days. It is safe while a publish runs: a publish relies only on blobs its
site's live build uses, which prune keeps, and on blobs it has just uploaded,
which are too new to delete.

The bucket comes from the SITES_ENDPOINT and SITES_BUCKET environment
variables, and its keys from SITES_ACCESS_KEY_ID and SITES_SECRET_ACCESS_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.config import DEFAULT_TOWN, TOWN_DIR, load_config

FORMAT = 1
# Files a GitHub Pages build needs that the Worker doesn't.
SKIPPED = {"CNAME", ".nojekyll"}
# Content types Python's table lacks or gets wrong on some systems.
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
         ".geojson": "application/geo+json", ".webmanifest": "application/manifest+json",
         ".xml": "application/xml", ".csv": "text/csv", ".svg": "image/svg+xml", ".woff2": "font/woff2",
         ".ics": "text/calendar", ".txt": "text/plain", ".md": "text/markdown"}
TEXT = ("text/", "application/json", "application/geo+json", "application/manifest+json", "application/xml",
        "image/svg+xml")
# A blob nothing uses is kept this long before prune deletes it, so a publish
# that has uploaded blobs but not yet written its manifest keeps them. Must be
# longer than any publish takes.
BLOB_GRACE = timedelta(days=2)
UPLOAD_THREADS = 16


def content_type(path: str) -> str:
    kind = TYPES.get(Path(path).suffix.lower()) or mimetypes.guess_type(path)[0] or "application/octet-stream"
    return f"{kind}; charset=utf-8" if kind.startswith(TEXT) else kind


def site_files(site_dir: Path) -> dict[str, Path]:
    """Every file to publish, by its path in the site ("meetings/index.html")."""
    return {p.relative_to(site_dir).as_posix(): p for p in sorted(site_dir.rglob("*"))
            if p.is_file() and p.name not in SKIPPED}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def site_key(domain: str, name: str) -> str:
    return f"sites/{domain}/{name}"


def is_missing(error: Exception) -> bool:
    response = getattr(error, "response", None) or {}
    return str(response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound")


def get_json(client, bucket: str, key: str) -> dict | None:
    try:
        return json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read())
    except Exception as e:
        if is_missing(e):
            return None
        raise


def put_json(client, bucket: str, key: str, data: dict) -> None:
    body = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json",
                      CacheControl="no-store")


def blob_exists(client, bucket: str, blob: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=f"blobs/{blob}")
        return True
    except Exception as e:
        if is_missing(e):
            return False
        raise


def list_keys(client, bucket: str, prefix: str) -> list[dict]:
    """Every object under prefix: [{"Key": ..., "LastModified": ...}]."""
    objects, token = [], None
    while True:
        page = client.list_objects_v2(Bucket=bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        objects += page.get("Contents", [])
        if not page.get("IsTruncated"):
            return objects
        token = page["NextContinuationToken"]


def publish(client, bucket: str, site_dir: Path, domain: str, town: str = "",
            now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    files = site_files(site_dir)
    if "index.html" not in files:
        raise SystemExit(f"{site_dir} has no index.html; build the site first.")
    entries = {}
    for rel, path in files.items():
        entries[rel] = {"blob": sha256(path), "type": content_type(rel), "size": path.stat().st_size}

    # Blobs the live build uses are kept by prune, so they're safe to reuse. Everything else is
    # uploaded, even if the bucket may have it: an old blob could be pruned before this build's
    # manifest is written, and uploading it again makes it new.
    live = get_json(client, bucket, site_key(domain, "current.json"))
    known = {e["blob"] for e in (live or {}).get("files", {}).values()}
    paths = {}
    for rel, entry in entries.items():
        if entry["blob"] not in known:
            paths.setdefault(entry["blob"], files[rel])

    def upload(item) -> None:
        blob, path = item
        client.put_object(Bucket=bucket, Key=f"blobs/{blob}", Body=path.read_bytes(),
                          ContentType="application/octet-stream")

    with ThreadPoolExecutor(UPLOAD_THREADS) as pool:
        list(pool.map(upload, paths.items()))
    uploaded = len(paths)

    digest = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:10]
    build = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{digest}"
    manifest = {"format": FORMAT, "domain": domain, "town": town, "build": build,
                "created_at": now.isoformat(timespec="seconds"), "files": entries}
    put_json(client, bucket, site_key(domain, f"builds/{build}.json"), manifest)
    put_json(client, bucket, site_key(domain, "current.json"), manifest)
    return {"domain": domain, "build": build, "files": len(entries), "uploaded": uploaded,
            "previous": (live or {}).get("build")}


# How long check waits for the Worker to serve a new build (it reuses a manifest for 60 seconds).
CHECK_SECONDS = 120
CHECK_EVERY = 10


def check_live(domain: str, site_dir: Path, user_agent: str, get=None, seconds: float = CHECK_SECONDS,
               every: float = CHECK_EVERY, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Whether https://<domain>/ serves the built site's index.html, asked again every `every`
    seconds for up to `seconds`. ok is False if it never does: what's live isn't what was built."""
    import requests
    expected = sha256(site_dir / "index.html")
    url = f"https://{domain}/"
    get = get or (lambda u: requests.get(u, timeout=30, allow_redirects=False, headers={
        "User-Agent": user_agent, "Accept-Language": "en", "Accept-Encoding": "identity", "Cache-Control": "no-cache"}))
    started, served, status = clock(), None, None
    while True:
        try:
            response = get(url)
            status, served = response.status_code, hashlib.sha256(response.content).hexdigest()
        except requests.RequestException as e:
            status, served = str(e)[:200], None
        if status == 200 and served == expected:
            return {"domain": domain, "ok": True, "blob": expected, "seconds": round(clock() - started)}
        if clock() - started + every > seconds:
            return {"domain": domain, "ok": False, "blob": expected, "served": served, "status": status,
                    "seconds": round(clock() - started)}
        sleep(every)


def build_names(keys: list[str], prefix: str) -> list[str]:
    """Build names from manifest keys under prefix, oldest first (names start with their UTC time)."""
    return sorted(k[len(prefix):-len(".json")] for k in keys if k.startswith(prefix) and k.endswith(".json"))


def builds(client, bucket: str, domain: str) -> list[str]:
    prefix = site_key(domain, "builds/")
    return build_names([o["Key"] for o in list_keys(client, bucket, prefix)], prefix)


def rollback(client, bucket: str, domain: str, build: str | None = None) -> dict:
    """Make an earlier build live: the given one, or the one before the live build."""
    live = (get_json(client, bucket, site_key(domain, "current.json")) or {}).get("build")
    history = builds(client, bucket, domain)
    if build is None:
        earlier = [b for b in history if live and b < live]
        if not earlier:
            raise SystemExit(f"{domain}: no build before {live}")
        build = earlier[-1]
    manifest = get_json(client, bucket, site_key(domain, f"builds/{build}.json"))
    if manifest is None:
        raise SystemExit(f"{domain}: no build {build}; builds kept: {', '.join(history) or 'none'}")
    missing = [b for b in {e["blob"] for e in manifest["files"].values()} if not blob_exists(client, bucket, b)]
    if missing:
        raise SystemExit(f"{domain}: build {build} is missing {len(missing)} files; not rolling back")
    put_json(client, bucket, site_key(domain, "current.json"), manifest)
    return {"domain": domain, "build": build, "previous": live}


def prune(client, bucket: str, keep: int = 10, dry_run: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    site_keys = [o["Key"] for o in list_keys(client, bucket, "sites/")]
    domains = sorted({k.split("/")[1] for k in site_keys if k.count("/") >= 2})
    old_builds, used = [], set()
    for domain in domains:
        live = get_json(client, bucket, site_key(domain, "current.json"))
        history = build_names(site_keys, site_key(domain, "builds/"))
        kept = set(history[-keep:]) | ({live["build"]} if live else set())
        old_builds += [site_key(domain, f"builds/{b}.json") for b in history if b not in kept]
        for build in sorted(kept):
            manifest = live if live and live["build"] == build else \
                get_json(client, bucket, site_key(domain, f"builds/{build}.json"))
            used |= {e["blob"] for e in (manifest or {}).get("files", {}).values()}
    old_blobs = [o["Key"] for o in list_keys(client, bucket, "blobs/")
                 if o["Key"][len("blobs/"):] not in used and now - o["LastModified"] > BLOB_GRACE]
    if not dry_run:
        # Manifests first, so a failure part-way never leaves a kept build without its blobs.
        for batch in (old_builds, old_blobs):
            for i in range(0, len(batch), 1000):
                response = client.delete_objects(Bucket=bucket, Delete={
                    "Objects": [{"Key": k} for k in batch[i:i + 1000]], "Quiet": True})
                if response.get("Errors"):
                    first = response["Errors"][0]
                    raise SystemExit(f"prune: {len(response['Errors'])} deletions failed, "
                                     f"e.g. {first.get('Key')}: {first.get('Message')}")
    return {"sites": len(domains), "builds_deleted": len(old_builds), "blobs_deleted": len(old_blobs),
            "blobs_kept": len(used), "dry_run": dry_run}


def make_client():
    missing = [k for k in ("SITES_ENDPOINT", "SITES_BUCKET", "SITES_ACCESS_KEY_ID", "SITES_SECRET_ACCESS_KEY")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} to deploy.")
    import boto3

    return boto3.client("s3", endpoint_url=os.environ["SITES_ENDPOINT"], region_name="auto",
                        aws_access_key_id=os.environ["SITES_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["SITES_SECRET_ACCESS_KEY"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["publish", "rollback", "prune", "check"])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--domain", help="the site's address (default: the town's [site] domain)")
    parser.add_argument("--site", type=Path, default=TOWN_DIR / "_site", help="the built site (publish)")
    parser.add_argument("--build", help="the build to make live (rollback; default: the one before)")
    parser.add_argument("--keep", type=int, default=10, help="builds kept per site (prune)")
    parser.add_argument("--dry-run", action="store_true", help="report what prune would delete")
    args = parser.parse_args()
    if args.command == "check":
        config = load_config(args.town)
        result = check_live(args.domain or config["site"]["domain"], args.site, config["site"]["user_agent"])
        print(json.dumps(result, indent=2))
        if not result["ok"]:
            print(f"::error::{result['domain']} doesn't serve the build just published (it serves "
                  f"{result.get('served') or 'nothing'}, status {result.get('status')})")
        return 0 if result["ok"] else 1
    client, bucket = make_client(), os.environ["SITES_BUCKET"]
    if args.command == "prune":
        if args.keep < 1:
            raise SystemExit("--keep must be at least 1.")
        result = prune(client, bucket, args.keep, args.dry_run)
    else:
        if not args.domain and not args.town:
            raise SystemExit("Pass --domain, or --town (or set TOWN).")
        domain = args.domain or load_config(args.town)["site"]["domain"]
        if args.command == "publish":
            result = publish(client, bucket, args.site, domain, town=args.town or "")
        else:
            result = rollback(client, bucket, domain, args.build)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
