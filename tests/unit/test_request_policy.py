from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from app.core.exceptions import ProxyModelNotAllowed, ProxyReasoningEffortNotAllowed
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.model_registry import ModelRegistry
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    apply_api_key_enforcement_to_chat_payload,
    normalize_source_reasoning_aliases,
    responses_source_route_excluded,
    validate_model_access,
)


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


def test_reasoning_effort_allowlist_rejects_max_before_wire_normalization() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [],
            "reasoning": {"effort": "max"},
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-reasoning-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["minimal", "low", "medium", "high", "xhigh"],
            enforced_service_tier=None,
        ),
    )

    with pytest.raises(ProxyReasoningEffortNotAllowed, match="max") as raised:
        apply_api_key_enforcement(request, api_key)

    assert raised.value.code == "reasoning_effort_not_allowed"
    assert raised.value.param == "reasoning.effort"
    assert request.reasoning is not None
    assert request.reasoning.effort == "max"


def test_reasoning_effort_allowlist_uses_client_plane_model_alias() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol-xhigh",
            "instructions": "",
            "input": [],
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-alias-reasoning-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["xhigh"],
            enforced_service_tier=None,
        ),
    )

    apply_api_key_enforcement(request, api_key)

    assert request.model == "gpt-5.6-sol"
    assert request.reasoning is not None
    assert request.reasoning.effort == "high"


def test_reasoning_effort_allowlist_preserves_client_alias_when_model_is_enforced() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol-xhigh",
            "instructions": "",
            "input": [],
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-enforced-plain-model-reasoning-policy",
            enforced_model="gpt-5.6-sol",
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["xhigh"],
            enforced_service_tier=None,
        ),
    )

    apply_api_key_enforcement(request, api_key)

    assert request.model == "gpt-5.6-sol"
    assert request.reasoning is not None
    assert request.reasoning.effort == "high"


def test_reasoning_effort_allowlist_is_idempotent_after_wire_normalization() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol-xhigh",
            "instructions": "",
            "input": [],
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-idempotent-reasoning-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["xhigh"],
            enforced_service_tier=None,
        ),
    )

    apply_api_key_enforcement(request, api_key)
    apply_api_key_enforcement(request, api_key)


@pytest.mark.parametrize(
    ("field", "value", "expected_effort"),
    [
        ("reasoningEffort", "max", "max"),
        ("reasoning_effort", "max", "max"),
        ("thinking", "minimal", "minimal"),
        ("thinking", {"effort": "max", "summary": "auto"}, "max"),
        ("enable_thinking", True, "medium"),
    ],
)
def test_reasoning_effort_allowlist_checks_responses_alias_fields(
    field: str,
    value: JsonValue,
    expected_effort: str,
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [],
            field: value,
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-responses-alias-reasoning-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["low"],
            enforced_service_tier=None,
        ),
    )

    with pytest.raises(ProxyReasoningEffortNotAllowed, match=expected_effort):
        apply_api_key_enforcement(request, api_key)


@pytest.mark.parametrize(
    "request_type",
    [
        pytest.param(ResponsesRequest, id="responses-and-websocket"),
        pytest.param(ResponsesCompactRequest, id="compact"),
    ],
)
def test_provider_reasoning_alias_runs_subscription_wire_fallback(request_type) -> None:
    request = request_type.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [],
            "thinking": "minimal",
        }
    )

    apply_api_key_enforcement(request, None)

    assert request.reasoning is not None
    assert request.reasoning.effort == "low"
    assert request.to_payload()["reasoning"] == {"effort": "low"}


def test_reasoning_effort_allowlist_ignores_blank_alias_before_thinking_effort() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [],
            "reasoningEffort": " ",
            "thinking": "max",
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-blank-reasoning-alias-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["low"],
            enforced_service_tier=None,
        ),
    )

    with pytest.raises(ProxyReasoningEffortNotAllowed, match="max"):
        apply_api_key_enforcement(request, api_key)


@pytest.mark.parametrize(
    "disabled_thinking",
    [False, "disabled", "false", "off", {"type": "disabled"}, {"enabled": False}],
)
def test_reasoning_effort_allowlist_checks_enabled_alias_after_disabled_thinking(
    disabled_thinking: JsonValue,
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [],
            "thinking": disabled_thinking,
            "enable_thinking": True,
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-conflicting-thinking-alias-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["low"],
            enforced_service_tier=None,
        ),
    )

    with pytest.raises(ProxyReasoningEffortNotAllowed, match="medium"):
        apply_api_key_enforcement(request, api_key)


