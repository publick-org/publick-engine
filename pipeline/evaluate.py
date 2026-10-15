"""The minutes prompt run against the real model, on minutes whose decisions were checked by hand.

Before a change to the minutes prompt ships, each document in evals/minutes.json is downloaded
(and its sha256 checked), summarized by the model with the prompt as it is now, transcribed too
if it's a scan, and checked as the site checks it (pipeline/factcheck.py). The report says, for
the whole set:

- each decision written down by hand: right (the model gave its outcome), wrong (it gave
  another), or missing; and for a right one, whether the check held it back anyway, and for a
  wrong one, whether the check caught it;
- every decision the check held back or listed, and why: a quote not in the minutes, an
  outcome that disagrees, a number or name far from the quote;
- errors planted in the model's own decisions that passed (approved turned to denied, and
  denied to approved, in both the decision and its outcome), and how many the check caught;
- which model ran, at what effort, and for each document its pages, tokens, seconds, and cost (a
  model priced higher for a long prompt is priced so, request by request: PRICES).

A wrong outcome the site would show fails the run (exit status 1): that's the error the checks
are for; and so does a summary cut off at the prompt's max_tokens, which the site would never get
(the report shows what it had written). Everything else is for a person to weigh.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate [--only NAME] [--model MODEL] [--effort EFFORT] [--json FILE]
    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set translations [--model MODEL] [--review-model MODEL] [--json FILE]   # the translation prompt's set
    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set batch [--efforts low,medium,high] [--json FILE]   # as a batch, by effort
    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set shadow [--model MODEL] [--effort EFFORT] [--json FILE]   # real summaries made again

A model in PRICES needs no prices; another needs --input-price and --output-price. Without --effort,
the model's default, as a town's run sends it. The JSON (--json) has the model, the effort, the
prices, the totals, and each document's result, so two runs can be compared.

It costs about $1 to $2 for the whole set (a few cents a document, more for the scan).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from pipeline import factcheck, summarize

SET = Path(__file__).resolve().parent.parent / "evals" / "minutes.json"
DEFAULT_MODEL = "claude-sonnet-5"
# Dollars per million tokens, as the towns' [summaries] give them for the default model.
DEFAULT_PRICES = {"input_price": 2.0, "output_price": 10.0}
# The models' list prices, dollars per million tokens, for a run that doesn't give them. Claude Haiku
# 5.5 is priced higher for a request whose prompt is over 100,000 tokens (summarize.cost()): a long
# document's summary, whose every page is read as text and as an image, can be.
PRICES = {
    "claude-sonnet-5": DEFAULT_PRICES,
    "claude-sonnet-5-5": {"input_price": 2.0, "output_price": 10.0},
    "claude-haiku-5-5": {"input_price": 0.1, "output_price": 0.5,
                         "long_prompt_tokens": 100_000, "long_input_price": 0.5, "long_output_price": 2.5},
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# A decision written down as denied is wrong given any of these; anything else is wrong given denied.
NOT_DENIED = {"approved", "tabled", "continued", "referred", "recommended"}


def compare(expected: dict, decisions: list[str], evidence: list[dict]) -> dict:
    """One decision written down by hand against the model's: the entries (counting from 1) that
    match it, their outcomes, and whether it's right, wrong, or missing."""
    outcomes = expected["outcome"] if isinstance(expected["outcome"], list) else [expected["outcome"]]
    entries = [i + 1 for i, text in enumerate(decisions)
               if re.search(expected["match"], text, re.I)
               and not (expected.get("unless") and re.search(expected["unless"], text, re.I))]
    given = [(evidence[i - 1] or {}).get("outcome") for i in entries]
    wrong = NOT_DENIED if "denied" in outcomes else {"denied"}
    if not entries:
        status = "missing"
    elif any(o in outcomes for o in given) and not any(o in wrong for o in given):
        status = "right"
    else:
        status = "wrong"
    return {"match": expected["match"], "expected": outcomes, "entries": entries, "given": given, "status": status}


