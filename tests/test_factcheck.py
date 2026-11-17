"""Summaries checked against their documents' own text, without AI (pipeline/factcheck.py)."""

import json
from pathlib import Path

from pipeline import factcheck

FIXTURES = Path(__file__).parent / "fixtures"

MINUTES = """CITY OF EXAMPLE CONSERVATION COMMISSION
Meeting Minutes, June 4, 2026
Present: Chair Houseman, Members Squibb, Bertoni, Delrosario, Laplante.
1. Minutes of May 21, 2026: motion to approve; unanimously voted.
2. 11 & 13 Bay View Avenue: Motion to amend the Enforcement Order, to have a Notice of Intent filed by July 323
14, 2026, for seasonal dune fencing. Motion carries (4-1), Laplante opposed.
3. DEP File #5-1458, 90 Boyles Street: Certificate of Compliance issued.
4. Grants: Preschool Partnership Grant of $423,733 accepted. Civics Grant of $40,000 accepted.
5. Payment of membership dues of $50.00 approved. Papers 317, 318, 319, 320 referred.
6. Override of approximately $14,000,0 00 discussed. Continued to August, 3 2026.
"""


def check(text, doc=MINUTES):
    record = {"kind": "minutes", "headline": "", "summary": text, "decisions": []}
    return factcheck.check(record, "minutes", [doc])


def problems(text, doc=MINUTES):
    return [(p["kind"], p["what"]) for p in check(text, doc)["problems"]]


def test_what_the_document_says_passes():
    for text in (
        "Approved the May 21, 2026 minutes, 5-0.",                       # unanimous: no one against
        "Required a Notice of Intent by July 14, 2026 for dune fencing, 4-1.",  # numbered lines in the date
        "Issued a Certificate of Compliance for DEP File #5-1458 at 90 Boyles Street.",
        "Accepted two grants totaling $463,733.",                       # a total the model added up
        "Approved $50 in dues and referred Papers 317-320.",            # cents dropped; a range
        "Discussed an override of about $14 million.",                   # rounded, and a broken-up number
        "Continued the matter to August 3, 2026.",                      # a typo in the document's date
        "Chair Houseman and Laplante spoke.",
    ):
        assert check(text)["result"] == "ok", (text, problems(text))


def test_what_the_document_doesnt_say_fails():
    assert problems("Approved the May 21, 2025 minutes.") == [("date", "May 21, 2025")]
    assert problems("Required a Notice of Intent by July 15, 2026.") == [("date", "July 15, 2026")]
    assert problems("Accepted a grant of $432,733.") == [("number", "432,733")]
    assert problems("Approved $500 in dues.") == [("number", "500")]
    assert problems("Issued a certificate for DEP File #8-1458.") == [("number", "8-1458")]
    assert problems("Chair Houseman and Kowalczyk spoke.") == [("name", "Kowalczyk")]
    assert check("Chair Kowalczyk spoke.")["result"] == "failed"


def test_a_vote_count_the_document_doesnt_give_is_noted_not_failed():
    result = check("Approved the fencing, 3-2.")
    assert result["problems"] == [{"field": "summary", "kind": "tally", "what": "3-2"}]
    assert result["result"] == "ok"
    assert problems("Approved the fencing, 4-1.") == []
    # In words, as many minutes write it.
    assert problems("Adjourned, 3-0.", "The committee voted 3 in favor, 0 opposed, to adjourn.") == []
    assert problems("Adjourned, 3-1.", "The committee voted 3 in favor, 0 opposed, to adjourn.") == [("tally", "3-1")]


def test_an_amount_written_out_where_the_document_gives_a_scale():
    # Beverly's Golf and Tennis Commission: the scan says "NTE $8K", the summary "$8,000".
    doc = "Motion to approve repairs NTE $8K and painting NTE $3.1K. Bond of $1.2 million. Room 45."
    assert problems("Approved repairs of up to $8,000 and painting of up to $3,100.", doc) == []
    assert problems("Noted a bond of $1,200,000.", doc) == []
    assert problems("Noted a bond of $1.2 million.", doc) == []
    # Only the amount itself: not one near it, and not a number the document gives without a scale.
    assert problems("Approved repairs of up to $8,001.", doc) == [("number", "8,001")]
    assert problems("Approved repairs of up to $9,000.", doc) == [("number", "9,000")]
    assert problems("Approved $45 for paint.", doc) == [("number", "45")]


def test_numbers_however_written():
    assert [factcheck.value(n) for n in ("55,000.00", "55,000", "014", "5,8", "7:00", "7:30")] == \
        ["55000", "55000", "14", "5.8", "7", "7:30"]


def test_where_the_evidence_is_weaker_nothing_fails():
    # Pages without text (scanned pages): what's missing may be on them.
    partial = factcheck.check({"summary": "Accepted a grant of $999,999.", "decisions": []}, "minutes", [MINUTES, ""])
    assert partial["source"] == "partial" and partial["result"] == "weak"
    # A scan, checked against the model's own transcription.
    scan = {"summary": "Accepted a grant of $999,999.", "decisions": [], "transcript": MINUTES,
            "transcript_source": "ai"}
    assert factcheck.check(scan, "minutes", None)["source"] == "ai"
    assert factcheck.check(scan, "minutes", None)["result"] == "weak"
    # A scan not transcribed yet can't be checked, and is checked again once it is.
    waiting = factcheck.check({"summary": "x", "decisions": []}, "minutes", None)
    assert waiting["result"] == "unchecked"
    assert factcheck.current({"fact_check": waiting})
    assert not factcheck.current({"fact_check": waiting, "transcript": "t", "transcript_source": "ai"})


def test_each_entry_is_checked_on_its_own():
    record = {"headline": "Two grants.", "summary": "", "decisions": ["Accepted $40,000.", "Accepted $41,000."]}
    result = factcheck.check(record, "minutes", [MINUTES])
    assert result["problems"] == [{"field": "decisions", "entry": 2, "kind": "number", "what": "41,000"}]


def test_whats_shown_leaves_out_what_failed_and_unconfirmed_vote_counts():
    record = {"kind": "minutes", "headline": "Approved two grants.",
              "summary": "The committee accepted a $40,000 grant. It also accepted a $41,000 grant.",
              "decisions": ["Accepted the Civics Grant of $40,000, 3-2.", "Accepted a grant of $41,000, 5-0."]}
    record["fact_check"] = factcheck.check(record, "minutes", [MINUTES])
    assert record["fact_check"]["result"] == "failed"
    shown = factcheck.shown(record, record, "minutes")
    # The decision with an amount the minutes don't have is left out, and counted; so is the sentence.
    assert shown["decisions"] == ["Accepted the Civics Grant of $40,000."] and shown["not_shown"] == 1
    assert shown["summary"] == "The committee accepted a $40,000 grant."
    assert shown["headline"] == "Approved two grants."
    # The record itself is unchanged.
    assert len(record["decisions"]) == 2
    # A translation loses the same entries; its summary, whose sentences can't be matched, goes whole.
    spanish = {**record, "headline": "Aprobó dos subvenciones.", "summary": "Aceptó $40,000. Aceptó $41,000.",
               "decisions": ["Aceptó la subvención de $40,000 con votación 3-2.", "Aceptó $41,000, 5-0."]}
    shown = factcheck.shown(spanish, record, "minutes")
    assert shown["decisions"] == ["Aceptó la subvención de $40,000."] and shown["summary"] == ""


def test_a_weaker_check_hides_nothing_but_unconfirmed_vote_counts():
    record = {"summary": "", "decisions": ["Accepted a grant of $999,999, 3-2."], "transcript": MINUTES,
              "transcript_source": "ai"}
    record["fact_check"] = factcheck.check(record, "minutes", None)
    assert record["fact_check"]["result"] == "weak"
    assert factcheck.shown(record, record, "minutes")["decisions"] == ["Accepted a grant of $999,999."]


def test_vote_counts_come_out_cleanly():
    for text, tally, expected in (
        ("Elected Gomes as Chair, 5-0-1 (Gomes abstaining).", "5-0-1", "Elected Gomes as Chair (Gomes abstaining)."),
        ("Approved NOI 028-3140 with conditions, approved 5-0.", "5-0", "Approved NOI 028-3140 with conditions."),
        ("Granted the petition, 11-0 (165-26).", "11-0", "Granted the petition (165-26)."),
        ("The committee voted 2-0-1 to recommend the plan.", "2-0-1", "The committee voted to recommend the plan."),
        ("Adjourned to Executive Session by a 7-2 vote.", "7-2", "Adjourned to Executive Session."),
        ("Motion carried (4-0).", "4-0", "Motion carried."),
        ("Se aprobó el plan con votación 5-0.", "5-0", "Se aprobó el plan."),
    ):
        assert factcheck.without_tally(text, tally) == expected


# ---- Decisions anchored to the minutes ------------------------------------------------

COUNCIL = """CITY OF EXAMPLE CITY COUNCIL
Minutes of April 7, 2026
200-26 Order: Executive Session on budgetary constraints litigation.
A motion was made by Councillor Sica, seconded by Councillor Lucas, that the Order be tabled.
The motion failed by a vote of 3-8.
201-26 Order: That the sum of $175,000 be transferred to Public Works Salaries.
A motion was made by Councillor Colon Hayes, seconded by Councillor Sica, that the Order be
approved. The motion carried by a unanimous vote.
Page 5City of Example
April 7, 2026City Council Meeting Minutes - Final
202-26 Petition: Pool Tables: Loyal Order of Moose, 562 Broadway, 2 tables (Renewal)
The Petition was referred to the License Committee.
203-26 Order: That $8,000 be appropriated for the Senior Center roof. A motion was made by
Councillor Lucas that the Order be approved. The motion did not carry, 4 yeas, 7 nays.
204-26 Ordinance: Self-storage facilities in the Industrial zoning districts. A motion was made
by Councillor Sica that the Ordinance be referred to the Rules & Ordinance Committee. The motion
carried by a unanimous vote.
""" + "Other business of the council was discussed at length. " * 40 + """
205-26 Order: That $2,500 be appropriated for the Linden Street playground. A motion was made by
Councillor Lucas, seconded by Councillor Sica, that the Order be approved. The motion carried, 9-2.
"""


