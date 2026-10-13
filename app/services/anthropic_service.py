import logging
from functools import cache
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

import anthropic
import httpx
from pydantic import TypeAdapter

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from app.models import ContentTypeMode
from app.schemas import ContentRecommendation, ContentRecommendations
from app.prompts import get_recommendation_system_prompt, get_recommendation_user_message
from app.services.llm_stream import ArrayItemParser


class AnthropicService:
    def __init__(self):
        # Async client: iterating a sync stream inside an async generator blocks the
        # event loop, stalling every other request until this one finishes.
        self.client = anthropic.AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY,
            http_client=httpx.AsyncClient(http2=True)
        )
        self.model = ANTHROPIC_MODEL
        self._schema = self._build_schema()

    @staticmethod
    def _build_schema() -> dict:
        adapter = TypeAdapter(ContentRecommendations)
        return anthropic.transform_schema(adapter.json_schema())

    async def get_recommendations(self, query: str, history: list | None = None, content_type: ContentTypeMode = ContentTypeMode.MOVIE, web_search: bool = False) -> AsyncGenerator[ContentRecommendation, None]:
        """
        Stream content recommendations using structured output.
        Yields individual recommendation dicts as they complete in the stream.
        Each dict has: title, year, reason, content_type.
        """
        system_prompt = get_recommendation_system_prompt(content_type)
        user_message = get_recommendation_user_message(query, content_type, history)

        tools = []
        if web_search:
            tools.append({
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 2,
            })

        try:
            async with self.client.messages.stream(
                model=self.model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=4096,
                **({"tools": tools} if tools else {}),
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": self._schema,
                    }
                },
            ) as stream:
                parser = ArrayItemParser(ContentRecommendation)

                async for event in stream:
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

                    for rec in parser.feed(event.delta.text):
                        yield rec
                    if parser.done:
                        return

        except Exception as e:
            logger.error("Error streaming from Anthropic API: %s", e)
            return

    async def close(self) -> None:
        await self.client.close()


@cache
def get_anthropic_service() -> AnthropicService:
    """One shared instance, so the HTTP connection pool is reused across requests."""
    return AnthropicService()
