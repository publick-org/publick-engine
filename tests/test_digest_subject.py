"""The weekly digest's subject, preheader and top story, chosen and written by a model (pipeline/digest_subject.py),
and how the build uses them (digest_subject.subject())."""

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from xml.dom import minidom
from zoneinfo import ZoneInfo

import pytest

from pipeline import build_site, cost_log, digest, digest_subject
from pipeline.config import load_config

TZ = ZoneInfo("America/New_York")
# The fixture town's issue of October 5 is built Sunday, October 4, and due at 5:30 PM (digest.EVENING_SEND_TIME).
SUNDAY_MORNING = datetime(2026, 10, 4, 9, 0, tzinfo=TZ)
GOOD = {"pick": "A2", "subject": "Open Space and Recreation Committee Tue on ADA compliance",
        "preheader": "Also: Council on Aging Board Wed, with Community updates"}


class FakeClient:
    """Stands in for anthropic.Anthropic: answers each request with the answer given, as JSON."""

    def __init__(self, answer: dict, stop_reason: str = "end_turn"):
        self.answer, self.stop_reason, self.calls = answer, stop_reason, []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **request):
        self.calls.append(request)
        return SimpleNamespace(id=f"msg_digest_{len(self.calls)}", stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.answer))],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=60))


@pytest.fixture
def town(data_dir):
    """A copy of the fixture town's data, and its config."""
    base = Path(tempfile.mkdtemp(prefix="publick-subject-"))
    shutil.copytree(data_dir, base / "data")
    yield base, load_config("gloucester")
    shutil.rmtree(base, ignore_errors=True)


def build(base: Path, now: datetime) -> tuple[str, str]:
    """The newest issue's feed title and email."""
    out = base / "site"
    build_site.build("gloucester", out, data_dir=base / "data", now=now)
    item = minidom.parse(str(out / "digest" / "feed.xml")).getElementsByTagName("item")[0]
    return (item.getElementsByTagName("title")[0].firstChild.data,
            item.getElementsByTagName("description")[0].firstChild.data)


def test_the_model_writes_the_subject_once_and_the_email_uses_it(town):
    base, config = town
    data = base / "data"
    client = FakeClient(GOOD)
    said = digest_subject.run(config, data, client, SUNDAY_MORNING)
    assert said == f"the issue of 2026-10-05: {GOOD['subject']}"
    # The model was given the week's meetings as the site shows them, and nothing else.
    request = client.calls[0]
    assert request["model"] == config["summaries"]["model"]
    assert "A2 · meeting to come · Open Space and Recreation Committee · Tuesday at 17:00" in request["messages"][0]["content"]
    assert "ADA compliance with the city's operations director" in request["messages"][0]["content"]
    record = json.loads((data / "digest" / "2026-10-05.json").read_text())
    assert (record["subject"], record["preheader"], record["problems"], record["tries"]) == (GOOD["subject"], GOOD["preheader"], [], 1)
    assert record["pick"]["url"] == "/meetings/2026-10-06-open-space-and-recreation-committee/"
    # What it cost is in the town's cost log, not in the record the data folder keeps.
    assert "cost" not in json.dumps(record)
    lines = cost_log.read_month(cost_log.LocalStore(data), "2026-10")
    assert [(e["kind"], e["issue"]) for e in lines if e["kind"] == "digest"] == [("digest", "2026-10-05")]
    assert json.loads((data / cost_log.LEDGER).read_text())["2026-10"]["digest_cost"] > 0
    # Asked again with nothing changed, it isn't asked.
    assert "already" in digest_subject.run(config, data, client, SUNDAY_MORNING) and len(client.calls) == 1

    title, body = build(base, SUNDAY_MORNING.replace(hour=10))
    assert title == GOOD["subject"]
    # The preheader, hidden, first; then the top story, linking its meeting, said once as written by AI.
    assert f'mso-hide:all;">{GOOD["preheader"]}&zwnj;' in body
    top = body[body.index("Coming up this week"):body.index("This week</p>")]
    assert GOOD["subject"] in top and "/meetings/2026-10-06-open-space-and-recreation-committee/?ref=digest-email" in top
    assert "Open Space and Recreation Committee · Tuesday, October 6, 2026 · 5:00 PM" in top
    assert "The agenda, and how to take part" in top and "Chosen and written by AI" in top
    assert digest.email_problems(body, "https://gloucester-ma.publick.org", base / "site") == []


def test_an_answer_with_a_fact_not_in_the_site_isnt_used(town):
    base, config = town
    data = base / "data"
    client = FakeClient({**GOOD, "subject": "Open Space Committee Tue on a $2.4M ADA plan"})
    said = digest_subject.run(config, data, client, SUNDAY_MORNING)
    assert "the answer wasn't used" in said and "'2.4'" in said
    record = json.loads((data / "digest" / "2026-10-05.json").read_text())
    assert "subject" not in record and record["problems"] and record["answer"]["subject"].startswith("Open Space")
    # The email goes out with the week's date as its subject, and no top story.
    title, body = build(base, SUNDAY_MORNING.replace(hour=10))
    assert title == "Week of October 5, 2026" and "Coming up this week" not in body


