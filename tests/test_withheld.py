"""A summary taken down by hand ([[summaries.withheld]]) after a reader reported an error."""

import copy
import json
import re

import pytest

from pipeline import build_site


def meeting(id_, body, day, **extra):
    return {"id": id_, "body": body, "date": day, "listings": [], **extra}


MEETINGS = [meeting("a1", "City Council", "2026-09-08"), meeting("a2", "Planning Board", "2026-09-08"),
            meeting("a3", "City Council", "2026-09-22", listings=[{"id": "ac-9"}])]


def test_an_entry_names_one_meeting_by_board_and_date_or_by_id():
    withheld = [{"board": "city council", "date": "2026-09-08", "kind": "minutes"},
                {"meeting": "ac-9", "kind": "agenda"}]
    assert build_site.withheld_summaries(MEETINGS, withheld) == {("a1", "minutes"), ("a3", "agenda")}


@pytest.mark.parametrize("entry", [
    {"board": "City Council", "date": "2026-09-09", "kind": "minutes"},   # no meeting that day
    {"meeting": "nope", "kind": "minutes"},
    {"board": "City Council", "date": "2026-09-08", "kind": "summary"},   # not a kind
    {"board": "City Council", "date": "2026-09-08"},
])
def test_an_entry_that_doesnt_name_one_meeting_stops_the_build(entry):
    # A typo mustn't leave up a summary meant to come down: the pull request's run fails instead.
    with pytest.raises(SystemExit, match=r"summaries\.withheld"):
        build_site.withheld_summaries(MEETINGS, [entry])
    with pytest.raises(SystemExit):
        build_site.withheld_summaries(MEETINGS + [meeting("a4", "City Council", "2026-09-08")],
                                      [{"board": "City Council", "date": "2026-09-08", "kind": "minutes"}])


def test_a_withheld_summary_is_shown_nowhere(config, data_dir, tmp_path, monkeypatch):
    from conftest import BUILT_AT
    config = copy.deepcopy(config)  # the session's config, shared with other tests
    config["site"]["languages"] = ["en", "es"]
    model = config["summaries"]["model"]
    meetings = build_site.load_meetings(data_dir, BUILT_AT.date(), model)["all"]
    m = next(m for m in meetings if m["decisions"]["decided"] and m["preview"])
    decision, headline = m["decisions"]["decided"][0], m["minutes_summary"]["summary"]
    config["summaries"]["withheld"] = [
        {"board": m["body"], "date": m["date"], "kind": "minutes", "checked": "2026-10-07"},
        {"meeting": m["id"], "kind": "agenda", "checked": "2026-10-07"},
    ]
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data_dir, now=BUILT_AT)

    page = (out / "meetings" / m["slug"] / "index.html").read_text()
    assert "Their summary was taken down while a reported error is checked." in page
    assert "The agenda&#39;s summary was taken down" in page or "The agenda's summary was taken down" in page
    es = (out / "es" / "meetings" / m["slug"] / "index.html").read_text()
    assert "Su resumen se retiró" in es
    # The fake model writes the same lines for every document, so each place is checked for this
    # meeting's own entry: no decision, minutes summary, or agenda summary anywhere it's listed.
    url = f"/meetings/{m['slug']}/"
    gone = [decision, headline, *[x for x in (m["preview"].get("headline"), m["preview"].get("summary")) if x]]
    for text in (page, es):
        assert not any(g in text for g in gone)
    assert url not in (out / "meetings" / "decisions" / "index.html").read_text()
    assert m["slug"] not in (out / "meetings" / "data" / "decisions.csv").read_text()
    search = json.loads((out / "meetings" / "search-index.json").read_text())
    entries = [e for e in (search if isinstance(search, list) else search.get("meetings", [])) if url in json.dumps(e)]
    assert entries and not any(g in json.dumps(e) for e in entries for g in (decision, headline))
    for listing in ("feed.xml", "meetings/past/index.html", f"meetings/boards/{m['body_slug']}/index.html"):
        text = (out / listing).read_text()
        chunks = [c for c in re.split(r"<item>|<li", text) if url in c]
        assert not any(g in c for c in chunks for g in gone), listing
    # The documents themselves are still linked.
    assert "Saved copy of the minutes" in page
