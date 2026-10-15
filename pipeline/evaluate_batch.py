"""The minutes prompt sent as a batch (the Message Batches API) at each effort: how long a batch takes,
and what a lower effort does to the summaries.

An experiment, run by hand before deciding whether a town's run sends its summaries as a batch (half
the price, but back only when the batch ends) and at what effort (a run sends none, so the model's
default: high for claude-sonnet-5). Every document of the minutes set (evals/minutes.json) is sent
once at each effort, one batch an effort, all sent at once, about the size of one job's documents on
a daily run. The report gives, for each effort:

- how long its batch took, from when it was sent to when it ended, as the API records them (a batch
  not ended after --wait-minutes is cancelled: that's an answer too);
- the set's results as pipeline/evaluate.py counts them (right, wrong and whether the site would show
  it, missing, cut off), and how many decisions the check held back;
- its output tokens and its cost at the batch price.

A scan isn't transcribed here, so its decisions are compared with the ones written down by hand
without the fact check. Batches vary with the API's load, so run it more than once, at the hour the
daily runs do. Nothing is saved but the report, and it never fails on what it finds.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set batch [--efforts low,medium,high]
                                                      [--wait-minutes 100] [--only NAME] [--json FILE]

It costs about half of a minutes run (pipeline/evaluate.py) for each effort: $1.50 to $3 for three.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from pipeline import evaluate, summarize

# The Message Batches API's price, against a request's own.
BATCH_PRICE = summarize.BATCH_PRICE
EFFORTS = ("low", "medium", "high", "xhigh", "max")
POLL_SECONDS = 15


def requests_for(documents: list[dict], pdfs: list[bytes], model: str, effort: str) -> list[dict]:
    """One request a document, as a run would send its summary, at the effort."""
    return [{"custom_id": f"doc-{i}",
             "params": summarize.summary_request(model, "minutes", pdf, d["title"], d["date"], effort)}
            for i, (d, pdf) in enumerate(zip(documents, pdfs))]


def send_and_wait(client, documents: list[dict], pdfs: list[bytes], model: str, efforts: list[str],
                  wait_seconds: float, clock=time.monotonic, sleep=time.sleep) -> dict[str, dict]:
    """Each effort's batch, sent at once and waited for: its id and, once ended, how many seconds it
    took. One still going after wait_seconds is cancelled, and waited for until it ends (what it
    finished is paid for, and counted), with no seconds."""
    batches = {}
    for effort in efforts:
        batch = client.messages.batches.create(requests=requests_for(documents, pdfs, model, effort))
        batches[effort] = {"id": batch.id, "seconds": None, "cancelled": False}
    deadline = clock() + wait_seconds
    waiting = list(efforts)
    while waiting:
        for effort in list(waiting):
            batch = client.messages.batches.retrieve(batches[effort]["id"])
            if batch.processing_status == "ended":
                if not batches[effort]["cancelled"]:
                    batches[effort]["seconds"] = round((batch.ended_at - batch.created_at).total_seconds())
                waiting.remove(effort)
        if not waiting:
            break
        if clock() >= deadline:
            for effort in waiting:
                if not batches[effort]["cancelled"]:
                    client.messages.batches.cancel(batches[effort]["id"])
                    batches[effort]["cancelled"] = True
        sleep(POLL_SECONDS)
    return batches


def judged(document: dict, pdf: bytes, result, prices: dict) -> dict:
    """One document's result in a batch, checked as pipeline/evaluate.py checks it."""
    if result is None or result.type != "succeeded":
        error = getattr(getattr(result, "error", None), "error", None)
        return {"name": document["name"], "failed": result.type if result else "missing",
                "error": getattr(error, "message", None)}
    try:
        parsed, usage = summarize.summary_result(result.message)
    except summarize.StoppedEarly as e:
        return evaluate.stopped(document, e, prices)
    return {**evaluate.judge(document, pdf, parsed, summarize.cost(usage, prices)),
            "output_tokens": usage["output_tokens"]}


def collect(client, documents: list[dict], pdfs: list[bytes], batches: dict[str, dict], prices: dict) -> list[dict]:
    """Each effort's batch with its documents' results (a cancelled one's unfinished documents "canceled")."""
    at_batch_price = {k: v * BATCH_PRICE for k, v in prices.items()}
    out = []
    for effort, batch in batches.items():
        # Results come in any order: matched to their documents by id.
        by_id = {r.custom_id: r.result for r in client.messages.batches.results(batch["id"])}
        results = [judged(d, pdf, by_id.get(f"doc-{i}"), at_batch_price) for i, (d, pdf) in enumerate(zip(documents, pdfs))]
        out.append({"effort": effort, **batch, "results": results})
    return out


