from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import cast
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


def test_oauth_error_html_escapes_message() -> None:
    html = oauth_module._error_html("<script>alert('x')</script>")

    assert "<script>" not in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _oauth_state_token(authorization_url: str) -> str:
    parsed = urlparse(authorization_url)
    return parse_qs(parsed.query)["state"][0]


def _proxy_auth_fixture(suffix: str = "primary") -> str:
    return f"proxy-fixture-value-{suffix}"


def _proxy_user_fixture() -> str:
    return "proxy-user-fixture"


@pytest.mark.asyncio
async def test_device_oauth_flow_creates_account(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    email = "device@example.com"
    raw_account_id = "acc_device"
    captured_sessions = {"device_code": False, "token_exchange": False}

    async def fake_device_code(**kwargs):
        captured_sessions["device_code"] = kwargs.get("session") is not None
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_123",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    async def fake_exchange_device_token(**kwargs):
        captured_sessions["token_exchange"] = kwargs.get("session") is not None
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
    assert captured_sessions == {"device_code": False, "token_exchange": False}


@pytest.mark.asyncio
async def test_starting_new_device_flow_cancels_previous_pending_poll(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()
    issued = 0

    async def fake_device_code(**_):
        nonlocal issued
        issued += 1
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code=f"CODE-{issued}",
            device_auth_id=f"dev_{issued}",
            interval_seconds=30,
            expires_in_seconds=300,
        )

    async def fake_exchange_device_token(**_):
        await asyncio.sleep(300)
        raise AssertionError("device token polling should be cancelled by the test")

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)

    first = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert first.status_code == 200
    await asyncio.sleep(0)
    async with oauth_module._OAUTH_STORE.lock:
        first_flow_id = first.json()["flowId"]
        first_flow = oauth_module._OAUTH_STORE.get_flow_locked(first_flow_id)
        assert first_flow is not None
        first_task = first_flow.poll_task
        assert first_task is not None

    second = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert second.status_code == 200
    second_flow_id = second.json()["flowId"]
    await asyncio.sleep(0)

    async with oauth_module._OAUTH_STORE.lock:
        pending_device_flows = [
            flow
            for flow in oauth_module._OAUTH_STORE._flows.values()
            if flow.method == "device" and flow.status == "pending"
        ]
        assert [flow.flow_id for flow in pending_device_flows] == [second_flow_id]
        assert oauth_module._OAUTH_STORE.get_flow_locked(first_flow_id) is None
    assert first_task.cancelled()

    await oauth_module._OAUTH_STORE.reset()


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
            current_payload: dict[str, str | None] = status.json()
            payload = current_payload
            if current_payload["status"] in {"success", "error"}:
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
async def test_oauth_start_with_existing_account_clears_stale_flows(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    stale_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert stale_start.status_code == 200
    stale_payload = stale_start.json()
    assert stale_payload["flowId"]

    async with oauth_module._OAUTH_STORE.lock:
        assert oauth_module._OAUTH_STORE._flows
        assert oauth_module._OAUTH_STORE._state_token_index

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_existing_after_stale_flow",
        email="existing-after-stale-flow@example.com",
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
    assert status.json() == {"status": "success", "errorMessage": None}

    async with oauth_module._OAUTH_STORE.lock:
        assert oauth_module._OAUTH_STORE._flows == {}
        assert oauth_module._OAUTH_STORE._state_token_index == {}


@pytest.mark.asyncio
async def test_terminal_oauth_flows_are_bounded_outside_full_reset():
    await oauth_module._OAUTH_STORE.reset()

    retained_limit = oauth_module._MAX_RETAINED_TERMINAL_OAUTH_FLOWS

    async with oauth_module._OAUTH_STORE.lock:
        for index in range(retained_limit + 2):
            flow = oauth_module.OAuthState(
                flow_id=f"flow-{index}",
                status="pending",
                method="browser",
                state_token=f"state-{index}",
                code_verifier=f"verifier-{index}",
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(flow)
            oauth_module._OAUTH_STORE.set_flow_status_locked(
                flow,
                status="error",
                error_message=f"failure-{index}",
            )

        assert len(oauth_module._OAUTH_STORE._flows) == retained_limit
        assert "flow-0" not in oauth_module._OAUTH_STORE._flows
        assert "flow-1" not in oauth_module._OAUTH_STORE._flows
        assert "state-0" not in oauth_module._OAUTH_STORE._state_token_index
        assert "state-1" not in oauth_module._OAUTH_STORE._state_token_index
        assert f"flow-{retained_limit + 1}" in oauth_module._OAUTH_STORE._flows
        assert oauth_module._OAUTH_STORE.state.error_message == f"failure-{retained_limit + 1}"


@pytest.mark.asyncio
async def test_expired_pending_browser_oauth_flows_are_pruned():
    await oauth_module._OAUTH_STORE.reset()

    now = time.time()
    async with oauth_module._OAUTH_STORE.lock:
        expired = oauth_module.OAuthState(
            flow_id="expired-flow",
            status="pending",
            method="browser",
            state_token="expired-state",
            code_verifier="expired-verifier",
            expires_at=now - 1,
        )
        active = oauth_module.OAuthState(
            flow_id="active-flow",
            status="pending",
            method="browser",
            state_token="active-state",
            code_verifier="active-verifier",
            expires_at=now + oauth_module._PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS,
        )
        oauth_module._OAUTH_STORE.remember_flow_locked(expired)
        oauth_module._OAUTH_STORE.remember_flow_locked(active)

        assert oauth_module._OAUTH_STORE.has_pending_browser_flows_locked()
        assert "expired-flow" not in oauth_module._OAUTH_STORE._flows
        assert "expired-state" not in oauth_module._OAUTH_STORE._state_token_index
        assert oauth_module._OAUTH_STORE.state.flow_id == "active-flow"


@pytest.mark.asyncio
async def test_only_expired_pending_browser_flow_no_longer_keeps_callback_server_alive():
    await oauth_module._OAUTH_STORE.reset()

    async with oauth_module._OAUTH_STORE.lock:
        flow = oauth_module.OAuthState(
            flow_id="expired-flow",
            status="pending",
            method="browser",
            state_token="expired-state",
            code_verifier="expired-verifier",
            expires_at=time.time() - 1,
        )
        oauth_module._OAUTH_STORE.remember_flow_locked(flow)

        assert not oauth_module._OAUTH_STORE.has_pending_browser_flows_locked()
        assert oauth_module._OAUTH_STORE._flows == {}
        assert oauth_module._OAUTH_STORE.state.status == "idle"


@pytest.mark.asyncio
async def test_callback_server_remains_reserved_until_stop_completes():
    await oauth_module._OAUTH_STORE.reset()
    stop_started = asyncio.Event()
    release_stop = asyncio.Event()

    class FakeCallbackServer:
        async def stop(self) -> None:
            stop_started.set()
            await release_stop.wait()

    fake_server = FakeCallbackServer()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))
        async with oauth_module._OAUTH_STORE.lock:
            oauth_module._OAUTH_STORE._callback_server = cast(oauth_module.OAuthCallbackServer, fake_server)

        stop_task = asyncio.create_task(service._stop_callback_server_if_idle())
        await stop_started.wait()
        assert oauth_module._OAUTH_STORE._callback_server is fake_server

        release_stop.set()
        await stop_task
        assert oauth_module._OAUTH_STORE._callback_server is None


