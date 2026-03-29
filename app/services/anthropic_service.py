import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

import anthropic
import httpx
from pydantic import TypeAdapter

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from app.models import ContentTypeMode
from app.schemas import ContentRecommendation, ContentRecommendations
from app.prompts import get_recommendation_system_prompt, get_recommendation_user_message


class AnthropicService:
    def __init__(self):
        http_client = httpx.Client(http2=True)
        self.client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            http_client=http_client
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
            with self.client.messages.stream(
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
                buffer = ""
                depth = 0
                in_string = False

                for event in stream:
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

                    for ch in event.delta.text:
                        if in_string:
                            buffer += ch
                            if ch == '"':
                                in_string = False
                            continue

                        if ch == '"' and depth >= 2:
                            in_string = True
                            buffer += ch
                            continue

                        if ch == '{':
                            depth += 1
                            if depth == 2:
                                buffer = '{'
                            continue

                        if ch == '}' and depth == 2:
                            buffer += '}'
                            try:
                                yield ContentRecommendation.model_validate(json.loads(buffer))
                            except json.JSONDecodeError:
                                logger.warning("Failed to parse: %s", buffer)
                            buffer = ""
                            depth -= 1
                            continue

                        if ch == ']':
                            return

                        if depth >= 2:
                            buffer += ch

        except Exception as e:
            logger.error("Error streaming from Anthropic API: %s", e)
            return