def test_an_issue_is_asked_about_a_few_times_at_most(town):
    base, config = town
    data = base / "data"
    (data / "digest").mkdir()
    (data / "digest" / "2026-10-05.json").write_text(json.dumps({"input": "older", "tries": digest_subject.MAX_TRIES}))
    client = FakeClient(GOOD)
    assert "asked 3 times already" in digest_subject.run(config, data, client, SUNDAY_MORNING)
    assert client.calls == []


def test_nothing_is_asked_outside_an_issues_weekend(town):
    base, config = town
    client = FakeClient(GOOD)
    # The Wednesday after: the issue of October 5 went out Sunday evening.
    assert digest_subject.run(config, base / "data", client, datetime(2026, 10, 7, 9, 0, tzinfo=TZ)) == "no issue waiting to be sent"
    assert client.calls == []


def test_an_answer_cut_short_isnt_used(town):
    base, config = town
    said = digest_subject.run(config, base / "data", FakeClient(GOOD, stop_reason="max_tokens"), SUNDAY_MORNING)
    assert "stopped early (max_tokens)" in said


def test_every_number_name_day_and_outcome_comes_from_the_candidate():
    source = ("City Council Tuesday Public hearing on FY27 tax rates, and approved: $2,383,850 for 448 High Street; "
              "a 4-4 tie at 1.75")
    days = {"Tuesday", "Tue", "Tues"}
    check = lambda text: digest_subject.ungrounded(text, source, days)
    assert check("Council hearing Tue on FY27 tax rates") == []
    assert check("Council approved $2,383,850 for 448 High Street") == []
    assert check("Council tie, 4-4, at 1.75") == []
    # Rounded, changed, cut from a bigger number, or made up: each is named.
    assert check("$2.4M for 448 High St") == ["2.4", "M", "St"]
    assert check("Council approves $2 million for schools") == ["2"]
    assert check("$850 for 448 High Street") == ["850"]
    assert check("Council hearing Tue on FY28 tax rates") == ["28"]
    assert check("Mayor Smith on tax rates") == ["Mayor", "Smith"]
    # A day not the meeting's, and an outcome the decisions don't have.
    assert check("Council hearing Wed on FY27 tax rates") == ["Wed"]
    assert check("Council denied $2,383,850 for 448 High Street") == ["denied"]
    # A sentence's first word may be capitalized where the source has it lowercase; a lead-in is allowed.
    assert check("Tax rates hearing Tuesday") == []
    assert check("Also: Hearing on tax rates") == []


def test_a_subject_may_give_the_meetings_date_and_time():
    c = {"id": "A1", "kind": "ahead", "board": "City Council", "day": "Tuesday", "date": "2026-11-17", "time": "18:30",
         "line": "Public hearing on FY27 tax rates.", "items": []}
    answer = {"pick": "A1", "subject": "Council hearing Nov 17 at 6:30 PM on FY27 tax rates", "preheader": ""}
    assert digest_subject.problems(answer, [c]) == []
    assert digest_subject.problems({**answer, "subject": "Council hearing Nov 18 on FY27 tax rates"}, [c]) == \
        ["its subject's '18' isn't in what the site says of A1"]


@pytest.mark.parametrize("answer, expected", [
    ({"pick": "A1", "subject": "Council hearing Tue on FY27 tax rates", "preheader": ""}, []),
    ({"pick": "A9", "subject": "Council hearing Tue on FY27 tax rates", "preheader": ""}, ["it picked 'A9', which isn't a candidate"]),
    ({"pick": "A1", "subject": "", "preheader": ""}, ["it has no subject"]),
    ({"pick": "A1", "subject": "Council hearing Tue on FY27 tax rates and more…", "preheader": ""},
     ["its subject is cut off or runs over a line"]),
    ({"pick": "A1", "subject": "Council hearing Tue on FY27 tax rates" + " and tax rates" * 3, "preheader": ""},
     ["its subject is 79 characters, past 60"]),
    ({"pick": "A1", "subject": "Council hearing Tue on FY27 tax rates", "preheader": "Also: Mayor's budget"},
     ["its preheader's 'Mayor' isn't in what the site says of any candidate"]),
])
def test_what_keeps_an_answer_from_being_used(answer, expected):
    cands = [{"id": "A1", "kind": "ahead", "board": "City Council", "day": "Tuesday", "date": "2026-11-17",
              "line": "Public hearing on FY27 tax rates.", "items": []}]
    assert digest_subject.problems(answer, cands) == expected


