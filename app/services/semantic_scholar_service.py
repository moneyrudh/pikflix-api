import asyncio
import logging
from functools import cache
from typing import Dict, Iterable, List, Optional

import httpx

from app.config import (
    PAPER_CANDIDATES_MAX, PAPER_RESULTS_PER_QUERY,
    SEMANTIC_SCHOLAR_API_KEY, SEMANTIC_SCHOLAR_BASE_URL, SEMANTIC_SCHOLAR_REQUESTS_PER_SECOND,
)
from app.models import S2Paper
from app.services.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

SEARCH_FIELDS = ",".join(S2Paper.model_fields)
ABSTRACT_EXCERPT = 300  # characters of each abstract shown to Claude when ranking
# Exponential backoff on 429 (throttled) and 5xx (overloaded): waits 1s, then 2s, or the server's
# Retry-After if it sends one, capped so a single search can't stall a request.
MAX_ATTEMPTS = 3
BACKOFF_BASE = 1.0
BACKOFF_MAX = 8.0
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def retry_delay(attempt: int, retry_after: Optional[str]) -> float:
    """Seconds to wait before retry number `attempt + 1`."""
    try:
        server_delay = float(retry_after) if retry_after else None
    except ValueError:  # Retry-After can also be an HTTP date; fall back to our own schedule
        server_delay = None
    delay = server_delay if server_delay is not None else BACKOFF_BASE * 2 ** attempt
    return min(max(delay, 0.0), BACKOFF_MAX)


class SemanticScholarError(Exception):
    """Semantic Scholar didn't answer usefully: timeout, connection failure, or an error status."""


class SemanticScholarService:
    def __init__(self) -> None:
        headers = {"User-Agent": "PikFlix/1.0 (https://pikflix.app)"}
        if SEMANTIC_SCHOLAR_API_KEY:
            headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
        self.client = httpx.AsyncClient(timeout=15.0, headers=headers)
        self.limiter = RateLimiter(SEMANTIC_SCHOLAR_REQUESTS_PER_SECOND)
        self.sleep = asyncio.sleep  # replaceable in tests

    async def close(self) -> None:
        await self.client.aclose()

    async def search(self, query: str, limit: int = PAPER_RESULTS_PER_QUERY) -> List[S2Paper]:
        """Relevance-ranked papers for a keyword query, with exponential backoff on 429/5xx.
        Raises SemanticScholarError when it gives up."""
        params: Dict[str, str | int] = {"query": query, "limit": limit, "fields": SEARCH_FIELDS}
        for attempt in range(MAX_ATTEMPTS):
            await self.limiter.wait()
            try:
                response = await self.client.get(f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/search", params=params)
            except httpx.HTTPError as e:
                raise SemanticScholarError(f"search '{query}': {e.__class__.__name__}") from e
            if response.status_code == 200:
                return [S2Paper.model_validate(paper) for paper in response.json().get("data") or []]
            if response.status_code not in RETRYABLE_STATUSES or attempt == MAX_ATTEMPTS - 1:
                raise SemanticScholarError(f"search '{query}': HTTP {response.status_code}")
            await self.sleep(retry_delay(attempt, response.headers.get("Retry-After")))
        raise AssertionError("unreachable")

    async def get_paper(self, paper_id: str) -> Optional[S2Paper]:
        """One paper by its paperId, or None if Semantic Scholar doesn't know it. Raises SemanticScholarError."""
        await self.limiter.wait()
        try:
            response = await self.client.get(f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/{paper_id}", params={"fields": SEARCH_FIELDS})
        except httpx.HTTPError as e:
            raise SemanticScholarError(f"paper {paper_id}: {e.__class__.__name__}") from e
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise SemanticScholarError(f"paper {paper_id}: HTTP {response.status_code}")
        return S2Paper.model_validate(response.json())


def merge_candidates(result_lists: List[List[S2Paper]], cap: int = PAPER_CANDIDATES_MAX) -> List[S2Paper]:
    """Interleave each query's results (so every angle is represented), drop duplicates by DOI,
    then arXiv id, then paperId, and keep the first `cap`."""
    merged: List[S2Paper] = []
    seen: set[str] = set()
    for rank in range(max((len(results) for results in result_lists), default=0)):
        for results in result_lists:
            if rank >= len(results):
                continue
            paper = results[rank]
            keys = {f"id:{paper.paperId}"}
            if paper.doi:
                keys.add(f"doi:{paper.doi.lower()}")
            if paper.arxiv_id:
                keys.add(f"arxiv:{paper.arxiv_id.lower()}")
            if keys & seen:
                continue
            seen |= keys
            merged.append(paper)
            if len(merged) == cap:
                return merged
    return merged


def _clean(text: Optional[str]) -> str:
    """One line, no column separators, so each candidate stays a single parseable row."""
    return " ".join((text or "").replace("|", "/").split())


def format_candidates(papers: Iterable[S2Paper]) -> str:
    """Compact list for Claude to rank: id | title | year | venue | citations | abstract excerpt."""
    return "\n".join(
        f"{p.paperId} | {_clean(p.title)} | {p.year or '?'} | {_clean(p.venue) or '?'} | "
        f"{p.citationCount if p.citationCount is not None else '?'} | {_clean(p.abstract)[:ABSTRACT_EXCERPT]}"
        for p in papers
    )


async def find_candidates(queries: List[str], s2: SemanticScholarService) -> Dict[str, S2Paper]:
    """Search every query (paced by the rate limiter), merge the results, and key them by paperId.
    A query that fails is skipped; if every query fails, raises so the stream ends with `error`."""
    results = await asyncio.gather(*(s2.search(query) for query in queries), return_exceptions=True)
    found: List[List[S2Paper]] = []
    for query, result in zip(queries, results):
        if isinstance(result, SemanticScholarError):
            logger.warning("Semantic Scholar search failed: %s", result)
        elif isinstance(result, BaseException):
            raise result
        else:
            found.append(result)
    if not found:
        raise SemanticScholarError("every search failed")
    return {paper.paperId: paper for paper in merge_candidates(found)}


@cache
def get_semantic_scholar_service() -> SemanticScholarService:
    """One shared instance, so the rate limit and connection pool cover every request."""
    return SemanticScholarService()
