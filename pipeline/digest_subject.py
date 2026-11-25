"""The weekly digest's top story, and its email's subject and preheader, chosen and written by a language model.

An issue of the digest (pipeline/digest.py) is built the Saturday before its Sunday send. While it's built and not
yet sent, this step gathers what the issue may lead with, as the site shows it (candidates(): the week's
meetings by their agendas, and the recent meetings whose decisions came in), and asks the model once to pick the one
most residents would want to know about and to write the subject and preheader from that candidate's own words. The
answer is kept in data/digest/<Monday>.json, with what it was asked (a fingerprint of the candidates), so the step
asks again only when the candidates change, at most MAX_TRIES times an issue; an answer that fails the check doesn't
replace the last one that passed. The build uses an answer only where problems() finds nothing wrong with it
(subject()): a candidate picked, a subject short enough, and every number, name, day and outcome in it in the
candidate's words. Otherwise the subject is the week's lead decision as a headline (digest.headline()). The prompt and
the check are both here, so a change to either runs the prompt's evaluation set (pipeline/evaluate_digest.py).

It runs after the summaries in each daily run (pipeline/update.py, town.yml), and again after a batch of them is
collected (pipeline/network.py), for a town whose digest is emailed ([digest] signup). It does nothing outside an
issue's Saturday and Sunday morning (asking_now(), before it reads anything), so it asks about one issue a week, a
request or two of the summaries' model
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
import re
import sys
from datetime import date, datetime, time, timedelta
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
# Where the answers are kept, in the town's data folder.
SUBJECTS_DIR = "digest"

# ---- The candidates, and the check of an answer --------------------------------------------------------------------
#
# The model chooses each issue's top story and writes its email's subject and preheader (the preview line an inbox
# shows beside the subject) from candidates(): the week's meetings to come, by their agenda's headline and items, and
# the recent meetings whose decisions came in, by their headline and decisions, all as the site already shows them.
# The build uses an answer only where problems() finds nothing wrong with it, so it says nothing the site doesn't.

# Candidates: a main board's and those with a public hearing first, then by day; and the strongest recent decisions.
AHEAD_CANDIDATES = 8
DECIDED_CANDIDATES = 6
AGENDA_ITEMS = 4
DECISION_TITLES = 5
# The longest a subject may be, after the site's name worker/digest.js puts before it, and a preheader.
SUBJECT_MAX = 60
PREHEADER_MAX = 90
# Words a preheader may start with, which no candidate has.
LEAD_INS = {"Also", "Plus", "Coming", "Last", "This", "Next", "Earlier"}
DAYS = {"Monday": ("Mon",), "Tuesday": ("Tue", "Tues"), "Wednesday": ("Wed",), "Thursday": ("Thu", "Thurs"),
        "Friday": ("Fri",), "Saturday": ("Sat",), "Sunday": ("Sun",)}
DAY_WORDS = {w for day, short in DAYS.items() for w in (day, *short)}
NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
# What a decision's outcome is said with: a subject or preheader that says one must find the same in the candidates'
# words ("denied" where the decision was approved is caught; "approved" is the outcome factcheck.OUTCOMES names).
OUTCOME = re.compile(r"\b(approv|adopt|pass(?=e[sd]\b)|den(?=y|ie|ial)|reject|fail|tabl|refer|continu|postpon|grant|"
                     r"award|ratif|confirm|overr|veto|withdr|accept|appoint|elect|revok|suspen)\w*", re.I)


def live_meetings(issue: dict) -> list[dict]:
    """The week's meetings that take place as listed: not cancelled, postponed, or taken off the town's listing."""
    return [m for m in issue["meetings"] if m["status"] not in ("cancelled", "postponed") and m.get("listed", True)]


def candidates(issue: dict, main_boards: list[str]) -> list[dict]:
    """What an issue's subject may be about, each with an id for the model to pick it by and its page's address,
    which identifies it from one build to the next: the week's meetings with an agenda's headline (AHEAD_CANDIDATES),
    a main board's (main_boards) and those with a public hearing first, then by day and time; and the recent meetings
    whose decisions came in (issue["decided"], from digest.decided()), the strongest first (DECIDED_CANDIDATES). Each
    carries the words the site shows for it, which are all the model is given, and all a subject's facts may come
    from."""
    ahead = [m for m in live_meetings(issue) if m.get("preview_line") and m.get("preview_from", "agenda") == "agenda"]
    ahead.sort(key=lambda m: (m["body"] not in main_boards, not m.get("public_hearing"), m["date"], m.get("start_time") or ""))
    found = []
    for n, m in enumerate(ahead[:AHEAD_CANDIDATES], 1):
        found.append({
            "id": f"A{n}", "kind": "ahead", "url": m["url"], "board": digest.meeting_name(m), "date": m["date"],
            "day": date.fromisoformat(m["date"]).strftime("%A"), "time": m.get("start_time"),
            "main_board": m["body"] in main_boards, "hearing": bool(m.get("public_hearing")), "line": m["preview_line"],
            "items": [str(i) for i in ((m.get("preview") or {}).get("items") or [])[:AGENDA_ITEMS]],
        })
    recent = [e for e in issue["decided"]["entries"] if e["recent"] and e["main"]]
    for n, e in enumerate(recent[:DECIDED_CANDIDATES], 1):
        m = e["meeting"]
        found.append({
            "id": f"D{n}", "kind": "decided", "url": m["url"], "board": e["name"], "date": m["date"],
            "day": date.fromisoformat(m["date"]).strftime("%A"), "main_board": e["main_board"], "source": e["source"],
            "line": e["line"] or "",
            "decisions": [f"{getattr(d, 'outcome', None) or 'decided'}: {getattr(d, 'title', None) or d}"
                          for d in e["main"][:DECISION_TITLES]],
        })
    return found


def when_words(c: dict) -> list[str]:
    """A candidate's day, date and time in the ways a subject may give them: "Tuesday", "November 17", "Nov 17",
    "6:30 PM", "6:30"."""
    day = date.fromisoformat(c["date"])
    words = [c["day"], f"{day:%B} {day.day}", f"{day:%b} {day.day}"]
    if c.get("time"):
        at = time.fromisoformat(c["time"])
        words += [f"{at.hour % 12 or 12}:{at.minute:02d} {'PM' if at.hour >= 12 else 'AM'}", f"{at.hour % 12 or 12}"]
    return words


def candidate_words(c: dict) -> str:
    """Everything a candidate says, which a subject about it may use."""
    return " ".join([c["board"], *when_words(c), c["line"], *c.get("items", []), *c.get("decisions", [])])


def ungrounded(text: str, source: str, days: set[str]) -> list[str]:
    """What in text source doesn't say, each as text has it: a number that isn't one of source's numbers whole ("2"
    or "2.4" from "$2,383,850" is none of them); a capitalized word source doesn't have, with its capitals (a
    sentence's first word, or a preheader's after its lead-in, in any case); a day not among days; and an outcome
    (OUTCOME: approved, denied, tabled, ...) source never says."""
    missing = []
    numbers = set(NUMBER.findall(source))
    missing += [n for n in NUMBER.findall(text) if n not in numbers]
    for match in WORD.finditer(text):
        word = re.sub(r"['’]s$", "", match.group()).rstrip("'’-")
        if word in DAY_WORDS:
            if word not in days:
                missing.append(word)
            continue
        if not word[0].isupper() or word in LEAD_INS:
            continue
        before = text[:match.start()].rstrip()
        starts = not before or before[-1] in ":;.!?"
        if not re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", source, re.I if starts else 0):
            missing.append(word)
    said = {m.lower() for m in OUTCOME.findall(source)}
    missing += [m.group() for m in OUTCOME.finditer(text) if m.group(1).lower() not in said]
    return missing


def days_of(cands: list[dict]) -> set[str]:
    return {w for c in cands for w in (c["day"], *DAYS.get(c["day"], ()))}


def problems(answer: dict, cands: list[dict]) -> list[str]:
    """What's wrong with a model's answer ({"pick", "subject", "preheader"}) for these candidates, one line each;
    empty when the build may use it. The subject is checked against the candidate it picked, the preheader against
    every candidate (it may name a second story)."""
    out = []
    pick = next((c for c in cands if c["id"] == answer.get("pick")), None)
    subject, preheader = str(answer.get("subject") or "").strip(), str(answer.get("preheader") or "").strip()
    if not pick:
        out.append(f"it picked {answer.get('pick')!r}, which isn't a candidate")
    if not subject:
        out.append("it has no subject")
    if len(subject) > SUBJECT_MAX:
        out.append(f"its subject is {len(subject)} characters, past {SUBJECT_MAX}")
    if len(preheader) > PREHEADER_MAX:
        out.append(f"its preheader is {len(preheader)} characters, past {PREHEADER_MAX}")
    for name, text in (("subject", subject), ("preheader", preheader)):
        if "…" in text or "..." in text or "\n" in text:
            out.append(f"its {name} is cut off or runs over a line")
    if pick:
        out += [f"its subject's {w!r} isn't in what the site says of {pick['id']}"
                for w in ungrounded(subject, candidate_words(pick), days_of([pick]))]
    every = " ".join(candidate_words(c) for c in cands)
    out += [f"its preheader's {w!r} isn't in what the site says of any candidate"
            for w in ungrounded(preheader, every, days_of(cands))]
    return out


def stored(data_dir: Path, issue: dict) -> dict | None:
    """The model's answer for an issue, as run() kept it, or None."""
    try:
        return json.loads(record_path(data_dir, issue).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def subject(issue: dict, main_boards: list[str], record: dict | None, now: datetime) -> dict:
    """An issue's subject, preheader, and top story ("pick", a candidate, or None), and whether a model wrote them
    ("written"). The model's answer (record, from stored()) is used where it has a subject and, for an issue not yet
    due, its candidate is still one of the issue's (by its page's address) and problems() finds nothing wrong with it
    there; an issue already due keeps the subject it was sent with. Otherwise the subject is the week's lead decision
    as a headline (digest.decided()'s lead_title, or None for the week's date), and the email makes its own
    preheader."""
    cands = candidates(issue, main_boards)
    if record and record.get("subject") and record.get("pick"):
        if now >= issue["send_at"]:
            return {"subject": record["subject"], "preheader": record.get("preheader") or None, "pick": record["pick"],
                    "written": True}
        current = next((c for c in cands if c["url"] == record["pick"].get("url")), None)
        if current:
            answer = {"pick": current["id"], "subject": record["subject"], "preheader": record.get("preheader")}
            if not problems(answer, cands):
                return {"subject": record["subject"], "preheader": record.get("preheader") or None, "pick": current,
                        "written": True}
    lead = issue["decided"]["lead"]
    pick = next((c for c in cands if lead and c["url"] == lead["meeting"]["url"]), None)
    return {"subject": issue["decided"]["lead_title"], "preheader": None, "pick": pick, "written": False}


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
    return data_dir / SUBJECTS_DIR / f"{issue['monday']}.json"


def asking_now(now: datetime) -> bool:
    """Whether now is between an issue's first build and its send: from the Saturday before its Sunday (that Sunday,
    for an issue before digest.MORNING_FROM) to the Sunday's send time. Checked before the town's meetings are read,
    so the step costs nothing the rest of the week."""
    sunday = now.date() + timedelta(days=(6 - now.weekday()) % 7)
    return datetime.combine(digest.last_day(sunday), time(0), now.tzinfo) <= now < digest.send_at(sunday, now.tzinfo)


def run(config: dict, data_dir: Path, client, now: datetime) -> str:
    """Ask about the coming issue, if its candidates changed. Returns what was done, a line for the run's log."""
    if not asking_now(now):
        return "no issue waiting to be sent"
    settings = summarize.summary_settings(config)
    missing = summarize.missing_prices(settings)
    if missing:
        return f"skipped: {missing}"
    issue, boards = coming_issue(config, data_dir, now)
    if not issue:
        return "no issue waiting to be sent"
    cands = candidates(issue, boards)
    if not cands:
        return f"the issue of {issue['monday']} has nothing to lead with"
    model = settings["model"]
    asked = fingerprint(model, cands)
    path = record_path(data_dir, issue)
    before = stored(data_dir, issue) or {}
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
    found = found or problems(answer, cands)
    pick = next((c for c in cands if c["id"] == answer.get("pick")), None)
    record = {"monday": issue["monday"], "version": VERSION, "model": model, "input": asked, "tries": tries + 1,
              "generated_at": now.isoformat(timespec="seconds"), "answer": answer, "problems": found}
    if not found:
        record.update(subject=answer["subject"].strip(), preheader=answer["preheader"].strip(), pick=pick)
    else:
        # The last answer that passed stays: the build checks it again against the issue as it is (subject()).
        record.update({k: before[k] for k in ("subject", "preheader", "pick") if k in before})
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
