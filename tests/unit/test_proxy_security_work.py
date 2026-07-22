from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from fastapi import WebSocket

from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.db.models import HttpBridgeSessionState, StickySession
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.support import _websocket_should_defer_reasoning_prelude
from app.modules.proxy.affinity import _sticky_key_from_session_header
from tests.unit.test_proxy_http_bridge import _make_app_settings, _make_bridge_session
from tests.unit.test_proxy_utils import (
    _make_account,
    _make_proxy_settings,
    _QueuedTestUpstreamWebSocket,
    _repo_factory,
    _RequestLogsRecorder,
    _SettingsCache,
)

pytestmark = pytest.mark.unit


def test_http_bridge_buffers_entire_reasoning_prelude_before_security_decision() -> None:
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-security-multi-reasoning",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        awaiting_response_created=False,
        response_id="resp-security-multi-reasoning",
        response_event_count=2,
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
        upstream_model_output_seen=True,
        deferred_reasoning_downstream_texts=['data: {"type":"response.output_item.added"}\n\n'],
    )

    assert _websocket_should_defer_reasoning_prelude(
        request_state,
        event_type="response.output_item.done",
        payload={"item": {"type": "reasoning"}},
    )


@pytest.mark.parametrize(
    "message",
    [
        (
            "This chat was flagged for possible cybersecurity risk. "
            "To get authorized for security work, join the Trusted Access for Cyber program. "
            "https://chatgpt.com/cyber"
        ),
        (
            "ⓘ This content can't be shown\n"
            "We take extra caution with cybersecurity requests. If you’re a security professional, "
            "you may be able to apply for Trusted Access.\n"
            "Trusted Access: https://openai.com/form/enterprise-trusted-access-for-cyber/"
        ),
    ],
)
def test_security_work_denial_classifier_accepts_upstream_variants(message: str) -> None:
    assert proxy_service._is_security_work_authorization_required_error("invalid_request_error", message)


def test_security_work_denial_classifier_accepts_literal_cyber_policy_code() -> None:
    assert proxy_service._is_security_work_authorization_required_error("cyber_policy", None)


def test_security_lineage_uses_codex_root_session_before_parent_or_turn_state() -> None:
    headers = {
        "session-id": "root-session",
        "x-codex-parent-thread-id": "root-parent",
        "thread-id": "child-thread",
        "x-codex-turn-state": "child-turn-state",
    }

    assert _sticky_key_from_session_header(headers) == "root-session"
    assert (
        _sticky_key_from_session_header({"x-codex-parent-thread-id": "root-parent", "thread-id": "child-thread"})
        == "root-parent"
    )


@pytest.mark.asyncio
async def test_security_lineage_persists_root_requirement_for_child_turn_without_poisoning_unrelated_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_id = "019f4da2-a220-72c2-8807-f02f2237dd2f"
    ordinary_account = _make_account("acc-lineage-ordinary")
    trusted_account = _make_account("acc-lineage-trusted")
    trusted_account.security_work_authorized = True

    class _StickyLineageRepository:
        def __init__(self) -> None:
            self.entries: dict[str, StickySession] = {}

        async def get_entry(self, key: str, *, kind: proxy_service.StickySessionKind):
            assert kind == proxy_service.StickySessionKind.CODEX_SESSION
            return self.entries.get(key)

        async def upsert(
            self,
            key: str,
            account_id: str | None,
            *,
            kind: proxy_service.StickySessionKind,
            requires_security_work_authorized: bool = False,
        ):
            prior = self.entries.get(key)
            self.entries[key] = StickySession(
                key=key,
                kind=kind,
                account_id=account_id,
                requires_security_work_authorized=(
                    requires_security_work_authorized
                    or (prior.requires_security_work_authorized if prior is not None else False)
                ),
            )
            return self.entries[key]

    sticky_repo = _StickyLineageRepository()

    class _RepoContext:
        async def __aenter__(self):
            return SimpleNamespace(sticky_sessions=sticky_repo)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = proxy_service.ProxyService(lambda: _RepoContext())
    settings = _make_app_settings()
    select_account = AsyncMock(
        side_effect=[
            proxy_service.AccountSelection(account=trusted_account, error_message=None, error_code=None),
            proxy_service.AccountSelection(account=ordinary_account, error_message=None, error_code=None),
        ]
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=settings)),
    )
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)

    # A cyber-policy denial on the ordinary root persists before retry. The
    # child has a different turn-state but shares session-id=root.
    await service._mark_security_lineage_requirement(root_id, account_id=ordinary_account.id)
    child_selection = await service._select_account_with_budget(
        time.monotonic() + 10,
        request_id="child-turn",
        kind="websocket",
        sticky_key="child-turn-state",
        sticky_kind=proxy_service.StickySessionKind.CODEX_SESSION,
        security_lineage_id=root_id,
    )

    assert child_selection.account is trusted_account
    assert select_account.await_args_list[0].kwargs["require_security_work_authorized"] is True
    root_entry = sticky_repo.entries[root_id]
    assert root_entry.account_id == trusted_account.id
    assert root_entry.requires_security_work_authorized is True

    unrelated_selection = await service._select_account_with_budget(
        time.monotonic() + 10,
        request_id="unrelated-turn",
        kind="websocket",
        sticky_key="unrelated-turn-state",
        sticky_kind=proxy_service.StickySessionKind.CODEX_SESSION,
        security_lineage_id="unrelated-root",
    )

    assert unrelated_selection.account is ordinary_account
    assert select_account.await_args_list[1].kwargs["require_security_work_authorized"] is False
    assert "unrelated-root" not in sticky_repo.entries


