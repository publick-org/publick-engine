"""Offline stand-ins for network access, serving saved fixtures."""

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, content: bytes, headers: dict | None = None):
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.headers = headers or {}


class FakeCityClient:
    """Serves the saved calendar feed, one event page for every event, and one agenda PDF."""

    def __init__(self, feed: bytes | None = None, event_page: str | None = None):
        self.feed = feed or (FIXTURES / "civicplus_calendar.xml").read_bytes()
        self.event_page = event_page or (FIXTURES / "civicplus_event.html").read_text()
        self.agenda = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        self.request_count += 1
        if "RSSFeed.aspx" in url:
            return FakeResponse(self.feed)
        if "Calendar.aspx?EID=" in url:
            return FakeResponse(self.event_page.encode())
        if url.endswith("/Archive.aspx"):
            return FakeResponse((FIXTURES / "civicplus_archive_index.html").read_bytes())
        if "Archive.aspx?ADID=" in url:
            adid = url.rsplit("=", 1)[1]
            # Each archive document gets distinct bytes (a trailing PDF comment), as real ones would.
            content = self.agenda if adid == "20124" else self.agenda + f"\n% document {adid}\n".encode()
            return FakeResponse(content, {"content-disposition": "inline;filename=September 28 2026.pdf"})
        raise AssertionError(f"unexpected URL {url}")


class FakeJSONResponse(FakeResponse):
    def __init__(self, data):
        import json
        super().__init__(json.dumps(data).encode())
        self._data = data

    def json(self):
        return self._data


