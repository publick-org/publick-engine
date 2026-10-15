"""The translation prompt run against the real model, on one town's English summaries, before a
prompt change ships (the minutes prompt's is pipeline/evaluate.py).

Each summary in evals/translations.json is translated, checked without AI, and reviewed by AI
exactly as a town's run does it (translate.make), and made again once, as a correction, when it
fails, as the next run would. The report says how many passed the first time and after the
correction, why the rest failed (the check, or the review, with its words), and what it cost.
Nothing is saved but the report: the translations are made in a temporary folder.

    ANTHROPIC_API_KEY=... python -m pipeline.evaluate_translations [--only NAME] [--json FILE]
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

from pipeline import summarize, translate

SET = Path(__file__).resolve().parent.parent / "evals" / "translations.json"
LANG = "es"


def build(network: Path, town: str) -> dict:
    """The set, from a network repository's town folder (towns/<town>)."""
    import tomllib
    data = network / "towns" / town / "data"
    config = tomllib.loads(next((network / "towns" / town / "config").glob("*.toml")).read_text(encoding="utf-8"))
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


def evaluate(the_set: dict, client, only: str | None = None) -> list[dict]:
    """Each document translated as a run does it, in a temporary town of a temporary network."""
    config = {**the_set["config"], "site": {"languages": ["en", LANG]}}
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
            tries, cost = [], 0.0
            for _ in range(translate.ATTEMPTS):
                out, paid = translate.make(client, config, data, LANG, d["kind"], meeting, doc, record, now, summarize.cost)
                cost += paid
                tries.append({"check": out["check"], "review": out["review"],
                              "translation": {f: out.get(f) for f in translate.FIELDS[d["kind"]]}})
                if translate.passes(data, out, record, d["kind"], LANG):
                    break
            results.append({"name": d["name"], "kind": d["kind"], "english": d["english"], "tries": tries,
                            "passed": translate.passes(data, out, record, d["kind"], LANG), "cost": round(cost, 6)})
    return results


def why(attempt: dict) -> str:
    """Which check a failed try failed: the check without AI, or the review."""
    return "check" if attempt["check"] != "ok" else "review"


def report(results: list[dict]) -> str:
    n = len(results)
    first = sum(1 for r in results if r["passed"] and len(r["tries"]) == 1)
    passed = sum(1 for r in results if r["passed"])
    failed_first = Counter(why(r["tries"][0]) for r in results if not (r["passed"] and len(r["tries"]) == 1))
    pct = lambda k: f"{k} of {n} ({100 * k / max(n, 1):.0f}%)"
    lines = [f"# Translation prompt {translate.VERSION}: {n} summaries", "",
             f"- Passed the first time: {pct(first)}",
             f"- Passed after the correction: {pct(passed)}",
             f"- First tries that failed: {failed_first['check']} the check, {failed_first['review']} the review",
             f"- Cost: ${sum(r['cost'] for r in results):.2f}", "",
             "## Shown in English after both tries", ""]
    for r in results:
        if not r["passed"]:
            last = r["tries"][-1]
            lines.append(f"- **{r['name']}** ({why(last)}): {last['check'] if last['check'] != 'ok' else last['review']}")
    lines += ["", "## Corrected on the second try", ""]
    for r in results:
        if r["passed"] and len(r["tries"]) > 1:
            first_try = r["tries"][0]
            lines.append(f"- **{r['name']}** ({why(first_try)}): "
                         f"{first_try['check'] if first_try['check'] != 'ok' else first_try['review']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    made = sub.add_parser("build", help="write the set from a network repository's town")
    made.add_argument("network", type=Path)
    made.add_argument("town")
    parser.add_argument("--only", help="the one summary of the set to run (its name)")
    parser.add_argument("--json", type=Path, help="also save every result here")
    args = parser.parse_args(argv)
    if args.command == "build":
        SET.write_text(json.dumps(build(args.network, args.town), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {SET}")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the set is run against the real model.")
    import anthropic
    results = evaluate(json.loads(SET.read_text(encoding="utf-8")), anthropic.Anthropic(), args.only)
    print(report(results))
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