def held(fact_check: dict) -> dict[int, list[dict]]:
    """The decisions the site doesn't show, by entry, with why: in a summary that failed, each with a
    problem other than a vote count (factcheck.shown())."""
    out: dict[int, list[dict]] = {}
    if fact_check["result"] != "failed":
        return out
    for p in fact_check["problems"]:
        if p["field"] == "decisions" and p["kind"] != "tally":
            out.setdefault(p["entry"], []).append(p)
    return out


def turned_round(text: str, outcome: str) -> tuple[str, str] | None:
    """A decision with its outcome turned round, as a model that dropped or added a "not" would
    write it (None for an outcome that has no opposite)."""
    if outcome == "approved":
        return re.sub(r"^\w+", "Denied", text, count=1), "denied"
    if outcome == "denied":
        flipped = re.sub(r"\b(?:failed|denied|rejected|defeated)\b", "approved", text, count=1, flags=re.I)
        flipped = re.sub(r"\b(?:did not|voted not to)\s+", "", flipped, count=1, flags=re.I)
        return ("Approved " + flipped[0].lower() + flipped[1:] if flipped == text else flipped), "approved"
    return None


def planted(record: dict, pages: list[str] | None, passed: list[int]) -> tuple[int, int]:
    """Each decision that passed, turned round, checked again on its own: how many were planted,
    and how many the check caught."""
    count = caught = 0
    for entry in passed:
        evidence = record["decision_evidence"][entry - 1]
        flipped = turned_round(record["decisions"][entry - 1], evidence["outcome"])
        if not flipped:
            continue
        text, outcome = flipped
        one = {**record, "headline": "", "summary": "", "decisions": [text],
               "decision_evidence": [{**evidence, "outcome": outcome}]}
        count += 1
        caught += any(p["kind"] == "outcome" for p in factcheck.check(one, "minutes", pages)["problems"])
    return count, caught


def prices_for(model: str, input_price: float | None = None, output_price: float | None = None) -> dict:
    """The model's prices: those given, else its list prices (PRICES), with its long-prompt prices if it has
    them."""
    listed = PRICES.get(model, {})
    prices = {**listed, **{k: v for k, v in (("input_price", input_price), ("output_price", output_price))
                           if v is not None}}
    if "input_price" not in prices or "output_price" not in prices:
        raise SystemExit(f"No list prices for {model}: give --input-price and --output-price")
    return prices


class Recorder:
    """A client that records each request it sends: its model, effort, tokens, why it stopped, how many
    seconds it took, and what it was for (purpose: a function of the request's parameters). Everything
    else goes to the client."""

    def __init__(self, client, clock=time.monotonic, purpose=None):
        self.requests: list[dict] = []
        self.messages = _Messages(client.messages, self.requests, clock, purpose)


class _Messages:
    def __init__(self, messages, requests: list[dict], clock, purpose):
        self._messages, self._requests, self._clock, self._purpose = messages, requests, clock, purpose

    def __getattr__(self, name):
        return getattr(self._messages, name)

    def _record(self, kwargs: dict, response, started: float) -> None:
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            if getattr(response.usage, key, None):
                usage[key] = getattr(response.usage, key)
        self._requests.append({"model": kwargs.get("model"), "effort": (kwargs.get("output_config") or {}).get("effort"),
                               "usage": usage, "stop_reason": response.stop_reason,
                               "seconds": round(self._clock() - started, 1),
                               "purpose": self._purpose(kwargs) if self._purpose else None})

    def create(self, **kwargs):
        started = self._clock()
        response = self._messages.create(**kwargs)
        self._record(kwargs, response, started)
        return response

    def stream(self, **kwargs):
        outer, started = self, self._clock()

        class Stream:
            def __enter__(self):
                self.inner = outer._messages.stream(**kwargs)
                self.stream = self.inner.__enter__()
                return self

            def __exit__(self, *exc):
                return self.inner.__exit__(*exc)

            def get_final_message(self):
                response = self.stream.get_final_message()
                outer._record(kwargs, response, started)
                return response

        return Stream()


