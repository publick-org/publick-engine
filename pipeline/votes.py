"""Roll call votes, read from a minutes PDF's own text, without AI.

Read from the minutes of a body listed in the town's [officials] table (its
"board", or its name, is the meeting's board), and kept with the minutes'
summary record. A vote is kept only as the minutes record it: each group ("Yea", "Nay",
"Recused", ...) with its stated count and the members it names, checked
against the body's members (the town's [officials] list). A roll call whose
counts don't match its names, or that names someone who isn't a member, or
the same member twice, is kept as unchecked and never shown. A member the
minutes don't name in a vote is "not recorded", never absent or yes. A voice
or "unanimous" vote names no one, so it isn't a roll call here.

The minutes' layout depends on the software that made them (pdftext.STYLES),
so a reader is written once per style and used for every town on it.
Legistar's (Malden's City Council) reads:

    A motion was made by Councillor Sica, seconded by Councillor Simonelli, to grant ...
    The motion carried by the following vote:
    Yea: 10 - Colon Hayes, Condon, Crowe, Linehan, McDonald, Sica, Simonelli,
    Taylor, Winslow and Luong
    Nay: 1 - O'Malley

Nothing is shown on the sites yet. To check every vote read against its
minutes, before a town's votes are public:

    python -m pipeline.votes [--town malden]
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

from pipeline import pdftext
from pipeline.officials import slugify

# Bump when the reading rules change, so minutes are read again.
VERSION = 1

LABELS = ("Yea", "Nay", "Abstain", "Recused", "Absent", "Present", "Excused", "Non-Voting")
GROUP = re.compile(r"^(%s)\s*:\s*(\d+)\s*-\s*(.*)$" % "|".join(LABELS))
# A motion runs to the end of its sentence (not "Mr." or "St.").
MOTION = re.compile(r"A motion was made by\b.*?(?:(?<!\b(?:Mr|Ms|Dr|St|No))(?<!\bMrs|\bAve)\.(?=\s+[A-Z])|$)")
OUTCOME = re.compile(r"The motion (carried|failed|passed)\b[^.:]*?\bvote\s*[.:]?", re.I)
ITEM = re.compile(r"^(\d{1,4}-\d{2})\b")
SENTENCE = re.compile(r"[^.]*\.")


def members_for(config: dict, board: str) -> list[str]:
    """The members of the [officials] body whose meetings these are, or [] (no roll calls read)."""
    for body in config.get("officials", {}).get("bodies", []):
        if slugify(body.get("board", body.get("name", ""))) == slugify(board):
            return [m["name"] for m in body.get("members", []) if m.get("name")]
    return []


def current(record: dict, members: list[str]) -> bool:
    """Whether the record's votes (minutes only) were read by these rules, against these members."""
    return record.get("kind") != "minutes" or not members or (record.get("votes_version") == VERSION and record.get("votes_members") == members)


def read(record: dict, pdf: bytes, members: list[str]) -> dict:
    """The minutes' record with its roll calls, read against the members."""
    if not members or record.get("kind") != "minutes":
        return record
    return {**record, "votes": roll_calls(pdf, members), "votes_version": VERSION, "votes_members": members}


def fold(text: str) -> str:
    """Compare names without accents, curly quotes, case, or spacing."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def split_names(text: str) -> list[str]:
    text = re.sub(r",?\s+and\s+", ", ", text.strip().rstrip(","))
    return [n.strip() for n in text.split(",") if n.strip()]


def member_for(name: str, members: list[str]) -> str | None:
    """The member a surname in the minutes names: the one whose full name ends with it."""
    found = [m for m in members if fold(m) == fold(name) or fold(m).endswith(" " + fold(name))]
    return found[0] if len(found) == 1 else None


def _names_x(row: dict) -> float | None:
    """Where a group line's names start: the word after "Yea: 10 -"."""
    words = row["text"].split()
    for k, w in enumerate(words[:-1]):
        if w == "-" or w.endswith("-") and w[:-1].isdigit():
            return row["xs"][k + 1]
    return None


