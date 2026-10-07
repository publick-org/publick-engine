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
    # Each says it's from the email, for the page counts (count.js).
    assert all(link.endswith("?ref=digest-email") for link in links)
    assert "<script" not in body and "<nav" not in body
    # It says what's inside first, and once, above the meetings, that their lines are written by AI.
    assert body.startswith("<p>10 meetings this week.</p>")
    assert body.count("is an AI summary of its agenda") == 1 and "(AI summary)" not in body


@pytest.fixture(scope="module")
def with_new_minutes(data_dir):
    """The fixture town with one set of minutes, recording seven decisions, collected on Wednesday,
    September 30, built on Sunday, October 4."""
    base = Path(tempfile.mkdtemp(prefix="publick-digest-"))
    shutil.copytree(data_dir, base / "data")
    path = base / "data" / "meetings" / "meetings.json"
    store = json.loads(path.read_text())
    minutes = store["archive-20079"]["minutes"][-1]
    minutes["fetched_at"] = "2026-09-30T06:00:00-04:00"
    path.write_text(json.dumps(store))
    summary_path = base / "data" / "summaries" / f"{minutes['sha256']}.json"
    summary = json.loads(summary_path.read_text())
    summary["decisions"] += [f"Approved the site plan for {n} Main St" for n in range(14, 20)]
    summary["decision_evidence"] *= 7
    summary_path.write_text(json.dumps(summary))
    build_site.build("gloucester", base / "site", data_dir=base / "data", now=datetime(2026, 10, 4, 7, 0, tzinfo=TZ))
    return base / "site"


def test_new_minutes_and_their_decisions(with_new_minutes):
    page = (with_new_minutes / "digest" / "2026-10-05" / "index.html").read_text()
    minutes = page[page.index('id="minutes"'):]
    assert 'href="/meetings/2026-08-25-city-council/"' in minutes
    assert "Approved the site plan for 12 Main St" in minutes
    assert "pulled from each meeting's minutes by AI" in minutes
    assert "2 more decisions on the meeting's page" in minutes
    feed = minidom.parse(str(with_new_minutes / "digest" / "feed.xml"))
    titles = [t.firstChild.data for t in feed.getElementsByTagName("title")]
    assert titles.index("Week of October 5, 2026") < titles.index("Week of September 28, 2026")
    body = feed.getElementsByTagName("item")[0].getElementsByTagName("description")[0].firstChild.data
    assert "Minutes from 1 meeting, with what it decided." in body.split("</p>")[0]
    assert body.count("written by AI from its minutes") == 1
    # The decisions not listed are a link away.
    assert ('<a href="https://gloucester-ma.publick.org/meetings/2026-08-25-city-council/?ref=digest-email">'
            "2 more decisions on the meeting's page</a>" in body)


def test_the_signup_form_posts_to_the_site_itself(site_dir):
    """[digest] signup in the test town's config: the form on /digest/, which the network's Worker answers."""
    index = (site_dir / "digest" / "index.html").read_text()
    form = index[index.index('<section aria-labelledby="signup">'):index.index("</section>")]
    assert '<form class="search-form" action="/digest/subscribe" method="post">' in form
    assert 'name="email" type="email" required' in form and 'name="lang" value="en"' in form
    # The field for bots is hidden from people, and from keyboards and screen readers with it.
    assert re.search(r'<div hidden>\s*<label for="signup-website">', form)
    assert "Buttondown, the service that sends the digest, keeps your address until you unsubscribe." in form
    assert 'href="/digest/#signup"' in (site_dir / "digest" / "2026-09-28" / "index.html").read_text()
    for outcome, title in (("thanks", "Check your email"), ("problem", "That didn't go through")):
        page = (site_dir / "digest" / outcome / "index.html").read_text()
        assert f"<h1>{title}</h1>" in page
    # Pages a form leads to aren't for search engines.
    assert "/digest/thanks/" not in (site_dir / "sitemap.xml").read_text()


def test_the_about_page_says_what_the_signup_keeps(site_dir):
    about = (site_dir / "about" / "index.html").read_text()
    assert "The one exception is the weekly digest by email, if you sign up" in about
    assert "the IP address you signed up from go to Buttondown" in about
    assert 'href="https://buttondown.com/legal/privacy"' in about
    assert "The emails don't record whether you open them or which links you click" in about


def test_a_signup_needs_who_keeps_the_addresses(config):
    assert digest.signup({**config, "digest": {"signup": False}}) is None
    with pytest.raises(SystemExit, match="needs provider and privacy_url"):
        digest.signup({**config, "digest": {"signup": True}})