def decisions(*entries, doc=COUNCIL):
    record = {"kind": "minutes", "headline": "", "summary": "",
              "decisions": [text for text, _, _ in entries],
              "decision_evidence": [{"outcome": outcome, "quote": quote} for _, outcome, quote in entries]}
    return factcheck.check(record, "minutes", [doc])


def anchor_problems(*entries):
    return [(p["entry"], p["kind"]) for p in decisions(*entries)["problems"] if p["kind"] in ("quote", "away", "outcome")]


RIGHT = [
    ("Failed a motion to table Order 200-26 on an executive session, 3-8.", "denied",
     "that the Order be tabled. The motion failed by a vote of 3-8."),
    ("Approved transferring $175,000 to Public Works Salaries (Order 201-26), unanimously.", "approved",
     "201-26 Order: That the sum of $175,000 be transferred to Public Works Salaries."),
    # A quote across a page header the PDF's text puts in the middle.
    ("Referred the Loyal Order of Moose pool table renewal (202-26) to the License Committee.", "referred",
     "The motion carried by a unanimous vote. 202-26 Petition: Pool Tables: Loyal Order of Moose, 562 Broadway, "
     "2 tables (Renewal) The Petition was referred to the License Committee."),
    ("Did not approve $8,000 for the Senior Center roof (203-26), 4-7.", "denied",
     "The motion did not carry, 4 yeas, 7 nays."),
    ("Referred the self-storage ordinance (204-26) to the Rules & Ordinance Committee.", "referred",
     "that the Ordinance be referred to the Rules & Ordinance Committee"),
    ("Approved $2,500 for the Linden Street playground (205-26), 9-2.", "approved",
     "seconded by Councillor Sica, that the Order be approved. The motion carried, 9-2."),
]


def test_decisions_anchored_to_the_minutes_pass():
    result = decisions(*RIGHT)
    assert result["result"] == "ok", result["problems"]
    # A quote cut with "...", and one whose words the PDF broke up, with odd spacing and case.
    assert anchor_problems(("Failed to table Order 200-26, 3-8.", "denied",
                            "A motion was made by Councillor Sica ... The motion failed by a vote of 3-8.")) == []
    assert anchor_problems(("Approved $175,000 for Public Works Salaries.", "approved",
                            "the sum of $175,000  be  TRANSFERRED to Public  Works Salaries")) == []


def test_a_quote_not_in_the_minutes_fails():
    assert anchor_problems(("Approved $175,000 for Public Works Salaries.", "approved",
                            "The Council voted to transfer $175,000 to Public Works Salaries.")) == [(1, "quote")]
    assert anchor_problems(("Approved $175,000 for Public Works Salaries.", "approved", "")) == [(1, "quote")]


def test_a_number_or_name_from_another_motion_is_listed():
    # $2,500 is in the minutes, but for another order, far from this one's quote.
    entry = ("Approved $2,500 for Public Works Salaries (Order 201-26).", "approved",
             "201-26 Order: That the sum of $175,000 be transferred to Public Works Salaries.")
    assert anchor_problems(entry) == [(1, "away")]
    # Listed, not held back, until it's measured how often a right decision is listed.
    assert decisions(entry)["result"] == "ok"


def test_an_outcome_turned_round_fails():
    # A dropped "not": the minutes say the motion failed.
    assert anchor_problems(("Approved $8,000 for the Senior Center roof (203-26), 4-7.", "approved",
                            "The motion did not carry, 4 yeas, 7 nays.")) == [(1, "outcome")]
    assert anchor_problems(("Tabled Order 200-26.", "tabled",
                            "that the Order be tabled. The motion failed by a vote of 3-8.")) == [(1, "outcome")]
    # The outcome and the decision's words disagree.
    assert anchor_problems(("Approved tabling Order 200-26.", "denied",
                            "that the Order be tabled. The motion failed by a vote of 3-8.")) == [(1, "outcome")]
    assert anchor_problems(("Denied the transfer of $175,000.", "approved",
                            "201-26 Order: That the sum of $175,000 be transferred to Public Works Salaries.")) \
        == [(1, "outcome")]
    # Referred, where the minutes say nothing of it.
    assert anchor_problems(("Referred $175,000 for Public Works Salaries to committee.", "referred",
                            "201-26 Order: That the sum of $175,000 be transferred to Public Works Salaries.")) \
        == [(1, "outcome")]
    assert anchor_problems(("Approved it.", "passed", "The motion carried by a unanimous vote.")) == [(1, "outcome")]


def test_a_not_in_what_was_decided_isnt_a_no():
    # Gloucester's Zoning Board of Appeals: an approval that there is "not" an increase.
    doc = ("Ms. Norton moves to determine that there is not an increase in the non-conformity in the application "
           "of Robert Rogers to rebuild a shed at 16 Ryan Rd. Mr. Nimon seconds All in favor, 5-0. "
           "Mr. Wilson moves to determine that there is not an increase in a non-conformity at 23 Cliff Rd. "
           "Mr. Cannavo seconds In Favor: 2 In Opposition: 3")
    entries = [("Determined there is not an increase in nonconformity for the shed at 16 Ryan Rd, 5-0.", "approved",
                "moves to determine that there is not an increase in the non-conformity in the application of Robert "
                "Rogers to rebuild a shed at 16 Ryan Rd. Mr. Nimon seconds All in favor, 5-0"),
               ("Failed a motion that there is not an increase in nonconformity at 23 Cliff Rd, 2-3.", "denied",
                "Mr. Wilson moves to determine that there is not an increase in a non-conformity at 23 Cliff Rd. "
                "Mr. Cannavo seconds In Favor: 2 In Opposition: 3")]
    assert [(p["entry"], p["kind"]) for p in decisions(*entries, doc=doc)["problems"]] == []
    # Called approved, the second fails: two for and three against.
    wrong = [(entries[1][0].replace("Failed a motion", "Determined"), "approved", entries[1][2])]
    assert [(p["entry"], p["kind"]) for p in decisions(*wrong, doc=doc)["problems"]] == [(1, "outcome")]


def test_a_two_thirds_vote_can_fail_with_more_for():
    doc = "A motion to override the tax cap. The motion failed on a roll call vote, 9-5, needing 10 votes."
    record = {"kind": "minutes", "headline": "", "summary": "", "decisions": ["Rejected a motion to override the tax cap, 9-5."],
              "decision_evidence": [{"outcome": "denied", "quote": "The motion failed on a roll call vote, 9-5"}]}
    assert factcheck.check(record, "minutes", [doc])["result"] == "ok"


def test_counts_that_arent_votes():
    assert factcheck.votes_for_against("The motion to approve 3-4 bedroom units carried") is None
    assert factcheck.votes_for_against("ages 5-12. The motion carried 5-0.") == (5, 0)
    assert factcheck.votes_for_against("On a roll call vote of 2 yea (Kantor, Sapienza) to 11 nay") == (2, 11)
    assert factcheck.votes_for_against("In Favor: 2 In Opposition: 3") == (2, 3)
    assert factcheck.votes_for_against("Yea: 10 - Anderson, Barbieri Nay: 1 - Colon") == (10, 1)
    assert factcheck.votes_for_against("Paper 200-26 was referred") is None


def test_counts_written_with_names_or_in_words_and_figures():
    # Nashua's roll calls: each count after its names.
    assert factcheck.votes_for_against("Yea: Alderman Thibeault, Alderman O’Brien, Alderwoman Kelly 7 Nay: 0") == (7, 0)
    assert factcheck.votes_for_against("Yea: Alderman Thibeault, Alderwoman Kelly 6 Nay: Alderman Johnson 1 MOTION CARRIED") == (6, 1)
    # Salem's boards: in words, with the figure after.
    assert factcheck.votes_for_against("The vote is four (4) in favor (Nina Vyedin, Christa McGaha, Stephen Larrick, "
                                       "and Peter Habib) and one (1) opposed (Ellen Simpson). The motion passes.") == (4, 1)
    assert factcheck.votes_for_against("The vote is none (0) in favor and five (5) opposed (Christa McGaha). "
                                       "The motion does not pass.") == (0, 5)
    # A dollar amount or an address in a roll call isn't its count.
    assert factcheck.votes_for_against("Yea: Alderman Gregg $1,500 Nay: 0") is None


def test_a_count_the_chair_says_in_a_recording():
    # Medford's City Council, as its recording's transcript has it.
    assert factcheck.document_votes("Seven in the affirmative, none in the negative. The motion passes.") == {(7, 0)}
    assert factcheck.document_votes("Six affirmative, one of the negative, the motion passes.") == {(6, 1)}
    assert factcheck.document_votes("7, the affirmative none, the negative, the motion passes.") == {(7, 0)}
    # A misheard word isn't a count.
    assert factcheck.document_votes("Assembly affirmative, none of the negative of the motion passes.") == set()
    doc = ("[0:41:12] On the motion of Councillor Seng to approve the comment letter, seconded by Councillor Scarpelli. "
           "Six affirmative, one of the negative, the motion passes. [0:41:30] Next item.")
    record = {"kind": "recording", "headline": "", "summary": "",
              "decisions": ["Approved sending a comment letter to the MWRA, 6-1.", "Approved the letter, 7-0."],
              "decision_evidence": [{"outcome": "approved", "quote": "On the motion of Councillor Seng to approve the comment "
                                     "letter, seconded by Councillor Scarpelli. Six affirmative, one of the negative, the motion passes."}] * 2}
    tallies = [(p["entry"], p["what"]) for p in factcheck.check(record, "recording", [doc])["problems"] if p["kind"] == "tally"]
    assert tallies == [(2, "7-0")]


