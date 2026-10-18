from __future__ import annotations

import json
from typing import Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.db.models import Account
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from app.modules.proxy.capability_routing import REQUIRED_CAPABILITY_HEADER
from tests.integration.test_proxy_responses import _make_auth_json
from tests.integration.test_proxy_websocket_responses import (
    _SequencedUpstreamWebSocket,
    _stub_request_logging,  # noqa: F401
    _websocket_response_batch,
)

pytestmark = pytest.mark.integration


async def _scoped_accounts(
    app_instance: FastAPI, scope: Literal["mixed", "authorized", "unauthorized"]
) -> tuple[str, list[Account]]:
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as client:
        for index in range(2):
            auth = _make_auth_json(f"acc_ws_cardinality_{index}", f"ws-cardinality-{index}@example.com")
            imported = await client.post(
                "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
            )
            assert imported.status_code == 200
        async with SessionLocal() as session:
            accounts = list(await session.scalars(select(Account).order_by(Account.id)))
            accounts[0].security_work_authorized = True
            accounts[1].security_work_authorized = False
            await session.commit()
            assigned = accounts if scope == "mixed" else [accounts[0 if scope == "authorized" else 1]]
            key = await ApiKeysService(ApiKeysRepository(session)).create_key(
                ApiKeyCreateData(
                    name="ws-cardinality", allowed_models=None, assigned_account_ids=[a.id for a in assigned]
                )
            )
        settings = await client.put("/api/settings", json={"apiKeyAuthEnabled": True, "stickyThreadsEnabled": False})
        assert settings.status_code == 200
    return key.key, accounts


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("marker", [None, "turn_unregistered_security", "http_turn_unregistered_security"])
@pytest.mark.parametrize("scope", ["mixed", "authorized", "unauthorized"])
@pytest.mark.parametrize("reuse", [False, True], ids=["initial", "reused"])
def test_websocket_security_requirement_does_not_prove_unknown_owner(
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    marker: str | None,
    scope: Literal["mixed", "authorized", "unauthorized"],
    reuse: bool,
) -> None:
    batches = [_websocket_response_batch("resp_ws_cardinality_complete")]
    if reuse:
        batches.insert(0, _websocket_response_batch("resp_ws_cardinality_bootstrap"))
    upstream = _SequencedUpstreamWebSocket([], deferred_message_batches=batches)
    opened: list[str] = []

    async def open_upstream(
        self: proxy_module.ProxyService, account: Account, headers: dict[str, str], **kwargs: object
    ) -> tuple[Account, _SequencedUpstreamWebSocket]:
        opened.append(account.id)
        return account, upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_try_open_websocket_connect_attempt", open_upstream)
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        key, accounts = client.portal.call(_scoped_accounts, app_instance, scope)
        if reuse:
            # Establish the socket through independent file ownership. An
            # unregistered marker alone must not select from the mixed pool.
            owner = accounts[1 if scope == "unauthorized" else 0]
            client.portal.call(
                get_proxy_service_for_app(app_instance)._pin_file_account,
                "file_cardinality_bootstrap",
                owner.id,
            )
        headers = {"Authorization": f"Bearer {key}"}
        if not (reuse and scope == "unauthorized"):
            headers[REQUIRED_CAPABILITY_HEADER] = "trusted_cyber"
        if marker is not None:
            headers["x-codex-turn-state"] = marker
        with client.websocket_connect(f"ws://localhost{path}", headers=headers) as websocket:
            if reuse:
                websocket.send_json(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": [
                            {
                                "role": "user",
                                "content": [{"type": "input_file", "file_id": "file_cardinality_bootstrap"}],
                            }
                        ],
                    }
                )
                assert websocket.receive_json()["type"] == "response.created"
                assert websocket.receive_json()["type"] == "response.completed"
            request: dict[str, object] = {
                "type": "response.create",
                "model": "gpt-5.4",
                "input": "continue",
                "previous_response_id": "resp_ws_security_unknown_owner",
            }
            if REQUIRED_CAPABILITY_HEADER not in headers:
                request["client_metadata"] = {REQUIRED_CAPABILITY_HEADER: "trusted_cyber"}
            websocket.send_json(request)
            event = websocket.receive_json()
            if scope == "authorized":
                assert event["type"] == "response.created", event
                assert websocket.receive_json()["type"] == "response.completed"
            elif scope == "mixed":
                assert event["type"] == "response.failed", event
                assert event["response"]["error"]["code"] == "previous_response_owner_unavailable"
            else:
                assert event["type"] == "codex_lb.warning", event
                assert event["warning"]["code"] == "no_security_work_authorized_accounts"
                terminal = websocket.receive_json()
                assert terminal["type"] == "error", terminal
                assert terminal["error"]["code"] == "no_security_work_authorized_accounts"
    expected_owner = accounts[1 if scope == "unauthorized" else 0].id
    assert opened == ([expected_owner] if reuse or scope == "authorized" else [])
    assert len(upstream.sent_text) == int(reuse) + int(scope == "authorized")
    if scope == "authorized":
        assert json.loads(upstream.sent_text[-1])["previous_response_id"] == "resp_ws_security_unknown_owner"
