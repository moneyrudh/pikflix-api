import asyncio
from types import SimpleNamespace
from typing import List

import pytest

from app.services.anthropic_service import AnthropicService, IncompleteRecommendationsError
from app.services.llm_stream import ParseStats

PICK = '{"title": "Heat", "year": 1995, "reason": "r", "content_type": "movie"}'


def text(chunk: str) -> SimpleNamespace:
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=chunk))


def stop(reason: str) -> SimpleNamespace:
    return SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=reason))


class FakeStream:
    def __init__(self, events: List[SimpleNamespace]) -> None:
        self._events = events

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            yield event


def service_streaming(events: List[SimpleNamespace]) -> AnthropicService:
    service = AnthropicService.__new__(AnthropicService)  # skip the real client
    service.model = "test"
    service._schema = {}
    service.client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs: FakeStream(events)))
    return service


async def collect(service: AnthropicService, **kwargs) -> List[str]:
    return [rec.title async for rec in service.get_recommendations("q", **kwargs)]


def test_complete_response_yields_every_pick():
    events = [text('{"items": [' + PICK + ", "), text(PICK.replace("Heat", "Ronin") + "]}"), stop("end_turn")]
    assert asyncio.run(collect(service_streaming(events))) == ["Heat", "Ronin"]


def test_response_cut_off_mid_list_raises_after_the_complete_picks():
    # Hits max_tokens halfway through the second pick: the first still counts, then it's an error, not success
    events = [text('{"items": [' + PICK + ', {"title": "Ron'), stop("max_tokens")]
    yielded: List[str] = []

    async def run() -> None:
        async for rec in service_streaming(events).get_recommendations("q"):
            yielded.append(rec.title)

    with pytest.raises(IncompleteRecommendationsError, match="max_tokens"):
        asyncio.run(run())
    assert yielded == ["Heat"]


def test_invalid_picks_are_counted_in_stats():
    bad = '{"title": "No year", "reason": "r", "content_type": "movie"}'
    events = [text('{"items": [' + PICK + ", " + bad + "]}"), stop("end_turn")]
    stats = ParseStats()
    assert asyncio.run(collect(service_streaming(events), stats=stats)) == ["Heat"]
    assert stats.skipped == 1
