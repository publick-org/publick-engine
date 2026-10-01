"""Tests for agenda previews, using a stand-in model client (no API calls)."""

import json
from pathlib import Path

from conftest import FETCHED_AT
from fakes import FakeAnthropic, FakeCityClient
from pipeline import fetch_meetings, summarize
from pipeline.config import load_config


def setup(tmp_path):
    config = load_config("gloucester")
    fetch_meetings.run(config, FakeCityClient(), tmp_path, now=FETCHED_AT)
    return config


def test_preview_is_cached_by_document_hash(tmp_path):
    config = setup(tmp_path)
    client = FakeAnthropic()
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    # Every fixture meeting links the same agenda PDF, so it is processed once: a summary,
    # then, as it's a scan with no text of its own, a transcription.
    assert result["summarized"] == 1 and result["transcribed"] == 1
    assert len(client.calls) == 2
    again = FakeAnthropic()
    assert summarize.run(config, again, tmp_path, limit=50, now=FETCHED_AT)["summarized"] == 0
    assert again.calls == []


def test_request_sends_pdf_and_asks_for_structured_output(tmp_path):
    config = setup(tmp_path)
    client = FakeAnthropic()
    summarize.run(config, client, tmp_path, limit=1, now=FETCHED_AT)
    call = client.calls[0]
    assert call["model"] == config["summaries"]["model"]
    document = call["messages"][0]["content"][0]
    assert document["type"] == "document" and document["source"]["media_type"] == "application/pdf"
    assert call["output_config"]["format"]["type"] == "json_schema"


def test_saved_preview_records_source_and_model(tmp_path):
    config = setup(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=1, now=FETCHED_AT)
    saved = json.loads(next((tmp_path / "summaries").glob("*.json")).read_text())
    assert saved["source_url"].startswith("https://www.gloucester-ma.gov/Archive.aspx?ADID=")
    assert saved["model"] == config["summaries"]["model"]
    assert saved["summary"] and saved["items"]


def test_truncated_response_is_not_saved(tmp_path):
    config = setup(tmp_path)
    result = summarize.run(config, FakeAnthropic(stop_reason="max_tokens"), tmp_path, limit=1, now=FETCHED_AT)
    assert result["summarized"] == 0 and result["errors"]
    assert not (tmp_path / "summaries").exists()


def test_skips_without_api_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["summarize"])
    assert summarize.main() == 0
    assert "skipping" in capsys.readouterr().out


def test_new_prompt_version_or_model_regenerates(tmp_path, monkeypatch):
    config = setup(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)
    monkeypatch.setitem(summarize.KINDS["agenda"], "version", summarize.KINDS["agenda"]["version"] + 1)
    again = FakeAnthropic()
    assert summarize.run(config, again, tmp_path, limit=5, now=FETCHED_AT)["summarized"] == 1
    config["summaries"]["model"] = "another-model"
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)["summarized"] == 1


def test_out_of_credit_stops_after_first_failure(tmp_path):
    config = setup(tmp_path)
    # Make the documents distinct so there is more than one to process.
    from pipeline import fetch_minutes
    from fakes import FakeCityClient
    fetch_minutes.run(config, FakeCityClient(), tmp_path, now=FETCHED_AT)

    class Broke(FakeAnthropic):
        def __init__(self):
            super().__init__()
            outer = self

            class Messages:
                def stream(self, **kwargs):
                    outer.calls.append(kwargs)
                    raise RuntimeError("Your credit balance is too low to access the Anthropic API.")

            self.messages = Messages()

    client = Broke()
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    assert len(client.calls) == 1
    assert result["summarized"] == 0 and "out of credit" in result["errors"][0]


def test_oversized_pdfs_are_not_sent():
    from pipeline import summarize
    assert summarize.too_large({"bytes": 29_811_159})
    assert not summarize.too_large({"bytes": 900_000})
    assert not summarize.too_large({})


