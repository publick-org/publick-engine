"""The translation prompt run against the real model, on one town's English summaries, before a
prompt change ships (the minutes prompt's is pipeline/evaluate.py).

Each summary in evals/translations.json is translated, checked without AI, and reviewed by AI
exactly as a town's run does it (translate.make), and made again once, as a correction, when it
fails, as the next run would. The report says which models translated and reviewed, at what
effort, how many passed the first time and after the correction, why the rest failed (the check,
the review, with its words, or a response cut off or refused), their tokens, and what it cost, in
all and a summary. Nothing is saved but the report: the translations are made in a temporary folder.

The models are the town's (its [summaries] translation_model and translation_review_model, or
translate.py's defaults) unless --model (the translator) and --review-model (the reviewer) say
otherwise, each with its effort (--effort, --review-effort) and, for a model not in
pipeline/evaluate.py's PRICES, its prices.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate_translations [--only NAME] [--model MODEL] [--effort EFFORT]
        [--review-model MODEL] [--review-effort EFFORT] [--json FILE]
    python -m pipeline.evaluate_translations build <network repository> <town folder>   # the set, from a town's data

The set holds the town's summaries with each meeting's board, title, and date, the town's
[strings.es] and [summaries] tables and its Spanish drafts (the board names the translator is
given), and the lowercase words of every town's summaries (the check's ordinary words). A run
costs about a cent a summary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pipeline import evaluate as minutes_set
from pipeline import glossary, summarize, translate

SET = Path(__file__).resolve().parent.parent / "evals" / "translations.json"
LANG = "es"


def build(network: Path, town: str) -> dict:
    """The set, from a network repository's town folder (towns/<town>)."""
    from pipeline.config import read_config
    data = network / "towns" / town / "data"
    config = read_config(next((network / "towns" / town / "config").glob("*.toml")), network)
    documents = []
    for kind, meeting, doc, record in summarize.summarized_documents(data):
        documents.append({"name": f"{meeting['date']} {meeting['body']} {kind}", "kind": kind, "title": meeting["title"],
                          "date": meeting["date"], "body": meeting["body"], "sha256": doc["sha256"],
                          "english": translate.english(record, kind)})
    drafts_file = translate.strings_path(data, LANG)
    return {
        "about": f"{town}'s English summaries as of {datetime.now(timezone.utc).date()}, for "
                 "pipeline/evaluate_translations.py.",
        "town": town,
        "config": {"strings": {LANG: config.get("strings", {}).get(LANG, {})}, "summaries": config.get("summaries", {})},
        "drafts": json.loads(drafts_file.read_text(encoding="utf-8")) if drafts_file.exists() else {},
        "words": sorted(translate.town_words(data)),
        "documents": documents,
    }


def models(the_set: dict, model: str | None = None, effort: str | None = None, prices: dict | None = None,
           review_model: str | None = None, review_effort: str | None = None, review_prices: dict | None = None) -> dict:
    """The set's [summaries] table with the translator and the reviewer given (None: the town's), each
    with its effort and prices, as a town's config gives them (translate.settings(), review_settings())."""
    table = dict(the_set["config"].get("summaries", {}))
    for prefix, name, level, cost in (("translation", model, effort, prices),
                                      ("translation_review", review_model, review_effort, review_prices)):
        if name:
            table[f"{prefix}_model"] = name
        if level:
            table[f"{prefix}_effort"] = level
        for key, value in (cost or {}).items():
            if key in ("input_price", "output_price"):
                table[f"{prefix}_{key}"] = value
    return table


def is_review(request: dict) -> bool:
    return request["purpose"] == "review"


def purpose(kwargs: dict) -> str:
    """What a request is for: the review of a translation's meaning, or the translation."""
    properties = kwargs["output_config"]["format"]["schema"]["properties"]
    return "review" if "problems" in properties else "translation"