def spent(requests: list[dict], prices: dict) -> dict:
    """Requests' tokens, seconds, and cost, each request priced on its own (a long prompt's price is the
    request's); "long_prompts" counts those priced so."""
    out = {"requests": len(requests), "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
           "cache_creation_input_tokens": 0, "seconds": 0.0, "cost": 0.0, "long_prompts": 0}
    for r in requests:
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            out[key] += r["usage"].get(key, 0)
        out["seconds"] = round(out["seconds"] + r["seconds"], 1)
        out["cost"] += summarize.cost(r["usage"], prices)
        prompt = r["usage"]["input_tokens"] + r["usage"].get("cache_read_input_tokens", 0) + r["usage"].get(
            "cache_creation_input_tokens", 0)
        out["long_prompts"] += bool(prices.get("long_prompt_tokens") and prompt > prices["long_prompt_tokens"])
    out["cost"] = round(out["cost"], 6)
    return out


def document_pdf(document: dict, get=None) -> bytes:
    """The document of the set, as it was checked by hand."""
    pdf = get(document["url"]) if get else fetch(document["url"])
    if hashlib.sha256(pdf).hexdigest() != document["sha256"]:
        raise SystemExit(f"{document['name']}: the document at {document['url']} isn't the one checked by hand")
    return pdf


def stopped(document: dict, e: summarize.StoppedEarly, prices: dict) -> dict:
    """A summary cut off: paid for, with nothing to check, reported with what it had written."""
    return {"name": document["name"], "stopped": str(e), "cost": round(summarize.cost(e.usage, prices), 4),
            "usage": e.usage, "partial": e.text}


def evaluate(document: dict, client, model: str, prices: dict, get=None, effort: str | None = None) -> dict:
    """One document of the set, summarized (and transcribed, if it's a scan) and checked, with its pages and
    what its requests used and cost ("spent": each request priced on its own)."""
    pdf = document_pdf(document, get)
    recorder = Recorder(client)
    extra = {"pages": summarize.page_count(pdf)}
    try:
        result, usage = summarize.summarize_pdf(recorder, model, "minutes", pdf, document["title"], document["date"],
                                                effort)
    except summarize.StoppedEarly as e:
        # The set goes on.
        out = {**stopped(document, e, prices), **extra}
        used = spent(recorder.requests, prices)
        return {**out, "cost": round(used["cost"], 4), "spent": used} if used["requests"] else out
    transcript = None
    extra["summary"] = spent(recorder.requests, prices)
    if document.get("scan"):
        try:
            transcript, _ = summarize.transcribe_pdf(recorder, model, "minutes", pdf, document["title"],
                                                     document["date"], effort)
        except summarize.StoppedEarly as e:
            # Checked without a transcription: unchecked, as the site would leave it.
            extra["transcript_stopped"] = str(e)
        extra["transcript"] = spent(recorder.requests[extra["summary"]["requests"]:], prices)
    used = spent(recorder.requests, prices)
    return {**judge(document, pdf, result, used["cost"], transcript), **extra, "spent": used}


def judge(document: dict, pdf: bytes, result: dict, paid: float, transcript: str | None = None) -> dict:
    """The model's summary of a document of the set, checked as the site checks it and against the
    decisions written down by hand. A scan is checked against its transcript, when there is one."""
    record = summarize.split_decisions(result)
    pages = factcheck.pages(pdf)
    if transcript is not None:
        record["transcript"], record["transcript_source"] = transcript, "ai"
    record["fact_check"] = factcheck.check(record, "minutes", pages)
    held_back = held(record["fact_check"])
    rows = [compare(e, record["decisions"], record.get("decision_evidence", [])) for e in document["decisions"]]
    for row in rows:
        row["held_back"] = [i for i in row["entries"] if i in held_back]
    passed = [i + 1 for i in range(len(record["decisions"])) if i + 1 not in held_back]
    count, caught = planted(record, pages, passed)
    return {"name": document["name"], "source": record["fact_check"]["source"], "cost": round(paid, 4),
            "decisions": record["decisions"], "evidence": record.get("decision_evidence", []),
            "problems": record["fact_check"]["problems"], "held_back": sorted(held_back), "expected": rows,
            "planted": count, "caught": caught}


