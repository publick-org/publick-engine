"""The digest subject prompt run against the real model, on issues whose best stories were read by hand, before a
change to the prompt (pipeline/digest_subject.py) or to how its answer is checked (problems(), there too) ships.

Each issue in evals/digest_subjects.json, taken from the network's weekly digests, has the candidates its subject
could be about, as digest_subject.candidates() gives them, and the ones a reader would want it to lead with ("good"). Each is
sent as a town's run sends it, and its answer checked as the build checks it. The report says, for the set and each
issue: the candidate picked, and whether it's a good one; the subject and preheader; and whatever would keep the
build from using the answer (then the email's subject is the lead decision's headline, as without a model).

A run fails (exit status 1) if fewer than USED_AT_LEAST of the answers would be used, or fewer than GOOD_AT_LEAST
of the picks are good.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate --set digest [--only NAME] [--model MODEL] [--json FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pipeline import digest_subject, summarize
from pipeline import evaluate as minutes_set

SET = Path(__file__).resolve().parent.parent / "evals" / "digest_subjects.json"
# The least share of answers the build would use, and of picks that are among the good ones.
USED_AT_LEAST = 0.85
GOOD_AT_LEAST = 0.7
# The model the network writes subjects with: its summaries' (defaults/network.toml [summaries] model).
DEFAULT_MODEL = "claude-haiku-5-5"


def evaluate(issue: dict, client, model: str, prices: dict) -> dict:
    """One issue's answer, checked."""
    response = client.messages.create(**digest_subject.request(model, issue["town"], issue["send"], issue["candidates"]))
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
            "problems": found, "used": not found, "usage": usage, "cost": summarize.cost(usage, prices)}


def report(results: list[dict], run: dict) -> tuple[str, bool]:
    """The run as Markdown, and whether it failed."""
    n = len(results)
    used, good = sum(r["used"] for r in results), sum(r["good"] for r in results)
    failed = n == 0 or used < USED_AT_LEAST * n or good < GOOD_AT_LEAST * n
    lines = [f"# Digest subjects: {'failed' if failed else 'passed'}", "",
             f"Model {run['model']}. {used} of {n} answers would be used (at least {USED_AT_LEAST:.0%}), "
             f"{good} of {n} picks are good (at least {GOOD_AT_LEAST:.0%}).", ""]
    if not minutes_set.costs_hidden():
        lines += [f"Cost: ${sum(r['cost'] for r in results):.4f}.", ""]
    lines += ["| Issue | Pick | Subject | Preheader | Not used because |", "|---|---|---|---|---|"]
    for r in results:
        pick = f"{r['pick']} {'(good)' if r['good'] else '(expected ' + ' or '.join(r['expected']) + ')'}"
        cell = lambda text: str(text or "").replace("|", "\\|")
        lines.append(f"| {r['name']} | {pick} | {cell(r['subject'])} | {cell(r['preheader'])} | {cell('; '.join(r['problems']))} |")
    return "\n".join(lines) + "\n", failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="the one issue of the set to run (its name)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", help="ignored: the subject is asked without one, as a town's run asks it")
    parser.add_argument("--input-price", type=float, help="dollars per million tokens (default: the model's in PRICES)")
    parser.add_argument("--output-price", type=float)
    parser.add_argument("--json", type=Path, help="also save every result here")
    args = parser.parse_args(argv)
    prices = minutes_set.prices_for(args.model, args.input_price, args.output_price)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic

    client = anthropic.Anthropic()
    issues = json.loads(SET.read_text(encoding="utf-8"))["issues"]
    if args.only:
        issues = [i for i in issues if i["name"] == args.only]
        if not issues:
            raise SystemExit(f"No issue named {args.only} in {SET}")
    results = []
    for issue in issues:
        print(f"{issue['name']}...", file=sys.stderr, flush=True)
        results.append(evaluate(issue, client, args.model, prices))
    run = {"set": "digest", "model": args.model, "prices": prices}
    text, failed = report(results, run)
    print(text)
    if args.json:
        args.json.write_text(json.dumps(minutes_set.without_costs({**run, "issues": results}), indent=2) + "\n",
                             encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
