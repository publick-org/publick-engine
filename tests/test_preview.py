"""A preview of a town's site at another address (build_site's --preview): the same pages, kept out of search
results and out of the page view counts."""

import re

import pytest

from conftest import BUILT_AT, DATA_DIR
from pipeline import build_site

HOST = "preview-gloucester-ma.example.org"


@pytest.fixture(scope="module")
def preview_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("preview")
    build_site.build("gloucester", out, data_dir=DATA_DIR, now=BUILT_AT, preview=HOST)
    return out


def test_every_page_is_noindex(preview_dir):
    pages = sorted(preview_dir.rglob("*.html"))
    assert len(pages) > 50
    for page in pages:
        assert '<meta name="robots" content="noindex">' in page.read_text(encoding="utf-8"), page


def test_pages_name_the_preview_address_not_the_town_s(preview_dir, config):
    for page in sorted(preview_dir.rglob("*.html")):
        html = page.read_text(encoding="utf-8")
        for address in re.findall(r'<link rel="canonical" href="([^"]+)"', html):
            assert address.startswith(f"https://{HOST}/"), page
        assert f"https://{config['site']['domain']}" not in html, page


def test_no_page_views_are_counted(preview_dir, site_dir):
    assert "goatcounter" in (site_dir / "index.html").read_text(encoding="utf-8")
    for page in sorted(preview_dir.rglob("*.html")):
        assert "goatcounter" not in page.read_text(encoding="utf-8"), page


def test_crawlers_are_turned_away(preview_dir):
    assert (preview_dir / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n"
    assert (preview_dir / "CNAME").read_text().strip() == HOST


def test_the_town_s_own_site_is_unchanged(site_dir):
    assert "noindex" not in (site_dir / "index.html").read_text(encoding="utf-8")
    assert "Allow: /" in (site_dir / "robots.txt").read_text()


@pytest.mark.parametrize("host", ["", "Preview.Example.org", "preview", "a b.example.org", "https://x.example.org", "x.example.org/"])
def test_a_preview_needs_a_hostname(host, tmp_path):
    with pytest.raises(SystemExit):
        build_site.preview_config({"site": {"domain": "town.example.org"}}, host)
