import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest

from app.models import ContentType
from app.schemas import BookPicks, ContentRecommendations
from app.services.anthropic_service import AnthropicService

SNAPSHOTS = Path(__file__).parent / "snapshots"
FORBIDDEN_KEYS = {"oneOf", "discriminator", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                  "minLength", "maxLength", "minItems", "maxItems"}


def walk_keys(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_keys(value)


SCHEMAS = {"content_recommendations": ContentRecommendations, "book_picks": BookPicks}


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_snapshot(name):
    """The schemas sent to Claude are part of the contract. Regenerate with UPDATE_SNAPSHOTS=1."""
    schema = AnthropicService.build_schema(SCHEMAS[name])
    path = SNAPSHOTS / f"{name}.json"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    assert schema == json.loads(path.read_text())


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_uses_items_key_and_supported_keywords(name):
    schema = AnthropicService.build_schema(SCHEMAS[name])
    assert list(schema["properties"]) == ["items"]  # the streaming parser reads the top-level `items` array
    assert not FORBIDDEN_KEYS & set(walk_keys(schema))


def test_content_type_enum_stays_a_constraint():
    schema = AnthropicService.build_schema(ContentRecommendations)
    # Enums must stay constraints, not be demoted into description text (anthropic<0.87 did that)
    assert schema["$defs"]["ContentType"]["enum"] == [t.value for t in ContentType]


def test_book_year_is_required_but_nullable():
    pick = AnthropicService.build_schema(BookPicks)["$defs"]["BookPick"]
    assert set(pick["required"]) == {"title", "author", "year", "reason"}
    assert {"type": "null"} in pick["properties"]["year"]["anyOf"]