def roll_calls(pdf: bytes, members: list[str]) -> list[dict]:
    """Every roll call in a supported style's minutes, each checked against the members."""
    if pdftext.style(pdf) != "legistar":
        return []
    rows, _ = pdftext._drop_running(pdftext.lines(pdf))
    for r in rows:
        r["text"] = r["text"].strip()

    votes, item, before, i = [], None, [], 0
    while i < len(rows):
        text = rows[i]["text"]
        if ITEM.match(text):
            item, before = ITEM.match(text).group(1), []
        if not GROUP.match(text):
            before.append(text)
            i += 1
            continue
        # A roll call: its groups, one after another. A group's names wrap onto lines that
        # start where its first name does; anything else (the next item, a note, a header)
        # ends it.
        groups, quote, j = [], [], i
        while j < len(rows) and GROUP.match(rows[j]["text"]):
            row = rows[j]
            label, count, names = GROUP.match(row["text"]).groups()
            quote.append(row["text"])
            x, last, j = _names_x(row), row, j + 1
            while j < len(rows) and x is not None:
                k = _skip_page_top(rows, j, last)
                nxt = rows[k] if k < len(rows) else None
                if not nxt or abs(nxt["x"] - x) > 3 or GROUP.match(nxt["text"]) \
                        or (nxt["page"] == last["page"] and last["y"] - nxt["y"] > 1.8 * nxt["size"]):
                    break
                names += " " + nxt["text"]
                quote.append(nxt["text"])
                last, j = nxt, k + 1
            groups.append({"label": label, "count": int(count), "names": split_names(names)})
            k = _skip_page_top(rows, j, last)
            if k < len(rows) and GROUP.match(rows[k]["text"]):
                j = k
        # What was voted on, and how it came out: the text since the item began (or since the
        # last roll call), as the minutes word it.
        context = " ".join(before)
        motions = list(MOTION.finditer(context))
        motion = motions[-1] if motions else None
        outcome = OUTCOME.search(context, motion.end() if motion else 0)
        notes = SENTENCE.findall(context[outcome.end():]) if outcome else []
        votes.append(check({
            "item": item,
            "motion": motion.group(0).strip() if motion else None,
            "outcome": outcome.group(0).strip() if outcome else None,
            "result": outcome.group(1).lower() if outcome else None,
            "notes": [n.strip() for n in notes if n.strip()],
            "groups": groups,
            "quote": "\n".join(quote),
        }, members))
        before, i = [], j
    return votes


def _skip_page_top(rows: list[dict], j: int, last: dict) -> int:
    """j, or past the lines topping a new page that aren't at the vote's margins (a running
    header the cleanup kept because it's only on some pages)."""
    if j >= len(rows) or rows[j]["page"] == last["page"]:
        return j
    page, k = rows[j]["page"], j
    while k < len(rows) and rows[k]["page"] == page and k - j < 4 and not GROUP.match(rows[k]["text"]) \
            and all(abs(rows[k]["x"] - m) > 3 for m in (last["x"], _names_x(last) or -99)):
        k += 1
    return k if k < len(rows) and rows[k]["page"] == page and k - j < 4 else j


TALLY = re.compile(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b")


def check(vote: dict, members: list[str]) -> dict:
    """Each group's count matches its names, every name is one member, no member is named
    twice, and a tally the outcome states ("by a 10-1 vote") matches the groups."""
    problems, seen = [], set()
    tally = TALLY.search(vote.get("outcome") or "")
    if tally:
        counts = {g["label"]: g["count"] for g in vote["groups"]}
        stated = (int(tally.group(1)), int(tally.group(2)))
        if stated != (counts.get("Yea", 0), counts.get("Nay", 0)):
            problems.append(f"the outcome says {stated[0]}-{stated[1]}, the groups "
                            f"{counts.get('Yea', 0)}-{counts.get('Nay', 0)}")
    for g in vote["groups"]:
        if g["count"] != len(g["names"]):
            problems.append(f"{g['label']}: {g['count']} stated, {len(g['names'])} named")
        g["members"] = []
        for name in g["names"]:
            member = member_for(name, members)
            if not member:
                problems.append(f"{name!r} isn't one of the members")
            elif member in seen:
                problems.append(f"{member} is named twice")
            else:
                seen.add(member)
                g["members"].append(member)
    return {**vote, "checked": not problems, "problems": problems,
            "not_recorded": [m for m in members if m not in seen]}


def report(config: dict, data_dir: Path) -> str:
    """Every roll call read, meeting by meeting, as plain text for a person to check
    against the minutes: unchecked ones say why."""
    from pipeline.summarize import summarized_documents
    out, total, checked = [], 0, 0
    for kind, meeting, doc, record in summarized_documents(data_dir):
        if kind != "minutes" or not record.get("votes"):
            continue
        out.append(f"\n{meeting['date']} {meeting['body']}  {doc['source_url']}")
        for v in record["votes"]:
            total += 1
            checked += v["checked"]
            out.append(f"  {v['item'] or '(no item number)'}: {v['motion'] or '(motion not found)'}")
            out.append(f"    {v['outcome'] or '(outcome not found)'}")
            out.extend(f"    {line}" for line in v["quote"].splitlines())
            if v["checked"]:
                out.append("    checked" + (f"; not recorded: {', '.join(v['not_recorded'])}" if v["not_recorded"] else ""))
            else:
                out.append(f"    NOT CHECKED: {'; '.join(v['problems'])}")
    return f"{total} roll calls, {checked} checked" + "\n".join(out)


def main() -> int:
    from pipeline.config import DATA_DIR, DEFAULT_TOWN, load_config
    parser = argparse.ArgumentParser(description="Roll call votes read from the saved minutes, for review.")
    parser.add_argument("--town", default=DEFAULT_TOWN)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    print(report(load_config(args.town), args.data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
