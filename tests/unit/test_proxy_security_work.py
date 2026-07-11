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

from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.db.models import StickySession
from app.modules.proxy import service as proxy_service
from app.modules.proxy.affinity import _sticky_key_from_session_header
from tests.unit.test_proxy_http_bridge import _make_app_settings, _make_bridge_session
from tests.unit.test_proxy_utils import _make_account, _repo_factory, _RequestLogsRecorder

pytestmark = pytest.mark.unit


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
    )
    assert upstream_control.replay_request_state is None
    assert upstream_control.reconnect_requested is True
    assert request_state.require_security_work_authorized is True


@pytest.mark.asyncio
async def test_http_bridge_reconnect_selects_security_work_authorized_account(monkeypatch: pytest.MonkeyPatch) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    regular_account = _make_account("acc_security_regular")
    authorized_account = _make_account("acc_security_authorized")
    authorized_account.security_work_authorized = True
    session = _make_bridge_session()
    session.account = regular_account
    session.upstream = cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="security_reconnect",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport="http",
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
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
        require_security_work_authorized=True,
    )

    select_args = select_account.await_args
    assert select_args is not None
    assert select_args.kwargs["require_security_work_authorized"] is True
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
async def test_http_bridge_create_passes_security_work_requirement_to_selection(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected_security_lineage_id: str | None,
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
    )

    assert select_account.await_args is not None
    assert select_account.await_args.kwargs["require_security_work_authorized"] is True
    assert select_account.await_args.kwargs["security_lineage_id"] == expected_security_lineage_id
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
    sticky_requirement.assert_awaited_once_with("sticky-security-root")
    assert get_or_create.await_args is not None
    assert get_or_create.await_args.kwargs["durable_lookup"] is None
    assert get_or_create.await_args.kwargs["require_security_work_authorized"] is True


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
    previous_session.previous_response_ids = {"resp-security-previous"}
    authorized_session = _make_bridge_session(key=current_key)
    authorized_session.account = _make_account("acc-security-created-authorized")
    authorized_session.account.security_work_authorized = True
    alias_key = proxy_service._http_bridge_previous_response_alias_key("resp-security-previous", None)
    service._http_bridge_sessions[previous_key] = previous_session
    service._http_bridge_previous_response_index[alias_key] = previous_key
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
    create_http_bridge_session = AsyncMock(return_value=authorized_session)
    monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
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
@pytest.mark.parametrize("downstream_visible", [False, True])
async def test_http_bridge_security_retry_after_response_created_requires_no_visible_output(
    monkeypatch: pytest.MonkeyPatch,
    downstream_visible: bool,
) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    regular_account = _make_account("acc_http_security_created_regular")
    authorized_account = _make_account("acc_http_security_created_authorized")
    authorized_account.security_work_authorized = True
    session = _make_bridge_session()
    session.account = regular_account
    session.durable_session_id = "durable-security-created"
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
        downstream_visible=downstream_visible,
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
    )
    session.pending_requests.append(request_state)
    session.queued_request_count = 1
    mark_durable = AsyncMock(return_value=SimpleNamespace(session_id=session.durable_session_id))
    monkeypatch.setattr(service._durable_bridge, "require_security_work_authorized", mark_durable)

    async def reconnect(
        _session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        require_security_work_authorized: bool,
    ) -> None:
        assert request_state.response_id is None
        assert request_state.response_event_count == 0
        assert request_state.replay_downstream_response_id == "resp-created-before-cyber-denial"
        assert request_state.suppress_next_created_downstream is True
        assert require_security_work_authorized is True
        _session.account = authorized_account
        _session.upstream = cast(
            UpstreamResponsesWebSocket,
            SimpleNamespace(send_text=AsyncMock()),
        )

    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    retried = await service._retry_http_bridge_security_work_request(session, request_state)

    assert retried is (not downstream_visible)
    if downstream_visible:
        mark_durable.assert_not_awaited()
        assert request_state.response_id == "resp-created-before-cyber-denial"
        assert request_state.require_security_work_authorized is False
        assert session.requires_security_work_authorized is False
    else:
        mark_durable.assert_awaited_once_with(session_id="durable-security-created")
        assert request_state.response_id is None
        assert request_state.replay_downstream_response_id == "resp-created-before-cyber-denial"
        assert request_state.suppress_next_created_downstream is True
        assert request_state.require_security_work_authorized is True
        assert session.requires_security_work_authorized is True
        assert session.account is authorized_account


@pytest.mark.asyncio
@pytest.mark.parametrize("require_security_work_authorized", [False, True])
async def test_http_bridge_failed_owner_failover_restores_original_continuity_state(
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
        awaiting_response_created=False,
        response_id="resp-created-owner",
        response_event_count=1,
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
    assert request_state.response_id == "resp-created-owner"
    assert request_state.response_event_count == 1
    assert request_state.replay_downstream_response_id is None
    assert request_state.suppress_next_created_downstream is False
    assert request_state.require_security_work_authorized is require_security_work_authorized
    assert session.requires_security_work_authorized is require_security_work_authorized
    assert list(session.pending_requests) == [request_state]
    assert session.queued_request_count == 1


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
async def test_process_websocket_security_retry_never_migrates_file_pinned_owner() -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_security_file_owner")
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
    assert request_state.require_security_work_authorized is False
