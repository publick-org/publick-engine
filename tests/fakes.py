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


def saved_issues() -> list[dict]:
    """The saved Open311 pages' requests as APIv2 lists them: an open one acknowledged when its id is
    odd, at its last update; a closed one archived, closed at its last update."""
    import json
    issue = json.loads((FIXTURES / "scf_issue_acknowledged.json").read_text())
    items = {}
    for name in ("open311_open_page.json", "open311_window_page.json"):
        for item in json.loads((FIXTURES / name).read_text()):
            items.setdefault(item["service_request_id"], item)
    issues = []
    for item in items.values():
        data = dict(issue, id=item["service_request_id"], created_at=item["requested_datetime"],
                    updated_at=item["updated_datetime"], lat=item["lat"], lng=item["long"], address=item["address"],
                    request_type={"id": item["service_code"], "title": item["service_name"], "organization": "Gloucester, MA"})
        if item["status"] == "closed":
            data.update(status="Archived", closed_at=item["updated_datetime"])
        elif item["service_request_id"] % 2:
            data.update(status="Acknowledged", acknowledged_at=item["updated_datetime"])
        else:
            data.update(status="Open", acknowledged_at=None)
        issues.append(data)
    return issues


def real_issues() -> list[dict]:
    """A page of real APIv2 requests (Gloucester, October 2026), without what their reporters wrote."""
    import json
    return json.loads((FIXTURES / "scf_v2_issues.json").read_text())["issues"]


