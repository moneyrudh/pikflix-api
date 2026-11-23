import logging
from functools import cache
from typing import AsyncGenerator, List, Type, TypeVar

logger = logging.getLogger(__name__)

import anthropic
import httpx
from pydantic import BaseModel, TypeAdapter

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, PAPER_QUERIES_MAX
from app.models import ContentTypeMode
from app.schemas import BookPick, BookPicks, ContentRecommendation, ContentRecommendations, PaperPick, PaperPicks, PaperQueries
from app.prompts import (
    get_book_system_prompt, get_book_user_message,
    get_paper_queries_system_prompt, get_paper_queries_user_message,
    get_paper_rank_system_prompt, get_paper_rank_user_message,
    get_recommendation_system_prompt, get_recommendation_user_message,
)
from app.services.llm_stream import ArrayItemParser, ParseStats


class IncompleteRecommendationsError(RuntimeError):
    """Claude's response ended before the `items` array closed (token limit, refusal, dropped stream)."""


T = TypeVar("T", bound=BaseModel)


class AnthropicService:
    def __init__(self):
        # Async client: iterating a sync stream inside an async generator blocks the
        # event loop, stalling every other request until this one finishes.
        self.client = anthropic.AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY,
            http_client=httpx.AsyncClient(http2=True)
        )
        self.model = ANTHROPIC_MODEL
        self._schemas = {wrapper: self.build_schema(wrapper)
                         for wrapper in (ContentRecommendations, BookPicks, PaperQueries, PaperPicks)}

    @staticmethod
    def build_schema(wrapper: Type[BaseModel]) -> dict:
        """The structured-output schema sent to Claude for a `{"items": [...]}` wrapper model."""
        return anthropic.transform_schema(TypeAdapter(wrapper).json_schema())

    def get_recommendations(self, query: str, history: list | None = None, content_type: ContentTypeMode = ContentTypeMode.MOVIE, web_search: bool = False, stats: ParseStats | None = None) -> AsyncGenerator[ContentRecommendation, None]:
        """Stream movie/show picks (title, year, reason, content_type) as each one completes."""
        return self._stream_items(
            get_recommendation_system_prompt(content_type),
            get_recommendation_user_message(query, content_type, history),
            ContentRecommendation, ContentRecommendations, web_search, stats,
        )

    def get_book_picks(self, query: str, history: list | None = None, web_search: bool = False, stats: ParseStats | None = None) -> AsyncGenerator[BookPick, None]:
        """Stream book picks (title, author, year, reason) as each one completes."""
        return self._stream_items(
            get_book_system_prompt(), get_book_user_message(query, history),
            BookPick, BookPicks, web_search, stats,
        )

    async def plan_paper_queries(self, query: str, history: list | None = None) -> List[str]:
        """Step 1 of a paper search: keyword queries for Semantic Scholar (short, so not streamed)."""
        response = await self.client.messages.create(
            model=self.model,
            system=get_paper_queries_system_prompt(),
            messages=[{"role": "user", "content": get_paper_queries_user_message(query, history)}],
            max_tokens=512,
            output_config={"format": {"type": "json_schema", "schema": self._schemas[PaperQueries]}},
        )
        text = next((block.text for block in response.content if block.type == "text"), "")
        queries = [q.strip() for q in PaperQueries.model_validate_json(text).queries if q.strip()]
        return list(dict.fromkeys(queries))[:PAPER_QUERIES_MAX]

    def get_paper_picks(self, query: str, candidates: str, history: list | None = None, stats: ParseStats | None = None) -> AsyncGenerator[PaperPick, None]:
        """Step 3 of a paper search: stream the chosen candidates (paper_id, reason), best first."""
        return self._stream_items(
            get_paper_rank_system_prompt(), get_paper_rank_user_message(query, candidates, history),
            PaperPick, PaperPicks, False, stats,
        )

    async def _stream_items(self, system_prompt: str, user_message: str, item: Type[T], wrapper: Type[BaseModel], web_search: bool, stats: ParseStats | None) -> AsyncGenerator[T, None]:
        """
        Stream structured output shaped like `wrapper` ({"items": [...]}) and yield each element as
        an `item` the moment it completes.
        API and network errors propagate, and a response that ends before the list closes raises
        IncompleteRecommendationsError, so the caller can tell the client the search failed.
        Pass `stats` to see how many elements were skipped as invalid.
        """
        tools = []
        if web_search:
            tools.append({
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 2,
            })

        async with self.client.messages.stream(
            model=self.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=4096,
            **({"tools": tools} if tools else {}),
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": self._schemas[wrapper],
                }
            },
        ) as stream:
            parser = ArrayItemParser(item, stats=stats)
            stop_reason = None

            async for event in stream:
                if event.type == "message_delta":
                    stop_reason = event.delta.stop_reason
                    continue
                if event.type == "content_block_start":
                    if event.content_block.type == "server_tool_use":
                        logger.info("Web search triggered: %s", event.content_block.name)
                    elif event.content_block.type == "web_search_tool_result":
                        try:
                            for result in event.content_block.content:
                                if result.type == "web_search_result":
                                    logger.info("Search result: %s — %s", result.title, result.url)
                        except (AttributeError, TypeError):
                            pass
                    continue

                if event.type != "content_block_delta":
                    continue
                if event.delta.type != "text_delta":
                    continue

                for element in parser.feed(event.delta.text):
                    yield element
                if parser.done:
                    return

            raise IncompleteRecommendationsError(f"response ended before the list closed (stop_reason={stop_reason})")

    async def close(self) -> None:
        await self.client.close()


@cache
def get_anthropic_service() -> AnthropicService:
    """One shared instance, so the HTTP connection pool is reused across requests."""
    return AnthropicService()
