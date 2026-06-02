from __future__ import annotations

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
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
async def test_device_oauth_reauth_reuses_existing_row_for_same_chatgpt_identity(
    async_client,
    monkeypatch,
):
    """OAuth reauth for the same ChatGPT identity must reuse the existing
    local row even when ``importWithoutOverwrite`` is enabled.

    Before #788, this code path created an ``__copyN`` row whenever the
    operator had toggled ``importWithoutOverwrite`` on, because the
    dashboard's side-by-side import setting was incorrectly conflated
    with reauth.

    The ``importWithoutOverwrite`` setting now governs the dashboard
    import path only (side-by-side rows when importing twice). The
    reauth path always reconciles to one local row per upstream
    ChatGPT identity, so a refresh-token-revoked account picks up the
    new tokens onto its historical row instead of forking a duplicate.
    """

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

    email = "device-reauth@example.com"
    raw_account_id = "acc_device_reauth"

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
    assert len(data) == 1
    base_id = generate_unique_account_id(raw_account_id, email)
    assert data[0]["accountId"] == base_id
    # Second reauth carried the team plan; it must be applied to the
    # existing row rather than a new __copy row.
    assert data[0]["planType"] == "team"


@pytest.mark.asyncio
async def test_oauth_persist_tokens_invalidates_routing_caches_after_identity_merge(monkeypatch):
    repo = AsyncMock()
    service = oauth_module.OauthService(repo)
    account_cache = SimpleNamespace(invalidated=False)

    def _invalidate_account_cache() -> None:
        account_cache.invalidated = True

    account_cache.invalidate = _invalidate_account_cache
    api_key_cache = SimpleNamespace(cleared=False)

    def _clear_api_key_cache() -> None:
        api_key_cache.cleared = True

    api_key_cache.clear = _clear_api_key_cache
    poller = SimpleNamespace(bumped=[])

    async def _bump(namespace: str) -> None:
        poller.bumped.append(namespace)

    poller.bump = _bump
    monkeypatch.setattr(oauth_module, "get_account_selection_cache", lambda: account_cache, raising=False)
    monkeypatch.setattr(oauth_module, "get_api_key_cache", lambda: api_key_cache, raising=False)
    monkeypatch.setattr(oauth_module, "get_cache_invalidation_poller", lambda: poller, raising=False)
    monkeypatch.setattr(oauth_module, "NAMESPACE_API_KEY", "api_key", raising=False)

    payload = {
        "email": "reauth-cache@example.com",
        "chatgpt_account_id": "acc_reauth_cache",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }

    await service._persist_tokens(
        OAuthTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            id_token=_encode_jwt(payload),
        )
    )

    repo.upsert.assert_awaited_once()
    assert account_cache.invalidated is True
    assert api_key_cache.cleared is True
    assert poller.bumped == ["api_key"]


@pytest.mark.asyncio
async def test_device_oauth_flow_keeps_same_email_distinct_upstream_identities_in_overwrite_mode(
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
        # Each of the first two flows uses a *different* upstream
        # chatgpt_account_id so that identity-aware reauth treats them
        # as distinct upstream identities and keeps both local rows.
        # The third flow then introduces a third upstream id under the
        # same email. OAuth/reauth is keyed by upstream identity rather
        # than email, so the overwrite-by-email import setting must not
        # collapse this credential slot.
        call_count["value"] += 1
        if call_count["value"] == 1:
            account_id = "acc_oauth_conflict_one"
            plan_type = "plus"
        elif call_count["value"] == 2:
            account_id = "acc_oauth_conflict_two"
            plan_type = "team"
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
    assert result["status"] == "success"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    matching_accounts = [account for account in accounts.json()["accounts"] if account["email"] == email]
    assert {account["accountId"] for account in matching_accounts} == {
        generate_unique_account_id("acc_oauth_conflict_one", email),
        generate_unique_account_id("acc_oauth_conflict_two", email),
        generate_unique_account_id("acc_oauth_conflict_new", email),
    }


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
