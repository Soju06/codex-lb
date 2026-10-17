from __future__ import annotations

import json
from collections import deque

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.db.models import Account, AccountStatus, ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.service import AccountsService
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.proxy._load_balancer.types import AccountLease
from app.modules.proxy.account_cache import clear_account_routing_unavailable
from tests.integration.test_proxy_websocket_responses import (
    _assert_ws_single_response_lifecycle_completed,
    _completed_first_turn_upstream_batch,
    _receive_until_terminal,
    _SequencedUpstreamWebSocket,
    _websocket_response_batch,
    _websocket_settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(20)]

_PATH = "/backend-api/codex/responses"
_FIRST = {"role": "user", "content": [{"type": "input_text", "text": "Remember amber-moon-728."}]}
_DELTA = {"role": "user", "content": [{"type": "input_text", "text": "Repeat the code."}]}
_PARENT = "resp_paused_first"


def _create(items, *, parent=None):
    payload = {"type": "response.create", "model": "gpt-5.4", "input": items, "stream": True}
    if parent is not None:
        payload["previous_response_id"] = parent
    return payload


class _OpenSocketFixture:
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
            lease_id = f"paused-test-{len(fixture.connects)}"
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
            if lease is not None and lease.lease_id.startswith("paused-test-"):
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
                id="paused_socket_owner",
                chatgpt_account_id="paused_socket_owner",
                email="paused-socket@example.com",
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
                        name=f"paused-{name}",
                        allowed_models=None,
                        limits=(
                            [LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000)]
                            if self.limited
                            else []
                        ),
                    )
                )
                self.keys[f"Bearer {name}"] = await service.get_key_by_id(key.id)

    def headers(self, *, key="first", session="paused-session", conversation="paused-conversation"):
        return {"Authorization": f"Bearer {key}", "session_id": session, "thread-id": conversation}


def _complete(websocket, payload, expected):
    websocket.send_json(payload)
    events, disconnect = _receive_until_terminal(websocket)
    assert _assert_ws_single_response_lifecycle_completed(events, disconnect) == expected


async def _set_paused(fixture, paused):
    async with SessionLocal() as session:
        service = AccountsService(AccountsRepository(session))
        if paused:
            assert await service.pause_account(fixture.account.id)
        else:
            assert await service.reactivate_account(fixture.account.id)


async def _assert_no_reserved_usage():
    async with SessionLocal() as session:
        rows = (await session.scalars(select(ApiKeyUsageReservation))).all()
        assert rows
        assert all(row.status != "reserved" for row in rows)


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("parent", [None, _PARENT])
@pytest.mark.parametrize("pause_during_admission", [False, True])
def test_open_socket_blocks_paused_account_and_resumes_same_context(
    app_instance, monkeypatch, path, parent, pause_during_admission
):
    upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
            _completed_first_turn_upstream_batch(_PARENT),
            _completed_first_turn_upstream_batch("resp_after_reactivation"),
        ],
    )
    fixture = _OpenSocketFixture(monkeypatch, [upstream], limited=True)
    logs = []

    async def record(service, **kwargs):
        logs.append(kwargs)

    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", record)
    original_acquire = proxy_module.ProxyService._acquire_account_response_create_lease_or_overload
    pause_next = False

    async def acquire(service, **kwargs):
        nonlocal pause_next
        lease = await original_acquire(service, **kwargs)
        if pause_next:
            pause_next = False
            await _set_paused(fixture, True)
        return lease

    monkeypatch.setattr(proxy_module.ProxyService, "_acquire_account_response_create_lease_or_overload", acquire)
    with TestClient(app_instance) as client:
        assert client.portal is not None
        client.portal.call(fixture.prepare)
        assert fixture.account is not None
        try:
            with client.websocket_connect(path, headers=fixture.headers()) as websocket:
                _complete(websocket, _create([_FIRST]), _PARENT)
                if pause_during_admission:
                    pause_next = True
                else:
                    client.portal.call(_set_paused, fixture, True)
                # The socket intentionally retains an older ACTIVE ORM snapshot.
                assert fixture.account.status == AccountStatus.ACTIVE
                websocket.send_json(_create([_DELTA], parent=parent))
                events, disconnect = _receive_until_terminal(websocket)
                assert disconnect is None
                assert len(events) == 1
                assert events[0]["type"] == "response.failed"
                expected = "previous_response_owner_unavailable" if parent else "preferred_account_unavailable"
                assert events[0]["response"]["error"]["code"] == expected
                assert len(upstream.sent_text) == 1
                assert not upstream.closed
                assert len(fixture.connects) == 1
                client.portal.call(_assert_no_reserved_usage)
                assert [log["error_code"] for log in logs if log["status"] == "error"] == [expected]

                client.portal.call(_set_paused, fixture, False)
                _complete(websocket, _create([_DELTA], parent=_PARENT), "resp_after_reactivation")
                assert len(fixture.connects) == 1
                assert json.loads(upstream.sent_text[-1])["previous_response_id"] == _PARENT
                assert json.loads(upstream.sent_text[-1])["input"] == [_DELTA]
                assert "amber-moon-728" not in upstream.sent_text[-1]
        finally:
            client.portal.call(_set_paused, fixture, False)
    assert fixture.released_leases == ["paused-test-0"]


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
def test_pause_rejects_new_frame_without_interrupting_streaming_response(app_instance, monkeypatch, path):
    first_batch = _websocket_response_batch(_PARENT, completed=False)
    terminal = _completed_first_turn_upstream_batch(_PARENT)[-1]
    upstream = _SequencedUpstreamWebSocket([], deferred_message_batches=[first_batch])
    fixture = _OpenSocketFixture(monkeypatch, [upstream], limited=True)
    with TestClient(app_instance) as client:
        assert client.portal is not None
        client.portal.call(fixture.prepare)
        assert fixture.account is not None
        try:
            with client.websocket_connect(path, headers=fixture.headers()) as websocket:
                websocket.send_json(_create([_FIRST]))
                for _ in first_batch:
                    websocket.receive_json()
                client.portal.call(_set_paused, fixture, True)
                websocket.send_json(_create([_DELTA]))
                events, disconnect = _receive_until_terminal(websocket)
                assert disconnect is None
                assert events[-1]["response"]["error"]["code"] == "preferred_account_unavailable"
                assert len(upstream.sent_text) == 1
                assert not upstream.closed
                client.portal.call(upstream._messages.put_nowait, terminal)
                events, disconnect = _receive_until_terminal(websocket)
                assert disconnect is None
                assert events[-1]["type"] == "response.completed"
                assert events[-1]["response"]["id"] == _PARENT
                client.portal.call(_assert_no_reserved_usage)
        finally:
            client.portal.call(_set_paused, fixture, False)
    assert fixture.released_leases == ["paused-test-0"]
