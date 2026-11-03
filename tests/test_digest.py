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
        meeting("2026-08-19", "2026-10-02T06:00:00-04:00"),  # 46 days before the issue: past the limit
        meeting("2026-08-20", "2026-09-01T06:00:00-04:00"),  # minutes collected the first day: history
        meeting("2026-08-20", "2026-10-02T07:00:00-04:00", body="Planning Board"),  # 45 days: listed
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
    assert [m["date"] for m in issue["minutes"]] == ["2026-08-20", "2026-09-15", "2026-09-22"]
    assert issue["send_at"] == datetime(2026, 10, 4, 17, 30, tzinfo=TZ)
    assert [day for day, _ in issue["days"]] == ["2026-10-05", "2026-10-11"]
    # Each earlier week with a meeting has its issue; those with nothing (September 6) have none.
    assert [i["sunday"] for i in found] == ["2026-10-04", "2026-09-27", "2026-09-20", "2026-09-13"]
    assert [m["date"] for m in found[1]["meetings"]] == ["2026-09-29", "2026-10-04"] and not found[1]["minutes"]


def test_an_issue_is_built_on_its_saturday_and_due_sunday_morning():
    """From MORNING_FROM, an issue is built the day before its Sunday, lists the minutes collected Sunday to
    Saturday, and is due at 7 AM. The issue before it keeps its week, so no minutes are listed twice."""
    meetings = [
        meeting("2026-10-20", "2026-11-01T06:00:00-05:00"),  # collected Sunday, November 1: that issue's
        meeting("2026-10-21", "2026-11-02T06:00:00-05:00"),  # the Monday after: the first morning issue's
        meeting("2026-10-27", "2026-11-07T06:00:00-05:00"),  # its Saturday
        meeting("2026-10-28", "2026-11-08T06:00:00-05:00"),  # its Sunday: the next issue's
        meeting("2026-11-02"), meeting("2026-11-09"), meeting("2026-11-15"), meeting("2026-11-16"),
    ]
    since = "2026-09-01T06:00:00-04:00"
    # Friday: November 8's issue isn't built yet.
    assert [i["sunday"] for i in digest.issues(meetings, since, date(2026, 11, 6), TZ)][:1] == ["2026-11-01"]
    found = digest.issues(meetings, since, date(2026, 11, 7), TZ)
    morning, evening = found[0], found[1]
    assert (morning["sunday"], morning["url"]) == ("2026-11-08", "/digest/2026-11-09/")
    assert (morning["minutes_from"], morning["minutes_to"]) == ("2026-11-02", "2026-11-07")
    assert [m["date"] for m in morning["minutes"]] == ["2026-10-21", "2026-10-27"]
    assert [m["date"] for m in morning["meetings"]] == ["2026-11-09", "2026-11-15"]
    assert morning["send_at"] == datetime(2026, 11, 8, 7, 0, tzinfo=TZ)
    # The last evening issue, as it was sent.
    assert (evening["sunday"], evening["minutes_from"], evening["minutes_to"]) == ("2026-11-01", "2026-10-26", "2026-11-01")
    assert [m["date"] for m in evening["minutes"]] == ["2026-10-20"]
    assert evening["send_at"] == datetime(2026, 11, 1, 17, 30, tzinfo=TZ)
    # The week after: Sunday to Saturday.
    later = digest.issues(meetings, since, date(2026, 11, 14), TZ)[0]
    assert (later["sunday"], later["minutes_from"], later["minutes_to"]) == ("2026-11-15", "2026-11-08", "2026-11-14")
    assert [m["date"] for m in later["minutes"]] == ["2026-10-28"]
    assert later["send_at"] == datetime(2026, 11, 15, 7, 0, tzinfo=TZ)


