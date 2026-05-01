from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

import app.modules.oauth.service as oauth_module
from app.core.auth import generate_unique_account_id
from app.core.clients.oauth import DeviceCode, OAuthTokens
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _oauth_state_token(authorization_url: str) -> str:
    parsed = urlparse(authorization_url)
    return parse_qs(parsed.query)["state"][0]


@pytest.mark.asyncio
async def test_device_oauth_flow_creates_account(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    email = "device@example.com"
    raw_account_id = "acc_device"

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_123",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    async def fake_exchange_device_token(**_):
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
        }
        return OAuthTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            id_token=_encode_jwt(payload),
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert start.status_code == 200
    assert start.json()["method"] == "device"

    complete = await async_client.post("/api/oauth/complete", json={})
    assert complete.status_code == 200
    assert complete.json()["status"] == "pending"

    await asyncio.sleep(0)

    payload = None
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        assert status.status_code == 200
        payload = status.json()
        if payload["status"] == "success":
            break
        await asyncio.sleep(0.05)
    assert payload and payload["status"] == "success"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert any(account["accountId"] == expected_account_id for account in data)


@pytest.mark.asyncio
async def test_device_oauth_flow_keeps_separate_accounts_when_import_without_overwrite_enabled(
    async_client,
    monkeypatch,
):
    await oauth_module._OAUTH_STORE.reset()

    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is True

    email = "device-separate@example.com"
    raw_account_id = "acc_device_separate"

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_sep",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    call_count = {"value": 0}

    async def fake_exchange_device_token(**_):
        call_count["value"] += 1
        plan_type = "plus" if call_count["value"] == 1 else "team"
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
        }
        return OAuthTokens(
            access_token=f"access-token-{call_count['value']}",
            refresh_token=f"refresh-token-{call_count['value']}",
            id_token=_encode_jwt(payload),
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    async def _run_device_flow_once() -> None:
        start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
        assert start.status_code == 200
        assert start.json()["method"] == "device"

        complete = await async_client.post("/api/oauth/complete", json={})
        assert complete.status_code == 200
        assert complete.json()["status"] == "pending"

        await asyncio.sleep(0)

        payload = None
        for _ in range(20):
            status = await async_client.get("/api/oauth/status")
            assert status.status_code == 200
            payload = status.json()
            if payload["status"] == "success":
                break
            await asyncio.sleep(0.05)
        assert payload and payload["status"] == "success"

    await _run_device_flow_once()
    await _run_device_flow_once()

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = [account for account in accounts.json()["accounts"] if account["email"] == email]
    assert len(data) == 2
    ids = {account["accountId"] for account in data}
    base_id = generate_unique_account_id(raw_account_id, email)
    assert base_id in ids
    assert any(account_id.startswith(f"{base_id}__copy") for account_id in ids if account_id != base_id)


@pytest.mark.asyncio
async def test_device_oauth_flow_reports_error_when_duplicate_email_is_ambiguous_in_overwrite_mode(
    async_client,
    monkeypatch,
):
    await oauth_module._OAUTH_STORE.reset()

    enable_separate = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert enable_separate.status_code == 200
    assert enable_separate.json()["importWithoutOverwrite"] is True

    email = "oauth-conflict@example.com"
    raw_account_id = "acc_oauth_conflict_base"

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_conflict",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    call_count = {"value": 0}

    async def fake_exchange_device_token(**_):
        call_count["value"] += 1
        if call_count["value"] <= 2:
            account_id = raw_account_id
            plan_type = "plus" if call_count["value"] == 1 else "team"
        else:
            account_id = "acc_oauth_conflict_new"
            plan_type = "pro"
        payload = {
            "email": email,
            "chatgpt_account_id": account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
        }
        return OAuthTokens(
            access_token=f"access-token-{call_count['value']}",
            refresh_token=f"refresh-token-{call_count['value']}",
            id_token=_encode_jwt(payload),
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    async def _run_device_flow_once() -> dict[str, str | None]:
        start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
        assert start.status_code == 200
        assert start.json()["method"] == "device"

        complete = await async_client.post("/api/oauth/complete", json={})
        assert complete.status_code == 200
        assert complete.json()["status"] == "pending"

        await asyncio.sleep(0)

        payload: dict[str, str | None] | None = None
        for _ in range(20):
            status = await async_client.get("/api/oauth/status")
            assert status.status_code == 200
            payload = status.json()
            if payload["status"] in {"success", "error"}:
                break
            await asyncio.sleep(0.05)
        assert payload is not None
        return payload

    assert (await _run_device_flow_once())["status"] == "success"
    assert (await _run_device_flow_once())["status"] == "success"

    enable_overwrite = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
        },
    )
    assert enable_overwrite.status_code == 200
    assert enable_overwrite.json()["importWithoutOverwrite"] is False

    result = await _run_device_flow_once()
    assert result["status"] == "error"
    assert result["errorMessage"] is not None
    assert "multiple matching accounts exist" in str(result["errorMessage"]).lower()