def evaluate(the_set: dict, client, only: str | None = None, summaries: dict | None = None,
             prices: dict | None = None, review_prices: dict | None = None) -> list[dict]:
    """Each document translated as a run does it, in a temporary town of a temporary network.
    summaries: the [summaries] table to run with (models()), else the set's. prices, review_prices:
    what the translator's and the reviewer's requests cost (pipeline/evaluate.py's prices_for()), else
    the table's."""
    config = {**the_set["config"], "summaries": summaries or the_set["config"].get("summaries", {}),
              "site": {"languages": ["en", LANG]}}
    prices = prices or translate.settings(config)
    review_prices = review_prices or translate.review_settings(config)
    recorder = minutes_set.Recorder(client, purpose=purpose)
    documents = [d for d in the_set["documents"] if not only or d["name"] == only]
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        towns = Path(tmp) / "towns"
        data = towns / the_set["town"] / "data"
        (data / "summaries").mkdir(parents=True)
        # Every town's ordinary words, as the network's own summaries give them (translate.town_words).
        words = towns / "_network" / "data" / "summaries"
        words.mkdir(parents=True)
        (words / "words.json").write_text(json.dumps({"summary": " ".join(the_set["words"])}), encoding="utf-8")
        if the_set.get("drafts"):
            translate.strings_path(data, LANG).parent.mkdir(parents=True, exist_ok=True)
            translate.strings_path(data, LANG).write_text(json.dumps(the_set["drafts"]), encoding="utf-8")
        for d in documents:
            (data / "summaries" / f"{d['sha256']}.json").write_text(json.dumps({**d["english"], "kind": d["kind"]}),
                                                                    encoding="utf-8")
        translate.town_words.cache_clear()
        now = datetime.now(timezone.utc)
        for d in documents:
            print(f"{d['name']}...", file=sys.stderr, flush=True)
            record = {**d["english"], "kind": d["kind"]}
            meeting = {"title": d["title"], "date": d["date"], "body": d["body"]}
            doc = {"sha256": d["sha256"]}
            tries, passed, start = [], False, len(recorder.requests)
            for _ in range(translate.ATTEMPTS):
                try:
                    out, _ = translate.make(recorder, config, data, LANG, d["kind"], meeting, doc, record, now,
                                            summarize.cost)
                except summarize.StoppedEarly as e:
                    # Cut off or refused: nothing saved, so the next run would try again from the start.
                    tries.append({"stopped": str(e)})
                    continue
                tries.append({"check": out["check"], "review": out["review"],
                              "translation": {f: out.get(f) for f in translate.FIELDS[d["kind"]]}})
                passed = translate.passes(data, out, record, d["kind"], LANG, glossary.entries(config))
                if passed:
                    break
            requests = recorder.requests[start:]
            used = {"translation": minutes_set.spent([r for r in requests if not is_review(r)], prices),
                    "review": minutes_set.spent([r for r in requests if is_review(r)], review_prices)}
            results.append({"name": d["name"], "kind": d["kind"], "english": d["english"], "tries": tries,
                            "passed": passed, "outcome": outcome(passed, tries), "spent": used,
                            "cost": round(used["translation"]["cost"] + used["review"]["cost"], 6)})
    return results


def outcome(passed: bool, tries: list[dict]) -> str:
    """How a summary's translation came out: passed on the first try, or corrected on the second; or,
    after both, failed the check without AI, failed the AI's review, or its last try was cut off or
    refused."""
    if passed:
        return "first_try" if len(tries) == 1 else "corrected"
    if "stopped" in tries[-1]:
        return "stopped"
    return "failed_check" if tries[-1]["check"] != "ok" else "failed_review"


def why(attempt: dict) -> str:
    """Which check a failed try failed: the check without AI, or the review; or it was cut off or refused."""
    if "stopped" in attempt:
        return "stopped"
    return "check" if attempt["check"] != "ok" else "review"


def what(attempt: dict) -> str:
    return {"stopped": attempt.get("stopped"), "check": attempt.get("check")}.get(why(attempt)) or attempt["review"]


def totals(results: list[dict]) -> dict:
    """The run's counts, tokens, and cost, for its report and its JSON."""
    n = len(results)
    t = {"summaries": n, **Counter(r["outcome"] for r in results)}
    for key in ("first_try", "corrected", "failed_check", "failed_review", "stopped"):
        t.setdefault(key, 0)
    t["passed"] = t["first_try"] + t["corrected"]
    t["first_tries_failed"] = dict(Counter(why(r["tries"][0]) for r in results if r["outcome"] != "first_try"))
    for role in ("translation", "review"):
        t[role] = {key: sum(r["spent"][role][key] for r in results)
                   for key in ("requests", "input_tokens", "output_tokens", "cache_read_input_tokens", "cost")}
        t[role]["cost"] = round(t[role]["cost"], 6)
    t["cost"] = round(sum(r["cost"] for r in results), 6)
    t["cost_per_summary"] = round(t["cost"] / max(n, 1), 6)
    return t


