"""Tests for roll call votes read from minutes, without AI."""

from pathlib import Path

from pipeline import votes

FIXTURES = Path(__file__).parent / "fixtures"
# Malden's City Council minutes, January 5, 2026: one roll call, its names wrapping onto a
# second line and its last group on the next page, under the page's running header.
ROLL_CALLS = (FIXTURES / "legistar_roll_calls.pdf").read_bytes()
# Malden's City Council minutes, April 21, 2026: votes under the consent agenda and the adjournment,
# sections with no paper of their own, after papers 229-26 (a guest) and 153-26 (a tabled petition).
SECTIONS = (FIXTURES / "legistar_sections.pdf").read_bytes()
COUNCIL = ["Peg Crowe", "Paul Condon", "Amanda Linehan", "Ryan O'Malley", "Ari Taylor", "Stephen Winslow",
           "Chris Simonelli", "Jadeane Sica", "Michelle Luong", "Karen Colón Hayes", "Carey McDonald"]


def vote(groups, outcome="The motion carried by the following vote:"):
    return {"item": "1-26", "motion": "A motion was made by ...", "outcome": outcome, "groups": groups, "quote": ""}


def group(label, names):
    return {"label": label, "count": len(names), "names": names}


def test_a_roll_call_is_read_with_its_motion_and_outcome():
    (v,) = votes.roll_calls(ROLL_CALLS, COUNCIL)
    assert v["item"] == "1-26"
    assert v["motion"] == ("A motion was made by Councillor Simonelli, seconded by Councillor Taylor, "
                           "to elect Councillor Linehan as the 2026 Council President.")
    # The outcome's sentence wraps onto a second line.
    assert v["outcome"] == "The motion carried by the following vote." and v["result"] == "carried"
    assert v["checked"] and v["problems"] == [] and v["not_recorded"] == []


def test_wrapped_names_and_a_group_on_the_next_page_are_read():
    (v,) = votes.roll_calls(ROLL_CALLS, COUNCIL)
    yea, nay = v["groups"]
    assert (yea["label"], yea["count"], len(yea["members"])) == ("Yea", 10, 10)
    # "Colon Hayes" in the minutes is Karen Colón Hayes; "Luong" was on the wrapped line.
    assert yea["members"][0] == "Karen Colón Hayes" and yea["members"][-1] == "Michelle Luong"
    assert nay["members"] == ["Ryan O'Malley"]
    # The running header between them isn't part of the vote.
    assert "Minutes" not in v["quote"] and v["quote"].endswith("Nay: 1 - O'Malley")


def test_a_name_that_isnt_a_member_leaves_the_vote_unchecked():
    members = [m for m in COUNCIL if m != "Peg Crowe"]
    (v,) = votes.roll_calls(ROLL_CALLS, members)
    assert not v["checked"] and "'Crowe' isn't one of the members" in v["problems"]


def test_a_member_the_minutes_dont_name_is_not_recorded():
    v = votes.check(vote([group("Yea", ["Condon", "Crowe"])]), ["Paul Condon", "Peg Crowe", "Ari Taylor"])
    assert v["checked"] and v["not_recorded"] == ["Ari Taylor"]


def test_counts_must_match_the_names():
    v = votes.check(vote([{"label": "Yea", "count": 3, "names": ["Condon", "Crowe"]}]), ["Paul Condon", "Peg Crowe"])
    assert not v["checked"] and v["problems"] == ["Yea: 3 stated, 2 named"]


def test_a_member_named_twice_leaves_the_vote_unchecked():
    v = votes.check(vote([group("Yea", ["Condon"]), group("Nay", ["Condon"])]), ["Paul Condon"])
    assert not v["checked"] and v["problems"] == ["Paul Condon is named twice"]


def test_a_surname_two_members_share_isnt_guessed():
    v = votes.check(vote([group("Yea", ["Smith"])]), ["Ann Smith", "Bob Smith"])
    assert not v["checked"] and v["problems"] == ["'Smith' isn't one of the members"]


