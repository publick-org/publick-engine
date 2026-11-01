"""A small HTTP client that is gentle with public servers.

City websites rate-limit aggressively, so every request identifies the
project, waits between calls, and backs off on errors instead of hammering.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}
# The longest a server's Retry-After is waited for, in seconds.
MAX_RETRY_AFTER = 300


def retry_after(value: str, now: datetime | None = None) -> float | None:
    """A Retry-After header in seconds, given as seconds ("120") or as a date
    ("Wed, 21 Oct 2026 07:28:00 GMT"), at most MAX_RETRY_AFTER; None if it has neither."""
    value = (value or "").strip()
    if value.isdigit():
        return min(int(value), MAX_RETRY_AFTER)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


# The folder of HostLimiter's files: by default one in the system's temporary folder, shared by
# every process on the machine (the towns of one network job, each run in its own process).
RATE_DIR_ENV = "PUBLICK_RATE_DIR"


class HostLimiter:
    """Spaces requests to a host across every process on this machine, so towns that share a
    source share its rate limit rather than each keeping its own. Each host has a small file:
    when it was last asked, and until when it asked us to wait (Retry-After), read and written
    under a lock. A process waits its turn holding the lock, so the others queue behind it."""

    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory or os.environ.get(RATE_DIR_ENV) or Path(tempfile.gettempdir()) / "publick-rate")

    def _update(self, host: str, change) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / f"{host}.json", "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                state = json.loads(f.read() or "{}")
            except ValueError:
                state = {}
            state = change(state)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(state))
            f.flush()

    def wait(self, host: str, interval: float, lane: str | None = None, lane_interval: float = 0.0) -> None:
        """Wait until interval seconds after the host was last asked by any process, and past any
        Retry-After it gave; then mark it asked now. A request in a lane (one path of the host, such
        as an API's listing) also waits lane_interval after the lane was last asked."""
        def turn(state: dict) -> dict:
            lanes = state.get("lanes", {})
            ready = max(state.get("last", 0.0) + interval, state.get("until", 0.0),
                        lanes.get(lane, 0.0) + lane_interval if lane else 0.0)
            # Never longer than the longest Retry-After honoured, should the clock have jumped.
            wait = min(ready - time.time(), max(interval, lane_interval, MAX_RETRY_AFTER))
            if wait > 0:
                time.sleep(wait)
            asked = time.time()
            return {**state, "last": asked, **({"lanes": {**lanes, lane: asked}} if lane else {})}
        self._update(host, turn)

    def hold(self, host: str, seconds: float) -> None:
        """The host asked for no requests for this many seconds (Retry-After): every process waits."""
        self._update(host, lambda state: {**state, "until": max(state.get("until", 0.0), time.time() + seconds)})

    def refuse(self, host: str, lane: str, seconds: float) -> None:
        """The host refused a lane: no process asks it again for this many seconds."""
        self._update(host, lambda state: {**state, "refused": {**state.get("refused", {}), lane: time.time() + seconds}})

    def refused_until(self, host: str, lane: str) -> float | None:
        """When the host's refusal of a lane ends (a time.time()), or None if it isn't refused."""
        found = []

        def read(state: dict) -> dict:
            found.append(state.get("refused", {}).get(lane, 0.0))
            return state
        self._update(host, read)
        return found[0] if found[0] > time.time() else None


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Refused(FetchError):
    """A lane the host refused (Lane): this request, or another process's within the cool-down."""


@dataclass(frozen=True)
class Lane:
    """Requests to one path of a host (an API's listing, not the lookups under it) that keep their
    own, longer interval between them, and that the host may refuse on their own. A 403, or a 429
    still answered after every retry, is a refusal: no process on the machine asks the path again
    for cooldown seconds, and each request in that time raises Refused without being sent."""
    path: str
    interval: float
    cooldown: float


class PoliteClient:
    def __init__(self, user_agent: str, delay: float = 3.0, retries: int = 3, timeout: float = 30.0,
                 limiter: HostLimiter | None = None, lanes: tuple[Lane, ...] = ()):
        """With a limiter, the delay is kept between any two requests to a host from any process
        on the machine using one, not only this client's own, and so are the lanes' intervals and
        refusals. Without one, they're kept by this client alone."""
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.limiter = limiter
        self.lanes = {lane.path: lane for lane in lanes}
        self._last = 0.0
        self._lane_last: dict[str, float] = {}
        self._refused: dict[str, float] = {}
        self.request_count = 0

    def _refused_until(self, host: str, lane: Lane) -> float | None:
        if self.limiter:
            return self.limiter.refused_until(host, lane.path)
        until = self._refused.get(lane.path, 0.0)
        return until if until > time.time() else None

    def _refuse(self, host: str, lane: Lane, status: int) -> Refused:
        if self.limiter:
            self.limiter.refuse(host, lane.path, lane.cooldown)
        else:
            self._refused[lane.path] = time.time() + lane.cooldown
        # The host and path only: never the query, which may carry a key.
        return Refused(f"{host}{lane.path}: HTTP {status}", status)

    def _wait(self, host: str, lane: Lane | None) -> None:
        if self.limiter:
            self.limiter.wait(host, self.delay, lane.path if lane else None, lane.interval if lane else 0.0)
            return
        ready = max(self._last + self.delay, self._lane_last.get(lane.path, 0.0) + lane.interval if lane else 0.0)
        wait = ready - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        if lane:
            self._lane_last[lane.path] = time.monotonic()

    def get(self, url: str) -> requests.Response:
        return self._request(url)

    def location(self, url: str) -> str | None:
        """Where a link that redirects leads (a short link's file): the Location it answers with, read with a
        HEAD request (a GET without following it where the host refuses HEAD), spaced and retried as a GET is.
        None when it doesn't redirect."""
        try:
            response = self._request(url, method="HEAD")
        except FetchError as e:
            if e.status not in (405, 501):
                raise
            response = self._request(url, method="GET", follow=False)
        target = response.headers.get("Location") if 300 <= response.status_code < 400 else None
        return urljoin(url, target) if target else None

    def post_json(self, url: str, body: dict) -> requests.Response:
        """A POST of a JSON body, as a web service asks for its answer (a SharpSchool site's
        listing of a folder), spaced and retried as a GET is."""
        return self._request(url, body)

    def _request(self, url: str, body: dict | None = None, method: str = "GET",
                 follow: bool = True) -> requests.Response:
        last_error, asked = None, None
        parts = urlsplit(url)
        host = parts.hostname or ""
        lane = self.lanes.get(parts.path.rstrip("/"))
        if lane and (until := self._refused_until(host, lane)):
            ends = datetime.fromtimestamp(until, timezone.utc).strftime("%H:%M UTC")
            raise Refused(f"{host}{lane.path}: refused earlier, not asked again before {ends}", 403)
        for attempt in range(self.retries + 1):
            if attempt:
                # 10s, 30s, 90s, or longer if the server asked for longer.
                time.sleep(max(10 * 3 ** (attempt - 1), asked or 0))
            self._wait(host, lane)
            self._last = time.monotonic()
            self.request_count += 1
            try:
                if body is not None:
                    response = self.session.post(url, json=body, timeout=self.timeout)
                elif method == "HEAD":
                    response = self.session.head(url, timeout=self.timeout, allow_redirects=False)
                elif not follow:
                    response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
                else:
                    response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                last_error, asked = e, None
                continue
            if response.status_code in RETRY_STATUS:
                last_error = FetchError(f"HTTP {response.status_code}", response.status_code)
                asked = retry_after(response.headers.get("Retry-After", ""))
                if asked and self.limiter:
                    self.limiter.hold(host, asked)
                continue
            if lane and response.status_code == 403:
                raise self._refuse(host, lane, 403)
            if response.status_code >= 400:
                raise FetchError(f"{url}: HTTP {response.status_code}", response.status_code)
            return response
        if lane and getattr(last_error, "status", None) == 429:
            # Still too many after every retry and every Retry-After: refused.
            raise self._refuse(host, lane, 429)
        raise FetchError(f"{url}: {last_error}", getattr(last_error, "status", None))
