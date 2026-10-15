"""The fact check replayed on real summaries and their documents' text, without AI.

pipeline/factcheck.py decides what of each summary the site shows. evals/factcheck/ holds a sample
of real summaries from the network's towns, each with its PDF's page text as factcheck.pages()
reads it (sample.json.gz), and what the check made of each when its results were last accepted
(expected.json): the result, the problems, and what factcheck.shown() keeps of the headline,
the summary, and each decision or agenda item. The tests replay the check on every summary
(tests/test_factcheck_replay.py), so a change to the check that shows or hides anything fails
them until expected.json is rewritten, and the diff of expected.json is the change's effect.

    python -m pipeline.evaluate_factcheck                    # what changed, entry by entry (exit status 1 if anything did)
    python -m pipeline.evaluate_factcheck --update           # accept it: rewrite expected.json
    python -m pipeline.evaluate_factcheck build <network repository> [--resample]

build takes each summary as the network has it now, downloads its PDF (from the town's document
storage, or the city's link), checks its sha256, and keeps only the pages' text; the summaries
are those already in the sample, or with --resample (or no sample yet) a new sample: the hard
cases in HARD_CASES, then at least one summary of each town's agendas and minutes and of each of
its meeting platforms, and enough of each prompt version, scan, and result (failed, weak, and
the kind of problem that failed it), filled up to about ENTRIES decisions and agenda items. The
check is given the words a town's run gives it: the lowercase words of every town's summaries
(translate.town_words()), saved with the sample. Nothing is sent to a model.
"""

from __future__ import annotations

import argparse
import functools
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

from pipeline import factcheck, summarize, translate

SAMPLE = Path(__file__).resolve().parent.parent / "evals" / "factcheck" / "sample.json.gz"
EXPECTED = SAMPLE.with_name("expected.json")
# Summaries the check has got wrong or nearly wrong, kept in every sample: (town, the start of the
# document's sha256).
HARD_CASES = [
    # Text extracted as glyph numbers ("/i255/1/2/3"), not words.
    ("lowell-ma", "3216e0edd7"),
    # Hearings "recessed ... until a date certain", given the outcome continued.
    ("waltham-ma", "04167a80ee"),
    # "(cont. to October 7, 2026)": continued, abbreviated.
    ("gloucester-ma", "4b99709440"),
    # School policies on "HOLD", given the outcome continued.
    ("methuen-ma", "494407f791"),
    # A motion that "did not get a second: Failed".
    ("portland-me", "346a2e5be3"),
    # An amount the PDF's text breaks up: "$ 575, 578. 50".
    ("bangor-me", "b3a93e9e3b"),
]
# About how many decisions and agenda items a new sample has in all.
ENTRIES = 300
# A new sample's summaries, beyond the hard cases: at most this many entries, and a PDF at most this big.
MAX_ENTRIES, MAX_BYTES = 15, 3_000_000
# How many summaries of each kind a new sample has at least, beyond one of each town's kinds and platforms.
AT_LEAST = {"prompt": 2, "source ai": 5, "source partial": 3, "source none": 1, "result failed": 12,
            "result weak": 5, "problem": 2}
# What isn't kept of a summary's record: what the check doesn't read, and its own result, kept apart.
DROPPED = {"plain_text", "usage", "cost", "transcript_usage", "transcript_cost", "fact_check"}


def platform(meeting: dict) -> str:
    """Where a meeting's documents come from: its listing's source ("agendacenter", "civicclerk",
    ...), "dnn" for a DotNetNuke calendar's, "calendar" for a CivicPlus calendar's."""
    return meeting.get("source") or ("dnn" if meeting["id"].startswith("dnn-") else "calendar")


def entries(record: dict) -> int:
    return len(record.get("decisions") or record.get("items") or [])


def candidates(network: Path) -> list[dict]:
    """Every summary a town's run checks (a meeting's latest agenda and minutes), from a network
    repository, in order of its document's sha256."""
    import tomllib
    out = []
    for town in sorted(p for p in (network / "towns").iterdir() if (p / "data" / "summaries").is_dir()):
        config = tomllib.loads(next((town / "config").glob("*.toml")).read_text(encoding="utf-8"))
        storage = config.get("storage") or {}
        # Each document's own listing: a meeting listed in more than one place has every listing's documents.
        listed = json.loads((town / "data" / "meetings" / "meetings.json").read_text(encoding="utf-8"))
        listing = {d["sha256"]: m for m in listed.values() for field in ("agendas", "minutes") for d in m.get(field) or []}
        for kind, meeting, doc, record in summarize.summarized_documents(town / "data"):
            if record.get("is_minutes") is False:
                continue
            file_url = (f"{storage['public_url'].rstrip('/')}/{storage.get('prefix', town.name).strip('/')}/"
                        f"{summarize.KINDS[kind]['folder']}/{doc['file']}") if storage.get("public_url") else None
            out.append({"town": town.name, "kind": kind, "platform": platform(listing.get(doc["sha256"], meeting)),
                        "body": meeting["body"], "date": meeting["date"], "sha256": doc["sha256"],
                        "bytes": doc.get("bytes") or 0, "file_url": file_url, "source_url": doc["source_url"],
                        "record": record})
    return sorted(out, key=lambda c: c["sha256"])


