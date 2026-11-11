"""A preview of a town's site at another address (build_site's --preview): the same pages, kept out of search
results and out of the page view counts."""

import re

import pytest

import test_spanish_site
from conftest import BUILT_AT, DATA_DIR
from pipeline import build_site, i18n

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


def test_no_file_names_the_town_s_address(preview_dir, config):
    # Feeds, the sitemap, the data files and the calendar too, with the calendar's folded lines joined.
    for path in sorted(p for p in preview_dir.rglob("*") if p.is_file()):
        if path.suffix in (".html", ".xml", ".json", ".csv", ".ics", ".txt", ".js", ".css"):
            text = path.read_text(encoding="utf-8").replace("\r\n ", "").replace("\n ", "")
            assert config["site"]["domain"] not in text, path


def test_another_language_s_pages_are_noindex_too(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site, "load_config", test_spanish_site.bilingual_config)
    monkeypatch.setattr(i18n, "strings", lambda lang: test_spanish_site.ENGINE_STRINGS if lang == "es" else {})
    build_site.build("gloucester", tmp_path, data_dir=DATA_DIR, now=BUILT_AT, preview=HOST)
    pages = sorted((tmp_path / "es").rglob("*.html"))
    assert len(pages) > 20
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert '<meta name="robots" content="noindex">' in html, page
        assert "goatcounter" not in html, page
        assert f'<link rel="canonical" href="https://{HOST}/' in html, page


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


def test_a_preview_isn_t_built_for_the_town_s_own_address(config):
    for host in (config["site"]["domain"], "www." + config["site"]["domain"]):
        with pytest.raises(SystemExit):
            build_site.preview_config(config, host)


def test_the_command_passes_the_address_on(monkeypatch, tmp_path):
    seen = {}

    def build(town, out, data, missing, share_cards, problems, preview=None):
        seen.update(out=out, preview=preview)
        return {}
    monkeypatch.setattr(build_site, "build", build)
    monkeypatch.setattr("sys.argv", ["build_site", "--town", "gloucester", "--out", str(tmp_path), "--preview", HOST])
    build_site.main()
    assert seen == {"out": tmp_path, "preview": HOST}


def test_the_command_won_t_build_a_preview_into_the_town_s_own_site(monkeypatch, capsys):
    monkeypatch.setattr(build_site, "build", lambda *a, **k: pytest.fail("built"))
    for out in ([], ["--out", str(build_site.TOWN_DIR / "_site")]):
        monkeypatch.setattr("sys.argv", ["build_site", "--town", "gloucester", "--preview", HOST, *out])
        with pytest.raises(SystemExit):
            build_site.main()
    assert "--preview needs --out" in capsys.readouterr().err