def test_site_keeps_older_summary_until_replaced(tmp_path, monkeypatch):
    config = setup(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    sha = next(m["agendas"][-1]["sha256"] for m in store.values() if m.get("agendas"))
    monkeypatch.setitem(summarize.KINDS["agenda"], "version", summarize.KINDS["agenda"]["version"] + 1)
    model = config["summaries"]["model"]
    assert summarize.cached(tmp_path, sha, model, "agenda") is None
    assert summarize.cached(tmp_path, sha, model, "agenda", current=False)["summary"]
    assert summarize.cached(tmp_path, sha, model, "minutes", current=False) is None


def with_minutes(tmp_path):
    """The fixture calendar and minutes: an upcoming agenda and 11 recent minutes are new, 4 older minutes are backlog."""
    from pipeline import fetch_minutes
    config = setup(tmp_path)
    fetch_minutes.run(config, FakeCityClient(), tmp_path, now=FETCHED_AT)
    return config


# What one FakeAnthropic request costs at the fixture's prices: 1,500 tokens in at $2, 400 out at $10 a million.
REQUEST = 0.007


def test_new_documents_go_before_the_backlog(tmp_path):
    config = with_minutes(tmp_path)
    client = FakeAnthropic()
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT, backlog_allowance=0)
    assert result["summarized"] == 12 and result["remaining"] == 4
    assert "older documents wait" in result["stopped"] and not result["errors"]
    titles = [c["messages"][0]["content"][1]["text"] for c in client.calls]
    assert not any("July 23" in t or "2026-07-23" in t for t in titles)


def test_backlog_gets_its_own_allowance(tmp_path):
    config = with_minutes(tmp_path)
    result = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT, backlog_allowance=REQUEST * 1.5)
    # Every new document, then older ones until their allowance is spent: two, since the check comes before each.
    assert result["summarized"] == 14 and result["remaining"] == 2


def test_stops_at_the_networks_allowance(tmp_path):
    config = with_minutes(tmp_path)
    result = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT, allowance=REQUEST * 2.5)
    assert result["summarized"] == 3
    assert "share of the network's monthly summary budget" in result["stopped"]


def test_ledger_counts_this_months_summaries(tmp_path):
    config = with_minutes(tmp_path)
    result = summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)
    ledger = json.loads((tmp_path / summarize.LEDGER).read_text())
    assert ledger == {"2026-09": {"cost": round(5 * REQUEST, 4), "documents": 5}}
    assert result["month_cost"] == round(5 * REQUEST, 2)
    saved = json.loads(next((tmp_path / "summaries").glob("*.json")).read_text())
    assert saved["cost"] == REQUEST


def test_ledger_is_filled_in_from_summaries_saved_before_costs_were(tmp_path):
    config = with_minutes(tmp_path)
    summarize.run(config, FakeAnthropic(), tmp_path, limit=3, now=FETCHED_AT)
    for path in (tmp_path / "summaries").glob("*.json"):
        record = json.loads(path.read_text())
        del record["cost"]
        path.write_text(json.dumps(record))
    (tmp_path / summarize.LEDGER).unlink()
    ledger = summarize.update_ledger(tmp_path, summarize.summary_settings(config), "2026-09")
    assert ledger["2026-09"] == {"cost": round(3 * REQUEST, 4), "documents": 3}


def test_ledger_keeps_earlier_months_as_they_were(tmp_path):
    config = with_minutes(tmp_path)
    (tmp_path / summarize.LEDGER).write_text(json.dumps({"2026-08": {"cost": 9.0, "documents": 99}}))
    summarize.run(config, FakeAnthropic(), tmp_path, limit=2, now=FETCHED_AT)
    # A summary from August made again in September replaces its file, so August isn't counted again.
    ledger = json.loads((tmp_path / summarize.LEDGER).read_text())
    assert ledger["2026-08"] == {"cost": 9.0, "documents": 99} and ledger["2026-09"]["documents"] == 2


def test_cut_off_responses_are_paid_for(tmp_path):
    config = with_minutes(tmp_path)
    result = summarize.run(config, FakeAnthropic(stop_reason="max_tokens"), tmp_path, limit=2, now=FETCHED_AT)
    assert result["summarized"] == 0 and result["estimated_cost"] == round(2 * REQUEST, 2)
    ledger = json.loads((tmp_path / summarize.LEDGER).read_text())
    assert ledger["2026-09"]["failed_cost"] == round(2 * REQUEST, 4)
    assert summarize.month_cost(ledger, "2026-09") == round(2 * REQUEST, 4)


def test_allowances_come_from_the_environment(monkeypatch):
    monkeypatch.setenv(summarize.ALLOWANCE_ENV, "3.25")
    monkeypatch.delenv(summarize.BACKLOG_ALLOWANCE_ENV, raising=False)
    assert summarize.dollars(summarize.ALLOWANCE_ENV) == 3.25
    assert summarize.dollars(summarize.BACKLOG_ALLOWANCE_ENV) is None