def strata(c: dict) -> set[str]:
    """What a summary stands for in a sample."""
    fc = c["record"].get("fact_check") or {}
    hard = {p["kind"] for p in fc.get("problems", []) if p["kind"] not in factcheck.NOTED}
    return ({f"town {c['town']} {c['kind']}", f"platform {c['town']} {c['platform']}",
             f"prompt {c['kind']} {c['record'].get('prompt_version')}", f"source {fc.get('source')}",
             f"result {fc.get('result')}"}
            | {f"problem {k}" for k in hard if fc.get("result") == "failed"})


def choose(found: list[dict]) -> list[dict]:
    """A new sample: the hard cases, then a summary for each stratum short of what it needs, then
    more until there are about ENTRIES entries, each in order of sha256."""
    chosen = [c for town, sha in HARD_CASES for c in found if c["town"] == town and c["sha256"].startswith(sha)]
    rest = [c for c in found if c not in chosen and entries(c["record"]) <= MAX_ENTRIES and c["bytes"] <= MAX_BYTES]
    needed = {}
    for s in sorted({s for c in found for s in strata(c)}):
        if s.startswith(("town ", "platform ")):
            needed[s] = 1
        elif s.split()[0] in ("prompt", "problem"):
            needed[s] = AT_LEAST[s.split()[0]]
        elif s in AT_LEAST:
            needed[s] = AT_LEAST[s]
    for s, n in needed.items():
        for c in rest:
            if sum(s in strata(x) for x in chosen) >= n:
                break
            if s in strata(c) and c not in chosen:
                chosen.append(c)
    for c in rest:
        if sum(entries(x["record"]) for x in chosen) >= ENTRIES:
            break
        if c not in chosen and entries(c["record"]) >= 3:
            chosen.append(c)
    return sorted(chosen, key=lambda c: (c["town"], c["kind"], c["date"], c["sha256"]))


@functools.lru_cache(maxsize=1)
def client():
    from pipeline.http import PoliteClient
    return PoliteClient(user_agent="publick-engine (+https://github.com/publick-org/publick-engine)", delay=1.0)


def download(url: str) -> bytes:
    return client().get(url).content


def document(c: dict, get=download) -> bytes:
    """A summary's PDF, the one it was made from (by sha256): from the town's document storage,
    or the city's link."""
    tried = []
    for url in filter(None, (c["file_url"], c["source_url"])):
        try:
            pdf = get(url)
        except Exception as e:
            tried.append(f"{url}: {e}")
            continue
        if hashlib.sha256(pdf).hexdigest() == c["sha256"]:
            return pdf
        tried.append(f"{url}: another document")
    raise SystemExit(f"{c['town']} {c['sha256'][:12]}: no copy of its PDF\n  " + "\n  ".join(tried))


def sample_item(c: dict, pdf: bytes) -> dict:
    """What the sample keeps of a summary: where it's from, its record as the check reads it, the
    check's result as the network has it, and its PDF's pages' text."""
    record = {k: v for k, v in c["record"].items() if k not in DROPPED}
    if record.get("transcript_source") != "ai":
        # The PDF's own text, which the check reads from the pages instead.
        record.pop("transcript", None)
    return {"town": c["town"], "kind": c["kind"], "platform": c["platform"], "body": c["body"], "date": c["date"],
            "sha256": c["sha256"], "file_url": c["file_url"], "source_url": c["source_url"], "record": record,
            "network_fact_check": c["record"].get("fact_check"), "pages": factcheck.pages(pdf)}


