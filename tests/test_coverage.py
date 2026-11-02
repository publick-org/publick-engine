"""The main boards' meetings counted by how far each got toward a summary from its recording
(pipeline/coverage.py), and the network's report of them (network.recordings_text())."""

import copy
import json
from datetime import date, datetime, timezone

import pytest

from pipeline import coverage, network, recordings
from pipeline.config import load_config

TODAY = date(2026, 10, 30)


def meeting(id, day, body, **extra):
    return {"id": id, "slug": id, "source": "agendacenter", "date": day, "body": body, "title": body,
            "status": "scheduled", "listed": True, "history": [], "agendas": [], "minutes": [], **extra}


def transcript(sha):
    return {"file": f"{sha}.txt", "sha256": sha, "bytes": 10, "seconds": 3600,
            "transcribed_at": "2026-10-28T06:00:00-04:00"}


@pytest.fixture
def config():
    config = copy.deepcopy(load_config("gloucester"))
    config["meetings"] = {**config.get("meetings", {}), "governing_body": "City Council"}
    config["recordings"] = {"sources": [{"platform": "telvue", "boards": {"City Council": "^City Council",
                                                                          "Planning Board": "^Planning"}}]}
    return config


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "meetings").mkdir()
    (tmp_path / "summaries").mkdir()
    meetings = [
        meeting("council-27", "2026-10-27", "City Council"),                  # summarized the next day
        meeting("council-20", "2026-10-26", "City Council"),                  # summarized three days later
        meeting("school-28", "2026-10-28", "School Committee"),               # no source reads it
        meeting("planning-28", "2026-10-28", "Planning Board"),               # read, nothing found
        meeting("zba-29", "2026-10-29", "Zoning Board of Appeals",            # minutes came first
                minutes=[{"id": "m", "sha256": "m" * 64, "file": "m.pdf"}]),
        meeting("council-29", "2026-10-29", "City Council"),                  # its recording failed
        meeting("ped-29", "2026-10-29", "Planning & Economic Development Committee"),  # not a main board
        meeting("cons-28", "2026-10-28", "Conservation Commission"),          # not a main board
        meeting("council-30", "2026-10-30", "City Council"),                  # today: not yet
        meeting("council-15", "2026-10-15", "City Council"),                  # before the window
    ]
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps({m["id"]: m for m in meetings}))
    recordings.save(tmp_path, {
        "telvue-1": {"id": "telvue-1", "platform": "telvue", "board": "City Council", "date": "2026-10-27",
                     "meeting": "council-27", "found_at": "2026-10-28T05:00:00-04:00", "source": 0,
                     "transcript": transcript("a" * 64)},
        "telvue-2": {"id": "telvue-2", "platform": "telvue", "board": "City Council", "date": "2026-10-26",
                     "meeting": "council-20", "found_at": "2026-10-29T05:00:00-04:00", "source": 0,
                     "transcript": transcript("b" * 64)},
        "telvue-3": {"id": "telvue-3", "platform": "telvue", "board": "City Council", "date": "2026-10-29",
                     "meeting": "council-29", "found_at": "2026-10-30T05:00:00-04:00", "source": 0,
                     "tries": recordings.MAX_TRIES, "error": "HTTP 403"},
    })
    for sha, made in (("a" * 64, "2026-10-28T07:00:00-04:00"), ("b" * 64, "2026-10-29T07:00:00-04:00")):
        (tmp_path / "summaries" / f"{sha}.json").write_text(json.dumps({"kind": "recording", "generated_at": made}))
    return tmp_path


