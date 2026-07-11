from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from app.core.exceptions import ProxyModelNotAllowed
from app.core.openai.model_registry import ModelRegistry
from app.core.openai.requests import ResponsesRequest
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.request_policy import apply_api_key_enforcement, validate_model_access


@pytest.mark.parametrize(
    ("alias", "canonical", "expected_effort", "expected_service_tier"),
    [
        ("gpt-5-extra", "gpt-5", "high", None),
        ("gpt-5.1-low", "gpt-5.1", "low", None),
        ("gpt-5.2-medium-fast", "gpt-5.2", "medium", "priority"),
        ("gpt-5.3-priority", "gpt-5.3", None, "priority"),
        ("gpt-5.4-xhigh", "gpt-5.4", "high", None),
        ("gpt-5.4-mini-high", "gpt-5.4-mini", "high", None),
        ("gpt-5.3-codex-fast", "gpt-5.3-codex", None, "priority"),
        ("gpt-5.1-codex-mini-extra-fast", "gpt-5.1-codex-mini", "high", "priority"),
        ("gpt-5.5-extra", "gpt-5.5", "high", None),
        ("gpt-5.5-extra-high-fast", "gpt-5.5", "high", "priority"),
        ("gpt-5.6-sol-extra-high-fast", "gpt-5.6-sol", "high", "priority"),
        ("gpt-5.6-sol-xhigh", "gpt-5.6-sol", "high", None),
        ("gpt-5.6-terra-extra-high-fast", "gpt-5.6-terra", "high", "priority"),
        ("gpt-5.6-terra-medium", "gpt-5.6-terra", "medium", None),
        ("gpt-5.6-luna-extra-high-fast", "gpt-5.6-luna", "high", "priority"),
        ("gpt-5.6-luna-low-fast", "gpt-5.6-luna", "low", "priority"),
    ],
)
def test_gpt5_cursor_aliases_target_canonical_models(
    alias: str,
    canonical: str,
    expected_effort: str | None,
    expected_service_tier: str | None,
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": alias,
            "instructions": "",
            "input": [],
            "reasoning": {"effort": "low"},
        }
    )

    apply_api_key_enforcement(request, None)

    assert request.model == canonical
    if expected_effort is not None:
        assert request.reasoning is not None
        assert request.reasoning.effort == expected_effort
    assert request.service_tier == expected_service_tier


def test_fast_mode_prohibition_keeps_harness_model_and_reasoning_but_omits_priority() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol-xhigh-fast",
            "instructions": "",
            "input": [],
        }
    )

    apply_api_key_enforcement(request, None, prohibit_fast_mode=True)

    assert request.model == "gpt-5.6-sol"
    assert request.reasoning is not None
    assert request.reasoning.effort == "high"
    assert request.service_tier is None


def test_fast_mode_prohibition_keeps_explicit_service_tier() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol-xhigh-fast",
            "instructions": "",
            "input": [],
            "service_tier": "flex",
        }
    )

    apply_api_key_enforcement(request, None, prohibit_fast_mode=True)

    assert request.service_tier == "flex"


def test_minimal_reasoning_alias_uses_upstream_safe_fallback() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1-minimal",
            "instructions": "",
            "input": [],
        }
    )

    apply_api_key_enforcement(request, None)

    assert request.model == "gpt-5.1"
    assert request.reasoning is not None
    assert request.reasoning.effort == "low"


def test_unknown_gpt5_suffix_is_not_rewritten() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5-preview",
            "instructions": "",
            "input": [],
        }
    )

    apply_api_key_enforcement(request, None)

    assert request.model == "gpt-5.5-preview"
    assert request.reasoning is None
    assert request.service_tier is None


def test_gpt56_ultra_suffix_is_not_rewritten() -> None:
    # The Cursor-style suffix grammar has no ``ultra``/``max`` reasoning
    # tokens (they are not effort levels every GPT-5-family base supports;
    # e.g. gpt-5.6-luna has no ``ultra``), so an ``ultra``-suffixed label is
    # an unknown alias and must pass through unchanged.
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol-ultra",
            "instructions": "",
            "input": [],
        }
    )

    apply_api_key_enforcement(request, None)

    assert request.model == "gpt-5.6-sol-ultra"
    assert request.reasoning is None
    assert request.service_tier is None


def test_enforced_non_lite_model_rejects_responses_lite_payload() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "exec"}],
                }
            ],
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-enforced-non-lite",
            enforced_model="gpt-5.5",
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
        ),
    )
    registry = cast(
        ModelRegistry,
        SimpleNamespace(
            get_models_for_metadata=lambda: {"gpt-5.5": SimpleNamespace(raw={"use_responses_lite": False})}
        ),
    )

    with pytest.raises(ProxyModelNotAllowed, match="does not support Responses Lite") as raised:
        apply_api_key_enforcement(request, api_key, registry=registry)

    assert raised.value.code == "responses_lite_model_mismatch"


def test_alias_equivalent_enforced_non_lite_model_rejects_responses_lite_payload() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5-extra-high-fast",
            "instructions": "",
            "input": [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "exec"}],
                }
            ],
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-enforced-alias-equivalent-non-lite",
            enforced_model="gpt-5.5",
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
        ),
    )
    registry = cast(
        ModelRegistry,
        SimpleNamespace(
            get_models_for_metadata=lambda: {"gpt-5.5": SimpleNamespace(raw={"use_responses_lite": False})}
        ),
    )

    with pytest.raises(ProxyModelNotAllowed, match="does not support Responses Lite") as raised:
        apply_api_key_enforcement(request, api_key, registry=registry)

    assert request.model == "gpt-5.5"
    assert raised.value.code == "responses_lite_model_mismatch"


def test_model_access_accepts_allowed_canonical_model_alias() -> None:
    api_key = cast(ApiKeyData, SimpleNamespace(allowed_models=frozenset({"gpt-5.5"})))

    validate_model_access(api_key, "gpt-5.5-extra-high-fast")


def test_model_access_accepts_allowed_canonical_gpt56_model_alias() -> None:
    api_key = cast(ApiKeyData, SimpleNamespace(allowed_models=frozenset({"gpt-5.6-sol"})))

    validate_model_access(api_key, "gpt-5.6-sol-extra-high-fast")


def test_model_access_accepts_allowed_qualified_canonical_model_alias() -> None:
    api_key = cast(ApiKeyData, SimpleNamespace(allowed_models=frozenset({"gpt-5.4-mini"})))

    validate_model_access(api_key, "gpt-5.4-mini-high")


def test_model_access_accepts_allowed_cursor_alias_for_canonical_model() -> None:
    api_key = cast(ApiKeyData, SimpleNamespace(allowed_models=frozenset({"gpt-5.4-mini-high"})))

    validate_model_access(api_key, "gpt-5.4-mini")


def test_model_access_rejects_alias_when_canonical_model_not_allowed() -> None:
    api_key = cast(ApiKeyData, SimpleNamespace(allowed_models=frozenset({"gpt-5.2"})))

    with pytest.raises(ProxyModelNotAllowed):
        validate_model_access(api_key, "gpt-5.5-extra")
