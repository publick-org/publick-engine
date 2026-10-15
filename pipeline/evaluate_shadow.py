"""Real summaries made again with a model, and checked as the site checks them, beside the ones published.

The fact check replay's sample (evals/factcheck/sample.json.gz, pipeline/evaluate_factcheck.py) holds
summaries of agendas and minutes from every town, as the network published them, with their
documents' text. Each of its documents is downloaded (its sha256 checked), summarized again with the
model and the current prompt, and checked as a town's run checks a new summary (factcheck.check(),
then factcheck.shown(): what the site would show), with no answers written down by hand: the fact
check is the grader. A scan is checked against the transcription the network already has, so both
summaries are checked against the same text (one with none stays unchecked). The published summary
is checked again with the same check, so the two are compared alike; its result as the network saved
it is in the JSON too.

The report gives, for each document and in all, for the summary made again and the published one:
the result (ok, weak, failed, unchecked; or cut off, refused), its decisions or agenda items, how
many of them the check hides, its problems by kind, its tokens and cost (each request priced on its
own, a long prompt at its own price; the published one at its model's list price, and what was paid,
in the JSON), and how many seconds the request took. The JSON (--json) also has both summaries'
headline, summary, and decisions or items side by side, to read. Nothing is saved but the report,
and it fails (exit status 1) only when a request couldn't be made.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set shadow [--model MODEL] [--effort EFFORT]
                                                       [--only "TOWN SHA256[:12]"] [--json FILE]

The published summaries were made with prompts of several versions (the sample keeps the version
each was made with), so a difference can be the prompt's as well as the model's: a run with the
network's own model (claude-sonnet-5) is the baseline.
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

from pipeline import evaluate, evaluate_factcheck, summarize, translate

DEFAULT_MODEL = evaluate.DEFAULT_MODEL
# Where a problem's kind is in its line (evaluate_factcheck.problem()): "decisions 2: outcome ...".
KIND = re.compile(r"^[^:]+: (\S+)")


def entries_field(kind: str) -> str:
    return "items" if kind == "agenda" else "decisions"


def checked(item: dict, record: dict, words: frozenset) -> dict:
    """A summary of the item's document checked as the site checks it, and what it would show: its
    result, how many of its entries are hidden, and its problems by kind."""
    field = entries_field(item["kind"])
    entries = len(record.get(field) or [])
    texts = {"headline": record.get("headline", ""), "summary": record.get("summary", ""), field: record.get(field) or []}
    if record.get("is_minutes") is False:
        # Not minutes (an agenda filed under minutes): the site doesn't check it, or show it as minutes.
        return {"result": "not minutes", "source": None, "entries": entries, "hidden_entries": 0, "hidden": [],
                "problems_by_kind": {}, "problems": [], **texts}
    replayed = evaluate_factcheck.replay({**item, "record": record}, words)
    return {"result": replayed["result"], "source": replayed["source"], "entries": entries,
            "hidden_entries": sum(h.startswith(field + " ") for h in replayed["hidden"]), "hidden": replayed["hidden"],
            "problems_by_kind": dict(Counter(KIND.match(p).group(1) for p in replayed["problems"])),
            "problems": replayed["problems"], **texts}


def published(item: dict, words: frozenset) -> dict:
    """The network's summary, checked again, with its tokens and its cost at its model's list price
    ("cost") and as paid ("paid": at the batch price, if it was made in one; None if not recorded)."""
    record = item["record"]
    out = {"model": record.get("model"), "prompt_version": record.get("prompt_version"),
           "saved_result": (item.get("network_fact_check") or {}).get("result"), **checked(item, record, words)}
    usage = record.get("usage")
    if usage:
        out["usage"] = usage
        try:
            out["cost"] = round(summarize.cost(usage, evaluate.prices_for(record.get("model") or "")), 6)
        except SystemExit:
            out["cost"] = None
        out["paid"] = record.get("cost")
    return out


def remade(item: dict, pdf: bytes, client, model: str, effort: str | None, prices: dict, words: frozenset) -> dict:
    """The document summarized again with the model, as a town's run sends it, and checked."""
    recorder = evaluate.Recorder(client)
    out = {"model": model, "effort": effort}
    try:
        result, _ = summarize.summarize_pdf(recorder, model, item["kind"], pdf, item.get("title") or item["body"],
                                            item["date"], effort)
    except summarize.StoppedEarly as e:
        out.update(result=e.args[0].removeprefix("stopped early: ").split(" ")[0], stopped=str(e), partial=e.text[-600:],
                   entries=0, hidden_entries=0, problems_by_kind={})
        result = None
    except Exception as e:  # the request couldn't be made: reported, and the run fails
        if "credit balance" in str(e).lower():
            raise
        out.update(result="error", error=str(e), entries=0, hidden_entries=0, problems_by_kind={})
        result = None
    used = evaluate.spent(recorder.requests, prices)
    out.update(usage={k: used[k] for k in ("input_tokens", "output_tokens")}, cost=used["cost"], seconds=used["seconds"],
               long_prompt=bool(used["long_prompts"]))
    if result is None:
        return out
    record = {**summarize.split_decisions(result), "kind": item["kind"]}
    if item["record"].get("transcript_source") == "ai" and item["record"].get("transcript"):
        # A scan: checked against the network's transcription, as the published summary is.
        record.update(transcript=item["record"]["transcript"], transcript_source="ai")
    # As a town's run checks a new summary: its own lowercase words are no one's name (summarize.save_summary).
    return {**out, **checked(item, record, words | translate.summary_words(record))}