@pytest.mark.asyncio
async def test_oauth_start_falls_back_to_device_on_os_error(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_browser_flow(self, **_):
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
    captured_session = object()

    async def fake_exchange_authorization_code(**kwargs):
        nonlocal captured_session
        captured_session = kwargs.get("session")
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
    payload = start.json()
    assert payload["method"] == "browser"

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
    assert captured_session is None

    status = await async_client.get("/api/oauth/status")
    assert status.status_code == 200
    assert status.json()["status"] == "success"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert any(account["accountId"] == expected_account_id for account in data)


@pytest.mark.asyncio
async def test_browser_oauth_redirect_uses_registered_uri_and_matches_token_exchange(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    email = "manual-remote-host@example.com"
    raw_account_id = "acc_manual_remote_host"
    captured_redirect_uri = None

    async def fake_exchange_authorization_code(**kwargs):
        nonlocal captured_redirect_uri
        captured_redirect_uri = kwargs["redirect_uri"]
        return OAuthTokens(
            access_token="manual-remote-access-token",
            refresh_token="manual-remote-refresh-token",
            id_token=_encode_jwt(
                {
                    "email": email,
                    "chatgpt_account_id": raw_account_id,
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ),
        )

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    start = await async_client.post(
        "/api/oauth/start",
        json={"forceMethod": "browser"},
        headers={"host": "dashboard.example.test:2455"},
    )
    assert start.status_code == 200
    payload = start.json()
    expected_callback_url = "http://dashboard.example.test:1455/auth/callback"
    assert payload["callbackUrl"] == expected_callback_url
    assert parse_qs(urlparse(payload["authorizationUrl"]).query)["redirect_uri"] == [expected_callback_url]

    async with oauth_module._OAUTH_STORE.lock:
        state_token = oauth_module._OAUTH_STORE.state.state_token

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": f"{expected_callback_url}?code=manual-code&state={state_token}",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "errorMessage": None}
    assert captured_redirect_uri == expected_callback_url


@pytest.mark.asyncio
async def test_manual_callback_returns_error_message_for_invalid_state(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    payload = start.json()
    assert payload["method"] == "browser"

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
    assert status.json() == {"status": "pending", "errorMessage": None}

    flow_status = await async_client.get("/api/oauth/status", params={"flowId": payload["flowId"]})
    assert flow_status.status_code == 200
    assert flow_status.json() == {"status": "pending", "errorMessage": None}


@pytest.mark.asyncio
async def test_manual_callback_sanitizes_unexpected_errors(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    async def fake_exchange_authorization_code(**_):
        raise RuntimeError("secret proxy://user:password@proxy.example.com:1080")

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    state_token = _oauth_state_token(start.json()["authorizationUrl"])

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": f"http://localhost:1455/auth/callback?code=manual-code&state={state_token}",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "errorMessage": "Unexpected OAuth callback error.",
    }


@pytest.mark.asyncio
async def test_manual_callback_is_idempotent_for_same_attempt(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    exchange_calls: list[str] = []

    async def fake_exchange_authorization_code(**kwargs):
        code = kwargs["code"]
        exchange_calls.append(code)
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

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    payload = start.json()
    state_token = _oauth_state_token(payload["authorizationUrl"])

    first = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": f"http://localhost:1455/auth/callback?code=manual-code&state={state_token}",
            "flowId": payload["flowId"],
        },
    )
    assert first.status_code == 200
    assert first.json() == {"status": "success", "errorMessage": None}

    second = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": f"http://localhost:1455/auth/callback?code=manual-code&state={state_token}",
            "flowId": payload["flowId"],
        },
    )
    assert second.status_code == 200
    assert second.json() == {"status": "success", "errorMessage": None}
    assert exchange_calls == ["manual-code"]


@pytest.mark.asyncio
async def test_oauth_status_binds_camel_case_flow_id(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    first_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert first_start.status_code == 200
    first_payload = first_start.json()
    assert first_payload["flowId"]

    second_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert second_start.status_code == 200
    second_payload = second_start.json()
    assert second_payload["flowId"]
    assert second_payload["flowId"] != first_payload["flowId"]

    error_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": "http://localhost:1455/auth/callback?code=manual-code&state=wrong",
            "flowId": second_payload["flowId"],
        },
    )
    assert error_response.status_code == 200
    assert error_response.json()["status"] == "error"

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {"status": "pending", "errorMessage": None}

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "pending", "errorMessage": None}

    typo_status = await async_client.get("/api/oauth/status", params={"flowId": f"{second_payload['flowId']}-typo"})
    assert typo_status.status_code == 200
    assert typo_status.json() == {"status": "pending", "errorMessage": None}

    latest_status = await async_client.get("/api/oauth/status")
    assert latest_status.status_code == 200
    assert latest_status.json() == {"status": "pending", "errorMessage": None}


