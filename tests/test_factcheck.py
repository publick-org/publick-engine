"""Summaries checked against their documents' own text, without AI (pipeline/factcheck.py)."""

from pipeline import factcheck

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


def test_what_s_only_noted_is_hidden_where_the_summary_failed_for_something_else():
    away = ("Approved $2,500 for Public Works Salaries (Order 201-26).", "approved",
            "201-26 Order: That the sum of $175,000 be transferred to Public Works Salaries.")
    record = {"kind": "minutes", "headline": "", "summary": "", "decisions": [away[0]],
              "decision_evidence": [{"outcome": away[1], "quote": away[2]}]}
    record["fact_check"] = factcheck.check(record, "minutes", [COUNCIL])
    assert [p["kind"] for p in record["fact_check"]["problems"]] == ["away"] and record["fact_check"]["result"] == "ok"
    assert factcheck.shown(record, record, "minutes")["decisions"] == [away[0]]
    # With a decision the minutes contradict, the summary fails, and neither is shown.
    record["decisions"].append("Approved $8,000 for the Senior Center roof, 4-7.")
    record["decision_evidence"].append({"outcome": "approved", "quote": "The motion did not carry, 4 yeas, 7 nays."})
    record["fact_check"] = factcheck.check(record, "minutes", [COUNCIL])
    assert record["fact_check"]["result"] == "failed"
    assert factcheck.shown(record, record, "minutes")["decisions"] == []


def test_names_however_the_document_writes_them():
    doc = "Motion by ALDERMAN O’BRIEN, seconded by Councillor Colon Hayes. Present: McCallum, Sanchez Parkinson."
    for text in ("Alderman O'Brien and Councillor Colon Hayes spoke.", "McCallum and Sanchez-Parkinson spoke."):
        assert problems(text, doc) == [], text
    # A name that's an ordinary word, after a title, is looked for too.
    assert problems("Councilor Hall spoke.", doc) == [("name", "Hall")]
    assert problems("McCallister spoke.", doc) == [("name", "McCallister")]
