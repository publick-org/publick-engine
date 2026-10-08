"""The sample of pages a daily run gives the browser checks (site_checks/pages.py)."""


from conftest import PAGE_PATHS, SITE_DIR

from pipeline import build_site
from site_checks.pages import parent, sample

HAND_WRITTEN = {build_site.url_for(p.relative_to(build_site.PAGES_DIR)) for p in build_site.PAGES_DIR.rglob("*.html")}


def size(path: str) -> int:
    return (SITE_DIR / (path.lstrip("/") + ("index.html" if path.endswith("/") else ""))).stat().st_size


def test_parent():
    assert parent("/meetings/2026-10-05-city-council/") == "/meetings/"
    assert parent("/311/ward/1/") == "/311/ward/"
    assert parent("/about/") == "/"


def test_sample_keeps_every_hand_written_page_and_some_of_each_record_template():
    chosen = sample(PAGE_PATHS, size, HAND_WRITTEN)
    assert set(chosen) <= set(PAGE_PATHS)
    assert HAND_WRITTEN & set(PAGE_PATHS) <= set(chosen)
    records = [p for p in PAGE_PATHS if p not in HAND_WRITTEN]
    assert records, "the fixture site has meeting, board, ward and category pages"
    for folder in {parent(p) for p in records}:
        members = [p for p in records if parent(p) == folder]
        picked = [p for p in chosen if p in members]
        assert min(members) in picked and max(members, key=lambda p: (size(p), p)) in picked
        assert len(picked) <= 2
    assert len(chosen) < len(PAGE_PATHS) / 2


def test_sample_of_made_up_paths():
    sizes = {"/meetings/a/": 5, "/meetings/b/": 50, "/meetings/c/": 7, "/311/ward/1/": 1, "/meetings/": 3}
    chosen = sample(list(sizes), sizes.get, {"/meetings/"})
    assert chosen == ["/311/ward/1/", "/meetings/", "/meetings/a/", "/meetings/b/"]
