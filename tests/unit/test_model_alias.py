from __future__ import annotations

import pytest

from app.core.openai.model_alias import parse_model_alias


@pytest.mark.parametrize(
    "raw, expected_model, expected_effort",
    [
        # Plain model — no transformation
        ("gpt-5.4", "gpt-5.4", None),
        ("o3", "o3", None),
        # Provider prefix stripped
        ("openai/gpt-5.4", "gpt-5.4", None),
        ("anthropic/o3", "o3", None),
        ("google/gemini-2.5-pro", "gemini-2.5-pro", None),
        # Reasoning effort extracted
        ("gpt-5.4(high)", "gpt-5.4", "high"),
        ("gpt-5.4(low)", "gpt-5.4", "low"),
        ("gpt-5.4(none)", "gpt-5.4", "none"),
        ("gpt-5.4(minimal)", "gpt-5.4", "minimal"),
        ("gpt-5.4(medium)", "gpt-5.4", "medium"),
        ("gpt-5.4(xhigh)", "gpt-5.4", "xhigh"),
        # Case-insensitive effort
        ("gpt-5.4(HIGH)", "gpt-5.4", "high"),
        ("gpt-5.4(Medium)", "gpt-5.4", "medium"),
        # Both prefix and effort
        ("openai/gpt-5.4(high)", "gpt-5.4", "high"),
        ("anthropic/o3(low)", "o3", "low"),
        # Invalid effort value — kept in model name as-is
        ("gpt-5.4(turbo)", "gpt-5.4(turbo)", None),
        ("gpt-5.4(999)", "gpt-5.4(999)", None),
        # Empty parentheses — no match
        ("gpt-5.4()", "gpt-5.4()", None),
        # Multiple slashes — only first prefix stripped
        ("a/b/gpt-5.4", "b/gpt-5.4", None),
    ],
)
def test_parse_model_alias(raw: str, expected_model: str, expected_effort: str | None) -> None:
    model, effort = parse_model_alias(raw)
    assert model == expected_model
    assert effort == expected_effort