@pytest.mark.asyncio
async def test_security_lineage_marker_does_not_migrate_account_scoped_file_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StickyLineageRepository:
        def __init__(self) -> None:
            self.entries: dict[str, StickySession] = {}

        async def get_entry(self, key: str, *, kind: proxy_service.StickySessionKind):
            assert kind == proxy_service.StickySessionKind.CODEX_SESSION
            return self.entries.get(key)

        async def upsert(
            self,
            key: str,
            account_id: str | None,
            *,
            kind: proxy_service.StickySessionKind,
            requires_security_work_authorized: bool = False,
        ):
            prior = self.entries.get(key)
            self.entries[key] = StickySession(
                key=key,
                kind=kind,
                account_id=account_id,
                requires_security_work_authorized=(
                    requires_security_work_authorized
                    or (prior.requires_security_work_authorized if prior is not None else False)
                ),
            )
            return self.entries[key]

    sticky_repo = _StickyLineageRepository()

    class _RepoContext:
        async def __aenter__(self):
            return SimpleNamespace(sticky_sessions=sticky_repo)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = proxy_service.ProxyService(lambda: _RepoContext())
    settings = _make_app_settings()
    ordinary_account = _make_account("acc_file_owner_after_classified_root")
    trusted_account = _make_account("acc_file_trusted_after_classified_root")
    trusted_account.security_work_authorized = True
    select_account = AsyncMock(
        return_value=proxy_service.AccountSelection(account=ordinary_account, error_message=None, error_code=None)
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=settings)),
    )
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)

    await service._mark_security_lineage_requirement(
        "root-with-file-request",
        account_id=trusted_account.id,
    )
    selection = await service._select_account_with_budget(
        time.monotonic() + 10,
        request_id="classified-file-request",
        kind="stream",
        sticky_key="root-with-file-request",
        sticky_kind=proxy_service.StickySessionKind.CODEX_SESSION,
        security_lineage_id="root-with-file-request",
        allow_security_lineage_account_migration=False,
    )

    assert selection.account is ordinary_account
    select_account_call = select_account.await_args
    assert select_account_call is not None
    assert select_account_call.kwargs["require_security_work_authorized"] is False


@pytest.mark.asyncio
async def test_approved_account_cyber_denial_persists_root_without_ordinary_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    trusted_account = _make_account("acc_ws_trusted_cyber_denial")
    trusted_account.security_work_authorized = True
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws-approved-cyber-denial",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        transport="websocket",
        request_text='{"type":"response.create","input":[]}',
        security_lineage_id="root-approved-cyber-denial",
    )
    upstream_control = proxy_service._WebSocketUpstreamControl()
    persist_requirement = AsyncMock()
    monkeypatch.setattr(service, "_mark_security_lineage_requirement", persist_requirement)

    await service._process_upstream_websocket_text(
        json.dumps(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp-approved-cyber-denial",
                    "status": "failed",
                    "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
                },
            },
            separators=(",", ":"),
        ),
        account=trusted_account,
        account_id_value=trusted_account.id,
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    persist_requirement.assert_awaited_once_with(
        "root-approved-cyber-denial",
        account_id=trusted_account.id,
        api_key_id=None,
    )
    assert upstream_control.replay_request_state is None
    assert upstream_control.reconnect_requested is True
    assert request_state.require_security_work_authorized is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("explicit_requirement", "request_requirement", "session_requirement", "expected_requirement"),
    [
        (True, False, False, True),
        (False, True, False, True),
        (False, False, True, True),
        (False, False, False, False),
    ],
)
async def test_http_bridge_reconnect_preserves_security_work_requirement(
    monkeypatch: pytest.MonkeyPatch,
    explicit_requirement: bool,
    request_requirement: bool,
    session_requirement: bool,
    expected_requirement: bool,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    regular_account = _make_account("acc_security_regular")
    authorized_account = _make_account("acc_security_authorized")
    authorized_account.security_work_authorized = True
    session = _make_bridge_session()
    session.account = regular_account
    session.upstream = cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock()))
    session.requires_security_work_authorized = session_requirement
    request_state = proxy_service._WebSocketRequestState(
        request_id="security_reconnect",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport="http",
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
        require_security_work_authorized=request_requirement,
    )
    selection = proxy_service.AccountSelection(account=authorized_account, error_message=None, error_code=None)
    select_account = AsyncMock(return_value=selection)
    new_upstream = cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(service, "_select_account_with_budget_for_stream", select_account)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=authorized_account))
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", AsyncMock(return_value=new_upstream))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy=None))
        ),
    )

    await service._reconnect_http_bridge_session(
        session,
        request_state=request_state,
        require_security_work_authorized=explicit_requirement,
    )

    select_args = select_account.await_args
    assert select_args is not None
    assert select_args.kwargs["require_security_work_authorized"] is expected_requirement
    assert request_state.require_security_work_authorized is expected_requirement
    assert session.requires_security_work_authorized is expected_requirement
    assert session.account is authorized_account
    assert session.upstream is new_upstream


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_security_lineage_id"),
    [
        ({"session_id": "security-create"}, "security-create"),
        ({}, None),
    ],
)
@pytest.mark.parametrize("allow_security_lineage_account_migration", [False, True])
async def test_http_bridge_create_passes_security_work_requirement_to_selection(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected_security_lineage_id: str | None,
    allow_security_lineage_account_migration: bool,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    authorized_account = _make_account("acc_security_create_authorized")
    authorized_account.security_work_authorized = True
    select_account = AsyncMock(
        return_value=proxy_service.AccountSelection(
            account=authorized_account,
            error_message=None,
            error_code=None,
        )
    )
    upstream = cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(service, "_select_account_with_budget_for_stream", select_account)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=authorized_account))
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", AsyncMock(return_value=upstream))
    monkeypatch.setattr(service, "_relay_http_bridge_upstream_messages", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy=None))
        ),
    )

    session = await service._create_http_bridge_session(
        proxy_service._HTTPBridgeSessionKey("session_header", "security-create", None),
        headers=headers,
        affinity=proxy_service._AffinityPolicy(
            key="security-create",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.6-sol",
        idle_ttl_seconds=120.0,
        require_security_work_authorized=True,
        allow_security_lineage_account_migration=allow_security_lineage_account_migration,
    )

    assert select_account.await_args is not None
    assert select_account.await_args.kwargs["require_security_work_authorized"] is True
    assert select_account.await_args.kwargs["security_lineage_id"] == expected_security_lineage_id
    assert (
        select_account.await_args.kwargs["allow_security_lineage_account_migration"]
        is allow_security_lineage_account_migration
    )
    assert session.upstream_reader is not None
    await session.upstream_reader


@pytest.mark.asyncio
async def test_http_bridge_stream_reads_sticky_security_requirement_without_durable_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    app_settings = _make_app_settings()
    dashboard_settings = SimpleNamespace(
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=1800,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
    )
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.6-sol", "instructions": "hi", "input": "continue"}
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="sticky-security-without-durable-lookup",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
    )
    assert request_state.event_queue is not None
    await request_state.event_queue.put(None)
    session = _make_bridge_session(key_value="sticky-security-root")
    sticky_requirement = AsyncMock(return_value=True)
    get_or_create = AsyncMock(return_value=session)

    def prepare_request(
        _payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
        client_ip: str | None = None,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id, client_ip
        request_state.security_lineage_id = "sticky-security-root"
        return request_state, '{"type":"response.create"}'

    monkeypatch.setattr(proxy_service, "get_settings", lambda: app_settings)
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=dashboard_settings)),
    )
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_security_lineage_requires_security_work_authorized", sticky_requirement)
    monkeypatch.setattr(service, "_prepare_http_bridge_request", prepare_request)
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"session_id": "sticky-security-root"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    sticky_requirement.assert_awaited_once_with("sticky-security-root", api_key_id=None)
    assert get_or_create.await_args is not None
    assert get_or_create.await_args.kwargs["durable_lookup"] is None
    assert get_or_create.await_args.kwargs["require_security_work_authorized"] is True


