import json
from typing import List, Optional

from pydantic import BaseModel

from app.models import ContentType
from app.schemas import ContentRecommendation
from app.services.llm_stream import ArrayItemParser


class Block(BaseModel):
    type: str
    options: List[str] = []
    ref: Optional[dict] = None


def feed_in_chunks(parser: ArrayItemParser, text: str, size: int) -> list:
    items = []
    for i in range(0, len(text), size):
        items.extend(parser.feed(text[i:i + size]))
    return items


def rec(title: str, reason: str = "fits") -> dict:
    return {"title": title, "year": 2000, "reason": reason, "content_type": "movie"}


def test_yields_each_element_in_order():
    text = json.dumps({"items": [rec("A"), rec("B"), rec("C")]})
    parser = ArrayItemParser(ContentRecommendation)
    items = parser.feed(text)
    assert [r.title for r in items] == ["A", "B", "C"]
    assert items[0].content_type == ContentType.MOVIE
    assert parser.done


def test_same_result_for_any_chunking():
    text = json.dumps({"items": [rec("A"), rec("B", 'has "quotes" and {braces}')]}, indent=2)
    expected = [r.title for r in ArrayItemParser(ContentRecommendation).feed(text)]
    for size in (1, 2, 3, 7, 50):
        items = feed_in_chunks(ArrayItemParser(ContentRecommendation), text, size)
        assert [r.title for r in items] == expected == ["A", "B"]


def test_escaped_quotes_and_braces_in_strings():
    reason = 'He said \\"run}\\" ] and left \\\\'
    text = '{"items": [{"title": "A", "year": 1999, "reason": "' + reason + '", "content_type": "show"}]}'
    items = feed_in_chunks(ArrayItemParser(ContentRecommendation), text, 1)
    assert len(items) == 1
    assert items[0].reason == 'He said "run}" ] and left \\'


def test_nested_objects_and_lists_inside_elements():
    blocks = [
        {"type": "question", "options": ["yes", "no"]},
        {"type": "media", "ref": {"type": "movie", "id": "603", "meta": {"x": [1, {"y": 2}]}}},
    ]
    parser = ArrayItemParser(Block)
    items = feed_in_chunks(parser, json.dumps({"items": blocks}), 4)
    assert [b.model_dump(exclude_defaults=True) for b in items] == blocks
    assert parser.done


def test_only_the_target_key_is_parsed():
    text = json.dumps({"other": [rec("X")], "items": [rec("A")]})
    items = ArrayItemParser(ContentRecommendation).feed(text)
    assert [r.title for r in items] == ["A"]


def test_invalid_elements_are_skipped():
    text = json.dumps({"items": [rec("A"), {"title": "missing fields"}, rec("B")]})
    parser = ArrayItemParser(ContentRecommendation)
    items = parser.feed(text)
    assert [r.title for r in items] == ["A", "B"]
    assert parser.stats.skipped == 1  # so the endpoint can report it as dropped


def test_stops_after_array_closes():
    parser = ArrayItemParser(ContentRecommendation)
    parser.feed(json.dumps({"items": [rec("A")]}))
    assert parser.done
    assert parser.feed(json.dumps(rec("late"))) == []


def test_incomplete_element_is_not_yielded():
    text = json.dumps({"items": [rec("A"), rec("B")]})
    cut = text.index('"B"')
    parser = ArrayItemParser(ContentRecommendation)
    assert [r.title for r in parser.feed(text[:cut])] == ["A"]
    assert not parser.done
    assert [r.title for r in parser.feed(text[cut:])] == ["B"]