def test_a_motion_held_to_the_next_meeting_is_continued():
    # Salem's City Council: an appointment held under its rules.
    doc = ("#404 Appointment of Alphonse Wright to the Food Policy Council The Mayor's Appointment of Alphonse Wright "
           "of 23 1/2 Broad Street to the Food Policy Council with a term to expire on November 16, 2028, was held "
           "until the next meeting, under the rules. #405 Appointment of Catrina Meyer")
    entry = ("Held the mayor's appointment of Alphonse Wright to the Food Policy Council until the next meeting.", "continued",
             "The Mayor's Appointment of Alphonse Wright of 23 1/2 Broad Street to the Food Policy Council with a term "
             "to expire on November 16, 2028, was held until the next meeting, under the rules.")
    assert [(p["entry"], p["kind"]) for p in decisions(entry, doc=doc)["problems"]] == []
    # Held, without saying until when, isn't continued.
    held = doc.replace("until the next meeting, under the rules", "under the rules")
    quote = entry[2].replace("until the next meeting, under the rules", "under the rules")
    assert [p["kind"] for p in decisions(("Held the appointment.", "continued", quote), doc=held)["problems"]] == ["outcome"]


def test_an_item_sent_on_to_the_full_board_is_referred():
    # Wallingford's Board of Education committees.
    doc = ("5.1. Proposed Addition of Second Read (New): Bylaw 9222 - Removal of Board Officers. The committee "
           "discussed it. This Bylaw will move forward to the full board for consent at their next Board of "
           "Education Meeting.")
    entry = ("Moved proposed new Bylaw 9222, Removal of Board Officers, forward for consent at the next meeting.", "referred",
             "Proposed Addition of Second Read (New): Bylaw 9222 - Removal of Board Officers ... This Bylaw will move "
             "forward to the full board for consent at their next Board of Education Meeting.")
    assert [(p["entry"], p["kind"]) for p in decisions(entry, doc=doc)["problems"]] == []


def test_a_not_in_a_condition_isnt_a_no():
    # Nashua's Business and Industrial Development Authority: a three-part motion, approved.
    doc = ("Rhum Bar: A three-part Motion was made by Dawn: 1. Motion to Accept a one-time payment IF it satisfies 100% "
           "of the owed principal; 2. If not, a counteroffer is to be made to the borrower; 3. if that is not accepted "
           "by the borrower, proceed with the scheduled payment plan. Motion was seconded by Christine; all in favor; approved.")
    entry = ("Approved a three-part motion for the Rhum Bar loan: accept a one-time payment if it satisfies 100% of the "
             "owed principal; if not, make a counteroffer; if that is not accepted, proceed with the payment plan.",
             "approved", "Rhum Bar: A three-part Motion was made by Dawn: ... Motion was seconded by Christine; all in favor; approved.")
    assert [(p["entry"], p["kind"]) for p in decisions(entry, doc=doc)["problems"]] == []
    # Outside a condition, a "not" still says no.
    assert factcheck.says_no("The board did not approve the payment plan.")
    assert not factcheck.says_no("If the payment is not accepted, the plan proceeds.")


def test_a_figure_of_the_order_a_vote_adopted_is_near_its_quote():
    # Salem's City Council: the order's total after the vote that adopted it.
    doc = ("The following order from the Mayor was adopted by a Roll Call Vote of 10-0-1. ORDERED Per MGL Chapter 44 "
           "Section 64 which allows for a municipality to pay for prior fiscal year invoices. "
           + "Department Vendor Amount IT Department AT&T FirstNet $ 317.71 " * 6 + "TOTAL: $9117.71")
    entry = ("Approved paying $9,117.71 in prior-year invoices, 10-0-1.", "approved",
             "The following order from the Mayor was adopted by a Roll Call Vote of 10-0-1.")
    assert [(p["entry"], p["kind"]) for p in decisions(entry, doc=doc)["problems"]] == []


def test_an_ordinal_run_into_the_next_word_is_a_date():
    assert (3, 17, None) in factcheck.document_dates("A motion to approve the March 17thmeeting minutes was made")


def test_older_summaries_without_evidence_are_checked_as_before():
    record = {"kind": "minutes", "headline": "", "summary": "", "decisions": [text for text, _, _ in RIGHT]}
    assert factcheck.check(record, "minutes", [COUNCIL])["result"] == "ok"


def test_what_fails_its_anchor_isnt_shown():
    record = {"kind": "minutes", "headline": "", "summary": "",
              "decisions": ["Approved $8,000 for the Senior Center roof, 4-7.", RIGHT[1][0]],
              "decision_evidence": [{"outcome": "approved", "quote": "The motion did not carry, 4 yeas, 7 nays."},
                                    {"outcome": "approved", "quote": RIGHT[1][2]}]}
    record["fact_check"] = factcheck.check(record, "minutes", [COUNCIL])
    shown = factcheck.shown(record, record, "minutes")
    assert shown["decisions"] == [RIGHT[1][0]] and shown["not_shown"] == 1


def test_what_the_real_model_wrote_on_the_test_set():
    """Cases from the first run of the minutes prompt against the real model (2026-10-03), each a right
    decision the check held back until it was fixed."""
    # Malden: the model quotes a roll call as the page shows it; the PDF's text has the count after the names.
    doc = ("A motion was made by Councillor Sica, seconded by Councillor Colon Hayes, that the Order be tabled. "
           "The motion failed by the following vote:\nYea: Colon Hayes, Sica and Winslow3 - \n"
           "Nay: Condon, Crowe, Linehan, McDonald, O'Malley, Simonelli, Taylor and Luong8 - ")
    quote = ("A motion was made by Councillor Sica, seconded by Councillor Colon Hayes, that the Order be tabled. "
             "The motion failed by the following vote:\nYea: 3 - Colon Hayes, Sica and Winslow\n"
             "Nay: 8 - Condon, Crowe, Linehan, McDonald, O'Malley, Simonelli, Taylor and Luong")
    assert decisions(("A motion to table the Order failed, 3-8.", "denied", quote), doc=doc)["result"] == "ok"
    # But not the quote's first words with something else after them.
    made_up = quote.split("The motion")[0] + "The motion carried unanimously by the following vote: Yea: 11"
    assert decisions(("Tabled the Order.", "tabled", made_up), doc=doc)["result"] == "failed"
    # Gloucester: "if they fail" is about the plants, not the motion.
    doc = ("Co-Chair Jackson moved to approve RCOC 028-2772 498 Washington Street; with a continuing condition that "
           "plants must be replaced if they fail within the next year. Member Cook seconded and was approved.")
    assert decisions(("Approved RCOC 028-2772 498 Washington Street.", "approved", doc), doc=doc)["result"] == "ok"
    # Deferred, and re-committed.
    doc = "3. Mr. Falcetano's request. Decision deferred to the August 13 meeting. It was re-committed back to the Housing Committee."
    assert decisions(("Deferred the request to the August 13 meeting.", "continued",
                      "Decision deferred to the August 13 meeting."), doc=doc)["result"] == "ok"
    assert decisions(("Re-committed the request to the Housing Committee.", "referred",
                      "It was re-committed back to the Housing Committee."), doc=doc)["result"] == "ok"
    # Beverly: a committee holding an item.
    doc = "Order #088-Transfer of $241,250 for union negotiations. Recommend the Council to hold (3-0). Hold."
    assert decisions(("Held Order #088 in the Finance and Property Committee, 3-0.", "tabled",
                      "Order #088-Transfer of $241,250 for union negotiations. Recommend the Council to hold (3-0)."),
                     doc=doc)["result"] == "ok"
    # Lawrence: "Withdrew" isn't anyone's name.
    doc = "There was no discussion on this Motion and it PASSED by a Unanimous Voice Vote DOC #348/19 MOTION TO WITHDRAW PASSED"
    assert decisions(("Withdrew DOC #348/19.", "withdrawn", "DOC #348/19 MOTION TO WITHDRAW PASSED"), doc=doc)["result"] == "ok"
    # The model's own words say how it ended: "Rejected a motion", a motion that "failed" seven words on.
    doc = "Aldermen Kantor and Sapienza voted yea. The motion failed. June 4th Doug motions, Leora seconds. Motion fails."
    assert decisions(("Rejected a motion to pass the school budget, 2-11.", "denied",
                      "Aldermen Kantor and Sapienza voted yea. The motion failed."),
                     ("Motion to approve the June 4, 2026 minutes failed.", "denied",
                      "June 4th Doug motions, Leora seconds. Motion fails."), doc=doc)["result"] == "ok"


def test_an_other_outcome_that_reads_as_passing_where_the_motion_failed_fails():
    # Said failed in the decision's own words, "other" is only a label: shown as it is.
    assert anchor_problems(("Motion to table Order 200-26 failed, 3-8.", "other",
                            "that the Order be tabled. The motion failed by a vote of 3-8.")) == []
    # Read as passing, where the minutes say it failed.
    assert anchor_problems(("Tabled Order 200-26 on an executive session.", "other",
                            "that the Order be tabled. The motion failed by a vote of 3-8.")) == [(1, "outcome")]
    # "Other" where the motion carried isn't checked: it has no words of its own.
    assert anchor_problems(("Took up Order 201-26 on $175,000 for Public Works Salaries.", "other",
                            "201-26 Order: That the sum of $175,000 be transferred to Public Works Salaries.")) == []