def test_when_minutes_were_posted():
    """Minutes collected on the site's first day are the town's history, not minutes just posted."""
    meetings = [
        meeting("2026-08-20", "2026-09-01T06:00:00-04:00"),  # the first day
        meeting("2026-09-15", "2026-10-01T01:30:00-04:00"),  # the town's own day, not UTC's
        meeting("2026-09-16", "2026-10-02T06:00:00-04:00", minutes_summary={"is_minutes": False}),
        meeting("2026-10-05"),
    ]
    posted = digest.minutes_posted(meetings, "2026-09-01T06:00:00-04:00", TZ)
    assert [posted[id(m)] for m in meetings] == [None, date(2026, 10, 1), None, None]
    assert set(digest.minutes_posted(meetings, None, TZ).values()) == {None}


def test_no_issues_before_any_meeting_is_seen():
    assert digest.issues([], None, date(2026, 10, 7), TZ) == []


def test_digest_pages(site_dir):
    """The fixture town is built on Friday, October 2, so its one issue is Sunday, September 27's."""
    index = (site_dir / "digest" / "index.html").read_text()
    assert 'href="/digest/2026-09-28/"' in index and 'href="/digest/feed.xml"' in index
    page = (site_dir / "digest" / "2026-09-28" / "index.html").read_text()
    assert "<title>Gloucester, MA meetings, week of Sep 28, 2026</title>" in page
    assert 'href="/meetings/2026-09-28-human-rights-commission/"' in page
    # The fixture's calendar was last read September 27, so the week's list may be missing meetings.
    assert "hasn't been able to check the city's meeting listings since September 27, 2026" in page
    # The minutes collected so far are the town's history, collected the first day.
    assert 'id="decided"' not in page
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
    # The site's own links, and the city's official site, as every page's footer has.
    site_links = [link for link in links if link.startswith("https://gloucester-ma.publick.org/")]
    # Buttondown's tag for each reader's unsubscribe link (worker/digest.js sends it without Buttondown's
    # template, which would add its own), as the signup form promises.
    assert site_links and set(links) - set(site_links) == {"https://gloucester-ma.gov", digest.UNSUBSCRIBE_TAG}
    # The postal address the law on commercial email asks for.
    assert f"Publick · {digest.MAILING_ADDRESS}</p>" in body
    # Each of the site's says it's from the email, for the page counts (count.js).
    assert all(link.endswith("?ref=digest-email") for link in site_links)
    # Styled inline, in the town's own colors, as email apps drop a stylesheet; headed with the site's name.
    assert "<style" not in body and 'style="' in body and "#011536" in body
    assert "Gloucester <span" in body and "Weekly digest · Week of September 28, 2026" in body
    assert "<script" not in body and "<nav" not in body
    # It says what's inside first, and once, above the meetings, that their lines are written by AI.
    assert re.search(r'<p style="[^"]*">10 meetings this week\.</p>', body)
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
    decided = page[page.index('id="decided"'):]
    assert '<h3><a href="/meetings/2026-08-25-city-council/">City Council</a></h3>' in decided
    # Its headline, then every main decision as a row with its outcome, said once.
    assert '<p class="summary">Approved a site plan for 12 Main St.</p>' in decided
    assert decided.count('<span class="outcome">Approved</span>') == 7
    assert "The site plan for 19 Main St" in decided
    # That it's written by AI is said once, above; a meeting checked in full says nothing more.
    assert decided.count("Written by AI from each meeting's minutes") == 1 and 'class="small checked"' not in decided
    feed = minidom.parse(str(with_new_minutes / "digest" / "feed.xml"))
    titles = [t.firstChild.data for t in feed.getElementsByTagName("title")]
    # The week's lead as a headline, for the email's subject; a week without decisions keeps its date.
    assert titles.index("City Council approved a site plan for 12 Main St") < titles.index("Week of September 28, 2026")
    body = feed.getElementsByTagName("item")[0].getElementsByTagName("description")[0].firstChild.data
    # It leads with the week's strongest meeting, whose headline isn't said again below.
    lead = re.search(r'<p style="[^"]*font-size:18px[^"]*"><a href="https://gloucester-ma\.publick\.org/meetings/2026-08-25-city-council/'
                     r'\?ref=digest-email" style="[^"]*">City Council</a><span style="[^"]*"> · Aug 25</span><br>'
                     r'Approved a site plan for 12 Main St\.</p>', body)
    assert lead and body.count("Approved a site plan for 12 Main St.") == 1
    assert body.index("<h2") > lead.end()
    # Where each meeting of the week is.
    assert "<span style=\"color:#545c65;\"> · City Council Committee Room</span>" in body
    assert body.count("Written by AI from each meeting's minutes") == 1
    # A main board's decisions by row, up to EMAIL_DECISIONS, and the rest a link away.
    assert body.count(">The site plan for ") == digest.EMAIL_DECISIONS
    assert re.search(r'<a href="https://gloucester-ma\.publick\.org/meetings/2026-08-25-city-council/\?ref=digest-email"'
                     r' style="[^"]*">2 more decisions on the meeting\'s page</a>', body)
    assert "more meeting decided" not in body


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
        assert '<meta name="robots" content="noindex">' in page
    assert "/digest/thanks/" not in (site_dir / "sitemap.xml").read_text()
    assert "noindex" not in (site_dir / "digest" / "index.html").read_text()


