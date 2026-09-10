"""Pure helpers behind the subscription-overflow designation (#2123 WP-B)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.core.exceptions import DashboardBadRequestError
from app.core.openai.model_registry import (
    MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
    MODEL_SOURCE_KIND_SUBSCRIPTION,
    UpstreamModel,
)
from app.db.models import ModelSource, ModelSourceModel
from app.modules.settings.subscription_overflow import (
    BLOCKER_SOURCE_KIND_UNSUPPORTED,
    BLOCKER_SOURCE_RESPONSES_UNSUPPORTED,
    CODEX_TOOL_TYPES,
    DRAIN_WINDOW,
    NEVER_OVERFLOWS_CODE_MODE_ONLY,
    NEVER_OVERFLOWS_NOT_IN_REGISTRY,
    NEVER_OVERFLOWS_RESPONSES_LITE,
    PIN_IDLE_TTL,
    PIN_TOMBSTONE_GRACE,
    SUBSCRIPTION_OVERFLOW_SOURCE_INVALID,
    WARNING_CONTEXT_WINDOW_MISSING,
    WARNING_CONTEXT_WINDOW_SMALLER,
    WARNING_NO_STREAMING,
    WARNING_NO_VISION,
    WARNING_NOT_IN_REGISTRY,
    WARNING_RESPONSES_LITE_EXCLUDED,
    WARNING_UNDECLARED_TOOL_TYPES,
    WARNING_UNPRICED,
    build_preflight,
    never_overflows_reason,
    resolve_drain_until,
    resolve_pins_expire_by,
    validate_overflow_source,
)

NOW = datetime(2026, 9, 8, 12, 0, 0)
PENDING_DRAIN = datetime(2026, 9, 20, 0, 0, 0)


def test_drain_window_is_pin_idle_ttl_plus_tombstone_grace_plus_one_day() -> None:
    assert PIN_IDLE_TTL == timedelta(days=7)
    assert PIN_TOMBSTONE_GRACE == timedelta(days=21)
    assert DRAIN_WINDOW == timedelta(days=29)


@pytest.mark.parametrize(
    ("current_source", "new_source", "current_drain", "expected"),
    [
        # non-NULL -> NULL arms the drain deadline in the same write.
        ("src_a", None, None, NOW + DRAIN_WINDOW),
        # NULL -> non-NULL clears a pending deadline (re-enable during drain).
        (None, "src_a", PENDING_DRAIN, None),
        (None, "src_a", None, None),
        # NULL -> NULL never touches the deadline, armed or not.
        (None, None, PENDING_DRAIN, PENDING_DRAIN),
        (None, None, None, None),
        # non-NULL -> a different non-NULL source never touches the deadline.
        ("src_a", "src_b", None, None),
        ("src_a", "src_a", None, None),
    ],
)
def test_resolve_drain_until_transitions(current_source, new_source, current_drain, expected) -> None:
    assert resolve_drain_until(current_source, new_source, current_drain, NOW) == expected


def test_resolve_pins_expire_by_is_the_clear_time_plus_the_pin_idle_ttl() -> None:
    # Drain cap (design §3, §8.8): every pin written before or during the drain
    # carries ``expires_at <= drain_until - PIN_TOMBSTONE_GRACE - 1 d``, so all
    # pinned conversations are gone 7 days after the clear -- 22 days before the
    # lookup window itself closes. The dashboard shows this date, not the deadline.
    drain_until = resolve_drain_until("src_a", None, None, NOW)
    # ``is not None`` narrows ``datetime | None`` for the type checker (an ``==``
    # assertion does not) so the arithmetic below type-checks under ``ty``.
    assert drain_until is not None
    assert drain_until == NOW + DRAIN_WINDOW
    assert resolve_pins_expire_by(drain_until) == NOW + PIN_IDLE_TTL
    assert resolve_pins_expire_by(drain_until) == drain_until - PIN_TOMBSTONE_GRACE - timedelta(days=1)
    assert resolve_pins_expire_by(None) is None


def _source(
    *,
    kind: str = MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
    supports_responses: bool = True,
    is_enabled: bool = True,
    models: list[ModelSourceModel] | None = None,
) -> ModelSource:
    return ModelSource(
        id="src_test",
        name="Test source",
        kind=kind,
        base_url="http://127.0.0.1:9/v1",
        is_enabled=is_enabled,
        health_status="unknown",
        supports_chat_completions=True,
        supports_responses=supports_responses,
        supports_audio_transcriptions=False,
        supports_embeddings=False,
        models=models or [],
    )


def _entry(
    model: str,
    *,
    is_enabled: bool = True,
    context_window: int | None = 400_000,
    max_output_tokens: int | None = None,
    supports_streaming: bool = True,
    supports_vision: bool = True,
    input_per_1m: float | None = 1.0,
    output_per_1m: float | None = 2.0,
    raw_metadata_json: str | None = None,
) -> ModelSourceModel:
    return ModelSourceModel(
        source_id="src_test",
        model=model,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_streaming=supports_streaming,
        supports_tools=True,
        supports_vision=supports_vision,
        input_per_1m=input_per_1m,
        output_per_1m=output_per_1m,
        raw_metadata_json=raw_metadata_json,
        is_enabled=is_enabled,
    )


def _registry_model(
    slug: str,
    *,
    context_window: int = 272_000,
    raw: dict | None = None,
    source_kind: str = MODEL_SOURCE_KIND_SUBSCRIPTION,
) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=slug,
        context_window=context_window,
        input_modalities=("text", "image"),
        supported_reasoning_levels=(),
        default_reasoning_level=None,
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus"}),
        source_kind=source_kind,
        raw=raw or {},
    )


def test_validate_overflow_source_accepts_responses_capable_openai_compatible_sources() -> None:
    validate_overflow_source(_source())
    # Enabled-ness is a kill switch, not an eligibility rule.
    validate_overflow_source(_source(is_enabled=False))


@pytest.mark.parametrize(
    "source",
    [
        None,
        _source(supports_responses=False),
        _source(kind="anthropic"),
        _source(kind="anthropic", supports_responses=False),
    ],
)
def test_validate_overflow_source_rejects_ineligible_sources(source) -> None:
    with pytest.raises(DashboardBadRequestError) as exc_info:
        validate_overflow_source(source)
    assert exc_info.value.code == SUBSCRIPTION_OVERFLOW_SOURCE_INVALID
    assert exc_info.value.status_code == 400


def test_never_overflows_reason_flags_lite_code_mode_and_unknown_slugs() -> None:
    assert never_overflows_reason(None) == NEVER_OVERFLOWS_NOT_IN_REGISTRY
    assert never_overflows_reason(_registry_model("gpt-5.6", raw={"use_responses_lite": True})) == (
        NEVER_OVERFLOWS_RESPONSES_LITE
    )
    assert never_overflows_reason(_registry_model("gpt-5.6", raw={"tool_mode": "code_mode_only"})) == (
        NEVER_OVERFLOWS_CODE_MODE_ONLY
    )
    assert never_overflows_reason(_registry_model("gpt-5.1")) is None


def _preflight(source: ModelSource, registry: dict[str, UpstreamModel], **counts):
    return build_preflight(
        source,
        registry_models=registry,
        scoped_api_key_count=counts.get("scoped_api_key_count", 0),
        live_pin_count=counts.get("live_pin_count", 0),
        tombstone_count=counts.get("tombstone_count", 0),
        drain_until=counts.get("drain_until"),
    )


def test_build_preflight_reports_blockers_without_hiding_the_rest_of_the_report() -> None:
    source = _source(kind="anthropic", supports_responses=False, models=[_entry("gpt-5.1")])
    preflight = _preflight(source, {"gpt-5.1": _registry_model("gpt-5.1")}, scoped_api_key_count=2)

    assert preflight.eligible is False
    assert preflight.blockers == [BLOCKER_SOURCE_KIND_UNSUPPORTED, BLOCKER_SOURCE_RESPONSES_UNSUPPORTED]
    assert [model.slug for model in preflight.served_models] == ["gpt-5.1"]
    assert preflight.scoped_api_key_count == 2


def test_build_preflight_lists_missing_overflowable_registry_slugs_only() -> None:
    registry = {
        "gpt-5.1": _registry_model("gpt-5.1"),
        "gpt-5.4": _registry_model("gpt-5.4"),
        "gpt-5.6": _registry_model("gpt-5.6", raw={"use_responses_lite": True, "tool_mode": "code_mode_only"}),
        # Source-owned catalog entries are not subscription slugs.
        "local-coder": _registry_model("local-coder", source_kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE),
    }
    source = _source(models=[_entry("gpt-5.1"), _entry("gpt-5.4", is_enabled=False)])
    preflight = _preflight(source, registry)

    assert preflight.eligible is True
    assert preflight.blockers == []
    # gpt-5.4 is listed but disabled -> missing; gpt-5.6 can never overflow -> not missing.
    assert preflight.missing_models == ["gpt-5.4"]
    assert [(model.slug, model.enabled) for model in preflight.served_models] == [("gpt-5.1", True), ("gpt-5.4", False)]


def test_build_preflight_counts_a_differently_cased_entry_as_serving_the_registry_slug() -> None:
    registry = {"gpt-5.1": _registry_model("gpt-5.1"), "gpt-5.4": _registry_model("gpt-5.4")}
    source = _source(models=[_entry("gpt-5.1"), _entry("GPT-5.4")])
    preflight = _preflight(source, registry)

    # served_models resolves GPT-5.4 case-insensitively (as the request path
    # does); missing_models must agree instead of listing gpt-5.4 as missing.
    served = {model.slug: model.never_overflows for model in preflight.served_models}
    assert served == {"GPT-5.4": False, "gpt-5.1": False}
    assert preflight.missing_models == []


def test_build_preflight_marks_lite_family_and_unknown_slugs_as_never_overflowing() -> None:
    registry = {"gpt-5.6": _registry_model("gpt-5.6", raw={"use_responses_lite": True, "tool_mode": "code_mode_only"})}
    source = _source(models=[_entry("gpt-5.6", context_window=8_192, supports_vision=False), _entry("qwen-local")])
    preflight = _preflight(source, registry)

    lite, unknown = preflight.served_models
    assert (lite.slug, lite.never_overflows, lite.never_overflows_reason) == (
        "gpt-5.6",
        True,
        NEVER_OVERFLOWS_RESPONSES_LITE,
    )
    # Exclusion is the only warning worth showing for a model that never overflows.
    assert lite.warnings == [WARNING_RESPONSES_LITE_EXCLUDED]
    assert lite.context_window_mismatch is None
    assert (unknown.slug, unknown.never_overflows_reason, unknown.warnings) == (
        "qwen-local",
        NEVER_OVERFLOWS_NOT_IN_REGISTRY,
        [WARNING_NOT_IN_REGISTRY],
    )


def test_build_preflight_warns_on_tools_vision_streaming_pricing_and_context_window() -> None:
    registry = {"gpt-5.1": _registry_model("gpt-5.1", context_window=272_000)}
    entry = _entry(
        "gpt-5.1",
        context_window=8_192,
        max_output_tokens=1_024,
        supports_streaming=False,
        supports_vision=False,
        input_per_1m=1.0,
        output_per_1m=None,
        raw_metadata_json='{"supports_search_tool": true, "experimental_supported_tools": ["custom", "apply_patch"]}',
    )
    preflight = _preflight(_source(models=[entry]), registry)

    (model,) = preflight.served_models
    assert model.never_overflows is False
    assert model.undeclared_tool_types == sorted(CODEX_TOOL_TYPES - {"web_search", "custom", "apply_patch"})
    assert model.priced is False
    assert model.context_window_mismatch is not None
    assert (
        model.context_window_mismatch.registry,
        model.context_window_mismatch.source,
        model.context_window_mismatch.max_output_tokens,
    ) == (272_000, 8_192, 1_024)
    assert model.warnings == [
        WARNING_UNDECLARED_TOOL_TYPES,
        WARNING_NO_VISION,
        WARNING_NO_STREAMING,
        WARNING_UNPRICED,
        WARNING_CONTEXT_WINDOW_SMALLER,
    ]


def test_build_preflight_clean_model_has_no_warnings_and_missing_window_is_reported() -> None:
    registry = {"gpt-5.1": _registry_model("gpt-5.1", context_window=272_000)}
    all_tools = json.dumps(
        {
            "supports_search_tool": True,
            "experimental_supported_tools": ["custom", "apply_patch", "shell", "local_shell", "tool_search"],
        }
    )
    clean = _entry("gpt-5.1", raw_metadata_json=all_tools)
    preflight = _preflight(_source(models=[clean]), registry, live_pin_count=3, tombstone_count=1, drain_until=NOW)
    (model,) = preflight.served_models
    assert model.warnings == []
    assert model.undeclared_tool_types == []
    assert model.context_window_mismatch is None
    assert (preflight.live_pin_count, preflight.tombstone_count, preflight.drain_until) == (3, 1, NOW)

    missing_window = _entry("gpt-5.1", context_window=None, raw_metadata_json=all_tools)
    (model,) = _preflight(_source(models=[missing_window]), registry).served_models
    assert model.warnings == [WARNING_CONTEXT_WINDOW_MISSING]
    assert model.context_window_mismatch is not None
    assert model.context_window_mismatch.source is None
