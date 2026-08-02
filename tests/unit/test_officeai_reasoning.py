from __future__ import annotations

import json
from pathlib import Path

from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.model_registry import ModelRegistry
from app.modules.proxy.officeai_reasoning import (
    apply_officeai_reasoning_override,
    default_officeai_reasoning_control_path,
    load_officeai_reasoning_control,
    resolve_officeai_reasoning_effort,
)


def _chat_request(**extra: object) -> ChatCompletionsRequest:
    return ChatCompletionsRequest.model_validate(
        {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            **extra,
        }
    )


def _write_control(path: Path, **values: object) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "effort": "maximum",
                **values,
            }
        ),
        encoding="utf-8",
    )


def test_missing_or_invalid_control_file_leaves_request_unchanged(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")

    assert load_officeai_reasoning_control(missing) is None
    assert load_officeai_reasoning_control(invalid) is None


def test_control_path_is_resolved_beside_sqlite_database(tmp_path: Path) -> None:
    database = tmp_path / "store.db"

    assert default_officeai_reasoning_control_path(f"sqlite+aiosqlite:///{database.as_posix()}") == (
        tmp_path / "officeai-reasoning.json"
    )
    assert default_officeai_reasoning_control_path("postgresql+asyncpg://db.example/app") is None


def test_maximum_resolves_to_highest_wire_safe_model_effort() -> None:
    registry = ModelRegistry()

    assert resolve_officeai_reasoning_effort("maximum", "gpt-5.4", registry=registry) == "xhigh"
    assert resolve_officeai_reasoning_effort("maximum", "gpt-5.6-sol", registry=registry) == "max"


def test_unknown_model_maximum_falls_back_to_high() -> None:
    assert (
        resolve_officeai_reasoning_effort(
            "maximum",
            "private-unknown-model",
            registry=ModelRegistry(),
        )
        == "high"
    )


def test_fixed_effort_is_clamped_to_model_capabilities() -> None:
    registry = ModelRegistry()

    assert resolve_officeai_reasoning_effort("max", "gpt-5.4", registry=registry) == "xhigh"
    assert resolve_officeai_reasoning_effort("xhigh", "gpt-5.6-luna", registry=registry) == "xhigh"


def test_override_fills_missing_effort_and_preserves_reasoning_summary(tmp_path: Path) -> None:
    control_path = tmp_path / "reasoning.json"
    _write_control(control_path, effort="maximum", api_key_prefix="sk-clb-office")
    chat = _chat_request(reasoning={"summary": "auto"})
    responses = chat.to_responses_request()

    applied = apply_officeai_reasoning_override(
        responses,
        original_chat_request=chat,
        config_path=control_path,
        authenticated_api_key_prefix="sk-clb-office",
        registry=ModelRegistry(),
    )

    assert applied == "xhigh"
    assert responses.reasoning is not None
    assert responses.reasoning.effort == "xhigh"
    assert responses.reasoning.summary == "auto"
    assert chat.model_dump(mode="json", exclude_none=True)["reasoning_effort"] == "xhigh"


def test_boolean_thinking_alias_can_be_replaced_with_selected_effort(tmp_path: Path) -> None:
    control_path = tmp_path / "reasoning.json"
    _write_control(control_path, effort="high")
    chat = _chat_request(enable_thinking=True)
    responses = chat.to_responses_request()
    assert responses.reasoning is not None
    assert responses.reasoning.effort == "medium"

    applied = apply_officeai_reasoning_override(
        responses,
        original_chat_request=chat,
        config_path=control_path,
        authenticated_api_key_prefix=None,
        registry=ModelRegistry(),
    )

    assert applied == "high"
    assert responses.reasoning is not None
    assert responses.reasoning.effort == "high"


def test_explicit_reasoning_effort_is_never_overwritten(tmp_path: Path) -> None:
    control_path = tmp_path / "reasoning.json"
    _write_control(control_path, effort="maximum")

    for explicit in (
        {"reasoning_effort": "low"},
        {"reasoningEffort": "low"},
        {"reasoning": {"effort": "low"}},
    ):
        chat = _chat_request(**explicit)
        responses = chat.to_responses_request()

        applied = apply_officeai_reasoning_override(
            responses,
            original_chat_request=chat,
            config_path=control_path,
            authenticated_api_key_prefix=None,
            registry=ModelRegistry(),
        )

        assert applied is None
        assert responses.reasoning is not None
        assert responses.reasoning.effort == "low"


def test_disabled_or_nonmatching_api_key_scope_is_ignored(tmp_path: Path) -> None:
    control_path = tmp_path / "reasoning.json"
    _write_control(control_path, enabled=False)
    chat = _chat_request()
    responses = chat.to_responses_request()

    assert (
        apply_officeai_reasoning_override(
            responses,
            original_chat_request=chat,
            config_path=control_path,
            authenticated_api_key_prefix=None,
            registry=ModelRegistry(),
        )
        is None
    )
    assert responses.reasoning is None

    _write_control(control_path, enabled=True, api_key_prefix="sk-clb-office")
    assert (
        apply_officeai_reasoning_override(
            responses,
            original_chat_request=chat,
            config_path=control_path,
            authenticated_api_key_prefix="sk-clb-other",
            registry=ModelRegistry(),
        )
        is None
    )
    assert responses.reasoning is None
