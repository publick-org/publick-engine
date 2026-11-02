"""The meetings calendar file (/meetings/calendar.ics) and each language's own RSS feed."""

import copy
import re
import xml.dom.minidom
from datetime import datetime
from zoneinfo import ZoneInfo

from pipeline import build_site, i18n, meeting_calendar


def unfold(text: str) -> list[str]:
    return text.replace("\r\n ", "").split("\r\n")


def events(text: str) -> list[dict]:
    out, current = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            out.append(current)
            current = None
        elif current is not None:
            key, _, value = line.partition(":")
            current[key] = value
    return out


CONFIG = {"site": {"name": "Gloucester Publick", "domain": "gloucester-ma.publick.org", "timezone": "America/New_York"}}
BUILT = datetime(2026, 11, 2, 9, 0, tzinfo=ZoneInfo("America/New_York"))


def meeting(slug, day, **extra):
    return {"url": f"/meetings/{slug}/", "body": "City Council", "date": day, "start_time": None, "end_time": None,
            "status": "scheduled", "location_name": None, "address": None, "location": None, **extra}


def test_a_meeting_is_an_event_at_its_time_in_utc(tmp_path):
    path = tmp_path / "calendar.ics"
    meeting_calendar.write(path, [
        meeting("2026-11-10-city-council", "2026-11-10", start_time="19:00", end_time="21:30",
                location_name="Kyrouz Auditorium", address="9 Dale Avenue, Gloucester, MA"),
        meeting("2026-11-12-planning-board", "2026-11-12", body="Planning Board; joint, with ZBA"),
    ], CONFIG, "https://gloucester-ma.publick.org", BUILT)
    raw = path.read_bytes()
    assert raw.endswith(b"END:VCALENDAR\r\n") and b"\n" not in raw.replace(b"\r\n", b"")
    assert all(len(line.encode()) <= 75 for line in raw.decode().split("\r\n"))
    text = raw.decode()
    assert "X-WR-CALNAME:Gloucester Publick: Meetings" in unfold(text)
    council, planning = events(text)
    # 7 PM in November is EST, five hours behind UTC.
    assert council["DTSTART"] == "20261111T000000Z" and council["DTEND"] == "20261111T023000Z"
    assert council["UID"] == "2026-11-10-city-council.en@gloucester-ma.publick.org"
    assert council["URL"] == "https://gloucester-ma.publick.org/meetings/2026-11-10-city-council/"
    assert council["LOCATION"] == "Kyrouz Auditorium\\, 9 Dale Avenue\\, Gloucester\\, MA"
    assert council["STATUS"] == "CONFIRMED"
    # No start time: a whole-day event; text escaped.
    assert planning["DTSTART;VALUE=DATE"] == "20261112" and planning["DTEND;VALUE=DATE"] == "20261113"
    assert planning["SUMMARY"] == "Planning Board\\; joint\\, with ZBA"
    assert "LOCATION" not in planning


def test_a_called_off_meeting_stays_marked_cancelled_and_old_ones_are_left_out(tmp_path):
    path = tmp_path / "calendar.ics"
    meeting_calendar.write(path, [
        meeting("2026-08-01-city-council", "2026-08-01"),
        meeting("2026-11-03-city-council", "2026-11-03", status="cancelled"),
        meeting("2026-11-04-city-council", "2026-11-04", status="postponed"),
    ], CONFIG, "https://gloucester-ma.publick.org", BUILT)
    cancelled, postponed = events(path.read_bytes().decode())
    assert cancelled["SUMMARY"] == "City Council (Cancelled)" and cancelled["STATUS"] == "CANCELLED"
    assert postponed["SUMMARY"] == "City Council (Postponed)" and postponed["STATUS"] == "CANCELLED"


def test_long_lines_fold_without_splitting_a_character():
    line = "SUMMARY:" + "Comisión de Conservación " * 6
    folded = meeting_calendar.fold(line)
    assert folded.replace("\r\n ", "") == line
    assert all(len(part.encode()) <= 75 for part in folded.split("\r\n"))


def test_the_built_site_has_a_calendar_and_links_it(site_dir):
    text = (site_dir / "meetings" / "calendar.ics").read_bytes().decode()
    assert text.startswith("BEGIN:VCALENDAR") and events(text)
    assert all(e["URL"].startswith("https://") and "/meetings/" in e["URL"] for e in events(text))
    assert 'href="/meetings/calendar.ics"' in (site_dir / "meetings" / "index.html").read_text()
    assert 'href="/meetings/calendar.ics"' in (site_dir / "index.html").read_text()


def test_spanish_has_its_own_feed_and_calendar(config, data_dir, tmp_path, monkeypatch):
    from conftest import BUILT_AT
    config = copy.deepcopy(config)  # the session's config, shared with other tests
    config["site"]["languages"] = ["en", "es"]
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data_dir, now=BUILT_AT)

    feed = (out / "es" / "feed.xml").read_text()
    doc = xml.dom.minidom.parseString(feed)
    assert doc.getElementsByTagName("language")[0].firstChild.data == "es"
    links = [n.firstChild.data for n in doc.getElementsByTagName("link")]
    assert links and all("/es/" in link for link in links)
    titles = [n.firstChild.data for n in doc.getElementsByTagName("title")]
    assert "Novedades de la Alcaldía" in titles[0]
    # A summary still in English isn't in the Spanish feed: its item says only that the document was posted.
    with i18n.use("es"):
        meetings = build_site.load_meetings(data_dir, BUILT_AT.date(), config["summaries"]["model"])["all"]
    english = [m for m in meetings if (m["minutes_summary"] or {}).get("english")]
    for m in english:
        for summary in filter(None, (m["minutes_summary"].get("headline"), m["minutes_summary"].get("summary"))):
            assert summary not in feed
    english_feed = (out / "feed.xml").read_text()
    assert "<language>en</language>" in english_feed and "/es/" not in english_feed

    page = (out / "es" / "index.html").read_text()
    assert 'href="/es/feed.xml"' in page and 'href="/es/meetings/calendar.ics"' in page
    cal = (out / "es" / "meetings" / "calendar.ics").read_bytes().decode()
    assert all("/es/meetings/" in e["URL"] and e["UID"].split("@")[0].endswith(".es") for e in events(cal))
    assert re.search(r"X-WR-CALNAME:.*: Reuniones", cal.replace("\r\n ", ""))