@pytest.mark.asyncio
async def test_oauth_start_with_existing_account_marks_success(async_client):
    await oauth_module._OAUTH_STORE.reset()

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_existing",
        email="existing@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(account)

    start = await async_client.post("/api/oauth/start", json={})
    assert start.status_code == 200
    assert start.json()["method"] == "browser"

    status = await async_client.get("/api/oauth/status")
    assert status.status_code == 200
    assert status.json()["status"] == "success"


@pytest.mark.asyncio
async def test_oauth_start_falls_back_to_device_on_os_error(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_browser_flow(self):
        raise OSError("no port")

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_fallback",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    monkeypatch.setattr(oauth_module.OauthService, "_start_browser_flow", fake_browser_flow)
    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)

    start = await async_client.post("/api/oauth/start", json={})
    assert start.status_code == 200
    payload = start.json()
    assert payload["method"] == "device"
    assert payload["deviceAuthId"] == "dev_fallback"


@pytest.mark.asyncio
async def test_manual_callback_returns_success_and_creates_account(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    email = "manual@example.com"
    raw_account_id = "acc_manual"

    async def fake_exchange_authorization_code(**_):
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
        }
        return OAuthTokens(
            access_token="manual-access-token",
            refresh_token="manual-refresh-token",
            id_token=_encode_jwt(payload),
        )

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    assert start.json()["method"] == "browser"

    async with oauth_module._OAUTH_STORE.lock:
        state_token = oauth_module._OAUTH_STORE.state.state_token

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": f"http://localhost:1455/auth/callback?code=manual-code&state={state_token}",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success", "errorMessage": None}

    status = await async_client.get("/api/oauth/status")
    assert status.status_code == 200
    assert status.json()["status"] == "success"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert any(account["accountId"] == expected_account_id for account in data)


@pytest.mark.asyncio
async def test_manual_callback_returns_error_message_for_invalid_state(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    assert start.json()["method"] == "browser"

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": "http://localhost:1455/auth/callback?code=manual-code&state=wrong",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "errorMessage": "Invalid OAuth callback: state mismatch or missing code.",
    }

    status = await async_client.get("/api/oauth/status")
    assert status.status_code == 200
    assert status.json() == {
        "status": "error",
        "errorMessage": "Invalid OAuth callback: state mismatch or missing code.",
    }


@pytest.mark.asyncio
async def test_concurrent_browser_oauth_flows_keep_callbacks_isolated(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    async def fake_exchange_authorization_code(**kwargs):
        code = kwargs["code"]
        payload = {
            "email": f"{code}@example.com",
            "chatgpt_account_id": f"acc_{code}",
            "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
        }
        return OAuthTokens(
            access_token=f"access-{code}",
            refresh_token=f"refresh-{code}",
            id_token=_encode_jwt(payload),
        )

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    first_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert first_start.status_code == 200
    first_payload = first_start.json()
    assert first_payload["method"] == "browser"
    assert first_payload["flowId"]

    second_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert second_start.status_code == 200
    second_payload = second_start.json()
    assert second_payload["method"] == "browser"
    assert second_payload["flowId"]
    assert second_payload["flowId"] != first_payload["flowId"]

    first_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": (
                f"http://localhost:1455/auth/callback?code=code-first&state="
                f"{_oauth_state_token(first_payload['authorizationUrl'])}"
            ),
            "flowId": first_payload["flowId"],
        },
    )
    assert first_response.status_code == 200
    assert first_response.json() == {"status": "success", "errorMessage": None}

    second_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": (
                f"http://localhost:1455/auth/callback?code=code-second&state="
                f"{_oauth_state_token(second_payload['authorizationUrl'])}"
            ),
            "flowId": second_payload["flowId"],
        },
    )
    assert second_response.status_code == 200
    assert second_response.json() == {"status": "success", "errorMessage": None}

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {"status": "success", "errorMessage": None}

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "success", "errorMessage": None}

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    expected_ids = {
        generate_unique_account_id("acc_code-first", "code-first@example.com"),
        generate_unique_account_id("acc_code-second", "code-second@example.com"),
    }
    assert expected_ids.issubset({account["accountId"] for account in data})