def build(network: Path, resample: bool, get=download) -> dict:
    """The sample, from a network repository."""
    found = candidates(network)
    if resample or not SAMPLE.exists():
        chosen = choose(found)
    else:
        keys = [(i["town"], i["sha256"]) for i in load()["items"]]
        by_key = {(c["town"], c["sha256"]): c for c in found}
        gone = [k for k in keys if k not in by_key]
        if gone:
            print("No longer in the network (left out): " + ", ".join(f"{t} {s[:12]}" for t, s in gone), file=sys.stderr)
        chosen = [by_key[k] for k in keys if k in by_key]
    items = []
    for c in chosen:
        print(f"{c['town']} {c['kind']} {c['date']} {c['sha256'][:12]}...", file=sys.stderr, flush=True)
        items.append(sample_item(c, document(c, get)))
    # Any town's words are every town's, in a network.
    town = next(t for t in sorted((network / "towns").iterdir()) if (t / "data" / "summaries").is_dir())
    return {"about": "Summaries from the network's towns with their PDFs' page text, for pipeline/evaluate_factcheck.py.",
            "words": sorted(translate.town_words(town / "data")), "items": items}


def load() -> dict:
    return json.loads(gzip.decompress(SAMPLE.read_bytes()))


def key(item: dict) -> str:
    return f"{item['town']} {item['sha256'][:12]}"


def problem(p: dict) -> str:
    """A problem in a line: where it is, its kind, and what."""
    return f"{p['field']}{' ' + str(p['entry']) if 'entry' in p else ''}: {p['kind']} {p['what']}"


def in_order(text: str) -> list:
    """A sort key that puts "decisions 2" before "decisions 10"."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", text)]


def kept(checked: dict, kind: str, field: str) -> list[int]:
    """The entries of a list field (counting from 1) factcheck.shown() keeps: it's given each
    entry's number in place of its text."""
    numbered = {**checked, field: [f"#{i + 1}" for i in range(len(checked.get(field) or []))]}
    out = factcheck.shown(numbered, checked, kind)
    return [int(t[1:]) for t in out.get(field) or []]


def replay(item: dict, words: frozenset) -> dict:
    """The check of one summary of the sample, and what the site would show of it."""
    kind, record = item["kind"], item["record"]
    fc = factcheck.check(record, kind, item["pages"], words)
    checked = {**record, "fact_check": fc}
    out = factcheck.shown(checked, checked, kind)
    shown, hidden = {}, []
    for field in factcheck.FIELDS[kind]:
        value = record.get(field)
        if isinstance(value, list):
            numbers = kept(checked, kind, field)
            hidden += [f"{field} {i + 1}" for i in range(len(value)) if i + 1 not in numbers]
            # Only what differs from the summary's own entry (a vote count taken out).
            shown[field] = {str(n): t for n, t in zip(numbers, out.get(field) or []) if t != value[n - 1]}
        else:
            shown[field] = out.get(field) or ""
            before, after = len(factcheck.sentences(value or "")), len(factcheck.sentences(shown[field]))
            if value and not shown[field]:
                hidden.append(field)
            elif after < before:
                hidden.append(f"{field} ({before - after} of {before} sentences)")
    return {"town": item["town"], "document": item["sha256"], "kind": kind, "meeting": f"{item['body']}, {item['date']}",
            "result": fc["result"], "source": fc["source"],
            "problems": sorted((problem(p) for p in fc["problems"]), key=in_order), "hidden": hidden, "shown": shown}


def replay_all(sample: dict) -> dict:
    words = frozenset(sample["words"])
    return {key(item): replay(item, words) for item in sample["items"]}


def entry_text(item: dict, where: str) -> str:
    """The summary's own text at a place ("decisions 3", "headline")."""
    field, _, n = where.partition(" ")
    value = item["record"].get(field)
    if isinstance(value, list):
        return value[int(n) - 1] if n.isdigit() and int(n) <= len(value) else ""
    return value or ""


def why(result: dict, where: str) -> list[str]:
    """The problems at a place."""
    field, _, n = where.partition(" ")
    prefix = f"{field} {n.split()[0]}:" if n and n.split()[0].isdigit() else f"{field}:"
    return [p for p in result["problems"] if p.startswith(prefix)]


