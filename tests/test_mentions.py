"""Addresses on agendas and minutes (pipeline/mentions.py): which lines count, the house numbers each
stands for, which are new, and each street's and address's feed."""

import json
import xml.etree.ElementTree as ET
from datetime import datetime

from conftest import TZ
from pipeline import build_site, mentions

TOWN = {"name": "Salem", "state_abbr": "MA"}
CONFIG = {"site": {"name": "Salem Publick"}, "town": TOWN}
BASE = "https://salem-ma.publick.org"


def meeting(day, body, agenda="", minutes="", address=""):
    """A meeting with an agenda and minutes whose text is given, posted the morning of its day."""
    url = f"/meetings/2026-10-{day:02d}-{body.lower().replace(' ', '-')}/"
    posted = f"2026-10-{day:02d}T08:00:00-04:00"
    return {"url": url, "date": f"2026-10-{day:02d}", "body": body, "body_en": body, "address": address,
            "agenda": {"fetched_at": posted} if agenda else None, "preview": {"transcript": agenda} if agenda else None,
            "minutes_doc": {"fetched_at": posted} if minutes else None,
            "minutes_summary": {"transcript": minutes} if minutes else None}


def found(meetings):
    return mentions.new(mentions.find(meetings, TOWN, build_site.doc_text))


def test_house_numbers_a_mention_stands_for():
    assert mentions.numbers("62-66") == ["62", "64", "66"]
    assert mentions.numbers("11 - 13") == ["11", "13"]
    assert mentions.numbers("171 & 281R") == ["171", "281R"]
    assert mentions.numbers("228/230") == ["228", "230"]
    assert mentions.numbers("62 62R 64") == ["62", "62R", "64"]
    assert mentions.numbers("10-13") == ["10", "11", "12", "13"]
    # Wider than MAX_RANGE, or with a letter: its two ends.
    assert mentions.numbers("2-400") == ["2", "400"]
    assert mentions.numbers("62-66R") == ["62", "66R"]


def test_a_range_names_each_address_in_it():
    x, = found([meeting(1, "Planning Board", agenda="1. 62-66 Margin Street, a site plan")])
    assert x["streets"] == ["MARGIN ST"] and x["new"] == [("MARGIN ST", "62"), ("MARGIN ST", "64"), ("MARGIN ST", "66")]


def test_an_item_carried_from_agenda_to_agenda_is_new_once():
    """A council's list of pending papers repeats on every agenda; the minutes, and another board, are new."""
    line = "165/26 Order: a special permit at 12 Elm Street"
    xs = found([meeting(1, "City Council", agenda=f"3. {line}"),
                meeting(8, "City Council", agenda=f"5. {line}", minutes="Voted to approve the permit at 12 Elm Street."),
                meeting(9, "Planning Board", agenda=f"a) {line}")])
    assert [(x["body"], x["kind"], bool(x["new"])) for x in xs] == [
        ("City Council", "agenda", True), ("City Council", "agenda", False), ("City Council", "minutes", True),
        ("Planning Board", "agenda", True)]


def test_where_a_board_meets_is_left_out_for_that_board():
    """An address in the first lines of three of one board's agendas is where it meets; the same address as an
    item, lower down another board's agenda, isn't."""
    head = "SCHOOL COMMITTEE\nCollins Middle School Library, 29 Highland Avenue\n"
    xs = found([meeting(1, "School Committee", agenda=head + "1. Budget"),
                meeting(8, "School Committee", agenda=head + "1. Minutes"),
                meeting(15, "School Committee", agenda=head + "1. Calendar"),
                meeting(16, "City Council", agenda="Call to order\nRoll call\nMinutes\nPublic comment\nReports\n"
                                                   "Old business\nNew business\nCommittees\n9. A crosswalk at 29 Highland Avenue")])
    assert [x["body"] for x in xs] == ["City Council"]


def test_the_way_in_three_times_is_where_boards_meet():
    """'Parking Lot Entrance at 75 Sabin Street' under every agenda is directions; once, it's an item."""
    way_in = "Municipal Annex, 100 Freight Street (Parking Lot Entrance at 75 Sabin Street)"
    xs = found([meeting(d, b, agenda=f"Agenda\n1\n2\n3\n4\n5\n6\n7\n8\n{way_in}\n9. Signs at 4 Elm Street")
                for d, b in ((1, "Purchasing Board"), (8, "Planning Commission"), (15, "Zoning Board"))])
    assert {x["address"] for x in xs} == {"4 Elm Street"}
    once = found([meeting(1, "Traffic Commission", agenda="A crosswalk to the parking lot entrance at 128 Rogers Street")])
    assert [x["address"] for x in once] == ["128 Rogers Street"]


