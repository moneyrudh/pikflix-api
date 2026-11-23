"""Stored conversation -> what the next turn sends, what recall lists, and what a reopened chat renders."""
import asyncio
import json
import logging
from typing import Any, Dict, List

from app.agent.blocks import BlockContext, BlockDrop, block_refs, parse_block
from app.models import ChatMessage

logger = logging.getLogger(__name__)

HISTORY_EXCHANGES = 20  # user messages (and everything after each) sent to Claude per turn


def _user_text(message: ChatMessage) -> str:
    """The typed text of a user message ('' for messages that only carry tool results)."""
    return "".join(b.get("text", "") for b in message.content if b.get("type") == "text")


def _reply_blocks(message: ChatMessage) -> List[Dict[str, Any]]:
    """The raw blocks of a stored final answer (its text content is the structured reply JSON)."""
    for block in message.content:
        if block.get("type") == "text":
            try:
                items = json.loads(block["text"]).get("items")
            except (ValueError, AttributeError):
                return []
            return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return []


def window(messages: List[ChatMessage], exchanges: int = HISTORY_EXCHANGES) -> List[Dict[str, Any]]:
    """The API messages for the last `exchanges` user messages and everything after them. Cuts only at a
    typed user message, so a tool call is never separated from its result."""
    starts = [i for i, m in enumerate(messages) if m.role == "user" and _user_text(m)]
    begin = starts[-exchanges] if len(starts) >= exchanges else (starts[0] if starts else 0)
    return [{"role": m.role, "content": m.content} for m in messages[begin:]]


def shown_refs(messages: List[ChatMessage]) -> List[str]:
    """Refs of every card and trailer shown so far, in order, without repeats (for recall)."""
    refs: Dict[str, None] = {}
    for message in messages:
        if message.role == "assistant":
            for raw in _reply_blocks(message):
                refs.update({ref: None for ref in block_refs(raw)})
    return list(refs)


async def render_turns(messages: List[ChatMessage], ctx: BlockContext) -> List[Dict[str, Any]]:
    """A stored chat as the client renders it: user text, and each answer's blocks hydrated like the
    stream's block_end payloads. Tool traffic isn't shown; blocks that no longer resolve are skipped."""
    turns: List[Dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            text = _user_text(message)
            if text:
                turns.append({"role": "user", "text": text})
            continue
        raws = _reply_blocks(message)
        if not raws:
            continue  # a tool-use step, not an answer
        parsed = [block for block in map(parse_block, raws) if block is not None]
        hydrated = await asyncio.gather(*(block.hydrate(ctx) for block in parsed), return_exceptions=True)  # type: ignore[attr-defined]
        blocks = []
        for result in hydrated:
            if isinstance(result, BlockDrop):
                logger.info("Skipped a stored block: %s", result)
            elif isinstance(result, BaseException):
                raise result
            else:
                blocks.append(result)
        turns.append({"role": "assistant", "blocks": blocks})
    return turns
