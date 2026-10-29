"""Each video platform's recordings, read from saved listings (pipeline/recordings.py's PLATFORMS)."""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fakes import FIXTURES, FakeJSONResponse

from pipeline import cablecast, castus, civicclerk_media, gemini, internet_archive, recordings, telvue, transcribe, youtube


class FakeAPI:
    """Serves each URL that starts with a key the JSON fixture it names."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        for start, name in self.routes.items():
            if url.startswith(start):
                return FakeJSONResponse(json.loads((FIXTURES / name).read_text()))
        raise AssertionError(f"unexpected URL {url}")


# ---- CivicClerk --------------------------------------------------------------------------------

PORTLAND = {"platform": "civicclerk", "api_url": "https://portlandme.api.civicclerk.com/v1",
            "portal_url": "https://portlandme.portal.civicclerk.com",
            "media_url": "https://cpmedia.azureedge.net/portlandme",
            "boards": {"Planning Board": "^Planning Board$",
                       "City Council Sustainability and Transportation Committee": "^Sustainability and Transportation"}}


def test_civicclerk_reads_the_events_with_a_recording():
    data = json.loads((FIXTURES / "civicclerk_media_events.json").read_text())
    found = civicclerk_media.parse(data, PORTLAND)
    assert [(f["board"], f["date"], f["listing"], f["seconds"]) for f in found] == [
        ("City Council Sustainability and Transportation Committee", "2026-10-21", "civicclerk-8476", 9831),
        ("Planning Board", "2026-10-27", "civicclerk-8440", 8009)]
    # A live recording and an uploaded one are both at the media host, by their file's name.
    assert civicclerk_media.media(None, PORTLAND, found[0])["url"] == \
        "https://cpmedia.azureedge.net/portlandme/40454366-980f-4a46-9975-101708e7d939.mp4"
    assert civicclerk_media.media(None, PORTLAND, found[1])["url"] == "https://cpmedia.azureedge.net/portlandme/080ae5f365.mp4"
    assert found[1]["url"] == "https://portlandme.portal.civicclerk.com/event/8440/media"
    assert "hasMedia eq true" in civicclerk_media.events_url(PORTLAND["api_url"], "2026-10-15").replace("%20", " ")


def test_civicclerk_recordings_match_their_events_meeting():
    meetings = {"civicclerk-8440": {"id": "civicclerk-8440", "date": "2026-10-27", "body": "Planning Board"},
                "civicclerk-8441": {"id": "civicclerk-8441", "date": "2026-10-27", "body": "Planning Board"}}
    recording = {"board": "Planning Board", "date": "2026-10-27", "listing": "civicclerk-8440"}
    assert recordings.match(recording, meetings) == ("civicclerk-8440", "matched")
    # A listing put together into another meeting is matched to that one.
    assert recordings.match({**recording, "listing": "civicclerk-1"}, meetings, {"civicclerk-1": "civicclerk-8441"})[0] == \
        "civicclerk-8441"


# ---- Internet Archive ------------------------------------------------------------------------------

BURLINGTON = {"platform": "internet_archive", "collection": "cctvvt",
              "boards": {"City Council": r"^Burlington City Council\b", "Board of Finance": r"^Burlington Board of Finance\b",
                         "Development Review Board": r"^Burlington Development Review Board\b"}}


def test_internet_archive_reads_the_towns_meetings_from_a_collection():
    client = FakeAPI({internet_archive.SEARCH: "archive_search.json"})
    found = internet_archive.find(client, BURLINGTON, "2026-10-15", "America/New_York")
    query = parse_qs(urlsplit(client.urls[0]).query)
    assert query["q"] == ["collection:(cctvvt) AND publicdate:[2026-10-15 TO null]"]
    assert [(f["board"], f["date"]) for f in found] == [
        ("City Council", "2026-10-26"), ("Board of Finance", "2026-10-26"), ("Development Review Board", "2026-10-20")]
    council = found[0]
    assert council["id"] == "archive-cctv17vt-Burlington_City_Council_-_10_26_2026-gvXqokBy5k4"
    # Its agenda's event on the city's portal names its meeting.
    assert council["listing"] == "civicclerk-10093"
    assert "listing" not in found[1] or found[1]["listing"].startswith("civicclerk-")


def test_internet_archive_reads_the_mp3_once_converted():
    recording = {"item": "cctv17vt-Burlington_City_Council_-_10_5_2026-rVPnJur1kZc"}
    client = FakeAPI({"https://archive.org/metadata/": "archive_item_derived.json"})
    got = internet_archive.media(client, BURLINGTON, recording)
    assert got["url"] == ("https://archive.org/download/cctv17vt-Burlington_City_Council_-_10_5_2026-rVPnJur1kZc/"
                          "Burlington_City_Council_-_10_5_2026-rVPnJur1kZc.mp3")
    assert recording["seconds"] == 6179


def test_internet_archive_waits_for_the_conversion():
    client = FakeAPI({"https://archive.org/metadata/": "archive_item_pending.json"})
    assert internet_archive.media(client, BURLINGTON, {"item": "x"}) is None
    # With nothing pending and no MP3, the video is read.
    metadata = json.loads((FIXTURES / "archive_item_pending.json").read_text())
    metadata.pop("pending_tasks")
    assert internet_archive.audio_file(metadata)["format"] == "h.264 HD"


@pytest.mark.parametrize("value, seconds", [("6178.46", 6178), ("01:42:59", 6179), ("", None), ("x", None)])
def test_internet_archive_lengths(value, seconds):
    assert internet_archive.length({"length": value}) == seconds


def test_platform_config_checks():
    for name, source, message in [
        ("civicclerk", {"boards": {"City Council": "x"}}, "needs api_url"),
        ("internet_archive", {"boards": {"City Council": "x"}}, "needs collection"),
    ]:
        with pytest.raises(SystemExit, match=message):
            recordings.check({"recordings": {"sources": [{"platform": name, **source}]}})


# ---- TelVue ------------------------------------------------------------------------------------------

class FakeTelVue:
    def __init__(self):
        self.urls = []

    def get(self, url):
        from fakes import FakeResponse
        self.urls.append(url)
        name = ("telvue_playlist_items.html" if "playlist_items?offset=" in url else "telvue_master.m3u8"
                if url.endswith("master.m3u8") else "telvue_media.html" if "/media/" in url else "telvue_playlist.html")
        return FakeResponse((FIXTURES / name).read_bytes())


LOWELL = {"platform": "telvue", "player": "4jeRlBTetCoFJdyT5ssIvBrB9VUvRN0M", "playlists": [1757],
          "boards": {"City Council": "^City Council - ", "Committee of the Whole": r"^Committee of the Whole \d"}}


def test_telvue_reads_a_playlist_page_by_page():
    client = FakeTelVue()
    found = telvue.find(client, LOWELL, "2026-05-01", "America/New_York")
    # The playlist says 19, its page lists 12: the rest are asked for from there.
    assert client.urls[1] == "https://videoplayer.telvue.com/player/4jeRlBTetCoFJdyT5ssIvBrB9VUvRN0M/playlists/1757/playlist_items?offset=12"
    council = found[0]
    assert council == {"id": "telvue-1065910", "platform": "telvue", "board": "City Council", "date": "2026-10-27",
                       "title": "City Council - October 27, 2026",
                       "url": "https://videoplayer.telvue.com/player/4jeRlBTetCoFJdyT5ssIvBrB9VUvRN0M/media/1065910",
                       "seconds": 11960, "part": None, "media_id": "1065910"}
    whole = [f for f in found if f["board"] == "Committee of the Whole"]
    assert [(f["date"], f["part"]) for f in whole] == [("2026-05-18", 2)]
    # Titles with no date aren't read.
    assert all(f["date"] for f in found)


def test_telvue_reads_the_audio_only_stream():
    client = FakeTelVue()
    got = telvue.media(client, LOWELL, {"url": "https://videoplayer.telvue.com/player/x/media/1065910"})
    assert got["url"] == ("https://telvuevod-secure.akamaized.net/vodhls/vod_player/71/media/1065910/1793163525/"
                          "index-saac_128k_0-a1.m3u8")


# ---- Castus --------------------------------------------------------------------------------------------

class FakeCastus:
    def __init__(self):
        self.urls, self.posted = [], []

    def get(self, url):
        from fakes import FakeResponse
        self.urls.append(url)
        if url.endswith(".m3u8"):
            return FakeResponse((FIXTURES / "castus_master.m3u8").read_bytes())
        name = "castus_config.json" if url.startswith(castus.CONFIG) else "castus_playlist.json"
        return FakeJSONResponse(json.loads((FIXTURES / name).read_text()))

    def post_json(self, url, body):
        self.posted.append((url, body))
        return FakeJSONResponse(json.loads((FIXTURES / "castus_media.json").read_text()))


MEDFORD = {"platform": "castus", "channel": "medford", "playlists": ["Medford City Council"],
           "boards": {"City Council": r"^City Council \d",
                      "City Council Public Works and Facilities Committee": r"^City Council Public Works"}}


def test_castus_reads_a_playlist_by_name():
    client = FakeCastus()
    found = castus.find(client, MEDFORD, "2026-10-01", "America/New_York")
    assert client.urls[1] == f"{castus.PLAYLISTS}/65aaf7ac239d4e0df91594c3/playlist/Medford%20City%20Council"
    assert [(f["board"], f["date"], f["seconds"]) for f in found] == [
        ("City Council", "2026-10-27", 11128),
        ("City Council Public Works and Facilities Committee", "2026-10-21", 5118),
        ("City Council", "2026-10-13", 14236)]
    assert found[0]["url"] == "https://cloud.castus.tv/vod/#/medford/video/6ae2605584f38f000225131c"


def test_castus_reads_the_smallest_stream():
    client = FakeCastus()
    got = castus.media(client, MEDFORD, {"video_id": "6ad8d491b2610c0002277ecf"})
    assert client.posted == [(castus.MEDIA, {"file": "6ad8d491b2610c0002277ecf", "type": "HLS", "user": ""})]
    assert got["url"] == "https://dlttx48mxf9m3.cloudfront.net/outputs/6ad8d491b2610c0002277ecf/Default/HLS/out360p.m3u8"


# ---- Cablecast ----------------------------------------------------------------------------------------

class FakeCablecast:
    def __init__(self, shows="cablecast_shows.json"):
        self.shows, self.urls = shows, []

    def get(self, url):
        from fakes import FakeResponse
        self.urls.append(url)
        if url.endswith(".m3u8"):
            return FakeResponse((FIXTURES / "cablecast_vod.m3u8").read_bytes())
        return FakeJSONResponse(json.loads((FIXTURES / self.shows).read_text()))


METHUEN = {"platform": "cablecast", "host": "https://methuentv.cablecast.tv", "projects": [157],
           "boards": {"City Council": r"^City Council (Regular |Special )?Meeting\b"}}


def test_cablecast_reads_a_projects_shows_with_their_recordings():
    client = FakeCablecast()
    found = cablecast.find(client, METHUEN, "2026-09-15", "America/New_York")
    assert client.urls == ["https://methuentv.cablecast.tv/cablecastapi/v1/shows?include=vod&page_size=50&offset=0&project=157"]
    # The title's date, where the show's own is a day off.
    assert [(f["id"], f["date"], f["seconds"]) for f in found] == [
        ("cablecast-8421", "2026-10-19", 8932), ("cablecast-8392", "2026-10-05", 7194),
        ("cablecast-8358", "2026-09-24", 318), ("cablecast-8355", "2026-09-21", 10410)]
    assert found[0]["url"] == "https://methuentv.cablecast.tv/internetchannel/show/8421"


def test_cablecast_reads_the_recordings_audio_track():
    got = cablecast.media(FakeCablecast(), METHUEN, {"vod_url": "https://methuentv.cablecast.tv/store-5/8426-cc-2026-10-19-v2/vod.mp4"})
    assert got["url"] == "https://methuentv.cablecast.tv/store-5/8426-cc-2026-10-19-v2/1080p_audio.m3u8"
    # An older recording, a single file, is read whole.
    old = "http://ondemand.satvonline.org/store-3/3975-Zoning-Board-Meeting-8-17-16-Medium-v1.mp4"
    assert cablecast.media(FakeCablecast(), METHUEN, {"vod_url": old}) == {"url": old}


def test_cablecast_by_category_leaves_shows_without_a_recording():
    salem = {"platform": "cablecast", "host": "http://cc.satvonline.org", "categories": [44],
             "boards": {"Zoning Board of Appeals": r"^Salem Zoning Board of Appeals\b", "City Council": r"City Council"}}
    found = cablecast.find(FakeCablecast("cablecast_shows_category.json"), salem, "2026-10-01", "America/New_York")
    # The council's meetings of 10-22 have no recording yet (one is the live stream's placeholder).
    assert [(f["board"], f["date"]) for f in found] == [("Zoning Board of Appeals", "2026-10-21")]


# ---- YouTube, transcribed by Gemini ------------------------------------------------------------------------

class FakeHTTP:
    """requests.get or requests.post: the response given for each call, and what each was asked."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        status, data = self.responses.pop(0)
        return SimpleNamespace(status_code=status, json=lambda: data, text=json.dumps(data))


