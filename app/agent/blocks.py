"""
What the agent's reply is made of. Each block type is one Pydantic model in BLOCKS: its docstring and
Field descriptions are the schema text Claude reads to decide when to use it, and its hydrate() turns
what Claude wrote (text, or a ref) into what the client renders.

Adding a block type: write the model and its hydrate(), append it to BLOCKS, and add a renderer for
its `type` in the client. The loop, streamer, events and prompt don't change.
"""
from typing import Any, Collection, Dict, List, Literal, Optional, Type, Union

import anthropic
from pydantic import BaseModel, Field, TypeAdapter, create_model

from app.models import MediaRef, Movie, Show
from app.services.hydrate import Hydrator


class BlockContext:
    """What hydration needs: the hydrator, and the refs tools returned in this chat (the only ones a
    block may cite)."""

    def __init__(self, hydrator: Hydrator, seen_refs: Collection[str]) -> None:
        # Pass the tools' live `seen` dict during a turn, so refs found this turn count immediately
        self.hydrator = hydrator
        self.seen_refs = seen_refs


class BlockDrop(Exception):
    """The block can't be shown: a ref no tool returned, or an item that can't be found."""


def _checked_ref(value: str, ctx: BlockContext) -> MediaRef:
    try:
        ref = MediaRef.parse(value)
    except ValueError:
        raise BlockDrop(f"malformed ref {value!r}")
    if str(ref) not in ctx.seen_refs:
        raise BlockDrop(f"hallucinated_ref {ref}")  # never returned by a tool in this chat
    return ref


class TextBlock(BaseModel):
    """A short passage of markdown: framing, connections between items, answers to questions. Keep each
    one brief; put items in media blocks rather than describing them at length."""
    type: Literal["text"]
    text: str

    async def hydrate(self, ctx: BlockContext) -> Dict[str, Any]:
        return {"type": "text", "text": self.text}


class MediaBlock(BaseModel):
    """One movie, show, book or paper, shown as a card. `ref` must be copied exactly from a tool result
    in this conversation."""
    type: Literal["media"]
    ref: str = Field(description="e.g. movie:603 or book:OL893414W, copied from a tool result")
    note: Optional[str] = Field(description="One line on why it's here or what to notice; null if obvious")

    async def hydrate(self, ctx: BlockContext) -> Dict[str, Any]:
        ref = _checked_ref(self.ref, ctx)
        item = await ctx.hydrator.hydrate(ref)
        if item is None:
            raise BlockDrop(f"couldn't load {ref}")
        return {"type": "media", "media_type": ref.type.value, "ref": str(ref), "item": item.model_dump(mode="json"),
                "note": self.note}


class TrailerBlock(BaseModel):
    """A playable trailer for a movie or show. Use when the user asks to see or watch a trailer."""
    type: Literal["trailer"]
    ref: str = Field(description="A movie or show ref copied from a tool result")

    async def hydrate(self, ctx: BlockContext) -> Dict[str, Any]:
        ref = _checked_ref(self.ref, ctx)
        item = await ctx.hydrator.hydrate(ref)
        if not isinstance(item, (Movie, Show)) or item.trailer is None:
            raise BlockDrop(f"no trailer for {ref}")
        title = item.name if isinstance(item, Show) else item.title
        return {"type": "trailer", "ref": str(ref), "title": title, "trailer": item.trailer.model_dump(mode="json")}


class QuestionBlock(BaseModel):
    """Ask the user one clarifying question when the request is ambiguous (e.g. several plausible
    matches). Options are short tappable answers; the user can always type their own."""
    type: Literal["question"]
    question: str
    options: List[str] = Field(description="2-5 short answers that best separate the possibilities")

    async def hydrate(self, ctx: BlockContext) -> Dict[str, Any]:
        return {"type": "question", "question": self.question, "options": self.options[:5]}


Block = Union[TextBlock, MediaBlock, TrailerBlock, QuestionBlock]
BLOCKS: List[Type[BaseModel]] = [TextBlock, MediaBlock, TrailerBlock, QuestionBlock]  # ← append new block types

_BY_TYPE: Dict[str, Type[BaseModel]] = {model.model_fields["type"].annotation.__args__[0]: model for model in BLOCKS}  # type: ignore[union-attr]


def reply_model() -> Type[BaseModel]:
    """The agent's structured reply, {"items": [...blocks]}, generated from BLOCKS. A plain Union (anyOf
    with a Literal tag), not a discriminator: Claude's structured outputs document anyOf, not oneOf."""
    items: Any = List[Union[tuple(BLOCKS)]]  # type: ignore[misc, valid-type]
    return create_model("AgentReply", items=(items, Field(description="The reply, in order")))


def _const_to_enum(node: Any) -> Any:
    """Pydantic writes a one-value Literal as `const`, which transform_schema demotes to description text;
    a one-value `enum` stays a real constraint, so each block's `type` tag is enforced."""
    if isinstance(node, dict):
        node = {k: _const_to_enum(v) for k, v in node.items()}
        if "const" in node:
            node["enum"] = [node.pop("const")]
        return node
    if isinstance(node, list):
        return [_const_to_enum(v) for v in node]
    return node


def reply_schema() -> Dict[str, Any]:
    """The structured-output schema sent to Claude for the agent's reply."""
    schema: Dict[str, Any] = anthropic.transform_schema(_const_to_enum(TypeAdapter(reply_model()).json_schema()))
    return schema


def parse_block(raw: Dict[str, Any]) -> Optional[BaseModel]:
    """A completed block from the reply stream, validated against its registry model; None if invalid."""
    model = _BY_TYPE.get(raw.get("type", ""))
    if model is None:
        return None
    try:
        return model.model_validate(raw)
    except ValueError:
        return None


def block_refs(raw: Dict[str, Any]) -> List[str]:
    """Refs a stored block points at (for recall and reopened chats)."""
    ref = raw.get("ref")
    return [ref] if raw.get("type") in ("media", "trailer") and isinstance(ref, str) else []

