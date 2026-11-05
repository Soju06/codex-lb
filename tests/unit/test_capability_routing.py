from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.capability_lineage import CapabilityLineageAlias
from app.modules.proxy.capability_routing import (
    CAPABILITY_LINEAGE_UNAVAILABLE_CODE,
    CAPABILITY_SIGNAL_UNTRUSTED_CODE,
    REQUIRED_CAPABILITY_HEADER,
    UNSUPPORTED_REQUIRED_CAPABILITY_CODE,
    CapabilityRoute,
    CapabilityRouter,
    RoutingCapability,
    RoutingIntent,
    capability_lineage_aliases,
    parse_routing_intent,
    reject_capability_signal_outside_response_create,
    strip_capability_metadata,
)
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories

pytestmark = pytest.mark.unit


def _api_key() -> ApiKeyData:
    return ApiKeyData(
        id="api-key-a",
        name="test",
        automatic_daybreak_routing=True,
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        last_used_at=None,
    )


def _error_code(error: ProxyResponseError) -> str | None:
    return error.payload["error"].get("code")


def _repo_factory(repository: object) -> ProxyRepoFactory:
    @asynccontextmanager
    async def factory() -> AsyncIterator[ProxyRepositories]:
        yield cast(ProxyRepositories, SimpleNamespace(capability_lineage=repository))

    return factory


def test_parse_routing_intent_accepts_one_authenticated_carrier() -> None:
    assert parse_routing_intent(
        {"X-CoDeX-LB-ReQuIrEd-CaPaBiLiTy": "trusted_cyber"},
        api_key=_api_key(),
    ) == RoutingIntent.requiring(RoutingCapability.TRUSTED_CYBER)
    assert parse_routing_intent(
        {},
        api_key=_api_key(),
        client_metadata={"x-codex-lb-required-capability": "trusted_cyber"},
    ) == RoutingIntent.requiring(RoutingCapability.TRUSTED_CYBER)


def test_parse_routing_intent_rejects_ambiguous_carriers() -> None:
    with pytest.raises(ProxyResponseError) as exc_info:
        parse_routing_intent(
            {REQUIRED_CAPABILITY_HEADER: "trusted_cyber"},
            api_key=_api_key(),
            client_metadata={REQUIRED_CAPABILITY_HEADER: "trusted_cyber"},
        )

    assert exc_info.value.status_code == 400
    assert _error_code(exc_info.value) == UNSUPPORTED_REQUIRED_CAPABILITY_CODE


def test_parse_routing_intent_rejects_duplicate_raw_header_values() -> None:
    with pytest.raises(ProxyResponseError) as exc_info:
        parse_routing_intent(
            {REQUIRED_CAPABILITY_HEADER: "trusted_cyber"},
            api_key=_api_key(),
            header_values=("trusted_cyber", "trusted_cyber"),
        )

    assert exc_info.value.status_code == 400
    assert _error_code(exc_info.value) == UNSUPPORTED_REQUIRED_CAPABILITY_CODE


@pytest.mark.parametrize(
    "value",
    ["other", "TRUSTED_CYBER", " trusted_cyber ", True, ["trusted_cyber"]],
)
def test_parse_routing_intent_rejects_unknown_or_malformed_values(value: JsonValue) -> None:
    with pytest.raises(ProxyResponseError) as exc_info:
        parse_routing_intent(
            {},
            api_key=_api_key(),
            client_metadata={REQUIRED_CAPABILITY_HEADER: value},
        )

    assert exc_info.value.status_code == 400
    assert _error_code(exc_info.value) == UNSUPPORTED_REQUIRED_CAPABILITY_CODE


def test_parse_routing_intent_rejects_unauthenticated_signal() -> None:
    with pytest.raises(ProxyResponseError) as exc_info:
        parse_routing_intent(
            {REQUIRED_CAPABILITY_HEADER: "trusted_cyber"},
            api_key=None,
        )

    assert exc_info.value.status_code == 403
    assert _error_code(exc_info.value) == CAPABILITY_SIGNAL_UNTRUSTED_CODE


def test_strip_capability_metadata_removes_only_internal_key() -> None:
    metadata = {
        "X-Codex-LB-Required-Capability": "trusted_cyber",
        "x-codex-window-id": "window-1",
        "custom": {"nested": True},
    }

    assert strip_capability_metadata(metadata) == {
        "x-codex-window-id": "window-1",
        "custom": {"nested": True},
    }
    assert metadata["X-Codex-LB-Required-Capability"] == "trusted_cyber"