@pytest.mark.asyncio
async def test_http_bridge_security_classified_full_resend_drops_ordinary_owner_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    app_settings = _make_app_settings()
    dashboard_settings = SimpleNamespace(
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=1800,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
    )
    payload = proxy_service.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "hi",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "prior"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "security follow-up"}]},
            ],
        }
    )
    durable_lookup = proxy_service.DurableBridgeLookup(
        session_id="classified-full-resend",
        canonical_kind="session_header",
        canonical_key="classified-full-resend",
        api_key_scope="__anonymous__",
        account_id="acc-ordinary-denied-owner",
        owner_instance_id=None,
        owner_epoch=1,
        lease_expires_at=None,
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state="classified-full-resend",
        latest_response_id="resp-security-classified",
        latest_input_item_count=1,
        latest_input_full_fingerprint=None,
        model="gpt-5.6-sol",
        requires_security_work_authorized=True,
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="classified-full-resend",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
    )
    assert request_state.event_queue is not None
    await request_state.event_queue.put(None)
    session = _make_bridge_session(key_value="classified-full-resend")
    get_or_create = AsyncMock(return_value=session)

    def prepare_request(
        _payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
        client_ip: str | None = None,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id, client_ip
        request_state.security_lineage_id = "classified-full-resend"
        return request_state, '{"type":"response.create","input":["full resend"]}'

    monkeypatch.setattr(proxy_service, "get_settings", lambda: app_settings)
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=dashboard_settings)),
    )
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=durable_lookup))
    monkeypatch.setattr(service, "_http_bridge_has_live_local_session", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_http_bridge_can_forward_to_active_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_security_lineage_requires_security_work_authorized", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_prepare_http_bridge_request", prepare_request)
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", get_or_create)
    monkeypatch.setattr(service, "_submit_http_bridge_request", AsyncMock())
    monkeypatch.setattr(service, "_detach_http_bridge_request", AsyncMock())

    chunks = [
        chunk
        async for chunk in service._stream_via_http_bridge(
            payload,
            headers={"session_id": "classified-full-resend"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        )
    ]

    assert chunks == []
    assert get_or_create.await_args is not None
    assert get_or_create.await_args.kwargs["require_security_work_authorized"] is True
    assert get_or_create.await_args.kwargs["preferred_account_id"] is None
    assert get_or_create.await_args.kwargs["durable_lookup"] is durable_lookup
    assert request_state.preferred_account_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("require_security_work_authorized", [False, True])
async def test_previous_response_recovery_applies_security_account_gate(
    monkeypatch: pytest.MonkeyPatch,
    require_security_work_authorized: bool,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    current_key = proxy_service._HTTPBridgeSessionKey("session_header", "security-current", None)
    previous_key = proxy_service._HTTPBridgeSessionKey("session_header", "security-previous", None)
    previous_session = _make_bridge_session(key=previous_key)
    previous_session.account = _make_account("acc-security-previous-ordinary")
    previous_session.request_model = "gpt-5.6-sol"
    previous_session.previous_response_ids = {"resp-security-previous"}
    authorized_session = _make_bridge_session(key=current_key)
    authorized_session.account = _make_account("acc-security-created-authorized")
    authorized_session.account.security_work_authorized = True
    authorized_session.request_model = "gpt-5.6-sol"
    alias_key = proxy_service._http_bridge_previous_response_alias_key("resp-security-previous", None)
    service._http_bridge_sessions[previous_key] = previous_session
    service._http_bridge_previous_response_index[alias_key] = previous_key
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    create_http_bridge_session = AsyncMock(return_value=authorized_session)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_should_wait_for_registration", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        current_key,
        headers={"x-codex-session-id": "security-current"},
        affinity=proxy_service._AffinityPolicy(
            key="security-current",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.6-sol",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp-security-previous",
        allow_previous_response_recovery_rebind=True,
        require_security_work_authorized=require_security_work_authorized,
    )

    if require_security_work_authorized:
        assert resolved is authorized_session
        create_http_bridge_session.assert_awaited_once()
        assert alias_key not in service._http_bridge_previous_response_index
    else:
        assert resolved is previous_session
        create_http_bridge_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_bridge_authorized_cyber_denial_persists_durable_requirement_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    trusted_account = _make_account("acc-http-trusted-cyber-denial")
    trusted_account.security_work_authorized = True
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-approved-cyber-denial",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","input":[]}',
        transport="http",
        security_lineage_id="root-http-approved-cyber-denial",
        skip_request_log=True,
    )
    session = _make_bridge_session(pending_requests=deque([request_state]), queued_request_count=1)
    session.account = trusted_account
    session.durable_session_id = "durable-http-approved-cyber-denial"
    persist_lineage = AsyncMock()
    persist_durable = AsyncMock(return_value=SimpleNamespace(session_id=session.durable_session_id))
    retry_security_work = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_mark_security_lineage_requirement", persist_lineage)
    monkeypatch.setattr(service._durable_bridge, "require_security_work_authorized", persist_durable)
    monkeypatch.setattr(service, "_retry_http_bridge_security_work_request", retry_security_work)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp-http-approved-cyber-denial",
                    "status": "failed",
                    "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
                },
            },
            separators=(",", ":"),
        ),
    )

    persist_lineage.assert_awaited_once_with(
        "root-http-approved-cyber-denial",
        account_id=trusted_account.id,
        api_key_id=None,
    )
    persist_durable.assert_awaited_once_with(session_id="durable-http-approved-cyber-denial")
    retry_security_work.assert_not_awaited()
    assert request_state.require_security_work_authorized is True
    assert session.requires_security_work_authorized is True
    assert request_state.excluded_account_ids == set()
    assert request_state.event_queue is not None
    advisory_event = await request_state.event_queue.get()
    assert advisory_event is not None
    assert "forward_original_security_work_error" in advisory_event
    terminal_event = await request_state.event_queue.get()
    assert terminal_event is not None
    assert "cyber_policy" in terminal_event
    assert await request_state.event_queue.get() is None