def headline_problems(headline, *entries):
    record = {"kind": "minutes", "headline": headline, "summary": "",
              "decisions": [text for text, _, _ in entries],
              "decision_evidence": [{"outcome": outcome, "quote": quote} for _, outcome, quote in entries]}
    return [p["what"] for p in factcheck.check(record, "minutes", [COUNCIL])["problems"]
            if p["field"] == "headline" and p["kind"] == "outcome"]


def test_a_headline_that_turns_its_decision_round_fails():
    roof, transfer = RIGHT[3], RIGHT[1]
    assert headline_problems("Approved $8,000 for the Senior Center roof", roof, transfer) \
        == ["Approved $8,000 for the Senior Center roof: says yes, but the decision was denied"]
    assert headline_problems("Rejected the $175,000 Public Works Salaries transfer", roof, transfer) \
        == ["Rejected the $175,000 Public Works Salaries transfer: says no, but the decision was approved"]
    # Right either way, and each clause of a headline about two decisions on its own.
    assert headline_problems("Turned down $8,000 for the Senior Center roof", roof, transfer) == []
    assert headline_problems("Rejected $8,000 for the Senior Center roof and approved $175,000 for Public Works "
                             "Salaries", roof, transfer) == []
    assert headline_problems("Approved $8,000 for the Senior Center roof; approved the $175,000 transfer for Public "
                             "Works Salaries", roof, transfer) \
        == ["Approved $8,000 for the Senior Center roof: says yes, but the decision was denied"]
    # Not about any one decision, or saying neither: not checked.
    assert headline_problems("Approved the consent agenda", roof, transfer) == []
    assert headline_problems("Council met on the Senior Center roof", roof, transfer) == []


def test_a_headline_turned_round_isnt_shown():
    record = {"kind": "minutes", "headline": "Approved $8,000 for the Senior Center roof", "summary": "",
              "decisions": [RIGHT[3][0]], "decision_evidence": [{"outcome": RIGHT[3][1], "quote": RIGHT[3][2]}]}
    record["fact_check"] = factcheck.check(record, "minutes", [COUNCIL])
    assert record["fact_check"]["result"] == "failed"
    shown = factcheck.shown(record, record, "minutes")
    assert shown["headline"] == "" and shown["decisions"] == [RIGHT[3][0]]


# ---- How the motion ended, read after the quote -------------------------------------

REVERSED = """CITY COUNCIL MINUTES October 6, 2026
Item 4. Motion by Councilor Jones, seconded by Councilor Smith, to approve the Elm Street site plan as presented.
Discussion followed. The motion failed 2-3.
Item 5. Motion to accept the minutes of September 29. The motion carried unanimously.
""" + "Attendance: Jones, Smith, Brown, Lee, Ortiz.\n" * 3
ELM = "Motion by Councilor Jones, seconded by Councilor Smith, to approve the Elm Street site plan as presented."


def test_a_vote_reversed_after_its_quote_is_caught():
    """A decision that says approved, 5-0, on Councilor Smith's motion, where the minutes, right after
    the quoted motion, say it failed 2-3 on Councilor Jones's: once passed as right in every part."""
    record = {"headline": "Council approves Elm Street plan", "summary": "The council approved the Elm Street site plan.",
              "decisions": ["Approved the Elm Street site plan, 5-0, on a motion by Councilor Smith."],
              "decision_evidence": [{"quote": ELM, "outcome": "approved"}]}
    record["fact_check"] = factcheck.check(record, "minutes", [REVERSED])
    assert record["fact_check"]["result"] == "failed"
    found = {(p["field"], p["kind"]) for p in record["fact_check"]["problems"]}
    assert found == {("decisions", "outcome"), ("decisions", "tally"), ("decisions", "mover"), ("headline", "outcome"),
                     ("summary", "outcome")}
    shown = factcheck.shown(record, record, "minutes")
    assert shown["decisions"] == [] and shown["not_shown"] == 1
    assert shown["headline"] == "" and shown["summary"] == ""


def outcome_problems(decision, outcome, quote, doc):
    return [p["what"] for p in decisions((decision, outcome, quote), doc=doc)["problems"] if p["kind"] == "outcome"]


def test_how_the_motion_ended_is_read_after_the_quote():
    assert outcome_problems("Approved the Elm Street site plan.", "approved", ELM, REVERSED) \
        == ["approved, but the minutes say the motion didn't carry: motion failed 2-3."]
    # Tabled, referred, and the others that say what happened: the same.
    doc = REVERSED.replace("to approve the Elm Street", "to table the Elm Street")
    assert outcome_problems("Tabled the Elm Street site plan.", "tabled", ELM.replace("approve", "table"), doc)
    # A motion that died for lack of a second, or one the minutes say was denied.
    for ended in ("The motion died for lack of a second.", "No second. Request denied.", "Action: Failed",
                  "Vote: 2-3."):
        doc = REVERSED.replace("Discussion followed. The motion failed 2-3.", ended)
        assert outcome_problems("Approved the Elm Street site plan.", "approved", ELM, doc), ended
    # Carried, it's right; and so is a denial where it failed.
    carried = REVERSED.replace("The motion failed 2-3.", "The motion carried 3-2.")
    assert outcome_problems("Approved the Elm Street site plan, 3-2.", "approved", ELM, carried) == []
    assert outcome_problems("Rejected the Elm Street site plan, 2-3.", "denied", ELM, REVERSED) == []


def test_only_this_motion_s_result_is_read():
    # The next item's result isn't this one's: the passage stops at the next item's heading ...
    doc = ("6. Motion to accept the Oak Street report.\nIt was discussed at length.\n"
           "7. Motion by Councilor Lee to approve the Pine Street plan. The motion failed 1-4.\n")
    assert outcome_problems("Accepted the Oak Street report.", "approved", "Motion to accept the Oak Street report.", doc) == []
    # ... or at the next motion (an amendment that failed isn't the main motion) ...
    doc = ("Motion by Councilor Lee to approve the Pine Street plan. Councilor Ortiz moved to amend it; "
           "the amendment failed 1-4. The main motion carried 4-1.")
    assert outcome_problems("Approved the Pine Street plan, 4-1.", "approved",
                            "Motion by Councilor Lee to approve the Pine Street plan.", doc) == []
    # ... and the first result is the motion's: a reconsideration that failed after it doesn't count.
    doc = "Motion to approve the Pine Street plan. Motion carried 4-1. Reconsideration failed 1-4."
    assert outcome_problems("Approved the Pine Street plan, 4-1.", "approved", "Motion to approve the Pine Street plan.", doc) == []
    # Nashua restates the motion as its result: "MOTION TO DENY CARRIED" isn't another motion.
    doc = "MOTION by Mr. Minkarah to deny the use variance. SECONDED by Mr. Boucher. MOTION TO DENY CARRIED 4-1."
    assert outcome_problems("Denied the use variance, 4-1.", "denied", "MOTION by Mr. Minkarah to deny the use variance.",
                            doc) == []
    # Lowell's "no second needed" is no failure; nor is a line number inside a count (Beverly's "carries (4-\n42 0)").
    doc = "Request of Ward 3 to refer the matter to the City Manager, no second needed, referred to City Manager."
    assert outcome_problems("Referred the matter to the City Manager.", "referred", "Request of Ward 3 to refer the matter",
                            doc) == []
    doc = "Motion to accept the minutes. Bartley seconds. Motion carries (4-\n42 0)."
    assert outcome_problems("Accepted the minutes.", "approved", "Motion to accept the minutes.", doc) == []


def test_an_approved_decision_whose_minutes_dont_say_how_it_ended_is_listed_not_held_back():
    doc = "Councilor Carson moved staff recommendation on the sale of 45 Allen Street. Seconded by Councilor Faloon.\n"
    result = decisions(("Moved staff recommendation on the sale of 45 Allen Street.", "approved",
                        "Councilor Carson moved staff recommendation on the sale of 45 Allen Street."), doc=doc)
    assert [p["kind"] for p in result["problems"]] == ["unanchored"] and result["result"] == "ok"
    # Said in words the check reads, it isn't listed.
    result = decisions(("Approved the minutes.", "approved", "Motion to approve the minutes. All members present voted in favor."),
                       doc="Motion to approve the minutes. All members present voted in favor.")
    assert result["problems"] == []


def tally_problems(decision, quote, doc):
    return [p["what"] for p in decisions((decision, "approved", quote), doc=doc)["problems"] if p["kind"] == "tally"]