def test_capability_lineage_aliases_use_authoritative_conversation():
    assert capability_lineage_aliases(
        {"x-codex-parent-thread-id": "task", "x-codex-window-id": "task:1", "thread-id": "thread"},
        session_id="shared-process",
        turn_state="shared-turn",
        previous_response_ids=("response",),
        client_metadata={"x-codex-window-id": "task:2"},
    ) == (
        CapabilityLineageAlias(kind="previous_response", value="response"),
        CapabilityLineageAlias(kind="codex_window", value="task:2"),
    )


def test_capability_lineage_aliases_use_thread_before_shared_process():
    assert capability_lineage_aliases(
        {"thread-id": "thread", "x-codex-parent-thread-id": "parent"}, session_id="process", turn_state="turn"
    ) == (CapabilityLineageAlias(kind="codex_thread", value="thread"),)


def test_capability_lineage_aliases_preserve_fallback_without_conversation():
    assert capability_lineage_aliases(
        {"x-codex-parent-thread-id": "parent"},
        session_id="process",
        turn_state="turn",
        previous_response_ids=("response",),
    ) == (
        CapabilityLineageAlias(kind="session_header", value="process"),
        CapabilityLineageAlias(kind="turn_state", value="turn"),
        CapabilityLineageAlias(kind="previous_response", value="response"),
    )


@pytest.mark.asyncio
async def test_capability_router_persists_explicit_intent_before_returning_required() -> None:
    repository = SimpleNamespace(is_required=AsyncMock(return_value=False), require=AsyncMock(return_value=("hash",)))
    router = CapabilityRouter(_repo_factory(repository))
    aliases = (CapabilityLineageAlias(kind="session_header", value="session-a"),)

    route = await router.route(
        RoutingIntent.requiring(RoutingCapability.TRUSTED_CYBER),
        api_key_id="api-key-a",
        aliases=aliases,
    )

    assert route == CapabilityRoute(require_security_work_authorized=True, aliases=aliases)
    repository.require.assert_awaited_once_with(
        capability="trusted_cyber",
        api_key_scope="api-key-a",
        aliases=aliases,
    )


@pytest.mark.asyncio
async def test_capability_router_restores_inherited_requirement_and_fails_closed_on_lookup_error() -> None:
    aliases = (CapabilityLineageAlias(kind="session_header", value="session-a"),)
    repository = SimpleNamespace(is_required=AsyncMock(return_value=True), require=AsyncMock(return_value=("hash",)))
    router = CapabilityRouter(_repo_factory(repository))

    route = await router.route(RoutingIntent.empty(), api_key_id="api-key-a", aliases=aliases)

    assert route.require_security_work_authorized is True
    repository.require.assert_awaited_once()

    repository.is_required.side_effect = RuntimeError("database unavailable")
    with pytest.raises(ProxyResponseError) as exc_info:
        await router.route(RoutingIntent.empty(), api_key_id="api-key-a", aliases=aliases)

    assert exc_info.value.status_code == 503
    assert _error_code(exc_info.value) == CAPABILITY_LINEAGE_UNAVAILABLE_CODE


@pytest.mark.parametrize(
    "payload,required",
    [
        ({}, False),
        ({"access_programs": {}}, False),
        ({"access_programs": {"cyber": "standard"}}, False),
        ({"model": "gpt-daybreak-blue-latest"}, True),
        ({"model": "gpt-6-sol", "access_programs": {"cyber": "daybreak_blue"}}, True),
    ],
)
@pytest.mark.parametrize("enabled", [False, True])
def test_automatic_daybreak_routing_is_per_key(payload: dict[str, JsonValue], required: bool, enabled: bool) -> None:
    original = deepcopy(payload)
    key = replace(_api_key(), automatic_daybreak_routing=enabled)
    assert parse_routing_intent({}, api_key=key, request_payload=payload).requires_trusted_cyber == (
        enabled and required
    )
    assert payload == original
    # Matching native/custom intent agrees, even when automatic recognition is off.
    if required:
        assert parse_routing_intent(
            {REQUIRED_CAPABILITY_HEADER: "trusted_cyber"}, api_key=key, request_payload=payload
        ).requires_trusted_cyber