def totals(results: list[dict]) -> dict:
    """The set's counts for one effort, as pipeline/evaluate.py's report gives them."""
    t = {"right": 0, "wrong": 0, "shown": 0, "missing": 0, "held": 0, "stopped": 0, "failed": 0,
         "output_tokens": 0, "cost": 0.0}
    for r in results:
        t["cost"] += r.get("cost", 0.0)
        if "failed" in r:
            t["failed"] += 1
            continue
        if "stopped" in r:
            t["stopped"] += 1
            t["output_tokens"] += r["usage"]["output_tokens"]
            continue
        t["output_tokens"] += r["output_tokens"]
        t["held"] += len(r["held_back"])
        for row in r["expected"]:
            t[row["status"]] += 1
            if row["status"] == "wrong" and r["source"] == "pdf" and not all(i in r["held_back"] for i in row["entries"]):
                t["shown"] += 1
    return t


def minutes_and_seconds(seconds: int | None, waited: float) -> str:
    if seconds is None:
        return f"not ended after {waited / 60:.0f} min (cancelled)"
    return f"{seconds // 60} min {seconds % 60:02d} s"


def report(runs: list[dict], documents: list[dict], model: str, waited: float) -> str:
    lines = [f"# The minutes prompt as a batch, by effort ({model})", "",
             f"Each effort's batch had the set's {len(documents)} documents, all sent at once. A run today sends "
             "each document on its own, at the model's default effort (high), at twice the batch price.", "",
             "| Effort | Batch took | Right | Wrong (shown) | Missing | Held back | Cut off | Failed | Output tokens "
             "| Cost at batch price |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        t = totals(run["results"])
        lines.append(f"| {run['effort']} | {minutes_and_seconds(run['seconds'], waited)} | {t['right']} | "
                     f"{t['wrong']} ({t['shown']}) | {t['missing']} | {t['held']} | {t['stopped']} | {t['failed']} | "
                     f"{t['output_tokens']:,} | ${t['cost']:.2f} |")
    lines += ["", "Wrong (shown): a decision with the wrong outcome, and how many of those the site would show "
              "(not held back by the check). A scan isn't transcribed here, so its decisions aren't checked.", ""]
    for run in runs:
        lines.append(f"# Effort: {run['effort']}")
        failed = [r for r in run["results"] if "failed" in r]
        for r in failed:
            lines.append(f"- {r['name']}: {r['failed']}{': ' + r['error'] if r.get('error') else ''}")
        checked = [r for r in run["results"] if "failed" not in r]
        if checked:
            text, _ = evaluate.report(checked)
            # Its headings one level down, under the effort's.
            lines += ["#" + line if line.startswith("#") else line for line in text.splitlines()]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--efforts", default="low,medium,high", help="comma-separated: " + ", ".join(EFFORTS))
    parser.add_argument("--wait-minutes", type=float, default=100, help="cancel a batch not ended by then")
    parser.add_argument("--only", help="the one document of the set to run (its name)")
    parser.add_argument("--model", default=evaluate.DEFAULT_MODEL)
    parser.add_argument("--input-price", type=float, default=evaluate.DEFAULT_PRICES["input_price"])
    parser.add_argument("--output-price", type=float, default=evaluate.DEFAULT_PRICES["output_price"])
    parser.add_argument("--json", type=Path, help="also save every result here")
    args = parser.parse_args(argv)
    efforts = [e.strip() for e in args.efforts.split(",") if e.strip()]
    if not efforts or set(efforts) - set(EFFORTS) or len(set(efforts)) != len(efforts):
        raise SystemExit(f"--efforts takes each of {', '.join(EFFORTS)} at most once, not {args.efforts}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic
    client = anthropic.Anthropic()
    documents = json.loads(evaluate.SET.read_text(encoding="utf-8"))["documents"]
    if args.only:
        documents = [d for d in documents if d["name"] == args.only]
        if not documents:
            raise SystemExit(f"No document named {args.only} in {evaluate.SET}")
    pdfs = [evaluate.document_pdf(d) for d in documents]
    prices = {"input_price": args.input_price, "output_price": args.output_price}
    print(f"Sending {len(efforts)} batches of {len(documents)} documents...", file=sys.stderr, flush=True)
    batches = send_and_wait(client, documents, pdfs, args.model, efforts, args.wait_minutes * 60)
    runs = collect(client, documents, pdfs, batches, prices)
    print(report(runs, documents, args.model, args.wait_minutes * 60))
    if args.json:
        args.json.write_text(json.dumps(runs, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
