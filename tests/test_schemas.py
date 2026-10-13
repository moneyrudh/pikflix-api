import json
import os
from pathlib import Path
from typing import Any, Iterator

from app.models import ContentType
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


def test_recommendation_schema_snapshot():
    """The schema sent to Claude is part of the contract. Regenerate with UPDATE_SNAPSHOTS=1."""
    schema = AnthropicService._build_schema()
    path = SNAPSHOTS / "content_recommendations.json"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    assert schema == json.loads(path.read_text())


def test_recommendation_schema_uses_items_key_and_supported_keywords():
    schema = AnthropicService._build_schema()
    assert list(schema["properties"]) == ["items"]
    assert not FORBIDDEN_KEYS & set(walk_keys(schema))
    # Enums must stay constraints, not be demoted into description text (anthropic<0.87 did that)
    assert schema["$defs"]["ContentType"]["enum"] == [t.value for t in ContentType]