def test_each_main_board_meeting_is_counted_at_the_furthest_step_it_reached(config, data_dir):
    result = coverage.coverage(config, data_dir, today=TODAY)
    assert (result["since"], result["until"]) == ("2026-10-23", "2026-10-29")
    assert result["held"] == 6
    assert result["next_day"] == 1
    assert {k: v for k, v in result["steps"].items() if v} == {
        "summarized": 2, "minutes": 1, "failed": 1, "not_found": 1, "no_source": 1}
    assert result["misses"] == [
        {"date": "2026-10-28", "board": "Planning Board", "step": "not_found"},
        {"date": "2026-10-28", "board": "School Committee", "step": "no_source"},
        {"date": "2026-10-29", "board": "City Council", "step": "failed", "detail": "telvue-3: HTTP 403"},
    ]


def test_a_recording_not_matched_and_one_waiting_are_told_apart(config, data_dir):
    store = recordings.load(data_dir)
    store["telvue-4"] = {"id": "telvue-4", "platform": "telvue", "board": "Planning Board", "date": "2026-10-28",
                         "meeting": None, "source": 0}
    store["telvue-3"].update(tries=1)
    recordings.save(data_dir, store)
    steps = {(m["board"], m["date"]): m["step"] for m in coverage.coverage(config, data_dir, today=TODAY)["misses"]}
    assert steps[("Planning Board", "2026-10-28")] == "unmatched"
    assert steps[("City Council", "2026-10-29")] == "waiting"


def test_a_town_without_recordings(config, data_dir):
    del config["recordings"]
    result = coverage.coverage(config, data_dir, today=TODAY)
    # What its recordings store already holds is still counted; the meetings with nothing in it aren't read.
    assert result["steps"]["no_recordings"] == 2
    assert result["steps"]["summarized"] == 2 and result["steps"]["failed"] == 1


def test_a_source_that_reads_every_listed_board_leaves_none_without_a_source(config, data_dir):
    config["recordings"]["sources"].append({"platform": "youtube", "channel": "UC" + "x" * 22, "boards": "listed"})
    steps = coverage.coverage(config, data_dir, today=TODAY)["steps"]
    assert steps["no_source"] == 0 and steps["not_found"] == 2


@pytest.mark.parametrize("name,main", [
    ("Planning Board", True), ("Planning Commission", True), ("Zoning Board of Appeals", True),
    ("Board of Zoning Appeals", True), ("Planning and Zoning Commission", True), ("Zoning Board of Review", True),
    ("Planning & Economic Development Committee", False), ("Planning Board Subcommittee", False),
    ("Conservation Commission", False),
])
def test_planning_and_zoning_boards_are_main_everywhere(name, main):
    assert coverage.planning_or_zoning(name) is main


def run_record(root, name, cov, finished="2026-10-30T12:00:00+00:00"):
    (root / "towns" / name / "config").mkdir(parents=True)
    (root / "towns" / name / "config" / f"{name.split('-')[0]}.toml").write_text("")
    (root / "towns" / name / "data").mkdir()
    (root / "towns" / name / "data" / "run.json").write_text(json.dumps({"finished_at": finished, "recordings": cov}))


def test_the_network_report_adds_up_the_run_records(tmp_path, config, data_dir):
    cov = coverage.coverage(config, data_dir, today=TODAY)
    for i in range(network.COVERAGE_TOWNS + 2):
        run_record(tmp_path, f"town{i:02d}-ma", cov)
    run_record(tmp_path, "stale-ma", cov, finished="2026-10-20T12:00:00+00:00")
    text = network.recordings_text(tmp_path, at=datetime(2026, 10, 30, 13, tzinfo=timezone.utc))
    towns = network.COVERAGE_TOWNS + 2
    assert f"in {towns} towns: **{6 * towns}**" in text
    assert f"by the end of the next day: **{towns}** (17%)" in text
    assert f"| Summarized from the recording | {2 * towns} | 33% | {towns} |" in text
    assert f"Boards most often read by no source: School Committee ({towns})." in text
    # The report's length doesn't grow with the network: the towns past COVERAGE_TOWNS are counted.
    assert text.count("| town") == network.COVERAGE_TOWNS
    assert "And 2 more towns with meetings missed." in text
    assert "Not counted (no run record with a count from the last 48 hours): 1 towns: stale-ma." in text
