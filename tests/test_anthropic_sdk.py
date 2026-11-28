"""The engine's requests and its reading of the answers, through the real anthropic SDK: its HTTP is answered
here, as the API answers it (a stream of events, a batch's results as JSON lines, an error's body), so the SDK
builds its own objects (Message, MessageBatch, its errors) for the engine to read. The other tests stand in for
the client with plain objects; these hold the engine to the SDK's own shapes."""

import json
from datetime import datetime, timedelta, timezone

import anthropic
import httpx2
import pytest

from pipeline import evaluate, evaluate_batch, summarize, translate

MODEL = "claude-haiku-5-5"
MINUTES = {"headline": "Approved a site plan for 12 Main St.", "summary": "The board approved a site plan.",
           "is_minutes": True,
           "decisions": [{"decision": "Approved the site plan, 5-0", "outcome": "approved", "quote": "Motion carried 5-0."}]}
SENT = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def client(answer) -> anthropic.Anthropic:
    """A real client whose requests answer() answers, as (body, path, headers) -> httpx2.Response."""
    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        return answer(body, request.url.path, request.headers)
    # The API's own address, whatever ANTHROPIC_BASE_URL the tests run with.
    return anthropic.Anthropic(api_key="test", base_url="https://api.anthropic.com", max_retries=0,
                               http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))


def events(*events: dict) -> httpx2.Response:
    """A streamed answer: each event as the API sends it."""
    return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                           content="".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode())


def streamed(text: str, stop_reason: str = "end_turn", stop_details: dict | None = None, cache_read: int = 0) -> httpx2.Response:
    """A streamed message as a current model writes it: a thinking block with its text left out, then the
    answer's text in pieces, with pings and an event this SDK doesn't know between them."""
    half = len(text) // 2
    return events(
        {"type": "message_start", "message": {
            "id": "msg_stream", "type": "message", "role": "assistant", "model": MODEL, "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 1, "cache_read_input_tokens": cache_read}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "c2ln"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "ping"},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": text[:half]}},
        {"type": "future_event", "detail": "not known to this SDK"},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": text[half:]}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details},
         "usage": {"output_tokens": 400}},
        {"type": "message_stop"},
    )


def message(text: str, stop_reason: str = "end_turn", **usage) -> dict:
    """A whole message as the API returns it, with a thinking block before its text."""
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": MODEL,
            "content": [{"type": "thinking", "thinking": "", "signature": "c2ln"}, {"type": "text", "text": text}],
            "stop_reason": stop_reason, "stop_sequence": None, "stop_details": None,
            "usage": {"input_tokens": 600, "output_tokens": 300, **usage}}


def test_a_summary_streams_through_the_real_sdk():
    sent = []

    def answer(body, path, headers):
        sent.append((body, path, headers))
        return streamed(json.dumps(MINUTES), cache_read=300)

    recorder = evaluate.Recorder(client(answer))
    made, usage = summarize.summarize_pdf(recorder, MODEL, "minutes", b"%PDF minutes", "Planning Board", "2026-11-02", "high")
    assert made == MINUTES and usage == {"input_tokens": 1200, "output_tokens": 400}
    # The evaluation's record of it reads the same message, with what was read from the cache.
    [record] = recorder.requests
    assert record["usage"] == {"input_tokens": 1200, "output_tokens": 400, "cache_read_input_tokens": 300}
    assert record["stop_reason"] == "end_turn" and record["effort"] == "high"
    [(body, path, headers)] = sent
    assert path == "/v1/messages" and body["stream"] is True
    # As the API version this SDK sends takes it: the document in the user's turn, and no sampling settings.
    assert headers["anthropic-version"] == "2026-10-21"
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert [m["role"] for m in body["messages"]] == ["user"] and body["messages"][0]["content"][0]["type"] == "document"
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_a_refusal_streamed_through_the_real_sdk_is_stopped_with_its_category():
    def answer(body, path, headers):
        return streamed("", "refusal", {"type": "refusal", "category": "cyber", "explanation": "Declined."})

    with pytest.raises(summarize.StoppedEarly) as stopped:
        summarize.summarize_pdf(client(answer), MODEL, "minutes", b"%PDF minutes", "Planning Board", "2026-11-02")
    assert str(stopped.value) == "stopped early: refusal (cyber)"
    assert stopped.value.usage == {"input_tokens": 1200, "output_tokens": 400}


def test_a_transcription_streams_through_the_real_sdk():
    def answer(body, path, headers):
        return streamed(json.dumps({"transcript": "1. Call to order."}))

    text, usage = summarize.transcribe_pdf(client(answer), MODEL, "agendas", b"%PDF scan", "City Council", "2026-11-02")
    assert text == "1. Call to order." and usage["requests"] == [{"input_tokens": 1200, "output_tokens": 400}]


