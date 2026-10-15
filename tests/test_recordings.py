"""A meeting's own recording, where its listing links one (a Finalsite district's YouTube
link, or a town file list's data-video), on the meeting's page."""

import copy
import json
import shutil

from pipeline import build_site


def test_a_meeting_links_its_own_recording(config, data_dir, tmp_path, monkeypatch):
    from conftest import BUILT_AT
    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    path = data / "meetings" / "meetings.json"
    meetings = json.loads(path.read_text())
    records = meetings.values() if isinstance(meetings, dict) else meetings
    past = next(m for m in records if m["date"] < "2026-09-30" and m.get("status", "scheduled") != "cancelled")
    upcoming = next(m for m in records if m["date"] > "2026-10-02" and m.get("status", "scheduled") == "scheduled")
    past.update(video_id="CyV9OIpJIT8", video_start=9805)
    upcoming["video_id"] = "Ab_c-12345"
    path.write_text(json.dumps(meetings))

    config = copy.deepcopy(config)  # the session's config, shared with other tests
    config["site"]["languages"] = ["en", "es"]
    monkeypatch.setattr(build_site, "load_config", lambda slug: config)
    out = tmp_path / "site"
    build_site.build("gloucester", out, data_dir=data, now=BUILT_AT)
    shown = {m["id"]: m for m in build_site.load_meetings(data, BUILT_AT.date())["all"]}

    page = (out / "meetings" / shown[past["id"]]["slug"] / "index.html").read_text()
    assert 'href="https://www.youtube.com/watch?v=CyV9OIpJIT8&amp;t=9805s"' in page
    assert "Watch the recording</a> of this meeting on YouTube." in page.replace(
        '<span class="visually-hidden"> (opens in new tab)</span>', "")
    es = (out / "es" / "meetings" / shown[past["id"]]["slug"] / "index.html").read_text()
    assert "Vea la grabación" in es and "watch?v=CyV9OIpJIT8" in es

    page = (out / "meetings" / shown[upcoming["id"]]["slug"] / "index.html").read_text()
    assert 'href="https://www.youtube.com/watch?v=Ab_c-12345"' in page
    assert "Watch it on" in page
