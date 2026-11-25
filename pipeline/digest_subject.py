"""The weekly digest's top story, and its email's subject and preheader, chosen and written by a language model.

An issue of the digest (pipeline/digest.py) is built the Saturday before its Sunday send. While it's built and not
yet sent, this step gathers what the issue may lead with, as the site shows it (digest.candidates(): the week's
meetings by their agendas, and the recent meetings whose decisions came in), and asks the model once to pick the one
most residents would want to know about and to write the subject and preheader from that candidate's own words. The
answer is kept in data/digest/<Monday>.json, with what it was asked (a fingerprint of the candidates), so the step
asks again only when the candidates change, at most MAX_TRIES times an issue. The build uses an answer only where
digest.problems() finds nothing wrong with it: a candidate picked, a subject short enough, and every number and name
in it in the candidate's words. Otherwise the subject is the week's lead decision as a headline (digest.headline()).

It runs after the summaries in each daily run (pipeline/update.py, town.yml), and again after a batch of them is
collected (pipeline/network.py), for a town whose digest is emailed ([digest] signup). It does nothing outside an
issue's Saturday and Sunday morning, so it asks about one issue a week, a request or two of the summaries' model
([summaries] model, at its prices), written to the town's cost log as kind "digest" (pipeline/cost_log.py). Needs
ANTHROPIC_API_KEY; without it, the step is skipped, and the subject is the lead decision's headline.

Usage:
    python -m pipeline.digest_subject [--town gloucester]
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import digest, i18n, summarize
from pipeline.config import DATA_DIR, DEFAULT_TOWN, load_config
from pipeline.files import write_atomic

# The prompt's version: an answer made with another is asked again (and test_prompt_versions.py pins its words).
VERSION = 1
# The most an issue is asked about: its candidates can change as the Saturday run and the batch it sends are
# collected, but an issue's subject isn't worth more requests than that.
MAX_TRIES = 3
MAX_TOKENS = 600

SYSTEM = """You choose the top story of a town's weekly email about its public meetings, and write the email's \
subject line and preheader (the preview text an inbox shows beside the subject). The email lists the week's \
meetings and what recent meetings decided; the subject decides whether a resident opens it.

Pick the one candidate most residents would want to know about: what changes their taxes, the town's spending, \
schools, housing, zoning, streets, public safety or services. When two matter about as much, prefer a meeting to \
come, where residents can still speak or watch a vote (a public hearing, the governing body's vote), over a \
decision already made. Prefer a decision over a discussion, and the governing body or school board over a \
committee, unless the committee's matter is clearly bigger.

Rules for the subject and the preheader:
- Use only what the candidate's text says. Copy every number, amount, address and name exactly as written: you may \
leave one out, but never round, abbreviate, convert or change it ("$2,383,850" stays "$2,383,850", never "$2.4M").
- Say nothing the text doesn't: no outcome it doesn't state, no "controversial", "big" or "finally".
- Plain, specific words, as a local newspaper's headline: no question, exclamation, emoji, quotes or clickbait.
- For a meeting to come, name the board, short if you like ("Council" for "City Council"), and its day, \
abbreviated (Mon, Tue, Wed, Thu, Fri, Sat, Sun): "Council hearing Tue on FY27 tax rates". For a decision, say what \
was decided, as its outcome says: "Council set residential tax factor at 1.75".
- Never cut a phrase short, end with "..." or a period, or run past the length allowed.

The subject is at most 60 characters. The preheader is at most 90 characters and tells the second story, from \
another candidate, starting with "Also:", or "Coming up:" for a meeting to come or "Last week:" for a decision when \
it's not the subject's kind. With only one candidate, the preheader says more of that one, without a lead-in."""

PROMPT = """The town is {town}. The email is sent {send}.

Candidates, as the town's site describes them:

{candidates}

Answer with the id of the candidate you pick, the subject, and the preheader."""

SCHEMA = {
    "type": "object",
    "properties": {"pick": {"type": "string"}, "subject": {"type": "string"}, "preheader": {"type": "string"}},
    "required": ["pick", "subject", "preheader"],
    "additionalProperties": False,
}


def describe(c: dict) -> str:
    """A candidate as the prompt lists it."""
    if c["kind"] == "ahead":
        head = [c["id"], "meeting to come", c["board"], c["day"] + (f" at {c['time']}" if c.get("time") else "")]
        if c["main_board"]:
            head.append("the town's main board")
        if c["hearing"]:
            head.append("a public hearing on its agenda")
        lines = [" · ".join(head), f"  Agenda: {c['line']}"]
        if c.get("items"):
            lines.append("  Items: " + "; ".join(c["items"]))
    else:
        head = [c["id"], "decided", c["board"], f"met {c['day']}, {c['date']}", f"from its {c['source']}"]
        if c["main_board"]:
            head.append("the town's main board")
        lines = [" · ".join(head)]
        if c["line"]:
            lines.append(f"  Headline: {c['line']}")
        lines.append("  Decisions: " + "; ".join(c["decisions"]))
    return "\n".join(lines)


def request(model: str, town: str, send: str, cands: list[dict]) -> dict:
    """The request for an issue's subject."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": PROMPT.format(
            town=town, send=send, candidates="\n\n".join(describe(c) for c in cands))}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }


def fingerprint(model: str, cands: list[dict]) -> str:
    """What an issue was asked: the prompt's version, the model, and the candidates."""
    text = json.dumps({"version": VERSION, "model": model, "candidates": cands}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def coming_issue(config: dict, data_dir: Path, now: datetime) -> tuple[dict | None, list[str]]:
    """The issue built and not yet due (digest.issues()), with what it says was decided (digest.decided()), and the
    town's main boards; None outside an issue's Saturday and Sunday morning. Its meetings are read as the build
    reads them (build_site.load_meetings()), without the warnings the build prints."""
    from pipeline import build_site

    boards = build_site.main_boards(config)
    with i18n.use("en", build_site.town_kind(config, data_dir)), contextlib.redirect_stdout(io.StringIO()):
        meetings = build_site.load_meetings(data_dir, now.date(), config.get("summaries", {}).get("model"), [],
                                            config.get("meetings", {}).get("corrections", []),
                                            config.get("summaries", {}).get("withheld", []), config["town"]["name"])
    due = [i for i in digest.issues(meetings["all"], meetings["tracking_since"], now.date(), now.tzinfo)
           if i["send_at"] > now]
    if not due:
        return None, boards
    issue = due[-1]
    issue["decided"] = digest.decided(issue, boards)
    return issue, boards


def record_path(data_dir: Path, issue: dict) -> Path:
    return data_dir / digest.SUBJECTS_DIR / f"{issue['monday']}.json"


def run(config: dict, data_dir: Path, client, now: datetime) -> str:
    """Ask about the coming issue, if its candidates changed. Returns what was done, a line for the run's log."""
    settings = summarize.summary_settings(config)
    missing = summarize.missing_prices(settings)
    if missing:
        return f"skipped: {missing}"
    issue, boards = coming_issue(config, data_dir, now)
    if not issue:
        return "no issue waiting to be sent"
    cands = digest.candidates(issue, boards)
    if not cands:
        return f"the issue of {issue['monday']} has nothing to lead with"
    model = settings["model"]
    asked = fingerprint(model, cands)
    path = record_path(data_dir, issue)
    before = digest.stored(data_dir, issue) or {}
    if before.get("input") == asked:
        return f"the issue of {issue['monday']} was asked about these candidates already"
    tries = before.get("tries", 0)
    if tries >= MAX_TRIES:
        return f"the issue of {issue['monday']} was asked {tries} times already"
    log = summarize.open_log(data_dir, settings, now, config)
    try:
        response = client.messages.create(**request(model, config["town"]["name"],
                                                     issue["send_at"].strftime("%A, %B %-d, at %-I:%M %p"), cands))
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        log.request(response, "digest", summarize.cost(usage, settings), model=model, usage=usage,
                    issue=issue["monday"])
    finally:
        log.close()
    text = next((b.text for b in response.content if b.type == "text"), "")
    answer, found = {}, []
    if response.stop_reason != "end_turn":
        found = [f"it stopped early ({summarize.stop_reason(response)})"]
    else:
        try:
            answer = json.loads(text)
        except ValueError:
            found = ["its answer isn't JSON"]
    found = found or digest.problems(answer, cands)
    pick = next((c for c in cands if c["id"] == answer.get("pick")), None)
    record = {"monday": issue["monday"], "version": VERSION, "model": model, "input": asked, "tries": tries + 1,
              "generated_at": now.isoformat(timespec="seconds"), "answer": answer, "problems": found}
    if not found:
        record.update(subject=answer["subject"].strip(), preheader=answer["preheader"].strip(), pick=pick)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    if found:
        return f"the issue of {issue['monday']}: the answer wasn't used ({'; '.join(found)})"
    return f"the issue of {issue['monday']}: {record['subject']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    if not args.town:
        raise SystemExit("Pass --town or set TOWN.")
    config = load_config(args.town)
    if not digest.signup(config) or "meetings" not in config or "summaries" not in config:
        print("Digest subject: the town's digest isn't emailed ([digest] signup) or summarized ([summaries]), "
              "so there's nothing to write.")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Digest subject: skipped, no ANTHROPIC_API_KEY.")
        return 0
    import anthropic

    now = datetime.now(ZoneInfo(config["site"]["timezone"]))
    try:
        print(f"Digest subject: {run(config, args.data, anthropic.Anthropic(), now)}")
    except anthropic.APIError as e:
        # A warning, not a failure: the email goes out all the same, with the lead decision's headline as its subject.
        print(f"::warning::Digest subject: the request failed ({type(e).__name__}); the subject is the lead decision's headline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