def test_a_review_and_its_cached_tokens_are_read_from_the_real_sdk_s_message():
    def answer(body, path, headers):
        return httpx2.Response(200, json=message(json.dumps({"problems": [
            {"id": "0", "is_error": True, "problem": "A number is missing."},
            {"id": "1", "is_error": False, "problem": "Faithful."}]}),
            cache_creation_input_tokens=0, cache_read_input_tokens=2048))

    response = client(answer).messages.create(model=MODEL, max_tokens=1024, messages=[{"role": "user", "content": "Review"}])
    assert translate.review_found(response) == {"0": "A number is missing."}
    # Only the cache's tokens there were.
    assert translate.usage_of(response) == {"input_tokens": 600, "output_tokens": 300, "cache_read_input_tokens": 2048}
    assert summarize.stop_reason(response) == "end_turn"


def test_a_batch_goes_through_the_real_sdk():
    """Sent, waited for, and its results read (in their own order): one made, one errored, one expired."""
    sent, looked = [], []
    ended = SENT + timedelta(minutes=7)

    def batch(status):
        return {"id": "msgbatch_1", "type": "message_batch", "processing_status": status,
                "request_counts": {"processing": 0, "succeeded": 1, "errored": 1, "canceled": 0, "expired": 1},
                "created_at": SENT.isoformat(), "ended_at": ended.isoformat() if status == "ended" else None,
                "expires_at": (SENT + timedelta(days=1)).isoformat(), "archived_at": None, "cancel_initiated_at": None,
                "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results" if status == "ended" else None}

    def answer(body, path, headers):
        if path == "/v1/messages/batches":
            sent.append(body)
            return httpx2.Response(200, json=batch("in_progress"))
        if path == "/v1/messages/batches/msgbatch_1":
            looked.append(path)
            return httpx2.Response(200, json=batch("ended" if len(looked) > 1 else "in_progress"))
        assert path == "/v1/messages/batches/msgbatch_1/results", path
        lines = [
            {"custom_id": "doc-2", "result": {"type": "expired"}},
            {"custom_id": "doc-1", "result": {"type": "errored", "error": {
                "type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}, "request_id": "req_1"}}},
            {"custom_id": "doc-0", "result": {"type": "succeeded", "message": message(json.dumps(MINUTES))}},
        ]
        return httpx2.Response(200, headers={"content-type": "application/binary"},
                               content="".join(json.dumps(line) + "\n" for line in lines).encode())

    real = client(answer)
    documents = [{"name": n, "title": "Planning Board", "date": "2026-11-02"} for n in ("one", "two", "three")]
    pdfs = [b"%PDF one", b"%PDF two", b"%PDF three"]
    batches = evaluate_batch.send_and_wait(real, documents, pdfs, MODEL, ["high"], 3600, clock=lambda: 0, sleep=lambda s: None)
    assert batches == {"high": {"id": "msgbatch_1", "seconds": 420, "cancelled": False}}
    [body] = sent
    assert [r["custom_id"] for r in body["requests"]] == ["doc-0", "doc-1", "doc-2"]
    assert body["requests"][0]["params"]["messages"][0]["content"][0]["type"] == "document"
    assert not {"temperature", "top_p", "top_k"} & set(body["requests"][0]["params"])

    # As pipeline/summarize.py's collect_batch() and pipeline/evaluate_batch.py's collect() read them: by id.
    by_id = {r.custom_id: r.result for r in real.messages.batches.results("msgbatch_1")}
    assert summarize.summary_result(by_id["doc-0"].message) == (MINUTES, {"input_tokens": 600, "output_tokens": 300})
    assert evaluate_batch.judged(documents[1], pdfs[1], by_id["doc-1"], {}) == {
        "name": "two", "failed": "errored", "error": "Overloaded"}
    assert evaluate_batch.judged(documents[2], pdfs[2], by_id["doc-2"], {}) == {
        "name": "three", "failed": "expired", "error": None}
    assert evaluate_batch.judged(documents[0], pdfs[0], by_id.get("doc-3"), {}) == {
        "name": "one", "failed": "missing", "error": None}


@pytest.mark.parametrize("status, kind, message, out_of_credit", [
    (400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API. "
                                   "Please go to Plans & Billing to upgrade or purchase credits.", True),
    (529, "overloaded_error", "Overloaded", False),
    (429, "rate_limit_error", "Number of request tokens has exceeded your per-minute rate limit", False),
])
def test_the_real_sdk_s_errors_are_read_as_the_engine_reads_them(status, kind, message, out_of_credit):
    """A run stops at the first request refused for want of credit (translate.out_of_credit(), from the
    error's text, which the SDK's error keeps), and the weekly digest's subject catches anthropic.APIError."""
    def answer(body, path, headers):
        return httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": message},
                                             "request_id": "req_1"})

    with pytest.raises(anthropic.APIError) as error:
        client(answer).messages.create(model=MODEL, max_tokens=1024, messages=[{"role": "user", "content": "Hi"}])
    assert isinstance(error.value, anthropic.APIStatusError) and error.value.status_code == status
    assert translate.out_of_credit(error.value) is out_of_credit
