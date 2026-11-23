import asyncio
import json
from types import SimpleNamespace
from typing import AsyncGenerator, List, Optional

import httpx
import pytest

import app.services.semantic_scholar_service as s2_module
from app.main import app
from app.models import S2Paper
from app.schemas import PaperPick, PaperPicks, PaperQueries
from app.services.anthropic_service import AnthropicService, get_anthropic_service
from app.services.openlibrary_service import get_openlibrary_service
from app.services.rate_limit import RateLimiter
from app.services.semantic_scholar_service import (
    SemanticScholarError, SemanticScholarService, find_candidates, format_candidates,
    get_semantic_scholar_service, merge_candidates,
)
from app.services.supabase_service import get_supabase_service
from app.services.tmdb_service import get_tmdb_service


def paper(pid: str, title: str = "A Paper", doi: Optional[str] = None, arxiv: Optional[str] = None, **extra) -> S2Paper:
    external = {k: v for k, v in (("DOI", doi), ("ArXiv", arxiv)) if v}
    return S2Paper(paperId=pid, title=title, externalIds=external or None, **extra)


def service_with(handler, delays: Optional[List[float]] = None) -> SemanticScholarService:
    service = SemanticScholarService()
    service.limiter = RateLimiter(per_second=1000)  # tests shouldn't wait

    async def record(seconds: float) -> None:  # backoff waits are recorded, not slept
        if delays is not None:
            delays.append(seconds)

    service.sleep = record
    service.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=service.client.headers)
    return service


def run_search(handler, delays: Optional[List[float]] = None) -> tuple[List[S2Paper] | None, Exception | None]:
    async def run():
        service = service_with(handler, delays)
        try:
            return await service.search("self-attention"), None
        except SemanticScholarError as e:
            return None, e
        finally:
            await service.close()

    return asyncio.run(run())


# --- Semantic Scholar client

def test_search_requests_the_model_fields_and_parses_results():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [{"paperId": "p1", "title": "Attention Is All You Need", "year": 2017,
                                                   "tldr": {"text": "Transformers."}, "externalIds": {"ArXiv": "1706.03762"}}]})

    results, error = run_search(handler)
    assert error is None and results[0].arxiv_id == "1706.03762"
    assert seen["params"]["query"] == "self-attention"
    assert set(seen["params"]["fields"].split(",")) == set(S2Paper.model_fields)


def test_api_key_is_sent_when_configured(monkeypatch):
    monkeypatch.setattr(s2_module, "SEMANTIC_SCHOLAR_API_KEY", "secret")
    assert SemanticScholarService().client.headers["x-api-key"] == "secret"
    monkeypatch.setattr(s2_module, "SEMANTIC_SCHOLAR_API_KEY", None)
    assert "x-api-key" not in SemanticScholarService().client.headers


def test_throttled_requests_back_off_exponentially_then_succeed():
    calls, delays = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else httpx.Response(200, json={"data": []})

    assert run_search(handler, delays) == ([], None)
    assert len(calls) == 3 and delays == [1.0, 2.0]


def test_backoff_follows_retry_after_but_is_capped():
    delays = []
    run_search(lambda request: httpx.Response(503, headers={"Retry-After": "3"}), delays)
    assert delays == [3.0, 3.0]
    assert s2_module.retry_delay(0, "120") == s2_module.BACKOFF_MAX
    assert s2_module.retry_delay(1, "Wed, 21 Oct 2026 07:28:00 GMT") == 2.0  # HTTP-date form: our own schedule


@pytest.mark.parametrize("respond, attempts", [
    (lambda request: httpx.Response(429), 3),   # still throttled after every retry
    (lambda request: httpx.Response(500), 3),   # overloaded
    (lambda request: httpx.Response(404), 1),   # not retryable
    (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)), 1),
])
def test_failures_raise_a_semantic_scholar_error(respond, attempts):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return respond(request)

    results, error = run_search(handler, [])
    assert results is None and isinstance(error, SemanticScholarError)
    assert len(calls) == attempts


# --- Candidates

def test_merge_interleaves_queries_and_drops_duplicates():
    first = [paper("a", doi="10.1/X"), paper("b"), paper("c", arxiv="1706.03762")]
    second = [paper("a2", doi="10.1/x"), paper("d"), paper("e", arxiv="1706.03762"), paper("b")]
    merged = merge_candidates([first, second])
    # Same DOI (any case), same arXiv id, or same paperId counts as the same paper
    assert [p.paperId for p in merged] == ["a", "b", "d", "c"]


def test_merge_respects_the_cap():
    assert len(merge_candidates([[paper(str(i)) for i in range(30)], [paper(f"x{i}") for i in range(30)]], cap=40)) == 40


def test_candidates_are_formatted_one_per_line():
    text = format_candidates([paper("p1", title="Pipes | and\nnewlines", year=2017, venue="NeurIPS", citationCount=100,
                                    abstract="word " * 200)])
    assert "\n" not in text
    pid, title, year, venue, cites, abstract = text.split(" | ")
    assert (pid, title, year, venue, cites) == ("p1", "Pipes / and newlines", "2017", "NeurIPS", "100")
    assert len(abstract) <= 300