class FakeSeeClickFix:
    """Serves a set of APIv2 requests as SeeClickFix lists them: on the organization's Open311 list
    (by submission time and open or closed), and on APIv2 (by request type, submission and update
    time, and status, no page past the 25th), newest first, 100 to a page; and one at a time.
    issues defaults to saved_issues(); a test changes them between runs as the city would."""

    def __init__(self, issues=None, missing_ids=(), services=(), refused_ids=(), refuse_all=False,
                 unlisted_ids=(), failing=None, refuse_listing=None):
        self.services = list(services)
        self.issues = issues if issues is not None else saved_issues()
        self.missing_ids = {str(i) for i in missing_ids}
        # A request made private (403 for that one), or SeeClickFix refusing every lookup.
        self.refused_ids, self.refuse_all = {str(i) for i in refused_ids}, refuse_all
        # Requests on Open311 that APIv2's listings leave out.
        self.unlisted_ids = {str(i) for i in unlisted_ids}
        # "open311" or "v2": that listing answers 503.
        self.failing = failing
        # APIv2's listing refused, as PoliteClient reports it (pipeline/http.py, Lane): True for every
        # listing request, or a function of the URL saying which.
        self.refuse_listing = refuse_listing
        self.urls = []
        self.request_count = 0

    @staticmethod
    def open311(issue: dict) -> dict:
        return {"service_request_id": issue["id"], "service_code": issue["request_type"]["id"],
                "service_name": issue["request_type"]["title"], "requested_datetime": issue["created_at"],
                "updated_datetime": issue["updated_at"], "status": "open" if issue["status"] in ("Open", "Acknowledged") else "closed",
                "lat": issue["lat"], "long": issue["lng"], "address": issue["address"], "description": "", "media_url": None}

    def get(self, url):
        from datetime import datetime
        from urllib.parse import parse_qs, urlsplit

        from pipeline.http import FetchError, Refused
        self.urls.append(url)
        self.request_count += 1
        path = urlsplit(url).path
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}

        def within(ts, start, end):
            t = datetime.fromisoformat(ts)
            return (not start or t >= datetime.fromisoformat(start)) and (not end or t < datetime.fromisoformat(end))

        newest = sorted(self.issues, key=lambda i: datetime.fromisoformat(i["created_at"]), reverse=True)
        if path.endswith("/services.json"):
            return FakeJSONResponse(self.services)
        if path.endswith("/requests.json"):
            if self.failing == "open311":
                raise FetchError(f"{url}: HTTP 503", 503)
            items = [self.open311(i) for i in newest if within(i["created_at"], q.get("start_date"), q.get("end_date"))
                     and q.get("status", self.open311(i)["status"]) == self.open311(i)["status"]]
            page, size = int(q["page"]), int(q["page_size"])
            return FakeJSONResponse(items[(page - 1) * size: page * size])
        if path.endswith("/issues"):
            if self.failing == "v2":
                raise FetchError(f"{url}: HTTP 503", 503)
            if self.refuse_listing is True or (callable(self.refuse_listing) and self.refuse_listing(url)):
                raise Refused("seeclickfix.com/api/v2/issues: HTTP 403", 403)
            page, size = int(q["page"]), int(q["per_page"])
            if page > 25:
                raise FetchError(f"{url}: HTTP 400", 400)
            codes, statuses = set(q["request_types"].split(",")), set(q["status"].split(","))
            items = [i for i in newest if str(i["request_type"]["id"]) in codes and i["status"].lower() in statuses
                     and str(i["id"]) not in self.unlisted_ids
                     and within(i["created_at"], q.get("after"), q.get("before"))
                     and within(i["updated_at"], q.get("updated_at_after"), q.get("updated_at_before"))]
            return FakeJSONResponse({"issues": items[(page - 1) * size: page * size], "errors": {},
                                     "metadata": {"pagination": {"entries": len(items), "page": page, "per_page": size}}})
        if "/issues/" in path:
            issue_id = path.rsplit("/", 1)[1]
            if issue_id in self.missing_ids:
                raise FetchError(f"{url}: HTTP 404", 404)
            if self.refuse_all or issue_id in self.refused_ids:
                raise FetchError(f"{url}: HTTP 403", 403)
            issue = next((i for i in self.issues if str(i["id"]) == issue_id), None)
            if issue is None:
                raise FetchError(f"{url}: HTTP 404", 404)
            return FakeJSONResponse(issue)
        raise AssertionError(f"unexpected URL {url}")


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: returns a fixed structured result. Each response has an id of
    its own, as the API's do (the cost log's key)."""

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
                 review_problems: list[dict] | None = None, batch_polls: int | None = 1,
                 batch_results: dict | None = None, input_tokens: int = 1500):
        """batch_polls: how many times a batch is looked at before it has ended (None: never, until
        cancelled). batch_results: a request's result type by its place in the batch ("errored",
        "canceled"), where it isn't "succeeded"; a cancelled batch's requests are all "canceled".
        input_tokens: each summary's and transcription's prompt, in tokens."""
        import uuid
        from types import SimpleNamespace
        self.calls = []
        message_id = lambda: f"msg_{uuid.uuid4().hex[:24]}"
        # Each batch's requests' params, in the order sent.
        self.batches_sent = []
        self.cancelled = []
        outer = self

        def respond(kwargs, record=True):
            import json
            if record:
                outer.calls.append(kwargs)
            properties = kwargs["output_config"]["format"]["schema"]["properties"]
            if set(properties) == {"transcript"}:
                payload = FakeAnthropic.TRANSCRIPT
            elif "decisions" in properties:
                payload = FakeAnthropic.MINUTES
            else:
                payload = preview or FakeAnthropic.PREVIEW
            return SimpleNamespace(
                id=message_id(),
                stop_reason=stop_reason,
                content=[SimpleNamespace(type="text", text=json.dumps(payload))],
                usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=400),
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
                    id=message_id(),
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
                id=message_id(),
                stop_reason=stop_reason,
                content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
                usage=SimpleNamespace(input_tokens=600, output_tokens=300),
            )

        class Batches:
            def __init__(self):
                self.looked = {}

            def create(self, requests):
                outer.batches_sent.append([r["params"] for r in requests])
                return SimpleNamespace(id=f"batch-{len(outer.batches_sent)}")

            def retrieve(self, batch_id):
                self.looked[batch_id] = self.looked.get(batch_id, 0) + 1
                ended = batch_id in outer.cancelled or (batch_polls is not None and self.looked[batch_id] >= batch_polls)
                return SimpleNamespace(id=batch_id, processing_status="ended" if ended else "in_progress")

            def cancel(self, batch_id):
                outer.cancelled.append(batch_id)

            def results(self, batch_id):
                requests = outer.batches_sent[int(batch_id.removeprefix("batch-")) - 1]
                out = []
                for i, params in enumerate(requests):
                    kind = "canceled" if batch_id in outer.cancelled else (batch_results or {}).get(i, "succeeded")
                    result = (SimpleNamespace(type="succeeded", message=respond(params, record=False))
                              if kind == "succeeded" else
                              SimpleNamespace(type=kind, error=SimpleNamespace(error=SimpleNamespace(message="overloaded"))))
                    out.append(SimpleNamespace(custom_id=f"doc-{i}", result=result))
                # In any order, as the API gives them.
                return reversed(out)

        class Messages:
            def __init__(self):
                self.batches = Batches()

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


class FakeCensus:
    """Serves TIGERweb's county for any point, and a small saved extract of the Census Bureau's address
    ranges (ADDRFEAT: a few Gloucester streets, and one of Rockport's) as Essex County's file for the
    years given; any other year's file isn't published (404)."""

    def __init__(self, years=(2026,), county="25009"):
        self.years = set(years)
        self.county = county
        self.urls = []

    def get(self, url):
        from pipeline.http import FetchError
        self.urls.append(url)
        if "tigerweb.geo.census.gov" in url:
            return FakeJSONResponse({"features": [{"attributes": {"GEOID": self.county}}]})
        if "/ADDRFEAT/" in url:
            year = int(url.split("/TIGER", 1)[1][:4])
            if year in self.years and f"_{self.county}_" in url:
                return FakeResponse((FIXTURES / "addrfeat_gloucester.zip").read_bytes())
            raise FetchError(f"{url}: HTTP 404", 404)
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


class FakeLinkPage:
    """A school district's page that links each meeting's documents (Methuen Public Schools'
    table, Waltham Public Schools' list, or Nashua's dated lines and the Policy committee's page,
    saved and trimmed: tests/fixtures/linkpage_*.html),
    and the Google Drive files it links, each downloading as a distinct PDF. `page` replaces
    the saved page of the board itself, and `replaced` any page by its address; `missing` lists
    Drive files that answer with a sign-in page instead."""
    METHUEN = "https://www.methuen.k12.ma.us/school-community/meetings-documents"
    WALTHAM = "https://www.walthampublicschools.org/minutes-and-documentation"
    NASHUA = "https://www.nashua.edu/our-district/board-of-education/boe-meeting-agendas-and-minutes"
    NASHUA_POLICY = NASHUA + "/policy"

    def __init__(self, page: str | None = None, missing: tuple = (), replaced: dict | None = None):
        self.pages = {self.METHUEN: FIXTURES / "linkpage_methuen.html", self.WALTHAM: FIXTURES / "linkpage_waltham.html",
                      self.NASHUA: FIXTURES / "linkpage_nashua.html",
                      self.NASHUA_POLICY: FIXTURES / "linkpage_nashua_policy.html"}
        self.page = page
        self.replaced = replaced or {}
        self.missing = missing
        self.pdf = (FIXTURES / "finalsite_doc.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    def get(self, url):
        import re
        self.urls.append(url)
        self.request_count += 1
        if url in self.replaced:
            return FakeResponse(self.replaced[url].encode())
        if url in self.pages:
            replaced = self.page is not None and url in (self.METHUEN, self.NASHUA)
            return FakeResponse(self.page.encode() if replaced else self.pages[url].read_bytes())
        drive = re.fullmatch(r"https://drive\.google\.com/uc\?export=download&id=([\w-]+)", url)
        if drive:
            if drive.group(1) in self.missing:
                return FakeResponse(b"<html>Sign in</html>")
            return FakeResponse(self.pdf + f"\n% file {drive.group(1)}\n".encode(),
                                {"content-disposition": f'attachment; filename="{drive.group(1)}.pdf"'})
        raise AssertionError(f"unexpected URL {url}")


class FakeDiligent:
    """A Diligent Community portal (Burlington School District's, saved and trimmed:
    tests/fixtures/diligent_burlington.json), serving its list of meetings, each meeting's
    documents (tests/fixtures/diligent_documents.json, with each document's id made the
    meeting's own), and each document as a distinct PDF. `entries` replaces the list,
    `documents` a meeting's documents by its id, and `content` a document's bytes by its id."""
    BASE = "https://bsdvt.community.highbond.com"

    def __init__(self, entries: list | None = None, documents: dict | None = None, content: dict | None = None):
        import json
        self.entries = entries if entries is not None else json.loads((FIXTURES / "diligent_burlington.json").read_text())
        self.template = json.loads((FIXTURES / "diligent_documents.json").read_text())
        self.documents = documents or {}
        self.content = content or {}
        self.pdf = (FIXTURES / "finalsite_doc.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    def documents_for(self, meeting_id: int) -> dict:
        if meeting_id in self.documents:
            return {"Documents": self.documents[meeting_id]}
        return {"Documents": [{**d, "Id": meeting_id * 100 + d["DocumentType"], "MeetingId": meeting_id}
                              for d in self.template["Documents"]]}

    def get(self, url):
        import json
        import re
        self.urls.append(url)
        self.request_count += 1
        if url.startswith(f"{self.BASE}/Services/MeetingsService.svc/meetings?"):
            return FakeResponse(json.dumps(self.entries).encode())
        found = re.fullmatch(rf"{re.escape(self.BASE)}/Services/MeetingsService\.svc/meetings/(\d+)/meetingDocuments", url)
        if found:
            return FakeResponse(json.dumps(self.documents_for(int(found.group(1)))).encode())
        found = re.fullmatch(rf"{re.escape(self.BASE)}/document/(\d+)(/\?printPdf=true)?", url)
        if found:
            doc_id = int(found.group(1))
            content = self.content.get(doc_id, self.pdf + f"\n% document {doc_id}\n".encode())
            return FakeResponse(content, {"content-disposition": f'inline; filename="Document {doc_id}.pdf"'})
        raise AssertionError(f"unexpected URL {url}")


class FakeOpenGov:
    """Rhode Island's Open Meetings portal for South Kingstown's School Committee: its completed
    meetings' feed (tests/fixtures/opengov_completed.xml: four meetings of 2026 and one of
    2006), and meeting pages (opengov_meeting_minutes.html, with an agenda and minutes, and
    opengov_meeting_amended.html, with an agenda and its amended version), trimmed. Meeting
    1094488 is served the first page with its own file numbers, and 1082970 the first page
    marked "Annual Calendar". `upcoming` and `cancelled` are feed items ((id, "2026-10-27
    17:00") pairs) for those feeds, `pages` replaces a meeting's page by its id, and `content`
    a file's bytes by its number."""
    BASE = "https://opengov.sos.ri.gov"

    def __init__(self, upcoming=(), cancelled=(), pages: dict | None = None, content: dict | None = None):
        import re
        minutes = (FIXTURES / "opengov_meeting_minutes.html").read_text()
        self.pages = {
            1093108: minutes,
            1094476: (FIXTURES / "opengov_meeting_amended.html").read_text(),
            1094488: minutes.replace("565478", "568425").replace("567158", "568999"),
            1082970: re.sub(r'(id="trMeetingAnnualCalendar">.*?<td>\s*)No', r"\1Yes", minutes, flags=re.S),
            **(pages or {}),
        }
        self.feeds = {3: (FIXTURES / "opengov_completed.xml").read_text(), 1: self.feed(upcoming), 4: self.feed(cancelled)}
        self.content = content or {}
        self.pdf = (FIXTURES / "finalsite_doc.pdf").read_bytes()
        self.urls = []
        self.request_count = 0

    @staticmethod
    def feed(items) -> str:
        from datetime import datetime
        from email.utils import format_datetime
        from zoneinfo import ZoneInfo
        entries = "".join(
            f"<item><link>https://opengov.sos.ri.gov/OpenMeetingsPublic/MeetingInformation?MeetingID={i}</link>"
            f"<title> - {when}</title><description>215 Columbia Street, Wakefield, RI, 02879 &lt;br/&gt; Filed on: x"
            f"</description><pubDate>{format_datetime(datetime.fromisoformat(when).replace(tzinfo=ZoneInfo('America/New_York')))}"
            "</pubDate></item>" for i, when in items)
        return f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>Feed</title>{entries}</channel></rss>'

    def get(self, url):
        import re
        from urllib.parse import unquote
        self.urls.append(url)
        self.request_count += 1
        found = re.match(rf"{re.escape(self.BASE)}/RSSFeed/Rss\?feedType=(\d+)&.*PublicBodies=(.+)$", url)
        if found:
            if unquote(found.group(2)) != "South Kingstown School Committee":
                return FakeResponse(self.feed([]).encode())
            return FakeResponse(self.feeds[int(found.group(1))].encode())
        found = re.fullmatch(rf"{re.escape(self.BASE)}/OpenMeetingsPublic/MeetingInformation\?MeetingID=(\d+)", url)
        if found:
            return FakeResponse(self.pages[int(found.group(1))].encode())
        found = re.fullmatch(rf"{re.escape(self.BASE)}/Common/DownloadMeetingFiles\?FilePath=(.+)", url)
        if found:
            number = re.search(r"(\d+)\.pdf$", unquote(found.group(1))).group(1)
            return FakeResponse(self.content.get(number, self.pdf + f"\n% file {number}\n".encode()))
        raise AssertionError(f"unexpected URL {url}")


class FakePrivateBucket:
    """The private store (pipeline/cost_log.py) through boto3's S3 client calls, as a dict of key -> body.
    Lists a page of PAGE keys at a time, as S3 does 1,000. failing: how many puts fail before they work
    again (None: every one)."""

    PAGE = 3

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = []
        self.failing = 0

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        body = self.objects[Key]
        return {"Body": type("Body", (), {"read": lambda self: body})()}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        if self.failing is None or self.failing > 0:
            if self.failing:
                self.failing -= 1
            raise ConnectionError("the bucket can't be reached")
        self.objects[Key] = Body
        self.puts.append(Key)

    def list_objects_v2(self, Bucket, Prefix, Delimiter=None, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if Delimiter:
            folders = sorted({Prefix + k[len(Prefix):].split(Delimiter)[0] + Delimiter
                              for k in keys if Delimiter in k[len(Prefix):]})
            keys = [k for k in keys if Delimiter not in k[len(Prefix):]]
            return {"Contents": [{"Key": k} for k in keys], "CommonPrefixes": [{"Prefix": f} for f in folders],
                    "IsTruncated": False}
        start = int(ContinuationToken or 0)
        page = keys[start:start + self.PAGE]
        more = start + self.PAGE < len(keys)
        return {"Contents": [{"Key": k} for k in page], "IsTruncated": more,
                **({"NextContinuationToken": str(start + self.PAGE)} if more else {})}

    def lines(self, prefix: str) -> list[dict]:
        """Every line of the objects under prefix, in the order of their keys."""
        import json
        return [json.loads(line) for k in sorted(self.objects) if k.startswith(prefix) and k.endswith(".jsonl")
                for line in self.objects[k].decode().splitlines()]