def test_a_stated_tally_must_match_the_groups():
    groups = [group("Yea", ["Condon", "Crowe"]), group("Nay", ["Taylor"])]
    members = ["Paul Condon", "Peg Crowe", "Ari Taylor"]
    assert votes.check(vote(groups, "The motion carried by a 2-1 vote."), members)["checked"]
    v = votes.check(vote(groups, "The motion carried by a 2-2 roll call vote."), members)
    assert not v["checked"] and v["problems"] == ["the outcome says 2-2, the groups 2-1"]


def test_only_a_supported_style_is_read():
    scan = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()
    assert votes.roll_calls(scan, COUNCIL) == []


def test_members_come_from_the_body_whose_meetings_these_are():
    config = {"officials": {"bodies": [
        {"name": "City Council", "members": [{"name": "Paul Condon"}]},
        {"name": "School Committee", "board": "Malden School Committee", "members": [{"name": "Dawn Macklin"}]},
    ]}}
    assert votes.members_for(config, "City Council") == ["Paul Condon"]
    assert votes.members_for(config, "Malden School Committee") == ["Dawn Macklin"]
    assert votes.members_for(config, "Planning Board") == []
    assert votes.members_for({}, "City Council") == []


def test_votes_are_read_again_when_the_rules_or_members_change():
    minutes = {"kind": "minutes"}
    assert not votes.current(minutes, COUNCIL)
    record = votes.read(minutes, ROLL_CALLS, COUNCIL)
    assert len(record["votes"]) == 1 and votes.current(record, COUNCIL)
    assert not votes.current(record, COUNCIL[:-1])
    assert not votes.current({**record, "votes_version": votes.VERSION - 1}, COUNCIL)


def test_agendas_and_bodies_without_members_have_no_votes():
    agenda = {"kind": "agenda"}
    assert votes.current(agenda, COUNCIL) and votes.read(agenda, ROLL_CALLS, COUNCIL) == agenda
    minutes = {"kind": "minutes"}
    assert votes.current(minutes, []) and votes.read(minutes, ROLL_CALLS, []) == minutes


def test_the_review_lists_every_roll_call_with_its_minutes(tmp_path):
    import json
    from pipeline import summarize
    meeting = {"date": "2026-01-05", "title": "City Council Meeting", "body": "City Council",
               "minutes": [{"id": "m", "file": "m.pdf", "sha256": "abc", "source_url": "https://example.org/m.pdf"}]}
    (tmp_path / "meetings").mkdir()
    (tmp_path / "meetings" / "meetings.json").write_text(json.dumps({"m": meeting}))
    summarize.save_record(tmp_path, "abc", votes.read({"kind": "minutes", "headline": "x"}, ROLL_CALLS,
                                                      [m for m in COUNCIL if m != "Peg Crowe"]))
    text = votes.report({}, tmp_path)
    assert text.startswith("1 roll calls, 0 checked")
    assert "2026-01-05 City Council  https://example.org/m.pdf" in text
    assert "NOT CHECKED: 'Crowe' isn't one of the members" in text


def test_minutes_whose_text_cant_be_read_have_no_roll_calls(monkeypatch):
    from pipeline import pdftext

    def unreadable(pdf):
        raise ValueError("bad xref table")
    monkeypatch.setattr(pdftext, "lines", unreadable)
    assert votes.roll_calls(ROLL_CALLS, COUNCIL) == []


def test_a_vote_under_a_section_with_no_paper_has_no_item():
    found = votes.roll_calls(SECTIONS, COUNCIL)
    first, last = found[0], found[-1]
    assert (first["item"], first["section"]) == (None, "CONSENT AGENDA")
    assert first["motion"].endswith("that this be approved on the Consent Agenda.")
    assert (last["item"], last["section"]) == (None, "ADJOURNMENT") and "adjourned" in last["motion"]
    # Votes on a paper keep it, and its section: a numbered list inside an item isn't a section.
    assert [(v["item"], v["section"]) for v in found[1:4]] == [
        ("224-26", "APPOINTMENTS & ELECTIONS"), ("225-26", "APPOINTMENTS & ELECTIONS"), ("226-26", "COMMUNICATIONS")]
    assert all(v["checked"] for v in found) and len(found) == 21


