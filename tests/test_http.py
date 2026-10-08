"""The polite HTTP client's retries (pipeline/http.py), without the network."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pipeline import http
from pipeline.http import FetchError, PoliteClient, retry_after

NOW = datetime(2026, 10, 21, 7, 27, 0, tzinfo=timezone.utc)


def client(responses, monkeypatch):
    """A client whose server answers with each (status, headers) in turn, and the sleeps it took."""
    slept = []
    monkeypatch.setattr(http.time, "sleep", slept.append)
    c = PoliteClient("test", delay=0)
    answers = iter(responses)
    c.session = SimpleNamespace(get=lambda url, timeout: SimpleNamespace(
        status_code=(a := next(answers))[0], headers=a[1] if len(a) > 1 else {}))
    return c, slept


def test_retry_after_in_seconds_or_as_a_date():
    assert retry_after("120") == 120
    assert retry_after("3600") == http.MAX_RETRY_AFTER
    assert retry_after("Wed, 21 Oct 2026 07:28:00 GMT", NOW) == 60
    assert retry_after("Wed, 21 Oct 2026 07:00:00 GMT", NOW) == 0  # already past
    assert retry_after("") is None and retry_after("soon") is None


def test_a_server_error_is_tried_again_and_a_missing_page_isnt(monkeypatch):
    c, slept = client([(503,), (200,)], monkeypatch)
    assert c.get("https://example.org").status_code == 200 and slept == [10] and c.request_count == 2
    c, slept = client([(404,), (200,)], monkeypatch)
    with pytest.raises(FetchError) as e:
        c.get("https://example.org")
    assert e.value.status == 404 and slept == []


def test_the_longer_of_retry_after_and_the_backoff_is_waited(monkeypatch):
    c, slept = client([(429, {"Retry-After": "45"}), (429, {"Retry-After": "5"}), (200,)], monkeypatch)
    c.get("https://example.org")
    assert slept == [45, 30]


def test_it_gives_up_after_its_retries(monkeypatch):
    c, slept = client([(503,)] * 4, monkeypatch)
    with pytest.raises(FetchError) as e:
        c.get("https://example.org")
    assert e.value.status == 503 and slept == [10, 30, 90]
