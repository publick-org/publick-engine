"""The agenda prompt run against the real model, on agendas whose items' actions were read by hand, before a
change to the prompt or to how an item's action is checked ships (the minutes prompt's is pipeline/evaluate.py).

Each agenda in evals/agendas.json is downloaded (and its sha256 checked), summarized by the model with the prompt
as it is now, transcribed too if it's a scan, and checked as the site checks it (pipeline/factcheck.py). Each item
written down by hand is found among the summary's items (the first whose text its pattern finds), and the report
says, for the whole set and each agenda:

- each item written down: right (the model gave one of the actions written down for it), wrong (it gave another),
  or missing (no item found); for a right one, whether the check took its action away (its words not found in the
  agenda), and for a wrong one, whether the check caught it;
- of all the summary's items, how many the model gave an action, and how many of those the check took away;
- whether the meeting page would group the items (pipeline/build_site.py agenda_groups()), and in which groups;
- which model ran, at what effort, and for each agenda its pages, tokens, seconds, and cost.

A wrong action the page would show (the item in a group it doesn't belong in) fails the run (exit status 1), and so
does a summary cut off at the prompt's max_tokens. "unstated" is never wrong for the page: the item is shown with the
items the agenda says nothing of, or in the list of all of them.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set agendas [--only NAME] [--model MODEL] [--effort EFFORT] [--json FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from pipeline import build_site, factcheck, summarize
from pipeline import evaluate as minutes_set

SET = Path(__file__).resolve().parent.parent / "evals" / "agendas.json"


def found(expected: dict, items: list[str]) -> int | None:
    """The index of the first item the expected item's pattern finds (and its unless doesn't), or None."""
    for i, text in enumerate(items):
        if re.search(expected["match"], text) and not (expected.get("unless") and re.search(expected["unless"], text)):
            return i
    return None


def judge(document: dict, result: dict, paid: float, transcript: str | None, pages: list[str] | None) -> dict:
    """The model's summary of an agenda of the set, checked as the site checks it, against the actions written down."""
    record = summarize.split_items(result)
    if transcript is not None:
        record["transcript"], record["transcript_source"] = transcript, "ai"
    record["fact_check"] = factcheck.check(record, "agenda", pages)
    items, actions = record.get("items") or [], record.get("item_actions") or []
    taken = {p["entry"] - 1 for p in record["fact_check"]["problems"] if p["field"] == "item_actions"}
    shown = factcheck.shown(record, record, "agenda")
    # What the page shows for each item: its action, "unstated" where the check took it away, None for an item
    # held back, or every item where the agenda had no text to check against.
    hidden = {p["entry"] - 1 for p in record["fact_check"]["problems"]
              if p["field"] == "items" and factcheck.hides(record["fact_check"], p)}
    on_page = [None if i in hidden or "item_actions" not in shown else ("unstated" if i in taken else a)
               for i, a in enumerate(actions)]
    rows = []
    for expected in document["items"]:
        i = found(expected, items)
        row = {"match": expected["match"], "expected": expected["action"], "entry": None if i is None else i + 1}
        if i is None:
            rows.append({**row, "status": "missing"})
            continue
        given = actions[i] if i < len(actions) else None
        status = "right" if given in expected["action"] else "wrong"
        row.update(status=status, given=given, item=items[i], words=(record.get("item_action_words") or [""] * len(items))[i],
                   on_page=on_page[i] if i < len(on_page) else None)
        # A wrong action is caught where the page doesn't show it: taken away by the check, or "unstated" given.
        row["caught"] = status == "wrong" and row["on_page"] in (None, "unstated")
        row["taken_away"] = status == "right" and given != "unstated" and row["on_page"] in (None, "unstated")
        rows.append(row)
    groups = build_site.agenda_groups(shown)
    return {"name": document["name"], "source": record["fact_check"]["source"], "cost": round(paid, 4),
            "items": items, "actions": actions, "action_words": record.get("item_action_words") or [],
            "given": sum(a != "unstated" for a in actions), "taken_away": len(taken),
            "groups": [[g, len(entries)] for g, entries in groups] if groups else None,
            "problems": [p for p in record["fact_check"]["problems"] if p["field"] == "item_actions"], "expected": rows}


def evaluate(document: dict, client, model: str, prices: dict, get=None, effort: str | None = None) -> dict:
    """One agenda of the set, summarized (and transcribed, if it's a scan) and checked."""
    pdf = minutes_set.document_pdf(document, get)
    recorder = minutes_set.Recorder(client)
    extra = {"pages": summarize.page_count(pdf)}
    try:
        result, _ = summarize.summarize_pdf(recorder, model, "agenda", pdf, document["title"], document["date"], effort)
    except summarize.StoppedEarly as e:
        out = {**minutes_set.stopped(document, e, prices), **extra}
        used = minutes_set.spent(recorder.requests, prices)
        return {**out, "cost": round(used["cost"], 4), "spent": used} if used["requests"] else out
    transcript = None
    extra["summary"] = minutes_set.spent(recorder.requests, prices)
    if document.get("scan"):
        try:
            transcript, _ = summarize.transcribe_pdf(recorder, model, "agenda", pdf, document["title"], document["date"],
                                                     effort)
        except summarize.StoppedEarly as e:
            # Checked without a transcription: no item's action is shown, as on the site.
            extra["transcript_stopped"] = str(e)
        extra["transcript"] = minutes_set.spent(recorder.requests[extra["summary"]["requests"]:], prices)
    used = minutes_set.spent(recorder.requests, prices)
    return {**judge(document, result, used["cost"], transcript, factcheck.pages(pdf)), **extra, "spent": used}


