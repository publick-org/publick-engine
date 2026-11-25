"""The digest subject prompt run against the real model, on issues whose best stories were read by hand, before a
change to the prompt (pipeline/digest_subject.py) or to how its answer is checked (problems(), there too) ships.

Each issue in evals/digest_subjects.json, taken from the network's weekly digests, has the candidates its subject
could be about, as digest_subject.candidates() gives them, and the ones a reader would want it to lead with ("good"). Each is
sent as a town's run sends it, and its answer checked as the build checks it. The report says, for the set and each
issue: the candidate picked, and whether it's a good one; the subject and preheader; and whatever would keep the
build from using the answer (then the email's subject is the lead decision's headline, as without a model).

A run fails (exit status 1) if fewer than USED_AT_LEAST of the answers would be used, or fewer than GOOD_AT_LEAST
of the picks are good.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set digest [--only NAME] [--model MODEL] [--effort EFFORT] [--json FILE]
    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set digest --models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5

With --models, each model runs the whole set, and the report compares them: how many answers each would have used,
how many good picks, how long a request took, and (unless costs are hidden) what each run cost.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from pipeline import digest_subject, summarize
from pipeline import evaluate as minutes_set

SET = Path(__file__).resolve().parent.parent / "evals" / "digest_subjects.json"
# The least share of answers the build would use, and of picks that are among the good ones.
USED_AT_LEAST = 0.85
GOOD_AT_LEAST = 0.7
# The model a run uses without --model: the one the network writes subjects with (defaults/network.toml [summaries]
# digest_model, or else its summaries' model).
DEFAULT_MODEL = "claude-haiku-5-5"


def evaluate(issue: dict, client, model: str, prices: dict, effort: str | None = None) -> dict:
    """One issue's answer, checked, with how long it took."""
    started = time.monotonic()
    response = client.messages.create(**digest_subject.request(model, issue["town"], issue["send"], issue["candidates"],
                                                               effort))
    seconds = round(time.monotonic() - started, 1)
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    text = next((b.text for b in response.content if b.type == "text"), "")
    answer, found = {}, []
    if response.stop_reason != "end_turn":
        found = [f"it stopped early ({summarize.stop_reason(response)})"]
    else:
        try:
            answer = json.loads(text)
        except ValueError:
            found = ["its answer isn't JSON"]
    found = found or digest_subject.problems(answer, issue["candidates"])
    return {"name": issue["name"], "pick": answer.get("pick"), "good": answer.get("pick") in issue["good"],
            "expected": issue["good"], "subject": answer.get("subject"), "preheader": answer.get("preheader"),
            "problems": found, "used": not found, "usage": usage, "cost": summarize.cost(usage, prices),
            "seconds": seconds}


def scored(results: list[dict]) -> tuple[int, int, bool]:
    """How many answers would be used, how many picks are good, and whether that fails the set."""
    n = len(results)
    used, good = sum(r["used"] for r in results), sum(r["good"] for r in results)
    return used, good, n == 0 or used < USED_AT_LEAST * n or good < GOOD_AT_LEAST * n


def report(runs: list[dict]) -> tuple[str, bool]:
    """Each model's run as Markdown, side by side, and whether any failed. runs: {"model", "effort", "results"}."""
    failed = False
    lines = ["# Digest subjects", "",
             f"Each run passes with at least {USED_AT_LEAST:.0%} of its answers usable and {GOOD_AT_LEAST:.0%} of its "
             "picks good.", "",
             "| Model | Effort | Usable | Good picks | Seconds, median | Result |" + ("" if minutes_set.costs_hidden() else " Cost |"),
             "|---|---|---|---|---|---|" + ("" if minutes_set.costs_hidden() else "---|")]
    for run in runs:
        results = run["results"]
        used, good, bad = scored(results)
        failed |= bad
        seconds = sorted(r.get("seconds", 0) for r in results)
        median = seconds[len(seconds) // 2] if seconds else 0
        cost = "" if minutes_set.costs_hidden() else f" ${sum(r['cost'] for r in results):.4f} |"
        lines.append(f"| {run['model']} | {run.get('effort') or 'default'} | {used} of {len(results)} | "
                     f"{good} of {len(results)} | {median} | {'failed' if bad else 'passed'} |{cost}")
    cell = lambda text: str(text or "").replace("|", "\\|")
    for run in runs:
        lines += ["", f"## {run['model']}" + (f", effort {run['effort']}" if run.get("effort") else ""), "",
                  "| Issue | Pick | Subject | Preheader | Not used because |", "|---|---|---|---|---|"]
        for r in run["results"]:
            pick = f"{r['pick']} {'(good)' if r['good'] else '(expected ' + ' or '.join(r['expected']) + ')'}"
            lines.append(f"| {r['name']} | {pick} | {cell(r['subject'])} | {cell(r['preheader'])} | "
                         f"{cell('; '.join(r['problems']))} |")
    return "\n".join(lines) + "\n", failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="the one issue of the set to run (its name)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--models", help="several models, separated by commas, each run on the set and compared")
    parser.add_argument("--effort", choices=minutes_set.EFFORTS,
                        help="sent with each request (default: none, the model's own, as a town's run sends it)")
    parser.add_argument("--input-price", type=float, help="dollars per million tokens (default: the model's in PRICES)")
    parser.add_argument("--output-price", type=float)
    parser.add_argument("--json", type=Path, help="also save every result here")
    args = parser.parse_args(argv)
    models = [m.strip() for m in (args.models or args.model).split(",") if m.strip()]
    if len(models) > 1 and (args.input_price is not None or args.output_price is not None):
        raise SystemExit("--input-price and --output-price are for one model; several are priced from PRICES")
    prices = {m: minutes_set.prices_for(m, args.input_price, args.output_price) for m in models}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic

    client = anthropic.Anthropic()
    issues = json.loads(SET.read_text(encoding="utf-8"))["issues"]
    if args.only:
        issues = [i for i in issues if i["name"] == args.only]
        if not issues:
            raise SystemExit(f"No issue named {args.only} in {SET}")
    runs = []
    for model in models:
        results = []
        for issue in issues:
            print(f"{model}: {issue['name']}...", file=sys.stderr, flush=True)
            results.append(evaluate(issue, client, model, prices[model], args.effort))
        runs.append({"model": model, "effort": args.effort, "prices": prices[model], "results": results})
    text, failed = report(runs)
    print(text)
    if args.json:
        args.json.write_text(json.dumps(minutes_set.without_costs({"set": "digest", "runs": runs}), indent=2) + "\n",
                             encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