class FakeSeeClickFix:
    """Serves saved Open311 pages and a single-issue record for any id."""

    def __init__(self, open_items=None, window_items=None, issue=None, missing_ids=(), services=(), refused_ids=(),
                 refuse_all=False):
        import json
        self.services = list(services)
        self.open_items = open_items if open_items is not None else json.loads((FIXTURES / "open311_open_page.json").read_text())
        self.window_items = window_items if window_items is not None else json.loads((FIXTURES / "open311_window_page.json").read_text())
        self.issue = issue or json.loads((FIXTURES / "scf_issue_acknowledged.json").read_text())
        self.missing_ids = set(missing_ids)
        # A request made private (403 for that one), or SeeClickFix refusing every lookup.
        self.refused_ids, self.refuse_all = set(refused_ids), refuse_all
        self.urls = []
        self.request_count = 0

    def get(self, url):
        from pipeline.http import FetchError
        self.urls.append(url)
        self.request_count += 1
        if url.endswith("/services.json"):
            return FakeJSONResponse(self.services)
        if "/requests.json" in url:
            page = int(url.split("page=")[1].split("&")[0])
            items = self.open_items if "status=open" in url else self.window_items
            return FakeJSONResponse(items[(page - 1) * 100: page * 100])
        if "/issues/" in url:
            issue_id = url.rsplit("/", 1)[1]
            if issue_id in self.missing_ids:
                raise FetchError(f"{url}: HTTP 404", 404)
            if self.refuse_all or issue_id in self.refused_ids:
                raise FetchError(f"{url}: HTTP 403", 403)
            item = next((i for i in self.open_items + self.window_items if str(i["service_request_id"]) == issue_id), None)
            data = dict(self.issue, id=int(issue_id))
            if item and item["status"] == "closed":
                data.update(status="Archived", closed_at=item["updated_datetime"], updated_at=item["updated_datetime"])
            elif item and int(issue_id) % 2:
                data.update(status="Acknowledged", acknowledged_at=item["updated_datetime"], updated_at=item["updated_datetime"])
            elif item:
                data.update(status="Open", acknowledged_at=None, updated_at=item["updated_datetime"])
            data["created_at"] = item["requested_datetime"] if item else data["created_at"]
            return FakeJSONResponse(data)
        raise AssertionError(f"unexpected URL {url}")


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: returns a fixed structured result."""

    # A transcription of the fixture agenda (a scan, with no text of its own).
    TRANSCRIPT = {
        "transcript": "# Human Rights Commission\n\n1. Call to order.\n2. Review and approval of July 27, 2026 minutes\n3. Meeting Joe Lucido, Assistant Director of Operations on City ADA compliance\n4. Review HRC Student Member Recruitment Search Draft Description\n5. Community updates.\n6. Next Meeting: October 26",
    }
    PREVIEW = {
        "headline": "ADA compliance with the city's operations director and a draft plan for recruiting a student member.",
        "summary": "The commission will meet with the city's Assistant Director of Operations about ADA compliance and review a draft description for recruiting a student member.",
        "items": ["ADA compliance with Joe Lucido", "Student member recruitment description", "Community updates"],
        "start_time": "",
        "location": "",
    }
    MINUTES = {
        "headline": "Approved a site plan for 12 Main St.",
        "summary": "The board approved a site plan for 12 Main St.",
        "is_minutes": True,
        "decisions": [{"decision": "Approved the site plan for 12 Main St, 5-0", "outcome": "approved",
                       "quote": "Motion to approve the site plan for 12 Main St. Motion carried 5-0."}],
    }

    def __init__(self, stop_reason: str = "end_turn", preview: dict | None = None, translation_drops_numbers: bool = False,
                 review_problems: list[dict] | None = None):
        from types import SimpleNamespace
        self.calls = []
        outer = self

        def respond(kwargs):
            import json
            outer.calls.append(kwargs)
            properties = kwargs["output_config"]["format"]["schema"]["properties"]
            if set(properties) == {"transcript"}:
                payload = FakeAnthropic.TRANSCRIPT
            elif "decisions" in properties:
                payload = FakeAnthropic.MINUTES
            else:
                payload = preview or FakeAnthropic.PREVIEW
            return SimpleNamespace(
                stop_reason=stop_reason,
                content=[SimpleNamespace(type="text", text=json.dumps(payload))],
                usage=SimpleNamespace(input_tokens=1500, output_tokens=400),
            )

        class Stream:
            def __init__(self, kwargs):
                self.kwargs = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return respond(self.kwargs)

        def translate(kwargs):
            """A "translation" of the English summary in the request: each text marked ES, numbers kept
            (or dropped, for a translation that fails its check)."""
            import json
            import re
            outer.calls.append(kwargs)
            text = kwargs["messages"][0]["content"]

            def es(t):
                t = "ES " + re.sub(r"\b[Aa]pproved\b", "aprobó", t)
                return re.sub(r"\d", "", t) if translation_drops_numbers else t
            properties = kwargs["output_config"]["format"]["schema"]["properties"]
            if "problems" in properties:
                # The review of a translation's meaning: what the test says it finds, or nothing.
                return SimpleNamespace(
                    stop_reason=stop_reason,
                    content=[SimpleNamespace(type="text", text=json.dumps(
                        {"problems": [{"is_error": True, **p} for p in review_problems or []]}))],
                    usage=SimpleNamespace(input_tokens=500, output_tokens=50),
                )
            if "translations" in properties:
                # A town's own text and names, drafted: a list of texts, a list back.
                payload = {"translations": [es(x) for x in json.loads(text[text.index("\n[") + 1:])]}
            else:
                # The summary to translate; a second try has the first translation after it.
                english, _ = json.JSONDecoder().raw_decode(text, text.index("\n{") + 1)
                payload = {k: [es(x) for x in v] if isinstance(v, list) else es(v) for k, v in english.items()}
            return SimpleNamespace(
                stop_reason=stop_reason,
                content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
                usage=SimpleNamespace(input_tokens=600, output_tokens=300),
            )

        class Messages:
            def stream(self, **kwargs):
                return Stream(kwargs)

            def create(self, **kwargs):
                return translate(kwargs)

        self.messages = Messages()


class FakeDESE:
    """Serves saved DESE open data responses, one per measure."""

    def __init__(self):
        self.urls = []

    def get(self, url):
        import json
        from urllib.parse import unquote_plus
        from pipeline.states.ma.schools import MEASURES
        self.urls.append(url)
        query = unquote_plus(url)
        name = next(n for n, m in MEASURES.items() if m["dataset"] in url and m["where"] in query)
        return FakeJSONResponse(json.loads((FIXTURES / f"dese_{name}.json").read_text()))


class FakeBudgetDLS:
    """Serves saved DLS budget workbooks. Schedule A has FY2024 and FY2025; later
    years come back as zeros, earlier ones as an empty table (as the site does)."""

    def __init__(self):
        self.urls = []

    def get(self, url):
        from urllib.parse import parse_qs, urlparse
        self.urls.append(url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        folder = FIXTURES / "dls_budget"
        report = q["rdReport"]
        if report == "ScheduleA.GeneralFund":
            year = int(q["islYear"])
            name = f"schedule_a_{year}.xlsx" if year in (2024, 2025) else "schedule_a_2026.xlsx"
        elif report == "DLS_Bond_Ratings":
            name = "bonds_moodys.xlsx" if q["islCompany"] == "Moodys" else "bonds_sp.xlsx"
        else:
            name = {"RevenueBySource.RBS.RevbySource2": "revenue.xlsx", "Prop2.5.ExcessLevyCapandOverride_10_pres": "levy.xlsx",
                    "FreeCash2": "free_cash.xlsx", "Dashboard.TrendAnalysisReports.StabFund": "stabilization.xlsx",
                    "351GenFunperCapita": "per_capita.xlsx"}[report]
        return FakeResponse((folder / name).read_bytes())


class FakeHousing:
    """Serves trimmed Census permit files, a saved Census Reporter response, a
    stand-in PDF, and DLS parcel counts. Patch pipeline.states.ma.housing.pdf_text with
    shi_pdf_text so the stand-in PDF reads as the saved inventory text."""

    def __init__(self):
        self.urls = []

    def get(self, url):
        import json
        self.urls.append(url)
        folder = FIXTURES / "housing"
        if "census.gov/econ/bps" in url:
            name = url.rsplit("/", 1)[1]
            path = folder / f"bps_{name}"
            text = path.read_bytes() if path.exists() else (folder / "bps_ne2025a.txt").read_bytes().split(b"\n \n")[0]
            return FakeResponse(text)
        if "censusreporter" in url:
            return FakeJSONResponse(json.loads((folder / "census_reporter.json").read_text()))
        if "mass.gov" in url:
            return FakeResponse(b"%PDF-1.7 stand-in")
        if "Parcel_counts" in url:
            return FakeResponse((folder / "dls_parcels.xlsx").read_bytes())
        raise AssertionError(url)


def shi_pdf_text(pdf: bytes) -> str:
    return (FIXTURES / "housing" / "shi_text.txt").read_text()


class FakePermits:
    """The city's public Drive folder listing and its permit CSV."""
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if "embeddedfolderview" in url:
            return FakeResponse((FIXTURES / "permits" / "drive_folder.html").read_bytes())
        if "id=NEWFILE" in url:
            return FakeResponse((FIXTURES / "permits" / "inspsrvcs.csv").read_bytes())
        raise AssertionError(f"unexpected URL {url}")


