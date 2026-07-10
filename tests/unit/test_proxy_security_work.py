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
        request_text='{"type":"response.create","model":"gpt-5.1","input":[]}',
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
    assert request_state.response_create_gate_acquired is False
    assert request_state.response_create_gate is None
    await asyncio.wait_for(gate.acquire(), timeout=0.1)
    gate.release()
