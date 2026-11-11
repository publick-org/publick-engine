"""Corrections of a meeting's summaries ([[meetings.corrections]] with replaces, remove_decision, or add_decision):
shown on its page with the reason and the evidence, carried to every list of its decisions, and taken down, with a
warning the network's daily alert lists, once the summary is made again or no longer has what they correct."""

import json
from pathlib import Path

import pytest

from pipeline import build_site, i18n, network
from pipeline.build_site import Decision

URL = "/meetings/2026-11-04-city-council/"


def meeting(**fields):
    return {"id": "m1", "date": "2026-11-04", "body": "City Council", "body_en": "City Council", "url": URL,
            "status": "scheduled", "listings": [], **fields}


def recording(**fields):
    return {"kind": "recording", "generated_at": "2026-11-05T13:01:48-05:00",
            "headline": "Approved bonds for the Frost and Wetherbee school roofs.",
            "summary": "Members approved a $7,881,647 bond for the Frost roof.",
            "decisions": ["Approved $7,881,647 bond for the Frost Elementary School roof replacement.",
                          "Failed: an amendment to keep the 15% affordable housing requirement.",
                          "Approved $40,316.80 payment of a prior year invoice, 7-0."],
            "decision_titles": ["$7,881,647 bond for Frost Elementary School roof", "15% affordable housing amendment",
                                "Prior year invoice"],
            "discussion": ["Frost roof: Members asked about the $7,881,647 bond."], **fields}


def shown(record: dict) -> dict:
    """A summary as load_meetings() has it by then: its decisions as Decisions, each with its title."""
    return {**record, "decisions": [Decision(d, 3, "quote", "approved", title, True, "schools")
                                    for d, title in zip(record["decisions"], record["decision_titles"])]}


def corrected(corrections, m=None, raw=None, english=None, in_language=None):
    """Apply corrections to a meeting summarized from its recording only; returns the meeting, the English
    summary, and the problems."""
    m = m or meeting()
    raw = raw or recording()
    english = english or {"preview": None, "minutes_summary": None, "recording_summary": raw}
    m.update(preview=None, minutes_summary=None, recording_summary=shown(in_language or raw))
    with i18n.use("en"):
        problems = build_site.correct_summaries(m, corrections, {"recording": raw}, english)
    return m, english, problems


def correction(**fields):
    return {"board": "City Council", "date": "2026-11-04", "checked": "2026-11-09", "note": "The agenda says otherwise.",
            "evidence": "https://example.org/agenda.pdf", **fields}


def test_words_replaced_everywhere_in_the_summary_are_marked_and_said():
    m, english, problems = corrected([correction(replaces="$7,881,647", **{"with": "$7,861,647"})])
    assert problems == []
    rs = m["recording_summary"]
    assert rs["decisions"][0] == "Approved $7,861,647 bond for the Frost Elementary School roof replacement."
    # The decision keeps its page, quote, outcome, and labels, its title corrected too, and links to the correction.
    d = rs["decisions"][0]
    assert (d.page, d.outcome, d.main, d.topic, d.title) == (3, "approved", True, "schools",
                                                             "$7,861,647 bond for Frost Elementary School roof")
    assert d.corrected == URL + "#corrections" and rs["decisions"][1].corrected is None
    assert "$7,861,647" in rs["summary"] and "$7,861,647" in rs["discussion"][0]
    assert english["recording_summary"]["decisions"][0].startswith("Approved $7,861,647")
    assert m["summary_corrections"] == [{"note": "The agenda says otherwise.", "evidence": "https://example.org/agenda.pdf",
                                         "checked": "2026-11-09", "replaced": "$7,881,647", "replaced_with": "$7,861,647"}]


def test_words_are_replaced_in_a_translation_that_has_them():
    spanish = recording(decisions=["Se aprobó un bono de $7,881,647 para el techo de la Frost Elementary School.",
                                   "Fracasó una enmienda.", "Se aprobó el pago de $40,316.80, 7-0."])
    m, _, problems = corrected([correction(replaces="$7,881,647", **{"with": "$7,861,647"})], in_language=spanish)
    assert problems == [] and m["recording_summary"]["decisions"][0].startswith("Se aprobó un bono de $7,861,647")