def test_feeds_for_each_street_and_address(tmp_path):
    xs = found([meeting(1, "Planning Board", agenda="1. 62-66 Main Street, a site plan\n2. 5 North Main Street, signs"),
                meeting(8, "Planning Board", agenda="1. 62-66 Main Street, a site plan"),
                meeting(9, "Zoning Board", minutes="Granted a variance for 64 Main Street.")])
    feeds = mentions.write_feeds(tmp_path, xs, CONFIG, BASE)
    assert feeds["MAIN ST"]["feed"] == "/streets/feeds/main-st.xml"
    assert feeds["MAIN ST"]["addresses"] == [["62", "/streets/feeds/main-st/62.xml"], ["64", "/streets/feeds/main-st/64.xml"],
                                             ["66", "/streets/feeds/main-st/66.xml"]]
    # North Main Street is its own street, not an address on Main Street.
    assert feeds["NORTH MAIN ST"]["addresses"] == [["5", "/streets/feeds/north-main-st/5.xml"]]
    channel = ET.parse(tmp_path / "streets/feeds/main-st/64.xml").getroot().find("channel")
    assert channel.findtext("title") == "Salem Publick: 64 Main Street"
    items = channel.findall("item")
    # Newest first; the agenda carried over to the 8th isn't repeated.
    assert [i.findtext("title") for i in items] == ["Zoning Board: minutes of October 9, 2026 – 64 Main Street",
                                                    "Planning Board: agenda for October 1, 2026 – 64 Main Street"]
    assert items[0].findtext("link") == BASE + "/meetings/2026-10-09-zoning-board/"
    assert items[0].findtext("description") == "Granted a variance for 64 Main Street."
    assert items[1].findtext("pubDate") == "Thu, 01 Oct 2026 08:00:00 -0400"
    assert (tmp_path / "streets/feeds/main-st/62.xml").exists() and not (tmp_path / "streets/feeds/main-st/5.xml").exists()


def test_a_feed_is_the_same_until_a_mention_comes(tmp_path):
    """No build time in a feed, so a daily run leaves the file as it was and publishing skips it."""
    meetings = [meeting(1, "Planning Board", agenda="1. 38 Pleasant Street, a porch")]
    mentions.write_feeds(tmp_path / "a", found(meetings), CONFIG, BASE)
    mentions.write_feeds(tmp_path / "b", found(meetings), CONFIG, BASE)
    path = "streets/feeds/pleasant-st/38.xml"
    assert (tmp_path / "a" / path).read_bytes() == (tmp_path / "b" / path).read_bytes()
    assert b"lastBuildDate" not in (tmp_path / "a" / path).read_bytes()
    assert mentions.write_feeds(None, found(meetings), CONFIG, BASE)["PLEASANT ST"]["feed"] == "/streets/feeds/pleasant-st.xml"
    assert not (tmp_path / "c").exists()


def test_recent_new_mentions():
    xs = found([meeting(1, "City Council", agenda="1. A permit at 12 Elm Street"),
                meeting(8, "City Council", agenda="1. A permit at 12 Elm Street"),
                meeting(9, "Planning Board", agenda="2. A site plan at 7 High Street")])
    recent = mentions.recent(xs, datetime(2026, 10, 30, 7, 0, tzinfo=TZ), days=25)
    assert recent["format"] == 1 and recent["days"] == 25
    # Newest first; the carried-over agenda isn't new, and the 1st is past the 25 days.
    assert [(m["board"], m["addresses"]) for m in recent["mentions"]] == [("Planning Board", ["7 HIGH ST"])]
    assert recent["mentions"][0]["names"] == ["7 High Street"]
    assert json.loads(json.dumps(recent)) == recent


def test_street_lookup_offers_the_feeds(site_dir):
    page = (site_dir / "streets" / "index.html").read_text()
    assert "follow_head" in page and "/static/js/streets.js?v=" in page


def test_alerts_pages_and_privacy_for_a_town_with_alerts(site_dir):
    """[alerts] follow in the test town's config: the pages the form and its emails lead to, and the About page."""
    for page in ("thanks", "problem", "confirm", "manage", "wrong"):
        html = (site_dir / "alerts" / page / "index.html").read_text()
        assert '<meta name="robots" content="noindex">' in html
    confirm = (site_dir / "alerts" / "confirm" / "index.html").read_text()
    assert 'action="/alerts/confirm"' in confirm and "/static/js/alerts.js?v=" in confirm
    # In English for now: no Spanish alerts pages, and the follow form only on the English street lookup.
    assert not (site_dir / "es" / "alerts").exists()
    assert 'data-follow="/alerts/follow"' in (site_dir / "streets" / "index.html").read_text()
    about = (site_dir / "about" / "index.html").read_text()
    assert "Another exception is an email alert for an address" in about and "https://postmarkapp.com/privacy-policy" in about


def test_alerts_need_their_email_service():
    import pytest
    assert mentions.alerts({}) is None and mentions.alerts({"alerts": {"follow": False}}) is None
    with pytest.raises(SystemExit, match="provider and privacy_url"):
        mentions.alerts({"slug": "salem", "alerts": {"follow": True}})
    assert mentions.alerts({"alerts": {"follow": True, "provider": "Postmark", "privacy_url": "https://p.example"}}) == {
        "provider": "Postmark", "privacy_url": "https://p.example"}