class FakeDrive:
    """The school district's public Drive folders, saved as served. A folder
    with no saved page is empty; every file downloads as a distinct PDF."""
    EMPTY_FOLDER = b'<html><body><div class="flip-entries"></div></body></html>'

    def __init__(self):
        self.urls = []
        self.pdf = (FIXTURES / "civicplus_agenda_scanned.pdf").read_bytes()

    def get(self, url):
        import re
        self.urls.append(url)
        if "embeddedfolderview?id=" in url:
            # An older shared folder's address also carries its resource key.
            page = FIXTURES / "drive" / f"{re.search(r'[?&]id=([\w-]+)', url).group(1)}.html"
            return FakeResponse(page.read_bytes() if page.exists() else self.EMPTY_FOLDER)
        if url.endswith("/meeting-schedule"):
            return FakeResponse((FIXTURES / "drive" / "schedule.html").read_bytes())
        if "uc?export=download&id=" in url:
            return FakeResponse(self.pdf + f"\n% drive file {url.rsplit('=', 1)[1]}\n".encode())
        raise AssertionError(f"unexpected URL {url}")


class FakeManchester:
    """Manchester's two meeting calendars, saved as served: the CivicClerk API
    (two pages of meetings from August 2026) and the city's DotNetNuke calendar
    (September and October 2026; other months are empty), with one saved event
    page served for every event."""
    EMPTY_MONTH = b"<div>No events</div>"

    def __init__(self, civicclerk_pages: list[dict] | None = None):
        import json
        self.pages = civicclerk_pages or [json.loads((FIXTURES / f"civicclerk_events_{n}.json").read_text()) for n in (1, 2)]
        self.urls = []
        self.request_count = 0

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        if "api.civicclerk.com" in url:
            return FakeJSONResponse(self.pages[1] if "skiptoken" in url else self.pages[0])
        if "/mctl/EventMonth/selecteddate/" in url:
            month = url.rsplit("/", 1)[1]  # 09-01-2026
            page = FIXTURES / f"dnn_calendar_{month[6:]}-{month[:2]}.html"
            return FakeResponse(page.read_bytes() if page.exists() else self.EMPTY_MONTH)
        if "/mctl/EventDetails" in url:
            return FakeResponse((FIXTURES / "dnn_event.html").read_bytes())
        raise AssertionError(f"unexpected URL {url}")