@pytest.mark.asyncio
async def test_manual_callback_error_resolves_state_before_marking_flow_failed(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    first_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert first_start.status_code == 200
    first_payload = first_start.json()
    first_error_url = (
        "http://localhost:1455/auth/callback?error=access_denied&state="
        f"{_oauth_state_token(first_payload['authorizationUrl'])}"
    )

    second_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert second_start.status_code == 200
    second_payload = second_start.json()
    assert second_payload["flowId"] != first_payload["flowId"]

    mismatched_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": first_error_url,
            "flowId": second_payload["flowId"],
        },
    )
    assert mismatched_response.status_code == 200
    assert mismatched_response.json() == {
        "status": "error",
        "errorMessage": "OAuth error: access_denied",
    }

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {"status": "pending", "errorMessage": None}

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "pending", "errorMessage": None}

    matching_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": first_error_url,
            "flowId": first_payload["flowId"],
        },
    )
    assert matching_response.status_code == 200
    assert matching_response.json() == {
        "status": "error",
        "errorMessage": "OAuth error: access_denied",
    }

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {
        "status": "error",
        "errorMessage": "OAuth error: access_denied",
    }

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "pending", "errorMessage": None}


@pytest.mark.asyncio
async def test_unknown_flow_error_does_not_mutate_latest_oauth_status():
    await oauth_module._OAUTH_STORE.reset()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))

        async with oauth_module._OAUTH_STORE.lock:
            latest = oauth_module.OAuthState(
                flow_id="latest-flow",
                status="pending",
                method="browser",
                state_token="latest-state",
                code_verifier="latest-verifier",
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(latest)
            oauth_module._OAUTH_STORE.set_latest_flow_locked(latest)

        await service._set_error("wrong flow", flow_id="missing-flow")

        async with oauth_module._OAUTH_STORE.lock:
            latest_state = oauth_module._OAUTH_STORE.state
            latest_flow = oauth_module._OAUTH_STORE.get_flow_locked("latest-flow")

    assert latest_state.status == "pending"
    assert latest_state.error_message is None
    assert latest_flow is not None
    assert latest_flow.status == "pending"
    assert latest_flow.error_message is None


@pytest.mark.asyncio
async def test_missing_flow_error_does_not_mutate_latest_oauth_status():
    await oauth_module._OAUTH_STORE.reset()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))

        async with oauth_module._OAUTH_STORE.lock:
            latest = oauth_module.OAuthState(
                flow_id="latest-flow",
                status="pending",
                method="browser",
                state_token="latest-state",
                code_verifier="latest-verifier",
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(latest)
            oauth_module._OAUTH_STORE.set_latest_flow_locked(latest)

        await service._set_error("wrong flow")

        async with oauth_module._OAUTH_STORE.lock:
            latest_state = oauth_module._OAUTH_STORE.state
            latest_flow = oauth_module._OAUTH_STORE.get_flow_locked("latest-flow")

    assert latest_state.status == "pending"
    assert latest_state.error_message is None
    assert latest_flow is not None
    assert latest_flow.status == "pending"
    assert latest_flow.error_message is None


@pytest.mark.asyncio
async def test_manual_callback_unknown_state_does_not_mutate_latest_flow(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    payload = start.json()

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": "http://localhost:1455/auth/callback?error=access_denied&state=missing-state",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "errorMessage": "OAuth error: access_denied",
    }

    status = await async_client.get("/api/oauth/status", params={"flowId": payload["flowId"]})
    assert status.status_code == 200
    assert status.json() == {"status": "pending", "errorMessage": None}


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


@pytest.mark.asyncio
async def test_callback_server_idle_stop_releases_store_lock_before_cleanup():
    await oauth_module._OAUTH_STORE.reset()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))

        class ObservingCallbackServer:
            async def stop(self) -> None:
                assert not oauth_module._OAUTH_STORE.lock.locked()

        async with oauth_module._OAUTH_STORE.lock:
            flow = oauth_module.OAuthState(
                flow_id="finished-browser-flow",
                status="success",
                method="browser",
                state_token="finished-state",
                code_verifier="finished-verifier",
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(flow)
            oauth_module._OAUTH_STORE.set_flow_status_locked(flow, status="success", error_message=None)
            oauth_module._OAUTH_STORE._callback_server = cast(
                oauth_module.OAuthCallbackServer,
                ObservingCallbackServer(),
            )

        await service._stop_callback_server_if_idle()


@pytest.mark.asyncio
async def test_existing_account_cleanup_releases_store_lock_before_callback_server_stop():
    await oauth_module._OAUTH_STORE.reset()

    class ExistingAccountRepo:
        async def list_accounts(self):
            return [object()]

    class ObservingCallbackServer:
        async def stop(self) -> None:
            assert not oauth_module._OAUTH_STORE.lock.locked()

    service = oauth_module.OauthService(cast(AccountsRepository, ExistingAccountRepo()))
    async with oauth_module._OAUTH_STORE.lock:
        flow = oauth_module.OAuthState(
            flow_id="pending-browser-flow",
            status="pending",
            method="browser",
            state_token="pending-state",
            code_verifier="pending-verifier",
        )
        oauth_module._OAUTH_STORE.remember_flow_locked(flow)
        oauth_module._OAUTH_STORE._callback_server = cast(
            oauth_module.OAuthCallbackServer,
            ObservingCallbackServer(),
        )

    response = await service.start_oauth(oauth_module.OauthStartRequest())

    assert response.method == "browser"


@pytest.mark.asyncio
async def test_new_browser_flow_waits_for_stopping_callback_server_before_reusing_slot(monkeypatch):
    await oauth_module._OAUTH_STORE.reset()
    stop_started = asyncio.Event()
    release_stop = asyncio.Event()
    started_servers: list[object] = []

    class StoppingCallbackServer:
        async def stop(self) -> None:
            stop_started.set()
            await release_stop.wait()

    class ReplacementCallbackServer:
        def __init__(self, *_, **__) -> None:
            self.started = False

        async def start(self) -> None:
            self.started = True
            started_servers.append(self)

        async def stop(self) -> None:
            return None

    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))
        stopping_server = StoppingCallbackServer()
        async with oauth_module._OAUTH_STORE.lock:
            oauth_module._OAUTH_STORE._callback_server = cast(oauth_module.OAuthCallbackServer, stopping_server)

        monkeypatch.setattr(oauth_module, "OAuthCallbackServer", ReplacementCallbackServer)
        stop_task = asyncio.create_task(service._stop_callback_server_if_idle())
        await stop_started.wait()

        start_task = asyncio.create_task(service._start_browser_flow())
        await asyncio.sleep(0)
        release_stop.set()

        response = await asyncio.wait_for(start_task, timeout=1)
        await stop_task

        assert response.method == "browser"
        assert len(started_servers) == 1
        async with oauth_module._OAUTH_STORE.lock:
            assert oauth_module._OAUTH_STORE._callback_server is started_servers[0]


