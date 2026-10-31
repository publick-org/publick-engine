"""Meetings' recordings found, matched to their meetings, and transcribed (pipeline/fetch_recordings.py)."""

import copy
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from conftest import TZ

from pipeline import fetch_recordings, listings, recordings, transcribe
from pipeline.config import load_config

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=TZ)


def meeting(id_, body, date, **extra):
    return {"id": id_, "slug": id_, "source": "agendacenter", "date": date, "body": body, "title": body,
            "status": "scheduled", "listed": True, "history": [], "agendas": [], "minutes": [], **extra}


@pytest.fixture
def data_dir(tmp_path):
    store = {m["id"]: m for m in [
        meeting("ac-1", "City Council", "2026-10-27"),
        meeting("ac-2", "City Council", "2026-10-20", minutes=[{"id": "m", "sha256": "abc", "file": "m.pdf"}]),
        meeting("ac-3", "School Committee", "2026-10-26"),
        meeting("ac-4", "Planning Board", "2026-10-21"),
        meeting("ac-5", "Planning Board", "2026-10-21", title="Planning Board Special Meeting", special=True),
        meeting("ac-6", "City Council", "2026-09-01"),
        meeting("ac-7", "Licensing Board", "2026-10-22", status="cancelled"),
    ]}
    (tmp_path / "meetings").mkdir()
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps(store))
    return tmp_path


class FakePlatform:
    """A platform listing the recordings given, each with its audio at a made-up address."""

    def __init__(self, found):
        self.found = found
        self.asked = []

    def find(self, client, source, since, timezone):
        self.asked.append(since)
        return copy.deepcopy(self.found)

    def media(self, client, source, recording):
        return {"url": f"https://video.example/{recording['id']}.m3u8"}


def found(id_, board, date, seconds=3600):
    return {"id": f"fake-{id_}", "platform": "fake", "board": board, "date": date, "title": f"{board} {date}",
            "url": f"https://video.example/{id_}", "seconds": seconds}


def fake_transcriber(calls):
    def run(model, media, folder, name, user_agent):
        calls.append(name)
        (folder / f"{name}.txt").write_text(f"[0:00:05] Transcript of {name}.\n")
        (folder / f"{name}.ogg").write_bytes(b"OggS" + name.encode())
        return {"seconds": 3600, "download_seconds": 30, "transcribe_seconds": 700}
    return run


@pytest.fixture
def config(monkeypatch):
    config = copy.deepcopy(load_config("gloucester"))
    config["recordings"] = {"sources": [{"platform": "fake", "boards": {"City Council": "1"}}]}
    return config


def use(monkeypatch, platform):
    monkeypatch.setattr(recordings, "platform", lambda name: platform)


def test_title_dates():
    assert recordings.title_date("City Council Meeting 10/27/2026") == "2026-10-27"
    assert recordings.title_date("City Council 10-27-26") == "2026-10-27"
    assert recordings.title_date("2026-10-27 City Council") == "2026-10-27"
    assert recordings.title_date("City Council - October 27, 2026") == "2026-10-27"
    assert recordings.title_date("School Committee Sept. 8th 2026") == "2026-09-08"
    assert recordings.title_date("Planning Board 21 October 2026") == "2026-10-21"
    assert recordings.title_date("Ward 3 Meeting") is None
    # A date that isn't one is passed over for the next.
    assert recordings.title_date("Item 13/40/2026 then 10/27/2026") == "2026-10-27"


def test_board_for_title_takes_the_longest_match():
    boards = {"School Committee": r"^School Committee\b", "School Committee Finance Subcommittee": r"School Committee Finance"}
    assert recordings.board_for_title("School Committee 10/27/2026", boards) == "School Committee"
    assert recordings.board_for_title("School Committee Finance Subcommittee 10/27", boards) == "School Committee Finance Subcommittee"
    assert recordings.board_for_title("Planning Board", boards) is None


