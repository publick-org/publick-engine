"""Publishing built sites to the sites bucket, rolling back, and pruning."""

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from pipeline import deploy

NOW = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)


class FakeBucket:
    """An S3 client over a dict, with S3's errors for missing keys and two keys per listing page."""

    def __init__(self):
        self.objects = {}  # key -> {"body", "type", "modified"}
        self.puts = []
        self.clock = NOW

    def put_object(self, Bucket, Key, Body, ContentType=None, CacheControl=None):
        self.objects[Key] = {"body": bytes(Body), "type": ContentType, "modified": self.clock}
        self.puts.append(Key)

    def _get(self, key, operation):
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey" if operation == "GetObject" else "404"}}, operation)
        return self.objects[key]

    def get_object(self, Bucket, Key):
        body = self._get(Key, "GetObject")["body"]
        return {"Body": type("Body", (), {"read": lambda self: body})()}

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self._get(Key, "HeadObject")["body"])}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]
        more = start + 2 < len(keys)
        return {"Contents": [{"Key": k, "LastModified": self.objects[k]["modified"]} for k in page],
                "IsTruncated": more, **({"NextContinuationToken": str(start + 2)} if more else {})}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)
        return {}

    def json(self, key):
        return json.loads(self.objects[key]["body"])


def make_site(path, pages: dict):
    for rel, text in {"index.html": "<h1>Home</h1>", **pages}.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    return path


@pytest.fixture
def bucket():
    return FakeBucket()


def test_publishes_the_built_site(bucket, site_dir):
    result = deploy.publish(bucket, "b", site_dir, "gloucester-ma.publick.org", town="gloucester", now=NOW)
    live = bucket.json("sites/gloucester-ma.publick.org/current.json")
    assert live == bucket.json(f"sites/gloucester-ma.publick.org/builds/{result['build']}.json")
    assert live["domain"] == "gloucester-ma.publick.org" and live["format"] == deploy.FORMAT
    assert "CNAME" not in live["files"] and ".nojekyll" not in live["files"]
    assert live["files"]["index.html"]["type"] == "text/html; charset=utf-8"
    assert live["files"]["static/css/site.css"]["type"] == "text/css; charset=utf-8"
    assert live["files"]["feed.xml"]["type"] == "application/xml; charset=utf-8"
    assert live["files"]["static/fonts/public-sans-400.woff2"]["type"] == "font/woff2"
    for rel, entry in live["files"].items():
        assert bucket.objects[f"blobs/{entry['blob']}"]["body"] == (site_dir / rel).read_bytes()
    assert result["uploaded"] == len({e["blob"] for e in live["files"].values()})


def test_goes_live_only_after_every_file_is_uploaded(bucket, tmp_path):
    site = make_site(tmp_path, {"about/index.html": "About", "static/site.css": "body{}"})
    deploy.publish(bucket, "b", site, "t.publick.org", now=NOW)
    assert bucket.puts[-1] == "sites/t.publick.org/current.json"
    assert bucket.puts[-2].startswith("sites/t.publick.org/builds/")
    assert all(k.startswith("blobs/") for k in bucket.puts[:-2])


def test_republishing_uploads_only_changed_files(bucket, tmp_path):
    site = make_site(tmp_path, {"about/index.html": "About", "static/site.css": "body{}"})
    first = deploy.publish(bucket, "b", site, "t.publick.org", now=NOW)
    assert first["uploaded"] == 3
    again = deploy.publish(bucket, "b", site, "t.publick.org", now=NOW + timedelta(days=1))
    assert again["uploaded"] == 0 and again["previous"] == first["build"]
    (site / "about/index.html").write_text("About, updated")
    assert deploy.publish(bucket, "b", site, "t.publick.org", now=NOW + timedelta(days=2))["uploaded"] == 1


def test_sites_share_identical_files(bucket, tmp_path):
    a = make_site(tmp_path / "a", {"static/site.css": "body{}"})
    b = make_site(tmp_path / "b", {"static/site.css": "body{}", "index.html": "<h1>B</h1>"})
    deploy.publish(bucket, "b", a, "a.publick.org", now=NOW)
    deploy.publish(bucket, "b", b, "b.publick.org", now=NOW)
    assert len([k for k in bucket.objects if k.startswith("blobs/")]) == 3


