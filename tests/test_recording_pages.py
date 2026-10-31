"""A meeting's summary from its recording, where the minutes' would be: before the minutes, what it decided and what
was discussed; after, what was discussed, under the minutes'. And a link to the recording, always."""

import copy
import hashlib
import json
import re
import shutil

import pytest
from conftest import BUILT_AT
from fakes import FakeAnthropic
from test_recording_summaries import TRANSCRIPT

from pipeline import build_site, recordings, summarize


def recording(m: dict, n: int) -> dict:
    return {"id": f"telvue-{n}", "platform": "telvue", "board": m["body"], "date": m["date"], "meeting": m["id"],
            "title": f"{m['body']} - recording {n}", "url": f"https://videoplayer.telvue.com/player/x/media/{n}",
            "seconds": 4400, "found_at": "2026-09-30T06:00:00-04:00", "source": 0,
            "transcript": {"file": f"telvue-{n}.txt", "sha256": hashlib.sha256(f"{TRANSCRIPT}{n}".encode()).hexdigest(),
                           "bytes": len(TRANSCRIPT), "seconds": 4400, "transcribed_at": "2026-09-30T06:50:00-04:00"}}


@pytest.fixture(scope="module")
def built(config, data_dir, tmp_path_factory):
    """The fixture town with two meetings recorded: one with no minutes, and one whose minutes are summarized."""
    tmp = tmp_path_factory.mktemp("recordings")
    data = tmp / "data"
    shutil.copytree(data_dir, data)
    config = copy.deepcopy(config)
    config["site"]["languages"] = ["en", "es"]
    config["summaries"].pop("since", None)
    model = config["summaries"]["model"]
    meetings = build_site.load_meetings(data, BUILT_AT.date(), model)["all"]
    before = next(m for m in meetings if m["date"] < BUILT_AT.date().isoformat() and not m["minutes"]
                  and m["status"] == "scheduled")
    after = next(m for m in meetings if m["decisions"]["decided"] and m["status"] == "scheduled")
    (data / "meetings" / "transcripts").mkdir(parents=True, exist_ok=True)
    store = {}
    for n, m in ((1, before), (2, after)):
        (data / "meetings" / "transcripts" / f"telvue-{n}.txt").write_text(f"{TRANSCRIPT}{n}")
        store[f"telvue-{n}"] = recording(m, n)
    recordings.save(data, store)
    summarize.run(config, FakeAnthropic(), data, limit=50, now=BUILT_AT)
    # The meeting whose minutes came is summarized from its recording no more: its summary made before they came.
    made = summarize.cached(data, store["telvue-1"]["transcript"]["sha256"], "recording")
    summarize.save_record(data, store["telvue-2"]["transcript"]["sha256"], made)
    import pipeline.build_site as site
    original = site.load_config
    site.load_config = lambda slug: config
    try:
        out = tmp / "site"
        build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    finally:
        site.load_config = original
    return out, before, after


def section(page: str, start: str, end: str = '<h2 id="agenda">') -> str:
    return page[page.index(start):page.index(end, page.index(start))]


def test_before_the_minutes_the_recording_is_what_was_decided(built):
    out, before, _ = built
    page = (out / "meetings" / before["slug"] / "index.html").read_text()
    decided = section(page, '<h2 id="minutes">')
    assert "From the meeting&#39;s recording. The official minutes come later" in decided or \
        "From the meeting's recording. The official minutes come later" in decided
    assert "Decisions heard in the recording" in decided
    # Each decision a row: its outcome and the vote count it ends with beside it, then the decision without the count.
    assert re.search(r'<span class="outcome">Approved <span class="tally">10–0</span></span>\s*'
                     r'<span class="decision-text">A \$1.5 million supplemental appropriation for the Fire Department\.<', decided)
    assert "Residents asked the council to keep the Flock camera pause in place." in decided
    assert "Checked against the transcript." in decided
    # The recording, its length, and its machine transcript, in the line of links under the date.
    links = section(page, '<p class="doc-links">', "</p>")
    assert 'href="https://videoplayer.telvue.com/player/x/media/1"' in links and "Recording" in links
    assert '<span class="runtime">(1 h 13 min)</span>' in links
    assert ">Machine transcript</a>" in links and "telvue-1.txt" in links
    assert (out / "meetings" / "transcripts" / "telvue-1.txt").read_text() == f"{TRANSCRIPT}1"
    es = (out / "es" / "meetings" / before["slug"] / "index.html").read_text()
    assert "Decisiones que se oyen en la grabación" in es and "Grabación" in es[es.index('<p class="doc-links">'):] and ">Aprobado <span" in es