def totals_of(results: list[dict]) -> dict:
    out = {"agendas": len(results), "stopped": 0, "items": 0, "given": 0, "taken_away": 0, "right": 0, "wrong": 0,
           "missing": 0, "wrong_caught": 0, "wrong_shown": 0, "right_taken_away": 0, "grouped": 0, "cost": 0.0}
    for r in results:
        out["cost"] += r.get("cost", 0.0)
        if "stopped" in r:
            out["stopped"] += 1
            continue
        out["items"] += len(r["items"])
        out["given"] += r["given"]
        out["taken_away"] += r["taken_away"]
        out["grouped"] += bool(r["groups"])
        for row in r["expected"]:
            out[row["status"]] += 1
            out["wrong_caught"] += bool(row.get("caught"))
            out["wrong_shown"] += row["status"] == "wrong" and not row.get("caught")
            out["right_taken_away"] += bool(row.get("taken_away"))
    out["cost"] = round(out["cost"], 4)
    return out


def report(results: list[dict], run: dict | None = None) -> tuple[str, bool]:
    """The report, and whether it fails: a wrong action the page would show, or a summary cut off."""
    money = minutes_set.money
    lines = ["# Agenda prompt against item actions read by hand", ""]
    for r in results:
        if "stopped" in r:
            lines.append(f"## {r['name']}: **{r['stopped']}** after {r['usage']['output_tokens']} tokens ({money(r['cost'], 3)})")
            lines += [f"It had written: `{r['partial'][-600:]}`", ""]
            continue
        lines.append(f"## {r['name']} ({r['source']}, {money(r['cost'], 3)})")
        groups = ", ".join(f"{g} {n}" for g, n in r["groups"]) if r["groups"] else "not grouped"
        lines.append(f"{len(r['items'])} items, {r['given']} given an action, {r['taken_away']} taken away by the check; "
                     f"the page: {groups}.")
        if r.get("spent"):
            lines.append(minutes_set.used_text(r))
        if r.get("transcript_stopped"):
            lines.append(f"Its transcription **{r['transcript_stopped']}**, so no item's action would be shown.")
        for row in r["expected"]:
            note = ""
            if row["status"] == "wrong":
                note = " — caught" if row["caught"] else " — **shown**"
            elif row.get("taken_away"):
                note = " — right, but taken away"
            given = f", given {row['given']} (\"{row['words']}\")" if row["status"] != "missing" else ""
            lines.append(f"- `{row['match']}`: {row['status']} (expected {'/'.join(row['expected'])}{given}){note}")
        for p in r["problems"]:
            lines.append(f"  - item {p['entry']}, action taken away ({p['what'][:120]}): {r['items'][p['entry'] - 1][:120]}")
        lines.append("")
    t = totals_of(results)
    lines[2:2] = [
        *([minutes_set.run_text(run), ""] if run else []),
        f"- Items: {t['items']}; given an action: {t['given']}; taken away by the check: {t['taken_away']}.",
        f"- Written down by hand: {t['right']} right, {t['wrong']} wrong ({t['wrong_caught']} caught, {t['wrong_shown']} "
        f"shown), {t['missing']} missing; right but taken away: {t['right_taken_away']}.",
        f"- Agendas the page would group: {t['grouped']} of {t['agendas'] - t['stopped']}.",
        f"- Agendas whose summary was cut off: {t['stopped']}.",
        f"- Cost: {money(t['cost'])} ({money(t['cost'] / max(len(results), 1), 4)} an agenda).",
        "",
    ]
    return "\n".join(lines), bool(t["wrong_shown"] or t["stopped"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="the one agenda of the set to run (its name)")
    parser.add_argument("--model", default=minutes_set.DEFAULT_MODEL)
    parser.add_argument("--effort", choices=minutes_set.EFFORTS, help="for the summaries and the scan's transcription "
                        "(default: none sent, the model's own, as a town's run sends them)")
    parser.add_argument("--input-price", type=float, help="dollars per million tokens (default: the model's in PRICES)")
    parser.add_argument("--output-price", type=float)
    parser.add_argument("--json", type=Path, help="also save every result here")
    args = parser.parse_args(argv)
    prices = minutes_set.prices_for(args.model, args.input_price, args.output_price)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic
    client = anthropic.Anthropic()
    documents = json.loads(SET.read_text(encoding="utf-8"))["documents"]
    if args.only:
        documents = [d for d in documents if d["name"] == args.only]
        if not documents:
            raise SystemExit(f"No agenda named {args.only} in {SET}")
    results = []
    for document in documents:
        print(f"{document['name']}...", file=sys.stderr, flush=True)
        results.append(evaluate(document, client, args.model, prices, effort=args.effort))
    run = {"set": "agendas", "model": args.model, "effort": args.effort, "prices": prices}
    text, failed = report(results, run)
    print(text)
    if args.json:
        args.json.write_text(json.dumps(minutes_set.without_costs({**run, "totals": totals_of(results), "documents": results}),
                                        indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
