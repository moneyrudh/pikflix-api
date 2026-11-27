import asyncio
import json
from typing import AsyncGenerator, List

import httpx
import pytest
from pydantic import ValidationError

from app.main import app
from datetime import datetime, timezone

from app.models import ContentType, ContentTypeMode, MediaType, UserQuery
from app.schemas import ContentRecommendation
from app.services.anthropic_service import get_anthropic_service
from app.services.openlibrary_service import get_openlibrary_service
from app.services.supabase_service import SupabaseService, get_supabase_service
from app.services.tmdb_service import get_tmdb_service
from tests.frontend import FRONTEND

# Per-title cache lookup delay (seconds). "Slow" is emitted first but finishes last.
DELAYS = {"Slow": 0.3, "Fast": 0.0, "Medium": 0.1, "Broken": 0.0}


class FakeAnthropic:
    fail_after: int | None = None  # raise after yielding this many picks, like a dropped connection
    invalid_picks = 0                # picks the parser would have rejected

    async def get_recommendations(self, *args, stats=None, **kwargs) -> AsyncGenerator[ContentRecommendation, None]:
        if stats is not None:
            stats.skipped += self.invalid_picks
        for i, title in enumerate(DELAYS):
            if i == self.fail_after:
                raise RuntimeError("overloaded")
            yield ContentRecommendation(title=title, year=2000, reason="r", content_type=ContentType.MOVIE)


class FakeSupabase:
    is_fresh_content = staticmethod(SupabaseService.is_fresh_content)

    async def get_content_by_title(self, title: str, year, content_type: ContentType) -> dict:
        await asyncio.sleep(DELAYS[title])
        if title == "Broken":
            raise RuntimeError("db down")
        now = datetime.now(timezone.utc).isoformat()
        return {"id": len(title), "title": title, "last_updated": now, "videos_checked_at": now}


async def post_stream(body: dict) -> List[dict]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=FRONTEND) as client:
        response = await client.post("/api/recommendations/", json=body)
    return [json.loads(line) for line in response.text.splitlines()]


def stream_lines(body: dict, fail_after: int | None = None, invalid_picks: int = 0) -> List[dict]:
    anthropic = FakeAnthropic()
    anthropic.fail_after = fail_after
    anthropic.invalid_picks = invalid_picks
    app.dependency_overrides = {
        get_anthropic_service: lambda: anthropic,
        get_supabase_service: FakeSupabase,
        get_tmdb_service: object,
        get_openlibrary_service: object,
    }
    try:
        return asyncio.run(post_stream(body))
    finally:
        app.dependency_overrides = {}


def by_type(events: List[dict], event_type: str) -> List[dict]:
    return [e for e in events if e["type"] == event_type]


def test_picks_stream_in_completion_order_and_failures_are_isolated():
    events = stream_lines({"query": "anything", "content_type": "movie"})

    assert events[0] == {"type": "init", "query": "anything", "media_types": ["movie"], "content_type": "movie"}
    content = by_type(events, "content")
    assert all(e["content_type"] == "movie" for e in content)
    # "Broken" is dropped without affecting the others; "Slow" no longer blocks the faster picks.
    assert [e["data"]["title"] for e in content] == ["Fast", "Medium", "Slow"]
    # Claude's reason is sent on the event, not mixed into the title's data
    assert all(e["reason"] == "r" and "reason" not in e["data"] for e in content)


def test_each_result_is_sent_as_item_and_legacy_content_then_done():
    events = stream_lines({"query": "anything", "media_types": ["movie"]})

    items = by_type(events, "item")
    assert [e["data"] for e in items] == [e["data"] for e in by_type(events, "content")]
    assert all(e["media_type"] == "movie" and e["reason"] == "r" for e in items)
    assert events[-1] == {"type": "done", "count": 3, "dropped": 1}


def test_picks_the_parser_rejected_count_as_dropped():
    events = stream_lines({"query": "anything"}, invalid_picks=2)
    assert events[-1] == {"type": "done", "count": 3, "dropped": 3}  # 1 failed lookup + 2 invalid picks


def test_llm_failure_sends_error_after_picks_already_in_flight():
    # Claude yields Slow and Fast, then the stream dies. Both picks still resolve and get sent.
    events = stream_lines({"query": "anything"}, fail_after=2)

    assert [e["data"]["title"] for e in by_type(events, "item")] == ["Fast", "Slow"]
    assert events[-1]["type"] == "error"
    assert not by_type(events, "done")
    assert "overloaded" not in events[-1]["message"]  # internal details stay in the logs


@pytest.mark.parametrize("legacy, expected", [
    (None, [MediaType.MOVIE]),
    (ContentTypeMode.SHOW, [MediaType.SHOW]),
    (ContentTypeMode.BOTH, [MediaType.MOVIE, MediaType.SHOW]),
])
def test_legacy_content_type_maps_to_media_types(legacy, expected):
    query = UserQuery(query="q", content_type=legacy)
    assert query.media_types == expected
    assert query.watch_mode == (legacy or ContentTypeMode.MOVIE)


def test_media_types_win_over_legacy_field_and_are_deduped():
    query = UserQuery(query="q", media_types=[MediaType.SHOW, MediaType.MOVIE, MediaType.SHOW], content_type=ContentTypeMode.MOVIE)
    assert query.media_types == [MediaType.SHOW, MediaType.MOVIE]
    assert query.watch_mode == ContentTypeMode.BOTH


@pytest.mark.parametrize("media_types, message", [
    ([], "must not be empty"),  # sent but empty: not silently treated as legacy "movie"
    ([MediaType.MOVIE, MediaType.BOOK], "same shelf"),
    ([MediaType.BOOK, MediaType.PAPER], "books and papers can.t be searched together"),  # failsafe: different pipelines
])
def test_invalid_media_types_are_rejected(media_types, message):
    with pytest.raises(ValidationError, match=message):
        UserQuery(query="q", media_types=media_types)


def raw_post(body: dict, headers: dict) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            return await client.post("/api/recommendations/", json=body)
    return asyncio.run(run())


@pytest.mark.parametrize("headers", [{}, {"X-Pikflix-Proxy-Secret": "wrong"}])
def test_requests_not_from_the_frontend_are_refused(headers, usage_limits):
    response = raw_post({"query": "q"}, headers)
    assert response.status_code == 403 and usage_limits == []


def test_searches_are_counted_per_visitor_without_storing_the_ip(usage_limits):
    from app.config import SEARCHES_PER_DAY, SEARCHES_PER_MINUTE
    stream_lines({"query": "q"})
    [(bucket, key, per_minute, per_day)] = usage_limits
    assert (bucket, per_minute, per_day) == ("search", SEARCHES_PER_MINUTE, SEARCHES_PER_DAY)
    assert "203.0.113.7" not in key and len(key) == 64  # a keyed hash of the IP


def test_an_over_limit_search_is_refused_before_claude_runs(usage_limits):
    usage_limits.refuse = 30
    anthropic = FakeAnthropic()
    anthropic.get_recommendations = None  # would raise if the endpoint got as far as calling Claude
    app.dependency_overrides = {get_anthropic_service: lambda: anthropic, get_supabase_service: FakeSupabase,
                                get_tmdb_service: object, get_openlibrary_service: object}
    try:
        response = raw_post({"query": "q"}, FRONTEND)
    finally:
        app.dependency_overrides = {}
    assert response.status_code == 429 and response.headers["retry-after"] == "30"
    assert response.json()["detail"] == "You're going a bit fast. Try again in 30 seconds."