def test_after_the_minutes_what_was_discussed_stays_under_them(built):
    out, _, after = built
    page = (out / "meetings" / after["slug"] / "index.html").read_text()
    decided = section(page, '<h2 id="minutes">')
    assert "Decisions recorded" in decided
    assert "Decisions heard in the recording" not in decided
    assert "supplemental appropriation" not in decided
    # What was discussed, after the minutes'.
    assert decided.index("Decisions recorded") < decided.index('<h3 id="discussed">What was discussed</h3>')
    assert "Residents asked the council to keep the Flock camera pause in place." in decided
    links = section(page, '<p class="doc-links">', "</p>")
    assert 'href="https://videoplayer.telvue.com/player/x/media/2"' in links and "Recording" in links


def test_the_decisions_page_lists_a_recordings_decisions_until_the_minutes(built):
    out, before, after = built
    page = (out / "meetings" / "decisions" / "index.html").read_text()
    entry = page[page.index(f'<a href="{before["url"]}#minutes">'):]
    entry = entry[:entry.index("</li>\n  \n  <li") if "</li>\n  \n  <li" in entry else len(entry)]
    assert 'href="https://videoplayer.telvue.com/player/x/media/1"' in entry and "From the recording" in entry
    assert "Checked against the recording&#39;s transcript" in entry or "Checked against the recording's transcript" in entry
    assert "supplemental appropriation for the Fire Department." in entry and '<span class="tally">10–0</span>' in entry
    rows = (out / "meetings" / "data" / "decisions.csv").read_text().splitlines()
    assert rows[0] == "meeting_date,board,kind,decision,meeting_url,minutes_url,source,recording_url"
    assert any("Fire Department" in r and r.endswith(",recording,https://videoplayer.telvue.com/player/x/media/1") for r in rows)
    assert all("Fire Department" not in r for r in rows if after["url"] in r)


def test_the_feed_says_a_summary_is_from_the_recording(built):
    out, _, _ = built
    feed = (out / "feed.xml").read_text()
    assert "AI summary of the meeting" in feed and "The minutes will follow." in feed


def test_the_digest_lists_meetings_summarized_from_their_recordings():
    from datetime import date
    from zoneinfo import ZoneInfo

    from pipeline import digest
    tz = ZoneInfo("America/New_York")
    m = {"date": "2026-09-28", "minutes": [], "recording_summary": {"headline": "x"},
         "transcript_doc": {"fetched_at": "2026-09-30T06:50:00-04:00"}}
    later = {**m, "minutes": [{"fetched_at": "2026-10-01T08:00:00-04:00"}], "minutes_summary": {}}
    issues = digest.issues([m, later], "2026-09-01T00:00:00-04:00", date(2026, 10, 6), tz)
    issue = next(i for i in issues if i["sunday"] == "2026-10-04")
    # The second's minutes came the same week: it's listed with them, not here.
    assert issue["recorded"] == [m]
    assert json.dumps(issue["minutes"], default=str).count("2026-10-01") == 1


def test_a_recording_with_no_length_or_transcript_is_still_linked(config, data_dir, tmp_path):
    """A recording found but not read yet (an Internet Archive upload still being processed) has no length or
    transcript: its meeting's page links it, without a length."""
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    model = config["summaries"]["model"]
    meetings = build_site.load_meetings(data, BUILT_AT.date(), model)["all"]
    m = next(m for m in meetings if m["date"] < BUILT_AT.date().isoformat() and m["status"] == "scheduled")
    entry = recording(m, 3)
    del entry["seconds"], entry["transcript"]
    recordings.save(data, {"telvue-3": entry})
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    page = (out / "meetings" / m["slug"] / "index.html").read_text()
    links = section(page, '<p class="doc-links">', "</p>")
    assert 'href="https://videoplayer.telvue.com/player/x/media/3"' in links and "Recording" in links
    assert 'class="runtime"' not in links and "Machine transcript" not in links
