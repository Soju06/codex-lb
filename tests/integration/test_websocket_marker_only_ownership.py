from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.testclient import WebSocketTestSession

import app.modules.model_sources.selection as source_selection
import app.modules.proxy.service as proxy_module
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from tests.integration.test_model_source_routing import _create_model_source
from tests.integration.test_proxy_responses import _make_auth_json
from tests.integration.test_proxy_websocket_responses import (
    _SequencedUpstreamWebSocket,
    _stub_request_logging,  # noqa: F401
    _websocket_response_batch,
)

pytestmark = pytest.mark.integration


def _receive_frame(websocket: WebSocketTestSession) -> dict[str, Any]:
    async def receive() -> dict[str, Any]:
        with anyio.fail_after(5):
            message = await websocket._send_rx.receive()
        websocket._raise_on_close(message)
        return json.loads(message["text"])

    return websocket.portal.call(receive)


async def _setup_accounts(app_instance, count: int, *, scoped: bool, paused: bool = False):
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as client:
        for index in range(count):
            auth = _make_auth_json(f"marker-owner-{index}", f"marker-{index}@example.com")
            response = await client.post(
                "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
            )
            assert response.status_code == 200
        async with SessionLocal() as session:
            accounts = list(await session.scalars(select(Account).order_by(Account.id)))
            if paused:
                accounts[0].status = AccountStatus.PAUSED
                await session.commit()
            authorization = None
            if scoped:
                key = await ApiKeysService(ApiKeysRepository(session)).create_key(
                    ApiKeyCreateData(name="marker-only", allowed_models=None, assigned_account_ids=[accounts[-1].id])
                )
                authorization = f"Bearer {key.key}"
        if scoped:
            response = await client.put("/api/settings", json={"apiKeyAuthEnabled": True})
            assert response.status_code == 200
    return accounts, authorization


