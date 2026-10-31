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


def test_a_summarys_decision_labels_are_kept_beside_its_decisions():
    from pipeline import summarize
    result = {"headline": "", "summary": "", "decisions": [
        {"decision": "Approved $200,000 for soil cleanup at 448 High Street, 7-0.", "outcome": "approved", "quote": "q",
         "title": "$200,000 for soil cleanup at 448 High Street", "main": True, "topic": "budget"},
        {"decision": "Approved three appointments, 7-0.", "outcome": "approved", "quote": "q",
         "title": "Three appointments", "main": False, "topic": "government"}]}
    record = summarize.split_decisions(result)
    assert record["decisions"][1] == "Approved three appointments, 7-0."
    assert record["decision_titles"] == ["$200,000 for soil cleanup at 448 High Street", "Three appointments"]
    assert record["decision_main"] == [True, False] and record["decision_topics"] == ["budget", "government"]
    # A summary made before decisions had them has none.
    older = summarize.split_decisions({"decisions": [{"decision": "Approved X.", "outcome": "approved", "quote": "q"}]})
    assert "decision_titles" not in older and "decision_main" not in older


def labeled(problems=(), result="ok"):
    record = summary(["Approved $200,000 for soil cleanup at 448 High Street, 7-0.", "Approved B.", "Approved C."],
                     ["approved"] * 3, problems, result)
    record.update(decision_titles=["$200,000 for soil cleanup at 448 High Street", "B", "C"],
                  decision_main=[True, False, True], decision_topics=["budget", "government", "schools"])
    return record


def test_the_fact_check_keeps_each_shown_decisions_labels():
    record = labeled([{"field": "decisions", "entry": 2, "kind": "name", "what": "B"}], result="failed")
    shown = factcheck.shown(record, record, "minutes")
    assert shown["decision_titles"] == ["$200,000 for soil cleanup at 448 High Street", "C"]
    assert shown["decision_main"] == [True, True] and shown["decision_topics"] == ["budget", "schools"]
    # A translation has no titles of its own: the English ones aren't shown with it.
    translated = {"kind": "minutes", "headline": "Otro", "summary": "",
                  "decisions": ["Aprobó $200,000, 7-0.", "Aprobó B.", "Aprobó C."]}
    shown = factcheck.shown(translated, record, "minutes")
    assert "decision_titles" not in shown and shown["decision_topics"] == ["budget", "schools"]


def test_a_title_is_shown_only_with_its_decisions_numbers_and_names():
    decision = "Approved $200,000 for soil remediation at 448 High Street and $45,470 for firefighter gear, 7-0."
    assert build_site.title_supported("$200,000 for soil cleanup at 448 High Street", decision)
    assert not build_site.title_supported("$250,000 for soil cleanup at 448 High Street", decision)
    assert not build_site.title_supported("Soil cleanup at 448 Main Street", decision)
    assert build_site.title_supported("Law Department's extra funding cut",
                                      "Cut the law department supplemental appropriation.")
    record = labeled()
    record["decision_titles"][2] = "Comcast conduit at 75 Riverside Avenue"
    with i18n.use("en"):
        decisions = build_site.decisions_shown(record, record, record)
    assert [d.title for d in decisions] == ["$200,000 for soil cleanup at 448 High Street", "B", None]
    assert [d.main for d in decisions] == [True, False, True] and decisions[0].topic == "budget"


def test_main_decisions_come_first_and_routine_ones_after():
    with i18n.use("en"):
        decisions = build_site.decisions_shown(labeled(), labeled(), labeled())
    main, routine = build_site.main_and_routine(decisions)
    assert [str(d) for d in main] == ["Approved $200,000 for soil cleanup at 448 High Street, 7-0.", "Approved C."]
    assert [str(d) for d in routine] == ["Approved B."]
    # All routine, or none said: one list.
    assert build_site.main_and_routine(main) == (main, [])
    assert build_site.main_and_routine(routine) == (routine, [])
    assert build_site.main_and_routine(["Approved X."]) == (["Approved X."], [])