def run(sample: dict, client, model: str, effort: str | None, prices: dict, only: str | None = None,
        get=evaluate_factcheck.download) -> list[dict]:
    words = frozenset(sample["words"])
    out = []
    for item in sample["items"]:
        key = evaluate_factcheck.key(item)
        if only and not f"{item['town']} {item['sha256']}".startswith(only):
            continue
        print(f"{key} {item['kind']}...", file=sys.stderr, flush=True)
        pdf = evaluate_factcheck.document(item, get)
        out.append({"key": key, "town": item["town"], "kind": item["kind"], "body": item["body"],
                    "title": item.get("title"), "date": item["date"], "pages": summarize.page_count(pdf),
                    "new": remade(item, pdf, client, model, effort, prices, words), "published": published(item, words)})
    return out


RESULTS = ("ok", "weak", "failed", "unchecked", "not minutes", "max_tokens", "refusal", "error")


def side_totals(docs: list[dict], side: str) -> dict:
    rows = [d[side] for d in docs]
    seconds = [r["seconds"] for r in rows if r.get("seconds") is not None]
    problems = Counter()
    for r in rows:
        problems.update(r.get("problems_by_kind") or {})
    costs = [r.get("cost") for r in rows]
    return {"documents": len(rows), "results": dict(Counter(r["result"] for r in rows)),
            "entries": sum(r.get("entries", 0) for r in rows), "hidden_entries": sum(r.get("hidden_entries", 0) for r in rows),
            "headlines_hidden": sum("headline" in (r.get("hidden") or []) for r in rows),
            "problems_by_kind": dict(sorted(problems.items())),
            "input_tokens": sum((r.get("usage") or {}).get("input_tokens", 0) for r in rows),
            "output_tokens": sum((r.get("usage") or {}).get("output_tokens", 0) for r in rows),
            "cost": round(sum(c for c in costs if c is not None), 4), "cost_missing": sum(c is None for c in costs),
            "long_prompts": sum(bool(r.get("long_prompt")) for r in rows),
            "seconds_median": statistics.median(seconds) if seconds else None,
            "seconds_longest": max(seconds) if seconds else None}


def totals(docs: list[dict]) -> dict:
    """Both sides' totals, for all the documents and for each kind."""
    groups = {"all": docs, **{kind: [d for d in docs if d["kind"] == kind] for kind in ("agenda", "minutes")}}
    return {name: {side: side_totals(group, side) for side in ("new", "published")} for name, group in groups.items()}


def results_text(results: dict) -> str:
    return ", ".join(f"{name} {results[name]}" for name in RESULTS if results.get(name)) or "none"


def problems_text(problems: dict) -> str:
    return ", ".join(f"{k} {n}" for k, n in problems.items()) or "none"


