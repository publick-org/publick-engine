"""What the site says about where its meetings come from, and about the place: a city
or a town (pipeline/i18n.py, pipeline/fetch_place.py), and a listing's time or agenda
link that isn't what it seems (build_site.check_listing)."""

import json
import re

import pytest
from babel.messages.pofile import read_po
from fakes import FakeJSONResponse

from pipeline import build_site, fetch_place, i18n


# ---- City or town ------------------------------------------------------------------------

@pytest.mark.parametrize("lang, city, town", [
    ("en", "How the city is doing", "How the town is doing"),
    ("en", "Not affiliated with or endorsed by the City of %(town)s.", "Not affiliated with or endorsed by the Town of %(town)s."),
    ("en", "the city's <a href=\"%(url)s\">Agenda Center</a>", "the town's <a href=\"%(url)s\">Agenda Center</a>"),
    ("en", "Elected citywide for four years.", "Elected townwide for four years."),
    ("en", "The median across Massachusetts cities and towns", "The median across Massachusetts cities and towns"),
    ("es", "Cómo le va a la ciudad", "Cómo le va al pueblo"),
    ("es", "el sitio web de la ciudad", "el sitio web del pueblo"),
    ("es", "La ciudad publicó esta reunión", "El pueblo publicó esta reunión"),
    ("es", "Elegido por toda la ciudad.", "Elegido por todo el pueblo."),
    ("es", "No está afiliado a la Ciudad de %(town)s.", "No está afiliado al Pueblo de %(town)s."),
    ("es", "lo que una ciudad recauda", "lo que un pueblo recauda"),
    ("es", "pueblos y ciudades", "pueblos y ciudades"),
])
def test_worded_for_a_town(lang, city, town):
    assert i18n.for_town(city, lang) == town


def test_only_the_wording_changes():
    """Names put in a string, and its links, are never reworded."""
    text = '<a href="https://www.city.example/city/">%(city)s</a> {city} city'
    assert i18n.for_town(text, "en") == '<a href="https://www.city.example/city/">%(city)s</a> {city} town'


def test_every_string_reads_for_a_town():
    """No string says "city" for a town, in any language, and the Spanish for a town ("el
    pueblo") has no word left that agreed with "la ciudad"."""
    with (i18n.STRINGS_DIR / "es.po").open("rb") as f:
        catalog = read_po(f)
    for m in catalog:
        for lang, text in (("en", m.id), ("es", m.string)):
            for form in (text if isinstance(text, tuple) else (text,)):
                worded = re.sub(r"<[^>]*>|%\([^)]*\)s", "", i18n.for_town(form or "", lang))
                assert not re.search(r"\b(?:[Cc]ity|[Cc]itywide|[Cc]iudad)\b", worded), (lang, form)
                if lang == "es" and re.search(r"ciudad", form or "", re.I):
                    assert not re.search(r"\bella\b", worded), form


def test_the_build_uses_the_language_and_the_kind():
    with i18n.use("es", "town"):
        assert i18n.gettext("How the city is doing") == "Cómo le va al pueblo"
        assert i18n.label("City") == "Ciudad"             # a label from data is never reworded
    with i18n.use("en", "town"):
        assert i18n.gettext("How the city is doing") == "How the town is doing"
    assert i18n.gettext("How the city is doing") == "How the city is doing"
    with pytest.raises(ValueError):
        with i18n.use("en", "village"):
            pass


def test_kind_from_the_config_or_the_census(tmp_path, config):
    assert build_site.town_kind(config, tmp_path) == "city"
    (tmp_path / "place.json").write_text(json.dumps({"name": "Wallingford town", "kind": "town"}))
    assert build_site.town_kind(config, tmp_path) == "town"
    assert build_site.town_kind({**config, "town": {**config["town"], "kind": "city"}}, tmp_path) == "city"


# ---- The Census Bureau's word for the place ----------------------------------------------------

def test_place_query():
    url = fetch_place.query_url("06000US0917078740")
    assert url.startswith(fetch_place.TIGERWEB + "/1/query?") and "GEOID%3D%270917078740%27" in url
    assert "/4/query?" in fetch_place.query_url("16000US2505595")
    with pytest.raises(ValueError):
        fetch_place.query_url("05000US25009")


@pytest.mark.parametrize("name, lsad, kind", [("Wallingford town", "43", "town"), ("Beverly city", "25", "city")])
def test_place_kind(name, lsad, kind):
    found = fetch_place.parse({"features": [{"attributes": {"NAME": name, "LSADC": lsad, "GEOID": "1"}}]})
    assert found == {"name": name, "lsad": lsad, "kind": kind}


def test_place_is_fetched_once(tmp_path, config):
    class Client:
        urls = []

        def get(self, url):
            self.urls.append(url)
            return FakeJSONResponse({"features": [{"attributes": {"NAME": "Gloucester city", "LSADC": "25", "GEOID": "2526150"}}]})

    client = Client()
    assert fetch_place.run(config, client, tmp_path)["kind"] == "city"
    assert fetch_place.run(config, client, tmp_path) == {"skipped": "already recorded"}
    assert len(client.urls) == 1
    assert json.loads((tmp_path / "place.json").read_text())["name"] == "Gloucester city"


# ---- A listing that isn't what it seems ------------------------------------------------------------

def meeting(**fields):
    return {"id": "dnn-1", "date": "2026-10-08", "start_time": "08:30", "end_time": None, **fields}


def test_a_time_in_the_night_is_set_aside():
    """Manchester's calendar listed the Highway Commission at 3:30 AM."""
    m = meeting(start_time="03:30", end_time="04:30")
    build_site.check_listing(m)
    assert (m["start_time"], m["end_time"], m["listed_time"]) == (None, None, "03:30")
    m = meeting()
    build_site.check_listing(m)
    assert (m["start_time"], m["listed_time"]) == ("08:30", None)


@pytest.mark.parametrize("url, own", [
    ("https://www.manchesternh.gov/Portals/2/Departments/econ_dev/MDC 2026 Website and City Hall Posting Agenda.pdf?ver=1", False),
    ("https://www.manchesternh.gov/departments/public-works/dpw-commissions", False),
    ("https://www.manchesternh.gov/Portals/2/Departments/pcd/PCDshare/2026-10-08 ZBA Agenda.pdf?ver=2026-09-30", True),
    ("https://www.manchesternh.gov/Portals/2/Departments/econ_dev/MDC/MDC October 8th 2026 Meeting Agenda.pdf", True),
    ("https://www.manchesternh.gov/Portals/2/Departments/pcd/BoardsCommissions/HeritageCommission/Agendas/2026-10-08", True),
])
def test_an_agenda_link_is_the_meeting_s_own_when_it_names_its_date(url, own):
    m = meeting(documents_url=url)
    build_site.check_listing(m)
    assert (m["documents_url"] == url) is own and (m["board_documents_url"] == url) is not own