def fetch(url: str) -> bytes:
    import requests
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def report(results: list[dict], run: dict | None = None) -> tuple[str, bool]:
    """The report, and whether a wrong outcome would be shown. run: the model, effort, and prices it
    was run with (main()), for its first lines."""
    lines, shown_wrong = ["# Minutes prompt against decisions checked by hand", ""], False
    totals = {"decisions": 0, "held": 0, "right": 0, "wrong": 0, "missing": 0, "right_held": 0, "wrong_caught": 0,
              "planted": 0, "caught": 0, "away": 0, "stopped": 0, "cost": 0.0}
    for r in results:
        if "stopped" in r:
            # A summary cut off is one the site would never get: it counts as wrong.
            totals["stopped"] += 1
            totals["cost"] += r["cost"]
            shown_wrong = True
            lines.append(f"## {r['name']}: **{r['stopped']}** after {r['usage']['output_tokens']} tokens (${r['cost']:.3f})")
            lines.append(f"It had written: `{r['partial'][-600:]}`")
            if r.get("spent"):
                lines.append(used_text(r))
            lines.append("")
            continue
        totals["decisions"] += len(r["decisions"])
        totals["held"] += len(r["held_back"])
        totals["planted"] += r["planted"]
        totals["caught"] += r["caught"]
        totals["cost"] += r["cost"]
        totals["away"] += sum(p["kind"] == "away" for p in r["problems"])
        lines.append(f"## {r['name']} ({r['source']}, ${r['cost']:.3f})")
        lines.append(f"{len(r['decisions'])} decisions, {len(r['held_back'])} held back; "
                     f"planted errors caught {r['caught']} of {r['planted']}.")
        if r.get("spent"):
            lines.append(used_text(r))
        if r.get("transcript_stopped"):
            lines.append(f"Its transcription **{r['transcript_stopped']}**, so it wasn't checked.")
        for row in r["expected"]:
            totals[row["status"]] += 1
            note = ""
            if row["status"] == "right" and row["held_back"]:
                totals["right_held"] += 1
                note = " — right, but held back"
            if row["status"] == "wrong":
                caught = all(i in r["held_back"] for i in row["entries"])
                totals["wrong_caught"] += caught
                shown_wrong |= not caught and r["source"] == "pdf"
                note = " — caught" if caught else " — **shown**"
            lines.append(f"- `{row['match']}`: {row['status']} (expected {'/'.join(row['expected'])}, "
                         f"given {', '.join(map(str, row['given'])) or 'none'}){note}")
        for p in r["problems"]:
            if p["field"] == "decisions" and p["kind"] != "tally":
                text = r["decisions"][p["entry"] - 1]
                lines.append(f"  - decision {p['entry']} ({p['kind']}: {p['what'][:120]}): {text[:140]}")
        lines.append("")
    lines[2:2] = [
        *([run_text(run), ""] if run else []),
        f"- Decisions: {totals['decisions']}; held back by the check: {totals['held']}; numbers or names far from "
        f"their quote (listed): {totals['away']}.",
        f"- Written down by hand: {totals['right']} right, {totals['wrong']} wrong ({totals['wrong_caught']} caught), "
        f"{totals['missing']} missing; right but held back: {totals['right_held']}.",
        f"- Planted errors caught: {totals['caught']} of {totals['planted']}.",
        f"- Documents whose summary was cut off: {totals['stopped']}.",
        f"- Cost: ${totals['cost']:.2f} (${totals['cost'] / max(len(results), 1):.4f} a document).",
        *([tokens_text(results)] if any(r.get("spent") for r in results) else []),
        "",
    ]
    return "\n".join(lines), shown_wrong


def run_text(run: dict) -> str:
    return (f"Model: {run['model']}, effort: {run['effort'] or 'none sent (the model default)'}; prices per million "
            f"tokens: {price_text(run['prices'])}.")


def price_text(prices: dict) -> str:
    text = f"${prices['input_price']:g} input, ${prices['output_price']:g} output"
    if all(prices.get(k) is not None for k in summarize.LONG_PROMPT):
        text += (f" (${prices['long_input_price']:g} and ${prices['long_output_price']:g} for a prompt over "
                 f"{prices['long_prompt_tokens']:,} tokens)")
    return text


