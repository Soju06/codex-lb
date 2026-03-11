from __future__ import annotations

import pytest

from app.modules.proxy.additional_model_limits import (
    get_additional_display_label_for_model,
    get_additional_limit_name_for_model,
    get_additional_model_limit,
)

pytestmark = pytest.mark.unit


def test_get_additional_model_limit_returns_seeded_mapping() -> None:
    resolved = get_additional_model_limit("gpt-5.3-codex-spark")

    assert resolved is not None
    assert resolved.model == "gpt-5.3-codex-spark"
    assert resolved.limit_name == "codex_other"
    assert resolved.display_label == "GPT-5.3-Codex-Spark"


def test_get_additional_model_limit_normalizes_case_and_whitespace() -> None:
    resolved = get_additional_model_limit("  GPT-5.3-CODEX-SPARK  ")

    assert resolved is not None
    assert resolved.limit_name == "codex_other"
    assert resolved.display_label == "GPT-5.3-Codex-Spark"


def test_get_additional_limit_name_for_model_returns_none_for_unmapped_model() -> None:
    assert get_additional_limit_name_for_model("gpt-5.3-codex") is None
    assert get_additional_limit_name_for_model(None) is None


def test_get_additional_display_label_for_model_returns_seeded_label() -> None:
    assert get_additional_display_label_for_model("gpt-5.3-codex-spark") == "GPT-5.3-Codex-Spark"
    assert get_additional_display_label_for_model("gpt-5.3-codex") is None