@pytest.mark.parametrize(
    ("thinking", "enable_thinking"),
    [
        ({"summary": "auto", "enabled": True}, None),
        ({"summary": "auto", "type": "enabled"}, None),
        ({"summary": "auto"}, True),
    ],
)
def test_reasoning_effort_allowlist_checks_enabled_thinking_with_metadata(
    thinking: JsonValue,
    enable_thinking: bool | None,
) -> None:
    request_payload: dict[str, JsonValue] = {
        "model": "gpt-5.6-sol",
        "instructions": "",
        "input": [],
        "thinking": thinking,
    }
    if enable_thinking is not None:
        request_payload["enable_thinking"] = enable_thinking
    request = ResponsesRequest.model_validate(request_payload)
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-thinking-metadata-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["low"],
            enforced_service_tier=None,
        ),
    )

    with pytest.raises(ProxyReasoningEffortNotAllowed, match="medium"):
        apply_api_key_enforcement(request, api_key)


@pytest.mark.parametrize("request_type", [ResponsesRequest, ResponsesCompactRequest])
@pytest.mark.parametrize(("alias_effort", "wire_effort"), [("minimal", "low"), ("ultra", "max")])
def test_reasoning_aliases_receive_wire_normalization_after_allowlist(
    request_type,
    alias_effort: str,
    wire_effort: str,
) -> None:
    request = request_type.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "hi",
            "input": [],
            "reasoningEffort": alias_effort,
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-reasoning-alias-wire-normalization",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=[alias_effort],
            enforced_service_tier=None,
        ),
    )

    apply_api_key_enforcement(request, api_key)

    assert request.reasoning is not None
    assert request.reasoning.effort == wire_effort


@pytest.mark.parametrize(
    "request_type",
    [
        pytest.param(ResponsesRequest, id="responses-and-websocket"),
        pytest.param(ResponsesCompactRequest, id="compact"),
    ],
)
def test_allowed_canonical_reasoning_effort_is_normalized_for_subscription_wire(request_type) -> None:
    request = request_type.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "hi",
            "input": [],
            "reasoning": {"effort": " LOW "},
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-canonical-reasoning-wire-normalization",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["low"],
            enforced_service_tier=None,
        ),
    )

    apply_api_key_enforcement(request, api_key)

    assert request.reasoning is not None
    assert request.reasoning.effort == "low"
    assert request.to_payload()["reasoning"] == {"effort": "low"}


def test_reasoning_effort_allowlist_checks_explicit_effort_with_fast_model_alias() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol-fast",
            "instructions": "",
            "input": [],
            "reasoning": {"effort": "max"},
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-fast-alias-reasoning-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["low"],
            enforced_service_tier=None,
        ),
    )

    with pytest.raises(ProxyReasoningEffortNotAllowed, match="max"):
        apply_api_key_enforcement(request, api_key)


def test_reasoning_effort_allowlist_checks_alias_effort_from_enforced_model() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [],
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-enforced-model-reasoning-policy",
            enforced_model="gpt-5.6-sol-xhigh",
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["low"],
            enforced_service_tier=None,
        ),
    )

    with pytest.raises(ProxyReasoningEffortNotAllowed, match="xhigh"):
        apply_api_key_enforcement(request, api_key)


def test_reasoning_effort_allowlist_allows_alias_effort_from_enforced_model() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [],
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-enforced-model-allowed-reasoning-policy",
            enforced_model="gpt-5.6-sol-xhigh",
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=["xhigh"],
            enforced_service_tier=None,
        ),
    )

    apply_api_key_enforcement(request, api_key)

    assert request.model == "gpt-5.6-sol"
    assert request.reasoning is not None
    assert request.reasoning.effort == "high"


