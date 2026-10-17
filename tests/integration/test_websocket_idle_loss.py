from __future__ import annotations

import json
import threading
from collections import deque

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.proxy._load_balancer.types import AccountLease
from app.modules.proxy.account_cache import clear_account_routing_unavailable
from tests.integration.test_proxy_websocket_responses import (
    _assert_ws_single_response_lifecycle_completed,
    _completed_first_turn_upstream_batch,
    _FakeUpstreamMessage,
    _receive_until_terminal,
    _SequencedUpstreamWebSocket,
    _websocket_settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(20)]

_PATH = "/backend-api/codex/responses"
_FIRST = {"role": "user", "content": [{"type": "input_text", "text": "Remember amber-moon-728."}]}
_DELTA = {"role": "user", "content": [{"type": "input_text", "text": "Repeat the code."}]}
_PARENT = "resp_retained_first"


def _create(items, *, parent=None):
    payload = {"type": "response.create", "model": "gpt-5.4", "input": items, "stream": True}
    if parent is not None:
        payload["previous_response_id"] = parent
    return payload


class _IdleSocketFixture:
    """Keep the API and database real while replacing the external transport."""

    def __init__(self, monkeypatch, upstreams, *, limited=False):
        self.upstreams = deque(upstreams)
        self.connects = []
        self.released_leases = []
        self.keys = {}
        self.account = None
        self.limited = limited
        fixture = self
        original_release = proxy_module.LoadBalancer.release_account_lease

        class SettingsCache:
            async def get(self):
                return _websocket_settings()

        async def allow_firewall(_websocket):
            return None

        async def authenticate(authorization, *, request=None):
            return fixture.keys[authorization]

        async def connect(service, headers, *, request_state, **kwargs):
            assert fixture.account is not None
            lease_id = f"retained-test-{len(fixture.connects)}"
            request_state.websocket_stream_lease = AccountLease(
                lease_id=lease_id,
                account_id=fixture.account.id,
                kind="stream",
                acquired_at=0.0,
                api_key_id=request_state.api_key.id,
            )
            fixture.connects.append(lease_id)
            return fixture.account, fixture.upstreams.popleft()

        async def release(load_balancer, lease):
            if lease is not None and lease.lease_id.startswith("retained-test-"):
                fixture.released_leases.append(lease.lease_id)
                return
            await original_release(load_balancer, lease)

        async def write_request_log(service, **kwargs):
            return None

        monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
        monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", authenticate)
        monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: SettingsCache())
        monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", connect)
        monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", write_request_log)
        monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", release)

    async def prepare(self):
        async with SessionLocal() as session:
            account = Account(
                id="retained_socket_owner",
                chatgpt_account_id="retained_socket_owner",
                email="retained-socket@example.com",
                plan_type="plus",
                access_token_encrypted=b"access",
                refresh_token_encrypted=b"refresh",
                id_token_encrypted=b"id",
                last_refresh=proxy_module.utcnow(),
                status=AccountStatus.ACTIVE,
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            session.expunge(account)
            self.account = account
            clear_account_routing_unavailable(account.id)
        async with SessionLocal() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            for name in ("first", "second"):
                key = await service.create_key(
                    ApiKeyCreateData(
                        name=f"retained-{name}",
                        allowed_models=None,
                        limits=(
                            [LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000)]
                            if self.limited
                            else []
                        ),
                    )
                )
                self.keys[f"Bearer {name}"] = await service.get_key_by_id(key.id)

    def headers(self, *, key="first", session="retained-session", conversation="retained-conversation"):
        return {"Authorization": f"Bearer {key}", "session_id": session, "thread-id": conversation}


def _complete(websocket, payload, expected):
    websocket.send_json(payload)
    events, disconnect = _receive_until_terminal(websocket)
    assert _assert_ws_single_response_lifecycle_completed(events, disconnect) == expected


@pytest.mark.parametrize("ending", ["close", "error"])
def test_idle_upstream_loss_signals_reconnect_and_preserves_full_context(app_instance, monkeypatch, ending):
    first = _SequencedUpstreamWebSocket([], deferred_message_batches=[_completed_first_turn_upstream_batch(_PARENT)])
    next_socket = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=[_completed_first_turn_upstream_batch("resp_recovered")]
    )
    fixture = _IdleSocketFixture(monkeypatch, [first, next_socket])
    closed = threading.Event()
    close_codes = []
    original_close = WebSocket.close

    async def close(ws, code=1000, reason=None):
        close_codes.append(code)
        await original_close(ws, code, reason)
        closed.set()

    monkeypatch.setattr(WebSocket, "close", close)
    with TestClient(app_instance) as client:
        assert client.portal is not None
        client.portal.call(fixture.prepare)
        with client.websocket_connect("/backend-api/codex/responses", headers=fixture.headers()) as downstream:
            _complete(downstream, _create([_FIRST]), _PARENT)
            client.portal.call(first._messages.put_nowait, _FakeUpstreamMessage(kind=ending, close_code=1000))
            assert closed.wait(2), "client was left connected to a lost upstream cache"
            assert close_codes[0] == 1012
        full_context = [_FIRST, {"role": "assistant", "content": [{"type": "output_text", "text": "ACK"}]}, _DELTA]
        with client.websocket_connect("/backend-api/codex/responses", headers=fixture.headers()) as downstream:
            _complete(downstream, _create(full_context), "resp_recovered")
            sent = json.loads(next_socket.sent_text[0])
            assert sent["input"] == full_context
            assert not sent.get("previous_response_id")
    assert fixture.released_leases == ["retained-test-0", "retained-test-1"]