@pytest.mark.asyncio
async def test_manual_callback_idempotent_success_requires_requested_flow(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    exchange_calls: list[str] = []

    async def fake_exchange_authorization_code(**kwargs):
        code = kwargs["code"]
        exchange_calls.append(code)
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
    first_callback_url = (
        f"http://localhost:1455/auth/callback?code=code-first&state="
        f"{_oauth_state_token(first_payload['authorizationUrl'])}"
    )

    second_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert second_start.status_code == 200
    second_payload = second_start.json()
    assert second_payload["flowId"] != first_payload["flowId"]

    first_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": first_callback_url,
            "flowId": first_payload["flowId"],
        },
    )
    assert first_response.status_code == 200
    assert first_response.json() == {"status": "success", "errorMessage": None}

    replay_with_wrong_flow = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": first_callback_url,
            "flowId": second_payload["flowId"],
        },
    )
    assert replay_with_wrong_flow.status_code == 200
    assert replay_with_wrong_flow.json() == {
        "status": "error",
        "errorMessage": "Invalid OAuth callback: state mismatch or missing code.",
    }
    assert exchange_calls == ["code-first"]

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {"status": "success", "errorMessage": None}

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "pending", "errorMessage": None}


# ---------------------------------------------------------------------------
# OAuth-with-proxy: deferred persistence + atomic probe via /api/oauth/complete
# ---------------------------------------------------------------------------


