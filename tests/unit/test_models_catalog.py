from __future__ import annotations

import pytest

from app.modules.claude.models_catalog import (
    KNOWN_CLAUDE_MODELS,
    is_known_claude_model,
    list_claude_models,
)

pytestmark = pytest.mark.unit


def test_known_claude_models_is_non_empty() -> None:
    assert len(KNOWN_CLAUDE_MODELS) >= 1


def test_known_claude_models_ids_start_with_claude_prefix() -> None:
    for model_id in KNOWN_CLAUDE_MODELS:
        assert model_id.startswith("claude-"), model_id


def test_known_claude_models_does_not_contain_deprecated_ids() -> None:
    deprecated = frozenset(
        {
            "claude-1",
            "claude-1.3",
            "claude-2.0",
            "claude-instant-1",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-3-5-sonnet-20240620",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-7-sonnet-20250219",
        }
    )
    assert deprecated.isdisjoint(KNOWN_CLAUDE_MODELS), (
        f"deprecated ids present: {sorted(deprecated & KNOWN_CLAUDE_MODELS)}"
    )


def test_list_claude_models_returns_anthropic_envelope_shape() -> None:
    out = list_claude_models()

    assert out["object"] == "list"
    assert isinstance(out["data"], list)
    assert out["data"], "data must be non-empty"
    assert len(out["data"]) == len(KNOWN_CLAUDE_MODELS)

    for entry in out["data"]:
        assert set(entry.keys()) >= {"id", "object", "display_name", "type"}
        assert isinstance(entry["id"], str)
        assert entry["object"] == "model"
        assert entry["type"] == "model"
        assert isinstance(entry["display_name"], str)
        assert entry["display_name"], "display_name must be a non-empty string"


def test_list_claude_models_returns_ids_in_sorted_order() -> None:
    out = list_claude_models()
    ids = [entry["id"] for entry in out["data"]]
    assert ids == sorted(ids), "list_claude_models must return models in sorted order"


def test_list_claude_models_ids_match_known_set() -> None:
    out = list_claude_models()
    listed = {entry["id"] for entry in out["data"]}
    assert listed == set(KNOWN_CLAUDE_MODELS)


def test_is_known_claude_model_recognizes_canonical_ids() -> None:
    for model_id in KNOWN_CLAUDE_MODELS:
        assert is_known_claude_model(model_id) is True


def test_is_known_claude_model_rejects_unknown_ids() -> None:
    assert is_known_claude_model("claude-1") is False
    assert is_known_claude_model("gpt-4o") is False
    assert is_known_claude_model("") is False
