"""Street lookup: street-name matching, the permit fetch, and the street index."""

import json

from conftest import BUILT_AT, FETCHED_AT
from fakes import FakePermits
from pipeline import build_site, fetch_permits
from pipeline.config import load_config
from pipeline.streets import addresses_in, street_keys, street_name


def test_street_names_match_across_sources():
    # The city, agendas, and SeeClickFix write the same street differently.
    assert street_keys("33 MAPLEWOOD AV, GLOUCESTER", "Gloucester") == ["MAPLEWOOD AVE"]
    assert street_keys("33 Maplewood Avenue", "Gloucester") == ["MAPLEWOOD AVE"]
    assert street_keys("33 Maplewood Ave Gloucester MA 01930, United States", "Gloucester") == ["MAPLEWOOD AVE"]
    assert street_keys("62, 62R, 64 Eastern Point Boulevard", "Gloucester") == ["EASTERN POINT BLVD"]
    assert street_keys("Railroad Ave & Washington St", "Gloucester") == ["RAILROAD AVE", "WASHINGTON ST"]
    assert street_keys("12 Main St Unit 3", "Gloucester") == ["MAIN ST"]
    assert street_keys("Ymca", "Gloucester") == [] and street_keys("", "Gloucester") == []
    assert street_name("EASTERN POINT BLVD") == "Eastern Point Boulevard"


def test_addresses_in_agenda_text():
    text = "38 Pleasant Street (Map 14, Lot 17) - door; 62-66 Eastern Point Blvd; posted 2026 15 September Road"
    assert addresses_in(text) == ["38 Pleasant Street", "62-66 Eastern Point Blvd"]
    # An order number isn't a house number; a range of them is.
    assert addresses_in("033/26 One Way - Fairmont Street from Auburn Street") == []
    assert addresses_in("h. 228/230 Clifton St/Pillacia") == ["228/230 Clifton St"]
    assert addresses_in("Open Space parcel, 0 Trask Lane; NOI 028-3146 Keystone Road") == ["0 Trask Lane"]


def test_permits_newest_export_building_and_demolition_only(tmp_path):
    config = load_config("gloucester")
    client = FakePermits()
    summary = fetch_permits.run(config, client, tmp_path, now=FETCHED_AT)
    assert summary["file"].endswith("2026-09-22 09_55_31.csv")
    assert any("id=NEWFILE" in u for u in client.urls)
    saved = json.loads((tmp_path / "permits" / "permits.json").read_text())
    permits = saved["permits"]
    # Electrical and short-form permits are left out, and so is anything older than the window.
    assert [p["id"] for p in permits] == ["BLD-26-589", "DEMO-26-12", "BLD-26-400"]
    assert permits[1]["cost"] == 12500 and permits[2]["cost"] is None
    # Names of applicants, owners, and contractors aren't stored.
    assert not any("Applicant" in json.dumps(p) or "Owner" in json.dumps(p) or "Contractor" in json.dumps(p)
                   for p in permits)
    # A second run within the week doesn't download again.
    assert "skipped" in fetch_permits.run(config, FakePermits(), tmp_path, now=FETCHED_AT)


def test_street_page_and_index(site_dir):
    page = (site_dir / "streets" / "index.html").read_text()
    assert "What's happening on your street" in page and "/static/js/streets.js?v=" in page
    assert 'data-index="/streets/streets.json?v=' in page
    index = json.loads((site_dir / "streets" / "streets.json").read_text())
    assert index["suffixes"]["AV"] == "AVE"
    pleasant = index["streets"]["PLEASANT ST"]
    assert pleasant["name"] == "Pleasant Street"
    assert pleasant["permits"][0]["address"] == "38 Pleasant Street"
    assert "Owner" not in json.dumps(index)
    home = (site_dir / "index.html").read_text()
    assert 'action="/streets/"' in home


def test_street_index_leaves_out_other_towns_addresses():
    """An applicant's own address elsewhere isn't a street in this town."""
    meeting = {"url": "/meetings/2026-10-07-planning-board/", "date": "2026-10-07", "body": "Planning Board",
               "address": "", "minutes_summary": None, "preview": {"transcript": (
                   "1. Group of 11 Azsr Ct, Halethorpe MD, for a special permit at 141 Winthrop Ave.\n"
                   "2. 9 Essex St, North Andover, MA, for 539 Common St.\n"
                   "3. 12 Broadway Ave, Unit 3, for a variance.")}}
    town = {"name": "Lawrence", "state_abbr": "MA"}
    index = build_site.street_index([meeting], [], [], BUILT_AT.date(), town)
    assert sorted(index["streets"]) == ["BROADWAY AVE", "COMMON ST", "WINTHROP AVE"]


def test_street_index_leaves_out_where_boards_meet():
    """An address alone on its line in three meetings' documents is a meeting place or letterhead
    (Beverly's City Hall, a school board's office), even split over lines; a case heard twice isn't."""
    def meeting(n, text):
        return {"url": f"/meetings/2026-10-0{n}-board/", "date": f"2026-10-0{n}", "body": "Board", "address": "",
                "minutes_summary": None, "preview": {"transcript": text}}
    meetings = [meeting(1, "191 Cabot Street\n4\n \nFAIRFIELD\n \nBOULEVARD\n12 Elm Street"),
                meeting(2, "191 Cabot Street, Beverly, MA 01915\n4 FAIRFIELD BOULEVARD\n1. A permit for 9 Dodge Street\n12 Elm Street"),
                meeting(3, "191 Cabot Street\n4 FAIRFIELD BOULEVARD\n2. Signs at 191 Cabot Street, Beverly")]
    index = build_site.street_index(meetings, [], [], BUILT_AT.date(), {"name": "Beverly", "state_abbr": "MA"})
    assert sorted(index["streets"]) == ["DODGE ST", "ELM ST"]
    assert index["streets"]["ELM ST"]["meetings_total"] == 2