@pytest.mark.asyncio
async def test_http_bridge_unapproved_cyber_denial_also_persists_durable_requirement_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    owner_account = _make_account("acc-http-unapproved-cyber-denial")
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-unapproved-cyber-denial",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        downstream_visible=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","input":[]}',
        transport="http",
        security_lineage_id="root-http-unapproved-cyber-denial",
        skip_request_log=True,
    )
    session = _make_bridge_session(pending_requests=deque([request_state]), queued_request_count=1)
    session.account = owner_account
    session.durable_session_id = "durable-http-unapproved-cyber-denial"
    persist_lineage = AsyncMock()
    persist_durable = AsyncMock(return_value=SimpleNamespace(session_id=session.durable_session_id))
    retry_security_work = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_mark_security_lineage_requirement", persist_lineage)
    monkeypatch.setattr(service._durable_bridge, "require_security_work_authorized", persist_durable)
    monkeypatch.setattr(service, "_retry_http_bridge_security_work_request", retry_security_work)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp-http-unapproved-cyber-denial",
                    "status": "failed",
                    "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
                },
            },
            separators=(",", ":"),
        ),
    )

    persist_lineage.assert_awaited_once_with(
        "root-http-unapproved-cyber-denial",
        account_id=owner_account.id,
        api_key_id=None,
    )
    persist_durable.assert_awaited_once_with(session_id="durable-http-unapproved-cyber-denial")
    retry_security_work.assert_not_awaited()
    assert request_state.require_security_work_authorized is True
    assert session.requires_security_work_authorized is True
    assert request_state.event_queue is not None
    advisory_event = await request_state.event_queue.get()
    assert advisory_event is not None
    assert "forward_original_security_work_error" in advisory_event
    terminal_event = await request_state.event_queue.get()
    assert terminal_event is not None
    assert "cyber_policy" in terminal_event
    assert await request_state.event_queue.get() is None


@pytest.mark.asyncio
async def test_http_bridge_reconnect_claims_durable_owner_before_publishing_account_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    old_account = _make_account("acc_durable_old")
    replacement_account = _make_account("acc_durable_replacement")
    old_upstream_close = AsyncMock()
    old_upstream = cast(UpstreamResponsesWebSocket, SimpleNamespace(close=old_upstream_close))
    replacement_upstream = cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock()))
    session = _make_bridge_session()
    session.account = old_account
    session.upstream = old_upstream
    session.durable_session_id = "durable-owner"
    session.durable_owner_epoch = 1
    request_state = proxy_service._WebSocketRequestState(
        request_id="durable-claim-order",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport="http",
    )
    select_account = AsyncMock(
        return_value=proxy_service.AccountSelection(
            account=replacement_account,
            error_message=None,
            error_code=None,
        )
    )
    claim_calls: list[str | None] = []

    async def claim_durable(
        claimed_session: proxy_service._HTTPBridgeSession,
        *,
        allow_takeover: bool,
        force_owner_epoch_advance: bool = False,
        claim_account_id: str | None = None,
    ) -> None:
        assert allow_takeover is True
        assert force_owner_epoch_advance is True
        assert claimed_session.account is old_account
        old_upstream_close.assert_not_awaited()
        claim_calls.append(claim_account_id)

    monkeypatch.setattr(service, "_select_account_with_budget_for_stream", select_account)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=replacement_account))
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", AsyncMock(return_value=replacement_upstream))
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", claim_durable)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy=None))
        ),
    )

    await service._reconnect_http_bridge_session(session, request_state=request_state)

    assert claim_calls == [replacement_account.id]
    assert session.account is replacement_account
    assert session.upstream is replacement_upstream
    old_upstream_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_security_retry_never_marks_or_migrates_file_pinned_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_http_security_file_owner")
    session = _make_bridge_session()
    session.account = account
    session.durable_session_id = "durable-file-owner"
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-security-file-owner",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        preferred_account_id=account.id,
        file_required_preferred_account=True,
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[{"type":"input_file","file_id":"file_123"}]}',
    )
    mark_durable = AsyncMock()
    reconnect = AsyncMock()
    monkeypatch.setattr(service._durable_bridge, "require_security_work_authorized", mark_durable)
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    assert not await service._retry_http_bridge_security_work_request(session, request_state)

    mark_durable.assert_not_awaited()
    reconnect.assert_not_awaited()
    assert request_state.preferred_account_id == account.id
    assert request_state.require_security_work_authorized is False
    assert session.requires_security_work_authorized is False


@pytest.mark.asyncio
async def test_http_bridge_security_retry_after_reasoning_output_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_proxy_settings()
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    regular_account = _make_account("acc_http_security_created_regular")
    authorized_account = _make_account("acc_http_security_created_authorized")
    authorized_account.security_work_authorized = True
    session = _make_bridge_session()
    session.account = regular_account
    session.durable_session_id = "durable-security-created"
    retry_upstream = SimpleNamespace(send_text=AsyncMock())
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-security-created",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        response_id="resp-created-before-cyber-denial",
        awaiting_response_created=False,
        response_event_count=1,
        downstream_visible=False,
        upstream_model_output_seen=True,
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
        deferred_reasoning_downstream_texts=['data: {"type":"response.output_item.added"}\n\n'],
    )
    session.pending_requests.append(request_state)
    session.queued_request_count = 1
    mark_durable = AsyncMock(return_value=SimpleNamespace(session_id=session.durable_session_id))
    monkeypatch.setattr(service._durable_bridge, "require_security_work_authorized", mark_durable)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", AsyncMock(return_value=object()))
    monkeypatch.setattr(service._load_balancer, "release_account_lease", AsyncMock())

    async def reconnect(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        require_security_work_authorized: bool,
    ) -> None:
        assert target_session is session
        assert require_security_work_authorized is True
        target_session.account = authorized_account
        target_session.upstream = cast(UpstreamResponsesWebSocket, retry_upstream)

    reconnect_mock = AsyncMock(side_effect=reconnect)
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect_mock)

    retried = await service._retry_http_bridge_security_work_request(session, request_state)

    assert retried is False
    mark_durable.assert_not_awaited()
    reconnect_mock.assert_not_awaited()
    retry_upstream.send_text.assert_not_awaited()
    assert request_state.response_id == "resp-created-before-cyber-denial"
    assert request_state.response_event_count == 1
    assert request_state.upstream_model_output_seen is True
    assert request_state.replay_downstream_response_id is None
    assert request_state.suppress_next_created_downstream is False
    assert request_state.deferred_reasoning_downstream_texts == ['data: {"type":"response.output_item.added"}\n\n']
    assert request_state.require_security_work_authorized is False
    assert session.requires_security_work_authorized is False
    assert session.account is regular_account


