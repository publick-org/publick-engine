"""Whole sites for sample towns whose meetings come from the other systems the
engine reads: Manchester's (a CivicClerk portal and a DotNetNuke city
calendar, with New Hampshire's figures), Malden's (a CivicPlus Agenda
Center) and Wallingford's (a calendar and a documents page, in Connecticut,
which has no state figures yet, and its Board of Education's Finalsite page). Their meetings, agendas, and minutes are collected from saved pages
by the real fetchers; the other sections are the Gloucester fixture data. Each
site gets the checks Gloucester's does (tests/test_site.py, and a sample of its
own pages in tests/test_accessibility.py), so a release that breaks one of
these towns fails here before it reaches their sites."""

import copy
import inspect
import shutil
from datetime import datetime
from types import SimpleNamespace

import pytest
import test_site
from conftest import BUILT_AT, FETCHED_AT, TZ
from fakes import FakeAnthropic, FakeFinalsite
from test_agendacenter import ALIASES, BASE, COMMITTEES, FakeAgendaCenter
from test_calendars import PORTAL
from test_calendars import manchester as with_manchester_calendars
from test_civicclerk_documents import API, FakeManchesterFiles
from test_filelist import BASE as WALLINGFORD, DOCUMENTS_URL, FakeTownSite, wallingford_meetings
from test_finalsite import MINUTES_DOC, finalsite_meetings
from test_nh import FakeCity, FakeParcels, run_extract
from test_nh import manchester as in_new_hampshire

from pipeline import (build_site, fetch_budget, fetch_finance, fetch_finalsite_meetings, fetch_meetings, fetch_minutes,
                      fetch_schools, summarize)
from pipeline.config import load_config
from pipeline.states.nh import figures

TOWNS = ["manchester", "malden", "wallingford"]
# What Malden's sample agendas say about when and where.
AGENDA_WHEN_WHERE = {"start_time": "18:30", "location": "Malden Government Center, 215 Pleasant St., Room 105"}
# Before the fixtures' first meetings, when their agendas are posted; their minutes are collected at FETCHED_AT.
EARLY = datetime(2026, 8, 1, 7, 0, tzinfo=TZ)


def named(town: dict, slug: str, folder: str) -> dict:
    """The site named for the town in [town], at <folder>.publick.org, as the network names it."""
    name, state = town["town"]["name"], town["town"]["state"]
    town["slug"] = slug
    town["site"].update(name=f"{name} Publick", name_prefix=f"{name} ", domain=f"{folder}.publick.org",
                        tagline=f"Public data on how {name}, {state} is doing. Updated daily.",
                        masthead=f"An independent guide to city government in {name}, {state}",
                        repo_url="https://github.com/publick-org/publick.org", contact_email=f"{folder}@publick.org",
                        user_agent=f"{folder}.publick.org (+https://{folder}.publick.org/about/)")
    town["analytics"]["prefix"] = folder
    town.pop("participation", None)  # Gloucester's boards
    return town


def manchester() -> dict:
    """As towns/manchester-nh has it: New Hampshire's figures, and meetings from
    CivicClerk and the city calendar, with their agendas, minutes, and summaries."""
    town = named(with_manchester_calendars(in_new_hampshire()), "manchester", "manchester-nh")
    del town["meetings"]["documents"]  # collected, the default
    town["summaries"] = load_config("gloucester")["summaries"]
    town.pop("permits")
    town["site"]["colors"] = {"primary": "#12469a", "primary_dark": "#14284b", "primary_soft": "#e7eef9", "accent": "#7c2629"}
    town["freshness"]["sources"] = [
        {"label": "Meetings (city calendar and CivicClerk)", "file": "meetings/status.json", "max_days": 2},
        {"label": "311 requests", "file": "311/status.json", "max_days": 2}]
    return town


def malden() -> dict:
    """As towns/malden-ma has it: every board's meetings from the Agenda Center, with agendas, minutes, and summaries."""
    town = copy.deepcopy(load_config("gloucester"))
    town["town"].update(name="Malden", official_site=BASE)
    for table in ("archive", "drive_meetings", "permits"):
        town.pop(table)
    town["meetings"] = {"base_url": BASE, "archive_url": f"{BASE}/AgendaCenter", "archive_name": "city's Agenda Center",
                        "aliases": ALIASES, "agenda_center": {"base_url": BASE, "since": "2026-08-01",
                                                              "exclude_categories": ["Community Outreach"],
                                                              "committees": COMMITTEES}}
    town["freshness"]["sources"] = [
        {"label": "Meetings (Agenda Center)", "file": "meetings/status.json", "max_days": 2},
        {"label": "311 requests", "file": "311/status.json", "max_days": 2}]
    return named(town, "malden", "malden-ma")


