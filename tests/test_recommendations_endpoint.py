import asyncio
import json
from typing import AsyncGenerator, List

import httpx

from app.main import app
from app.models import CacheResult, ContentType
from app.schemas import ContentRecommendation
from app.services.anthropic_service import get_anthropic_service
from app.services.supabase_service import get_supabase_service
from app.services.tmdb_service import get_tmdb_service

# Per-title cache lookup delay (seconds). "Slow" is emitted first but finishes last.
DELAYS = {"Slow": 0.3, "Fast": 0.0, "Medium": 0.1, "Broken": 0.0}


class FakeAnthropic:
    async def get_recommendations(self, *args, **kwargs) -> AsyncGenerator[ContentRecommendation, None]:
        for title in DELAYS:
            yield ContentRecommendation(title=title, year=2000, reason="r", content_type=ContentType.MOVIE)


class FakeSupabase:
    async def get_content_by_titles(self, recs: List[ContentRecommendation], content_type: ContentType) -> CacheResult:
        title = recs[0].title
        await asyncio.sleep(DELAYS[title])
        if title == "Broken":
            raise RuntimeError("db down")
        return CacheResult(found=[{"id": len(title), "title": title}])


async def post_stream(body: dict) -> List[dict]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/recommendations/", json=body)
    return [json.loads(line) for line in response.text.splitlines()]


def stream_lines(body: dict) -> List[dict]:
    app.dependency_overrides = {
        get_anthropic_service: FakeAnthropic,
        get_supabase_service: FakeSupabase,
        get_tmdb_service: object,
    }
    try:
        return asyncio.run(post_stream(body))
    finally:
        app.dependency_overrides = {}


def test_picks_stream_in_completion_order_and_failures_are_isolated():
    events = stream_lines({"query": "anything", "content_type": "movie"})

    assert events[0]["type"] == "init"
    content = events[1:]
    assert all(e["type"] == "content" and e["content_type"] == "movie" for e in content)
    # "Broken" is dropped without affecting the others; "Slow" no longer blocks the faster picks.
    assert [e["data"]["title"] for e in content] == ["Fast", "Medium", "Slow"]