@pytest.mark.asyncio
async def test_http_bridge_security_retry_after_reasoning_output_does_not_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    regular_account = _make_account("acc_http_security_retry_reconnect_regular")
    session = _make_bridge_session()
    session.account = regular_account
    session.durable_session_id = "durable-security-retry-reconnect"
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-security-retry-reconnect",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        response_id="resp-security-retry-reconnect",
        awaiting_response_created=False,
        response_event_count=1,
        downstream_visible=False,
        upstream_model_output_seen=True,
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
        deferred_reasoning_downstream_texts=['data: {"type":"response.output_item.added"}\n\n'],
    )
    session.pending_requests.append(request_state)
    session.queued_request_count = 1
    mark_durable = AsyncMock(return_value=SimpleNamespace(session_id=session.durable_session_id))
    reconnect = AsyncMock(side_effect=RuntimeError("replacement connect failed"))
    monkeypatch.setattr(service._durable_bridge, "require_security_work_authorized", mark_durable)
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)
    monkeypatch.setattr(service, "_release_request_state_account_response_create_lease", AsyncMock())

    retried = await service._retry_http_bridge_security_work_request(session, request_state)

    assert retried is False
    mark_durable.assert_not_awaited()
    reconnect.assert_not_awaited()
    assert request_state.deferred_reasoning_downstream_texts == ['data: {"type":"response.output_item.added"}\n\n']
    assert request_state.require_security_work_authorized is False
    assert session.requires_security_work_authorized is False


@pytest.mark.asyncio
async def test_http_bridge_security_retry_buffers_reasoning_prelude_before_cyber_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    regular_account = _make_account("acc_http_security_reasoning_regular")
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-security-reasoning-prelude",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
        security_lineage_id="root-http-security-reasoning-prelude",
    )
    session = _make_bridge_session(pending_requests=deque([request_state]), queued_request_count=1)
    session.account = regular_account
    session.durable_session_id = "durable-security-reasoning-prelude"
    persist_lineage = AsyncMock()
    persist_durable = AsyncMock(return_value=SimpleNamespace(session_id=session.durable_session_id))
    retry_security_work = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_mark_security_lineage_requirement", persist_lineage)
    monkeypatch.setattr(service._durable_bridge, "require_security_work_authorized", persist_durable)
    monkeypatch.setattr(service, "_retry_http_bridge_security_work_request", retry_security_work)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.created",
                "sequence_number": 1,
                "response": {"id": "resp-http-security-reasoning-prelude"},
            },
            separators=(",", ":"),
        ),
    )
    assert request_state.event_queue is not None
    created_block = await request_state.event_queue.get()
    assert created_block is not None
    assert "response.created" in created_block

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.output_item.added",
                "sequence_number": 2,
                "response_id": "resp-http-security-reasoning-prelude",
                "item": {"id": "rs_live_like", "type": "reasoning", "summary": []},
            },
            separators=(",", ":"),
        ),
    )

    assert request_state.event_queue.empty()
    assert request_state.response_event_count == 1
    assert request_state.upstream_model_output_seen is True
    assert len(request_state.deferred_reasoning_downstream_texts) == 1

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp-http-security-reasoning-prelude",
                    "status": "failed",
                    "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
                },
            },
            separators=(",", ":"),
        ),
    )

    warning_block = await request_state.event_queue.get()
    assert warning_block is not None
    warning = json.loads(warning_block.split("data: ", 1)[1])
    assert warning["type"] == "codex_lb.warning"
    assert warning["warning"]["code"] == "security_work_authorization_required"
    assert warning["warning"]["action"] == "forward_original_security_work_error"

    failed_block = await request_state.event_queue.get()
    assert failed_block is not None
    assert "response.failed" in failed_block
    assert "cyber_policy" in failed_block
    assert await request_state.event_queue.get() is None
    assert request_state.event_queue.empty()
    assert request_state.deferred_reasoning_downstream_texts == []
    persist_lineage.assert_awaited_once_with(
        "root-http-security-reasoning-prelude",
        account_id=regular_account.id,
        api_key_id=None,
    )
    persist_durable.assert_awaited_once_with(session_id="durable-security-reasoning-prelude")
    retry_security_work.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("require_security_work_authorized", [False, True])
async def test_http_bridge_owner_failover_never_migrates_on_file_id_in_fresh_retry_text(
    monkeypatch: pytest.MonkeyPatch,
    require_security_work_authorized: bool,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    owner_account = _make_account("acc_http_owner_file_retry_text")
    session = _make_bridge_session()
    session.account = owner_account
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-owner-failover-file-retry-text",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        awaiting_response_created=False,
        response_id="resp-created-owner",
        response_event_count=1,
        previous_response_id="resp-owner",
        preferred_account_id=owner_account.id,
        excluded_account_ids={"acc-already-excluded"},
        request_text='{"type":"response.create","previous_response_id":"resp-owner","input":["follow-up"]}',
        responses_lite_model="gpt-5.6-sol",
        fresh_upstream_request_text=(
            '{"type":"response.create","input":[{"type":"input_file","file_id":"file_123"},{"type":"input_text","text":"retry"}]}'
        ),
        fresh_upstream_request_is_retry_safe=True,
        fresh_upstream_request_responses_lite_model="gpt-5.6-sol-mini",
    )
    session.pending_requests.append(request_state)
    session.queued_request_count = 1
    reconnect = AsyncMock(side_effect=RuntimeError("replacement connect unexpected"))
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    assert not await service._retry_http_bridge_owner_failover_request(
        session,
        request_state,
        require_security_work_authorized=require_security_work_authorized,
    )

    reconnect.assert_not_awaited()
    assert request_state.previous_response_id == "resp-owner"
    assert request_state.proxy_injected_previous_response_id is False
    assert request_state.request_text == (
        '{"type":"response.create","previous_response_id":"resp-owner","input":["follow-up"]}'
    )
    assert request_state.responses_lite_model == "gpt-5.6-sol"
    assert request_state.preferred_account_id == owner_account.id
    assert request_state.excluded_account_ids == {"acc-already-excluded"}
    assert request_state.require_security_work_authorized is False
    assert session.requires_security_work_authorized is False
    assert list(session.pending_requests) == [request_state]
    assert session.queued_request_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("require_security_work_authorized", [False, True])