def test_publish_reuploads_a_file_prune_could_take(bucket, tmp_path):
    """A file no build uses may be pruned at any moment. Publishing it again makes it new, so a prune running
    before this build's manifest is written keeps it."""
    site = make_site(tmp_path, {})
    deploy.publish(bucket, "b", site, "t.publick.org", now=NOW)
    old = "blobs/" + hashlib.sha256(b"<h1>Back again</h1>").hexdigest()
    bucket.put_object(Bucket="b", Key=old, Body=b"<h1>Back again</h1>")
    bucket.objects[old]["modified"] = NOW - timedelta(days=30)
    (site / "index.html").write_text("<h1>Back again</h1>")
    bucket.clock = NOW + timedelta(days=1)
    real_put = deploy.put_json

    def prune_before_manifest(client, bucket_name, key, data):
        if key.endswith(".json") and "/builds/" in key:
            deploy.prune(client, bucket_name, now=bucket.clock)
        real_put(client, bucket_name, key, data)

    deploy.put_json = prune_before_manifest
    try:
        deploy.publish(bucket, "b", site, "t.publick.org", now=bucket.clock)
    finally:
        deploy.put_json = real_put
    assert old in bucket.objects


def test_needs_a_built_site(bucket, tmp_path):
    with pytest.raises(SystemExit, match="no index.html"):
        deploy.publish(bucket, "b", tmp_path, "t.publick.org", now=NOW)


def publish_days(bucket, site, domain, days):
    names = []
    for day in range(days):
        (site / "index.html").write_text(f"<h1>Day {day}</h1>")
        bucket.clock = NOW + timedelta(days=day)
        names.append(deploy.publish(bucket, "b", site, domain, now=bucket.clock)["build"])
    return names


def test_rollback_makes_the_previous_build_live(bucket, tmp_path):
    site = make_site(tmp_path, {})
    names = publish_days(bucket, site, "t.publick.org", 3)
    assert deploy.rollback(bucket, "b", "t.publick.org") == {"domain": "t.publick.org", "build": names[1],
                                                               "previous": names[2]}
    assert deploy.rollback(bucket, "b", "t.publick.org")["build"] == names[0]
    with pytest.raises(SystemExit, match="no build before"):
        deploy.rollback(bucket, "b", "t.publick.org")
    assert deploy.rollback(bucket, "b", "t.publick.org", names[2])["build"] == names[2]
    assert bucket.json("sites/t.publick.org/current.json")["build"] == names[2]


def test_rollback_refuses_a_build_with_missing_files(bucket, tmp_path):
    site = make_site(tmp_path, {})
    names = publish_days(bucket, site, "t.publick.org", 2)
    old = bucket.json(f"sites/t.publick.org/builds/{names[0]}.json")
    del bucket.objects[f"blobs/{old['files']['index.html']['blob']}"]
    with pytest.raises(SystemExit, match="missing 1 files"):
        deploy.rollback(bucket, "b", "t.publick.org")
    assert bucket.json("sites/t.publick.org/current.json")["build"] == names[1]


def test_prune_keeps_recent_and_live_builds_and_their_files(bucket, tmp_path):
    site = make_site(tmp_path / "t", {"static/site.css": "body{}"})
    names = publish_days(bucket, site, "t.publick.org", 6)
    other = make_site(tmp_path / "o", {"static/site.css": "body{}"})
    deploy.publish(bucket, "b", other, "o.publick.org", now=NOW)
    deploy.rollback(bucket, "b", "t.publick.org", names[0])
    bucket.clock = NOW + timedelta(days=10)

    before = set(bucket.objects)
    dry = deploy.prune(bucket, "b", keep=2, dry_run=True, now=bucket.clock)
    assert dry["builds_deleted"] == 3 and dry["blobs_deleted"] == 3
    assert set(bucket.objects) == before

    result = deploy.prune(bucket, "b", keep=2, now=bucket.clock)
    assert result == {**dry, "dry_run": False} and result["sites"] == 2
    assert deploy.builds(bucket, "b", "t.publick.org") == [names[0], names[4], names[5]]
    for domain in ("t.publick.org", "o.publick.org"):
        for build in deploy.builds(bucket, "b", domain):
            for entry in bucket.json(f"sites/{domain}/builds/{build}.json")["files"].values():
                assert f"blobs/{entry['blob']}" in bucket.objects