def test_a_vote_count_is_looked_for_near_its_quote():
    # 5-0 isn't in Item 4's passage, nor is "unanimously": Item 5's.
    assert tally_problems("Approved the Elm Street site plan, 5-0.", ELM, REVERSED) == ["5-0"]
    assert tally_problems("Accepted the September 29 minutes, 5-0.", "Motion to accept the minutes of September 29.",
                          REVERSED) == []
    # A count with its abstentions, or without them; not another abstention.
    doc = "Motion to approve the plan. The motion passed 5-0-1."
    assert tally_problems("Approved the plan, 5-0-1.", "Motion to approve the plan.", doc) == []
    assert tally_problems("Approved the plan, 5-0.", "Motion to approve the plan.", doc) == []
    assert tally_problems("Approved the plan, 5-0-2.", "Motion to approve the plan.", doc) == ["5-0-2"]
    # An address or a paper's number isn't a vote count.
    doc = "Motion to approve the waivers for 3-13 Putnam Street. MOTION CARRIED 6-0."
    assert tally_problems("Approved waivers for 3-13 Putnam Street, 6-0.", "Motion to approve the waivers", doc) == []
    # Left out of the text, as a count the minutes don't give: not failed.
    record = {"kind": "minutes", "headline": "", "summary": "", "decisions": ["Approved the Elm Street site plan, 5-0."],
              "decision_evidence": [{"quote": ELM.replace("Councilor Smith", "Councilor Lee"), "outcome": "approved"}]}
    doc = REVERSED.replace("Councilor Smith", "Councilor Lee").replace("The motion failed 2-3.", "Motion carried.")
    record["fact_check"] = factcheck.check(record, "minutes", [doc])
    assert record["fact_check"]["result"] == "ok"
    assert factcheck.shown(record, record, "minutes")["decisions"] == ["Approved the Elm Street site plan."]


def mover_problems(decision, quote, doc):
    return [p["what"] for p in decisions((decision, "approved", quote), doc=doc)["problems"] if p["kind"] == "mover"]


def test_who_moved_and_seconded_is_who_the_minutes_say():
    doc = REVERSED.replace("The motion failed 2-3.", "The motion carried 3-2.")
    assert mover_problems("Approved the Elm Street site plan on a motion by Councilor Smith.", ELM, doc) \
        == ["moved by Councilor Smith, but the minutes say Councilor Jones"]
    assert mover_problems("Approved the Elm Street site plan (motion by Councilor Jones, seconded by Councilor Brown).",
                          ELM, doc) == ["seconded by Councilor Brown, but the minutes say Councilor Smith"]
    assert mover_problems("Approved the Elm Street site plan (motion by Councilor Jones, seconded by Councilor Smith).",
                          ELM, doc) == []
    # Before the quote, in capitals, or after the name ("Bob made a motion, Maggie seconded").
    doc = "MOTION BY ALDERMAN O’BRIEN TO RECOMMEND FINAL PASSAGE. MOTION CARRIED."
    assert mover_problems("Recommended final passage on a motion by Alderman O'Brien.", "TO RECOMMEND FINAL PASSAGE", doc) == []
    assert mover_problems("Recommended final passage on a motion by Alderman Wood.", "TO RECOMMEND FINAL PASSAGE", doc)
    doc = "Approved GLO.1420 as historically significant. Bob made a motion, Maggie seconded, all in favor."
    assert mover_problems("Approved GLO.1420; motion by Bob, seconded by Maggie.", "Approved GLO.1420 as historically significant.",
                          doc) == []
    # Initials can't be compared: Burlington's "AS moves to approve".
    doc = "AS moves to approve the nomination. MB seconds. All in favor."
    assert mover_problems("Approved the nomination on a motion by Alison Smith.", "AS moves to approve the nomination.", doc) == []


def summary_problems(summary, *entries, doc=COUNCIL):
    record = {"kind": "minutes", "headline": "", "summary": summary,
              "decisions": [text for text, _, _ in entries],
              "decision_evidence": [{"outcome": outcome, "quote": quote} for _, outcome, quote in entries]}
    return [p["what"] for p in factcheck.check(record, "minutes", [doc])["problems"] if p["field"] == "summary"]


def test_a_summary_sentence_that_turns_its_decision_round_fails():
    roof, transfer = RIGHT[3], RIGHT[1]
    assert summary_problems("The council approved $8,000 for the Senior Center roof. It also moved $175,000 to Public "
                            "Works Salaries.", roof, transfer) \
        == ["The council approved $8,000 for the Senior Center roof: says yes, but the decision was denied"]
    assert summary_problems("The council rejected $8,000 for the Senior Center roof and approved $175,000 for Public "
                            "Works Salaries.", roof, transfer) == []
    # A motion named for what it would have done isn't a yes; a decision whose own words say "not" can't be
    # held to a clause either way.
    assert summary_problems("Members voted down a motion to adopt $8,000 for the Senior Center roof.", roof) == []
    no_objection = ("Voted that the Council does not object to the $175,000 transfer to Public Works Salaries.", "approved",
                    transfer[2])
    assert summary_problems("The council voted not to object to the $175,000 Public Works Salaries transfer.", no_objection) == []
    # The sentence that fails isn't shown; the rest of the summary is.
    record = {"kind": "minutes", "headline": "", "summary": "The council approved $8,000 for the Senior Center roof, 4-7. "
              "It also moved $175,000 to Public Works Salaries.", "decisions": [roof[0]],
              "decision_evidence": [{"outcome": roof[1], "quote": roof[2]}]}
    record["fact_check"] = factcheck.check(record, "minutes", [COUNCIL])
    assert factcheck.shown(record, record, "minutes")["summary"] == "It also moved $175,000 to Public Works Salaries."


def test_what_s_only_noted_is_shown_where_another_decision_failed():
    away = ("Approved $2,500 for Public Works Salaries (Order 201-26).", "approved",
            "201-26 Order: That the sum of $175,000 be transferred to Public Works Salaries.")
    record = {"kind": "minutes", "headline": "", "summary": "", "decisions": [away[0]],
              "decision_evidence": [{"outcome": away[1], "quote": away[2]}]}
    record["fact_check"] = factcheck.check(record, "minutes", [COUNCIL])
    assert [p["kind"] for p in record["fact_check"]["problems"]] == ["away"] and record["fact_check"]["result"] == "ok"
    assert factcheck.shown(record, record, "minutes")["decisions"] == [away[0]]
    # With a decision the minutes contradict, the summary fails: that decision isn't shown, and the one only
    # noted still is, as it was.
    record["decisions"].append("Approved $8,000 for the Senior Center roof, 4-7.")
    record["decision_evidence"].append({"outcome": "approved", "quote": "The motion did not carry, 4 yeas, 7 nays."})
    record["fact_check"] = factcheck.check(record, "minutes", [COUNCIL])
    assert record["fact_check"]["result"] == "failed"
    shown = factcheck.shown(record, record, "minutes")
    assert shown["decisions"] == [away[0]] and shown["not_shown"] == 1
    assert shown["decision_outcomes"] == ["approved"]


def test_one_failed_decision_hides_only_itself():
    # Gloucester's City Council, 2026-10-27, from its recording: the grant's quote reorders the motion's words
    # (decision 4, "quote"); the appointment and the consent agenda don't say how their motions ended near the
    # quote (1 and 2, "unanchored"), and the tax factor's "7 opposed" is "seven" in the transcript (7, "away").
    # The transcript bears out all four votes; only the grant's isn't shown.
    fixture = json.loads((FIXTURES / "factcheck_gloucester_council.json").read_text(encoding="utf-8"))
    record = fixture["record"]
    fc = record["fact_check"]
    assert fc["result"] == "failed"
    assert sorted((p["entry"], p["kind"]) for p in fc["problems"]) == [
        (1, "unanchored"), (2, "unanchored"), (4, "quote"), (7, "away"), (8, "tally")]
    shown = factcheck.shown(record, record, "recording")
    decisions = record["decisions"]
    assert shown["decisions"] == [*decisions[:3], *decisions[4:7], "Approved a residential tax factor of 0.9973, a 1.03 "
                                  "shift to commercial and industrial property, for fiscal 2027.", decisions[8]]
    assert shown["not_shown"] == 1
    # The decisions shown are as they'd be if the summary had passed (the same vote count taken out).
    passed = {**record, "decisions": [d for i, d in enumerate(record["decisions"]) if i != 3],
              "fact_check": {**fc, "result": "ok", "problems": [
                  {**p, "entry": p["entry"] - (p["entry"] > 4)} for p in fc["problems"] if p["entry"] != 4]}}
    for field in ("decision_titles", "decision_main", "decision_topics"):
        passed[field] = [v for i, v in enumerate(record[field]) if i != 3]
    passed["decision_evidence"] = [e for i, e in enumerate(record["decision_evidence"]) if i != 3]
    as_passed = factcheck.shown(passed, passed, "recording")
    for field in ("decisions", "decision_outcomes", "decision_titles", "decision_main", "decision_topics"):
        assert shown[field] == as_passed[field], field
    # The headline and the summary have no problems of their own, and are shown whole.
    assert shown["headline"] == record["headline"] and shown["summary"] == record["summary"]
    # The Spanish page loses the same decision.
    spanish = factcheck.shown({**record, **fixture["es"]}, record, "recording")
    es = fixture["es"]["decisions"]
    assert len(spanish["decisions"]) == 8 and spanish["not_shown"] == 1
    assert spanish["decisions"][:6] == [*es[:3], *es[4:7]]


def test_names_however_the_document_writes_them():
    doc = "Motion by ALDERMAN O’BRIEN, seconded by Councillor Colon Hayes. Present: McCallum, Sanchez Parkinson."
    for text in ("Alderman O'Brien and Councillor Colon Hayes spoke.", "McCallum and Sanchez-Parkinson spoke."):
        assert problems(text, doc) == [], text
    # A name that's an ordinary word, after a title, is looked for too.
    assert problems("Councilor Hall spoke.", doc) == [("name", "Hall")]
    assert problems("McCallister spoke.", doc) == [("name", "McCallister")]


