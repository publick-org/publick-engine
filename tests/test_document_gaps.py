"""Where a town's recent meetings are missing their agendas and minutes (pipeline/document_gaps.py), and
the network's report of them (network.documents_text())."""

import json
from datetime import date, datetime, timezone

import pytest

from pipeline import document_gaps, network

TODAY = date(2026, 11, 2)
AGENDA = [{"id": "a", "sha256": "a" * 64, "file": "a.pdf"}]
MINUTES = [{"id": "m", "sha256": "m" * 64, "file": "m.pdf"}]


def meeting(id, day, body, source="agendacenter", **extra):
    return {"id": id, "slug": id, "source": source, "date": day, "body": body, "title": body, "status": "scheduled",
            "listed": True, "history": [], "agendas": [], "minutes": [], "first_seen": "2026-08-01T07:00:00-04:00",
            **extra}


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "meetings").mkdir()
    meetings = [
        # A board with agendas and minutes: no gap.
        meeting("pb-1", "2026-08-20", "Planning Board", agendas=AGENDA, minutes=MINUTES),
        meeting("pb-2", "2026-09-17", "Planning Board", agendas=AGENDA),
        # A board with neither, three times.
        meeting("water-1", "2026-08-27", "Board of Water Commissioners", source="calendar", start_time="15:30"),
        meeting("water-2", "2026-09-24", "Board of Water Commissioners", source="calendar", start_time="15:30"),
        meeting("water-3", "2026-10-29", "Board of Water Commissioners", source="calendar", start_time="15:30"),
        # Agendas, but no minutes for meetings 45 days old or more.
        meeting("lic-1", "2026-08-12", "Licensing Board", agendas=AGENDA),
        meeting("lic-2", "2026-09-09", "Licensing Board", agendas=AGENDA),
        meeting("lic-3", "2026-10-14", "Licensing Board", agendas=AGENDA),
        # Its minutes may still come: one meeting 45 days old, not two.
        meeting("cons-1", "2026-09-10", "Conservation Commission", agendas=AGENDA),
        meeting("cons-2", "2026-10-08", "Conservation Commission", agendas=AGENDA),
        # The calendar's name and the Agenda Center's for one meeting.
        meeting("3943", "2026-10-16", "Beverly Retirement Board", source="calendar", start_time="09:00"),
        meeting("agendacenter-2955", "2026-10-16", "Contributory Retirement Board", agendas=AGENDA),
        # Two boards that share only a kind of body, the same day: not a pair.
        meeting("6309", "2026-10-13", "Malden Housing Authority", source="calendar", start_time="08:30"),
        meeting("agendacenter-4470", "2026-10-13", "City Council Committee of the Whole", agendas=AGENDA),
        # Cancelled, today's, and older than the window: not counted.
        meeting("x-1", "2026-10-20", "Cemetery Commission", status="cancelled"),
        meeting("x-2", "2026-11-02", "Cemetery Commission"),
        meeting("x-3", "2026-07-01", "Cemetery Commission"),
    ]
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps({m["id"]: m for m in meetings}))
    (tmp_path / "meetings" / "opengov_status.json").write_text(json.dumps({"meetings_waiting": 290, "errors": []}))
    (tmp_path / "meetings" / "page_status.json").write_text(json.dumps({"documents_waiting": 0}))
    # A recording waiting to be transcribed isn't a document waiting.
    (tmp_path / "meetings" / "recordings_status.json").write_text(json.dumps({"waiting": ["telvue-1"]}))
    return tmp_path


def test_each_kind_of_gap_is_found(data_dir):
    gaps = document_gaps.gaps(data_dir, "Beverly", TODAY)
    assert (gaps["since"], gaps["until"]) == ("2026-08-04", "2026-11-01")
    # The retirement board's one meeting is too few to call a gap: it's the pair below.
    assert gaps["no_documents"] == [{"board": "Board of Water Commissioners", "meetings": 3}]
    assert gaps["no_minutes"] == [{"board": "Licensing Board", "meetings": 2}]
    assert gaps["pairs"] == [{"date": "2026-10-16", "board": "Beverly Retirement Board",
                              "other": "Contributory Retirement Board"}]
    assert gaps["waiting"] == {"opengov_status": {"count": 290, "unit": "meetings"}} and gaps["backlog"] == {}
    assert gaps["counts"] == {"no_documents": 1, "no_minutes": 1, "pairs": 1}


def test_a_readers_backlog_older_than_the_window_is_counted_apart_by_its_unit(data_dir):
    """Pawtucket's opengov and Manchester's page reader, set up with since = 2026-01-01, reading their first
    months: what they leave is all older than the window, and shrinks every run."""
    meetings = data_dir / "meetings"
    (meetings / "opengov_status.json").write_text(json.dumps({"meetings_waiting": 198, "waiting_newest": "2026-06-30"}))
    (meetings / "page_status.json").write_text(json.dumps({"documents_waiting": 94, "waiting_newest": "2026-10-20"}))
    gaps = document_gaps.gaps(data_dir, "Beverly", TODAY)
    assert gaps["waiting"] == {"page_status": {"count": 94, "unit": "documents"}}
    assert gaps["backlog"] == {"opengov_status": {"count": 198, "unit": "meetings", "newest": "2026-06-30"}}