def report(results: list[dict], run: dict | None = None) -> str:
    """The report. run: the translator and the reviewer, each with its model, effort, and prices (main())."""
    n = len(results)
    t = totals(results)
    pct = lambda k: f"{k} of {n} ({100 * k / max(n, 1):.0f}%)"
    lines = [f"# Translation prompt {translate.VERSION}: {n} summaries", ""]
    if run:
        for role, label in (("translation", "Translated by"), ("review", "Reviewed by")):
            lines.append(f"{label} {run[role]['model']}, effort: {run[role]['effort'] or 'none sent (the model default)'}; "
                         f"prices per million tokens: {minutes_set.price_text(run[role]['prices'])}.")
        lines.append("")
    failed_first = t["first_tries_failed"]
    tokens = lambda role: (f"{t[role]['requests']} requests, {t[role]['input_tokens']:,} input "
                           f"({t[role]['cache_read_input_tokens']:,} more read from the cache) and "
                           f"{t[role]['output_tokens']:,} output tokens, {minutes_set.money(t[role]['cost'])}")
    lines += [f"- Passed the first time: {pct(t['first_try'])}",
              f"- Passed after the correction: {pct(t['passed'])}",
              f"- Shown in English after both tries: {t['failed_check']} failed the check, {t['failed_review']} "
              f"failed the review, {t['stopped']} cut off or refused",
              f"- First tries that failed: {failed_first.get('check', 0)} the check, {failed_first.get('review', 0)} "
              f"the review, {failed_first.get('stopped', 0)} cut off or refused",
              f"- Translations: {tokens('translation')}",
              f"- Reviews: {tokens('review')}",
              f"- Cost: {minutes_set.money(t['cost'])} ({minutes_set.money(t['cost_per_summary'], 4)} a summary)", "",
              "## Shown in English after both tries", ""]
    for r in results:
        if not r["passed"]:
            last = r["tries"][-1]
            lines.append(f"- **{r['name']}** ({why(last)}): {what(last)}")
    lines += ["", "## Corrected on the second try", ""]
    for r in results:
        if r["passed"] and len(r["tries"]) > 1:
            first_try = r["tries"][0]
            lines.append(f"- **{r['name']}** ({why(first_try)}): {what(first_try)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    made = sub.add_parser("build", help="write the set from a network repository's town")
    made.add_argument("network", type=Path)
    made.add_argument("town")
    parser.add_argument("--only", help="the one summary of the set to run (its name)")
    parser.add_argument("--model", help="the translator (default: the town's translation_model, or translate.py's)")
    parser.add_argument("--effort", choices=minutes_set.EFFORTS, help="the translator's (default: the town's, or "
                        "low for translate.py's model, else none sent)")
    parser.add_argument("--input-price", type=float, help="the translator's, for a model not in pipeline/evaluate.py's PRICES")
    parser.add_argument("--output-price", type=float)
    parser.add_argument("--review-model", help="the reviewer (default: the town's translation_review_model, or translate.py's)")
    parser.add_argument("--review-effort", choices=minutes_set.EFFORTS, help="the reviewer's (default: low)")
    parser.add_argument("--review-input-price", type=float)
    parser.add_argument("--review-output-price", type=float)
    parser.add_argument("--json", type=Path, help="also save every result here")
    args = parser.parse_args(argv)
    if args.command == "build":
        SET.write_text(json.dumps(build(args.network, args.town), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {SET}")
        return 0
    the_set = json.loads(SET.read_text(encoding="utf-8"))
    run = runs_with(the_set, args)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic
    results = evaluate(the_set, anthropic.Anthropic(), args.only, run["summaries"], run["translation"]["prices"],
                       run["review"]["prices"])
    print(report(results, run))
    if args.json:
        args.json.write_text(json.dumps(minutes_set.without_costs({
                                         "set": "translations", "prompt_version": translate.VERSION,
                                         "translation": run["translation"], "review": run["review"],
                                         "totals": totals(results), "summaries": results}),
                                        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def runs_with(the_set: dict, args) -> dict:
    """The [summaries] table the run uses (models()), and its translator and reviewer: model, effort,
    and prices (given, else the model's list prices, else the town's)."""
    first = models(the_set, args.model, args.effort, None, args.review_model, args.review_effort)
    config = {"summaries": first}
    roles = {}
    for role, settings, given in (("translation", translate.settings(config), (args.input_price, args.output_price)),
                                  ("review", translate.review_settings(config),
                                   (args.review_input_price, args.review_output_price))):
        town = {k: settings[k] for k in ("input_price", "output_price") if settings.get(k) is not None}
        try:
            prices = minutes_set.prices_for(settings["model"], *given)
        except SystemExit:
            if len(town) < 2:
                raise
            prices = {**town, **{k: v for k, v in zip(("input_price", "output_price"), given) if v is not None}}
        roles[role] = {"model": settings["model"], "effort": settings["effort"], "prices": prices}
    summaries = models(the_set, args.model, args.effort, roles["translation"]["prices"], args.review_model,
                       args.review_effort, roles["review"]["prices"])
    return {"summaries": summaries, **roles}


if __name__ == "__main__":
    sys.exit(main())