METHUEN_SCHOOLS = {"platform": "youtube", "channel": "UCez3QQIzOP9up4JK-B08L5g",
                   "boards": {"School Committee": r"^Methuen School Committee \d"}}


def test_youtube_finds_videos_in_the_feed_and_their_lengths(monkeypatch):
    from fakes import FakeResponse
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = SimpleNamespace(get=lambda url: FakeResponse((FIXTURES / "youtube_feed.xml").read_bytes()))
    get = FakeHTTP((200, {"items": [
        {"id": "4DvgJgx_Q6U", "snippet": {"liveBroadcastContent": "none"}, "contentDetails": {"duration": "PT2H41M7S"}}]}))
    found = youtube.find(client, METHUEN_SCHOOLS, "2026-10-20", "America/New_York", get=get)
    assert [(f["id"], f["date"], f["seconds"]) for f in found] == [("youtube-4DvgJgx_Q6U", "2026-10-26", 9667)]
    assert found[0]["url"] == "https://www.youtube.com/watch?v=4DvgJgx_Q6U"
    # The key goes in a header, never in the address.
    url, kwargs = get.calls[0]
    assert url == youtube.VIDEOS and "test-key" not in json.dumps(kwargs["params"])
    assert kwargs["headers"] == {"x-goog-api-key": "test-key"}


