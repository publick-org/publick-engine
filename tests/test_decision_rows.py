"""Each decision as a row (macros.html decision_rows): its outcome, from the summary's decision_evidence as the fact
check leaves it (factcheck.shown) or else from the English decision's first words, and the vote count the decision
ends with, said beside it; the decision without the count, and in English without the verb its outcome says."""

import pytest

from pipeline import build_site, factcheck, i18n


@pytest.mark.parametrize("text, body, tally", [
    ("Approved the Comcast grant of location, 7-0.", "Approved the Comcast grant of location.", "7–0"),
    ("Approve the site plan. (3–0)", "Approve the site plan.", "3–0"),
    ("Aprobó la licencia, 7-0.", "Aprobó la licencia.", "7–0"),
    # A paper number, a count inside the decision, or one with more after it stays in the text.
    ("Tabled the supplemental appropriation (26-142).", "Tabled the supplemental appropriation (26-142).", None),
    ("Approved the consent agenda 6-1, including a grant.", "Approved the consent agenda 6-1, including a grant.", None),
    ("Approved Paper 26-142B, 7-0 on roll call.", "Approved Paper 26-142B, 7-0 on roll call.", None),
    # Counts that add up to more than a board's seats aren't a vote.
    ("Set the hearing for 10-27.", "Set the hearing for 10-27.", None),
])
def test_a_decision_ending_with_a_vote_count_gives_it(text, body, tally):
    assert build_site.split_tally(text) == (body, tally)
    decision = build_site.Decision(text, None, None, "approved")
    assert (decision.body, decision.tally, decision.outcome, str(decision)) == (body, tally, "approved", text)


@pytest.mark.parametrize("text, outcome", [
    ("Approved the minutes of May 21, 2026.", "approved"),
    ("Voted 5-0 to approve the variance.", "approved"),
    ("Appointed Jane Doe to the Library Board.", "approved"),
    ("Denied the special permit for 12 Elm Street.", "denied"),
    ("Failed 3-4 a motion to change the zoning.", "denied"),
    ("Tabled Paper 26-142.", "tabled"),
    ("Continued the hearing to July 9, 2026.", "continued"),
    ("Referred the order to the Finance Committee.", "referred"),
    ("Voted 3-0 to recommend that the City Council approve the budget.", "recommended"),
    ("Withdrew the petition.", "withdrawn"),
    ("Discussed the budget.", None),
])
def test_an_older_summarys_outcome_is_read_from_its_first_words(text, outcome):
    assert build_site.outcome_from_text(text) == outcome


def test_the_verb_the_outcome_says_is_left_off_in_english():
    with i18n.use("en"):
        assert build_site.without_outcome_verb("Approved the Comcast grant.", "approved") == "The Comcast grant."
        assert build_site.without_outcome_verb("Approved a request, and referred it.", "referred") == \
            "Approved a request, and referred it."
        assert build_site.without_outcome_verb("Failed a motion to amend.", "denied") == "Failed a motion to amend."
        assert build_site.without_outcome_verb("Approved X.", None) == "Approved X."
    with i18n.use("es"):
        assert build_site.without_outcome_verb("Aprobó la licencia.", "approved") == "Aprobó la licencia."
        # A summary not translated yet is in English on the Spanish page.
        assert build_site.without_outcome_verb("Approved the license.", "approved", english=True) == "The license."


def summary(decisions, outcomes, problems=(), result="ok"):
    return {"kind": "minutes", "summary": "", "headline": "", "decisions": decisions,
            "decision_evidence": [{"outcome": o, "quote": "q"} for o in outcomes],
            "fact_check": {"version": 1, "source": "pdf", "result": result, "problems": list(problems)}}


def test_the_fact_check_keeps_each_shown_decisions_outcome():
    # A decision the check hides goes with its outcome.
    record = summary(["Approved A, 3-0.", "Denied B.", "Tabled C."], ["approved", "denied", "tabled"],
                     [{"field": "decisions", "entry": 2, "kind": "name", "what": "B"}], result="failed")
    shown = factcheck.shown(record, record, "minutes")
    assert shown["decisions"] == ["Approved A, 3-0.", "Tabled C."]
    assert shown["decision_outcomes"] == ["approved", "tabled"]
    # One whose outcome it only noted is shown without one.
    record = summary(["Approved A, 3-0.", "Tabled C."], ["approved", "tabled"],
                     [{"field": "decisions", "entry": 2, "kind": "outcome", "what": "tabled, but"}])
    shown = factcheck.shown(record, record, "minutes")
    assert shown["decisions"] == ["Approved A, 3-0.", "Tabled C."] and shown["decision_outcomes"] == ["approved", None]
    decisions = build_site.decisions_shown(shown, shown, record)
    assert [d.outcome for d in decisions] == ["approved", None]


def test_without_outcomes_they_are_read_from_the_english():
    record = {"kind": "minutes", "decisions": ["Approved A, 3-0.", "Referred B to committee."]}
    translated = {"kind": "minutes", "decisions": ["Aprobó A, 3-0.", "Remitió B al comité."]}
    decisions = build_site.decisions_shown(translated, record, record)
    assert [(d.outcome, d.body, d.tally) for d in decisions] == [("approved", "Aprobó A.", "3–0"), ("referred", "Remitió B al comité.", None)]
    # Unchecked, the summary's own outcomes.
    record = {"kind": "recording", "decisions": ["Approved A.", "Approved B and referred it."],
              "decision_evidence": [{"outcome": "approved", "quote": "q"}, {"outcome": "referred", "quote": "q"}]}
    assert [d.outcome for d in build_site.decisions_shown(record, record, record)] == ["approved", "referred"]


def test_later_meetings_without_an_agenda_are_one_line_each(site_dir):
    """On the meetings page, a meeting after this week with no agenda yet is a short line under "Later"; this week's,
    and any with an agenda, are listed by day."""
    import re
    page = (site_dir / "meetings" / "index.html").read_text()
    later = page[page.index('<h3 id="later">'):page.index("</ul>", page.index('<h3 id="later">'))]
    by_day = page[page.index('<h2 id="upcoming">'):page.index('<h3 id="later">')]
    lines = re.findall(r'<li><time datetime="([\d-]+)">', later)
    assert lines and min(lines) >= "2026-10-09"
    for day in lines:
        assert f'id="day-{day}"' not in by_day or "No agenda posted yet" not in by_day