async def test_http_bridge_failed_precreated_owner_failover_restores_original_continuity_state(
    monkeypatch: pytest.MonkeyPatch,
    require_security_work_authorized: bool,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    owner_account = _make_account("acc_http_owner")
    session = _make_bridge_session()
    session.account = owner_account
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-owner-failover-restore",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        awaiting_response_created=True,
        response_id=None,
        response_event_count=0,
        previous_response_id="resp-owner",
        proxy_injected_previous_response_id=True,
        preferred_account_id=owner_account.id,
        excluded_account_ids={"acc-already-excluded"},
        request_text='{"type":"response.create","previous_response_id":"resp-owner","input":["follow-up"]}',
        responses_lite_model="gpt-5.6-sol",
        fresh_upstream_request_text='{"type":"response.create","input":["full-history"]}',
        fresh_upstream_request_is_retry_safe=True,
        fresh_upstream_request_responses_lite_model="gpt-5.6-sol-mini",
    )
    session.pending_requests.append(request_state)
    session.queued_request_count = 1
    reconnect = AsyncMock(side_effect=RuntimeError("replacement connect failed"))
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    assert not await service._retry_http_bridge_owner_failover_request(
        session,
        request_state,
        require_security_work_authorized=require_security_work_authorized,
    )

    reconnect.assert_awaited_once()
    assert reconnect.await_args is not None
    assert reconnect.await_args.kwargs["require_security_work_authorized"] is require_security_work_authorized
    assert request_state.previous_response_id == "resp-owner"
    assert request_state.proxy_injected_previous_response_id is True
    assert request_state.preferred_account_id == owner_account.id
    assert request_state.excluded_account_ids == {"acc-already-excluded"}
    assert request_state.request_text == (
        '{"type":"response.create","previous_response_id":"resp-owner","input":["follow-up"]}'
    )
    assert request_state.responses_lite_model == "gpt-5.6-sol"
    assert request_state.replay_count == 0
    assert request_state.response_id is None
    assert request_state.response_event_count == 0
    assert request_state.replay_downstream_response_id is None
    assert request_state.suppress_next_created_downstream is False
    assert request_state.require_security_work_authorized is require_security_work_authorized
    assert session.requires_security_work_authorized is require_security_work_authorized
    assert list(session.pending_requests) == [request_state]
    assert session.queued_request_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("require_security_work_authorized", [False, True])
async def test_http_bridge_owner_failover_reacquires_replacement_account_create_lease(
    monkeypatch: pytest.MonkeyPatch,
    require_security_work_authorized: bool,
) -> None:
    settings = _make_proxy_settings()
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    owner_account = _make_account("acc_http_owner_lease_old")
    replacement_account = _make_account("acc_http_owner_lease_new")
    replacement_upstream = AsyncMock()
    old_lease = object()
    replacement_lease = object()
    release_lease = AsyncMock()
    acquire_lease = AsyncMock(return_value=replacement_lease)
    session = _make_bridge_session()
    session.account = owner_account
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-owner-failover-replacement-lease",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        awaiting_response_created=True,
        previous_response_id="resp-owner",
        proxy_injected_previous_response_id=True,
        preferred_account_id=owner_account.id,
        request_text='{"type":"response.create","previous_response_id":"resp-owner","input":["tail"]}',
        fresh_upstream_request_text='{"type":"response.create","input":["full resend"]}',
        fresh_upstream_request_is_retry_safe=True,
        account_response_create_lease=cast(Any, old_lease),
        account_response_create_release=release_lease,
    )
    session.pending_requests.append(request_state)
    session.queued_request_count = 1

    async def reconnect(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        require_security_work_authorized: bool,
    ) -> None:
        assert target_session is session
        assert request_state.account_response_create_lease is None
        target_session.account = replacement_account
        target_session.upstream = cast(proxy_service.UpstreamResponsesWebSocket, replacement_upstream)
        target_session.requires_security_work_authorized = require_security_work_authorized

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release_lease)
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", acquire_lease)
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    assert await service._retry_http_bridge_owner_failover_request(
        session,
        request_state,
        require_security_work_authorized=require_security_work_authorized,
    )

    release_lease.assert_awaited_once_with(old_lease)
    acquire_lease.assert_awaited_once_with(
        account_id=replacement_account.id,
        request_id=request_state.request_id,
        surface="http_bridge",
        concurrency_caps=proxy_service.effective_account_concurrency_caps(settings),
    )
    assert request_state.account_response_create_lease is replacement_lease
    assert request_state.account_response_create_release is release_lease
    replacement_upstream.send_text.assert_awaited_once_with(request_state.request_text)


@pytest.mark.asyncio
async def test_process_websocket_security_retry_releases_response_create_gate() -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_security_gate_regular")
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_security_gate",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        transport="websocket",
        previous_response_id="resp_ws_owner",
        preferred_account_id=account.id,
        proxy_injected_previous_response_id=True,
        request_text='{"type":"response.create","model":"gpt-5.1","previous_response_id":"resp_ws_owner","input":[]}',
        fresh_upstream_request_text='{"type":"response.create","model":"gpt-5.1","input":[]}',
        fresh_upstream_request_is_retry_safe=True,
    )
    request_state.response_create_gate = gate
    request_state.response_create_gate_acquired = True
    pending_requests = deque([request_state])
    upstream_control = proxy_service._WebSocketUpstreamControl()
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )
    text = json.dumps(
        {
            "type": "response.failed",
            "response": {
                "id": "resp_ws_security_gate",
                "status": "failed",
                "error": {
                    "code": "invalid_request_error",
                    "type": "invalid_request_error",
                    "message": cyber_message,
                },
            },
        },
        separators=(",", ":"),
    )

    await service._process_upstream_websocket_text(
        text,
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=gate,
    )

    assert upstream_control.replay_request_state is request_state
    assert request_state.require_security_work_authorized is True
    assert request_state.previous_response_id is None
    assert request_state.response_create_gate_acquired is False
    assert request_state.response_create_gate is None
    await asyncio.wait_for(gate.acquire(), timeout=0.1)
    gate.release()