def test_a_section_heading_is_a_numbered_line_in_capitals():
    assert votes.SECTION.match("12. ADJOURNMENT").group(1) == "ADJOURNMENT"
    assert votes.SECTION.match("6. CONSENT AGENDA")
    assert not votes.SECTION.match("1. The elimination of the line items associated with Ward Improvement Funds;")
    assert not votes.SECTION.match("173-26 Response to Friedman OML March 12")



def test_a_roll_call_keeps_its_page_and_its_item_as_the_minutes_describe_it():
    found = votes.roll_calls(SECTIONS, COUNCIL)
    consent, first = found[0], found[1]
    assert (consent["page"], consent["subject"]) == (2, None)
    assert first["page"] == 3 and first["subject"] == (
        "Mayoral Reappointment: John P. King of 67 Upham Street, Malden, as member of the Board of Appeal, having "
        "served since 2000, said term to commence upon confirmation by the City Council and expire May 1, 2029.")
    # A long description is cut at a word.
    communication = found[3]
    assert communication["item"] == "226-26" and communication["subject"].endswith("…")
    assert len(communication["subject"]) <= votes.SUBJECT_CHARS + 1
    (v,) = votes.roll_calls(ROLL_CALLS, COUNCIL)
    assert (v["page"], v["subject"]) == (1, "Election of City Council President for 2026")


def test_an_attachment_named_for_a_paper_doesnt_begin_an_item():
    # Under "Attachments:", a file's name can start with a paper number, its own or another's.
    assert votes.ITEM.match("263-26") and not votes.ITEM.match("263-26 City council Meeting letter")
    assert not votes.ITEM.match("73-26 Response to OML Complaint reg. 1.20.2026")


def test_votes_are_shown_only_where_the_town_says_so():
    assert not votes.show({}) and not votes.show({"votes": {}})
    assert votes.show({"votes": {"show": True}}) and not votes.show({"votes": {"show": False}})
    import pytest
    with pytest.raises(SystemExit, match=r"\[votes\] show"):
        votes.show({"slug": "malden", "votes": {"show": "yes"}})


def test_only_checked_roll_calls_read_by_current_rules_against_the_members_are_shown():
    record = votes.read({"kind": "minutes"}, SECTIONS, COUNCIL)
    record["votes"][0] = {**record["votes"][0], "checked": False}
    shown, held = votes.shown(record, COUNCIL)
    assert len(shown) == len(record["votes"]) - 1 and held == 1 and all(v["checked"] for v in shown)
    assert shown == [v for v in record["votes"] if v["checked"]]
    # Read by rules from before a consent agenda's vote lost the last paper's number: not shown.
    assert votes.shown({**record, "votes_version": 1}, COUNCIL) is None
    assert votes.shown({**record, "votes_version": votes.SHOWN_FROM}, COUNCIL) is not None
    # Read against other members, until the next run reads them again.
    assert votes.shown(record, COUNCIL[:-1]) is None
    # Not read, not minutes, or a document filed as minutes that isn't.
    assert votes.shown({"kind": "minutes"}, COUNCIL) is None
    assert votes.shown({**record, "kind": "recording"}, COUNCIL) is None
    assert votes.shown({**record, "is_minutes": False}, COUNCIL) is None
    assert votes.shown(None, COUNCIL) is None and votes.shown(record, []) is None