@pytest.mark.parametrize(
    ("requested_effort", "allowed_effort"),
    [
        ("xhigh", "high"),
        ("high", "xhigh"),
        ("ultra", "max"),
        ("max", "ultra"),
    ],
)
def test_reasoning_effort_allowlist_keeps_client_plane_efforts_distinct(
    requested_effort: str,
    allowed_effort: str,
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": [],
            "reasoning": {"effort": requested_effort},
        }
    )
    api_key = cast(
        ApiKeyData,
        SimpleNamespace(
            id="key-ultra-reasoning-policy",
            enforced_model=None,
            enforced_reasoning_effort=None,
            allowed_reasoning_efforts=[allowed_effort],
            enforced_service_tier=None,
        ),
    )

    with pytest.raises(ProxyReasoningEffortNotAllowed, match=requested_effort):
        apply_api_key_enforcement(request, api_key)


def test_source_chat_reasoning_aliases_remain_unchanged_without_policy() -> None:
    payload: dict[str, JsonValue] = {
        "reasoning_effort": "ultra",
        "reasoningEffort": "ultra",
        "thinking": {"effort": "ultra", "summary": "auto"},
        "reasoning": {"effort": "ultra", "summary": "auto"},
    }

    apply_api_key_enforcement_to_chat_payload(payload, None)

    assert payload == {
        "reasoning_effort": "ultra",
        "reasoningEffort": "ultra",
        "thinking": {"effort": "ultra", "summary": "auto"},
        "reasoning": {"effort": "ultra", "summary": "auto"},
    }


def test_source_chat_reasoning_policy_aligns_conflicting_aliases() -> None:
    payload: dict[str, JsonValue] = {
        "reasoning_effort": "low",
        "reasoningEffort": "low",
        "thinking": {"effort": "ultra", "summary": "auto", "type": "budget", "budget": 4096},
        "enable_thinking": True,
        "reasoning": {"effort": "ultra", "summary": "auto"},
    }

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort="low")

    assert payload == {
        "reasoning_effort": "low",
        "reasoningEffort": "low",
        "thinking": {"effort": "low", "summary": "auto", "type": "budget", "budget": 4096},
        "reasoning": {"effort": "low", "summary": "auto"},
    }


@pytest.mark.parametrize("inactive_selector", [{"type": "disabled"}, {"enabled": False}])
def test_source_chat_reasoning_policy_removes_inactive_selector_from_explicit_thinking(
    inactive_selector: dict[str, JsonValue],
) -> None:
    payload: dict[str, JsonValue] = {
        "thinking": {"effort": "low", **inactive_selector, "vendor_hint": "keep"},
    }

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort="low")

    assert payload == {"thinking": {"effort": "low", "vendor_hint": "keep"}}


@pytest.mark.parametrize("effort", ["minimal", "xhigh"])
def test_source_chat_reasoning_policy_preserves_client_plane_effort(effort: str) -> None:
    payload: dict[str, JsonValue] = {"reasoning_effort": "low"}

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort=effort)

    assert payload == {
        "reasoning_effort": effort,
    }


def test_source_chat_reasoning_policy_preserves_caller_alias_set() -> None:
    payload: dict[str, JsonValue] = {"thinking": "ultra"}

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort="ultra")

    assert payload == {"thinking": "max"}


def test_source_chat_reasoning_policy_preserves_authorized_enable_thinking() -> None:
    payload: dict[str, JsonValue] = {"enable_thinking": True}

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort="medium")

    assert payload == {"enable_thinking": True}


@pytest.mark.parametrize(
    "thinking",
    [
        {"type": "enabled", "budget_tokens": 2048},
        {"enabled": True, "summary": "auto", "vendor_hint": "keep"},
    ],
)
def test_source_chat_reasoning_policy_preserves_implicit_thinking_object(thinking: dict[str, JsonValue]) -> None:
    payload: dict[str, JsonValue] = {"thinking": thinking}

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort="medium")

    assert payload == {"thinking": thinking}


def test_source_chat_reasoning_policy_strips_blank_effort_from_implicit_thinking_object() -> None:
    payload: dict[str, JsonValue] = {
        "thinking": {"effort": " ", "enabled": True, "budget_tokens": 2048, "vendor_hint": "keep"}
    }

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort="medium")

    assert payload == {"thinking": {"enabled": True, "budget_tokens": 2048, "vendor_hint": "keep"}}