def test_one_failed_query_is_skipped_but_all_failing_is_an_error():
    class FakeS2:
        def __init__(self, failing: set[str]) -> None:
            self.failing = failing

        async def search(self, query: str) -> List[S2Paper]:
            if query in self.failing:
                raise SemanticScholarError(f"{query}: HTTP 429")
            return [paper(f"{query}-1")]

    found = asyncio.run(find_candidates(["a", "b"], FakeS2({"a"})))
    assert list(found) == ["b-1"]
    with pytest.raises(SemanticScholarError):
        asyncio.run(find_candidates(["a", "b"], FakeS2({"a", "b"})))


def test_to_paper_maps_semantic_scholar_fields():
    raw = S2Paper(paperId="p1", title="Attention Is All You Need", year=2017, venue="",
                  authors=[{"authorId": "1", "name": "Ashish Vaswani"}], citationCount=100000,
                  externalIds={"DOI": "10.5555/x", "ArXiv": "1706.03762"},
                  openAccessPdf={"url": "https://arxiv.org/pdf/1706.03762"}, tldr={"text": "Transformers."},
                  fieldsOfStudy=None)
    p = raw.to_paper()
    assert (p.id, p.doi, p.arxiv_id, p.tldr, p.open_access_pdf) == ("p1", "10.5555/x", "1706.03762", "Transformers.", "https://arxiv.org/pdf/1706.03762")
    assert p.venue is None and p.fields_of_study == [] and p.authors[0].name == "Ashish Vaswani"


# --- Plan step

def test_plan_parses_dedupes_and_caps_queries():
    service = AnthropicService.__new__(AnthropicService)
    service.model = "test"
    service._schemas = {PaperQueries: {}, PaperPicks: {}}
    reply = json.dumps({"queries": ["self-attention", " self-attention ", "in-context learning", "scaling laws", "fourth"]})

    async def create(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)])

    service.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    assert asyncio.run(service.plan_paper_queries("q")) == ["self-attention", "in-context learning", "scaling laws"]


# --- Endpoint

CANDIDATES = [paper(f"p{i}", title=f"Paper {i}", year=2000 + i) for i in range(1, 12)]


class FakeAnthropic:
    def __init__(self, picks: List[str]) -> None:
        self.picks = picks
        self.candidate_text = ""

    async def plan_paper_queries(self, query: str, history=None) -> List[str]:
        return ["q1", "q2"]

    async def get_paper_picks(self, query: str, candidates: str, history=None, stats=None) -> AsyncGenerator[PaperPick, None]:
        self.candidate_text = candidates
        for pid in self.picks:
            yield PaperPick(paper_id=pid, reason=f"why {pid}")


class FakePaperCache:
    def __init__(self) -> None:
        self.saved: List[str] = []

    async def save_papers(self, papers) -> None:
        self.saved += [p.id for p in papers]


class FakeS2:
    async def search(self, query: str) -> List[S2Paper]:
        return CANDIDATES[:6] if query == "q1" else CANDIDATES[4:]


def run_paper_search(picks: List[str], cache: Optional[FakePaperCache] = None) -> tuple[List[dict], FakeAnthropic]:
    anthropic = FakeAnthropic(picks)
    cache = cache or FakePaperCache()
    app.dependency_overrides = {
        get_anthropic_service: lambda: anthropic,
        get_semantic_scholar_service: FakeS2,
        get_supabase_service: lambda: cache, get_tmdb_service: object, get_openlibrary_service: object,
    }

    async def post() -> List[dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/recommendations/", json={"query": "q", "media_types": ["paper"]})
        return [json.loads(line) for line in response.text.splitlines()]

    try:
        return asyncio.run(post()), anthropic
    finally:
        app.dependency_overrides = {}


def test_papers_come_only_from_search_results():
    events, anthropic = run_paper_search(["p3", "made-up-id", "p7", "p3", "p11"])
    items = [e for e in events if e["type"] == "item"]
    assert [e["data"]["id"] for e in items] == ["p3", "p7", "p11"]   # invented and repeated ids never shown
    assert all(e["media_type"] == "paper" and e["reason"] == f"why {e['data']['id']}" for e in items)
    assert not [e for e in events if e["type"] == "content"]
    assert events[-1] == {"type": "done", "count": 3, "dropped": 2}
    # Claude ranked the merged, de-duplicated candidates of both queries
    assert len(anthropic.candidate_text.splitlines()) == 11


def test_semantic_scholar_outage_says_so_instead_of_blaming_claude():
    class DownS2:
        async def search(self, query: str) -> List[S2Paper]:
            raise SemanticScholarError(f"search '{query}': HTTP 429")

    anthropic = FakeAnthropic(["p1"])
    app.dependency_overrides = {
        get_anthropic_service: lambda: anthropic, get_semantic_scholar_service: DownS2,
        get_supabase_service: FakePaperCache, get_tmdb_service: object, get_openlibrary_service: object,
    }

    async def post() -> List[dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/recommendations/", json={"query": "q", "media_types": ["paper"]})
        return [json.loads(line) for line in response.text.splitlines()]

    try:
        events = asyncio.run(post())
    finally:
        app.dependency_overrides = {}
    assert events[-1] == {"type": "error", "message": "Paper search is unavailable right now. Please try again in a minute."}


def test_every_candidate_is_cached_for_the_agent():
    cache = FakePaperCache()
    run_paper_search(["p1"], cache)
    assert sorted(cache.saved) == sorted(p.paperId for p in CANDIDATES)


def test_paper_search_stops_at_the_display_limit():
    events, _ = run_paper_search([f"p{i}" for i in range(1, 12)])
    assert events[-1] == {"type": "done", "count": 9, "dropped": 0}