def test_pages_show_main_decisions_by_title_and_filter_by_topic(tmp_path):
    """A meeting page shows its main decisions by their titles, then "Also voted on"; the Decisions page folds the
    routine ones and has a topic filter for its script."""
    import json
    import shutil
    from conftest import BUILT_AT, DATA_DIR
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    labeled_one = None
    for path in sorted((data / "summaries").glob("*.json")):
        rec = json.loads(path.read_text())
        if rec.get("kind") == "minutes" and rec.get("decisions") and rec.get("is_minutes", True) and not labeled_one:
            for field in ("fact_check", "decision_evidence", "decision_pages"):
                rec.pop(field, None)
            rec["decisions"] = ["Approved the site plan for 12 Elm Street, 5-0.", "Approved the minutes of May 1, 2026.",
                                "Approved two appointments to the Library Board."]
            rec["decision_main"] = [True, False, False]
            rec["decision_topics"] = ["housing", "government", "government"]
            rec["decision_titles"] = ["Main decision title", "Minutes", "Library Board appointments"]
            path.write_text(json.dumps(rec))
            labeled_one = rec
    assert labeled_one
    build_site.build("gloucester", tmp_path / "site", data_dir=data, now=BUILT_AT)
    pages = [p.read_text() for p in (tmp_path / "site" / "meetings").glob("20*/index.html")
             if '<strong class="decision-title">Main decision title</strong>' in p.read_text()]
    assert len(pages) == 1
    page = pages[0]
    assert page.index("Main decision title") < page.index('<h3 class="decision-group">Also voted on</h3>')
    # Every vote, in the order taken, folded above the main ones; and where it met after what it decided.
    in_order = page[page.index('<details class="all-votes">'):page.index("</details>", page.index('<details class="all-votes">'))]
    grouped = page[page.index("</details>", page.index('<details class="all-votes">')):]
    assert page.index('<details class="all-votes">') < page.index('<strong class="decision-title">')
    assert grouped.count('data-topic="government"') == len(labeled_one["decisions"]) - 1
    assert "<summary>Show all 3 votes in order</summary>" in in_order
    assert in_order.index("The site plan for 12 Elm Street") < in_order.index("The minutes of May 1, 2026.")
    assert '<dl class="facts">' not in page[:page.index('class="decision-rows"')]
    # Where it met, where the page says, after what it decided, under its own heading.
    assert ('<h2 id="details">Meeting details</h2>' in page) == ('<dl class="facts">' in page)
    if '<dl class="facts">' in page:
        assert page.index('class="decision-rows"') < page.index('<h2 id="details">') < page.index('<dl class="facts">')
    decisions = (tmp_path / "site" / "meetings" / "decisions" / "index.html").read_text()
    # The main decisions first, by title; the meeting below is one line, its decisions folded.
    main = decisions[decisions.index('<h2 id="main">The main decisions</h2>'):decisions.index('<h2 id="by-meeting">')]
    assert "Main decision title" in main and "Library Board" not in main
    assert '<summary>Show its 3 decisions</summary>' in decisions
    assert '<div class="topic-filter" role="group"' in decisions and "decisions.js" in decisions
    assert 'data-topic="housing" aria-pressed="false">Housing and zoning</button>' in decisions
    assert 'data-topic="government" aria-pressed="false">City government</button>' in decisions


def test_the_meetings_page_lists_the_last_two_weeks(site_dir):
    """Under "Recent", the meetings of the last two weeks that were held, newest first, with what each decided."""
    import re
    page = (site_dir / "meetings" / "index.html").read_text()
    recent = page[page.index('<h2 id="recent">Recent</h2>'):page.index('href="/meetings/past/"')]
    days = re.findall(r'<time datetime="(2026-\d\d-\d\d)">', recent)
    assert days and days == sorted(days, reverse=True) and min(days) >= "2026-09-18" and max(days) < "2026-10-02"