def test_match_is_the_boards_one_meeting_that_day(data_dir):
    meetings, _ = listings.read(data_dir)
    assert recordings.match({"board": "City Council", "date": "2026-10-27"}, meetings) == ("ac-1", "matched")
    assert recordings.match({"board": "city  council", "date": "2026-10-27"}, meetings)[0] == "ac-1"
    assert recordings.match({"board": "City Council", "date": "2026-10-28"}, meetings) == (None, "no meeting listed")
    assert recordings.match({"board": "Planning Board", "date": "2026-10-21"}, meetings) == (None, "2 meetings that day")
    # A cancelled meeting has no recording.
    assert recordings.match({"board": "Licensing Board", "date": "2026-10-22"}, meetings)[0] is None


def test_a_run_transcribes_new_meetings_newest_first(config, data_dir, monkeypatch):
    platform = FakePlatform([
        found("1", "City Council", "2026-10-27"),
        found("2", "City Council", "2026-10-20"),      # its minutes came first
        found("3", "School Committee", "2026-10-26"),
        found("4", "Planning Board", "2026-10-21"),    # two meetings that day
        found("5", "City Council", "2026-09-01"),      # older than max_age_days
        found("6", "City Council", "2026-10-30"),      # not yet
        found("7", "City Council", "2026-10-27"),      # a second recording of a meeting
    ])
    use(monkeypatch, platform)
    calls = []
    status = fetch_recordings.run(config, None, data_dir, now=NOW, load_model=lambda: "model",
                                  transcriber=fake_transcriber(calls))
    assert platform.asked == ["2026-10-15"]
    assert calls == ["fake-1", "fake-3"]
    assert status["transcribed"] == ["fake-1", "fake-3"]
    assert status["recordings_listed"] == 5
    assert status["unmatched"] == ["Planning Board 2026-10-21 (fake-4): 2 meetings that day"]
    store = recordings.load(data_dir)
    assert set(store) == {"fake-1", "fake-2", "fake-3", "fake-4", "fake-7"}
    t = store["fake-1"]["transcript"]
    assert t["file"] == "fake-1.txt" and t["words"] == 3 and t["model"] == transcribe.MODEL
    assert (data_dir / "meetings" / "transcripts" / "fake-1.txt").read_text().startswith("[0:00:05]")
    assert (data_dir / "meetings" / "recordings" / "fake-1.ogg").exists()
    assert "transcript" not in store["fake-7"]

    # Each meeting then has its recordings and its transcript, as its documents are listed.
    meetings, _ = listings.read(data_dir)
    council = meetings["ac-1"]
    assert [r["id"] for r in council["recordings"]] == ["fake-1", "fake-7"]
    assert council["transcripts"][0]["sha256"] == t["sha256"]
    assert "transcripts" not in meetings["ac-2"]

    # A second run finds nothing new to do.
    calls.clear()
    status = fetch_recordings.run(config, None, data_dir, now=NOW, load_model=lambda: "model",
                                  transcriber=fake_transcriber(calls))
    assert calls == [] and status["recordings_new"] == 0


def test_what_doesnt_fit_the_runs_hours_waits(config, data_dir, monkeypatch):
    use(monkeypatch, FakePlatform([found("1", "City Council", "2026-10-27", seconds=4 * 3600),
                                   found("3", "School Committee", "2026-10-26", seconds=3600)]))
    calls = []
    status = fetch_recordings.run(config, None, data_dir, now=NOW, hours=0.5, load_model=lambda: "model",
                                  transcriber=fake_transcriber(calls), clock=lambda: 0.0)
    assert calls == ["fake-3"]
    assert status["waiting"] == ["fake-1"]


def test_a_failed_recording_is_tried_again_then_left(config, data_dir, monkeypatch):
    use(monkeypatch, FakePlatform([found("1", "City Council", "2026-10-27")]))

    def failing(model, media, folder, name, user_agent):
        raise transcribe.TranscribeError("ffmpeg couldn't read the audio")

    for _ in range(3):
        status = fetch_recordings.run(config, None, data_dir, now=NOW, load_model=lambda: "model", transcriber=failing)
    recording = recordings.load(data_dir)["fake-1"]
    assert recording["tries"] == fetch_recordings.MAX_TRIES
    assert status["errors"] == []


def test_find_only_transcribes_nothing(config, data_dir, monkeypatch):
    use(monkeypatch, FakePlatform([found("1", "City Council", "2026-10-27")]))
    status = fetch_recordings.run(config, None, data_dir, now=NOW, find_only=True,
                                  load_model=lambda: pytest.fail("no model is loaded"))
    assert status["transcribed"] == [] and recordings.load(data_dir)["fake-1"]["meeting"] == "ac-1"