@pytest.mark.parametrize("thinking", [{"enabled": False}, {"type": "disabled"}])
def test_source_chat_reasoning_policy_drops_inactive_thinking_object_beside_enable_alias(
    thinking: dict[str, JsonValue],
) -> None:
    payload: dict[str, JsonValue] = {"thinking": thinking, "enable_thinking": True}

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort="medium")

    assert payload == {"enable_thinking": True}


def test_source_chat_reasoning_policy_drops_conflicting_implicit_thinking_object() -> None:
    payload: dict[str, JsonValue] = {
        "reasoning_effort": "low",
        "thinking": {"type": "enabled", "budget_tokens": 2048},
    }

    apply_api_key_enforcement_to_chat_payload(payload, None, allowed_reasoning_effort="low")

    assert payload == {"reasoning_effort": "low"}


def test_source_reasoning_policy_preserves_effortless_thinking_beside_enable_alias() -> None:
    thinking: dict[str, JsonValue] = {"type": "adaptive", "budget_tokens": 2048}
    payload: dict[str, JsonValue] = {
        "reasoning": {"effort": "low"},
        "thinking": thinking,
        "enable_thinking": True,
    }

    normalize_source_reasoning_aliases(payload)

    assert payload == {"reasoning": {"effort": "low"}, "thinking": thinking}


def test_source_reasoning_policy_strips_blank_effort_from_preserved_thinking() -> None:
    payload: dict[str, JsonValue] = {
        "reasoning": {"effort": "low"},
        "thinking": {"effort": " ", "type": "adaptive", "vendor_hint": "keep"},
    }

    normalize_source_reasoning_aliases(payload)

    assert payload == {
        "reasoning": {"effort": "low"},
        "thinking": {"type": "adaptive", "vendor_hint": "keep"},
    }


@pytest.mark.parametrize("thinking", [False, {"type": "disabled"}, {"enabled": False}])
def test_source_reasoning_policy_drops_inactive_thinking_beside_enable_alias(thinking: JsonValue) -> None:
    payload: dict[str, JsonValue] = {"thinking": thinking, "enable_thinking": True}

    normalize_source_reasoning_aliases(payload)

    assert payload == {"reasoning": {"effort": "medium"}}


def test_source_chat_reasoning_policy_materializes_model_alias_effort_for_canonical_source() -> None:
    payload: dict[str, JsonValue] = {}

    apply_api_key_enforcement_to_chat_payload(
        payload,
        None,
        allowed_reasoning_effort="xhigh",
        materialize_allowed_reasoning_effort=True,
    )

    assert payload == {"reasoning_effort": "xhigh"}


def _responses_request_with_input(input_value: object) -> ResponsesRequest:
    return ResponsesRequest.model_validate({"model": "gpt-5", "instructions": "", "input": input_value})


def test_source_route_excluded_is_false_for_plain_turns() -> None:
    request = _responses_request_with_input([{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}])

    assert responses_source_route_excluded(request) is False


@pytest.mark.parametrize(
    "previous_response_id",
    [
        "resp_0ba42212936dca97016a0d52aec2588191bc2499d3088e4e3e",
        "resp_source_owned_continuation",
    ],
)
def test_source_route_excluded_does_not_infer_previous_response_ownership(previous_response_id: str) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "previous_response_id": previous_response_id,
        }
    )

    assert responses_source_route_excluded(request) is False


def test_source_route_excluded_is_false_for_blank_previous_response_id() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "previous_response_id": "   ",
        }
    )

    assert request.previous_response_id is None
    assert responses_source_route_excluded(request) is False


def test_source_route_excluded_for_input_file_references() -> None:
    request = _responses_request_with_input(
        [{"role": "user", "content": [{"type": "input_file", "file_id": "file_123"}]}]
    )

    assert responses_source_route_excluded(request) is True


def test_source_route_excluded_for_terminal_compaction_trigger() -> None:
    request = _responses_request_with_input(
        [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "compaction_trigger"},
        ]
    )

    assert responses_source_route_excluded(request) is True
    assert responses_source_route_excluded(request, exclude_compaction=False) is False


def test_source_route_excluded_raises_for_malformed_compaction_trigger() -> None:
    request = _responses_request_with_input(
        [
            {"type": "compaction_trigger"},
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        ]
    )

    with pytest.raises(ClientPayloadError):
        responses_source_route_excluded(request)