def test_a_meeting_page_shows_its_checked_roll_calls(config, data_dir, tmp_path, monkeypatch):
    import copy
    import html
    import re
    import shutil
    from conftest import BUILT_AT
    from pipeline import build_site, summarize
    config = copy.deepcopy(config)  # the session's config, shared with other tests
    config["site"]["languages"] = ["en", "es"]
    config["votes"] = {"show": True}
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    members = votes.members_for(config, "City Council")
    meetings = build_site.load_meetings(data, BUILT_AT.date(), config["summaries"]["model"])["all"]
    council = [m for m in meetings if m["body"] == "City Council" and m["minutes_summary"]]
    shown_at, older = council[-1], council[0]
    a, b, c, d, e, f, g = members
    named = [{"label": "Yea", "count": 4, "names": [a, b, c, d]}, {"label": "Nay", "count": 1, "names": [e]},
             {"label": "Recused", "count": 1, "names": [f]}]
    vote = votes.check({"item": "301-26", "subject": "Order: That the city repave Main Street.", "section": "ORDERS",
                        "page": 4, "motion": "A motion was made by Councillor Avery, seconded by Councillor Blake, "
                        "that the Order be adopted.", "outcome": "The motion carried by the following vote:",
                        "result": "carried", "notes": ["Councillor Gray left the meeting at 8:10 PM."],
                        "groups": named, "quote": ""}, members)
    wrong = votes.check({"item": "302-26", "subject": None, "section": "ORDERS", "page": 5,
                         "motion": "A motion was made to table the miscounted order.", "outcome": None, "result": "carried",
                         "notes": [], "groups": [{"label": "Yea", "count": 6, "names": [a, b]}], "quote": ""}, members)
    assert vote["checked"] and vote["not_recorded"] == [g] and not wrong["checked"]
    for m, version in ((shown_at, votes.VERSION), (older, 1)):
        sha = m["minutes_doc"]["sha256"]
        record = summarize.cached(data, sha, "minutes", current=False)
        summarize.save_record(data, sha, {**record, "votes": [vote, wrong], "votes_version": version,
                                          "votes_members": members})
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)

    page = (out / "meetings" / shown_at["slug"] / "index.html").read_text()
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    assert 'id="roll-calls"' in page
    assert "Item 301-26 : Order: That the city repave Main Street." in text
    assert "that the Order be adopted." in text and "The motion carried." in text
    assert f"Yea (4) {a}, {b}, {c}, {d}" in text and f"Nay (1) {e}" in text and f"Recused (1) {f}" in text
    # A member the minutes don't name is "not recorded", never absent or a vote.
    assert f"Not recorded in this vote (1) {g}" in text
    assert "The minutes add: Councillor Gray left the meeting at 8:10 PM." in text
    assert f'href="{shown_at["minutes_doc"]["source_url"]}#page=4"' in page and "In the minutes, page 4" in text
    # The unchecked one isn't shown, only counted.
    assert "miscounted" not in page and "302-26" not in page
    assert "1 roll call in these minutes isn't shown" in text
    # Which minutes they're read from, and which aren't: the older meeting's were read by older rules.
    read = [m for m in council if m is not older]
    assert (f"City Council minutes read for roll calls: {len(read)} meetings, from "
            f"{build_site.format_date(read[0]['date'], 'plain')} to {build_site.format_date(read[-1]['date'], 'plain')}.") in text
    assert f"The minutes of one other meeting on this site, {build_site.format_date(older['date'], 'plain')}, aren't included." in text
    assert 'id="roll-calls"' not in (out / "meetings" / older["slug"] / "index.html").read_text()
    # In Spanish, the minutes' own words are marked as English.
    es = (out / "es" / "meetings" / shown_at["slug"] / "index.html").read_text()
    assert "Votaciones nominales" in es and "No consta en esta votación (1)" in es
    assert '<blockquote class="roll-call-motion" lang="en">' in es


def test_no_votes_are_shown_unless_the_town_says_so(site_dir):
    # The tests' town collects votes but doesn't show them.
    assert not any('id="roll-calls"' in p.read_text() for p in (site_dir / "meetings").rglob("index.html"))