def test_config_checks(config):
    config["recordings"] = {"sources": [{"platform": "vimeo", "boards": {"City Council": "1"}}]}
    with pytest.raises(SystemExit, match="platform must be one of"):
        recordings.check(config)
    config["recordings"] = {"sources": [{"platform": "telvue", "boards": {}}]}
    with pytest.raises(SystemExit, match="boards must map"):
        recordings.check(config)


def test_transcript_paragraphs():
    segments = [(0.0, 4.0, "Good evening."), (4.2, 8.0, "Call the roll."), (12.0, 15.0, "Councilor Nuon."),
                (15.1, 30.0, "Present."), (80.5, 82.0, "Thank you.")]
    assert transcribe.text(segments) == ("[0:00:00] Good evening. Call the roll.\n"
                                         "[0:00:12] Councilor Nuon. Present.\n"
                                         "[0:01:20] Thank you.\n")
    assert transcribe.stamp(3723.9) == "1:02:03"


def test_transcribe_reads_a_chunk_at_a_time():
    seen = []

    class Model:
        def transcribe(self, audio, **options):
            assert options["vad_filter"] and not options["condition_on_previous_text"]
            return [SimpleNamespace(start=1.0, end=2.0, text=f" part {len(seen)} ")], None

    def read(path, start, seconds):
        seen.append(start)
        return [0.0] * 10

    out = transcribe.transcribe(Model(), "audio.ogg", length=4000, chunk=1800, read=read)
    assert seen == [0.0, 1800.0, 3600.0]
    assert out == [(1.0, 2.0, "part 1"), (1801.0, 1802.0, "part 2"), (3601.0, 3602.0, "part 3")]


def test_audio_command_sends_the_platforms_headers(tmp_path):
    cmd = transcribe.audio_command({"url": "https://x/a.m3u8", "headers": {"Referer": "https://x/"}},
                                   tmp_path / "a.ogg", "town.publick.org")
    assert cmd[cmd.index("-headers") + 1] == "Referer: https://x/\r\n"
    assert cmd[cmd.index("-i") + 1] == "https://x/a.m3u8"
    assert "libopus" in cmd


def test_a_meeting_in_parts_is_transcribed_from_every_part(config, data_dir, monkeypatch):
    parts = [{**found(f"p{n}", "City Council", "2026-10-27"), "part": n, "title": f"City Council 10-27-26 Part {n}"}
             for n in (2, 1)]
    platform = FakePlatform(parts)
    use(monkeypatch, platform)
    calls = []
    fetch_recordings.run(config, None, data_dir, now=NOW, load_model=lambda: "model", transcriber=fake_transcriber(calls))
    assert calls == ["fake-p1", "fake-p2"]
    meetings, _ = listings.read(data_dir)
    doc = meetings["ac-1"]["transcripts"][0]
    assert [p["file"] for p in doc["parts"]] == ["fake-p1.txt", "fake-p2.txt"]
    first = doc["sha256"]

    # A third part found later makes the transcript a new document, once it's transcribed too.
    platform.found.append({**found("p3", "City Council", "2026-10-27"), "part": 3})
    fetch_recordings.run(config, None, data_dir, now=NOW, find_only=True)
    meetings, _ = listings.read(data_dir)
    assert "transcripts" not in meetings["ac-1"]
    fetch_recordings.run(config, None, data_dir, now=NOW, load_model=lambda: "model", transcriber=fake_transcriber(calls))
    meetings, _ = listings.read(data_dir)
    doc = meetings["ac-1"]["transcripts"][0]
    assert len(doc["parts"]) == 3 and doc["sha256"] != first


def test_title_parts():
    assert recordings.title_part("City Council 4-13-26 Part 1") == 1
    assert recordings.title_part("Economic & Community Development Committee 10-19-26 Part 3") == 3
    assert recordings.title_part("School Committee part two") == 2
    assert recordings.title_part("Department Partners Meeting") is None


def test_hls_stream_takes_audio_only_else_the_smallest():
    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2250000,CODECS="avc1.4d401f,mp4a.40.2"