def differences(item: dict, expected: dict | None, actual: dict) -> list[str]:
    """What changed in one summary's check, for a person to read: each part now shown or hidden
    and why, then the other problems found or no longer found, and anything else shown otherwise."""
    head = f"{key(item)}, {item['kind']} of {actual['meeting']} ({item['file_url'] or item['source_url']})"
    if expected is None:
        return [f"{head}: not in expected.json"]
    lines = []
    if expected["result"] != actual["result"] or expected["source"] != actual["source"]:
        lines.append(f"  result: {expected['result']} ({expected['source']}) -> {actual['result']} ({actual['source']})")
    found, gone = list(actual["problems"]), list(expected["problems"])
    for p in actual["problems"]:
        if p in gone:
            gone.remove(p)
            found.remove(p)
    told = set()
    for where in [w for w in actual["hidden"] if w not in expected["hidden"]]:
        lines.append(f"  {where}: shown -> hidden: {entry_text(item, where)[:200]!r}")
        causes = why(actual, where)
        lines += [f"    because: {p}{'' if p in found else ' (found before too)'}" for p in causes]
        if not set(causes) & set(found):
            lines.append(f"    and the summary's result is now {actual['result']}")
        told |= set(causes)
    for where in [w for w in expected["hidden"] if w not in actual["hidden"]]:
        lines.append(f"  {where}: hidden -> shown: {entry_text(item, where)[:200]!r}")
        causes = [p for p in why(expected, where) if p in gone]
        lines += [f"    no longer: {p}" for p in causes]
        if not causes:
            lines.append(f"    its problems are still found, but the summary's result is now {actual['result']}")
        told |= set(causes)
    lines += [f"  problem found: {p}" for p in found if p not in told]
    lines += [f"  problem no longer found: {p}" for p in gone if p not in told]
    for field in actual["shown"]:
        if expected["shown"].get(field) != actual["shown"][field]:
            lines.append(f"  shown {field}: {json.dumps(expected['shown'].get(field))[:300]} -> "
                         f"{json.dumps(actual['shown'][field])[:300]}")
    return [head + ":", *lines] if lines else []


def compare(sample: dict, expected: dict, actual: dict) -> list[str]:
    """Every summary whose check changed, and every one expected that's no longer in the sample."""
    lines = []
    for item in sample["items"]:
        lines += differences(item, expected.get(key(item)), actual[key(item)])
    lines += [f"{k}: in expected.json, not in the sample" for k in expected if k not in actual]
    return lines


def against_network(sample: dict, actual: dict) -> list[str]:
    """The summaries whose check, replayed, isn't what the network has saved (checked by an
    earlier version of the check, or with fewer words)."""
    lines = []
    for item in sample["items"]:
        saved, now = item.get("network_fact_check") or {}, actual[key(item)]
        saved_problems = sorted((problem(p) for p in saved.get("problems", [])), key=in_order)
        if (saved.get("result"), saved.get("source"), saved_problems) != (now["result"], now["source"], now["problems"]):
            lines.append(f"{key(item)} ({item['kind']}, saved with version {saved.get('version')}): "
                         f"{saved.get('result')} -> {now['result']}")
            lines += [f"  only saved: {p}" for p in saved_problems if p not in now["problems"]]
            lines += [f"  only now: {p}" for p in now["problems"] if p not in saved_problems]
    return lines


def write_expected(actual: dict) -> None:
    EXPECTED.write_text(json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def summary(sample: dict, actual: dict) -> str:
    """The sample in a few lines: summaries and entries by town, platform, kind, source, and result."""
    from collections import Counter
    items = sample["items"]
    lines = [f"{len(items)} summaries, {sum(entries(i['record']) for i in items)} decisions and agenda items, "
             f"{sum(len(r['hidden']) for r in actual.values())} of their parts hidden"]
    for label, of in (("town", lambda i: i["town"]), ("platform", lambda i: i["platform"]), ("kind", lambda i: i["kind"]),
                      ("prompt", lambda i: f"{i['kind']} {i['record'].get('prompt_version')}"),
                      ("source", lambda i: actual[key(i)]["source"]), ("result", lambda i: actual[key(i)]["result"])):
        counted = Counter(of(i) for i in items)
        lines.append(f"  {label}: " + ", ".join(f"{k} {n}" for k, n in sorted(counted.items())))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    made = sub.add_parser("build", help="write the sample from a network repository, and its expected results")
    made.add_argument("network", type=Path)
    made.add_argument("--resample", action="store_true", help="choose the summaries again")
    parser.add_argument("--update", action="store_true", help="accept the check's results: rewrite expected.json")
    args = parser.parse_args(argv)
    if args.command == "build":
        sample = build(args.network, args.resample)
        SAMPLE.parent.mkdir(parents=True, exist_ok=True)
        SAMPLE.write_bytes(gzip.compress(json.dumps(sample, ensure_ascii=False).encode("utf-8"), mtime=0))
        actual = replay_all(sample)
        write_expected(actual)
        print(summary(sample, actual))
        print("\n".join(against_network(sample, actual)) or "Every summary's check is as the network saved it.")
        print(f"Wrote {SAMPLE} ({SAMPLE.stat().st_size // 1024} KB) and {EXPECTED}")
        return 0
    sample = load()
    actual = replay_all(sample)
    if args.update:
        write_expected(actual)
        print(f"Wrote {EXPECTED}")
        return 0
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    changed = compare(sample, expected, actual)
    print("\n".join(changed) if changed else f"No change: {summary(sample, actual)}")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
