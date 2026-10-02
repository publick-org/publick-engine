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