def wallingford() -> dict:
    """Every board's meetings, agendas, minutes, and summaries from the town website's
    calendar and documents page, and the Board of Education's from the school district's
    Finalsite page. Connecticut has no state figures yet, so no budget or schools sections."""
    town = copy.deepcopy(load_config("gloucester"))
    town["town"].update(name="Wallingford", state="Connecticut", state_abbr="CT", official_site=WALLINGFORD)
    for table in ("archive", "drive_meetings", "permits", "finance", "schools"):
        town.pop(table)
    town["sections"] = [s for s in town["sections"] if s["slug"] not in ("budget", "schools")]
    town["meetings"] = wallingford_meetings()
    town["finalsite_meetings"] = finalsite_meetings()
    town["freshness"]["sources"] = [
        {"label": "Meetings (town website)", "file": "meetings/status.json", "max_days": 2},
        {"label": "311 requests", "file": "311/status.json", "max_days": 2}]
    return named(town, "wallingford", "wallingford-ct")


def gloucester_data(data_dir, base, *left_out):
    """The Gloucester fixture data for the other sections: none of its meetings or summaries."""
    data = base / "data"
    skip = {"meetings", "summaries", *left_out}
    shutil.copytree(data_dir, data, ignore=lambda folder, names: [n for n in names if folder == str(data_dir) and n in skip])
    return data


def collect_meetings(town, client_class, data, summaries=None) -> None:
    """Two daily runs' meetings steps: the calendars with their upcoming agendas, the minutes they link, then summaries."""
    for now in (EARLY, FETCHED_AT):
        status = fetch_meetings.run(town, client_class(), data, now=now)
        assert not status["errors"] and not status["failed_calendars"], status
    assert not fetch_minutes.run_linked(town, client_class(), data, now=FETCHED_AT)["errors"]
    assert not summarize.run(town, summaries or FakeAnthropic(), data, limit=100, now=FETCHED_AT)["errors"]


def build(town, base, data) -> SimpleNamespace:
    """The town's site, built from its data as the daily run builds it."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_site, "load_config", lambda slug: town)
        build_site.build(town["slug"], base / "site", data_dir=data, now=BUILT_AT)
    return SimpleNamespace(dir=base / "site", data=data, config=town)


@pytest.fixture(scope="module")
def manchester_site(tmp_path_factory, data_dir):
    town, base = manchester(), tmp_path_factory.mktemp("manchester")
    data = gloucester_data(data_dir, base, "finance", "schools", "permits")
    collect_meetings(town, FakeManchesterFiles, data)
    with pytest.MonkeyPatch.context() as mp:
        # New Hampshire's figures from the fixture files, as in tests/test_nh.py.
        run_extract(mp, base / "figures")
        fetch_finance.run(town, FakeParcels(), data, now=FETCHED_AT)
        fetch_budget.run(town, FakeCity(), data, now=FETCHED_AT)
        fetch_schools.run(town, None, data, now=FETCHED_AT)
        site = build(town, base, data)
    figures.load.cache_clear()
    return site


@pytest.fixture(scope="module")
def malden_site(tmp_path_factory, data_dir):
    town, base = malden(), tmp_path_factory.mktemp("malden")
    data = gloucester_data(data_dir, base, "permits")
    # The Agenda Center lists no times or places; its towns' agendas give them.
    collect_meetings(town, FakeAgendaCenter, data, FakeAnthropic(preview={**FakeAnthropic.PREVIEW, **AGENDA_WHEN_WHERE}))
    return build(town, base, data)


@pytest.fixture(scope="module")
def wallingford_site(tmp_path_factory, data_dir):
    town, base = wallingford(), tmp_path_factory.mktemp("wallingford")
    data = gloucester_data(data_dir, base, "permits", "finance", "schools")
    assert not fetch_finalsite_meetings.run(town, FakeFinalsite(), data, now=FETCHED_AT)["errors"]
    collect_meetings(town, FakeTownSite, data)
    return build(town, base, data)


@pytest.fixture(params=TOWNS)
def town_site(request):
    return request.getfixturevalue(f"{request.param}_site")


def page(site, path: str) -> str:
    return (site.dir / path.strip("/") / "index.html").read_text()


def meeting_page(site, meeting_id: str) -> str:
    """A meeting's page, found by its id (its address is fixed when it is first recorded)."""
    return page(site, f"/meetings/{fetch_meetings.load_store(site.data)[meeting_id]['slug']}/")


