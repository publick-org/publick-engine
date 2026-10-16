"""The translation prompt run against the real model, on English summaries from several towns in
several states, before a prompt change ships (the minutes prompt's is pipeline/evaluate.py).

Each summary in evals/translations.json is translated, checked without AI, and reviewed by AI
exactly as its town's run does it (translate.make, with the town's glossary and board names), and
made again once, as a correction, when it fails, as the next run would. The report says which
models translated and reviewed, at what effort, how many passed the first time and after the
correction, by town, why the rest failed (the check, the review, with its words, or a response cut
off or refused), how many of the gold terms were kept, their tokens, and what it cost, in all and a
summary. Nothing is saved but the report: the translations are made in a temporary folder.

The gold terms are the set's reference translations for the glossary: for each summary, each term
of its town's glossary that the English uses, entry by entry, with the wording the glossary gives
it in Spanish (pipeline/glossary.py), as the set was built. A summary's last translation keeps a
gold term when it writes it so, whether or not it passed its other checks.

The run fails (exit status 1) when it misses one of the set's thresholds: the share of summaries
that pass after the correction (passed), the share of gold terms kept (gold_terms), and the most
summaries cut off or refused (stopped).

The models are the town's (its [summaries] translation_model and translation_review_model, or
translate.py's defaults) unless --model (the translator) and --review-model (the reviewer) say
otherwise, each with its effort (--effort, --review-effort) and, for a model not in
pipeline/evaluate.py's PRICES, its prices.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate_translations [--only NAME] [--model MODEL] [--effort EFFORT]
        [--review-model MODEL] [--review-effort EFFORT] [--json FILE]
    python -m pipeline.evaluate_translations build <network repository> <town folder>...   # the set, from towns' data

The set holds, for each town, its board names in Spanish (from its [strings.es]: the board names
the translator is given), its glossary ([[glossary]] and [terms]); the network's [summaries]
table; the lowercase words of every town's summaries (the check's ordinary words); the thresholds;
and the summaries, each with its town, its meeting's board, title, and date, and its gold terms.
A town gives up to PER_TOWN summaries, those whose English uses a glossary term first.
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
# What the set keeps of the network's [summaries]: which models translate and review, at what effort.
MODEL_KEYS = ("translation_model", "translation_effort", "translation_review_model", "translation_review_effort")
# The most summaries a town gives the set (build()).
PER_TOWN = 8
# What a run must reach, unless the set gives its own: the share of summaries passing after the correction,
# the share of gold terms kept, and the most summaries cut off or refused.
THRESHOLDS = {"passed": 0.75, "gold_terms": 0.95, "stopped": 0}


def gold_terms(english: dict, kind: str, terms: list[dict]) -> list[dict]:
    """A summary's gold terms: each glossary term with a Spanish wording that each of its entries uses,
    {"where": "summary" or "items 2", "term": English, "es": the glossary's Spanish}."""
    out = []
    for field in translate.FIELDS[kind]:
        value = english.get(field)
        texts = list(enumerate(value, 1)) if isinstance(value, list) else [(None, value or "")]
        for i, text in texts:
            for e in terms:
                if not e.get("bodies") and glossary.wording(e, LANG) and glossary.found(e, text):
                    out.append({"where": field if i is None else f"{field} {i}", "term": glossary.english(e),
                                "es": glossary.wording(e, LANG)})
    return out


def build(network: Path, towns: list[str], per_town: int = PER_TOWN) -> dict:
    """The set, from a network repository's towns' folders (towns/<town>): for each, up to per_town of its
    summaries, those that use a glossary term first, newest first, agendas and minutes in turn."""
    from pipeline.config import read_config
    the_set = {"about": f"English summaries of {len(towns)} towns as of {datetime.now(timezone.utc).date()}, with "
                        "the gold terms of their glossaries, for pipeline/evaluate_translations.py.",
               "thresholds": THRESHOLDS, "summaries": {}, "words": [], "towns": {}, "documents": []}
    for town in towns:
        data = network / "towns" / town / "data"
        config = read_config(next((network / "towns" / town / "config").glob("*.toml")), network)
        terms = glossary.entries(config)
        candidates = []
        for kind, meeting, doc, record in summarize.summarized_documents(data):
            english = translate.english(record, kind)
            candidates.append({"town": town, "name": f"{town} {meeting['date']} {meeting['body']} {kind}", "kind": kind,
                               "title": meeting["title"], "date": meeting["date"], "body": meeting["body"],
                               "sha256": doc["sha256"], "english": english, "gold": gold_terms(english, kind, terms)})
        # Newest first, then those with gold terms ahead of the rest.
        candidates.sort(key=lambda d: (d["date"], d["sha256"]), reverse=True)
        candidates.sort(key=lambda d: not d["gold"])
        # Agendas and minutes in turn, so both kinds are in the set.
        by_kind = {kind: [d for d in candidates if d["kind"] == kind] for kind in ("minutes", "agenda")}
        picked = [d for pair in zip(*by_kind.values()) for d in pair][:per_town]
        picked += [d for d in candidates if d not in picked][:per_town - len(picked)]
        chosen = sorted(picked, key=lambda d: (d["date"], d["name"]))
        boards = {d["body"] for d in chosen}
        strings = config.get("strings", {}).get(LANG, {})
        the_set["towns"][town] = {"strings": {LANG: {b: strings[b] for b in sorted(boards) if b in strings}},
                                  "glossary": config.get("glossary", []), "terms": config.get("terms", {})}
        # The models and their efforts; their prices are the models' list prices (pipeline/evaluate.py PRICES).
        the_set["summaries"] = the_set["summaries"] or {k: v for k, v in config.get("summaries", {}).items()
                                                        if k in MODEL_KEYS}
        the_set["words"] = the_set["words"] or sorted(translate.town_words(data))
        the_set["documents"] += chosen
    return the_set


def towns_of(the_set: dict) -> dict:
    """Each town's config for the run, by town: a set of one town (the set before it held several) as one."""
    if "towns" in the_set:
        return the_set["towns"]
    return {the_set["town"]: the_set["config"]}


def network_summaries(the_set: dict) -> dict:
    return the_set.get("summaries") or the_set.get("config", {}).get("summaries", {})


def models(the_set: dict, model: str | None = None, effort: str | None = None, prices: dict | None = None,
           review_model: str | None = None, review_effort: str | None = None, review_prices: dict | None = None) -> dict:
    """The set's [summaries] table with the translator and the reviewer given (None: the town's), each
    with its effort and prices, as a town's config gives them (translate.settings(), review_settings())."""
    table = dict(network_summaries(the_set))
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
    """Each document translated as a run does it, in its town of a temporary network, with the town's
    board names and glossary. summaries: the [summaries] table to run with (models()), else the set's.
    prices, review_prices: what the translator's and the reviewer's requests cost (pipeline/evaluate.py's
    prices_for()), else the table's."""
    summaries = summaries or network_summaries(the_set)
    configs = {town: {**config, "summaries": summaries, "site": {"languages": ["en", LANG]}}
               for town, config in towns_of(the_set).items()}
    first = next(iter(configs.values()))
    prices = prices or translate.settings(first)
    review_prices = review_prices or translate.review_settings(first)
    recorder = minutes_set.Recorder(client, purpose=purpose)
    documents = [d for d in the_set["documents"] if not only or d["name"] == only]
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        towns = Path(tmp) / "towns"
        # Every town's ordinary words, as the network's own summaries give them (translate.town_words).
        words = towns / "_network" / "data" / "summaries"
        words.mkdir(parents=True)
        (words / "words.json").write_text(json.dumps({"summary": " ".join(the_set["words"])}), encoding="utf-8")
        for town in configs:
            (towns / town / "data" / "summaries").mkdir(parents=True)
        for d in documents:
            town = d.get("town") or the_set["town"]
            (towns / town / "data" / "summaries" / f"{d['sha256']}.json").write_text(
                json.dumps({**d["english"], "kind": d["kind"]}), encoding="utf-8")
        translate.town_words.cache_clear()
        now = datetime.now(timezone.utc)
        for d in documents:
            print(f"{d['name']}...", file=sys.stderr, flush=True)
            town = d.get("town") or the_set["town"]
            config, data = configs[town], towns / town / "data"
            terms = glossary.entries(config)
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
                passed = translate.passes(data, out, record, d["kind"], LANG, terms)
                if passed:
                    break
            requests = recorder.requests[start:]
            used = {"translation": minutes_set.spent([r for r in requests if not is_review(r)], prices),
                    "review": minutes_set.spent([r for r in requests if is_review(r)], review_prices)}
            last = next((t["translation"] for t in reversed(tries) if "translation" in t), None)
            results.append({"name": d["name"], "town": town, "kind": d["kind"], "english": d["english"], "tries": tries,
                            "passed": passed, "outcome": outcome(passed, tries),
                            "gold": kept_gold(d.get("gold", []), last, terms), "spent": used,
                            "cost": round(used["translation"]["cost"] + used["review"]["cost"], 6)})
    return results


def kept_gold(gold: list[dict], translation: dict | None, terms: list[dict]) -> list[dict]:
    """Each gold term, with whether the translation (its last try's) writes it as the glossary gives it."""
    by_term = {glossary.english(e): e for e in terms}
    out = []
    for g in gold:
        field, _, i = g["where"].partition(" ")
        value = (translation or {}).get(field)
        text = (value[int(i) - 1] if isinstance(value, list) and i and int(i) <= len(value) else "") if i else (value or "")
        entry = by_term.get(g["term"])
        out.append({**g, "kept": bool(entry and isinstance(text, str) and glossary.found(entry, text, LANG))})
    return out


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
    gold = [g for r in results for g in r.get("gold", [])]
    t["gold_terms"] = len(gold)
    t["gold_terms_kept"] = sum(g["kept"] for g in gold)
    t["by_town"] = {town: {"summaries": sum(r.get("town") == town for r in results),
                           "passed": sum(r.get("town") == town and r["passed"] for r in results)}
                    for town in dict.fromkeys(r.get("town") for r in results) if town}
    return t


def missed(t: dict, thresholds: dict) -> list[str]:
    """The thresholds a run's totals miss, each as a sentence; none when it passes."""
    out = []
    n = max(t["summaries"], 1)
    if "passed" in thresholds and t["passed"] / n < thresholds["passed"]:
        out.append(f"{t['passed']} of {t['summaries']} passed, under {thresholds['passed']:.0%}")
    if "gold_terms" in thresholds and t["gold_terms"] and t["gold_terms_kept"] / t["gold_terms"] < thresholds["gold_terms"]:
        out.append(f"{t['gold_terms_kept']} of {t['gold_terms']} gold terms kept, under {thresholds['gold_terms']:.0%}")
    if "stopped" in thresholds and t["stopped"] > thresholds["stopped"]:
        out.append(f"{t['stopped']} cut off or refused, over {thresholds['stopped']}")
    return out


def report(results: list[dict], run: dict | None = None, thresholds: dict | None = None) -> str:
    """The report. run: the translator and the reviewer, each with its model, effort, and prices (main()).
    thresholds: the set's, for its verdict."""
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
              f"- Gold terms kept: {t['gold_terms_kept']} of {t['gold_terms']}"
              + (f" ({100 * t['gold_terms_kept'] / t['gold_terms']:.0f}%)" if t["gold_terms"] else ""),
              f"- Cost: {minutes_set.money(t['cost'])} ({minutes_set.money(t['cost_per_summary'], 4)} a summary)"]
    if len(t["by_town"]) > 1:
        lines.append("- Passed by town: " + "; ".join(f"{town} {c['passed']} of {c['summaries']}"
                                                       for town, c in t["by_town"].items()))
    if thresholds is not None:
        misses = missed(t, thresholds)
        lines.append("- Thresholds: " + ("**missed**: " + "; ".join(misses) if misses else "all met")
                     + f" (passed {thresholds.get('passed', 0):.0%}, gold terms {thresholds.get('gold_terms', 0):.0%}, "
                       f"at most {thresholds.get('stopped', 0)} cut off or refused).")
    lines += ["", "## Shown in English after both tries", ""]
    for r in results:
        if not r["passed"]:
            last = r["tries"][-1]
            lines.append(f"- **{r['name']}** ({why(last)}): {what(last)}")
    lines += ["", "## Corrected on the second try", ""]
    for r in results:
        if r["passed"] and len(r["tries"]) > 1:
            first_try = r["tries"][0]
            lines.append(f"- **{r['name']}** ({why(first_try)}): {what(first_try)}")
    lost = [(r["name"], g) for r in results for g in r.get("gold", []) if not g["kept"]]
    if lost:
        lines += ["", "## Gold terms not kept", ""]
        lines += [f"- **{name}**, {g['where']}: \"{g['term']}\" should be \"{g['es']}\"" for name, g in lost]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    made = sub.add_parser("build", help="write the set from a network repository's towns")
    made.add_argument("network", type=Path)
    made.add_argument("towns", nargs="+")
    made.add_argument("--per-town", type=int, default=PER_TOWN)
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
        SET.write_text(json.dumps(build(args.network, args.towns, args.per_town), indent=1, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        print(f"Wrote {SET}")
        return 0
    the_set = json.loads(SET.read_text(encoding="utf-8"))
    run = runs_with(the_set, args)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic
    results = evaluate(the_set, anthropic.Anthropic(), args.only, run["summaries"], run["translation"]["prices"],
                       run["review"]["prices"])
    thresholds = {**THRESHOLDS, **the_set.get("thresholds", {})}
    print(report(results, run, thresholds))
    t = totals(results)
    if args.json:
        args.json.write_text(json.dumps(minutes_set.without_costs({
                                         "set": "translations", "prompt_version": translate.VERSION,
                                         "translation": run["translation"], "review": run["review"],
                                         "thresholds": thresholds, "missed": missed(t, thresholds),
                                         "totals": t, "summaries": results}),
                                        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # One summary alone (--only) is a look, not a verdict.
    return 1 if missed(t, thresholds) and not args.only else 0


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
