"""The weekly digest (pipeline/digest.py): which meetings and minutes each week's issue lists,
its pages, and the feed an email provider sends from."""

import json
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from xml.dom import minidom
from zoneinfo import ZoneInfo

import pytest

from pipeline import build_site, digest

TZ = ZoneInfo("America/New_York")


def meeting(day: str, minutes_at: str | None = None, **extra) -> dict:
    return {"date": day, "body": "City Council", "first_seen": "2026-09-01T06:00:00-04:00",
            "minutes": [{"fetched_at": minutes_at}] if minutes_at else [], **extra}


@pytest.mark.parametrize("since, today, expected", [
    (date(2026, 9, 26), date(2026, 10, 2), ["2026-09-27"]),                # a Saturday start; a Friday build
    (date(2026, 9, 27), date(2026, 10, 4), ["2026-10-04", "2026-09-27"]),  # Sundays at both ends count
    (date(2026, 9, 28), date(2026, 10, 3), []),                            # no Sunday yet
])
def test_sundays(since, today, expected):
    assert [d.isoformat() for d in digest.sundays(since, today)] == expected


def test_an_issue_lists_the_week_ahead_and_the_minutes_of_the_week_before():
    meetings = [
        meeting("2026-08-20", "2026-09-01T06:00:00-04:00"),  # minutes collected the first day: history
        meeting("2026-09-15", "2026-09-28T06:00:00-04:00"),  # minutes collected Monday of the week before
        meeting("2026-09-22", "2026-10-04T23:30:00-04:00"),  # and late on the issue's Sunday
        meeting("2026-09-24", "2026-10-05T00:30:00-04:00"),  # the next issue's
        meeting("2026-05-01", "2026-10-01T06:00:00-04:00"),  # a meeting long past: a source's history
        meeting("2026-09-29", "2026-10-02T06:00:00-04:00", minutes_summary={"is_minutes": False}),  # an agenda filed as minutes
        meeting("2026-10-04"), meeting("2026-10-05"), meeting("2026-10-11"), meeting("2026-10-12"),
    ]
    found = digest.issues(meetings, "2026-09-01T06:00:00-04:00", date(2026, 10, 7), TZ)
    issue = found[0]
    assert (issue["sunday"], issue["monday"], issue["end"], issue["url"]) == ("2026-10-04", "2026-10-05", "2026-10-11", "/digest/2026-10-05/")
    assert [m["date"] for m in issue["meetings"]] == ["2026-10-05", "2026-10-11"]
    assert [m["date"] for m in issue["minutes"]] == ["2026-09-15", "2026-09-22"]
    assert issue["send_at"] == datetime(2026, 10, 4, 17, 30, tzinfo=TZ)
    assert [day for day, _ in issue["days"]] == ["2026-10-05", "2026-10-11"]
    # Each earlier week with a meeting has its issue; those with nothing (September 6) have none.
    assert [i["sunday"] for i in found] == ["2026-10-04", "2026-09-27", "2026-09-20", "2026-09-13"]
    assert [m["date"] for m in found[1]["meetings"]] == ["2026-09-29", "2026-10-04"] and not found[1]["minutes"]


def test_no_issues_before_any_meeting_is_seen():
    assert digest.issues([], None, date(2026, 10, 7), TZ) == []


def test_digest_pages(site_dir):
    """The fixture town is built on Friday, October 2, so its one issue is Sunday, September 27's."""
    index = (site_dir / "digest" / "index.html").read_text()
    assert 'href="/digest/2026-09-28/"' in index and 'href="/digest/feed.xml"' in index
    page = (site_dir / "digest" / "2026-09-28" / "index.html").read_text()
    assert "<title>Week of September 28, 2026 | " in page
    assert 'href="/meetings/2026-09-28-human-rights-commission/"' in page
    # The fixture's calendar was last read September 27, so the week's list may be missing meetings.
    assert "hasn't been able to check the city's meeting listings since September 27, 2026" in page
    # The minutes collected so far are the town's history, collected the first day.
    assert 'id="minutes"' not in page
    assert 'href="/digest/"' in (site_dir / "index.html").read_text()
    assert 'href="/digest/"' in (site_dir / "about" / "index.html").read_text()
    sitemap = (site_dir / "sitemap.xml").read_text()
    assert "/digest/</loc>" in sitemap and "/digest/2026-09-28/</loc>" in sitemap


def test_digest_feed(site_dir):
    feed = minidom.parse(str(site_dir / "digest" / "feed.xml"))
    items = feed.getElementsByTagName("item")
    assert len(items) == 1
    text = lambda tag: items[0].getElementsByTagName(tag)[0].firstChild.data
    assert text("title") == "Week of September 28, 2026"
    assert text("link") == "https://gloucester-ma.publick.org/digest/2026-09-28/"
    # Due Sunday at 5:30 PM, the town's time: the email provider sends it then.
    assert text("pubDate") == "Sun, 27 Sep 2026 17:30:00 -0400"
    body = text("description")
    # Every link in the email is in full; no page's markup (navigation, scripts) comes with it.
    links = re.findall(r'href="([^"]+)"', body)
    assert links and all(link.startswith("https://gloucester-ma.publick.org/") for link in links)
    assert "<h2>Meetings</h2>" in body and "(AI summary)" in body and "<script" not in body and "<nav" not in body


@pytest.fixture(scope="module")
def with_new_minutes(data_dir):
    """The fixture town with one set of minutes collected on Wednesday, September 30, built on Sunday, October 4."""
    base = Path(tempfile.mkdtemp(prefix="publick-digest-"))
    shutil.copytree(data_dir, base / "data")
    path = base / "data" / "meetings" / "meetings.json"
    store = json.loads(path.read_text())
    store["archive-20079"]["minutes"][-1]["fetched_at"] = "2026-09-30T06:00:00-04:00"
    path.write_text(json.dumps(store))
    build_site.build("gloucester", base / "site", data_dir=base / "data", now=datetime(2026, 10, 4, 7, 0, tzinfo=TZ))
    return base / "site"


def test_new_minutes_and_their_decisions(with_new_minutes):
    page = (with_new_minutes / "digest" / "2026-10-05" / "index.html").read_text()
    minutes = page[page.index('id="minutes"'):]
    assert 'href="/meetings/2026-08-25-city-council/"' in minutes
    assert "Approved the site plan for 12 Main St" in minutes
    assert "pulled from each meeting's minutes by AI" in minutes
    feed = (with_new_minutes / "digest" / "feed.xml").read_text()
    assert feed.index("Week of October 5, 2026") < feed.index("Week of September 28, 2026")