def _install_upstream(monkeypatch, account: Account, *, batches=None):
    upstream = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=batches if batches is not None else [_websocket_response_batch("resp_marker_only")]
    )
    selection_calls = []
    opened = []

    async def select_account(self, deadline, **kwargs):
        selection_calls.append(kwargs)
        return proxy_module.AccountSelection(account=account, error_message=None)

    async def open_upstream(self, selected_account, headers, **kwargs):
        opened.append((selected_account.id, dict(headers)))
        return selected_account, upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(proxy_module.ProxyService, "_try_open_websocket_connect_attempt", open_upstream)
    return upstream, selection_calls, opened


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("marker", ["turn_unregistered_reconnect", "http_turn_unregistered_reconnect"])
@pytest.mark.parametrize(
    ("count", "scoped", "paused", "lookup_failure"),
    [
        (0, False, False, False),
        (1, False, False, False),
        (2, False, False, False),
        (2, True, False, False),
        (2, False, True, False),
        (1, False, False, True),
    ],
)
def test_marker_only_reconnect_counts_assignment_owners(
    app_instance, monkeypatch, path, marker, count, scoped, paused, lookup_failure
):
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None

        async def setup():
            return await _setup_accounts(app_instance, count, scoped=scoped, paused=paused)

        accounts, authorization = client.portal.call(setup)
        selected = accounts[-1] if accounts else Account(id="unexpected-selection")
        upstream, selection_calls, opened = _install_upstream(monkeypatch, selected)
        service = get_proxy_service_for_app(app_instance)
        candidates = AsyncMock(wraps=service._load_balancer.list_continuity_owner_candidates)
        if lookup_failure:
            candidates.side_effect = RuntimeError("private owner repository failure")
        monkeypatch.setattr(service._load_balancer, "list_continuity_owner_candidates", candidates)
        headers = {"x-codex-turn-state": marker}
        if authorization is not None:
            headers["Authorization"] = authorization
        allowed = (count == 1 or scoped) and not lookup_failure
        with client.websocket_connect(f"ws://localhost{path}", headers=headers) as websocket:
            # Opaque state exercises continuation ownership; plain text is stateless.
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.4",
                    "input": [{"type": "reasoning", "encrypted_content": "opaque-test-state", "summary": []}],
                }
            )
            frame = _receive_frame(websocket)
            if allowed:
                assert frame["type"] == "response.created", frame
                assert _receive_frame(websocket)["type"] == "response.completed"
            else:
                assert frame["type"] == "response.failed", frame
                assert frame["response"]["error"]["code"] == "previous_response_owner_unavailable"
                assert frame["response"]["error"]["message"] == (
                    "Previous response owner account is unavailable; retry later."
                )
        candidates.assert_awaited_once_with(account_ids=[selected.id] if scoped else None)
        assert len(selection_calls) == int(allowed)
        assert len(opened) == int(allowed)
        assert len(upstream.sent_text) == int(allowed)
        if allowed:
            assert selection_calls[0]["preferred_account_id"] == selected.id
            assert selection_calls[0]["fallback_on_preferred_account_unavailable"] is False
            assert opened[0][0] == selected.id
            assert opened[0][1]["x-codex-turn-state"] == marker
            assert "previous_response_id" not in json.loads(upstream.sent_text[0])


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("source_state", ["confirmed", "unavailable"])
def test_marker_only_preserves_source_lookup_outcomes(app_instance, monkeypatch, path, source_state):
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None

        async def setup():
            accounts, _ = await _setup_accounts(app_instance, 2, scoped=False)
            async with AsyncClient(
                transport=ASGITransport(app=app_instance), base_url="http://testserver"
            ) as setup_client:
                await _create_model_source(
                    setup_client,
                    name="marker-source",
                    model="marker-source-model",
                    base_url="http://127.0.0.1:1/v1",
                    supports_responses=True,
                )
            return accounts

        accounts = client.portal.call(setup)
        upstream, selected, opened = _install_upstream(monkeypatch, accounts[-1])
        service = get_proxy_service_for_app(app_instance)
        candidates = AsyncMock(wraps=service._load_balancer.list_continuity_owner_candidates)
        monkeypatch.setattr(service._load_balancer, "list_continuity_owner_candidates", candidates)
        if source_state == "unavailable":
            monkeypatch.setattr(
                source_selection, "select_responses_model_source", AsyncMock(side_effect=RuntimeError("catalog down"))
            )
        with client.websocket_connect(
            f"ws://localhost{path}", headers={"x-codex-turn-state": "turn_source_reconnect"}
        ) as websocket:
            websocket.send_json({"type": "response.create", "model": "marker-source-model", "input": "continue"})
            frame = _receive_frame(websocket)
            if source_state == "confirmed":
                assert frame["type"] == "error", frame
                assert frame["status"] == 503
                assert frame["error"]["code"] == "model_source_requires_http_transport"
            else:
                assert frame["type"] == "response.created", frame
                assert _receive_frame(websocket)["type"] == "response.completed"
        candidates.assert_not_awaited()
        assert len(selected) == int(source_state == "unavailable")
        assert len(opened) == int(source_state == "unavailable")
        assert len(upstream.sent_text) == int(source_state == "unavailable")


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
def test_fresh_proxy_generated_marker_does_not_require_a_sole_owner(app_instance, monkeypatch, path):
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None

        async def setup():
            return await _setup_accounts(app_instance, 2, scoped=False)

        accounts, _ = client.portal.call(setup)
        upstream, selected, _ = _install_upstream(monkeypatch, accounts[-1])
        service = get_proxy_service_for_app(app_instance)
        candidates = AsyncMock(wraps=service._load_balancer.list_continuity_owner_candidates)
        monkeypatch.setattr(service._load_balancer, "list_continuity_owner_candidates", candidates)
        with client.websocket_connect(f"ws://localhost{path}") as websocket:
            websocket.send_json({"type": "response.create", "model": "gpt-5.4", "input": "new request"})
            assert _receive_frame(websocket)["type"] == "response.created"
            assert _receive_frame(websocket)["type"] == "response.completed"
        candidates.assert_not_awaited()
        assert len(selected) == 1
        assert selected[0]["preferred_account_id"] is None
        assert len(upstream.sent_text) == 1


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("marker", ["turn_file_reconnect", "http_turn_file_reconnect"])
def test_file_owner_does_not_authorize_a_later_unowned_marker_only_turn(app_instance, monkeypatch, path, marker):
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None

        async def setup():
            accounts, _ = await _setup_accounts(app_instance, 2, scoped=False)
            await get_proxy_service_for_app(app_instance)._pin_file_account("file_marker_owner", accounts[-1].id)
            return accounts

        accounts = client.portal.call(setup)
        # Leave the first response in flight so no socket-local anchor is
        # injected into the marker-only second turn.
        batch = _websocket_response_batch("resp_file_marker_owner")
        upstream, selected, opened = _install_upstream(monkeypatch, accounts[-1], batches=[batch[:1], batch[1:]])
        service = get_proxy_service_for_app(app_instance)
        candidates = AsyncMock(wraps=service._load_balancer.list_continuity_owner_candidates)
        monkeypatch.setattr(service._load_balancer, "list_continuity_owner_candidates", candidates)
        with client.websocket_connect(f"ws://localhost{path}", headers={"x-codex-turn-state": marker}) as websocket:
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.4",
                    "input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file_marker_owner"}]}],
                }
            )
            assert _receive_frame(websocket)["type"] == "response.created"
            candidates.assert_not_awaited()
            # The second turn needs its own owner because it carries opaque state.
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.4",
                    "input": [{"type": "reasoning", "encrypted_content": "opaque-test-state", "summary": []}],
                }
            )
            frame = _receive_frame(websocket)
            assert frame["type"] == "response.failed", frame
            assert frame["response"]["error"]["code"] == "previous_response_owner_unavailable"
        candidates.assert_awaited_once_with(account_ids=None)
        assert len(selected) == 1
        assert selected[0]["preferred_account_id"] == accounts[-1].id
        assert selected[0]["fallback_on_preferred_account_unavailable"] is False
        assert len(opened) == 1
        assert len(upstream.sent_text) == 1
