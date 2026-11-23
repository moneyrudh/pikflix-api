"""The agent's NDJSON stream: one Pydantic model per event type; each line is `event.model_dump_json()`."""
from typing import Any, Dict, Literal, Union

from pydantic import BaseModel


class SessionEvent(BaseModel):
    """First event of every turn: which chat this is (new chats get their id here)."""
    type: Literal["session"] = "session"
    session_id: str
    title: str


class StatusEvent(BaseModel):
    """Progress while tools run, e.g. "Looking up Dune (2021)"."""
    type: Literal["status"] = "status"
    text: str


class BlockStartEvent(BaseModel):
    """A block opened at `index` (the client can show a placeholder)."""
    type: Literal["block_start"] = "block_start"
    index: int
    block_type: str


class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    index: int
    text: str


class BlockEndEvent(BaseModel):
    """The block at `index`, ready to render (media blocks carry the full item). May arrive out of order."""
    type: Literal["block_end"] = "block_end"
    index: int
    block: Dict[str, Any]


class BlockDropEvent(BaseModel):
    """The block at `index` won't be shown (invalid, unknown ref, or not found): remove its placeholder."""
    type: Literal["block_drop"] = "block_drop"
    index: int


class TurnDoneEvent(BaseModel):
    type: Literal["done"] = "done"


class TurnErrorEvent(BaseModel):
    """Last event of a failed turn. Blocks already sent stay valid."""
    type: Literal["error"] = "error"
    message: str


AgentEvent = Union[SessionEvent, StatusEvent, BlockStartEvent, TextDeltaEvent, BlockEndEvent, BlockDropEvent,
                   TurnDoneEvent, TurnErrorEvent]