def test_an_issue_already_sent_keeps_its_subject():
    """Before an issue is due, the model's answer is used only while its candidate is still one of the issue's; after,
    the issue keeps what it was sent with."""
    record = {"subject": "Council hearing Tue on FY27 tax rates", "preheader": "",
              "pick": {"id": "A1", "kind": "ahead", "url": "/meetings/gone/", "board": "City Council", "day": "Tuesday",
                       "date": "2026-11-17", "line": "Public hearing on FY27 tax rates.", "items": []}}
    issue = {"meetings": [], "send_at": datetime(2026, 11, 15, 7, 0, tzinfo=TZ),
             "decided": {"entries": [], "lead": None, "lead_title": None}}
    before = digest_subject.subject(issue, ["City Council"], record, datetime(2026, 11, 14, 12, 0, tzinfo=TZ))
    assert (before["subject"], before["pick"], before["written"]) == (None, None, False)
    after = digest_subject.subject(issue, ["City Council"], record, datetime(2026, 11, 16, 12, 0, tzinfo=TZ))
    assert (after["subject"], after["pick"]["url"], after["written"]) == (record["subject"], "/meetings/gone/", True)


@pytest.mark.parametrize("now, asking", [
    (datetime(2026, 11, 20, 23, 59, tzinfo=TZ), False),  # Friday night
    (datetime(2026, 11, 21, 0, 0, tzinfo=TZ), True),     # Saturday: the issue of Sunday, November 22 is built
    (datetime(2026, 11, 22, 6, 59, tzinfo=TZ), True),    # Sunday, before the send
    (datetime(2026, 11, 22, 7, 0, tzinfo=TZ), False),    # sent
    (datetime(2026, 11, 25, 12, 0, tzinfo=TZ), False),   # Wednesday
    (datetime(2026, 10, 4, 9, 0, tzinfo=TZ), True),      # an evening issue's Sunday (before digest.MORNING_FROM)
])
def test_the_step_reads_nothing_outside_an_issues_weekend(now, asking):
    assert digest_subject.asking_now(now) is asking


def test_an_answer_that_fails_keeps_the_last_one_that_passed(town):
    base, config = town
    data = base / "data"
    digest_subject.run(config, data, FakeClient(GOOD), SUNDAY_MORNING)
    record = json.loads((data / "digest" / "2026-10-05.json").read_text())
    # The candidates change (another run's summaries), and the next answer has a made-up figure.
    record["input"] = "older"
    (data / "digest" / "2026-10-05.json").write_text(json.dumps(record))
    said = digest_subject.run(config, data, FakeClient({**GOOD, "subject": "Open Space Committee Tue on a $9M plan"}),
                              SUNDAY_MORNING)
    assert "wasn't used" in said
    kept = json.loads((data / "digest" / "2026-10-05.json").read_text())
    assert (kept["subject"], kept["tries"]) == (GOOD["subject"], 2) and kept["problems"]
    title, _ = build(base, SUNDAY_MORNING.replace(hour=10))
    assert title == GOOD["subject"]


def test_a_sent_issue_whose_meeting_moved_keeps_its_subject_and_loses_its_story(town):
    """An issue in the feed weeks after it was sent links no page the site no longer has."""
    base, config = town
    data = base / "data"
    digest_subject.run(config, data, FakeClient(GOOD), SUNDAY_MORNING)
    record = json.loads((data / "digest" / "2026-10-05.json").read_text())
    record["pick"]["url"] = "/meetings/2026-10-06-gone/"
    (data / "digest" / "2026-10-05.json").write_text(json.dumps(record))
    title, body = build(base, datetime(2026, 10, 7, 9, 0, tzinfo=TZ))
    assert title == GOOD["subject"] and "Coming up this week" not in body and "/meetings/2026-10-06-gone/" not in body


def test_the_subject_has_a_model_of_its_own_or_the_summaries():
    summaries = {"model": "claude-haiku-5-5", "input_price": 0.1, "output_price": 0.5, "effort": "high"}
    assert digest_subject.settings({"summaries": summaries}) == {
        "model": "claude-haiku-5-5", "input_price": 0.1, "output_price": 0.5, "effort": None}
    chosen = digest_subject.settings({"summaries": {**summaries, "digest_model": "claude-sonnet-5-5",
                                                    "digest_input_price": 2.0, "digest_output_price": 10.0,
                                                    "digest_effort": "low"}})
    assert chosen == {"model": "claude-sonnet-5-5", "input_price": 2.0, "output_price": 10.0, "effort": "low"}
    request = digest_subject.request(chosen["model"], "Gloucester", "Sunday", [], chosen["effort"])
    assert request["model"] == "claude-sonnet-5-5" and request["output_config"]["effort"] == "low"


def test_a_digest_model_without_its_prices_asks_nothing(town):
    base, config = town
    client = FakeClient(GOOD)
    config = {**config, "summaries": {**config["summaries"], "digest_model": "claude-sonnet-5-5"}}
    assert digest_subject.run(config, base / "data", client, SUNDAY_MORNING).startswith("skipped:")
    assert client.calls == []