class FakeFinalsite:
    """A school district's Finalsite board page and the posts on it (Wallingford's Board of
    Education, saved as served), and the Google Docs they link. A post with no saved body
    links only its agenda. Every Doc exports as a distinct PDF; `edits` gives a Doc other
    content, as editing it in place would, and `posts` replaces a post's body."""
    PAGE_URL = "https://www.wallingford.k12.ct.us/board-of-education/board-of-education-meetings"
    SCHEDULE_URL = PAGE_URL + "/board-of-education-schedule-2024"

    def __init__(self, page: str | None = None, posts: dict | None = None, edits: dict | None = None):
        self.page = page or (FIXTURES / "finalsite_board.html").read_text(encoding="utf-8")
        self.posts = posts or {}
        self.edits = edits or {}
        self.pdf = (FIXTURES / "finalsite_doc.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    @staticmethod
    def agenda_only(post_id: str) -> str:
        return (f'<div class="fsElement fsPostElement fsPost  fsSingleItem" id="fsEl_23192"><div class="fsElementContent">'
                f'<article class="fsStyleAutoclear fsBoard-30 " data-post-id="{post_id}"><div class="fsTitle ">A meeting</div>'
                f'<div class="fsBody"><p><a href="https://docs.google.com/document/d/agenda-of-post-{post_id}-0000000/edit'
                f'?usp=sharing" target="_blank">Agenda</a></p></div></article></div></div>')

    def get(self, url):
        import re
        self.urls.append(url)
        self.request_count += 1
        if url == self.PAGE_URL:
            return FakeResponse(self.page.encode())
        if url == self.SCHEDULE_URL:
            return FakeResponse((FIXTURES / "finalsite_schedule.html").read_bytes())
        if url.startswith("https://www.wallingford.k12.ct.us/fs/elements/23192?"):
            post_id = re.search(r"post_id=(\d+)", url).group(1)
            saved = FIXTURES / f"finalsite_post_{post_id}.html"
            body = self.posts.get(post_id) or (saved.read_text(encoding="utf-8") if saved.exists() else self.agenda_only(post_id))
            return FakeResponse(body.encode())
        doc = re.fullmatch(r"https://docs\.google\.com/document/d/([\w-]+)/export\?format=pdf", url)
        if doc:
            content = self.pdf + f"\n% doc {doc.group(1)} {self.edits.get(doc.group(1), '')}\n".encode()
            return FakeResponse(content, {"content-disposition": "attachment; filename=\"AGENDA.pdf\"; "
                                                                 "filename*=UTF-8''AGENDA%20SEPTEMBER%2030%2C%202026%20.pdf"})
        raise AssertionError(f"unexpected URL {url}")