def lines(page: str) -> list[str]:
    """Each signup (site/templates/digest_line.html) on a built page."""
    return [m.group(0) for m in re.finditer(r'<form class="line-form" id="[^"]*-signup[^"]*".*?</form>', page, re.S)]


def check_line(line: str, form_id: str, lang: str = "en") -> None:
    """One signup: a labeled email field described by the note under it, the language it was signed up in, a
    field for bots that people never reach, and a plain form, no script."""
    assert f'<form class="line-form" id="{form_id}" action="/digest/subscribe" method="post">' in line
    assert f'<label for="{form_id}-email">' in line
    assert re.search(rf'<input id="{form_id}-email" name="email" type="email" required maxlength="254" '
                     rf'autocomplete="email" placeholder="[^"]+" aria-describedby="{form_id}-note">', line)
    assert f'<p class="small" id="{form_id}-note">' in line
    assert f'<input type="hidden" name="lang" value="{lang}">' in line
    assert re.search(rf'<div hidden>\s*<label for="{form_id}-website">', line)
    assert '<button class="button button-primary" type="submit">' in line
    assert "<script" not in line and "onsubmit" not in line
    # Its past issues are in English, wherever the form is.
    assert '<a href="/digest/" hreflang="en">' in line


def test_the_signup_is_where_readers_are(site_dir):
    """Most readers never reach /digest/: the form is on the home page as one line, under the heading and again
    in "Stay up to date", at the end of each meeting's page, and in the footer of the other pages."""
    home = (site_dir / "index.html").read_text()
    top, end = lines(home)
    check_line(top, "home-signup")
    check_line(end, "home-signup-end")
    # The first under the heading, before the week's meetings and the street lookup; the second with the ways to
    # keep up, which don't link the digest the form signs up for.
    assert home.index("<h1>") < home.index('id="home-signup"') < home.index('id="coming-up"') < home.index('id="home-street"')
    keep_up = home[home.index('id="keep-up"'):home.index("<footer")]
    assert 'id="home-signup-end"' in keep_up and 'href="/feed.xml"' in keep_up and 'href="/digest/">' not in keep_up
    for meeting_page in sorted((site_dir / "meetings").glob("20*/index.html")):
        page = meeting_page.read_text()
        if 'http-equiv="refresh"' in page:
            continue
        [line] = lines(page)
        check_line(line, "meeting-signup")
        # After the meeting's own content, so it never moves it down; not in the footer too.
        assert page.index('id="agenda"') < page.index('id="meeting-signup"') < page.index('id="source"')
        assert page.index('id="meeting-signup"') < page.index("<footer")
    for url in ("meetings", "meetings/boards/city-council", "about", "311", "digest/2026-09-28"):
        page = (site_dir / url / "index.html").read_text()
        [line] = lines(page)
        check_line(line, "footer-signup")
        assert page.index("<footer") < page.index('id="footer-signup"')
    assert 'the <a href="/digest/#signup">weekly digest by email</a> lists every board' in \
        (site_dir / "meetings" / "boards" / "city-council" / "index.html").read_text()
    # The digest's own page has its form, and the pages the form leads to have none.
    assert lines((site_dir / "digest" / "index.html").read_text()) == []
    for outcome in ("thanks", "problem"):
        assert "/digest/subscribe" not in (site_dir / "digest" / outcome / "index.html").read_text()
    # Every id on a page is its own, with the form on it.
    for path in site_dir.rglob("*.html"):
        ids = re.findall(r'\bid="([^"]+)"', path.read_text())
        assert len(ids) == len(set(ids)), path


