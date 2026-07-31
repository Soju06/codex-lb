from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    strip_capability_metadata,
)
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories

pytestmark = pytest.mark.unit


def _api_key() -> ApiKeyData:
    return ApiKeyData(
        id="api-key-a",
        name="test",
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


def test_capability_lineage_aliases_preserve_domains_and_stable_task_root() -> None:
    aliases = capability_lineage_aliases(
        {
            "x-codex-parent-thread-id": "task-parent",
            "x-codex-window-id": "task-root:2",
        },
        session_id="same-visible-id",
        turn_state="same-visible-id",
        previous_response_ids=("resp-1",),
        client_metadata={
            "x-codex-parent-thread-id": "task-root",
            "x-codex-window-id": "task-metadata:3",
        },
    )

    assert aliases == (
        CapabilityLineageAlias(kind="session_header", value="same-visible-id"),
        CapabilityLineageAlias(kind="turn_state", value="same-visible-id"),
        CapabilityLineageAlias(kind="previous_response", value="resp-1"),
        CapabilityLineageAlias(kind="codex_task", value="task-parent"),
        CapabilityLineageAlias(kind="codex_task", value="task-root"),
        CapabilityLineageAlias(kind="codex_window", value="task-root:2"),
        CapabilityLineageAlias(kind="codex_window", value="task-metadata:3"),
        CapabilityLineageAlias(kind="codex_task", value="task-metadata"),
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