def test_upcoming_agenda_is_read_again_for_its_time_and_place(tmp_path):
    config = setup(tmp_path)
    store = json.loads((tmp_path / "meetings" / "meetings.json").read_text())
    upcoming = next(m for m in store.values() if m.get("agendas") and m["date"] >= FETCHED_AT.date().isoformat())
    upcoming["start_time"] = None  # as an Agenda Center lists it
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps(store))
    summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)
    path = tmp_path / "summaries" / f"{upcoming['agendas'][-1]['sha256']}.json"
    record = json.loads(path.read_text())
    # A summary from before agendas gave a time and place is made again, once, while the meeting is upcoming.
    for key in ("start_time", "location"):
        del record[key]
    path.write_text(json.dumps(record))
    today = FETCHED_AT.date().isoformat()
    model = config["summaries"]["model"]
    assert [d["sha256"] for _, _, d in summarize.pending_documents(tmp_path, today, model)] == [upcoming["agendas"][-1]["sha256"]]
    summarize.run(config, FakeAnthropic(), tmp_path, limit=5, now=FETCHED_AT)
    assert summarize.pending_documents(tmp_path, today, model) == []
    # Once the meeting is past, an old summary isn't made again for it.
    path.write_text(json.dumps(record))
    assert summarize.pending_documents(tmp_path, "2026-12-31", model) == []


def test_agenda_summary_asks_for_the_meetings_time_and_place():
    schema = summarize.KINDS["agenda"]["schema"]
    assert {"start_time", "location"} <= set(schema["required"])
    assert "24-hour HH:MM" in summarize.KINDS["agenda"]["prompt"]


# ---- Readable text: the PDF's own words, or a transcription after every summary ----

LEGISTAR = (Path(__file__).parent / "fixtures" / "legistar_minutes.pdf").read_bytes()


def minutes_town(tmp_path, pdf=LEGISTAR, date="2026-07-28"):
    """A town with one meeting whose minutes are the given PDF."""
    import hashlib
    sha = hashlib.sha256(pdf).hexdigest()
    (tmp_path / "meetings" / "minutes").mkdir(parents=True)
    (tmp_path / "meetings" / "minutes" / "m.pdf").write_bytes(pdf)
    meeting = {"date": date, "title": "City Council Meeting", "body": "City Council",
               "minutes": [{"id": "m", "file": "m.pdf", "sha256": sha, "bytes": len(pdf),
                            "source_url": "https://example.org/m.pdf", "fetched_at": FETCHED_AT.isoformat()}]}
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps({"m": meeting}))
    return sha


def saved(tmp_path, sha):
    return json.loads((tmp_path / "summaries" / f"{sha}.json").read_text())


def test_summaries_no_longer_ask_for_the_full_text():
    for kind in ("agenda", "minutes"):
        assert "transcript" not in summarize.KINDS[kind]["schema"]["properties"]
        assert "transcript" not in summarize.KINDS[kind]["prompt"]


def test_a_legistar_document_gets_its_own_text_and_no_transcription(tmp_path):
    config = load_config("gloucester")
    sha = minutes_town(tmp_path)
    client = FakeAnthropic()
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    assert result["summarized"] == 1 and result["transcribed"] == 0 and len(client.calls) == 1
    record = saved(tmp_path, sha)
    assert record["transcript_source"] == "pdf" and record["transcript"].startswith("# City of Malden")
    assert "plain_text" not in record


def test_an_old_ai_transcript_is_replaced_by_the_documents_own_text(tmp_path):
    config = load_config("gloucester")
    sha = minutes_town(tmp_path)
    summarize.save_record(tmp_path, sha, {**FakeAnthropic.MINUTES, "kind": "minutes", "transcript": "An AI copy.",
                                          "model": config["summaries"]["model"],
                                          "prompt_version": summarize.KINDS["minutes"]["version"],
                                          "generated_at": "2026-09-01T00:00:00-04:00", "cost": 0.1})
    client = FakeAnthropic()
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    assert result["laid_out"] == 1 and client.calls == []
    record = saved(tmp_path, sha)
    assert record["transcript_source"] == "pdf" and "An AI copy." not in record["transcript"]
    # Done once: the next run leaves it.
    assert summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT)["laid_out"] == 0


