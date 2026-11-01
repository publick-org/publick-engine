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


def test_a_short_links_location_is_read_with_a_head_request(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    c = PoliteClient("test", delay=0)
    asked = []
    target = "https://files-backend.assets.thrillshare.com/documents/asset/uploaded_file/4049/Sbsd/x/Minutes.pdf"
    c.session = SimpleNamespace(head=lambda url, timeout, allow_redirects: asked.append((url, allow_redirects)) or
                                SimpleNamespace(status_code=301, headers={"Location": target}))
    assert c.location("https://aptg.co/Lj5CdR") == target
    assert asked == [("https://aptg.co/Lj5CdR", False)]
    # A relative Location is read against the link; a link that doesn't redirect has none.
    c.session.head = lambda url, timeout, allow_redirects: SimpleNamespace(status_code=302, headers={"Location": "/f"})
    assert c.location("https://aptg.co/abc") == "https://aptg.co/f"
    c.session.head = lambda url, timeout, allow_redirects: SimpleNamespace(status_code=200, headers={})
    assert c.location("https://aptg.co/abc") is None


def test_a_host_that_refuses_head_is_asked_with_a_get_not_followed(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    c = PoliteClient("test", delay=0)
    gets = []
    c.session = SimpleNamespace(
        head=lambda url, timeout, allow_redirects: SimpleNamespace(status_code=405, headers={}),
        get=lambda url, timeout, allow_redirects: gets.append(allow_redirects) or SimpleNamespace(
            status_code=301, headers={"Location": "https://example.org/a.pdf"}))
    assert c.location("https://aptg.co/abc") == "https://example.org/a.pdf" and gets == [False]


def test_a_json_post_is_retried_as_a_get_is(monkeypatch):
    slept, sent = [], []
    monkeypatch.setattr(http.time, "sleep", slept.append)
    c = PoliteClient("test", delay=0)
    answers = iter([500, 200])
    c.session = SimpleNamespace(post=lambda url, json, timeout: sent.append(json) or SimpleNamespace(
        status_code=next(answers), headers={}))
    assert c.post_json("https://example.org/svc", {"parentId": "1"}).status_code == 200
    assert sent == [{"parentId": "1"}] * 2 and slept == [10] and c.request_count == 2


def test_the_longer_of_retry_after_and_the_backoff_is_waited(monkeypatch):
    c, slept = client([(429, {"Retry-After": "45"}), (429, {"Retry-After": "5"}), (200,)], monkeypatch)
    c.get("https://example.org")
    assert slept == [45, 30]


def test_it_gives_up_after_its_retries(monkeypatch):
    c, slept = client([(503,)] * 4, monkeypatch)
    with pytest.raises(FetchError) as e:
        c.get("https://example.org")
    assert e.value.status == 503 and slept == [10, 30, 90]


# APIv2's listing, as fetch_311 paces it: its own interval, and a cool-down once refused.
LISTING = http.Lane("/api/v2/issues", interval=8.0, cooldown=6 * 3600)
LIST_URL = "https://seeclickfix.com/api/v2/issues?request_types=1&per_page=1&page=1"
ONE_URL = "https://seeclickfix.com/api/v2/issues/123"


class Server:
    """Answers each request with (status, headers) from answer(url), and keeps the URLs asked."""

    def __init__(self, answer):
        self.answer, self.urls, self.headers = answer, [], {}

    def get(self, url, timeout):
        self.urls.append(url)
        status, headers = self.answer(url)
        return SimpleNamespace(status_code=status, headers=headers)


@pytest.fixture
def clock(monkeypatch):
    """A clock that moves only when the code sleeps."""
    now = [1000.0]
    monkeypatch.setattr(http.time, "time", lambda: now[0])
    monkeypatch.setattr(http.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(http.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    return now


def town(rate_dir, answer, limiter=True):
    """One town's fetch: its own process and client, sharing the machine's rate files."""
    c = PoliteClient("test", delay=3.2, lanes=(LISTING,), limiter=http.HostLimiter(rate_dir) if limiter else None)
    c.session = Server(answer)
    return c


def ok(url):
    return 200, {}


def test_a_refused_listing_is_left_alone_by_every_process_until_its_cool_down_ends(tmp_path, clock):
    """A 403 on the listing is a refusal: the other towns' processes don't ask it again until the
    cool-down ends, and their lookups go on meanwhile."""
    blocked = lambda url: (403, {}) if "/issues?" in url else (200, {})
    town_a, town_b = town(tmp_path, blocked), town(tmp_path, blocked)
    with pytest.raises(http.Refused) as e:
        town_a.get(LIST_URL)
    # Said without the query, which may carry a key.
    assert e.value.status == 403 and str(e.value) == "seeclickfix.com/api/v2/issues: HTTP 403"
    with pytest.raises(http.Refused):
        town_b.get(LIST_URL)
    assert town_b.session.urls == []
    assert town_b.get(ONE_URL).status_code == 200
    clock[0] += LISTING.cooldown - 60
    with pytest.raises(http.Refused):
        town_b.get(LIST_URL)
    # The cool-down over, the listing is asked again.
    clock[0] += 61
    town_b.session = Server(ok)
    assert town_b.get(LIST_URL).status_code == 200 and len(town_b.session.urls) == 1


def test_a_403_on_one_request_is_not_a_refused_listing(tmp_path, clock):
    """A lookup's 403 is that request made private: an error of its own, and the listing goes on."""
    c = town(tmp_path, lambda url: (403, {}) if "/issues/" in url else (200, {}))
    with pytest.raises(FetchError) as e:
        c.get(ONE_URL)
    assert e.value.status == 403 and not isinstance(e.value, http.Refused)
    assert town(tmp_path, ok).get(LIST_URL).status_code == 200


def test_the_listing_honours_retry_after_and_is_refused_only_once_the_retries_are_spent(tmp_path, clock):
    answers = iter([(429, {"Retry-After": "120"}), (200, {})])
    c = town(tmp_path, lambda url: next(answers))
    start = clock[0]
    assert c.get(LIST_URL).status_code == 200 and clock[0] - start >= 120
    assert town(tmp_path, ok).get(LIST_URL).status_code == 200  # not refused
    c = town(tmp_path, lambda url: (429, {"Retry-After": "60"}))
    with pytest.raises(http.Refused) as e:
        c.get(LIST_URL)
    assert e.value.status == 429 and len(c.session.urls) == 4
    other = town(tmp_path, ok)
    with pytest.raises(http.Refused):
        other.get(LIST_URL)
    # A server error after every retry is an error, not a refusal.
    c = town(tmp_path / "other", lambda url: (503, {}))
    with pytest.raises(FetchError) as e:
        c.get(LIST_URL)
    assert e.value.status == 503 and not isinstance(e.value, http.Refused)
    assert town(tmp_path / "other", ok).get(LIST_URL).status_code == 200


def test_the_listing_keeps_its_own_longer_interval_across_processes(tmp_path, clock):
    """Listing requests from any process are LISTING.interval apart; a lookup waits only the
    host's delay, after either."""
    a, b = town(tmp_path, ok), town(tmp_path, ok)
    a.get(LIST_URL)
    start = clock[0]
    b.get(ONE_URL)
    assert clock[0] - start == pytest.approx(3.2)
    b.get(LIST_URL)
    assert clock[0] - start == pytest.approx(8.0)
    a.get(ONE_URL)
    assert clock[0] - start == pytest.approx(11.2)


def test_without_a_limiter_the_client_keeps_the_lane_itself(clock):
    c = town(None, lambda url: (403, {}) if "/issues?" in url else (200, {}), limiter=False)
    c.get(ONE_URL)
    with pytest.raises(http.Refused):
        c.get(LIST_URL)
    start = clock[0]
    c.session.answer = ok
    with pytest.raises(http.Refused):
        c.get(LIST_URL)
    assert len([u for u in c.session.urls if "/issues?" in u]) == 1
    clock[0] += LISTING.cooldown
    c.get(LIST_URL)
    c.get(LIST_URL)
    assert clock[0] - start == pytest.approx(LISTING.cooldown + LISTING.interval)