def test_a_town_without_a_digest_shows_no_signup(data_dir, monkeypatch):
    """A town whose digest has no signup ([digest] signup off, as for a town the digest isn't sent for): no
    form on any page, in any language, and no pages for it to lead to."""
    real_load = build_site.load_config

    def without_signup(town):
        config = real_load(town)
        config["digest"] = {**config.get("digest", {}), "signup": False}
        config["site"]["languages"] = ["en", "es"]
        return config
    monkeypatch.setattr(build_site, "load_config", without_signup)
    out = Path(tempfile.mkdtemp(prefix="publick-no-digest-")) / "site"
    build_site.build("gloucester", out, data_dir=data_dir, now=datetime(2026, 10, 2, 7, 0, tzinfo=TZ))
    pages = list(out.rglob("*.html"))
    assert any(p.parts[-3:] == ("es", "meetings", "index.html") for p in pages)
    for path in pages:
        page = path.read_text()
        assert "-signup-email" not in page and "/digest/subscribe" not in page, path
    # The home page links the digest's issues in its place.
    assert 'href="/digest/">Weekly digest</a>' in (out / "index.html").read_text()
    assert not (out / "digest" / "thanks").exists() and not (out / "es" / "digest").exists()


def test_recently_decided_says_what_was_decided_not_when(site_dir):
    """On the home page, a decided meeting's line says its minutes are posted and marks a committee's
    recommendation, without the weekday and time beside its date."""
    home = (site_dir / "index.html").read_text()
    decided = home[home.index('id="decided"'):home.index('id="numbers"')]
    assert "Minutes posted" in decided and "Minutes posted by" not in decided
    assert not re.search(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)[,·<]", decided)


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


def test_the_email_lists_some_meetings_minutes_and_links_the_rest(with_new_minutes, data_dir, monkeypatch):
    """A week with more meetings' decisions than digest.EMAIL_MEETINGS: the email lists that many, and
    links the issue's page, which lists them all."""
    monkeypatch.setattr(digest, "EMAIL_MEETINGS", 0)
    out = Path(tempfile.mkdtemp(prefix="publick-digest-")) / "site"
    build_site.build("gloucester", out, data_dir=with_new_minutes.parent / "data", now=datetime(2026, 10, 4, 7, 0, tzinfo=TZ))
    body = minidom.parse(str(out / "digest" / "feed.xml")).getElementsByTagName("item")[0] \
        .getElementsByTagName("description")[0].firstChild.data
    assert "The site plan for 14 Main St" not in body
    assert re.search(r'<a href="https://gloucester-ma\.publick\.org/digest/2026-10-05/\?ref=digest-email#decided" style="[^"]*">'
                     r'What 1 more meeting decided, on the site</a>', body)
    assert "The site plan for 14 Main St" in (out / "digest" / "2026-10-05" / "index.html").read_text()