# ---- Every page -----------------------------------------------------------------

# The checks Gloucester's built site gets, each called with this site's pages.
SITE_CHECKS = [test_site.test_expected_pages_exist, test_site.test_support_files, test_site.test_footer_names_the_network,
               test_site.test_every_page_has_accessible_structure, test_site.test_internal_links_resolve,
               test_site.test_pages_set_a_content_security_policy,
               test_site.test_outbound_and_pdf_links_open_in_new_tab_with_warning]


@pytest.mark.parametrize("check", SITE_CHECKS, ids=lambda c: c.__name__.removeprefix("test_"))
def test_site_passes_gloucesters_checks(town_site, check):
    given = {"site_dir": town_site.dir, "config": town_site.config, "page_files": sorted(town_site.dir.rglob("*.html"))}
    check(**{name: given[name] for name in inspect.signature(check).parameters})


def test_every_section_is_built(town_site):
    sections = {s["slug"] for s in town_site.config["sections"]}
    for folder in ("meetings/past", "meetings/boards", "meetings/decisions", "meetings/search", "311", "budget", "schools",
                   "housing", "streets", "about"):
        if folder.split("/")[0] in sections | {"streets"}:
            assert (town_site.dir / folder / "index.html").exists(), folder
    assert (town_site.dir / "feed.xml").exists() and (town_site.dir / "meetings" / "search-index.json").exists()
    assert "Gloucester" not in page(town_site, "/meetings/")


# ---- Manchester: CivicClerk and the city calendar ---------------------------------------

def test_manchester_meetings_come_from_both_calendars(manchester_site):
    upcoming = page(manchester_site, "/meetings/")
    assert f'href="{PORTAL}"' in upcoming
    assert "/meetings/2026-10-06-board-of-mayor-and-aldermen/" in upcoming  # CivicClerk
    assert "/meetings/2026-10-08-zoning-board-of-adjustment/" in upcoming  # the city calendar
    past = page(manchester_site, "/meetings/past/")
    assert "/meetings/2026-09-01-committee-on-lands-and-buildings/" in past and "/meetings/2026-09-03-planning-board/" in past
    assert "Committee on Lands and Buildings" in page(manchester_site, "/meetings/boards/")


def test_manchester_civicclerk_meeting_has_its_agenda_and_minutes(manchester_site):
    board = meeting_page(manchester_site, "civicclerk-1968")
    assert "Board of Mayor and Aldermen" in board and f"{PORTAL}/event/1968/files" in board
    assert f"{API}/Meetings/GetMeetingFileStream(fileId=5184,plainText=false)" in board
    assert 'href="/meetings/agendas/civicclerk-5184.pdf"' in board
    assert f"{API}/Meetings/GetMeetingFileStream(fileId=5246,plainText=false)" in board
    assert 'href="/meetings/minutes/civicclerk-5246.pdf"' in board
    assert "What was decided" in board and "Approved the site plan for 12 Main St, 5-0" in board
    assert "ADA compliance with Joe Lucido" in board  # the agenda's summary
    assert "(CivicClerk)" in board


def test_manchester_city_calendar_meeting_has_its_agenda(manchester_site):
    planning = meeting_page(manchester_site, "dnn-106728")
    assert "Planning Board" in planning and "Public Works Building" in planning
    assert "2026-10-01_PB_AGENDA.PDF" in planning and 'href="/meetings/agendas/dnn-' in planning
    assert "ADA compliance with Joe Lucido" in planning


def test_manchester_decisions_and_cancellations(manchester_site):
    decisions = page(manchester_site, "/meetings/decisions/")
    assert "Committee on Finance" in decisions and "12 Main St" in decisions
    assert "Cancelled" in meeting_page(manchester_site, "civicclerk-2065")
    assert "Cancelled" in meeting_page(manchester_site, "dnn-106925")


def test_manchester_has_new_hampshires_pages(manchester_site):
    assert "Calculated by Publick." in page(manchester_site, "/budget/")
    assert "Statewide Assessment System" in page(manchester_site, "/schools/")
    assert "--primary: #12469a;" in (manchester_site.dir / "static" / "css" / "site.css").read_text()


# ---- Malden: the Agenda Center -------------------------------------------------------------

