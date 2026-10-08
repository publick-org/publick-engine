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