# A Legistar report's text, as its PDF has it: each label after its line's value ("RESULT: ACCEPTED" on the page).
LEGISTAR = """1. Approval of the Minutes of the Public Health and Public Safety Committee
Meeting of July 1, 2026.
Committee
Minutes
(ID # 26-1212)
ACCEPTEDRESULT:
Ward Four City Councilor Clingan and Ward One City
Councilor McLaughlin
AYE:
City Councilor At Large StrezoABSENT:
2. Director of Emergency Management conveying the 2025-2026 Cummings
School Winter Warming Center After Action Report and Improvement Plan.
Officer's
Communication
(ID # 26-1339)
Chair McLaughlin noted the center was open for 112 nights and asked for a later update.
KEPT IN COMMITTEERESULT:
3. Pedestrian and Transit Advisory Committee submitting comments re: bus network redesign.
Public
Communication
(ID # 26-1210)
PLACED ON FILE AND SENT FOR DISCUSSIONRESULT:
Traffic and Parking CommitteeFOLLOWUP:
"""


def test_a_legistar_report_is_read_in_the_pages_order():
    def legistar(*entries):
        found = decisions(*entries, doc=LEGISTAR)
        return [(p["entry"], p["kind"]) for p in found["problems"] if p["kind"] in ("quote", "outcome")]
    assert legistar(("Accepted the minutes of the July 1, 2026 meeting.", "approved",
                     "Approval of the Minutes of the Public Health and Public Safety Committee Meeting of July 1, "
                     "2026. ... RESULT: ACCEPTED")) == []
    # Kept in committee, given continued; and the item and its result joined without "...".
    assert legistar(("Kept in committee the Warming Center's after action report.", "continued",
                     "Director of Emergency Management conveying the 2025-2026 Cummings School Winter Warming Center "
                     "After Action Report and Improvement Plan. RESULT: KEPT IN COMMITTEE")) == []
    assert legistar(("Placed on file and sent for discussion to the Traffic and Parking Committee the bus network "
                     "comments.", "referred",
                     "Pedestrian and Transit Advisory Committee submitting comments re: bus network redesign. ... "
                     "RESULT: PLACED ON FILE AND SENT FOR DISCUSSION FOLLOWUP: Traffic and Parking Committee")) == []
    # A result the minutes don't give is still not found.
    assert legistar(("Approved the Warming Center's after action report.", "approved",
                     "Director of Emergency Management conveying the 2025-2026 Cummings School Winter Warming Center "
                     "After Action Report and Improvement Plan. RESULT: APPROVED")) == [(1, "quote")]
    # The page's text keeps its length, so a quote's page is where it was.
    assert factcheck.LABEL_AFTER.sub(r"\2:\1", LEGISTAR).__len__() == len(LEGISTAR)


def test_sentences_joined_without_an_ellipsis_are_found_in_order():
    # Each sentence must be in the minutes, in order: one from elsewhere, or out of order, isn't found.
    entry = ("Placed on file the bus network comments.", "other",
             "Pedestrian and Transit Advisory Committee submitting comments re: bus network redesign. "
             "Chair McLaughlin noted the center was open for 112 nights and asked for a later update.")
    assert [(p["entry"], p["kind"]) for p in decisions(entry, doc=LEGISTAR)["problems"] if p["kind"] == "quote"] \
        == [(1, "quote")]


def test_a_pdfs_ligatures_and_accents_are_read_as_written():
    doc = ("6. Motion to waive the ﬁrst reading by Erika Reinfeld second by Aaron Olapade- 6/1\n"
           "7. Citation commending Ramón Bueno for his commitment to climate action. Approved.\n")
    assert anchor_problems_in(doc, ("Waived the first reading, 6-1.", "approved",
                                    "Motion to waive the first reading by Erika Reinfeld second by Aaron Olapade- 6/1"),
                              ("Approved a citation commending Ramón Bueno.", "approved",
                               "Citation commending Ramón Bueno for his commitment to climate action. Approved.")) == []
    assert check("Officers waived the first reading.", doc="The Ofﬁcers waived the ﬁrst reading of the policy, "
                 "as the board's bylaws allow for any policy.")["result"] == "ok"


def anchor_problems_in(doc, *entries):
    return [(p["entry"], p["kind"]) for p in decisions(*entries, doc=doc)["problems"]
            if p["kind"] in ("quote", "away", "outcome")]


def test_outcomes_in_the_minutes_own_words():
    for decision, outcome, quote in (
        ("Continued the OPEB review to November 5, 2026.", "continued",
         "Memorandum from the City Auditor on the OPEB Valuation Reports (cont. to November 5, 2026)"),
        ("Continued the matter to the next meeting.", "continued",
         "In City Council, C. Liang requested matter be put over to next meeting. No objections."),
        ("Put policy JJA on hold for curriculum language review.", "continued", 'JJA: Hold for "curriculum language"'),
        ("Continued the hearing to October 26, 2026.", "continued",
         "a motion that was approved by a voice vote to recess the hearing until a date certain of Monday, October 26, 2026."),
        ("Continued policy C2 to the next meeting.", "continued", "We will come back to this next time, only a first read."),
        ("Referred Resolution R-26-049 to the Personnel/Administrative Affairs Committee.", "referred",
         "Given its first reading, assigned to the PERSONNEL/ADMINISTRATIVE AFFAIRS COMMITTEE, by President Wilshire"),
        ("Sent the cafeteria reports to the full Board.", "referred",
         "The consensus of the Operations Committee was to send the Cafeteria Reports (4.2) to the full Board for approval."),
        ("Rejected the bid for the Bridge St. Bridge Rehab project.", "denied",
         "To formally reject the received bid for the Bridge St. Bridge Rehab project and rebid the project."),
    ):
        assert anchor_problems_in(quote + "\n", (decision, outcome, quote)) == [], (decision, quote)


def test_a_page_of_glyph_numbers_has_no_text_to_check():
    glyphs = "/i255\n/1/2/3/4/i255/5/i255/6/i255\n/7/8/9/10/i255/12/13/i255/14/12/15/16/17/17/i255/18/i255/20/21/22\n" * 3
    record = {"kind": "minutes", "headline": "Approved park namings at Saint Louis Park.", "summary": "", "decisions": []}
    assert factcheck.check(record, "minutes", [glyphs, glyphs])["result"] == "unchecked"
    assert factcheck.check(record, "minutes", [glyphs, MINUTES])["source"] == "partial"


def test_a_count_with_no_one_against_beside_someone_opposed_is_left_out():
    # Gloucester's City Council, 2026-10-27, as its recording's chair said it: "Ayes have it 8 to 0, 1 opposed."
    doc = ("to recommend that the City Council adopt a residential tax classification factor of 0.9973 that equates to a "
           "commercial industrial CIP shift of 1.03 for fiscal year 2027, and I so move. ... Ayes have it 8 to 0, 1 opposed.")
    text = "Approved a residential tax factor of 0.9973, a 1.03 shift to commercial and industrial property, for fiscal 2027, 8 to 0 with 1 opposed."
    record = {"kind": "recording", "headline": "", "summary": "", "decisions": [text],
              "decision_evidence": [{"outcome": "approved", "quote": doc}]}
    fc = factcheck.check(record, "recording", [doc])
    assert ("tally", "8 to 0 with 1 opposed") in [(p["kind"], p["what"]) for p in fc["problems"]]
    assert fc["result"] == "ok"          # noted, not failed
    record["fact_check"] = fc
    assert factcheck.shown(record, record, "recording")["decisions"] == [
        "Approved a residential tax factor of 0.9973, a 1.03 shift to commercial and industrial property, for fiscal 2027."]
    for consistent in ("Approved the plan, 8 to 0.", "Approved the plan, 8-1, with 1 opposed.", "Approved the plan 5-0; one no-show."):
        assert not factcheck.CONTRADICTED.search(consistent), consistent
    for contradicted in ("Approved the plan, 5-0, one opposed.", "Approved the plan 7 to 0 and 2 against."):
        assert factcheck.CONTRADICTED.search(contradicted), contradicted


STREETS = frozenset({"STANDLEY ST", "OLD STANDLEY ST", "ENON ST", "BLAINE ST", "MAPLE AVE", "MAPLE ST", "BROWN ST",
                     "BROWNE ST"})


def test_a_recordings_misheard_street_is_shown_as_the_town_spells_it():
    # Beverly's Planning Board, 2026-10-27: the transcript has "38 Stanley Street"; the street is Standley Street.
    doc = "I move to set the public hearing for the OSRD site plan review at 38 Stanley Street for November 17th. 7 to nothing."
    record = {"kind": "recording", "headline": "", "summary": "",
              "decisions": ["Set a November 17 public hearing on the 38 Stanley Street site plan review."],
              "decision_titles": ["Public hearing date for 38 Stanley Street"],
              "decision_evidence": [{"outcome": "other", "quote": doc}]}
    fc = factcheck.check(record, "recording", [doc], streets=STREETS)
    assert [(p["field"], p["kind"], p["what"], p["fix"]) for p in fc["problems"] if p["kind"] == "spelling"] == [
        ("decisions", "spelling", "38 Stanley Street", "38 Standley Street"),
        ("decision_titles", "spelling", "38 Stanley Street", "38 Standley Street")]
    assert fc["result"] == "ok"
    record["fact_check"] = fc
    shown = factcheck.shown(record, record, "recording")
    assert shown["decisions"] == ["Set a November 17 public hearing on the 38 Standley Street site plan review."]
    assert shown["decision_titles"] == ["Public hearing date for 38 Standley Street"]
    # In a translation too: a street's name isn't translated.
    spanish = {**record, "decisions": ["Fijó una audiencia pública el 17 de noviembre sobre 38 Stanley Street."],
               "decision_titles": ["Audiencia para 38 Stanley Street"]}
    assert factcheck.shown(spanish, record, "recording")["decisions"] == [
        "Fijó una audiencia pública el 17 de noviembre sobre 38 Standley Street."]


