"""The search's answers: the things a search can name, each shown above the results with where to find it.

A search for a street, a board, or an official gets a card for it first: "Sargent Street", with how
many meetings mention it and its 311 requests and permits, linked to the street lookup; "Conservation
Commission", with when it meets next, linked to its page; "Paul Lundberg", Mayor, linked to the Officials
page. The list is the town's own (the street lookup's index, the meetings' boards, the officials in its
config), written for each language to /search/answers.json, which the search page (site/static/js/search.js)
loads with the first search.

The search page matches a search against each answer's names, after reading both the same way (words(),
which the page's script repeats): letters without accents, street types written one way ("St", "Street"),
and the words of a question left out ("who is the mayor", "when does the city council meet"), which
it also leaves out of the search of the pages. An answer is shown when the search names all of it (its
words other than generic ones: "Main Street" in "pothole on main st") or is part of its name ("sargent").
The last word can be partly typed, and a longer word can be a letter or two off ("sargant").
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

from pipeline import streets as streets_mod

# The words of a question, and others that say nothing about what's searched for, in each language the site has,
# without accents (words()). A word that can be part of a name ("meeting", in "Town Meeting") isn't one.
STOP_WORDS = {
    "en": """a about an and any are at can could did do does for from how i in is it me my of on or our that the
             their there this to was were what when where which who whom why will with""".split(),
    "es": """a al algo alguien como con cuando cual cuales de del donde el en es esta este hay la las lo los mi
             mis o para por que quien quienes se sobre son su sus un una y""".split(),
}
# Words that alone name no one thing, without accents: a search for "committee" alone gets no answer, nor one for
# "street" (a street type, from streets.SUFFIXES).
GENERIC_WORDS = {
    "en": "board boards city commission committee committees department of subcommittee the town trustees".split(),
    "es": "comision comite de del departamento junta la municipal subcomite".split(),
}


def words(text: str, lang: str = "en") -> list[str]:
    """A name or a search as the search page compares them: lowercase, without accents or punctuation, street types
    written one way ("street" -> "st"), and without STOP_WORDS. search.js's words() does the same."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn").replace("'", "").replace("’", "")
    stop = set(STOP_WORDS.get(lang, ()))
    return [w for w in (suffixes().get(w, w) for w in re.split(r"[^a-z0-9]+", text)) if w and w not in stop]


def suffixes() -> dict[str, str]:
    """Each way of writing a street type, as streets.SUFFIXES has them, lowercase."""
    return {k.lower(): v.lower() for k, v in streets_mod.SUFFIXES.items()}


def street_answers(index: dict, prefix: str) -> list[dict]:
    """Each street the street lookup knows (build_site.street_index()), with its counts, linked to the lookup."""
    out = []
    for s in index["streets"].values():
        counts = {k: s.get(f"{k}_total", 0) for k in ("meetings", "requests", "permits")}
        if any(counts.values()):
            out.append({"k": "street", "n": s["name"], "u": f"{prefix}/streets/?q={quote(s['name'], safe='')}",
                        "c": {k: v for k, v in counts.items() if v}})
    return out


def board_answers(boards: list[dict], today: str, prefix: str) -> list[dict]:
    """Each board with meetings, with the day it meets next or last met, linked to its page. A board's name in
    English is a name of it too, on another language's site."""
    out = []
    for b in boards:
        answer = {"k": "board", "n": b["name"], "u": prefix + b["url"]}
        if b.get("name_en") and b["name_en"] != b["name"]:
            answer["a"] = [b["name_en"]]
        if b.get("next"):
            answer["next"] = b["next"]["date"]
        last = next((m["date"] for m in b["meetings"] if m["date"] < today), None)
        if last:
            answer["last"] = last
        out.append(answer)
    return out


def official_answers(officials: dict, prefix: str) -> list[dict]:
    """Each official on the Officials page, with their bodies, seats and roles, linked to their row. An official is
    found by name, and by what they are: a body of one ("Mayor"), or a role on a body ("City Council President")."""
    by_name: dict[str, dict] = {}
    for body in officials["bodies"]:
        for m in body["members"]:
            what = ", ".join([body["name"]] + ([m["seat"]] if body.get("show_seat") and m.get("seat") else [])
                             + ([m["role"]] if m.get("role") else []))
            aliases = ([body["name"]] if len(body["members"]) == 1 else []) + \
                      ([role_name(body["name"], m["role"])] if m.get("role") else [])
            answer = by_name.get(m["name"])
            if answer:
                # Without what's said already: the mayor on the School Committee, in its "Mayor" seat.
                said = {part for seat in answer["d"].split("; ") for part in seat.split(", ")}
                answer["d"] += "; " + ", ".join(part for part in what.split(", ") if part not in said)
                answer.setdefault("a", []).extend(aliases)
                continue
            by_name[m["name"]] = answer = {"k": "official", "n": m["name"], "u": f"{prefix}/officials/#{m['anchor']}",
                                           "d": what}
            if aliases:
                answer["a"] = aliases
    return list(by_name.values())


def role_name(body: str, role: str) -> str:
    """A role on a body, named with the body, without saying a word twice: "City Council President"."""
    said = set(words(body))
    return " ".join([body] + [w for w in role.split() if words(w) and not set(words(w)) <= said])


def answers(lang: str, prefix: str, today: str, streets: dict | None = None, boards: list[dict] | None = None,
            officials: dict | None = None) -> dict:
    """The language's answers file: how to read a search (its stop words, generic words, and street types) and
    every answer, from what the town's site has."""
    return {"stop": STOP_WORDS.get(lang, []), "generic": GENERIC_WORDS.get(lang, []), "suffixes": suffixes(),
            "answers": (street_answers(streets, prefix) if streets else [])
            + (board_answers(boards, today, prefix) if boards else [])
            + (official_answers(officials, prefix) if officials else [])}