@pytest.mark.asyncio
async def test_direct_websocket_security_replay_reacquires_create_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    regular_account = _make_account("acc_ws_security_gate_regular_e2e")
    authorized_account = _make_account("acc_ws_security_gate_authorized_e2e")
    authorized_account.security_work_authorized = True
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )
    first_upstream = _QueuedTestUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": "resp_ws_security_gate_denied",
                            "status": "failed",
                            "error": {
                                "code": "invalid_request_error",
                                "type": "invalid_request_error",
                                "message": cyber_message,
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
                error_code=None,
            )
        ]
    )
    second_upstream = _QueuedTestUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text='{"type":"response.created","response":{"id":"resp_ws_security_gate_ok","status":"in_progress"}}',
                data=None,
                close_code=None,
                error=None,
                error_code=None,
            ),
            SimpleNamespace(
                kind="text",
                text='{"type":"response.completed","response":{"id":"resp_ws_security_gate_ok","status":"completed","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}',
                data=None,
                close_code=None,
                error=None,
                error_code=None,
            ),
        ]
    )
    connect_count = 0

    async def fake_connect(_self: Any, _headers: Any, **_kwargs: Any):
        nonlocal connect_count
        connect_count += 1
        if connect_count == 1:
            return regular_account, first_upstream
        return authorized_account, second_upstream

    admission_count = 0
    original_acquire = service._acquire_request_state_response_create_admission

    async def track_acquire(request_state: Any, **kwargs: Any) -> None:
        nonlocal admission_count
        admission_count += 1
        await original_acquire(request_state, **kwargs)

    class _Downstream:
        def __init__(self, request_text: str) -> None:
            self.request_text = request_text
            self.request_sent = False
            self.done = asyncio.Event()
            self.sent_text: list[str] = []

        async def receive(self) -> dict[str, object]:
            if not self.request_sent:
                self.request_sent = True
                return {"type": "websocket.receive", "text": self.request_text}
            await self.done.wait()
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            payload = json.loads(text)
            if payload.get("type") in {"response.completed", "response.failed", "error"}:
                self.done.set()

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason
            self.done.set()

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", track_acquire)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))
    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "security check"}]}],
        "stream": True,
    }
    downstream = _Downstream(json.dumps(request_payload, separators=(",", ":")))

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert connect_count == 2
    assert admission_count == 2
    assert len(first_upstream.sent_text) == 1
    assert len(second_upstream.sent_text) == 1
    assert any(json.loads(text).get("type") == "response.completed" for text in downstream.sent_text)


@pytest.mark.asyncio
async def test_websocket_classified_fresh_replay_drops_previous_owner_before_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    trusted_account = _make_account("acc_ws_security_fresh_replay_trusted")
    trusted_account.security_work_authorized = True
    upstream = cast(UpstreamResponsesWebSocket, AsyncMock())
    select_account = AsyncMock(
        return_value=proxy_service.AccountSelection(account=trusted_account, error_message=None, error_code=None)
    )
    monkeypatch.setattr(service, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(return_value=upstream))

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_security_fresh_replay_select",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport="websocket",
        previous_response_id="resp_ws_security_ordinary_owner",
        preferred_account_id="acc_ws_security_ordinary_owner",
        proxy_injected_previous_response_id=True,
        request_text=(
            '{"type":"response.create","model":"gpt-5.1",'
            '"previous_response_id":"resp_ws_security_ordinary_owner","input":["tail"]}'
        ),
        fresh_upstream_request_text='{"type":"response.create","model":"gpt-5.1","input":["self-contained"]}',
        fresh_upstream_request_is_retry_safe=True,
        require_security_work_authorized=True,
        security_lineage_id="root-ws-security-fresh-replay-select",
    )

    account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key="turn-ws-security-fresh-replay-select",
        sticky_kind=proxy_service.StickySessionKind.CODEX_SESSION,
        prefer_earlier_reset=False,
        routing_strategy="capacity_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=cast(Any, SimpleNamespace(send_text=AsyncMock())),
    )

    assert account is trusted_account
    assert selected_upstream is upstream
    assert request_state.previous_response_id is None
    assert request_state.preferred_account_id is None
    assert json.loads(request_state.request_text or "{}")["input"] == ["self-contained"]
    select_account_call = select_account.await_args
    assert select_account_call is not None
    assert select_account_call.kwargs["preferred_account_id"] is None
    assert select_account_call.kwargs["fallback_on_preferred_account_unavailable"] is True
    assert select_account_call.kwargs["require_security_work_authorized"] is True


@pytest.mark.asyncio
async def test_process_websocket_security_retry_never_migrates_file_pinned_owner() -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_security_file_owner")
    mark_security_lineage = AsyncMock()
    service._mark_security_lineage_requirement = mark_security_lineage
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_security_file_owner",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        transport="websocket",
        previous_response_id="resp_ws_file_owner",
        preferred_account_id=account.id,
        file_required_preferred_account=True,
        request_text='{"type":"response.create","model":"gpt-5.6-sol","previous_response_id":"resp_ws_file_owner","input":[]}',
        fresh_upstream_request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
        fresh_upstream_request_is_retry_safe=True,
    )
    pending_requests = deque([request_state])
    upstream_control = proxy_service._WebSocketUpstreamControl()
    text = json.dumps(
        {
            "type": "response.failed",
            "response": {
                "id": "resp_ws_security_file_owner",
                "status": "failed",
                "error": {
                    "code": "invalid_request_error",
                    "type": "invalid_request_error",
                    "message": (
                        "This chat was flagged for possible cybersecurity risk. "
                        "To get authorized for security work, join Trusted Access for Cyber."
                    ),
                },
            },
        },
        separators=(",", ":"),
    )

    await service._process_upstream_websocket_text(
        text,
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert upstream_control.replay_request_state is None
    assert upstream_control.reconnect_requested is False
    assert request_state.require_security_work_authorized is False
    mark_security_lineage.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_websocket_security_retry_detects_file_id_in_fresh_retry_text() -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_security_file_body")
    mark_security_lineage = AsyncMock()
    service._mark_security_lineage_requirement = mark_security_lineage
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_security_file_body",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        transport="websocket",
        previous_response_id="resp_ws_file_body",
        preferred_account_id=account.id,
        file_required_preferred_account=False,
        request_text=(
            '{"type":"response.create","model":"gpt-5.6-sol","previous_response_id":"resp_ws_file_body","input":[]}'
        ),
        fresh_upstream_request_text=(
            '{"type":"response.create","model":"gpt-5.6-sol","input":[{"type":"input_file","file_id":"file_123"}]}'
        ),
        fresh_upstream_request_is_retry_safe=True,
    )
    upstream_control = proxy_service._WebSocketUpstreamControl()
    text = json.dumps(
        {
            "type": "response.failed",
            "response": {
                "id": "resp_ws_security_file_body",
                "status": "failed",
                "error": {
                    "code": "cyber_policy",
                    "type": "invalid_request_error",
                    "message": "denied by Trusted Access",
                },
            },
        },
        separators=(",", ":"),
    )

    await service._process_upstream_websocket_text(
        text,
        account=account,
        account_id_value=account.id,
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert upstream_control.replay_request_state is None
    assert upstream_control.reconnect_requested is False
    assert request_state.require_security_work_authorized is False
    assert request_state.previous_response_id == "resp_ws_file_body"
    assert request_state.fresh_upstream_request_is_retry_safe is True
    mark_security_lineage.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_websocket_security_denial_without_fresh_replay_body_marks_root_and_retires_owner() -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc-ws-security-no-fresh-body")
    mark_security_lineage = AsyncMock()
    service._mark_security_lineage_requirement = mark_security_lineage
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws-security-no-fresh-body",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        transport="websocket",
        previous_response_id="resp-ws-security-anchor",
        request_text=(
            '{"type":"response.create","model":"gpt-5.6-sol",'
            '"previous_response_id":"resp-ws-security-anchor","input":"tail"}'
        ),
        security_lineage_id="root-ws-security-no-fresh-body",
    )
    upstream_control = proxy_service._WebSocketUpstreamControl()

    await service._process_upstream_websocket_text(
        json.dumps(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp-ws-security-no-fresh-body",
                    "status": "failed",
                    "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
                },
            },
            separators=(",", ":"),
        ),
        account=account,
        account_id_value=account.id,
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    mark_security_lineage.assert_awaited_once_with(
        "root-ws-security-no-fresh-body",
        account_id=account.id,
        api_key_id=None,
    )
    assert upstream_control.replay_request_state is None
    assert upstream_control.reconnect_requested is True
    assert request_state.require_security_work_authorized is True


