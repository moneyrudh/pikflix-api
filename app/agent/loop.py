"""
One agent turn: Claude (streamed, with tools and the structured block reply) until it answers.

Each round streams one Claude response. Tool calls announce a status as soon as each is fully written,
run concurrently (at most MAX_TOOL_CALLS per turn), and their results feed the next round. The final
reply's JSON flows through ReplyStreamer; completed blocks are validated against the block registry
and hydrated in background tasks, so later text keeps streaming while a card loads.

`new_messages` collects what this turn added to the conversation, in complete units only (an
assistant tool-use message together with its tool results, or the final answer), so a turn cut off
mid-way never leaves a tool call without its result in the stored history.
"""
import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

import anthropic
from pydantic import BaseModel

from app.agent.blocks import BlockContext, BlockDrop, TextBlock, parse_block, reply_schema
from app.agent.events import (
    BlockDropEvent, BlockEndEvent, BlockStartEvent, StatusEvent, TextDeltaEvent, TurnDoneEvent, TurnErrorEvent,
)
from app.agent.streamer import ReplyStreamer, StreamUpdate
from app.agent.tools import ToolContext, run_tool, tool_definitions, tool_status
from app.prompts import AGENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_ROUNDS = 4        # up to 3 rounds of tools, then the answer
MAX_TOOL_CALLS = 8    # per turn
MAX_TOKENS = 4096
TURN_ERROR_MESSAGE = "Something went wrong while answering. Please try again."


class AgentTurnError(Exception):
    pass


def _content(message: Any) -> List[Dict[str, Any]]:
    """A response's content blocks as plain dicts, valid to store and to send back to the API."""
    return [block.model_dump(mode="json", exclude_none=True) for block in message.content]


class AgentTurn:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str, tools: ToolContext, blocks: BlockContext) -> None:
        self._client = client
        self._model = model
        self._tools = tools
        self._blocks = blocks
        self._schema = reply_schema()
        self.new_messages: List[Dict[str, Any]] = []

    async def run(self, messages: List[Dict[str, Any]]) -> AsyncGenerator[BaseModel, None]:
        """Stream this turn's events. `messages` is the conversation so far, ending with the user's message."""
        queue: asyncio.Queue[Optional[BaseModel]] = asyncio.Queue()

        async def produce() -> None:
            try:
                await self._turn(list(messages), queue)
            except Exception:
                logger.exception("Agent turn failed")
                await queue.put(TurnErrorEvent(message=TURN_ERROR_MESSAGE))
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            producer.cancel()  # client gone: stop Claude and any tools still running

    def _request(self, messages: List[Dict[str, Any]], tools_allowed: bool) -> Dict[str, Any]:
        definitions = tool_definitions()
        definitions[-1] = {**definitions[-1], "cache_control": {"type": "ephemeral"}}  # tools + system are cached
        request: Dict[str, Any] = {
            "model": self._model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": AGENT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "tools": definitions,
            "output_config": {"format": {"type": "json_schema", "schema": self._schema}},
        }
        if not tools_allowed:
            request["tool_choice"] = {"type": "none"}  # out of rounds or tool calls: answer now
        return request

    async def _turn(self, messages: List[Dict[str, Any]], queue: "asyncio.Queue[Optional[BaseModel]]") -> None:
        streamer = ReplyStreamer()
        hydrations: List[asyncio.Task[None]] = []
        tool_calls = 0
        for round_ in range(MAX_ROUNDS):
            tools_allowed = round_ < MAX_ROUNDS - 1 and tool_calls < MAX_TOOL_CALLS
            async with self._client.messages.stream(**self._request(messages, tools_allowed)) as stream:
                async for event in stream:
                    if event.type == "content_block_stop" and event.content_block.type == "tool_use":
                        block = event.content_block
                        await queue.put(StatusEvent(text=tool_status(block.name, block.input, self._tools)))
                    elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                        for update in streamer.feed(event.delta.text):
                            await self._on_update(update, queue, hydrations)
                final = await stream.get_final_message()

            assistant = {"role": "assistant", "content": _content(final)}
            if final.stop_reason == "tool_use":
                results, tool_calls = await self._run_tools(final, tool_calls)
                user = {"role": "user", "content": results}
                messages += [assistant, user]
                self.new_messages += [assistant, user]
                continue
            if final.stop_reason not in ("end_turn", "stop_sequence"):
                raise AgentTurnError(f"stopped with {final.stop_reason}")

            self.new_messages.append(assistant)
            for update in streamer.close():
                await self._on_update(update, queue, hydrations)
            await asyncio.gather(*hydrations)
            await queue.put(TurnDoneEvent())
            return
        raise AgentTurnError("no answer after the last round")

    async def _run_tools(self, final: Any, used: int) -> "tuple[List[Dict[str, Any]], int]":
        calls = [block for block in final.content if block.type == "tool_use"]
        allowed = calls[:max(MAX_TOOL_CALLS - used, 0)]
        outputs = await asyncio.gather(*(run_tool(c.name, c.input, self._tools) for c in allowed))
        results = [{"type": "tool_result", "tool_use_id": c.id, "content": out.text, "is_error": out.is_error}
                   for c, out in zip(allowed, outputs)]
        # Every tool_use needs a result; calls over budget get one that says so
        results += [{"type": "tool_result", "tool_use_id": c.id, "is_error": True,
                     "content": "Tool budget for this turn is used up; answer with what you have."}
                    for c in calls[len(allowed):]]
        return results, used + len(calls)

    async def _on_update(self, update: StreamUpdate, queue: "asyncio.Queue[Optional[BaseModel]]",
                         hydrations: List["asyncio.Task[None]"]) -> None:
        if update.kind == "start":
            await queue.put(BlockStartEvent(index=update.index, block_type=update.block_type or ""))
        elif update.kind == "text":
            await queue.put(TextDeltaEvent(index=update.index, text=update.text or ""))
        else:
            block = parse_block(update.block or {})
            if block is None:
                await queue.put(BlockDropEvent(index=update.index))
            elif isinstance(block, TextBlock):
                await queue.put(BlockEndEvent(index=update.index, block=await block.hydrate(self._blocks)))
            else:
                # Cards load in the background so the text after them keeps streaming
                hydrations.append(asyncio.create_task(self._finish(update.index, block, queue)))

    async def _finish(self, index: int, block: Any, queue: "asyncio.Queue[Optional[BaseModel]]") -> None:
        try:
            payload = await block.hydrate(self._blocks)
        except BlockDrop as e:
            logger.warning("Dropped block %d: %s", index, e)
            await queue.put(BlockDropEvent(index=index))
            return
        ref = payload.get("ref")
        if ref:
            self._tools.shown.setdefault(ref, self._tools.seen.get(ref, ref))
        await queue.put(BlockEndEvent(index=index, block=payload))
