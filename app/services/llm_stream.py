import json
import logging
from dataclasses import dataclass
from typing import Generic, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class ParseStats:
    """Counters a caller can pass in to see what the parser threw away."""
    skipped: int = 0  # elements that weren't valid JSON or didn't match the model


class ArrayItemParser(Generic[T]):
    """
    Incrementally parses streamed structured output of the form {"<key>": [ {...}, {...} ]}
    and returns each array element as a validated model the moment its closing brace arrives.

    Character-by-character state machine: tracks string/escape state so braces and quotes
    inside strings are ignored, and tracks depth relative to the array so elements may
    contain nested objects and lists.
    """

    def __init__(self, model: Type[T], key: str = "items", stats: Optional[ParseStats] = None) -> None:
        self._model = model
        self.stats = stats if stats is not None else ParseStats()
        self._key = key
        self._depth = 0
        self._in_string = False
        self._escaped = False
        self._key_buf: Optional[List[str]] = None  # collects a top-level string (candidate key)
        self._last_key: Optional[str] = None
        self._array_depth: Optional[int] = None    # depth just inside the target array, once found
        self._element: List[str] = []
        self.done = False                           # the target array has closed

    def feed(self, text: str) -> List[T]:
        items: List[T] = []
        for ch in text:
            if self.done:
                break

            in_element = self._array_depth is not None and self._depth > self._array_depth
            if in_element:
                self._element.append(ch)

            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif ch == "\\":
                    self._escaped = True
                elif ch == '"':
                    self._in_string = False
                    if self._key_buf is not None:
                        self._last_key = "".join(self._key_buf)
                        self._key_buf = None
                    continue
                if self._key_buf is not None:
                    self._key_buf.append(ch)
                continue

            if ch == '"':
                self._in_string = True
                if self._depth == 1 and self._array_depth is None:
                    self._key_buf = []
            elif ch in "{[":
                if self._array_depth is None:
                    if ch == "[" and self._depth == 1 and self._last_key == self._key:
                        self._array_depth = self._depth + 1
                elif self._depth == self._array_depth:
                    self._element = [ch]
                self._depth += 1
            elif ch in "}]":
                self._depth -= 1
                if self._array_depth is not None:
                    if self._depth == self._array_depth:
                        item = self._validate("".join(self._element))
                        if item is not None:
                            items.append(item)
                        self._element = []
                    elif self._depth < self._array_depth:
                        self.done = True
        return items

    def _validate(self, raw: str) -> Optional[T]:
        try:
            return self._model.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("Skipping invalid %s: %s (%s)", self._model.__name__, raw, e)
            self.stats.skipped += 1
            return None