@pytest.mark.asyncio
async def test_process_websocket_security_denial_with_original_file_body_keeps_owner() -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc-ws-security-file-original")
    mark_security_lineage = AsyncMock()
    service._mark_security_lineage_requirement = mark_security_lineage
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws-security-file-original",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        transport="websocket",
        previous_response_id="resp-ws-security-file-anchor",
        request_text=(
            '{"type":"response.create","model":"gpt-5.6-sol",'
            '"previous_response_id":"resp-ws-security-file-anchor",'
            '"input":[{"type":"input_file","file_id":"file-ws-owner"}]}'
        ),
        security_lineage_id="root-ws-security-file-original",
    )
    upstream_control = proxy_service._WebSocketUpstreamControl()

    await service._process_upstream_websocket_text(
        json.dumps(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp-ws-security-file-original",
                    "status": "failed",
                    "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
                },
            },
            separators=(",", ":"),
        ),
        account=account,
        account_id_value=account.id,
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    mark_security_lineage.assert_not_awaited()
    assert upstream_control.reconnect_requested is False
    assert request_state.require_security_work_authorized is False


@pytest.mark.asyncio
async def test_http_bridge_security_denial_without_fresh_replay_body_marks_root_without_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc-http-security-no-fresh-body")
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-security-no-fresh-body",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp-http-security-anchor",
        request_text=(
            '{"type":"response.create","model":"gpt-5.6-sol",'
            '"previous_response_id":"resp-http-security-anchor","input":"tail"}'
        ),
        security_lineage_id="root-http-security-no-fresh-body",
        skip_request_log=True,
    )
    session = _make_bridge_session(pending_requests=deque([request_state]), queued_request_count=1)
    session.account = account
    close = AsyncMock()
    terminal_text = json.dumps(
        {
            "type": "response.failed",
            "response": {
                "id": "resp-http-security-no-fresh-body",
                "status": "failed",
                "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
            },
        },
        separators=(",", ":"),
    )
    session.upstream = cast(
        UpstreamResponsesWebSocket,
        SimpleNamespace(
            close=close,
        ),
    )
    mark_security_lineage = AsyncMock()
    retry_security_work = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_mark_security_lineage_requirement", mark_security_lineage)
    monkeypatch.setattr(service, "_retry_http_bridge_security_work_request", retry_security_work)
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)

    await service._process_http_bridge_upstream_text(session, terminal_text)

    mark_security_lineage.assert_awaited_once_with(
        "root-http-security-no-fresh-body",
        account_id=account.id,
        api_key_id=None,
    )
    retry_security_work.assert_not_awaited()
    assert request_state.require_security_work_authorized is True
    assert session.requires_security_work_authorized is True
    assert session.upstream_control.retire_after_drain is True
    assert await service._retire_http_bridge_after_drain_if_ready(session) is True
    assert session.closed is True
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_security_denial_with_original_file_body_keeps_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc-http-security-file-original")
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-security-file-original",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp-http-security-file-anchor",
        request_text=(
            '{"type":"response.create","model":"gpt-5.6-sol",'
            '"previous_response_id":"resp-http-security-file-anchor",'
            '"input":[{"type":"input_file","file_id":"file-http-owner"}]}'
        ),
        security_lineage_id="root-http-security-file-original",
        skip_request_log=True,
    )
    session = _make_bridge_session(pending_requests=deque([request_state]), queued_request_count=1)
    session.account = account
    terminal_text = json.dumps(
        {
            "type": "response.failed",
            "response": {
                "id": "resp-http-security-file-original",
                "status": "failed",
                "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
            },
        },
        separators=(",", ":"),
    )
    mark_security_lineage = AsyncMock()
    retry_security_work = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_mark_security_lineage_requirement", mark_security_lineage)
    monkeypatch.setattr(service, "_retry_http_bridge_security_work_request", retry_security_work)
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)

    await service._process_http_bridge_upstream_text(session, terminal_text)

    mark_security_lineage.assert_not_awaited()
    retry_security_work.assert_not_awaited()
    assert request_state.require_security_work_authorized is False
    assert session.requires_security_work_authorized is False
    assert session.upstream_control.retire_after_drain is False


@pytest.mark.asyncio
async def test_http_bridge_security_retry_does_not_trust_missing_durable_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc-http-security-missing-durable")
    request_state = proxy_service._WebSocketRequestState(
        request_id="http-security-missing-durable",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp-http-security-durable-anchor",
        proxy_injected_previous_response_id=True,
        fresh_upstream_request_text='{"type":"response.create","input":"full resend"}',
        fresh_upstream_request_is_retry_safe=True,
        request_text='{"type":"response.create","previous_response_id":"resp-http-security-durable-anchor","input":"tail"}',
        security_lineage_id="root-http-security-missing-durable",
        skip_request_log=True,
    )
    session = _make_bridge_session(pending_requests=deque([request_state]), queued_request_count=1)
    session.account = account
    session.durable_session_id = "durable-http-security-missing"
    persist_durable = AsyncMock(return_value=None)
    retry_security_work = AsyncMock(return_value=True)
    monkeypatch.setattr(service._durable_bridge, "require_security_work_authorized", persist_durable)
    monkeypatch.setattr(service, "_retry_http_bridge_security_work_request", retry_security_work)

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp-http-security-missing-durable",
                    "status": "failed",
                    "error": {"code": "cyber_policy", "message": "denied by Trusted Access"},
                },
            },
            separators=(",", ":"),
        ),
    )

    persist_durable.assert_awaited_once_with(session_id="durable-http-security-missing")
    retry_security_work.assert_awaited_once()
    retry_call = retry_security_work.await_args
    assert retry_call is not None
    assert retry_call.kwargs["durable_security_requirement_persisted"] is False