def test_prune_spares_new_files_not_yet_in_a_manifest(bucket, tmp_path):
    site = make_site(tmp_path, {})
    deploy.publish(bucket, "b", site, "t.publick.org", now=NOW)
    bucket.put_object(Bucket="b", Key="blobs/" + "0" * 64, Body=b"uploading")
    assert deploy.prune(bucket, "b", now=NOW + timedelta(hours=1))["blobs_deleted"] == 0
    assert deploy.prune(bucket, "b", now=NOW + timedelta(days=3))["blobs_deleted"] == 1


def test_content_types():
    assert deploy.content_type("311/data/requests.csv") == "text/csv; charset=utf-8"
    assert deploy.content_type("static/wards.geojson") == "application/geo+json; charset=utf-8"
    assert deploy.content_type("static/share/gloucester.png") == "image/png"
    assert deploy.content_type("meetings/agendas/a.pdf") == "application/pdf"
    assert deploy.content_type("static/favicon.svg") == "image/svg+xml; charset=utf-8"
    assert deploy.content_type("unknown.zzz") == "application/octet-stream"


WORKER = Path(__file__).parent.parent / "worker" / "index.js"
SERVE_ALL = """
import { readFileSync, existsSync } from "node:fs";
import worker from "WORKER";
const root = process.argv[1], paths = JSON.parse(process.argv[2]);
const SITES = { async get(key) {
  const file = `${root}/${key}`;
  if (!existsSync(file)) return null;
  const bytes = readFileSync(file);
  return { body: bytes, json: async () => JSON.parse(bytes) };
} };
const out = {};
for (const path of paths) {
  const r = await worker.fetch(new Request(`https://gloucester-ma.publick.org${path}`), { SITES });
  out[path] = [r.status, r.headers.get("Content-Type"), (await r.arrayBuffer()).byteLength];
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node for the Worker")
def test_worker_serves_every_published_page(bucket, site_dir, tmp_path):
    from conftest import PAGE_PATHS

    deploy.publish(bucket, "b", site_dir, "gloucester-ma.publick.org", now=NOW)
    for key, obj in bucket.objects.items():
        (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / key).write_bytes(obj["body"])
    paths = [*PAGE_PATHS, "/static/css/site.css", "/feed.xml", "/meetings", "/no-such-page/"]
    script = SERVE_ALL.replace("WORKER", WORKER.as_uri())
    run = subprocess.run(["node", "--input-type=module", "-e", script, str(tmp_path), json.dumps(paths)],
                         capture_output=True, text=True, check=True)
    served = json.loads(run.stdout)
    for path in PAGE_PATHS:
        rel = path.lstrip("/") + ("index.html" if path.endswith("/") else "")
        assert served[path] == [200, "text/html; charset=utf-8", (site_dir / rel).stat().st_size], path
    assert served["/static/css/site.css"][:2] == [200, "text/css; charset=utf-8"]
    assert served["/feed.xml"][0] == 200
    assert served["/meetings"][0] == 301
    assert served["/no-such-page/"][:2] == [404, "text/html; charset=utf-8"]


def test_prune_reports_failed_deletions(bucket, tmp_path):
    site = make_site(tmp_path, {})
    publish_days(bucket, site, "t.publick.org", 3)
    bucket.delete_objects = lambda Bucket, Delete: {"Errors": [{"Key": Delete["Objects"][0]["Key"], "Message": "denied"}]}
    with pytest.raises(SystemExit, match="1 deletions failed.*denied"):
        deploy.prune(bucket, "b", keep=1, now=NOW + timedelta(days=10))