def report(docs: list[dict], run_info: dict) -> str:
    t = totals(docs)
    lines = [f"# Summaries made again with {run_info['model']}, checked as the site checks them", "",
             f"Effort: {run_info['effort'] or 'none sent (the model default)'}; prices per million tokens: "
             f"{evaluate.price_text(run_info['prices'])}. The published summaries are the network's (their model and "
             "prompt version are in the table), checked again with the same check; each summary made again is sent "
             "on its own, at the full price.", ""]
    for name, label in (("all", "All"), ("agenda", "Agendas"), ("minutes", "Minutes")):
        new, pub = t[name]["new"], t[name]["published"]
        if not new["documents"]:
            continue
        lines += [f"## {label}: {new['documents']} documents", "",
                  "| | Made again | Published |", "|---|---|---|",
                  f"| Results | {results_text(new['results'])} | {results_text(pub['results'])} |",
                  f"| Decisions and items | {new['entries']} | {pub['entries']} |",
                  f"| Hidden by the check | {new['hidden_entries']} (and {new['headlines_hidden']} headlines) | "
                  f"{pub['hidden_entries']} (and {pub['headlines_hidden']} headlines) |",
                  f"| Problems by kind | {problems_text(new['problems_by_kind'])} | {problems_text(pub['problems_by_kind'])} |",
                  f"| Tokens, input / output | {new['input_tokens']:,} / {new['output_tokens']:,} | "
                  f"{pub['input_tokens']:,} / {pub['output_tokens']:,} |",
                  f"| Cost (list price) | ${new['cost']:.2f} (${new['cost'] / max(new['documents'], 1):.4f} a document"
                  + (f"; {new['long_prompts']} at the long-prompt price" if new["long_prompts"] else "") + ") | "
                  f"${pub['cost']:.2f}" + (f" ({pub['cost_missing']} not recorded)" if pub["cost_missing"] else "") + " |",
                  f"| Seconds a document, median / longest | {seconds_text(new)} | not recorded |", ""]
    lines += ["## Each document", "",
              "Result, entries, hidden, and problems: made again / published. Published: its model and prompt version.", "",
              "| Document | Kind | Pages | Published | Result | Entries | Hidden | Problems | Tokens in / out | Cost | Seconds |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in docs:
        new, pub = d["new"], d["published"]
        cost = f"${new['cost']:.4f} / " + (f"${pub['cost']:.4f}" if pub.get("cost") is not None else "–")
        lines.append(f"| {d['key']} {d['body'][:40]}, {d['date']} | {d['kind']} | {d['pages'] or '?'} | "
                     f"{pub['model']} v{pub['prompt_version']} | {new['result']} / {pub['result']} | "
                     f"{new.get('entries', 0)} / {pub['entries']} | {new.get('hidden_entries', 0)} / {pub['hidden_entries']} | "
                     f"{problems_text(new.get('problems_by_kind') or {})} / {problems_text(pub['problems_by_kind'])} | "
                     f"{new['usage']['input_tokens']:,} / {new['usage']['output_tokens']:,} | {cost} | {new['seconds']:.0f} |")
    stopped = [d for d in docs if d["new"].get("stopped") or d["new"].get("error")]
    if stopped:
        lines += ["", "## Cut off, refused, or not made", ""]
        lines += [f"- {d['key']} ({d['kind']}): {d['new'].get('stopped') or d['new'].get('error')}" for d in stopped]
    lines += ["", "## Headlines, made again and published", ""]
    for d in docs:
        lines += [f"- {d['key']} ({d['kind']}, {d['body'][:40]}, {d['date']})",
                  f"  - made again: {d['new'].get('headline', '–')}", f"  - published: {d['published']['headline']}"]
    return "\n".join(lines) + "\n"


def seconds_text(side: dict) -> str:
    if side["seconds_median"] is None:
        return "–"
    return f"{side['seconds_median']:.0f} / {side['seconds_longest']:.0f}"


def main(argv: list[str] | None = None) -> int:
    import json
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", choices=evaluate.EFFORTS, help="default: none sent (the model's own), as a run sends it")
    parser.add_argument("--input-price", type=float, help="dollars per million tokens (default: the model's in "
                        "pipeline/evaluate.py's PRICES)")
    parser.add_argument("--output-price", type=float)
    parser.add_argument("--only", help="one document: its town and the start of its sha256, as the report gives them")
    parser.add_argument("--json", type=Path, help="also save every result here")
    args = parser.parse_args(argv)
    prices = evaluate.prices_for(args.model, args.input_price, args.output_price)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic
    sample = evaluate_factcheck.load()
    if args.only and not any(f"{i['town']} {i['sha256']}".startswith(args.only) for i in sample["items"]):
        raise SystemExit(f"No document {args.only} in {evaluate_factcheck.SAMPLE}")
    docs = run(sample, anthropic.Anthropic(), args.model, args.effort, prices, args.only)
    info = {"set": "shadow", "model": args.model, "effort": args.effort, "prices": prices}
    print(report(docs, info))
    if args.json:
        args.json.write_text(json.dumps({**info, "totals": totals(docs), "documents": docs}, indent=2,
                                        ensure_ascii=False) + "\n", encoding="utf-8")
    return 1 if any(d["new"].get("error") for d in docs) else 0


if __name__ == "__main__":
    sys.exit(main())