def test_a_scan_is_transcribed_a_few_pages_a_request(tmp_path, monkeypatch):
    config = load_config("gloucester")
    scan = (Path(__file__).parent / "fixtures" / "civicplus_agenda_scanned.pdf").read_bytes()
    from pypdf import PdfReader, PdfWriter
    import io
    writer = PdfWriter()
    for _ in range(4):
        for page in PdfReader(io.BytesIO(scan)).pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    long_scan = out.getvalue()
    pages = len(PdfReader(io.BytesIO(long_scan)).pages)
    monkeypatch.setitem(summarize.TRANSCRIBE, "pages", 2)
    sha = minutes_town(tmp_path, long_scan)
    client = FakeAnthropic()
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    asks = [c for c in client.calls if set(c["output_config"]["format"]["schema"]["properties"]) == {"transcript"}]
    assert result["transcribed"] == 1 and len(asks) == -(-pages // 2)
    assert "pages 1 to 2 of" in asks[0]["messages"][0]["content"][1]["text"]
    record = saved(tmp_path, sha)
    assert record["transcript_source"] == "ai" and record["transcript_cost"] > 0
    ledger = json.loads((tmp_path / summarize.LEDGER).read_text())
    assert ledger["2026-09"]["transcript_cost"] == round(record["transcript_cost"], 4)


def test_transcriptions_wait_for_every_summary(tmp_path):
    config = setup(tmp_path)
    # The fixture agenda's summary is the only one waiting; with no room for it, nothing is transcribed.
    client = FakeAnthropic()
    result = summarize.run(config, client, tmp_path, limit=0, now=FETCHED_AT)
    assert result["transcribed"] == 0 and client.calls == []


def test_transcriptions_wait_when_the_budget_is_spent(tmp_path):
    config = setup(tmp_path)
    # Summarized, with nothing left of the backlog's share for a transcription.
    result = summarize.run(config, FakeAnthropic(), tmp_path, limit=50, now=FETCHED_AT, backlog_allowance=0.0)
    assert result["summarized"] == 1 and result["transcribed"] == 0
    assert "transcriptions wait" in result["stopped"]
    record = json.loads(next((tmp_path / "summaries").glob("*.json")).read_text())
    assert "transcript" not in record and record["plain_text"] == ""


def test_the_networks_budget_counts_transcriptions(tmp_path):
    from pipeline import network
    town = tmp_path / "towns" / "a-ma"
    (town / "config").mkdir(parents=True)
    (town / "config" / "a.toml").write_text("")
    (town / "data").mkdir()
    (town / "data" / summarize.LEDGER).write_text(
        json.dumps({"2026-09": {"cost": 10.0, "failed_cost": 1.0, "transcript_cost": 4.0}}))
    assert network.summary_budget(tmp_path, 50.0, 1, FETCHED_AT.date())["spent"] == 15.0
    assert summarize.month_cost(json.loads((town / "data" / summarize.LEDGER).read_text()), "2026-09") == 15.0


def transcription_requests(client):
    return [c for c in client.calls if set(c["output_config"]["format"]["schema"]["properties"]) == {"transcript"}]


def test_a_pdf_with_text_isnt_transcribed(tmp_path, monkeypatch):
    from pipeline import pdftext
    monkeypatch.setattr(pdftext, "STYLES", [])  # not a supported style, so not laid out here
    config = load_config("gloucester")
    sha = minutes_town(tmp_path)
    client = FakeAnthropic()
    result = summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)
    # Screen readers can read it, so the page links it: a summary, and no transcription.
    assert result["summarized"] == 1 and result["transcribed"] == 0 and transcription_requests(client) == []
    record = saved(tmp_path, sha)
    assert "transcript" not in record and record["needs_transcript"] is False
    assert "called the meeting to order" in record["plain_text"]   # for search and the street lookup


def test_a_scanners_own_text_is_transcribed_as_a_scan(tmp_path, monkeypatch):
    from pipeline import pdftext
    assert pdftext.page_texts(LEGISTAR)
    monkeypatch.setattr(pdftext, "made_by", lambda pdf: "RICOH IM C6000")
    assert pdftext.page_texts(LEGISTAR) is None
    config = load_config("gloucester")
    sha = minutes_town(tmp_path)
    client = FakeAnthropic()
    assert summarize.run(config, client, tmp_path, limit=50, now=FETCHED_AT)["transcribed"] == 1
    assert saved(tmp_path, sha)["transcript_source"] == "ai" and "needs_transcript" not in saved(tmp_path, sha)