@pytest.mark.parametrize("lang, label, placeholder, button, note", [
    ("en", "Get the week ahead by email", "Email address", "Sign up", None),
    ("es", "Reciba la semana que viene por correo electrónico", "Correo electrónico", "Suscribirse",
     "Por ahora, el resumen está en inglés."),
])
def test_the_signup_in_each_language(lang, label, placeholder, button, note):
    """The signup's own words in the site's Spanish (site/strings/es.po), and the language it's signed up in."""
    from jinja2 import Environment, FileSystemLoader

    from pipeline import i18n
    env = Environment(loader=FileSystemLoader(str(build_site.SITE_DIR / "templates")), autoescape=True,
                      trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.i18n"])
    env.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True, pgettext=i18n.pgettext)
    env.policies["ext.i18n.trimmed"] = True
    context = {"digest_signup": {"provider": "Buttondown"}, "config": {"meetings": {}}, "meeting_links": {"documents": True},
               "town": {"name": "Gloucester"}, "lang": lang, "form_id": "home-signup"}
    with i18n.use(lang):
        line = env.get_template("digest_line.html").render(**context)
    assert f'<label for="home-signup-email">{label}</label>' in line
    assert f'placeholder="{placeholder}"' in line
    assert f'type="submit">{button}</button>' in line
    assert f'<input type="hidden" name="lang" value="{lang}">' in line
    assert (note in line) if note else "For now" not in line
    # Nothing for a town without the signup.
    with i18n.use(lang):
        assert env.get_template("digest_line.html").render(**{**context, "digest_signup": None}).strip() == ""


def decision(text: str, outcome: str = "approved", main: bool | None = True, title: str | None = None):
    return build_site.Decision(text, outcome=outcome, title=title, main=main)


def decided_meeting(body: str, day: str, decided: list, recommended: list | None = None, **extra) -> dict:
    return {"body": body, "date": day, "title": f"{body} Meeting", "url": f"/meetings/{day}-x/",
            "decisions": {"decided": decided, "recommended": recommended or [], "procedural": []}, **extra}


def test_what_was_decided_puts_the_strongest_meeting_first():
    """A main board's meeting first, then the one that settled the most; a meeting that decided nothing apart; the lead
    is the first, as a headline."""
    council = decided_meeting("City Council", "2026-10-13", [
        decision("Approved the minutes", main=False), decision("Tabled the parcel sale", "tabled"),
        decision("Approved the school repairs, 7-0", title="School repairs"), decision("Denied the permit rule", "denied"),
    ], preview_line="Approved repairs at two schools, and denied a permit rule for student housing.")
    planning = decided_meeting("Planning Board", "2026-10-14", [decision(f"Approved site plan {n}") for n in range(4)])
    licensing = decided_meeting("Licensing Board", "2026-10-15", [decision("Approved a license")])
    quiet = decided_meeting("Conservation Commission", "2026-10-07", [], preview_line="Heard a wetlands update.")
    recorded = decided_meeting("Board of Health", "2026-10-16", [decision("Approved a fee", main=None)])
    issue = {"minutes": [licensing, quiet, planning, council], "recorded": [recorded]}
    found = digest.decided(issue, ["City Council", "School Committee"])
    assert [e["meeting"]["body"] for e in found["entries"]] == ["City Council", "Planning Board", "Board of Health", "Licensing Board"]
    assert [e["meeting"]["body"] for e in found["quiet"]] == ["Conservation Commission"]
    first = found["entries"][0]
    # Its main decisions, those that settled a matter first; the routine one counted, not listed.
    assert [str(d) for d in first["main"]] == ["Approved the school repairs, 7-0", "Denied the permit rule", "Tabled the parcel sale"]
    assert (first["routine"], first["count"], first["source"]) == (1, 4, "minutes")
    assert found["entries"][2]["source"] == "recording"
    assert found["lead"] is first
    assert found["lead_title"] == "City Council approved repairs at two schools"
    assert digest.decided({"minutes": [quiet], "recorded": []}, [])["lead"] is None


