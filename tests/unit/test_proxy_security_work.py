from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.websocket.helpers import _prepare_websocket_request_state_for_owner_failover
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
async def test_http_bridge_create_passes_security_work_requirement_to_selection(
    monkeypatch: pytest.MonkeyPatch,
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
        headers={"session_id": "security-create"},
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
    assert session.upstream_reader is not None
    await session.upstream_reader


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


def test_websocket_replay_safe_owner_failover_can_migrate_without_security_filter() -> None:
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws-owner-replay",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp-owner",
        preferred_account_id="acc-owner",
        request_text='{"type":"response.create","previous_response_id":"resp-owner","input":[]}',
        fresh_upstream_request_text='{"type":"response.create","input":[]}',
        fresh_upstream_request_is_retry_safe=True,
    )
    excluded_account_ids: set[str] = set()

    assert _prepare_websocket_request_state_for_owner_failover(
        request_state,
        owner_account_id="acc-owner",
        exclude_account_ids=excluded_account_ids,
    )
    assert request_state.require_security_work_authorized is False
    assert request_state.preferred_account_id is None
    assert excluded_account_ids == {"acc-owner"}


def test_websocket_file_pin_owner_failover_stays_fail_closed() -> None:
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws-file-owner",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp-owner",
        preferred_account_id="acc-owner",
        file_required_preferred_account=True,
        request_text='{"type":"response.create","previous_response_id":"resp-owner","input":[]}',
        fresh_upstream_request_text='{"type":"response.create","input":[]}',
        fresh_upstream_request_is_retry_safe=True,
    )
    excluded_account_ids: set[str] = set()

    assert not _prepare_websocket_request_state_for_owner_failover(
        request_state,
        owner_account_id="acc-owner",
        exclude_account_ids=excluded_account_ids,
    )
    assert request_state.preferred_account_id == "acc-owner"
    assert excluded_account_ids == set()


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
    assert request_state.require_security_work_authorized is False
    assert session.requires_security_work_authorized is False
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