index-savc_2250k-v1-a1.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=300000,CODECS="avc1.4d401f,mp4a.40.2"
index-savc_300k-v1-a1.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=128000,CODECS="mp4a.40.2"
index-saac_128k_0-a1.m3u8
"""
    base = "https://cdn.example/media/1/master.m3u8"
    assert recordings.hls_stream(master, base) == "https://cdn.example/media/1/index-saac_128k_0-a1.m3u8"
    video_only = master.rsplit("#EXT-X-STREAM-INF:BANDWIDTH=128000", 1)[0]
    assert recordings.hls_stream(video_only, base) == "https://cdn.example/media/1/index-savc_300k-v1-a1.m3u8"
    assert recordings.hls_stream("#EXTM3U\n#EXTINF:6,\nseg-1.ts\n", base) == base


def test_a_board_pattern_must_be_a_regular_expression(config):
    config["recordings"] = {"sources": [{"platform": "internet_archive", "collection": "x", "boards": {"City Council": "("}}]}
    with pytest.raises(SystemExit, match="isn't a regular expression"):
        recordings.check(config)


class FakeTranscribingPlatform(FakePlatform):
    """A platform that makes its own transcripts (as YouTube's, by Gemini), or none yet."""

    def __init__(self, found, ready=True):
        super().__init__(found)
        self.ready = ready

    def media(self, client, source, recording):
        return None

    def transcript(self, client, source, recording, storage=None):
        return ("[0:00:05] Made elsewhere.\n", {"model": "gemini-x", "usage": {"requests": 1}}) if self.ready else None


def test_a_platform_that_transcribes_its_own_needs_no_whisper(config, data_dir, monkeypatch):
    use(monkeypatch, FakeTranscribingPlatform([{**found("1", "City Council", "2026-10-27"), "platform": "youtube"}]))
    status = fetch_recordings.run(config, None, data_dir, now=NOW, load_model=lambda: pytest.fail("no Whisper"),
                                  transcriber=lambda *a: pytest.fail("no ffmpeg"))
    assert status["transcribed"] == ["fake-1"]
    t = recordings.load(data_dir)["fake-1"]["transcript"]
    assert t["model"] == "gemini-x" and t["words"] == 2
    assert (data_dir / "meetings" / "transcripts" / "fake-1.txt").read_text() == "[0:00:05] Made elsewhere.\n"


def test_without_its_key_a_youtube_video_waits(config, data_dir, monkeypatch):
    use(monkeypatch, FakeTranscribingPlatform([{**found("1", "City Council", "2026-10-27"), "platform": "youtube"}],
                                              ready=False))
    for _ in range(3):
        status = fetch_recordings.run(config, None, data_dir, now=NOW, load_model=lambda: pytest.fail("no Whisper"))
    assert status["not_ready"] == ["fake-1"]
    assert "tries" not in recordings.load(data_dir)["fake-1"]


def test_youtube_videos_linked_from_the_meetings_are_matched_through_their_listing(config, data_dir, monkeypatch):
    from pipeline import youtube
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    store = json.loads((data_dir / "meetings" / "meetings.json").read_text())
    store["ac-1"]["video_id"] = "council1027"
    # Two Planning Board meetings that day: a title couldn't tell them apart, the link does.
    store["ac-5"].update(video_id="pbspecial", video_start=95)
    store["ac-3"]["video_id"] = "school1026"
    (data_dir / "meetings" / "meetings.json").write_text(json.dumps(store))
    config["recordings"] = {"sources": [{"platform": "youtube", "boards": ["City Council", "Planning Board"]}]}
    recordings.check(config)
    monkeypatch.setattr(recordings, "platform", lambda name: youtube)
    status = fetch_recordings.run(config, None, data_dir, now=NOW, find_only=True)
    kept = recordings.load(data_dir)
    assert status["unmatched"] == [] and status["errors"] == []
    assert {k: (v["meeting"], v["board"]) for k, v in kept.items()} == {
        "youtube-council1027": ("ac-1", "City Council"), "youtube-pbspecial-95": ("ac-5", "Planning Board")}
    assert kept["youtube-pbspecial-95"]["url"] == "https://www.youtube.com/watch?v=pbspecial&t=95s"