def test_main_decisions_without_labels_are_all_of_them():
    """Summaries from before decisions were labeled main or routine, and those that call every one routine, list all."""
    m = decided_meeting("City Council", "2026-10-13", [decision("A", main=None), decision("B", "referred", main=None)])
    assert ([str(d) for d in digest.main_decisions(m)[0]], digest.main_decisions(m)[1]) == (["A", "B"], 0)
    m = decided_meeting("City Council", "2026-10-13", [decision("A", main=False), decision("B", main=False)])
    assert digest.main_decisions(m) == (m["decisions"]["decided"], 0)


@pytest.mark.parametrize("line, name, expected", [
    ("Approved $200,000 soil cleanup at 448 High Street, and asked for a draft ban on black plastic takeout containers.",
     "City Council", "City Council approved $200,000 soil cleanup at 448 High Street"),
    ("Overrode the mayor's veto of the Boston Avenue zoning amendment after a motion to require permits failed.",
     "City Council", "City Council overrode the mayor's veto of the Boston Avenue zoning…"),
    ("PILOT framework sent to committee.", "City Council", "PILOT framework sent to committee"),
    ("Set October 27 hearings on 37 Enon Street apartments.", "Planning Board", "Planning Board set October 27 hearings on 37 Enon Street apartments"),
    ("Approved a license.", "City Council Resident Services and Public Engagement Committee", "Approved a license"),
    ("Tabled it, and more.", "City Council", "City Council tabled it, and more"),
    ("", "City Council", None),
])
def test_the_lead_as_a_headline(line, name, expected):
    """The lead's headline cut to its first clause, after its board's name where that's short and the headline starts
    with what the board did."""
    entry = {"meeting": {"preview_line": line}, "main_board": True, "name": name}
    assert digest.headline(entry) == expected
    assert expected is None or len(expected) <= digest.SUBJECT_NAME + 1 + digest.SUBJECT_LENGTH


@pytest.mark.parametrize("title, day_part, expected", [
    ("City Council Regular Meeting", None, "City Council"),
    ("City Council Meeting", None, "City Council"),
    ("City Council Committee of the Whole", None, "City Council Committee of the Whole"),
    ("Special Meeting", None, "City Council"),
    ("City Council Meeting", "evening", "City Council (evening)"),
])
def test_meeting_names(title, day_part, expected):
    assert digest.meeting_name({"body": "City Council", "title": title, "day_part": day_part}) == expected


def test_every_issue_email_is_sendable(with_new_minutes):
    """Each issue's email has its unsubscribe link and postal address, no section left empty, and stays under the size
    Gmail cuts an email at (102 KB), past which the footer, with the unsubscribe link, would be hidden."""
    feed = minidom.parse(str(with_new_minutes / "digest" / "feed.xml"))
    for item in feed.getElementsByTagName("item"):
        body = item.getElementsByTagName("description")[0].firstChild.data
        assert len(body.encode()) < 100_000
        assert digest.UNSUBSCRIBE_TAG in body and digest.MAILING_ADDRESS in body
        for heading in re.finditer(r"<h2[^>]*>([^<]*)</h2>", body):
            after = body[heading.end():].split("<h2", 1)[0]
            assert re.search(r"<a |<td", after), f"an empty section: {heading.group(1)}"


@pytest.mark.parametrize("place, expected", [
    ("Temporary Beverly City Hall, 1st Floor Conference Room, 175 Cabot Street, Beverly, MA", "Temporary Beverly City Hall, 1st Floor Conference Room"),
    ("Beverly Senior Center (90 Colon Street, Beverly)", "Beverly Senior Center (90 Colon Street)"),
    ("Community Room, Beverly Police Department, 175 Elliott Street, Beverly", "Community Room, Beverly Police Department"),
    ("City Hall, Beverly, MA 01915", "City Hall"),
    ("", None),
])
def test_where_a_meeting_is_in_the_email(place, expected):
    """A meeting's place, short: without the town, its state and ZIP code, and at most its first two parts."""
    assert digest.short_place({"location": place}, "Beverly") == expected
