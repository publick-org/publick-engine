"""The agenda set's runner (pipeline/evaluate_agendas.py), without the model: a summary is judged against the actions
read by hand, as the site would show it."""

import json

from pipeline import evaluate_agendas, summarize

AGENDA = """CONSENT AGENDA ACTION
1. Mayoral Appointment Chief of Police Scott A. Carola TTE 11/29/2029 (refer O&A)
2. Memorandum from the DPW Financial Manager requesting approval to pay FY2026 invoices with FY2027 funds for Republic
Services in the amount of $292,223.95. (refer B&F)
STANDING COMMITTEE REPORTS
1. Memorandum from the Mayor regarding Route 127 road safety audit. (FCV)
SCHEDULED PUBLIC HEARINGS
1. PH2026-015: Petition to the City Council to accept and lay out a public way re: Deacon Farm Lane.
"""

DOCUMENT = {"name": "gloucester", "items": [
    {"match": "Carola", "action": ["referred"]},
    {"match": "Republic Services", "action": ["referred"]},
    {"match": "Route 127", "action": ["vote"]},
    {"match": "Deacon Farm", "action": ["hearing"]},
    {"match": "[Ss]hopping cart", "action": ["hearing"]},
]}


def result(*actions):
    items = ["Scott A. Carola appointed Chief of Police", "Pay $292,223.95 in Republic Services invoices",
             "Route 127 road safety audit", "Public hearing on Deacon Farm Lane as a public way"]
    words = {"referred": "refer O&A", "vote": "FCV", "hearing": "SCHEDULED PUBLIC HEARINGS", "information": "Info only",
             "unstated": ""}
    return {"headline": "Deacon Farm Lane.", "summary": "", "start_time": "", "location": "",
            "items": [{"item": i, "action": a, "action_words": w or words[a]} for i, (a, w) in zip(items, actions)]}


def test_right_wrong_and_missing_as_the_page_would_show_them():
    judged = evaluate_agendas.judge(DOCUMENT, result(("referred", ""), ("referred", "refer B&F"), ("vote", ""),
                                                     ("hearing", "")), 0.01, None, [AGENDA])
    assert [r["status"] for r in judged["expected"]] == ["right", "right", "right", "right", "missing"]
    assert judged["given"] == 4 and judged["taken_away"] == 0
    assert judged["groups"] == [["vote", 1], ["hearing", 1], ["referred", 2]]
    text, failed = evaluate_agendas.report([judged])
    assert not failed and "4 right, 0 wrong (0 caught, 0 shown), 1 missing" in text
    # A wrong action whose words are in the agenda is shown, and fails the run; one whose words aren't is caught.
    judged = evaluate_agendas.judge(DOCUMENT, result(("vote", "FCV"), ("referred", "refer P&D"), ("vote", ""),
                                                     ("hearing", "")), 0.01, None, [AGENDA])
    rows = judged["expected"]
    assert rows[0]["status"] == "wrong" and not rows[0]["caught"] and rows[0]["on_page"] == "vote"
    assert rows[1]["status"] == "right" and rows[1]["taken_away"]
    text, failed = evaluate_agendas.report([judged])
    assert failed and "— **shown**" in text and "right, but taken away" in text
    judged = evaluate_agendas.judge(DOCUMENT, result(("hearing", "refer X"), ("referred", "refer B&F"), ("vote", ""),
                                                     ("hearing", "")), 0.01, None, [AGENDA])
    assert judged["expected"][0]["caught"] and not evaluate_agendas.report([judged])[1]


def test_without_text_no_action_is_shown():
    judged = evaluate_agendas.judge(DOCUMENT, result(("vote", "FCV"), ("referred", "refer B&F"), ("vote", ""),
                                                     ("hearing", "")), 0.01, None, None)
    assert judged["source"] == "none" and judged["groups"] is None
    assert judged["expected"][0]["caught"] and not evaluate_agendas.report([judged])[1]


def test_the_set_names_actions_the_prompt_gives():
    documents = json.loads(evaluate_agendas.SET.read_text(encoding="utf-8"))["documents"]
    assert documents and all(d["url"].startswith("https://files.publick.org/") and len(d["sha256"]) == 64 for d in documents)
    for d in documents:
        for item in d["items"]:
            assert item["action"] and set(item["action"]) <= set(summarize.ITEM_ACTIONS), item