def test_one_meeting_listed_twice_is_no_gap_once_it_is_listed_once(data_dir):
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text())
    store["3943"]["body"] = "Contributory Retirement Board"
    path.write_text(json.dumps(store))
    assert document_gaps.gaps(data_dir, "Beverly", TODAY)["pairs"] == []


def test_a_listing_moved_to_another_day_is_no_pair(data_dir):
    # Beverly's calendar listed the subcommittee on 10-13, "RESCHEDULED to 10-14", the day the commission met.
    path = data_dir / "meetings" / "meetings.json"
    store = json.loads(path.read_text())
    store["3934"] = meeting("3934", "2026-10-13", "Beverly Regional Airport Commission Subcommittee on Mission",
                            source="calendar", start_time="18:00", status="rescheduled")
    store["agendacenter-2946"] = meeting("agendacenter-2946", "2026-10-13", "Airport Commission", agendas=AGENDA)
    path.write_text(json.dumps(store))
    assert [p["board"] for p in document_gaps.gaps(data_dir, "Beverly", TODAY)["pairs"]] == ["Beverly Retirement Board"]
    store["3934"]["status"] = "scheduled"
    path.write_text(json.dumps(store))
    assert len(document_gaps.gaps(data_dir, "Beverly", TODAY)["pairs"]) == 2


def run_record(root, name, gaps, finished="2026-11-02T12:00:00+00:00"):
    (root / "towns" / name / "config").mkdir(parents=True)
    (root / "towns" / name / "config" / f"{name.split('-')[0]}.toml").write_text("")
    (root / "towns" / name / "data").mkdir()
    (root / "towns" / name / "data" / "run.json").write_text(json.dumps({"finished_at": finished, "documents": gaps}))


def test_the_network_report_adds_up_the_run_records(tmp_path, data_dir):
    gaps = document_gaps.gaps(data_dir, "Beverly", TODAY)
    for i in range(network.COVERAGE_TOWNS + 2):
        run_record(tmp_path / "net", f"town{i:02d}-ma", gaps)
    run_record(tmp_path / "net", "stale-ma", gaps, finished="2026-10-20T12:00:00+00:00")
    clean = {**gaps, "counts": {"no_documents": 0, "no_minutes": 0, "pairs": 0}, "no_documents": [], "no_minutes": [],
             "pairs": [], "waiting": {}}
    run_record(tmp_path / "net", "clean-ma", clean)
    text = network.documents_text(tmp_path / "net", at=datetime(2026, 11, 2, 13, tzinfo=timezone.utc))
    towns = network.COVERAGE_TOWNS + 3
    assert f"in {towns} towns: **{gaps['held'] * towns}**" in text
    assert f"| Boards with no agenda or minutes | {towns - 1} | {towns - 1} |" in text
    assert f"| Meetings likely listed twice | {towns - 1} | {towns - 1} |" in text
    assert f"| Waiting for their documents, in this window | {290 * (towns - 1)} meetings | {towns - 1} |" in text
    assert ("| town00-ma | Board of Water Commissioners (3) | 2026-10-16 Beverly Retirement Board / Contributory "
            "Retirement Board | Licensing Board (2) | 290 meetings (opengov) |") in text
    # The report's length doesn't grow with the network, and a town with nothing to look at isn't listed.
    assert text.count("| town") == network.COVERAGE_TOWNS
    assert "And 2 more towns." in text
    assert "clean-ma" not in text
    assert "Not counted (no run record with a count from the last 48 hours): 1 towns: stale-ma." in text
    assert "Still reading meetings older than the window" not in text


def test_the_report_labels_units_counts_a_backlog_apart_and_reads_older_records(tmp_path, data_dir):
    gaps = document_gaps.gaps(data_dir, "Beverly", TODAY)
    run_record(tmp_path / "net", "pawtucket-ri", {**gaps, "waiting": {},
               "backlog": {"opengov_status": {"count": 198, "unit": "meetings", "newest": "2026-06-30"}}})
    run_record(tmp_path / "net", "manchester-nh", {**gaps, "waiting": {"page_status": {"count": 94, "unit": "documents"}}})
    # A run record from before units were kept: its count read as meetings.
    run_record(tmp_path / "net", "lowell-ma", {**gaps, "waiting": {"opengov_status": 12}})
    text = network.documents_text(tmp_path / "net", at=datetime(2026, 11, 2, 13, tzinfo=timezone.utc))
    assert "| Waiting for their documents, in this window | 94 documents, 12 meetings | 2 |" in text
    assert "| 94 documents (page) |" in text and "| 12 meetings (opengov) |" in text
    assert "Still reading meetings older than the window, as a new town's first runs do: pawtucket-ri, 198 meetings (opengov)." in text
