import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

import anthropic
import httpx
from pydantic import TypeAdapter

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from app.schemas import MovieRecommendations


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
        adapter = TypeAdapter(MovieRecommendations)
        return anthropic.transform_schema(adapter.json_schema())

    @staticmethod
    def _build_user_message(query: str, history: list | None = None) -> str:
        if not history:
            return f"Find me movies that match this description: {query}"

        parts = ["Conversation history:\n"]
        for i, turn in enumerate(history, 1):
            parts.append(f"[Query {i}]: \"{turn.query}\"")
            recs = ", ".join(
                f"{r.title} ({r.year})" if r.year else r.title
                for r in turn.recommendations
            )
            parts.append(f"[Recommended]: {recs}\n")

        parts.append(f"[Current request]: \"{query}\"")
        return "\n".join(parts)

    async def get_movie_recommendations(self, query: str, history: list | None = None) -> AsyncGenerator[dict, None]:
        """
        Stream movie recommendations using structured output.
        Yields individual movie dicts as they complete in the stream.
        """
        system_prompt = (
            "You are a movie recommendation assistant. Given a user's requirements, "
            "provide exactly 9 diverse movie recommendations that match their criteria.\n\n"
            "Make sure the results are diverse in terms of era, style, and popularity.\n"
            "If the user searched for a specific movie or a prompt that warrants less than 9 movie responses, "
            "smartly recommend the remaining movies that are the closest match with the movie the user asked for.\n"
            "You MUST give exactly 9 movies. No more. No less.\n"
            "The user may only require one movie, but you can recommend similar movies based on title, director, genre, etc.\n"
            "When the user provides follow-up requests, use the conversation history to understand context "
            "and avoid recommending movies that were already suggested."
        )

        user_message = self._build_user_message(query, history)

        try:
            with self.client.messages.stream(
                model=self.model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=4096,
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

                for text in stream.text_stream:
                    for ch in text:
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
                                yield json.loads(buffer)
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
