"""A meeting summarized from its recording's transcript until its minutes come (pipeline/summarize.py's
"recording" kind)."""

import copy
import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FakeAnthropic

from pipeline import factcheck, listings, recordings, summarize
from pipeline.config import load_config

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=TZ)
TRANSCRIPT = """[0:00:05] Good evening. The council will come to order. Clerk, call the roll.
[0:42:10] Motion to approve the supplemental appropriation of $1.5 million for the fire department. Clerk, call the roll.
[0:43:02] Councilor Noone. Yes. Councilor Gichia. Yes. The motion passes 10 to 0.
[1:10:00] Public comment. I'm here to ask you to keep the Flock camera pause in place.
"""


@pytest.fixture
def config():
    config = copy.deepcopy(load_config("gloucester"))
    config["summaries"] = {**config["summaries"], "since": "2026-10-01"}
    config.setdefault("officials", {})["bodies"] = [{"name": "City Council", "members": [
        {"name": "Corey Nuon"}, {"name": "Erik Gitschier"}]}]
    return config


@pytest.fixture
def data_dir(tmp_path):
    meeting = {"id": "ac-1", "slug": "ac-1", "source": "agendacenter", "date": "2026-10-27", "body": "City Council",
               "title": "City Council", "status": "scheduled", "listed": True, "history": [], "agendas": [], "minutes": []}
    (tmp_path / "meetings" / "transcripts").mkdir(parents=True)
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps({"ac-1": meeting}))
    (tmp_path / "meetings" / "transcripts" / "telvue-1.txt").write_text(TRANSCRIPT)
    import hashlib
    recordings.save(tmp_path, {"telvue-1": {
        "id": "telvue-1", "platform": "telvue", "board": "City Council", "date": "2026-10-27", "meeting": "ac-1",
        "title": "City Council - October 27, 2026", "url": "https://videoplayer.telvue.com/player/x/media/1",
        "found_at": "2026-10-28T06:00:00-04:00", "source": 0,
        "transcript": {"file": "telvue-1.txt", "sha256": hashlib.sha256(TRANSCRIPT.encode()).hexdigest(),
                       "bytes": len(TRANSCRIPT), "seconds": 4400, "transcribed_at": "2026-10-28T06:50:00-04:00"}}})
    return tmp_path


def test_a_recording_is_summarized_from_its_transcript(config, data_dir):
    client = FakeAnthropic()
    summary = summarize.run(config, client, data_dir, limit=10, now=NOW)
    assert summary["summarized"] == 1, summary
    request = client.calls[0]
    document, prompt = request["messages"][0]["content"]
    # The transcript goes as text, with the board's members to spell their names.
    assert document["source"] == {"type": "text", "media_type": "text/plain", "data": TRANSCRIPT}
    assert "machine transcript of the recording of: City Council, 2026-10-27" in prompt["text"]
    assert "The board's members: Corey Nuon, Erik Gitschier." in prompt["text"]

    meetings, _ = listings.read(data_dir)
    doc = meetings["ac-1"]["transcripts"][0]
    record = summarize.cached(data_dir, doc["sha256"], "recording")
    assert record["kind"] == "recording" and record["transcript_source"] == "recording"
    assert record["decision_evidence"][0]["outcome"] == "approved"
    # Checked against the transcript, without its times: the quote, the amount and the count are all there.
    assert record["fact_check"]["source"] == "recording"
    assert record["fact_check"]["result"] == "ok", record["fact_check"]["problems"]
    assert "votes" not in record
    assert factcheck.how_checked(record) == "recording"

    # Done once.
    assert summarize.pending_documents(data_dir, "2026-10-28", config["summaries"]["model"]) == []


def test_what_isnt_in_the_transcript_fails_its_check(config, data_dir):
    wrong = {**FakeAnthropic.RECORDING, "headline": "Approved $2.5 million for the Fire Department, 10-0."}
    client = FakeAnthropic()
    FakeAnthropic.RECORDING, saved = wrong, FakeAnthropic.RECORDING
    try:
        summarize.run(config, client, data_dir, limit=10, now=NOW)
    finally:
        FakeAnthropic.RECORDING = saved
    meetings, _ = listings.read(data_dir)
    record = summarize.cached(data_dir, meetings["ac-1"]["transcripts"][0]["sha256"], "recording")
    assert record["fact_check"]["result"] == "failed"
    assert factcheck.shown(record, record, "recording")["headline"] == ""


def test_the_minutes_replace_the_recording(config, data_dir):
    store = json.loads((data_dir / "meetings" / "meetings.json").read_text())
    store["ac-1"]["minutes"] = [{"id": "m-1", "sha256": "0" * 64, "file": "m-1.pdf", "source_url": "x",
                                 "fetched_at": "2026-10-28T08:00:00-04:00"}]
    (data_dir / "meetings" / "meetings.json").write_text(json.dumps(store))
    pending = summarize.pending_documents(data_dir, "2026-10-28", config["summaries"]["model"])
    assert [kind for kind, _, _ in pending] == ["minutes"]


def test_a_meeting_in_parts_is_summarized_from_every_part(config, data_dir):
    store = recordings.load(data_dir)
    first = store["telvue-1"]
    store["telvue-1"] = {**first, "part": 1}
    store["telvue-2"] = {**first, "id": "telvue-2", "part": 2, "title": "Part 2",
                         "transcript": {**first["transcript"], "file": "telvue-2.txt", "sha256": "f" * 64}}
    (data_dir / "meetings" / "transcripts" / "telvue-2.txt").write_text("[0:00:01] The second part.\n")
    recordings.save(data_dir, store)
    client = FakeAnthropic()
    summarize.run(config, client, data_dir, limit=10, now=NOW)
    sent = client.calls[0]["messages"][0]["content"][0]["source"]["data"]
    assert sent.startswith("Part 1: City Council - October 27, 2026\n[0:00:05]")
    assert "Part 2: Part 2\n[0:00:01] The second part." in sent

