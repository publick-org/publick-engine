"""A meeting summarized from its recording's transcript until its minutes come (pipeline/summarize.py's
"recording" kind)."""

import copy
import json
from datetime import datetime

import pytest
from conftest import TZ
from fakes import FakeAnthropic

from pipeline import factcheck, listings, pdftext, recordings, summarize
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



def test_a_recordings_streets_are_held_to_the_towns(config, tmp_path):
    # The town's address ranges (pipeline/fetch_addresses.py) give its streets as the Census writes them.
    (tmp_path / "addresses.json").write_text(json.dumps({"year": 2025, "streets": {"Standley St": {"ranges": []},
                                                                                     "Enon St": {"ranges": []}}}))
    assert summarize.town_streets(tmp_path) == {"STANDLEY ST", "ENON ST"}
    assert summarize.town_streets(tmp_path / "elsewhere") == frozenset()
    transcript = b"[0:10:00] I move to set the public hearing at 38 Stanley Street for November 17th. 7 to nothing."
    record = {"kind": "recording", "headline": "", "summary": "",
              "decisions": ["Set a November 17 public hearing on the 38 Stanley Street site plan review."],
              "decision_evidence": [{"outcome": "other", "quote": "I move to set the public hearing at 38 Stanley Street"}]}
    meeting = {"body": "Planning Board"}
    checked = summarize.check_recording(config, meeting, record, transcript, frozenset(), tmp_path)
    assert factcheck.shown(checked, checked, "recording")["decisions"] == [
        "Set a November 17 public hearing on the 38 Standley Street site plan review."]
    # A town without address ranges is checked as before.
    checked = summarize.check_recording(config, meeting, record, transcript, frozenset())
    assert not [p for p in checked["fact_check"]["problems"] if p["kind"] == "spelling"]


def test_a_recordings_amounts_and_addresses_are_held_to_its_meetings_agenda(config, tmp_path):
    # Lowell's Planning Board, 2026-11-02: the transcript has "41 Third Street"; the agenda, 41 Hurd Street.
    transcript = b"[0:20:00] Site plan for 41 Third Street. All in favor? Motion passes."
    record = {"kind": "recording", "headline": "", "summary": "", "discussion": [],
              "decisions": ["Approved the site plan for 41 Third Street."],
              "decision_evidence": [{"outcome": "approved", "quote": "Site plan for 41 Third Street. All in favor? Motion passes."}]}
    agenda, scan = "a" * 64, "b" * 64
    meeting = {"body": "Planning Board", "agendas": [{"sha256": agenda}, {"sha256": scan}, {"sha256": "c" * 64}]}
    folder = summarize.summaries_dir(tmp_path)
    folder.mkdir(parents=True)
    (folder / f"{agenda}.json").write_text(json.dumps({"kind": "agenda", "plain_text": "Site Plan Review – 41 Hurd Street"}))
    # A scan's transcription by the model isn't held against: only the agenda's own text.
    (folder / f"{scan}.json").write_text(json.dumps({"kind": "agenda", "transcript": "41 Third Street", "transcript_source": "ai"}))
    assert summarize.meeting_agendas(tmp_path, meeting) == [agenda, scan]
    checked = summarize.check_recording(config, meeting, record, transcript, frozenset(), tmp_path)
    fc = checked["fact_check"]
    assert [(p["kind"], p["what"], p["agenda"]) for p in fc["problems"]] == [("agenda", "41 Third Street", "41 Hurd Street")]
    assert fc["result"] == "failed" and fc["agendas"] == [agenda, scan]
    assert factcheck.current(checked, [agenda, scan])
    # Checked again when the meeting has another agenda.
    assert not factcheck.current(checked, [agenda, scan, "c" * 64])
    # An agenda's record that can't be read: checked without it, the summary still saved.
    (folder / f"{agenda}.json").write_text("{")
    assert summarize.meeting_agenda_text(tmp_path, [agenda, scan]) == ""
    # A meeting without an agenda: as before.
    alone = summarize.check_recording(config, {"body": "Planning Board"}, record, transcript, frozenset(), tmp_path)
    assert alone["fact_check"]["result"] == "ok" and alone["fact_check"]["agendas"] == []


def test_a_recording_summarized_in_a_batch_is_checked_against_its_agenda(config, data_dir):
    store = json.loads((data_dir / "meetings" / "meetings.json").read_text())
    agenda = "a" * 64
    store["ac-1"]["agendas"] = [{"id": "a-1", "sha256": agenda, "file": "a-1.pdf", "source_url": "x",
                                 "fetched_at": "2026-10-20T08:00:00-04:00"}]
    (data_dir / "meetings" / "meetings.json").write_text(json.dumps(store))
    folder = summarize.summaries_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{agenda}.json").write_text(json.dumps({
        "kind": "agenda", "prompt_version": summarize.KINDS["agenda"]["version"], "headline": "", "summary": "", "items": [],
        "start_time": "", "location": "", "plain_text": "Fire Department, $1.5 million", "text_version": pdftext.VERSION,
        "fact_check": {"version": factcheck.VERSION, "source": "pdf", "result": "ok", "problems": []}}))
    client = FakeAnthropic()
    summarize.run(config, client, data_dir, limit=10, now=NOW, batch=True)
    sent = json.loads((data_dir / summarize.BATCH_FILE).read_text())
    assert [d["meeting"].get("agendas") for d in sent["documents"] if d["kind"] == "recording"] == [[{"sha256": agenda}]]
    summarize.run(config, client, data_dir, limit=10, now=NOW, collecting=True)
    meetings, _ = listings.read(data_dir)
    record = summarize.cached(data_dir, meetings["ac-1"]["transcripts"][0]["sha256"], "recording")
    # Checked as it's collected against the meeting's agenda, so the next run doesn't check it again.
    assert record["fact_check"]["agendas"] == [agenda]
    assert factcheck.current(record, summarize.meeting_agendas(data_dir, meetings["ac-1"]))
