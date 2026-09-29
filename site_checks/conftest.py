"""Checks for one town's built site, run by the town workflow before it deploys.

PUBLICK_SITE_DIR is the built site (_site). The town's config comes from
PUBLICK_TOWN_DIR or the current directory, as for every other command.

    python -m pytest <engine>/site_checks
"""

import functools
import http.server
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import build_site  # noqa: E402
from pipeline.config import DEFAULT_TOWN, TOWN_DIR, load_config  # noqa: E402
from site_checks.pages import sample  # noqa: E402

SITE_DIR = Path(os.environ.get("PUBLICK_SITE_DIR") or TOWN_DIR / "_site").resolve()
if not (SITE_DIR / "index.html").exists():
    raise pytest.UsageError(f"No built site at {SITE_DIR}. Build it first, or set PUBLICK_SITE_DIR.")
PAGE_PATHS = sorted(build_site.url_for(p.relative_to(SITE_DIR)) for p in SITE_DIR.rglob("*.html") if p.name != "404.html")


def _page_size(path: str) -> int:
    return (SITE_DIR / (path.lstrip("/") + ("index.html" if path.endswith("/") else ""))).stat().st_size


# The pages the browser checks run on: all of them, or a sample (site_checks/pages.py).
HAND_WRITTEN = {build_site.url_for(p.relative_to(build_site.PAGES_DIR)) for p in build_site.PAGES_DIR.rglob("*.html")}
BROWSER_PATHS = (sample(PAGE_PATHS, _page_size, HAND_WRITTEN) if os.environ.get("PUBLICK_CHECK_PAGES") == "sample"
                 else PAGE_PATHS)


@pytest.fixture(scope="session")
def site_dir():
    return SITE_DIR


@pytest.fixture(scope="session")
def config():
    return load_config(DEFAULT_TOWN)


@pytest.fixture(scope="session")
def page_files():
    return sorted(SITE_DIR.rglob("*.html"))


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Static server that mimics GitHub Pages: /foo -> /foo/index.html, 404.html for misses."""

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        page = Path(self.directory) / "404.html"
        if code == 404 and page.exists():
            body = page.read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().send_error(code, message, explain)


@pytest.fixture(scope="session")
def server_url():
    handler = functools.partial(_Handler, directory=str(SITE_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
