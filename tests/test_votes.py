"""Tests for roll call votes read from minutes, without AI."""

from pathlib import Path

from pipeline import votes

FIXTURES = Path(__file__).parent / "fixtures"
# Malden's City Council minutes, January 5, 2026: one roll call, its names wrapping onto a
# second line and its last group on the next page, under the page's running header.
ROLL_CALLS = (FIXTURES / "legistar_roll_calls.pdf").read_bytes()
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