def _oauth_tokens_for(email: str, raw_account_id: str) -> OAuthTokens:
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return OAuthTokens(
        access_token="oauth-access-token",
        refresh_token="oauth-refresh-token",
        id_token=_encode_jwt(payload),
    )


def _device_code_fixture() -> "DeviceCode":
    return DeviceCode(
        verification_url="https://auth.openai.com/codex/device",
        user_code="ZYXW-VUTS",
        device_auth_id="dev_proxy",
        interval_seconds=1,
        expires_in_seconds=30,
    )


def _start_device_with_proxy_payload() -> dict[str, object]:
    return {
        "forceMethod": "device",
        "expectProxy": True,
        "proxyHost": "proxy.example.com",
        "proxyPort": 1080,
        "proxyRemoteDns": True,
    }


def _expire_latest_flow_locked() -> None:
    flow = oauth_module._OAUTH_STORE.get_flow_locked(None)
    assert flow is not None
    flow.pending_expires_at = 0.0


@pytest.mark.asyncio
async def test_oauth_start_rejects_proxy_fields_without_expect_proxy(async_client):
    await oauth_module._OAUTH_STORE.reset()

    response = await async_client.post(
        "/api/oauth/start",
        json={
            "forceMethod": "device",
            "expectProxy": False,
            "proxyHost": "proxy.example.com",
            "proxyPort": 1080,
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"] == "Proxy fields require expectProxy=true"


@pytest.mark.asyncio
async def test_oauth_start_with_expected_proxy_does_not_short_circuit_when_accounts_exist(
    async_client,
    monkeypatch,
):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(
            Account(
                id="existing_oauth_account",
                email="existing-oauth@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )

    response = await async_client.post(
        "/api/oauth/start",
        json={
            "expectProxy": True,
            "proxyHost": "proxy.example.com",
            "proxyPort": 1080,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "browser"
    assert body["authorizationUrl"]

    status = await async_client.get("/api/oauth/status")
    assert status.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_oauth_reset_does_not_hold_store_lock_while_stopping_callback_server():
    await oauth_module._OAUTH_STORE.reset()
    stopped_with_lock_available = False

    class LockCheckingServer:
        async def stop(self) -> None:
            nonlocal stopped_with_lock_available
            async with oauth_module._OAUTH_STORE.lock:
                stopped_with_lock_available = True

    async with oauth_module._OAUTH_STORE.lock:
        oauth_module._OAUTH_STORE._callback_server = cast(
            oauth_module.OAuthCallbackServer,
            LockCheckingServer(),
        )

    await asyncio.wait_for(oauth_module._OAUTH_STORE.reset(), timeout=1)

    assert stopped_with_lock_available is True


def _scripted_proxy_probe(result, captured: dict | None = None):
    from app.core.clients.account_proxy_probe import ProbeReason  # noqa: F401

    async def _stub(
        *,
        host,
        port,
        username,
        password,
        remote_dns,
        refresh_token,
        settings=None,
    ):
        if captured is not None:
            captured.update(
                host=host,
                port=port,
                username=username,
                password=password,
                remote_dns=remote_dns,
                refresh_token=refresh_token,
            )
        return result

    return _stub


def _ok_oauth_probe_result():
    from app.core.auth.models import OAuthTokenPayload
    from app.core.clients.account_proxy_probe import ProbeReason, ProbeResult

    return ProbeResult(
        reason=ProbeReason.OK,
        upstream_status_code=200,
        checked_at=utcnow(),
        tokens=OAuthTokenPayload(
            access_token="rotated-oauth-access",
            refresh_token="rotated-oauth-refresh",
            id_token="rotated-oauth-id",
        ),
    )


@pytest.mark.asyncio
async def test_device_oauth_with_expect_proxy_defers_persistence(async_client, monkeypatch):
    """expect_proxy=true must hold device-flow tokens in transient state."""

    await oauth_module._OAUTH_STORE.reset()

    email = "device-defer@example.com"
    raw_account_id = "acc_device_defer"

    async def fake_device_code(**_):
        return _device_code_fixture()

    async def fake_exchange_device_token(**_):
        return _oauth_tokens_for(email, raw_account_id)

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    start = await async_client.post(
        "/api/oauth/start",
        json=_start_device_with_proxy_payload(),
    )
    assert start.status_code == 200

    await asyncio.sleep(0)

    payload = None
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        assert status.status_code == 200
        payload = status.json()
        if payload["status"] == "tokens_ready":
            break
        await asyncio.sleep(0.05)
    assert payload and payload["status"] == "tokens_ready"

    # No Account row exists yet — persistence is deferred to /complete.
    accounts = await async_client.get("/api/accounts")
    expected_account_id = generate_unique_account_id(raw_account_id, email)
    assert not any(a["accountId"] == expected_account_id for a in accounts.json()["accounts"])


@pytest.mark.asyncio
async def test_manual_oauth_with_expect_proxy_uses_proxy_session_and_defers_persistence(
    async_client,
    monkeypatch,
):
    """Browser/manual token exchange must use the configured proxy session."""

    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    email = "manual-proxy@example.com"
    raw_account_id = "acc_manual_proxy"
    captured_session = {"present": False}
    exchange_calls = 0

    async def fake_exchange_authorization_code(**kwargs):
        nonlocal exchange_calls
        exchange_calls += 1
        captured_session["present"] = kwargs.get("session") is not None
        return _oauth_tokens_for(email, raw_account_id)

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    start = await async_client.post(
        "/api/oauth/start",
        json={
            "forceMethod": "browser",
            "expectProxy": True,
            "proxyHost": "proxy.example.com",
            "proxyPort": 1080,
            "proxyRemoteDns": True,
        },
    )
    assert start.status_code == 200

    async with oauth_module._OAUTH_STORE.lock:
        state_token = oauth_module._OAUTH_STORE.state.state_token

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": f"http://localhost:1455/auth/callback?code=manual-code&state={state_token}",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "tokens_ready"
    assert captured_session["present"] is True
    assert exchange_calls == 1

    duplicate = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": f"http://localhost:1455/auth/callback?code=manual-code&state={state_token}",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "tokens_ready"
    assert exchange_calls == 1

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert not any(a["accountId"] == expected_account_id for a in accounts.json()["accounts"])


@pytest.mark.asyncio
async def test_complete_with_proxy_atomically_probes_and_persists(async_client, monkeypatch):
    """Finalize an expect_proxy attempt with proxy fields → atomic probe+upsert."""

    from app.core.crypto import TokenEncryptor

    await oauth_module._OAUTH_STORE.reset()

    email = "oauth-with-proxy@example.com"
    raw_account_id = "acc_oauth_with_proxy"

    async def fake_device_code(**_):
        return _device_code_fixture()

    async def fake_exchange_device_token(**_):
        return _oauth_tokens_for(email, raw_account_id)

    async def fake_sleep(_: float) -> None:
        return None

    captured: dict = {}
    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_proxy_probe(_ok_oauth_probe_result(), captured),
    )

    invalidate_events: list[str] = []

    async def _capture_invalidate(account_id: str):
        invalidate_events.append(account_id)

    monkeypatch.setattr(
        "app.modules.accounts.service.invalidate_account_client",
        _capture_invalidate,
    )

    start = await async_client.post(
        "/api/oauth/start",
        json=_start_device_with_proxy_payload(),
    )
    assert start.status_code == 200

    # Wait for token-arrival to land in pending state.
    await asyncio.sleep(0)
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        if status.json()["status"] == "tokens_ready":
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("did not reach tokens_ready")

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    complete = await async_client.post(
        "/api/oauth/complete",
        json={
            "proxyHost": "proxy.example.com",
            "proxyPort": 1080,
            "proxyUsername": _proxy_user_fixture(),
            "proxyPassword": _proxy_auth_fixture(),
            "proxyRemoteDns": True,
            "proxyLabel": "house-1",
        },
    )
    assert complete.status_code == 200, complete.text
    complete_body = complete.json()
    assert complete_body["status"] == "success"
    assert complete_body["accountId"] == expected_account_id
    assert complete_body["proxy"]["host"] == "proxy.example.com"

    assert captured["host"] == "proxy.example.com"
    assert captured["password"] == _proxy_auth_fixture()
    assert captured["refresh_token"] == "oauth-refresh-token"
    assert invalidate_events == [expected_account_id]

    accounts = await async_client.get("/api/accounts")
    target = next(a for a in accounts.json()["accounts"] if a["accountId"] == expected_account_id)
    assert target["proxy"]["host"] == "proxy.example.com"
    assert target["proxy"]["hasPassword"] is True

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(expected_account_id)
        assert account is not None
        # Rotated tokens (from the probe) MUST be persisted — not the
        # original tokens from the OAuth exchange — so the next refresh
        # uses the proxy-validated token.
        assert encryptor.decrypt(account.access_token_encrypted) == "rotated-oauth-access"
        assert encryptor.decrypt(account.refresh_token_encrypted) == "rotated-oauth-refresh"
        assert encryptor.decrypt(account.id_token_encrypted) == "rotated-oauth-id"


@pytest.mark.asyncio
async def test_complete_with_proxy_probe_failure_preserves_pending_tokens(async_client, monkeypatch):
    """Probe failure must surface 422 and keep tokens for retry."""

    from app.core.clients.account_proxy_probe import ProbeReason, ProbeResult

    await oauth_module._OAUTH_STORE.reset()

    email = "oauth-proxy-fail@example.com"
    raw_account_id = "acc_oauth_proxy_fail"

    async def fake_device_code(**_):
        return _device_code_fixture()

    async def fake_exchange_device_token(**_):
        return _oauth_tokens_for(email, raw_account_id)

    async def fake_sleep(_: float) -> None:
        return None

    probe_attempts: list[str] = []

    async def _failing_probe(**kwargs):
        probe_attempts.append(kwargs["host"])
        if len(probe_attempts) == 1:
            return ProbeResult(reason=ProbeReason.PROXY_AUTH, detail="bad creds", checked_at=utcnow())
        return _ok_oauth_probe_result()

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _failing_probe,
    )

    start = await async_client.post(
        "/api/oauth/start",
        json=_start_device_with_proxy_payload(),
    )
    assert start.status_code == 200

    await asyncio.sleep(0)
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        if status.json()["status"] == "tokens_ready":
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("did not reach tokens_ready")

    # First attempt: bad credentials, probe rejected.
    failed = await async_client.post(
        "/api/oauth/complete",
        json={
            "proxyHost": "proxy.example.com",
            "proxyPort": 1080,
            "proxyPassword": _proxy_auth_fixture("invalid"),
        },
    )
    assert failed.status_code == 422
    body = failed.json()
    assert body["error"]["code"] == "proxy_probe_failed"
    assert body["error"]["reason"] == "proxy_auth"

    # Account MUST NOT be persisted yet.
    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert not any(a["accountId"] == expected_account_id for a in accounts.json()["accounts"])

    # Pending tokens are still held — status remains tokens_ready so the
    # operator can retry with corrected proxy without redoing sign-in.
    status = await async_client.get("/api/oauth/status")
    assert status.json()["status"] == "tokens_ready"

    # Retry with correct credentials. No second device-flow polling: the
    # held tokens are reused.
    succeeded = await async_client.post(
        "/api/oauth/complete",
        json={
            "proxyHost": "proxy.example.com",
            "proxyPort": 1080,
            "proxyPassword": _proxy_auth_fixture("retry"),
        },
    )
    assert succeeded.status_code == 200, succeeded.text
    succeeded_body = succeeded.json()
    assert succeeded_body["status"] == "success"
    assert succeeded_body["proxy"]["host"] == "proxy.example.com"

    accounts = await async_client.get("/api/accounts")
    assert any(
        a["accountId"] == expected_account_id and a["proxy"]["host"] == "proxy.example.com"
        for a in accounts.json()["accounts"]
    )


@pytest.mark.asyncio
async def test_complete_without_proxy_when_expect_proxy_true_rejects_without_persist(async_client, monkeypatch):
    """expect_proxy=true requires proxy fields at finalization."""

    await oauth_module._OAUTH_STORE.reset()

    email = "oauth-defer-noproxy@example.com"
    raw_account_id = "acc_oauth_defer_noproxy"

    async def fake_device_code(**_):
        return _device_code_fixture()

    async def fake_exchange_device_token(**_):
        return _oauth_tokens_for(email, raw_account_id)

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_proxy_probe(_ok_oauth_probe_result()),
    )

    start = await async_client.post(
        "/api/oauth/start",
        json=_start_device_with_proxy_payload(),
    )
    assert start.status_code == 200

    await asyncio.sleep(0)
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        if status.json()["status"] == "tokens_ready":
            break
        await asyncio.sleep(0.05)

    complete = await async_client.post("/api/oauth/complete", json={})
    assert complete.status_code == 422
    assert complete.json()["error"]["code"] == "validation_error"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert not any(a["accountId"] == expected_account_id for a in accounts.json()["accounts"])

    status = await async_client.get("/api/oauth/status")
    assert status.json()["status"] == "tokens_ready"

    retry = await async_client.post(
        "/api/oauth/complete",
        json={"proxyHost": "proxy.example.com", "proxyPort": 1080},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "success"


@pytest.mark.asyncio
async def test_pending_tokens_expire_after_ttl(async_client, monkeypatch):
    """Held tokens MUST be dropped after _PENDING_TOKENS_TTL_SECONDS."""

    await oauth_module._OAUTH_STORE.reset()

    email = "oauth-ttl@example.com"
    raw_account_id = "acc_oauth_ttl"

    async def fake_device_code(**_):
        return _device_code_fixture()

    async def fake_exchange_device_token(**_):
        return _oauth_tokens_for(email, raw_account_id)

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    start = await async_client.post(
        "/api/oauth/start",
        json=_start_device_with_proxy_payload(),
    )
    assert start.status_code == 200

    await asyncio.sleep(0)
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        if status.json()["status"] == "tokens_ready":
            break
        await asyncio.sleep(0.05)

    # Force the TTL into the past so the next status read trips the
    # expiry guard in oauth_status().
    async with oauth_module._OAUTH_STORE.lock:
        _expire_latest_flow_locked()

    status = await async_client.get("/api/oauth/status")
    body = status.json()
    assert body["status"] == "error"
    assert "expired" in (body["errorMessage"] or "")

    # Account MUST NOT be persisted after the expiry.
    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert not any(a["accountId"] == expected_account_id for a in accounts.json()["accounts"])


@pytest.mark.asyncio
async def test_oauth_reset_drops_pending_proxy_tokens(async_client, monkeypatch):
    """Closing/resetting the dialog must drop unpersisted held tokens."""

    await oauth_module._OAUTH_STORE.reset()

    email = "oauth-reset@example.com"
    raw_account_id = "acc_oauth_reset"

    async def fake_device_code(**_):
        return _device_code_fixture()

    async def fake_exchange_device_token(**_):
        return _oauth_tokens_for(email, raw_account_id)

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    start = await async_client.post(
        "/api/oauth/start",
        json=_start_device_with_proxy_payload(),
    )
    assert start.status_code == 200

    await asyncio.sleep(0)
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        if status.json()["status"] == "tokens_ready":
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("did not reach tokens_ready")

    reset = await async_client.post("/api/oauth/reset")
    assert reset.status_code == 200
    assert reset.json()["status"] == "reset"

    complete = await async_client.post(
        "/api/oauth/complete",
        json={"proxyHost": "proxy.example.com", "proxyPort": 1080},
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "pending"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert not any(a["accountId"] == expected_account_id for a in accounts.json()["accounts"])


@pytest.mark.asyncio
async def test_concurrent_proxy_finalization_does_not_double_probe(async_client, monkeypatch):
    """A double-click or duplicate request must not run two proxy probes."""

    await oauth_module._OAUTH_STORE.reset()

    email = "oauth-double-finish@example.com"
    raw_account_id = "acc_oauth_double_finish"

    async def fake_device_code(**_):
        return _device_code_fixture()

    async def fake_exchange_device_token(**_):
        return _oauth_tokens_for(email, raw_account_id)

    async def fake_sleep(_: float) -> None:
        return None

    probe_started = asyncio.Event()
    release_probe = asyncio.Event()
    probe_calls = 0

    async def _blocking_probe(**_kwargs):
        nonlocal probe_calls
        probe_calls += 1
        probe_started.set()
        await release_probe.wait()
        return _ok_oauth_probe_result()

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _blocking_probe,
    )

    start = await async_client.post(
        "/api/oauth/start",
        json=_start_device_with_proxy_payload(),
    )
    assert start.status_code == 200

    await asyncio.sleep(0)
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        if status.json()["status"] == "tokens_ready":
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("did not reach tokens_ready")

    proxy_json = {"proxyHost": "proxy.example.com", "proxyPort": 1080}
    first = asyncio.create_task(async_client.post("/api/oauth/complete", json=proxy_json))
    await asyncio.wait_for(probe_started.wait(), timeout=2)

    second = await async_client.post("/api/oauth/complete", json=proxy_json)
    assert second.status_code == 200
    assert second.json()["status"] == "pending"
    assert probe_calls == 1

    release_probe.set()
    first_response = await first
    assert first_response.status_code == 200, first_response.text
    assert first_response.json()["status"] == "success"
    assert probe_calls == 1

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert sum(1 for account in accounts.json()["accounts"] if account["accountId"] == expected_account_id) == 1


@pytest.mark.asyncio
async def test_complete_rejects_expired_pending_tokens_without_status_poll(async_client, monkeypatch):
    """TTL expiry MUST also be enforced by /complete."""

    await oauth_module._OAUTH_STORE.reset()

    email = "oauth-complete-ttl@example.com"
    raw_account_id = "acc_oauth_complete_ttl"

    async def fake_device_code(**_):
        return _device_code_fixture()

    async def fake_exchange_device_token(**_):
        return _oauth_tokens_for(email, raw_account_id)

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    start = await async_client.post(
        "/api/oauth/start",
        json=_start_device_with_proxy_payload(),
    )
    assert start.status_code == 200

    await asyncio.sleep(0)
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        if status.json()["status"] == "tokens_ready":
            break
        await asyncio.sleep(0.05)

    async with oauth_module._OAUTH_STORE.lock:
        _expire_latest_flow_locked()

    complete = await async_client.post(
        "/api/oauth/complete",
        json={"proxyHost": "proxy.example.com", "proxyPort": 1080},
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "error"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert not any(a["accountId"] == expected_account_id for a in accounts.json()["accounts"])
