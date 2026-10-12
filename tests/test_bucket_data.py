"""311's working files kept in the town's bucket, not git (pipeline/bucket_data.py)."""

import json
import shutil

import pytest

from conftest import FETCHED_AT, REAL_DATA_DIR
from fakes import FakeSeeClickFix
from pipeline import bucket_data, fetch_311
from pipeline.config import load_config
from pipeline.http import FetchError


class FakeResponse:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeBucket:
    """The bucket through its keys (boto3's client), as a dict of key -> (body, headers)."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        body = self.objects[Key][0]
        return {"Body": type("Body", (), {"read": lambda self: body})()}

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.objects[Key] = (Body, {"ContentType": ContentType, "CacheControl": CacheControl})


@pytest.fixture
def config(monkeypatch):
    monkeypatch.delenv("DOCUMENTS_LOCAL", raising=False)
    monkeypatch.delenv("STORAGE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("STORAGE_SECRET_ACCESS_KEY", raising=False)
    return load_config("gloucester")


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("STORAGE_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("STORAGE_SECRET_ACCESS_KEY", "secret")
    bucket = FakeBucket()
    monkeypatch.setattr(bucket_data.documents, "make_client", lambda settings: bucket)
    return bucket


def test_files_are_read_from_the_public_address_without_keys(config, tmp_path, monkeypatch):
    prefix = config["storage"].get("prefix", config["slug"])
    asked = []

    def get(url, timeout, headers):
        asked.append(url)
        return FakeResponse(200, b'{"a": 1}') if url.endswith("requests.json") else FakeResponse(404)

    monkeypatch.setattr(bucket_data.requests, "get", get)
    assert bucket_data.pull(config, tmp_path) == ["311/requests.json"]
    assert asked[0] == f"https://files.publick.org/{prefix}/data/311/requests.json"
    assert json.loads((tmp_path / "311" / "requests.json").read_text()) == {"a": 1}
    # Not in the bucket: left as it is. Already here: not read again, unless refreshed.
    assert not (tmp_path / "311" / "scorecard.json").exists()
    asked.clear()
    assert bucket_data.pull(config, tmp_path) == [] and asked == [f"https://files.publick.org/{prefix}/data/311/scorecard.json"]
    monkeypatch.setattr(bucket_data.requests, "get", lambda url, timeout, headers: FakeResponse(503))
    with pytest.raises(FetchError, match="HTTP 503"):
        bucket_data.pull(config, tmp_path, refresh=True)


def test_files_are_saved_with_the_keys(config, keys, tmp_path):
    (tmp_path / "311").mkdir()
    (tmp_path / "311" / "scorecard.json").write_text("{}")
    assert bucket_data.push(config, tmp_path, "311/scorecard.json")
    body, headers = keys.objects[bucket_data.key(config, "311/scorecard.json")]
    assert body == b"{}" and headers["CacheControl"] == "no-store"
    # With the keys, read from the bucket itself.
    (tmp_path / "311" / "scorecard.json").unlink()
    assert bucket_data.pull(config, tmp_path) == ["311/scorecard.json"]
    with pytest.raises(ValueError):
        bucket_data.push(config, tmp_path, "311/status.json")


def test_a_town_without_a_bucket_keeps_them_in_data(config, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENTS_LOCAL", "1")
    assert bucket_data.pull(config, tmp_path) == [] and not bucket_data.push(config, tmp_path, "311/requests.json")
    del config["storage"]
    monkeypatch.delenv("DOCUMENTS_LOCAL")
    assert not bucket_data.enabled(config)


def test_fetch_reads_the_bucket_and_saves_back(config, keys, tmp_path):
    shutil.copytree(REAL_DATA_DIR / "static", tmp_path / "static")
    fetch_311.run(config, FakeSeeClickFix(), tmp_path, now=FETCHED_AT, detail_limit=0)
    saved = keys.objects[bucket_data.key(config, "311/requests.json")][0]
    assert json.loads(saved) == json.loads((tmp_path / "311" / "requests.json").read_text())
    # A fresh checkout: the history comes from the bucket, not from nothing.
    (tmp_path / "311" / "requests.json").unlink()
    summary = fetch_311.run(config, FakeSeeClickFix(), tmp_path, now=FETCHED_AT, detail_limit=0)
    assert summary["new"] == 0 and summary["requests_total"] == len(json.loads(saved))


def test_fetch_never_starts_again_from_nothing(config, keys, tmp_path):
    shutil.copytree(REAL_DATA_DIR / "static", tmp_path / "static")
    fetch_311.run(config, FakeSeeClickFix(), tmp_path, now=FETCHED_AT, detail_limit=0)
    before = keys.objects[bucket_data.key(config, "311/requests.json")]
    # The bucket's copy lost, and none here: the run stops before saving anything over the history.
    keys.objects.clear()
    (tmp_path / "311" / "requests.json").unlink()
    with pytest.raises(FetchError, match="saved 311 requests are missing"):
        fetch_311.run(config, FakeSeeClickFix(), tmp_path, now=FETCHED_AT, detail_limit=0)
    assert not keys.objects and before
