from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.db.models import Account
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.request_logs.repository import RequestLogsRepository
from tests.integration.test_model_source_routing import _create_model_source, _set_source_enabled
from tests.integration.test_proxy_responses import _disable_http_bridge, _make_auth_json  # noqa: F401

pytestmark = pytest.mark.integration

_RESPONSE_PATHS = [
    "/backend-api/codex/responses",
    "/backend-api/codex/responses/",
    "/v1/responses",
    "/v1/responses/",
]
_MODEL = "source-continuity-regression-model"


async def _create_disabled_source(client: AsyncClient) -> None:
    source_id = await _create_model_source(
        client,
        name="continuity-regression-source",
        model=_MODEL,
        base_url="http://127.0.0.1:1/v1",
        supports_responses=True,
    )
    await _set_source_enabled(client, source_id, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _RESPONSE_PATHS)
@pytest.mark.parametrize("turn_state", ["turn_registered_owner", "http_turn_registered_owner"])
@pytest.mark.parametrize("source_disabled", [False, True], ids=["source-missing", "source-disabled"])
async def test_registered_synthetic_turn_keeps_subscription_owner_without_previous_response(
    async_client, app_instance, monkeypatch, path, turn_state, source_disabled
):
    if source_disabled:
        await _create_disabled_source(async_client)
    for raw_id in ("acc_continuity_owner", "acc_continuity_other"):
        auth = _make_auth_json(raw_id, f"{raw_id}@example.com")
        imported = await async_client.post(
            "/api/accounts/import",
            files={"auth_json": ("auth.json", json.dumps(auth), "application/json")},
        )
        assert imported.status_code == 200
    async with SessionLocal() as session:
        accounts = {account.chatgpt_account_id: account for account in (await session.scalars(select(Account))).all()}
    owner = accounts["acc_continuity_owner"]
    other = accounts["acc_continuity_other"]
    service = get_proxy_service_for_app(app_instance)
    claim = await service._durable_bridge.claim_live_session(
        session_key_kind="prompt_cache",
        session_key_value="registered-synthetic-owner",
        api_key_id=None,
        instance_id="continuity-regression-instance",
        owner_process_epoch="continuity-regression-process",
        lease_ttl_seconds=60.0,
        account_id=owner.id,
        model=_MODEL,
        service_tier=None,
        latest_turn_state=turn_state,
        latest_response_id=None,
        allow_takeover=True,
    )
    await service._durable_bridge.register_turn_state(
        session_id=claim.session_id,
        api_key_id=None,
        instance_id="continuity-regression-instance",
        owner_epoch=claim.owner_epoch,
        turn_state=turn_state,
        lease_ttl_seconds=60.0,
    )
    sent_accounts: list[str | None] = []

    async def select_account(self, deadline, **kwargs):
        # Without continuity proof, ordinary selection prefers the other account.
        account = owner if kwargs.get("preferred_account_id") == owner.id else other
        return proxy_module.AccountSelection(account=account, error_message=None, error_code=None)

    async def ensure_fresh(self, account, **kwargs):
        return account

    async def stream(payload, headers, access_token, account_id, **kwargs):
        sent_accounts.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_registered_owner",'
            '"object":"response","status":"completed","output":[],"usage":'
            '{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)

    response = await async_client.post(
        path,
        headers={"x-codex-turn-state": turn_state},
        json={"model": _MODEL, "input": "continue", "stream": True},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert '"response.completed"' in response.text
    assert sent_accounts == ["acc_continuity_owner"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _RESPONSE_PATHS)
async def test_disabled_source_owner_lookup_failure_preserves_sanitized_http_error(
    async_client, app_instance, monkeypatch, path
):
    await _create_disabled_source(async_client)
    lookups: list[str] = []

    async def fail_owner_lookup(self, *, response_id, api_key_id, session_id=None):
        lookups.append(response_id)
        raise RuntimeError("private database connection detail")

    monkeypatch.setattr(RequestLogsRepository, "find_latest_owner_record_for_response_id", fail_owner_lookup)
    # Disable transport exception propagation to check the envelope a client sees.
    async with AsyncClient(
        transport=ASGITransport(app=app_instance, raise_app_exceptions=False),
        base_url="http://testserver",
        follow_redirects=True,
    ) as client:
        response = await client.post(
            path,
            json={
                "model": _MODEL,
                "input": "continue",
                "previous_response_id": "resp_disabled_source_owner",
                "stream": True,
            },
        )

    assert lookups == ["resp_disabled_source_owner"]
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert response.json()["error"]["type"] == "server_error"
    assert response.json()["error"]["message"] == "Previous response owner lookup failed; retry later."
    assert "private database connection detail" not in response.text
