from __future__ import annotations

import json
from collections.abc import AsyncIterator, Collection, Mapping
from typing import Literal
from unittest.mock import Mock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient

import app.modules.proxy.service as proxy_module
from app.db.models import Account
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import SECURITY_WORK_AUTHORIZED_ACCOUNTS_EXHAUSTED, AccountSelection
from tests.integration.test_http_responses_bridge import (
    _collect_sse_events,
    _FakeBridgeUpstreamWebSocket,
    _FakeUpstreamMessage,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration

SECURITY_MESSAGE = (
    "This chat was flagged for possible cybersecurity risk. "
    "To get authorized for security work, join the Trusted Access for Cyber program. "
    "https://chatgpt.com/cyber"
)


class _SecurityDeniedUpstream(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "error",
                        "status": 400,
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_request_error",
                            "message": SECURITY_MESSAGE,
                        },
                    }
                ),
            )
        )


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_bridge(app_instance: FastAPI) -> AsyncIterator[None]:
    yield
    service = get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        sessions = list(service._http_bridge_sessions.values())
        inflight = list(service._http_bridge_inflight_sessions.values())
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()
        service._http_bridge_previous_response_index.clear()
    for session in sessions:
        await service._close_http_bridge_session(session)
        assert session.upstream.closed
    for future in inflight:
        if not future.done():
            future.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_mode", ["native_backend", "public_backend", "v1"])
@pytest.mark.parametrize("failure_kind", ["transient_refresh", "repeated_401"])
async def test_http_bridge_security_exhaustion_preserves_warning_and_denial(
    async_client: AsyncClient,
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    client_mode: Literal["native_backend", "public_backend", "v1"],
    failure_kind: Literal["transient_refresh", "repeated_401"],
) -> None:
    _install_bridge_settings(monkeypatch, enabled=True)
    dashboard_settings = await proxy_module.get_settings_cache().get()
    dashboard_settings.upstream_stream_transport = "websocket"
    native = client_mode == "native_backend"
    path = "/v1/responses" if client_mode == "v1" else "/backend-api/codex/responses"
    ordinary = await _get_account(await _import_account(async_client, "ordinary", "ordinary@example.com"))
    authorized = await _get_account(await _import_account(async_client, "authorized", "authorized@example.com"))
    authorized.security_work_authorized = True
    upstream = _SecurityDeniedUpstream()
    selections: list[tuple[bool, set[str]]] = []
    attempts: list[tuple[str, str | None, bool]] = []

    async def select_account(
        self: proxy_module.ProxyService,
        deadline: float,
        *,
        require_security_work_authorized: bool = False,
        exclude_account_ids: Collection[str] | None = None,
        **_kwargs: object,
    ) -> AccountSelection:
        required = require_security_work_authorized
        excluded = set(exclude_account_ids or ())
        selections.append((required, excluded))
        if len(selections) == 1:
            assert not required
            return AccountSelection(account=ordinary, error_message=None)
        assert required
        assert ordinary.id in excluded
        if authorized.id not in excluded:
            return AccountSelection(account=authorized, error_message=None)
        return AccountSelection(
            account=None,
            error_message="All authorized accounts excluded",
            error_code=SECURITY_WORK_AUTHORIZED_ACCOUNTS_EXHAUSTED,
        )

    async def refresh(
        self: proxy_module.ProxyService,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float,
    ) -> Account:
        attempts.append(("refresh", account.id, force))
        if account.id == authorized.id and force and failure_kind == "transient_refresh":
            raise proxy_module.RefreshError("temporarily_unavailable", "Refresh temporarily unavailable", False)
        return account

    async def connect(
        headers: Mapping[str, str],
        access_token: str,
        account_id_header: str | None,
        **_kwargs: object,
    ) -> _FakeBridgeUpstreamWebSocket:
        attempts.append(("connect", account_id_header, False))
        if account_id_header == ordinary.chatgpt_account_id:
            return upstream
        assert account_id_header == authorized.chatgpt_account_id
        raise proxy_module.ProxyResponseError(401, proxy_module.openai_error("invalid_api_key", "Expired session"))

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", refresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    advisory = Mock(wraps=proxy_module._security_work_advisory_event)
    monkeypatch.setattr(proxy_module, "_security_work_advisory_event", advisory)

    events = await _collect_sse_events(
        async_client,
        path,
        json_body={
            "model": "gpt-5.1",
            "input": "Analyze this security task",
            **({"instructions": "Return exactly OK."} if native else {}),
            "prompt_cache_key": "security-exhaustion",
            "stream": True,
        },
        headers={"accept": "text/event-stream", "user-agent": "codex-cli/1.0" if native else "custom-client/1.0"},
    )

    assert selections == [(False, set()), (True, {ordinary.id}), (True, {ordinary.id, authorized.id})]
    expected_attempts = [
        ("refresh", ordinary.id, False),
        ("connect", ordinary.chatgpt_account_id, False),
        ("refresh", authorized.id, False),
        ("connect", authorized.chatgpt_account_id, False),
        ("refresh", authorized.id, True),
    ]
    if failure_kind == "repeated_401":
        expected_attempts.append(("connect", authorized.chatgpt_account_id, False))
    assert attempts == expected_attempts
    assert len(upstream.sent_text) == 1
    assert [call.kwargs["code"] for call in advisory.call_args_list] == [
        "security_work_authorization_required",
        "no_security_work_authorized_accounts",
    ]
    assert [call.kwargs["action"] for call in advisory.call_args_list] == [
        "retry_security_work_authorized",
        "forward_original_security_work_error",
    ]
    assert SECURITY_WORK_AUTHORIZED_ACCOUNTS_EXHAUSTED not in json.dumps(events)
    terminal_type = "error" if native else "response.failed"
    terminal_events = [event for event in events if event["type"] == terminal_type]
    assert len(terminal_events) == 1
    terminal = terminal_events[0]
    error = terminal["response"]["error"] if terminal_type == "response.failed" else terminal["error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] == "invalid_request_error"
    assert error["message"] == SECURITY_MESSAGE
    expected_types = ["codex_lb.warning", "codex_lb.warning"] if native else ["response.created"]
    expected_types.append(terminal_type)
    assert [event["type"] for event in events] == expected_types
    if native:
        assert [event["warning"]["code"] for event in events[:2]] == [
            "security_work_authorization_required",
            "no_security_work_authorized_accounts",
        ]
        assert events[1]["warning"]["action"] == "forward_original_security_work_error"
    service = get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        assert not service._http_bridge_inflight_sessions
        sessions = list(service._http_bridge_sessions.values())
    for session in sessions:
        assert not session.pending_requests
        assert session.queued_request_count == 0
        assert not session.response_create_gate.locked()