def test_only_a_clear_misspelling_is_respelled():
    fixes = lambda text: [(p["what"], p["fix"]) for p in factcheck.misheard_streets(text, STREETS)]
    assert fixes("Approved a garage at 6 Blane St.") == [("6 Blane St", "6 Blaine St")]
    assert fixes("A plan for 37 Enon Street.") == []            # one of the town's
    # Blane is a street of the town's too, with another type: "Blane Street" may be new, not misheard.
    assert factcheck.misheard_streets("A garage at 6 Blane Street.", STREETS | {"BLANE RD"}) == []
    assert fixes("A plan for 9 Browns Street.") == []           # one letter from two streets
    assert fixes("A plan for 9 Mapel Avenue.") == []            # two letters from Maple
    assert fixes("A plan for 12 Elmm Street.") == []            # too short to match
    # Only a recording's: minutes and agendas are the city's own words.
    record = {"kind": "minutes", "headline": "", "summary": "", "decisions": ["Approved a garage at 6 Blane Street."]}
    assert not [p for p in factcheck.check(record, "minutes", ["6 Blane Street garage approved."], streets=STREETS)["problems"]
                if p["kind"] == "spelling"]


# Lawrence's City Council, 2026-11-04, and Lowell's Planning Board, 2026-11-02, as their agendas give them.
AGENDA = """393/26 Order – Bond Authorization – Frost Elementary School Roof Replacement – $7,861,647.00
434/26 Payment of Prior Year Invoice [$30,316.80] Brody, Hardoon, Perkins & Kesten
Site Plan Review – 41 Hurd Street, 01852
Permit approval to operate a car wash at 585 Pawtucket Boulevard. The subject property is in the
165/26  Loading Zone – 67 Jackson Street [AF Café]
o. 342 (aka 344, 346, & 348) Lake Avenue (CC-EO-2026-002)
Location: 78 Oak Brook Drive.
900/902/920/940 So. Colony Road
2. 6 Woburn Street (PB-2026-024)
Donations-$18,815.70
"""


def agenda_problems(text: str, noted: bool = True) -> list[tuple]:
    return [(p["kind"], p["what"], p.get("agenda")) for p in factcheck.against_agenda(text, factcheck.Agenda(AGENDA), noted)]


def test_a_recordings_amounts_are_held_to_the_agenda():
    # The chair said $7,881,647 before the clerk read the order: the summary has what the transcript has.
    assert agenda_problems("Approved $7,881,647 in borrowing for the Frost Elementary School roof replacement.") == [
        ("agenda", "$7,881,647", "$7,861,647")]
    assert agenda_problems("Approved $7,861,647 in borrowing for the Frost roof.") == []
    assert agenda_problems("Approved paying a prior year invoice of $40,316.80.") == [("agenda", "$40,316.80", "$30,316.80")]
    # The same dollars with other cents, and two figures next to each other swapped.
    assert agenda_problems("Accepted an $18,815.17 donation.") == [("agenda", "$18,815.17", "$18,815.70")]
    # The cents left off, or given where the agenda leaves them off ("$7,861,647.00" is $7,861,647), are the same amount.
    assert agenda_problems("Accepted an $18,815 donation.") == []
    assert agenda_problems("Approved $7,861,647.25 for the roof.") == []
    assert agenda_problems("Approved $ 7,881,647 for the roof.") == [("agenda", "$ 7,881,647", "$7,861,647")]
    assert agenda_problems("Approved $7,816,647 for the roof.") == [("agenda", "$7,816,647", "$7,861,647")]
    # Rounded, or said to be about: only the figure the agenda has nothing near is listed.
    assert agenda_problems("Approved about $7.9 million for the roof.") == []
    # A figure the agenda has nothing like (set at the meeting: a fine, an amendment) is only listed, and only for a
    # decision; a round one a figure off another of the agenda's is likely another item's.
    assert agenda_problems("Fined the licensee $500.") == [("off_agenda", "$500", None)]
    assert agenda_problems("Fined the licensee $500.", noted=False) == []
    assert agenda_problems("Approved $20,000 for the study.") == [("off_agenda", "$20,000", None)]


def test_a_recordings_addresses_are_held_to_the_agenda():
    assert agenda_problems("Approved the site plan for 41 Third Street with conditions.") == [
        ("agenda", "41 Third Street", "41 Hurd Street")]
    # The transcript's words, in lowercase.
    assert agenda_problems("Continued the car wash review at 585 protected Boulevard.") == [
        ("agenda", "585 protected Boulevard", "585 Pawtucket Boulevard")]
    # The same street, a house number one figure off.
    assert agenda_problems("Sent a loading zone at 66 Jackson Street to the city engineer.") == [
        ("agenda", "66 Jackson Street", "67 Jackson Street")]
    # However the agenda writes it: another type's abbreviation, other house numbers between, the street's name in two
    # words, a direction abbreviated, a house number among others.
    assert agenda_problems("Approved the site plan for 41 Hurd St.") == []
    assert agenda_problems("Amended the enforcement order for 342 Lake Avenue.") == []
    assert agenda_problems("A certificate of compliance for 78 Oakbrook Drive.") == []
    assert agenda_problems("Applications at 920 South Colony Road.") == []
    assert agenda_problems("Applications at 920 Colony Road.") == []
    # Not on the agenda at all: listed, not failed. Nor is another street at the same number with a name unlike it.
    assert agenda_problems("Approved the definitive site plan for 10 Woburn Street.") == [("off_agenda", "10 Woburn Street", None)]
    assert agenda_problems("A garage at 41 Elm Street.") == [("off_agenda", "41 Elm Street", None)]
    # Not an address: a count, a word between, an amount.
    assert agenda_problems("Approved 4 on Elm Street.") == []
    assert agenda_problems("Voted 3 times on Main Street parking.") == []


def test_a_recording_summary_with_an_amount_or_address_off_the_agenda_isnt_shown():
    doc = ("[0:10:00] Order for $7,881,647 for the Frost School roof. All in favor? The ayes have it. "
           "[0:20:00] Site plan for 41 Third Street, 205 units. All in favor? Motion passes. "
           "[0:30:00] Fine of $500 for the Gold Lounge. All in favor? Motion passes.")
    record = {"kind": "recording", "headline": "Site plan approved for 41 Third Street.",
              "summary": "The Frost roof was approved. The 41 Third Street plan has 205 units.",
              "decisions": ["Approved $7,881,647 for the Frost School roof.", "Approved the site plan for 41 Third Street.",
                            "Approved a $500 fine for the Gold Lounge."],
              "decision_titles": ["$7,881,647 for the Frost roof", "Site plan for 41 Third Street", "Gold Lounge fine"],
              "decision_evidence": [{"outcome": "approved", "quote": "Order for $7,881,647 for the Frost School roof. All in "
                                     "favor? The ayes have it."},
                                    {"outcome": "approved", "quote": "Site plan for 41 Third Street. All in favor? Motion passes."},
                                    {"outcome": "approved", "quote": "Fine of $500 for the Gold Lounge. All in favor? Motion passes."}],
              "discussion": []}
    # Without an agenda, as before.
    assert factcheck.check(record, "recording", [doc])["result"] == "ok"
    fc = factcheck.check(record, "recording", [doc], agenda=AGENDA)
    assert fc["result"] == "failed" and fc["recording_version"] == factcheck.RECORDING_VERSION
    assert sorted((p["field"], p.get("entry"), p["kind"]) for p in fc["problems"]) == [
        ("decision_titles", 1, "agenda"), ("decision_titles", 2, "agenda"), ("decisions", 1, "agenda"),
        ("decisions", 2, "agenda"), ("decisions", 3, "off_agenda"), ("headline", None, "agenda"), ("summary", None, "agenda")]
    record["fact_check"] = fc
    shown = factcheck.shown(record, record, "recording")
    assert shown["decisions"] == ["Approved a $500 fine for the Gold Lounge."] and shown["not_shown"] == 2
    assert shown["headline"] == "" and shown["summary"] == "The Frost roof was approved."
    # A minutes' summary isn't held to an agenda: the minutes are the city's own record.
    minutes = {**record, "kind": "minutes"}
    assert not [p for p in factcheck.check(minutes, "minutes", [doc], agenda=AGENDA)["problems"] if "agenda" in p["kind"]]


def test_an_address_respelled_for_the_town_is_held_to_the_agenda_as_shown():
    record = {"kind": "recording", "headline": "", "summary": "", "decisions": ["Approved a garage at 6 Blane St."],
              "decision_evidence": [{"outcome": "approved", "quote": "a garage at 6 Blane St. Motion passes."}]}
    fc = factcheck.check(record, "recording", ["a garage at 6 Blane St. Motion passes."], streets=STREETS,
                         agenda="Garage, 6 Blaine Street")
    assert [(p["kind"], p["what"]) for p in fc["problems"]] == [("spelling", "6 Blane St")] and fc["result"] == "ok"


def test_a_recordings_check_is_current_with_its_version_and_agendas():
    checked = {"kind": "recording", "fact_check": {"version": factcheck.VERSION, "recording_version": factcheck.RECORDING_VERSION,
                                                   "agendas": ["a" * 64], "result": "ok", "problems": []}}
    assert factcheck.current(checked) and factcheck.current(checked, ["a" * 64])
    # The meeting's agenda changed, or one came.
    assert not factcheck.current(checked, ["b" * 64]) and not factcheck.current(checked, ["a" * 64, "b" * 64])
    # Checked before the recording's own checks changed.
    older = {**checked, "fact_check": {**checked["fact_check"], "recording_version": None}}
    assert not factcheck.current(older)
    # Every other summary only with VERSION.
    assert factcheck.current({"kind": "minutes", "fact_check": {"version": factcheck.VERSION}})


