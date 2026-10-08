"""Each recent and upcoming meeting's card for social media (pipeline/share_cards.py): what it says,
and the build drawing it and pointing the meeting's page at it."""

import re
import struct
from datetime import date

from conftest import BUILT_AT, DATA_DIR

from pipeline import build_site, share_cards

TODAY = date(2026, 10, 2)


def meeting(**kw):
    return {"date": "2026-10-06", "body": "Planning Board", "status": "scheduled", "preview": None, "minutes_summary": None, **kw}


def test_what_a_card_says():
    card = lambda **kw: share_cards.card(meeting(**kw), TODAY, "x.publick.org", "Tuesday, October 6, 2026")
    assert card(preview={"headline": "Public hearing on 37 Enon Street."}) == {
        "board": "Planning Board", "when": "Tuesday, October 6, 2026", "kicker": "On the agenda",
        "headline": "Public hearing on 37 Enon Street.", "foot": "AI summary of the agenda · x.publick.org"}
    # The minutes' headline over the agenda's, unless the minutes are something else.
    both = {"preview": {"headline": "A"}, "minutes_summary": {"headline": "B"}}
    assert card(**both)["kicker"] == "What was decided" and card(**both)["headline"] == "B"
    assert card(preview={"headline": "A"}, minutes_summary={"headline": "B", "is_minutes": False})["headline"] == "A"
    # A cancelled meeting says so, and nothing from its agenda.
    assert card(status="cancelled", preview={"headline": "A"})["kicker"] == "Cancelled"
    assert card(status="cancelled", preview={"headline": "A"})["headline"] == ""
    assert card()["kicker"] == "Coming up"
    assert card(date="2026-09-30")["kicker"] == ""
    # Only from CARD_DAYS back.
    assert card(date="2026-09-02") is not None and card(date="2026-09-01") is None


def test_a_card_is_versioned_by_what_it_says():
    c = share_cards.card(meeting(preview={"headline": "A"}), TODAY, "x", "Tue")
    assert share_cards.version(c) == share_cards.version(dict(c))
    assert share_cards.version(c) != share_cards.version({**c, "headline": "B"})


def test_the_build_draws_recent_meetings_cards(tmp_path):
    site = tmp_path / "site"
    build_site.build("gloucester", site, data_dir=DATA_DIR, now=BUILT_AT, share_cards=True)

    def og(slug):
        html = (site / "meetings" / slug / "index.html").read_text()
        return re.search(r'og:image" content="([^"]*)"', html).group(1), re.search(r'og:image:alt" content="([^"]*)"', html).group(1)

    image, alt = og("2026-09-28-historical-commission")
    assert re.fullmatch(r"https://gloucester-ma\.publick\.org/static/share/meetings/2026-09-28-historical-commission\.png\?v=[0-9a-f]{10}", image)
    assert alt.startswith("Historical Commission, Monday, September 28, 2026") and "On the agenda" in alt
    png = (site / "static" / "share" / "meetings" / "2026-09-28-historical-commission.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (1200, 630)
    # An older meeting, and every page that isn't a meeting's, keep the town's image.
    assert "/static/share/gloucester.png" in og("2026-07-14-city-council")[0]
    assert "/static/share/gloucester.png" in (site / "index.html").read_text()
    assert not (site / "static" / "share" / "meetings" / "2026-07-14-city-council.png").exists()
