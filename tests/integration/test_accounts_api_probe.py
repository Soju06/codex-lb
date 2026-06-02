from __future__ import annotations

import base64
import json

import pytest

from app.core.auth import generate_unique_account_id
from app.core.auth.refresh import RefreshError
from app.modules.accounts.service import AccountsService

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_test_account(async_client, *, email: str, account_id: str) -> str:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token-not-a-real-secret",
            "refreshToken": "refresh",
            "accountId": account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200, response.text
    return generate_unique_account_id(account_id, email)


@pytest.mark.asyncio
async def test_probe_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/probe")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_probe_paused_account_returns_409(async_client, monkeypatch):
    async def _fake_probe(self, **kwargs):  # noqa: ARG001 - signature match only
        raise AssertionError("paused account should not invoke upstream probe")

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)

    account_id = await _import_test_account(
        async_client,
        email="probe-paused@example.com",
        account_id="acc_probe_paused",
    )
    pause_resp = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert pause_resp.status_code == 200

    response = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_not_probable"


@pytest.mark.asyncio
async def test_probe_refresh_failure_returns_structured_409(async_client, monkeypatch):
    async def _fail_probe(self, account_id, model=None):  # noqa: ARG001 - route-level error handling only
        raise RefreshError(
            code="invalid_grant",
            message="refresh token revoked",
            is_permanent=True,
        )

    monkeypatch.setattr(AccountsService, "probe_account", _fail_probe)

    response = await async_client.post("/api/accounts/acc_refresh_failed/probe")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_probe_refresh_failed"
    assert "refresh token revoked" in body["error"]["message"]


@pytest.mark.asyncio
async def test_probe_active_account_returns_snapshot(async_client, monkeypatch):
    captured: dict = {}

    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):
        captured["model"] = model
        captured["chatgpt_account_id"] = chatgpt_account_id
        # Do not capture the access token — only assert it was non-empty.
        captured["had_token"] = bool(access_token)
        return 200

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)

    account_id = await _import_test_account(
        async_client,
        email="probe-active@example.com",
        account_id="acc_probe_active",
    )

    response = await async_client.post(
        f"/api/accounts/{account_id}/probe",
        json={"model": "gpt-5.5-test"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "probed"
    assert body["accountId"] == account_id
    assert body["probeStatusCode"] == 200
    assert body["accountStatusBefore"] == "active"
    assert body["accountStatusAfter"] == "active"

    assert captured["model"] == "gpt-5.5-test"
    assert captured["chatgpt_account_id"] == "acc_probe_active"
    assert captured["had_token"] is True


@pytest.mark.asyncio
async def test_probe_uses_default_model_when_body_omitted(async_client, monkeypatch):
    captured: dict = {}

    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):  # noqa: ARG001
        captured["model"] = model
        return 200

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)

    account_id = await _import_test_account(
        async_client,
        email="probe-default-model@example.com",
        account_id="acc_probe_default_model",
    )

    response = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert response.status_code == 200, response.text
    # The default model is service-owned; assert the helper was called with
    # *some* model string rather than coupling the test to the constant.
    assert isinstance(captured["model"], str)
    assert captured["model"]
