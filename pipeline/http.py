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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

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

    def wait(self, host: str, interval: float) -> None:
        """Wait until interval seconds after the host was last asked by any process, and past any
        Retry-After it gave; then mark it asked now."""
        def turn(state: dict) -> dict:
            ready = max(state.get("last", 0.0) + interval, state.get("until", 0.0))
            # Never longer than the longest Retry-After honoured, should the clock have jumped.
            wait = min(ready - time.time(), max(interval, MAX_RETRY_AFTER))
            if wait > 0:
                time.sleep(wait)
            return {**state, "last": time.time()}
        self._update(host, turn)

    def hold(self, host: str, seconds: float) -> None:
        """The host asked for no requests for this many seconds (Retry-After): every process waits."""
        self._update(host, lambda state: {**state, "until": max(state.get("until", 0.0), time.time() + seconds)})


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PoliteClient:
    def __init__(self, user_agent: str, delay: float = 3.0, retries: int = 3, timeout: float = 30.0,
                 limiter: HostLimiter | None = None):
        """With a limiter, the delay is kept between any two requests to a host from any process
        on the machine using one, not only this client's own."""
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.limiter = limiter
        self._last = 0.0
        self.request_count = 0

    def get(self, url: str) -> requests.Response:
        last_error, asked = None, None
        host = urlsplit(url).hostname or ""
        for attempt in range(self.retries + 1):
            if attempt:
                # 10s, 30s, 90s, or longer if the server asked for longer.
                time.sleep(max(10 * 3 ** (attempt - 1), asked or 0))
            if self.limiter:
                self.limiter.wait(host, self.delay)
            else:
                wait = self.delay - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
            self._last = time.monotonic()
            self.request_count += 1
            try:
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
            if response.status_code >= 400:
                raise FetchError(f"{url}: HTTP {response.status_code}", response.status_code)
            return response
        raise FetchError(f"{url}: {last_error}", getattr(last_error, "status", None))