def test_the_report_gives_the_agendas_own_beside_what_differs(tmp_path):
    (tmp_path / "summaries").mkdir()
    fc = {"version": factcheck.VERSION, "source": "recording", "result": "failed", "problems": [
        {"field": "decisions", "entry": 3, "kind": "agenda", "what": "41 Third Street", "agenda": "41 Hurd Street"}]}
    (tmp_path / "summaries" / f"{'a' * 64}.json").write_text(json.dumps({"kind": "recording", "fact_check": fc}))
    assert "  decisions 3: agenda '41 Third Street' (the agenda: '41 Hurd Street')" in factcheck.report(tmp_path)


# Malden's City Council, 2026-10-27: the chair recounts the Rules and Ordinance Committee's vote on an amendment,
# then the council votes to enroll the paper.
MALDEN = ("[1:10:00] As a member of the Ordinance Committee I'll recount what happened. Councilor O'Malley moved to amend "
          "the paper to sustain the 15% inclusionary standard. That also failed for lack of a second, at which point it was "
          "seconded. We did vote it and it failed at that point. [1:20:00] A motion by Councilor Winslow to enroll. We will "
          "call the roll. Councilor Crowe. Yes. That paper has been enrolled. " + "Members discussed the order. " * 60 +
          "[1:40:00] Next is the snow and ice order. All in favor? Aye. Opposed? Motion carries. " +
          "Members discussed the budget. " * 60 + "[2:10:00] Motion to approve the minutes. All in favor? Motion carries.")


def recording(decisions: list[tuple[str, str, str]]) -> dict:
    return {"kind": "recording", "headline": "", "summary": "", "discussion": [], "decisions": [d for d, _, _ in decisions],
            "decision_evidence": [{"outcome": o, "quote": q} for _, o, q in decisions]}


def test_a_committees_vote_recounted_isnt_shown_as_the_boards():
    record = recording([
        ("Failed: an amendment to keep the 15% inclusionary standard.", "denied",
         "Councilor O'Malley moved to amend the paper to sustain the 15% inclusionary standard. ... We did vote it and it "
         "failed at that point."),
        ("Enrolled the inclusionary zoning paper.", "approved",
         "A motion by Councilor Winslow to enroll. We will call the roll. ... That paper has been enrolled.")])
    fc = factcheck.check(record, "recording", [MALDEN])
    assert [(p["entry"], p["kind"]) for p in fc["problems"] if p["kind"] == "committee"] == [(1, "committee")]
    record["fact_check"] = fc
    assert factcheck.shown(record, record, "recording")["decisions"] == ["Enrolled the inclusionary zoning paper."]
    # The board's own vote, announced: not recounted, though the committee is named before it.
    taken = recording([("Approved the amendment.", "approved", "That also failed for lack of a second, at which point it "
                        "was seconded. We did vote it and it failed at that point. [1:20:00] A motion by Councilor "
                        "Winslow to enroll. We will call the roll.")])
    assert not [p for p in factcheck.check(taken, "recording", [MALDEN])["problems"] if p["kind"] == "committee"]
    # A vote recounted with no committee: another check's to judge.
    plain = MALDEN.replace("As a member of the Ordinance Committee I'll", "I'll")
    assert not [p for p in factcheck.check(record, "recording", [plain])["problems"] if p["kind"] == "committee"]
    # The board's own name isn't another committee: a School Committee recounting its own vote is another check's.
    school = MALDEN.replace("the Ordinance Committee", "the School Committee")
    assert not [p for p in factcheck.check(record, "recording", [school], body="School Committee")["problems"]
                if p["kind"] == "committee"]
    assert [p for p in factcheck.check(record, "recording", [MALDEN], body="School Committee")["problems"]
            if p["kind"] == "committee"]
    # Only a recording's: minutes record what was voted where.
    assert not [p for p in factcheck.check({**record, "kind": "minutes"}, "minutes", [MALDEN])["problems"]
                if p["kind"] == "committee"]


def test_a_vote_the_recording_announces_that_no_decision_is_about_is_listed():
    record = recording([("Enrolled the inclusionary zoning paper.", "approved",
                         "A motion by Councilor Winslow to enroll. We will call the roll. ... That paper has been enrolled.")])
    fc = factcheck.check(record, "recording", [MALDEN])
    left = [p for p in fc["problems"] if p["kind"] == "unlisted"]
    # The snow and ice order's vote; not the minutes', a procedural motion's; not the enrollment's, which is listed.
    assert [p["field"] for p in left] == ["transcript"]
    assert left[0]["what"].endswith("Next is the snow and ice order. All in favor? Aye. Opposed? Motion carries")
    # Listed, not held against the summary, and shown nowhere.
    assert fc["result"] == "ok"
    record["fact_check"] = fc
    assert factcheck.shown(record, record, "recording")["decisions"] == record["decisions"]
    assert "transcript: unlisted" in "\n".join(evaluate_factcheck_lines(fc))
    # Votes taken one after another are each read; a vote said twice is one.
    consent = ("Item one, a license renewal. All in favor? Motion carries. Motion carries. " + "Discussion. " * 15 +
               "Item two, another license. All in favor? Motion carries.")
    assert len(factcheck.unlisted(consent, factcheck.Located(consent), [])) == 2
    # A summary with no decisions: every vote announced is listed.
    none = recording([])
    assert len([p for p in factcheck.check(none, "recording", [MALDEN])["problems"] if p["kind"] == "unlisted"]) == 2


def evaluate_factcheck_lines(fc: dict) -> list[str]:
    from pipeline import evaluate_factcheck
    return [evaluate_factcheck.problem(p) for p in fc["problems"]]


# Lowell's City Council, 2026-10-27: the recording's summary was written before the minutes, which were summarized after.
LOWELL_RECORDING = {
    "kind": "recording", "headline": "Approved $1.5 million for 14 firefighter recruits.",
    "summary": "The meeting approved $1.5 million to hire 14 firefighter recruits. A letter opposing a shelter at the ICC "
               "building was approved.",
    "discussion": ["Fire overtime and hiring: Members asked whether 14 recruits would lower overtime.",
                   "ICC building future: Members and residents discussed rumors that the building would become a shelter. "
                   "A letter opposing that use was approved."],
    "decisions": [], "fact_check": {"version": factcheck.VERSION, "source": "recording", "result": "ok", "problems": []}}
LOWELL_MINUTES = {
    "kind": "minutes", "decisions": [
        "Approved $1.5 million to hire 14 firefighter recruits, 11-0.",
        "Referred to the City Manager a request for a letter opposing the ICC being used as any type of shelter.",
        "Revoked the liquor license for Hurricane Betty's at 350 Southbridge Street, 3-0."],
    "decision_evidence": [{"outcome": "approved"}, {"outcome": "referred"}, {"outcome": "approved"}],
    "fact_check": {"version": factcheck.VERSION, "source": "pdf", "result": "ok", "problems": []}}


def test_a_recordings_summary_beside_the_minutes_leaves_out_what_they_contradict():
    found = factcheck.against_minutes(LOWELL_RECORDING, LOWELL_MINUTES)
    assert [(p["field"], p.get("entry"), p["kind"]) for p in found] == [("summary", None, "minutes"), ("discussion", 2, "minutes")]
    assert found[1]["minutes"].startswith("Referred to the City Manager")
    checked = factcheck.beside_minutes(LOWELL_RECORDING, LOWELL_MINUTES)
    shown = factcheck.shown(LOWELL_RECORDING, checked, "recording")
    assert shown["summary"] == "The meeting approved $1.5 million to hire 14 firefighter recruits."
    assert shown["discussion"] == [LOWELL_RECORDING["discussion"][0]]
    # The record itself is left as it was.
    assert LOWELL_RECORDING["fact_check"]["problems"] == []
    # A translation: the discussion entry by its place; the summary, whose sentences can't be matched, not at all.
    spanish = {**LOWELL_RECORDING, "headline": "Aprobó $1.5 millones.", "summary": "Se aprobó una carta.",
               "discussion": ["Horas extra: ...", "Futuro del edificio ICC: ... Se aprobó una carta."]}
    assert factcheck.shown(spanish, checked, "recording")["discussion"] == ["Horas extra: ..."]


def test_only_a_clear_contradiction_of_the_minutes_is_left_out():
    def found(text: str, minutes: dict = LOWELL_MINUTES) -> list:
        return factcheck.against_minutes({"summary": text}, minutes)
    # The minutes agree, or the clause is about no decision of theirs.
    assert found("The council approved $1.5 million for 14 firefighter recruits.") == []
    assert found("A letter opposing a shelter at the ICC building was referred to the manager.") == []
    assert found("Members approved a new playground on Elm Street.") == []
    # A no about a request named for what it would have done isn't about the decision: Worcester's License Commission.
    assert found("A request to hold Hurricane Betty's revocation during an appeal was turned down.") == []
    # A no about a decision approved.
    assert len(found("The council turned down Hurricane Betty's liquor license revocation.")) == 1
    # A decision the minutes' own check doesn't show isn't held against.
    hidden = {**LOWELL_MINUTES, "fact_check": {**LOWELL_MINUTES["fact_check"], "result": "failed", "problems": [
        {"field": "decisions", "entry": 2, "kind": "quote", "what": "x"}]}}
    assert found("A letter opposing a shelter at the ICC building was approved.", hidden) == []
    # Without minutes, or minutes with no decisions: as it is.
    assert factcheck.beside_minutes(LOWELL_RECORDING, None) is LOWELL_RECORDING
    assert factcheck.beside_minutes(LOWELL_RECORDING, {"decisions": []}) is LOWELL_RECORDING