def used_text(r: dict) -> str:
    """A document's pages, tokens, seconds, and cost, in a line."""
    s = r["spent"]
    text = (f"{r.get('pages') or '?'} pages; {s['requests']} requests, {s['input_tokens']:,} input and "
            f"{s['output_tokens']:,} output tokens, {s['seconds']:.0f} s, ${s['cost']:.4f}")
    if r.get("transcript"):
        text += f" (the transcription: ${r['transcript']['cost']:.4f})"
    if s["long_prompts"]:
        text += f"; {s['long_prompts']} at the long-prompt price"
    return text + "."


def tokens_text(results: list[dict]) -> str:
    rows = [r["spent"] for r in results if r.get("spent")]
    seconds = sorted(s["seconds"] for s in rows)
    return (f"- Tokens: {sum(s['input_tokens'] for s in rows):,} input, {sum(s['output_tokens'] for s in rows):,} output; "
            f"{sum(s['long_prompts'] for s in rows)} requests at the long-prompt price; seconds a document: median "
            f"{seconds[len(seconds) // 2]:.0f}, longest {seconds[-1]:.0f}.")


def totals_of(results: list[dict]) -> dict:
    """The run's totals for its JSON: documents, decisions and how those written down came out, held back,
    cut off, tokens, and cost."""
    t = {"documents": len(results), "decisions": 0, "held_back": 0, "right": 0, "wrong": 0, "wrong_shown": 0,
         "missing": 0, "stopped": 0, "input_tokens": 0, "output_tokens": 0, "long_prompts": 0, "cost": 0.0}
    for r in results:
        t["cost"] += r["cost"]
        for key in ("input_tokens", "output_tokens", "long_prompts"):
            t[key] += (r.get("spent") or {}).get(key, 0)
        if "stopped" in r:
            t["stopped"] += 1
            continue
        t["decisions"] += len(r["decisions"])
        t["held_back"] += len(r["held_back"])
        for row in r["expected"]:
            t[row["status"]] += 1
            t["wrong_shown"] += (row["status"] == "wrong" and r["source"] == "pdf"
                                 and not all(i in r["held_back"] for i in row["entries"]))
    t["cost"] = round(t["cost"], 4)
    t["cost_per_document"] = round(t["cost"] / max(len(results), 1), 4)
    return t


def main() -> int:
    # The translation prompt's set has its own runner (pipeline/evaluate_translations.py), as do the others.
    if "--set" in sys.argv:
        at = sys.argv.index("--set")
        chosen, rest = sys.argv[at + 1], sys.argv[1:at] + sys.argv[at + 2:]
        if chosen == "translations":
            from pipeline import evaluate_translations
            return evaluate_translations.main(rest)
        if chosen == "batch":
            from pipeline import evaluate_batch
            return evaluate_batch.main(rest)
        if chosen == "shadow":
            from pipeline import evaluate_shadow
            return evaluate_shadow.main(rest)
        if chosen != "minutes":
            raise SystemExit(f"--set is minutes, translations, batch, or shadow, not {chosen}")
        sys.argv[1:] = rest
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="the one document of the set to run (its name)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", choices=EFFORTS, help="for the summaries and the scan's transcription "
                        "(default: none sent, the model's own, as a town's run sends them)")
    parser.add_argument("--input-price", type=float, help="dollars per million tokens (default: the model's in PRICES)")
    parser.add_argument("--output-price", type=float)
    parser.add_argument("--json", type=Path, help="also save every result here")
    args = parser.parse_args()
    prices = prices_for(args.model, args.input_price, args.output_price)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic
    client = anthropic.Anthropic()
    documents = json.loads(SET.read_text(encoding="utf-8"))["documents"]
    if args.only:
        documents = [d for d in documents if d["name"] == args.only]
        if not documents:
            raise SystemExit(f"No document named {args.only} in {SET}")
    results = []
    for document in documents:
        print(f"{document['name']}...", file=sys.stderr, flush=True)
        results.append(evaluate(document, client, args.model, prices, effort=args.effort))
    run = {"set": "minutes", "model": args.model, "effort": args.effort, "prices": prices}
    text, shown_wrong = report(results, run)
    print(text)
    if args.json:
        args.json.write_text(json.dumps({**run, "totals": totals_of(results), "documents": results}, indent=2) + "\n",
                             encoding="utf-8")
    return 1 if shown_wrong else 0


if __name__ == "__main__":
    sys.exit(main())