def test_malden_meetings_come_from_the_agenda_center(malden_site):
    past = page(malden_site, "/meetings/past/")
    assert "/meetings/2026-09-29-city-council-finance-committee/" in past
    assert "City Council Finance Committee" in page(malden_site, "/meetings/boards/")
    assert f'href="{BASE}/AgendaCenter"' in page(malden_site, "/about/")
    finance = meeting_page(malden_site, "agendacenter-4453")
    assert "City Council Finance Committee" in finance
    assert f"{BASE}/AgendaCenter/ViewFile/Agenda/_09292026-4453" in finance
    assert 'href="/meetings/agendas/4453-202609241217.pdf"' in finance
    assert "ADA compliance with Joe Lucido" in finance


def test_malden_meeting_has_its_minutes(malden_site):
    appeal = meeting_page(malden_site, "agendacenter-4400")
    assert "Board of Appeal" in appeal
    assert f"{BASE}/AgendaCenter/ViewFile/Minutes/_09162026-4400" in appeal
    assert 'href="/meetings/minutes/agendacenter-4400.pdf"' in appeal
    assert "What was decided" in appeal and "Approved the site plan for 12 Main St, 5-0" in appeal
    assert "Board of Appeal" in page(malden_site, "/meetings/decisions/")


def test_malden_cancelled_meeting(malden_site):
    assert "Cancelled" in meeting_page(malden_site, "agendacenter-4392")


def test_malden_meeting_time_and_place_come_from_its_agenda(malden_site):
    finance = meeting_page(malden_site, "agendacenter-4453")
    assert "6:30 PM" in finance and "Room 105" in finance
    assert "From the posted agenda, as read by AI." in finance
    assert "Not listed on the" not in finance and "calendar" not in finance
    assert "This meeting's agenda in the City of Malden's Agenda Center" in finance
    assert 'content="City Council Finance Committee Meeting on ' in finance


# ---- Wallingford: the town website's calendar and documents page ------------------------------

def test_wallingford_calendar_meeting_has_its_place_and_agenda(wallingford_site):
    utilities = meeting_page(wallingford_site, "filelist-public-utilities-commission-58")
    assert "Public Utilities Commission Meeting" in utilities and "6:00 PM" in utilities
    assert "Robert F. Parisi Council Chambers" in utilities and "45 South Main Street" in utilities
    assert f"{DOCUMENTS_URL}DownloadFile.aspx?FileID=12194" in utilities
    assert 'href="/meetings/agendas/filelist-12194.pdf"' in utilities
    assert "ADA compliance with Joe Lucido" in utilities


def test_wallingford_meeting_from_its_documents_has_its_minutes(wallingford_site):
    council = meeting_page(wallingford_site, "filelist-file-12176")
    assert "Town Council Meeting" in council and "Not listed on the city calendar" in council
    assert f"{DOCUMENTS_URL}DownloadFile.aspx?FileID=12188" in council
    assert 'href="/meetings/minutes/filelist-12188.pdf"' in council
    assert "What was decided" in council and "Approved the site plan for 12 Main St, 5-0" in council
    assert "Town Council" in page(wallingford_site, "/meetings/decisions/")


def test_wallingford_cancellations_and_sources(wallingford_site):
    assert "Cancelled" in meeting_page(wallingford_site, "filelist-personnel-and-pension-appeals-board-5")
    about = page(wallingford_site, "/about/")
    assert f'href="{DOCUMENTS_URL}"' in about and "Minutes are collected from January 2026 on." in about
    assert not (wallingford_site.dir / "budget").exists() and not (wallingford_site.dir / "schools").exists()


def test_wallingford_board_of_education_from_the_district_website(wallingford_site):
    committee = meeting_page(wallingford_site, "finalsite-1698")
    assert "Board of Education Operations Committee Meeting" in committee
    assert f"https://docs.google.com/document/d/{MINUTES_DOC}/edit?usp=sharing" in committee
    assert "Minutes on Google Docs" in committee and "Agenda on Google Docs" in committee
    assert 'href="/meetings/minutes/' in committee and "What was decided" in committee
    assert "minutes posted by Wallingford Public Schools" in committee
    assert f'known from the <a href="{FakeFinalsite.PAGE_URL}"' in committee and "Wallingford Public Schools website" in committee
    assert "Not listed on the" not in committee and "town calendar" not in committee
    cancelled = meeting_page(wallingford_site, "finalsite-1707")
    assert "Cancelled" in cancelled and "Agenda not recorded here." in cancelled
    board = page(wallingford_site, "/meetings/boards/board-of-education/")
    assert "Earlier meetings are on the <a" in board and "Wallingford Public Schools website" in board
    about = page(wallingford_site, "/about/")
    assert "Board of Education meetings, agendas, and minutes:</strong> Wallingford Public Schools" in about
    assert f'href="{FakeFinalsite.PAGE_URL}"' in about and "from July 2026 on." in about
