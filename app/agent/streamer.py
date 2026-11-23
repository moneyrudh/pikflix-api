"""
Turns the agent's streamed structured reply ({"items": [...blocks]}) into per-block updates, so text
renders word by word and each block can be finished as soon as it's complete.

On every delta the whole buffer is re-parsed with pydantic_core's partial JSON parser, the same
approach the Anthropic SDK uses for streamed tool input. A reply is a few KB, so this costs
microseconds per delta (measured: ~4-7 us at 2.5 KB). Two parses per delta:
- allow_partial=True keeps only finished strings: it decides each block's `type` (a cut-off "te" must
  not look like a type) and whether a block is complete;
- allow_partial="trailing-strings" also keeps the open string, for the text still being written. It
  holds back a half-received escape (\\n, \\u00e9) until complete, so the text so far is always a
  correct prefix.

Earlier blocks never change (the array only grows at the end), so three counters are all the state:
`started` (announced), `finished` (complete), `sent` (characters of the open text block emitted).
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic_core import from_json


@dataclass
class StreamUpdate:
    kind: Literal["start", "text", "complete"]
    index: int
    block_type: Optional[str] = None
    text: Optional[str] = None                # "text": the newly arrived characters
    block: Optional[Dict[str, Any]] = None    # "complete": the whole raw block


def _items(buffer: str, partial: Any) -> List[Dict[str, Any]]:
    try:
        parsed = from_json(buffer, allow_partial=partial)
    except ValueError:
        return []  # not parseable yet (e.g. just whitespace)
    items = parsed.get("items") if isinstance(parsed, dict) else None
    return [i if isinstance(i, dict) else {} for i in items] if isinstance(items, list) else []


class ReplyStreamer:
    def __init__(self) -> None:
        self._buffer = ""
        self._started = 0
        self._finished = 0
        self._sent = 0

    def feed(self, delta: str) -> List[StreamUpdate]:
        self._buffer += delta
        return self._advance(_items(self._buffer, True), _items(self._buffer, "trailing-strings"), closed=False)

    def close(self) -> List[StreamUpdate]:
        """The reply ended: one strict parse finishes the last block. Raises ValueError if the JSON is
        invalid (a cut-off reply), so the caller can end the turn with an error."""
        parsed = from_json(self._buffer) if self._buffer.strip() else {"items": []}
        items = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            raise ValueError("reply has no items list")
        items = [i if isinstance(i, dict) else {} for i in items]
        return self._advance(items, items, closed=True)

    def _advance(self, items: List[Dict[str, Any]], texts: List[Dict[str, Any]], closed: bool) -> List[StreamUpdate]:
        updates: List[StreamUpdate] = []
        for i in range(self._finished, len(items)):
            block_type = items[i].get("type")
            if not isinstance(block_type, str):
                break  # its "type" is still streaming
            if i == self._started:
                updates.append(StreamUpdate("start", i, block_type=block_type))
                self._started += 1
                self._sent = 0
            if block_type == "text":
                text = texts[i].get("text") if i < len(texts) else None
                if isinstance(text, str) and len(text) > self._sent:
                    updates.append(StreamUpdate("text", i, text=text[self._sent:]))
                    self._sent = len(text)
            # Complete once a later block has started (the parser shows a just-opened one as {}) or the reply closed
            if i == len(items) - 1 and not closed:
                break
            updates.append(StreamUpdate("complete", i, block_type=block_type, block=items[i]))
            self._finished += 1
        return updates