@pytest.mark.parametrize(
    "program",
    [
        None,
        True,
        [],
        "daybreak_blue",
        {"other": True},
        {"cyber": None},
        {"cyber": True},
        {"cyber": []},
        {"cyber": "DAYBREAK_BLUE"},
        {"cyber": "daybreak_red"},
        {"cyber": "unknown"},
    ],
)
def test_native_malformed_or_unsupported_program_fails_closed(program: JsonValue) -> None:
    with pytest.raises(ProxyResponseError) as exc:
        parse_routing_intent({}, api_key=_api_key(), request_payload={"access_programs": program})
    assert exc.value.status_code == 400
    assert _error_code(exc.value) == "unsupported_access_program"


def test_native_program_without_a_key_has_no_per_key_opt_in() -> None:
    assert (
        parse_routing_intent({}, api_key=None, request_payload={"access_programs": {"cyber": "daybreak_blue"}})
        == RoutingIntent.empty()
    )


def test_explicit_standard_conflicts_with_custom_required_intent() -> None:
    with pytest.raises(ProxyResponseError) as exc:
        parse_routing_intent(
            {REQUIRED_CAPABILITY_HEADER: "trusted_cyber"},
            api_key=_api_key(),
            request_payload={"access_programs": {"cyber": "standard"}},
        )
    assert _error_code(exc.value) == "unsupported_access_program"


def test_disabled_automatic_routing_preserves_existing_custom_carrier() -> None:
    disabled = replace(_api_key(), automatic_daybreak_routing=False)
    assert parse_routing_intent(
        {REQUIRED_CAPABILITY_HEADER: "trusted_cyber"},
        api_key=disabled,
        request_payload={"access_programs": {"cyber": "standard"}},
    ).requires_trusted_cyber


def test_enforced_model_does_not_create_control_frame_intent() -> None:
    key = replace(_api_key(), enforced_model="gpt-daybreak-blue-latest")
    reject_capability_signal_outside_response_create(
        api_key=key, client_metadata=None, request_payload={"type": "session.update"}
    )


def test_websocket_inferred_prefix_anchor_stays_with_its_conversation():
    from app.core.openai.requests import ResponsesRequest
    from app.modules.proxy import service as proxy_service

    conversation = CapabilityLineageAlias(kind="codex_window", value="task:2")
    prefix = [{"role": "user", "content": "shared prefix"}]
    completed = proxy_service._WebSocketRequestState(
        request_id="completed",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        input_item_count=1,
        input_full_fingerprint=proxy_service._fingerprint_input_items(prefix),
        conversation_alias=conversation,
    )
    continuity = proxy_service._WebSocketContinuityState()
    proxy_service._record_websocket_continuity_completion(
        continuity, request_state=completed, response_id="resp_completed"
    )
    payload = ResponsesRequest(model="gpt-5.1", instructions="", input=[*prefix, {"role": "user", "content": "next"}])
    same = proxy_service._websocket_continuity_anchor_for_payload(
        continuity, responses_payload=payload, codex_session_affinity=True, conversation_alias=conversation
    )
    assert same is not None and same.previous_response_id == "resp_completed"
    assert (
        proxy_service._websocket_continuity_anchor_for_payload(
            continuity,
            responses_payload=payload,
            codex_session_affinity=True,
            conversation_alias=CapabilityLineageAlias(kind="codex_window", value="task:1"),
        )
        is None
    )


@pytest.mark.parametrize("compact", [False, True])
def test_required_routing_keeps_disabled_stickiness_disabled(compact):
    from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
    from app.modules.proxy._service.compact import _sticky_key_for_compact_request
    from app.modules.proxy.affinity import _sticky_key_for_responses_request

    headers = {"x-codex-window-id": "task:2", "session_id": "shared-process"}
    if compact:
        policy = _sticky_key_for_compact_request(
            ResponsesCompactRequest(model="gpt-5.1", instructions="", input="fresh", prompt_cache_key="shared-cache"),
            headers,
            codex_session_affinity=False,
            openai_cache_affinity=False,
            openai_cache_affinity_max_age_seconds=1800,
            sticky_threads_enabled=False,
            require_trusted_cyber=True,
        )
    else:
        policy = _sticky_key_for_responses_request(
            ResponsesRequest(model="gpt-5.1", instructions="", input="fresh", prompt_cache_key="shared-cache"),
            headers,
            codex_session_affinity=False,
            openai_cache_affinity=False,
            openai_cache_affinity_max_age_seconds=1800,
            sticky_threads_enabled=False,
            require_trusted_cyber=True,
        )
    assert policy.key is None
    assert policy.kind is None