def test_a_decision_taken_off_and_one_added():
    m, english, problems = corrected([
        correction(remove_decision="affordable housing requirement"),
        correction(add_decision="Approved a $10,149,973 bond for an airport administration building.",
                   add_decision_shown="Se aprobó un bono de $10,149,973 para el aeropuerto."),
    ])
    assert problems == []
    en = english["recording_summary"]
    assert len(en["decisions"]) == 3 and "affordable" not in " ".join(en["decisions"])
    # The lists beside the decisions follow them.
    assert en["decision_titles"] == ["$7,881,647 bond for Frost Elementary School roof", "Prior year invoice", None]
    added = m["recording_summary"]["decisions"][-1]
    # Shown in the page's language, its outcome read from its English.
    assert added == "Se aprobó un bono de $10,149,973 para el aeropuerto." and added.outcome == "approved"
    assert added.corrected == URL + "#corrections" and added.page is None
    assert [c.get("removed") or c.get("added") for c in m["summary_corrections"]] == [
        "Failed: an amendment to keep the 15% affordable housing requirement.",
        "Se aprobó un bono de $10,149,973 para el aeropuerto."]


def test_the_decisions_lists_are_corrected(config, data_dir):
    """The meeting's decisions, as the decisions page, decisions.csv, and the digest read them."""
    with i18n.use("en"):
        all_meetings = build_site.load_meetings(data_dir, build_site.date(2026, 10, 1), config["summaries"]["model"])["all"]
    m = next(m for m in all_meetings if m["decisions"]["decided"])
    first = str(m["decisions"]["decided"][0])
    fixes = [{"meeting": m["id"], "replaces": first, "with": "Approved a corrected decision.", "note": "n",
              "checked": "2030-01-01"},
             {"meeting": m["id"], "add_decision": "Approved an added decision.", "note": "n", "checked": "2030-01-01"}]
    with i18n.use("en"):
        loaded = build_site.load_meetings(data_dir, build_site.date(2026, 10, 1), config["summaries"]["model"], [], fixes)
    m = next(x for x in loaded["all"] if x["id"] == m["id"])
    decided = m["decisions"]["decided"]
    assert first not in decided
    marked = {str(d) for d in decided if d.corrected}
    assert marked == {"Approved a corrected decision.", "Approved an added decision."}
    assert loaded["correction_problems"] == []


@pytest.mark.parametrize("fix, problem", [
    ({"replaces": "$9,999", "with": "$1"}, "\"$9,999\" isn't in its summaries any more"),
    ({"remove_decision": "Approved"}, "2 of its decisions have \"Approved\""),
    ({"remove_decision": "a vote never taken"}, "0 of its decisions have"),
    ({"add_decision": "Approved $40,316.80 payment of a prior year invoice, 7-0."}, "already has"),
])
def test_a_correction_that_no_longer_fits_is_not_shown(fix, problem):
    m, english, problems = corrected([correction(**fix)])
    assert len(problems) == 1 and problem in problems[0] and m["summary_corrections"] == []
    assert english["recording_summary"] == recording()


def test_a_correction_comes_down_when_the_summary_is_made_again():
    m, _, problems = corrected([correction(replaces="$7,881,647", checked="2026-11-04", **{"with": "$7,861,647"})])
    assert "recording summary was made again after the correction was checked on 2026-11-04" in problems[0]
    assert "$7,881,647" in m["recording_summary"]["decisions"][0] and m["summary_corrections"] == []
    # Made the day it was checked is the summary it was checked against.
    _, _, problems = corrected([correction(replaces="$7,881,647", checked="2026-11-05", **{"with": "$7,861,647"})])
    assert problems == []


def test_a_meeting_with_no_decisions_has_none_to_correct():
    m = meeting(status="cancelled")
    _, _, problems = corrected([correction(add_decision="Approved a bond.")], m=m)
    assert "has no decisions" in problems[0]


@pytest.mark.parametrize("fields, message", [
    ({"replaces": "$1"}, "`with`"),
    ({"replaces": "$1", "with": "$2", "add_decision": "Approved."}, "not replaces and add_decision"),
    ({"add_decision": "Approved.", "doubtful": True}, "not add_decision and doubtful"),
    ({"add_decision": "Approved.", "note": ""}, "`note`"),
])
def test_an_entry_that_doesnt_say_what_it_corrects_stops_the_build(fields, message):
    with pytest.raises(SystemExit, match=message):
        build_site.summary_corrections([meeting()], [correction(**fields)])