def test_youtube_without_a_key_finds_videos_that_wait(monkeypatch):
    from fakes import FakeResponse
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = SimpleNamespace(get=lambda url: FakeResponse((FIXTURES / "youtube_feed.xml").read_bytes()))
    found = youtube.find(client, METHUEN_SCHOOLS, "2026-09-01", "America/New_York", get=lambda *a, **k: pytest.fail())
    assert len(found) == 4 and all("seconds" not in f for f in found)
    assert youtube.transcript(None, METHUEN_SCHOOLS, {**found[0], "seconds": 600}) is None


def test_a_live_video_has_no_length_yet():
    get = FakeHTTP((200, {"items": [{"id": "a", "snippet": {"liveBroadcastContent": "live"}, "contentDetails": {"duration": "P0D"}},
                                    {"id": "b", "snippet": {"liveBroadcastContent": "none"}, "contentDetails": {"duration": "PT45M"}}]}))
    assert youtube.details(["a", "b"], "k", get) == {"a": {"seconds": None}, "b": {"seconds": 2700}}


def interaction(text, tokens=(1000, 500)):
    return {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
            "usage": {"total_input_tokens": tokens[0], "total_output_tokens": tokens[1]}}


def test_gemini_transcribes_a_video_a_part_at_a_time(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    post = FakeHTTP(
        (200, interaction("[0:00:05] Good evening. The committee will come to order.\n[0:12:40] Motion to approve the budget.")),
        # The second part's times, given from its own start: moved to the recording's.
        (200, interaction("[0:01:10] The motion carries 5 to 0.\nThe meeting is adjourned.")),
    )
    text, made = youtube.transcript(None, METHUEN_SCHOOLS, {"video_id": "abc", "seconds": 1500, "title": "School Committee",
                                                            "date": "2026-10-26"}, post=post)
    assert text == ("[0:00:05] Good evening. The committee will come to order.\n"
                    "[0:12:40] Motion to approve the budget.\n"
                    "[0:21:10] The motion carries 5 to 0. The meeting is adjourned.\n")
    assert made == {"model": gemini.MODEL, "usage": {"requests": 2, "input_tokens": 2000, "output_tokens": 1000}}
    first, second = (kwargs["json"] for _, kwargs in post.calls)
    video = first["input"][0]
    assert video["uri"] == "https://www.youtube.com/watch?v=abc"
    assert video["processing"] == {"type": "static", "start_offset": 0, "end_offset": 1200, "fps": gemini.FPS}
    assert second["input"][0]["processing"]["start_offset"] == 1200 and second["input"][0]["processing"]["end_offset"] == 1500
    assert first["store"] is False
    assert "from 0:20:00 to 0:25:00" in second["input"][1]["text"]
    assert post.calls[0][1]["headers"]["x-goog-api-key"] == "test-key"


def test_gemini_errors_are_a_failed_try():
    from pipeline.http import FetchError
    post = FakeHTTP((429, {"error": {"message": "quota"}}))
    with pytest.raises(FetchError, match="HTTP 429"):
        gemini.transcribe(post, "abc", 600, "x", "2026-10-26", "k")
    with pytest.raises(transcribe.TranscribeError, match="heard nothing"):
        gemini.transcribe(FakeHTTP((200, interaction(""))), "abc", 600, "x", "2026-10-26", "k")


def test_youtube_is_the_backup():
    own = {"id": "telvue-1", "platform": "telvue", "found_at": "2026-10-28T06:00", "source": 0}
    yt = {"id": "youtube-1", "platform": "youtube", "found_at": "2026-10-27T23:00", "source": 1}
    assert recordings.chosen([yt]) == [yt]
    # Found later, the town's own recording is still the one transcribed.
    assert recordings.chosen([yt, own]) == [own]
    # Once a meeting is transcribed, a recording found after it isn't.
    assert recordings.chosen([{**yt, "transcript": {"sha256": "x"}}, own])[0]["id"] == "youtube-1"
