"""Real summaries made again and checked as the site checks them (pipeline/evaluate_shadow.py), against the fake model."""

import hashlib
import json

import pytest
from fakes import FakeAnthropic

from pipeline import evaluate, evaluate_factcheck, evaluate_shadow, summarize

MINUTES_PDF, AGENDA_PDF = b"%PDF minutes", b"%PDF agenda scan"
# The minutes' text: the fake model's decision is in it, and the published summary's address isn't.
MINUTES_TEXT = ("PLANNING BOARD MINUTES. Motion to approve the site plan for 12 Main St. Motion carried 5-0. "
                "The board approved a site plan for 12 Main St.")


def item(kind, pdf, record, pages=None, **more):
    return {"town": "t-ma", "kind": kind, "platform": "agendacenter", "body": "Planning Board",
            "title": "Planning Board Meeting", "date": "2026-09-22", "sha256": hashlib.sha256(pdf).hexdigest(),
            "file_url": f"https://files.publick.org/t-ma/{kind}.pdf", "source_url": None, "record": record,
            "network_fact_check": {"result": "failed"}, "pages": pages, **more}


SAMPLE = {"words": ["site", "plan"], "items": [
    item("minutes", MINUTES_PDF, {
        "kind": "minutes", "model": "claude-sonnet-5", "prompt_version": 4,
        "headline": "Approved a site plan for 14 Main St.", "summary": "The board approved a site plan.",
        "decisions": ["Approved the site plan for 14 Main St, 5-0", "Approved the site plan for 12 Main St, 5-0"],
        "decision_evidence": [{"outcome": "approved", "quote": "Motion to approve the site plan for 12 Main St. Motion carried 5-0."}] * 2,
        "usage": {"input_tokens": 3000, "output_tokens": 500}, "cost": 0.0055}, pages=[MINUTES_TEXT]),
    item("agenda", AGENDA_PDF, {
        "kind": "agenda", "model": "claude-sonnet-5", "prompt_version": 5,
        "headline": "ADA compliance with Joe Lucido.", "summary": "The commission will meet.", "items": ["ADA compliance"],
        "transcript": FakeAnthropic.TRANSCRIPT["transcript"], "transcript_source": "ai",
        "usage": {"input_tokens": 2000, "output_tokens": 200}}),
]}
FILES = {f"https://files.publick.org/t-ma/{kind}.pdf": pdf for kind, pdf in (("minutes", MINUTES_PDF), ("agenda", AGENDA_PDF))}
HAIKU = evaluate.prices_for("claude-haiku-5-5")


def run(client, **kwargs):
    return evaluate_shadow.run(SAMPLE, client, "claude-haiku-5-5", kwargs.pop("effort", "medium"), HAIKU,
                               get=FILES.__getitem__, **kwargs)


def test_each_document_is_summarized_again_as_a_run_sends_it():
    client = FakeAnthropic()
    run(client)
    minutes, agenda = client.calls
    assert minutes["model"] == agenda["model"] == "claude-haiku-5-5"
    assert minutes["output_config"]["effort"] == "medium" and agenda["output_config"]["effort"] == "medium"
    # The prompt of the document's kind, with its meeting's title, and the document itself.
    assert minutes["system"] == summarize.KINDS["minutes"]["system"] and agenda["system"] == summarize.KINDS["agenda"]["system"]
    assert minutes["messages"][0]["content"][1]["text"].startswith("These are the posted minutes for: Planning Board Meeting, 2026-09-22.")
    assert minutes["messages"][0]["content"][0]["type"] == "document"
    # Without an effort, none is sent.
    client = FakeAnthropic()
    run(client, effort=None)
    assert all("effort" not in c["output_config"] for c in client.calls)


def test_both_summaries_are_checked_alike_and_counted():
    docs = run(FakeAnthropic())
    minutes, agenda = docs
    new, pub = minutes["new"], minutes["published"]
    # The fake model's decision is in the minutes; the published one's 14 Main St isn't, so it's hidden.
    assert new["result"] == "ok" and new["entries"] == 1 and new["hidden_entries"] == 0
    assert pub["result"] == "failed" and pub["entries"] == 2 and pub["hidden_entries"] == 1
    assert pub["problems_by_kind"].get("number") and "headline" in pub["hidden"] and pub["saved_result"] == "failed"
    assert new["usage"] == {"input_tokens": 1500, "output_tokens": 400}
    assert new["cost"] == round(summarize.cost(new["usage"], HAIKU), 6) and new["long_prompt"] is False
    assert pub["cost"] == round((3000 * 2 + 500 * 10) / 1e6, 6) and pub["paid"] == 0.0055
    # Both side by side, to read.
    assert new["headline"] == FakeAnthropic.MINUTES["headline"] and pub["headline"] == "Approved a site plan for 14 Main St."
    assert new["decisions"] == ["Approved the site plan for 12 Main St, 5-0"] and len(pub["decisions"]) == 2
    # A scan is checked against the network's transcription, for both.
    assert agenda["new"]["source"] == agenda["published"]["source"] == "ai"
    assert agenda["new"]["items"] == FakeAnthropic.PREVIEW["items"] and agenda["published"]["paid"] is None
    totals = evaluate_shadow.totals(docs)
    assert totals["all"]["new"]["documents"] == 2 and totals["minutes"]["published"]["hidden_entries"] == 1
    assert totals["all"]["published"]["input_tokens"] == 5000 and totals["agenda"]["new"]["entries"] == 3
    text = evaluate_shadow.report(docs, {"model": "claude-haiku-5-5", "effort": "medium", "prices": HAIKU})
    assert "# Summaries made again with claude-haiku-5-5, checked as the site checks them" in text
    assert "| Hidden by the check | 0 (and 0 headlines) | 1 (and 1 headlines) |" in text
    assert "| t-ma " in text and "claude-sonnet-5 v4 | ok / failed | 1 / 2 | 0 / 1 |" in text
    json.dumps(docs)


def test_a_summary_cut_off_or_refused_is_reported_and_the_set_goes_on():
    docs = run(FakeAnthropic(stop_reason="max_tokens"))
    assert [d["new"]["result"] for d in docs] == ["max_tokens", "max_tokens"]
    assert docs[0]["new"]["cost"] > 0 and docs[0]["published"]["result"] == "failed"
    text = evaluate_shadow.report(docs, {"model": "claude-haiku-5-5", "effort": None, "prices": HAIKU})
    assert "## Cut off, refused, or not made" in text and "stopped early: max_tokens" in text
    assert evaluate_shadow.totals(docs)["all"]["new"]["results"] == {"max_tokens": 2}


def test_only_one_document():
    sha = SAMPLE["items"][1]["sha256"]
    assert [d["kind"] for d in run(FakeAnthropic(), only=f"t-ma {sha[:12]}")] == ["agenda"]


def test_a_document_that_isnt_the_one_summarized_stops_the_run():
    with pytest.raises(SystemExit, match="no copy of its PDF"):
        evaluate_shadow.run(SAMPLE, FakeAnthropic(), "claude-haiku-5-5", None, HAIKU, get=lambda url: b"another")


def test_the_sample_has_what_the_set_needs():
    sample = evaluate_factcheck.load()
    for i in sample["items"]:
        assert i["title"] and i["record"]["model"] and i["record"]["usage"]["input_tokens"] > 0, evaluate_factcheck.key(i)
    assert {i["kind"] for i in sample["items"]} == {"agenda", "minutes"}
