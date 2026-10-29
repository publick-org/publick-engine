"""A meeting's page with the summary of its recording, until the minutes' replaces it."""

import copy
import hashlib
import shutil

from conftest import BUILT_AT
from fakes import FakeAnthropic
from test_recording_summaries import TRANSCRIPT

from pipeline import build_site, recordings, summarize


def test_a_meeting_page_shows_the_recordings_summary_until_the_minutes(config, data_dir, tmp_path, monkeypatch):
    """Built for a meeting of the fixture town with no minutes: the summary from its recording, in English and Spanish."""
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    session_config = copy.deepcopy(config)
    session_config["site"]["languages"] = ["en", "es"]
    model = session_config["summaries"]["model"]
    meetings = build_site.load_meetings(data, BUILT_AT.date(), model)["all"]
    m = next(m for m in meetings if m["date"] < BUILT_AT.date().isoformat() and not m["minutes"]
             and m["status"] == "scheduled")
    (data / "meetings" / "transcripts").mkdir(parents=True, exist_ok=True)
    (data / "meetings" / "transcripts" / "telvue-1.txt").write_text(TRANSCRIPT)
    recordings.save(data, {"telvue-1": {
        "id": "telvue-1", "platform": "telvue", "board": m["body"], "date": m["date"], "meeting": m["id"],
        "title": f"{m['body']} - recording", "url": "https://videoplayer.telvue.com/player/x/media/1", "seconds": 4400,
        "found_at": "2026-09-30T06:00:00-04:00", "source": 0,
        "transcript": {"file": "telvue-1.txt", "sha256": hashlib.sha256(TRANSCRIPT.encode()).hexdigest(),
                       "bytes": len(TRANSCRIPT), "seconds": 4400, "transcribed_at": "2026-09-30T06:50:00-04:00"}}})
    session_config["summaries"].pop("since", None)
    summarize.run(session_config, FakeAnthropic(), data, limit=50, now=BUILT_AT)
    monkeypatch.setattr(build_site, "load_config", lambda slug: session_config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)

    page = (out / "meetings" / m["slug"] / "index.html").read_text()
    assert "What happened, from the recording" in page
    assert "Approved $1.5 million for the Fire Department, 10-0." in page
    assert "Residents asked the council to keep the Flock camera pause in place." in page
    assert "Checked against the transcript." in page
    assert "Machine transcript</a>, as made, not corrected" in page
    assert "(1 h 13 min)" in page
    assert (out / "meetings" / "transcripts" / "telvue-1.txt").read_text() == TRANSCRIPT
    es = (out / "es" / "meetings" / m["slug"] / "index.html").read_text()
    assert "Qué pasó, según la grabación" in es
    feed = (out / "feed.xml").read_text()
    assert "(AI summary of the meeting&#39;s recording. The minutes will follow.)" in feed or \
        "(AI summary of the meeting's recording. The minutes will follow.)" in feed