def test_a_matter_discussed_is_named_first():
    assert build_site.discussion_parts("Law Department funding: Members debated whether to keep funding it.") == \
        ("Law Department funding", "Members debated whether to keep funding it.")
    # A summary from before matters were named, or a colon later in a sentence, is shown as it is.
    text = "Members discussed the city charter. A legal opinion said: it took effect."
    assert build_site.discussion_parts(text) == (None, text)
    assert build_site.discussion_parts("A very long first clause that runs on and on for words: then more.")[0] is None


def test_a_translations_own_titles_are_kept():
    from pipeline import translate
    record = labeled()
    # Titles are translated with the summary, and only where it has them.
    assert translate.fields("minutes", record)[-1] == "decision_titles"
    assert "decision_titles" not in translate.fields("minutes", {"decisions": ["Approved X."]})
    assert translate.english(record, "minutes")["decision_titles"] == record["decision_titles"]
    translated = {"kind": "minutes", "headline": "Otro", "summary": "",
                  "decisions": ["Aprobó $200,000 para limpiar el suelo en 448 High Street, 7-0.", "Aprobó B.", "Aprobó C."],
                  "decision_titles": ["$200,000 para limpiar el suelo en 448 High Street", "B", "C"], "translated": True}
    shown = factcheck.shown(translated, record, "minutes")
    assert shown["decision_titles"] == translated["decision_titles"]
    with i18n.use("es"):
        decisions = build_site.decisions_shown(shown, record, record)
    assert decisions[0].title == "$200,000 para limpiar el suelo en 448 High Street"


def test_decisions_that_settled_a_matter_come_before_those_put_off():
    ds = [build_site.Decision("Referred X.", outcome="referred"), build_site.Decision("Approved Y.", outcome="approved"),
          build_site.Decision("Tabled Z.", outcome="tabled"), build_site.Decision("Denied W.", outcome="denied")]
    final, put_off = build_site.final_and_put_off(ds)
    assert [str(d) for d in final] == ["Approved Y.", "Denied W."] and [str(d) for d in put_off] == ["Referred X.", "Tabled Z."]
    # All one or the other: one list.
    assert build_site.final_and_put_off(final) == (final, [])


def test_a_detail_that_only_says_the_title_again_isnt_shown():
    assert not build_site.adds_to("Request for update on City Charter", "The request for an update on the City Charter.")
    assert build_site.adds_to("$200,000 soil cleanup at 448 High Street",
                              "$200,000 from the capital stabilization fund for soil remediation at 448 High Street.")


def test_a_title_the_document_doesnt_support_is_dropped_not_its_decision():
    doc = ("PLANNING BOARD MINUTES. Motion by Squibb to approve the site plan for 12 Elm Street with the conditions in "
           "the staff report. Motion carried 3-0. " * 3)
    record = {"kind": "minutes", "headline": "", "summary": "",
              "decisions": ["Approved the site plan for 12 Elm Street, 3-0."],
              "decision_evidence": [{"outcome": "approved", "quote": "Motion by Squibb to approve the site plan for 12 Elm "
                                     "Street with the conditions in the staff report. Motion carried 3-0."}],
              "decision_titles": ["Site plan for 14 Elm Street"], "decision_main": [True], "decision_topics": ["housing"]}
    fc = factcheck.check(record, "minutes", [doc])
    assert fc["result"] == "ok" and [p["field"] for p in fc["problems"]] == ["decision_titles"]
    shown = factcheck.shown(dict(record, fact_check=fc), dict(record, fact_check=fc), "minutes")
    assert shown["decisions"] == record["decisions"] and shown["decision_titles"] == [None]
    record["decision_titles"] = ["Site plan for 12 Elm Street, with the staff report's conditions"]
    assert not [p for p in factcheck.check(record, "minutes", [doc])["problems"] if p["field"] == "decision_titles"]