def test_corrections_of_listings_and_of_summaries_are_kept_apart():
    m = {**meeting(), "listed": True, "history": [], "start_time": "18:00", "listed_time": None}
    fixes, problems = build_site.summary_corrections([m], [correction(add_decision="Approved."),
                                                           correction(doubtful=True)])
    assert list(fixes) == ["m1"] and len(fixes["m1"]) == 1 and problems == []
    assert build_site.apply_corrections([m], [correction(add_decision="Approved.")]) == [] and "correction" not in m
    _, problems = build_site.summary_corrections([], [correction(add_decision="Approved.")])
    assert "0 meetings match it" in problems[0]


def test_the_daily_alert_lists_a_towns_corrections_not_shown(tmp_path):
    from datetime import datetime, timezone
    for name in ("gloucester-ma", "salem-ma"):
        (tmp_path / "towns" / name / "config").mkdir(parents=True)
        (tmp_path / "towns" / name / "config" / f"{name.split('-')[0]}.toml").write_text("")
        record = {"finished_at": "2026-10-02T09:00:00+00:00", "deployed": True, "stale": False, "fetched": True,
                  "steps": [], "corrections": [f"Correction {n} | x" for n in range(5)] if name == "salem-ma" else []}
        path = tmp_path / "towns" / name / "data" / network.RUN_RECORD
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record))
    rows = network.behind(tmp_path, 30, datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc))
    assert [r["folder"] for r in rows] == ["salem-ma"]
    # A few, then how many more; a | doesn't end the table's cell.
    assert rows[0]["problems"] == ["not shown: Correction 0 \\| x", "not shown: Correction 1 \\| x",
                                   "not shown: Correction 2 \\| x", "and 2 more corrections not shown"]


def test_the_build_reports_its_corrections_not_shown(tmp_path, monkeypatch):
    def build(town, out, data, missing, share_cards, problems, preview=None):
        problems.append("Correction for City Council 2026-11-04: 0 meetings match it, so it isn't shown.")
        return ["/"]
    monkeypatch.setattr(build_site, "build", build)
    report = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["build_site", "--town", "gloucester", "--report", str(report)])
    build_site.main()
    assert json.loads(report.read_text()) == {"corrections": [
        "Correction for City Council 2026-11-04: 0 meetings match it, so it isn't shown."]}


def test_a_run_records_its_corrections_not_shown(tmp_path, monkeypatch):
    (tmp_path / "towns" / "salem-ma" / "config").mkdir(parents=True)
    (tmp_path / "towns" / "salem-ma" / "config" / "salem.toml").write_text("")

    def step(name, cmd, env, cwd, timeout):
        if name == "Build site":
            Path(cmd[cmd.index("--report") + 1]).write_text(json.dumps({"corrections": ["not shown"]}))
        return {"name": name, "ok": True, "error": None, "seconds": 0.0}
    monkeypatch.setattr(network, "step", step)
    result = network.run_town(tmp_path, "salem-ma", fetch=False, deploy=False, reports=None)
    assert result["corrections"] == ["not shown"]
    assert not (tmp_path / "towns" / "salem-ma" / ".build-report.json").exists()


def test_a_translation_in_its_own_words_is_shown_corrected_in_english():
    spanish = recording(decisions=["Se aprobó un bono para el techo de la Frost Elementary School.",
                                   "Fracasó una enmienda.", "Se aprobó el pago de una factura, 7-0."],
                        decision_titles=["Bono para el techo de la Frost", "Enmienda", "Factura"])
    m, _, problems = corrected([correction(replaces="$7,881,647", **{"with": "$7,861,647"})], in_language=spanish)
    rs = m["recording_summary"]
    assert problems == [] and rs["english"] is True
    first = rs["decisions"][0]
    assert first == "Approved $7,861,647 bond for the Frost Elementary School roof replacement."
    assert first.corrected and first.page == 3 and first.title == "$7,861,647 bond for Frost Elementary School roof"
    assert rs["decisions"][1] == "Failed: an amendment to keep the 15% affordable housing requirement."
    assert not rs["decisions"][1].corrected
