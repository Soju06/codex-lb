from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.responses import JSONResponse

import app.modules.oauth.service as oauth_module
from app.core.auth import generate_unique_account_id
from app.core.clients.oauth import DeviceCode, OAuthError, OAuthTokens
from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, OAuthFlowState
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth import api as oauth_api_module
from app.modules.oauth.repository import OAuthFlowRepository
from app.modules.oauth.schemas import ManualCallbackRequest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _oauth_flow_schema(db_setup):
    """Ensure the shared schema (incl. ``oauth_flow_states``) exists.

    Every OAuth service path now persists flow state to the shared DB, so the
    service-level tests that construct ``OauthService`` directly need the table
    present, not only the ``async_client`` ones.
    """

    del db_setup


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _oauth_state_token(authorization_url: str) -> str:
    parsed = urlparse(authorization_url)
    return parse_qs(parsed.query)["state"][0]


@pytest.mark.asyncio
async def test_manual_callback_api_sanitizes_unexpected_exception():
    class FailingOauthService:
        async def manual_callback(self, callback_url: str, flow_id: str | None = None):
            raise RuntimeError("Traceback (most recent call last): password=super-secret")

    response = cast(
        JSONResponse,
        await oauth_api_module.manual_callback(
            ManualCallbackRequest(callback_url="http://localhost:1455/?code=c&state=s"),
            context=cast(Any, SimpleNamespace(service=FailingOauthService())),
        ),
    )

    assert response.status_code == 500
    payload = json.loads(bytes(response.body))
    assert payload == {
        "error": {
            "code": "manual_callback_failed",
            "message": "An internal error occurred.",
        }
    }
    assert "super-secret" not in bytes(response.body).decode()


@pytest.mark.asyncio
async def test_manual_callback_api_preserves_oauth_error():
    class FailingOauthService:
        async def manual_callback(self, callback_url: str, flow_id: str | None = None):
            raise OAuthError("invalid_grant", "Authorization code expired", status_code=400)

    response = cast(
        JSONResponse,
        await oauth_api_module.manual_callback(
            ManualCallbackRequest(callback_url="http://localhost:1455/?code=c&state=s"),
            context=cast(Any, SimpleNamespace(service=FailingOauthService())),
        ),
    )

    assert response.status_code == 502
    assert json.loads(bytes(response.body)) == {
        "error": {
            "code": "invalid_grant",
            "message": "Authorization code expired",
        }
    }


@pytest.mark.asyncio
async def test_manual_callback_service_sanitizes_unexpected_exception(monkeypatch, caplog):
    await oauth_module._OAUTH_STORE.reset()
    caplog.set_level(logging.ERROR, logger=oauth_module.logger.name)
    # Persist the flow durably (real flows are written to the shared DB at start)
    # so the reconciliation gate keeps it rather than dropping it as stale.
    async with SessionLocal() as session:
        await OAuthFlowRepository(session, TokenEncryptor()).create(
            oauth_module.OAuthFlowRecord(
                flow_id="flow-1",
                method="browser",
                status="pending",
                state_token="state-1",
                code_verifier="verifier-1",
            )
        )
    async with oauth_module._OAUTH_STORE.lock:
        oauth_module._OAUTH_STORE.remember_flow_locked(
            oauth_module.OAuthState(
                flow_id="flow-1",
                status="pending",
                method="browser",
                state_token="state-1",
                code_verifier="verifier-1",
            )
        )

    async def fake_oauth_route():
        return None

    async def fake_exchange_authorization_code(**_kwargs):
        raise RuntimeError("Unexpected error: /home/app/password.txt")

    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)
    service = oauth_module.OauthService(cast(AccountsRepository, SimpleNamespace()))

    response = await service.manual_callback("http://localhost:1455/?code=code-1&state=state-1", flow_id="flow-1")

    assert response.status == "error"
    assert response.error_message == "An internal error occurred."
    assert "RuntimeError" in caplog.text
    assert "password.txt" not in caplog.text
    assert "/home/app" not in caplog.text
    assert "Traceback" not in caplog.text
    async with oauth_module._OAUTH_STORE.lock:
        flow = oauth_module._OAUTH_STORE.get_flow_locked("flow-1")
        assert flow is not None
        assert flow.error_message == "An internal error occurred."


def test_oauth_error_html_escapes_message():
    html = oauth_module._error_html("bad <script>alert('x')</script>")

    assert "<script>" not in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html


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
    first_poll_started = asyncio.Event()
    first_poll_cancelled = asyncio.Event()

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

    async def fake_exchange_device_token(*, device_auth_id: str, **_):
        if device_auth_id == "dev_1":
            first_poll_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                first_poll_cancelled.set()
                raise
        await asyncio.Event().wait()
        raise AssertionError("device token polling should not complete in this test")

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)

    first = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert first.status_code == 200
    await asyncio.wait_for(first_poll_started.wait(), timeout=1)
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
    await asyncio.wait_for(first_poll_cancelled.wait(), timeout=1)
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
async def test_device_oauth_flow_heals_deactivated_account_when_import_without_overwrite_enabled(
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

    email = "device-reauth@example.com"
    raw_account_id = "acc_device_reauth"
    account_id = generate_unique_account_id(raw_account_id, email)

    encryptor = TokenEncryptor()
    existing = Account(
        id=account_id,
        chatgpt_account_id=raw_account_id,
        email=email,
        plan_type="plus",
        routing_policy="preserve",
        access_token_encrypted=encryptor.encrypt("old-access"),
        refresh_token_encrypted=encryptor.encrypt("old-refresh"),
        id_token_encrypted=encryptor.encrypt("old-id"),
        last_refresh=utcnow(),
        status=AccountStatus.DEACTIVATED,
        deactivation_reason="refresh_failed",
    )
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(existing, merge_by_email=False)

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_reauth",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    async def fake_exchange_device_token(**_):
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
        }
        return OAuthTokens(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            id_token=_encode_jwt(payload),
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert start.status_code == 200

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

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = [account for account in accounts.json()["accounts"] if account["email"] == email]
    assert len(data) == 1
    healed = data[0]
    assert healed["accountId"] == account_id
    assert healed["status"] == "active"
    assert healed["deactivationReason"] is None
    assert healed["planType"] == "pro"
    assert healed["routingPolicy"] == "preserve"


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

    repo.upsert.assert_not_awaited()
    repo.upsert_account_slot.assert_awaited_once()
    assert repo.upsert_account_slot.await_args.kwargs == {
        "preserve_unknown_workspace_duplicates": False,
        "preserve_identity_slots": True,
    }
    assert account_cache.invalidated is True
    assert api_key_cache.cleared is True
    assert poller.bumped == ["api_key"]


@pytest.mark.asyncio
async def test_oauth_persist_tokens_uses_slot_upsert_for_label_only_workspace(monkeypatch):
    repo = AsyncMock()
    service = oauth_module.OauthService(repo)
    monkeypatch.setattr(
        oauth_module,
        "get_account_selection_cache",
        lambda: SimpleNamespace(invalidate=lambda: None),
        raising=False,
    )
    monkeypatch.setattr(oauth_module, "get_api_key_cache", lambda: SimpleNamespace(clear=lambda: None), raising=False)
    monkeypatch.setattr(oauth_module, "get_cache_invalidation_poller", lambda: None, raising=False)

    payload = {
        "email": "label-workspace@example.com",
        "chatgpt_account_id": "acc_label_workspace",
        "https://api.openai.com/auth": {
            "workspace_label": "Label Only Workspace",
            "chatgpt_plan_type": "plus",
        },
    }

    await service._persist_tokens(
        OAuthTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            id_token=_encode_jwt(payload),
        )
    )

    repo.upsert.assert_not_awaited()
    repo.upsert_account_slot.assert_awaited_once()
    saved_account = repo.upsert_account_slot.await_args.args[0]
    assert saved_account.workspace_label == "Label Only Workspace"
    assert repo.upsert_account_slot.await_args.kwargs == {
        "preserve_unknown_workspace_duplicates": False,
        "preserve_identity_slots": True,
    }


@pytest.mark.asyncio
async def test_targeted_reauth_replaces_only_matching_team_seat(monkeypatch):
    repo = AsyncMock()
    service = oauth_module.OauthService(repo)
    monkeypatch.setattr(oauth_module, "get_account_selection_cache", lambda: SimpleNamespace(invalidate=lambda: None))
    monkeypatch.setattr(oauth_module, "get_api_key_cache", lambda: SimpleNamespace(clear=lambda: None))
    monkeypatch.setattr(oauth_module, "get_cache_invalidation_poller", lambda: None)

    target_id = "shared-workspace_seat-a"
    existing_token = _encode_jwt(
        {
            "email": "seat-a@example.com",
            "sub": "auth0|seat-a",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "shared-workspace",
                "chatgpt_user_id": "user-seat-a",
            },
        }
    )
    intended = Account(
        id=target_id,
        chatgpt_account_id="shared-workspace",
        email="seat-a@example.com",
        plan_type="team",
        access_token_encrypted=service._encryptor.encrypt("old-access"),
        refresh_token_encrypted=service._encryptor.encrypt("old-refresh"),
        id_token_encrypted=service._encryptor.encrypt(existing_token),
        last_refresh=utcnow(),
        status=AccountStatus.REAUTH_REQUIRED,
    )
    repo.get_by_id.return_value = intended
    repo.replace_reauthorized.side_effect = lambda _account_id, account: account

    await service._persist_tokens(
        OAuthTokens(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token=_encode_jwt(
                {
                    "email": "seat-a@example.com",
                    "sub": "auth0|seat-a",
                    "https://api.openai.com/auth": {
                        "chatgpt_account_id": "shared-workspace",
                        "chatgpt_user_id": "user-seat-a",
                        "chatgpt_plan_type": "team",
                    },
                }
            ),
        ),
        intended_account_id=target_id,
    )

    repo.replace_reauthorized.assert_awaited_once()
    assert repo.replace_reauthorized.await_args.args[0] == target_id
    saved = repo.replace_reauthorized.await_args.args[1]
    assert saved.chatgpt_user_id == "user-seat-a"
    repo.upsert_account_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_targeted_reauth_rejects_other_seat_in_same_team_workspace(monkeypatch):
    repo = AsyncMock()
    service = oauth_module.OauthService(repo)
    target_id = "shared-workspace_seat-a"
    intended = Account(
        id=target_id,
        chatgpt_account_id="shared-workspace",
        chatgpt_user_id="user-seat-a",
        email="seat-a@example.com",
        plan_type="team",
        access_token_encrypted=service._encryptor.encrypt("old-access"),
        refresh_token_encrypted=service._encryptor.encrypt("old-refresh"),
        id_token_encrypted=service._encryptor.encrypt("unused"),
        last_refresh=utcnow(),
        status=AccountStatus.REAUTH_REQUIRED,
    )
    repo.get_by_id.return_value = intended

    with pytest.raises(oauth_module.ReauthSeatMismatchError):
        await service._persist_tokens(
            OAuthTokens(
                access_token="other-access",
                refresh_token="other-refresh",
                id_token=_encode_jwt(
                    {
                        "email": "seat-b@example.com",
                        "sub": "google-oauth2|seat-b",
                        "https://api.openai.com/auth": {
                            "chatgpt_account_id": "shared-workspace",
                            "chatgpt_user_id": "user-seat-b",
                        },
                    }
                ),
            ),
            intended_account_id=target_id,
        )

    repo.replace_reauthorized.assert_not_awaited()
    repo.upsert_account_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_targeted_reauth_rejects_missing_workspace_for_known_team_seat():
    repo = AsyncMock()
    service = oauth_module.OauthService(repo)
    target_id = "shared-workspace_seat-a"
    intended = Account(
        id=target_id,
        chatgpt_account_id="shared-workspace",
        chatgpt_user_id="user-seat-a",
        email="seat-a@example.com",
        plan_type="team",
        access_token_encrypted=service._encryptor.encrypt("old-access"),
        refresh_token_encrypted=service._encryptor.encrypt("old-refresh"),
        id_token_encrypted=service._encryptor.encrypt("unused"),
        last_refresh=utcnow(),
        status=AccountStatus.REAUTH_REQUIRED,
    )
    repo.get_by_id.return_value = intended

    with pytest.raises(oauth_module.ReauthSeatMismatchError):
        await service._persist_tokens(
            OAuthTokens(
                access_token="personal-access",
                refresh_token="personal-refresh",
                id_token=_encode_jwt(
                    {
                        "email": "seat-a@example.com",
                        "sub": "auth0|seat-a",
                        "https://api.openai.com/auth": {
                            "chatgpt_user_id": "user-seat-a",
                        },
                    }
                ),
            ),
            intended_account_id=target_id,
        )

    repo.replace_reauthorized.assert_not_awaited()
    repo.upsert_account_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_targeted_reauth_allows_legacy_sub_match_for_new_chatgpt_user_id(monkeypatch):
    repo = AsyncMock()
    service = oauth_module.OauthService(repo)
    monkeypatch.setattr(oauth_module, "get_account_selection_cache", lambda: SimpleNamespace(invalidate=lambda: None))
    monkeypatch.setattr(oauth_module, "get_api_key_cache", lambda: SimpleNamespace(clear=lambda: None))
    monkeypatch.setattr(oauth_module, "get_cache_invalidation_poller", lambda: None)

    target_id = "shared-workspace_legacy-seat-a"
    existing_token = _encode_jwt(
        {
            "email": "seat-a@example.com",
            "sub": "auth0|legacy-seat-a",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "shared-workspace",
            },
        }
    )
    intended = Account(
        id=target_id,
        chatgpt_account_id="shared-workspace",
        email="seat-a@example.com",
        plan_type="team",
        access_token_encrypted=service._encryptor.encrypt("old-access"),
        refresh_token_encrypted=service._encryptor.encrypt("old-refresh"),
        id_token_encrypted=service._encryptor.encrypt(existing_token),
        last_refresh=utcnow(),
        status=AccountStatus.REAUTH_REQUIRED,
    )
    repo.get_by_id.return_value = intended
    repo.replace_reauthorized.side_effect = lambda _account_id, account: account

    await service._persist_tokens(
        OAuthTokens(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token=_encode_jwt(
                {
                    "email": "seat-a@example.com",
                    "sub": "auth0|legacy-seat-a",
                    "https://api.openai.com/auth": {
                        "chatgpt_account_id": "shared-workspace",
                        "chatgpt_user_id": "user-seat-a",
                        "chatgpt_plan_type": "team",
                    },
                }
            ),
        ),
        intended_account_id=target_id,
    )

    repo.replace_reauthorized.assert_awaited_once()
    assert repo.replace_reauthorized.await_args.args[0] == target_id
    saved = repo.replace_reauthorized.await_args.args[1]
    assert saved.chatgpt_user_id == "user-seat-a"
    repo.upsert_account_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_targeted_reauth_rejects_workspace_mismatch_when_chatgpt_account_id_is_missing(monkeypatch):
    repo = AsyncMock()
    service = oauth_module.OauthService(repo)
    monkeypatch.setattr(oauth_module, "get_account_selection_cache", lambda: SimpleNamespace(invalidate=lambda: None))
    monkeypatch.setattr(oauth_module, "get_api_key_cache", lambda: SimpleNamespace(clear=lambda: None))
    monkeypatch.setattr(oauth_module, "get_cache_invalidation_poller", lambda: None)

    target_id = "legacy-workspace_seat-a"
    existing_token = _encode_jwt(
        {
            "email": "seat-a@example.com",
            "sub": "auth0|legacy-seat-a",
        }
    )
    intended = Account(
        id=target_id,
        chatgpt_account_id=None,
        chatgpt_user_id=None,
        workspace_id="legacy-workspace-a",
        email="seat-a@example.com",
        plan_type="team",
        access_token_encrypted=service._encryptor.encrypt("old-access"),
        refresh_token_encrypted=service._encryptor.encrypt("old-refresh"),
        id_token_encrypted=service._encryptor.encrypt(existing_token),
        last_refresh=utcnow(),
        status=AccountStatus.REAUTH_REQUIRED,
    )
    repo.get_by_id.return_value = intended

    with pytest.raises(oauth_module.ReauthSeatMismatchError):
        await service._persist_tokens(
            OAuthTokens(
                access_token="new-access",
                refresh_token="new-refresh",
                id_token=_encode_jwt(
                    {
                        "email": "seat-a@example.com",
                        "sub": "auth0|legacy-seat-a",
                        "https://api.openai.com/auth": {
                            "chatgpt_user_id": "user-seat-a",
                            "workspace_id": "legacy-workspace-b",
                            "chatgpt_plan_type": "team",
                        },
                    }
                ),
            ),
            intended_account_id=target_id,
        )

    repo.replace_reauthorized.assert_not_awaited()
    repo.upsert_account_slot.assert_not_awaited()


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
async def test_device_oauth_flow_reports_proxy_route_errors(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_oauth_route(*_args, **_kwargs):
        raise UpstreamProxyRouteError("default_pool_unconfigured", account_id=None)

    monkeypatch.setattr(oauth_module, "resolve_upstream_route", fake_oauth_route)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})

    assert start.status_code == 502
    assert start.json()["error"]["code"] == "default_pool_unconfigured"


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


def _make_replica_service(store: "oauth_module.OAuthStateStore") -> oauth_module.OauthService:
    """Build an OauthService bound to a distinct process-local store, standing in
    for one replica behind a load balancer. All replicas share the one test DB."""

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _repo_factory():
        async with SessionLocal() as session:
            yield AccountsRepository(session)

    return oauth_module.OauthService(
        cast(AccountsRepository, SimpleNamespace(list_accounts=AsyncMock(return_value=[]))),
        repo_factory=_repo_factory,
        store=store,
    )


@pytest.mark.asyncio
async def test_browser_oauth_flow_completes_on_replica_that_did_not_start_it(monkeypatch):
    """Multi-replica regression: replica A starts a browser flow; the manually
    pasted callback lands on replica B, whose in-memory store never saw the flow.

    Before this change the flow record lived only in replica A's process-local
    ``_OAUTH_STORE``, so replica B reported "state mismatch" and the account was
    never added. Now B loads the encrypted verifier + metadata from the shared
    DB and completes the exchange.
    """

    async def fake_callback_server_start(self) -> None:
        return None

    email = "cross-replica@example.com"
    raw_account_id = "acc_cross_replica"

    async def fake_exchange_authorization_code(**_):
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
        }
        return OAuthTokens(
            access_token="cross-access",
            refresh_token="cross-refresh",
            id_token=_encode_jwt(payload),
        )

    async def fake_oauth_route():
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)
    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)

    replica_a = _make_replica_service(oauth_module.OAuthStateStore())
    replica_b = _make_replica_service(oauth_module.OAuthStateStore())

    start = await replica_a.start_oauth(oauth_module.OauthStartRequest(force_method="browser"))
    assert start.method == "browser"
    assert start.flow_id is not None
    state_token = _oauth_state_token(start.authorization_url or "")

    # Replica B never held this flow in memory.
    async with replica_b._store.lock:
        assert replica_b._store.get_flow_by_state_token_locked(state_token) is None

    result = await replica_b.manual_callback(
        f"http://localhost:1455/auth/callback?code=cross-code&state={state_token}",
        flow_id=start.flow_id,
    )
    assert result.status == "success"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    async with SessionLocal() as session:
        stored = await AccountsRepository(session).get_by_id(expected_account_id)
    assert stored is not None

    # Replica A, still holding a stale in-memory pending flow, must report the
    # authoritative success written by replica B to the shared DB.
    status_a = await replica_a.oauth_status(start.flow_id)
    assert status_a.status == "success"


@pytest.mark.asyncio
async def test_oauth_status_reads_completion_written_by_another_replica(monkeypatch):
    """A flow started on replica A and marked success in the shared DB by another
    replica is reported as success by A's status poll, not its stale pending."""

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    replica_a = _make_replica_service(oauth_module.OAuthStateStore())
    start = await replica_a.start_oauth(oauth_module.OauthStartRequest(force_method="browser"))
    assert start.flow_id is not None

    # Replica A's in-memory view is still pending.
    async with replica_a._store.lock:
        local = replica_a._store.get_flow_locked(start.flow_id)
        assert local is not None and local.status == "pending"

    # Another replica writes the terminal status directly to the shared DB.
    async with SessionLocal() as session:
        repo = oauth_module.OAuthFlowRepository(session, TokenEncryptor())
        assert await repo.set_status(start.flow_id, status="success", error_message=None)

    status = await replica_a.oauth_status(start.flow_id)
    assert status.status == "success"


def test_is_expired_pending_normalizes_tz_aware_expiry():
    """Finding 1 (dialect-agnostic): on PostgreSQL asyncpg returns an
    offset-AWARE ``expires_at`` for the ``DateTime(timezone=True)`` column while
    ``utcnow()`` is naive UTC. The expiry comparison must normalize before
    comparing instead of raising ``TypeError: can't compare offset-naive and
    offset-aware datetimes``.
    """

    from app.modules.oauth.repository import OAuthFlowRepository

    now = utcnow()  # naive UTC, as returned by ``utcnow``
    live_aware = cast(
        OAuthFlowState,
        SimpleNamespace(status="pending", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
    )
    expired_aware = cast(
        OAuthFlowState,
        SimpleNamespace(status="pending", expires_at=datetime.now(timezone.utc) - timedelta(hours=1)),
    )

    # Must not raise, and must classify correctly.
    assert OAuthFlowRepository._is_expired_pending(live_aware, now) is False
    assert OAuthFlowRepository._is_expired_pending(expired_aware, now) is True


@pytest.mark.asyncio
async def test_get_by_flow_id_survives_tz_aware_expiry_from_asyncpg():
    """Finding 1 (read path): ``get_by_flow_id`` / ``get_by_state_token`` must
    not raise when the ORM row carries an offset-aware ``expires_at`` (asyncpg's
    representation for ``DateTime(timezone=True)`` on PostgreSQL) and must still
    correctly classify live vs expired pending flows.
    """

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = oauth_module.OAuthFlowRepository(session, encryptor)
        await repo.create(
            oauth_module.OAuthFlowRecord(
                flow_id="tz-aware-flow",
                method="browser",
                status="pending",
                state_token="tz-aware-state",
                code_verifier="tz-aware-verifier",
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    async with SessionLocal() as session:
        repo = oauth_module.OAuthFlowRepository(session, encryptor)
        row = await session.get(OAuthFlowState, "tz-aware-flow")
        assert row is not None

        # Simulate asyncpg: replace the naive value with an offset-aware one.
        row.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        live = await repo.get_by_flow_id("tz-aware-flow")
        assert live is not None
        live_by_state = await repo.get_by_state_token("tz-aware-state")
        assert live_by_state is not None

        # Aware + expired must be filtered out, still without raising.
        row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        assert await repo.get_by_flow_id("tz-aware-flow") is None
        assert await repo.get_by_state_token("tz-aware-state") is None


@pytest.mark.asyncio
async def test_complete_on_origin_replica_honors_durable_success_from_other_replica(monkeypatch):
    """Finding 2: replica A starts a browser flow; replica B completes it and
    writes durable success to the shared DB. The dashboard on A polls status
    (success) then immediately calls ``/complete`` with the same ``flowId``.

    Before the fix, ``complete_oauth`` used A's stale local ``pending`` browser
    flow (hydration skips existing flows) and returned ``pending``, so the UI
    flipped back to pending and never invalidated accounts. Now the durable
    terminal status is authoritative: ``/complete`` returns success and A's
    in-memory flow is reconciled.
    """

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    replica_a = _make_replica_service(oauth_module.OAuthStateStore())
    start = await replica_a.start_oauth(oauth_module.OauthStartRequest(force_method="browser"))
    assert start.flow_id is not None

    # Replica A's in-memory flow is still pending.
    async with replica_a._store.lock:
        local = replica_a._store.get_flow_locked(start.flow_id)
        assert local is not None and local.status == "pending"

    # Another replica completes the flow and writes durable success.
    async with SessionLocal() as session:
        repo = oauth_module.OAuthFlowRepository(session, TokenEncryptor())
        assert await repo.set_status(start.flow_id, status="success", error_message=None)

    # poll(): status returns the durable success ...
    status = await replica_a.oauth_status(start.flow_id)
    assert status.status == "success"

    # ... then the frontend immediately calls /complete with the same flowId.
    complete = await replica_a.complete_oauth(oauth_module.OauthCompleteRequest(flow_id=start.flow_id))
    assert complete.status == "success"

    # The origin replica's in-memory flow converges on the durable terminal.
    async with replica_a._store.lock:
        local = replica_a._store.get_flow_locked(start.flow_id)
        assert local is not None and local.status == "success"


@pytest.mark.asyncio
async def test_set_status_success_is_not_overwritten_by_later_error():
    """Monotonic terminal write (repository.py): when a device flow is completed
    twice (duplicate/losing poller), the poller that exchanged the code persists
    ``success`` while the other later receives an OAuth error for the consumed
    code. That stale error MUST NOT turn the durable row back to ``error``.
    """

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = oauth_module.OAuthFlowRepository(session, encryptor)
        await repo.create(
            oauth_module.OAuthFlowRecord(
                flow_id="mono-flow",
                method="device",
                status="pending",
                device_auth_id="dev-mono",
                user_code="MONO-CODE",
                interval_seconds=1,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    async with SessionLocal() as session:
        repo = oauth_module.OAuthFlowRepository(session, encryptor)
        # Winning poller persists success.
        assert await repo.set_status("mono-flow", status="success", error_message=None) is True
        # Losing/duplicate poller's later error for the consumed code is rejected.
        assert await repo.set_status("mono-flow", status="error", error_message="invalid_grant") is False
        record = await repo.get_by_flow_id("mono-flow")
        assert record is not None
        assert record.status == "success"
        assert record.error_message is None
        # success -> success remains idempotent.
        assert await repo.set_status("mono-flow", status="success", error_message=None) is True
        # error -> success is still allowed (success may win over an earlier error).
        await repo.create(
            oauth_module.OAuthFlowRecord(
                flow_id="err-then-ok",
                method="device",
                status="error",
                error_message="transient",
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        assert await repo.set_status("err-then-ok", status="success", error_message=None) is True
        healed = await repo.get_by_flow_id("err-then-ok")
        assert healed is not None and healed.status == "success"


@pytest.mark.asyncio
async def test_set_status_success_is_atomic_across_concurrent_sessions():
    """The monotonic guard MUST hold under real cross-session concurrency, not
    only single-session Python logic. Two pollers (separate DB sessions) both
    read the row while it is ``pending`` (the TOCTOU window); the winning poller
    commits ``success`` first, then the losing poller tries to write ``error``
    for the now-consumed device code. A client-side read-then-write guard would
    still see its stale ``pending`` snapshot and clobber the success; the SQL
    conditional UPDATE rejects it atomically.
    """

    encryptor = TokenEncryptor()
    async with SessionLocal() as seed_session:
        await oauth_module.OAuthFlowRepository(seed_session, encryptor).create(
            oauth_module.OAuthFlowRecord(
                flow_id="race-flow",
                method="device",
                status="pending",
                device_auth_id="dev-race",
                user_code="RACE-CODE",
                interval_seconds=1,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    async with SessionLocal() as session_win, SessionLocal() as session_lose:
        repo_win = oauth_module.OAuthFlowRepository(session_win, encryptor)
        repo_lose = oauth_module.OAuthFlowRepository(session_lose, encryptor)

        # Losing poller loads the still-``pending`` row into its own session and
        # keeps that transaction/snapshot open — the TOCTOU window where both
        # transactions have observed ``pending``. A client-side read-then-write
        # guard would re-use this cached ``pending`` snapshot on its own status
        # write and wrongly conclude the downgrade is allowed.
        preloaded = await session_lose.get(OAuthFlowState, "race-flow")
        assert preloaded is not None and preloaded.status == "pending"

        # Winning poller exchanges the code and commits ``success`` first.
        assert await repo_win.set_status("race-flow", status="success", error_message=None) is True

        # Losing poller now gets an OAuth error for the consumed code while still
        # holding its stale ``pending`` snapshot. The atomic conditional UPDATE
        # re-checks the current row state in SQL and refuses to downgrade the
        # committed ``success``; a client-side guard would clobber it.
        applied = await repo_lose.set_status("race-flow", status="error", error_message="invalid_grant")
        assert applied is False

    async with SessionLocal() as verify_session:
        record = await oauth_module.OAuthFlowRepository(verify_session, encryptor).get_by_flow_id("race-flow")
        assert record is not None
        assert record.status == "success"
        assert record.error_message is None


@pytest.mark.asyncio
async def test_device_complete_ack_stays_pending_when_own_poller_already_succeeded():
    """Device same-replica ``/complete`` contract: the fire-and-forget
    acknowledgement (no ``flow_id``) must report ``pending`` even when this
    flow's own in-process poller has already raced to ``success`` (the DB/store
    already shows success). This deterministically reproduces the CI race where
    the instant device-token exchange completed before ``/complete`` was read,
    and must NOT spawn a second poll of the consumed device code.
    """

    await oauth_module._OAUTH_STORE.reset()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))

        async with oauth_module._OAUTH_STORE.lock:
            flow = oauth_module.OAuthState(
                flow_id="device-raced",
                status="pending",
                method="device",
                device_auth_id="dev-raced",
                user_code="RACED-CODE",
                interval_seconds=1,
                expires_at=time.time() + 30,
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(flow)
            # The flow's own poller has already written success.
            oauth_module._OAUTH_STORE.set_flow_status_locked(flow, status="success", error_message=None)

        response = await service.complete_oauth(oauth_module.OauthCompleteRequest())
        assert response.status == "pending"

        async with oauth_module._OAUTH_STORE.lock:
            done = oauth_module._OAUTH_STORE.get_flow_locked("device-raced")
            assert done is not None
            # No second poller was started for the already-consumed device code.
            assert done.poll_task is None


@pytest.mark.asyncio
async def test_superseded_device_poller_does_not_persist_account(monkeypatch):
    """Liveness race: a device flow's in-process poller is superseded by a newer
    device start (which atomically re-claims the single-active slot) in the
    window between the exchange and the account write. The abandoned poller must
    lose the atomic slot consume and abort before doing durable damage: no
    account is added and no terminal status is written for the abandoned attempt.
    """

    email = "superseded-device@example.com"
    raw_account_id = "acc_superseded_device"

    exchange_started = asyncio.Event()
    release_exchange = asyncio.Event()

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="SUPER-CODE",
            device_auth_id="dev-super",
            interval_seconds=0,
            expires_in_seconds=300,
        )

    async def fake_exchange_device_token(**_):
        exchange_started.set()
        await release_exchange.wait()
        return OAuthTokens(
            access_token="super-access",
            refresh_token="super-refresh",
            id_token=_encode_jwt(
                {
                    "email": email,
                    "chatgpt_account_id": raw_account_id,
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ),
        )

    async def fake_oauth_route():
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)

    replica = _make_replica_service(oauth_module.OAuthStateStore())
    start = await replica.start_oauth(oauth_module.OauthStartRequest(force_method="device"))
    assert start.flow_id is not None

    # The poller is now blocked mid-exchange, holding the (about-to-be-consumed)
    # device code, and it holds the single-active device slot.
    await asyncio.wait_for(exchange_started.wait(), timeout=2)
    async with SessionLocal() as session:
        assert await OAuthFlowRepository(session, TokenEncryptor()).current_device_slot_flow_id() == start.flow_id

    # A replacement device start (another replica) atomically re-claims the slot
    # in the window between the exchange and the abandoned poller's account write.
    async with SessionLocal() as session:
        await OAuthFlowRepository(session, TokenEncryptor()).claim_device_slot("replacement-flow")

    async with replica._store.lock:
        flow = replica._store.get_flow_locked(start.flow_id)
        assert flow is not None
        poll_task = flow.poll_task
        assert poll_task is not None

    # Let the abandoned exchange complete; the poller must lose the atomic slot
    # consume and abort rather than persist the account.
    release_exchange.set()
    await asyncio.wait_for(poll_task, timeout=2)

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    async with SessionLocal() as session:
        stored = await AccountsRepository(session).get_by_id(expected_account_id)
    assert stored is None

    # No terminal status was written for the superseded flow; the replacement
    # still holds the slot.
    async with SessionLocal() as session:
        repo = OAuthFlowRepository(session, TokenEncryptor())
        record = await repo.get_by_flow_id(start.flow_id)
        assert record is not None and record.status == "pending"
        assert await repo.current_device_slot_flow_id() == "replacement-flow"


@pytest.mark.asyncio
async def test_concurrent_device_starts_leave_exactly_one_current_flow(monkeypatch):
    """Two replicas starting device OAuth "simultaneously" must leave exactly ONE
    current device flow (the atomic slot UPSERT), and only the poller that still
    holds the slot may persist -- the other's consume matches zero rows.
    """

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="RACE-CODE",
            device_auth_id="dev-race",
            interval_seconds=30,
            expires_in_seconds=300,
        )

    # Never returns tokens: keep both pollers pending so we can assert the slot
    # invariant deterministically without either completing.
    async def fake_exchange_device_token(**_):
        await asyncio.Event().wait()

    async def fake_oauth_route():
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)

    replica_a = _make_replica_service(oauth_module.OAuthStateStore())
    replica_b = _make_replica_service(oauth_module.OAuthStateStore())

    start_a, start_b = await asyncio.gather(
        replica_a.start_oauth(oauth_module.OauthStartRequest(force_method="device")),
        replica_b.start_oauth(oauth_module.OauthStartRequest(force_method="device")),
    )
    assert start_a.flow_id is not None and start_b.flow_id is not None
    assert start_a.flow_id != start_b.flow_id

    # Exactly one flow holds the single-active slot.
    async with SessionLocal() as session:
        current = await OAuthFlowRepository(session, TokenEncryptor()).current_device_slot_flow_id()
    assert current in {start_a.flow_id, start_b.flow_id}

    # Only the current flow can consume the slot; the other loses.
    async with SessionLocal() as session:
        repo = OAuthFlowRepository(session, TokenEncryptor())
        other = start_b.flow_id if current == start_a.flow_id else start_a.flow_id
        assert await repo.consume_device_slot(other) is False
    async with SessionLocal() as session:
        repo = OAuthFlowRepository(session, TokenEncryptor())
        assert await repo.consume_device_slot(current) is True
        # Once consumed, neither can consume again (no double-persist).
        assert await repo.consume_device_slot(current) is False

    # Clean up the parked poll tasks.
    for replica, start in ((replica_a, start_a), (replica_b, start_b)):
        async with replica._store.lock:
            flow = replica._store.get_flow_locked(start.flow_id)
            task = flow.poll_task if flow is not None else None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_expired_local_browser_flow_callback_is_rejected_on_origin_replica(monkeypatch):
    """The pending-flow TTL must hold uniformly, including on the replica that
    started the flow and still holds its local state: a callback that arrives
    after the TTL is rejected (state-mismatch / expired) instead of being
    completed from the stale cached verifier.
    """

    async def fake_callback_server_start(self) -> None:
        return None

    exchange_calls: list[str | None] = []

    async def fake_exchange_authorization_code(**kwargs):
        exchange_calls.append(kwargs.get("code"))
        raise AssertionError("an expired flow must never exchange the authorization code")

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    replica = _make_replica_service(oauth_module.OAuthStateStore())
    start = await replica.start_oauth(oauth_module.OauthStartRequest(force_method="browser"))
    assert start.flow_id is not None
    state_token = _oauth_state_token(start.authorization_url or "")

    # Force the flow past its TTL both in the local store and the shared DB.
    async with replica._store.lock:
        local = replica._store.get_flow_by_state_token_locked(state_token)
        assert local is not None and local.status == "pending"
        local.expires_at = time.time() - 1
    async with SessionLocal() as session:
        row = await session.get(OAuthFlowState, start.flow_id)
        assert row is not None
        row.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()

    result = await replica.manual_callback(
        f"http://localhost:1455/auth/callback?code=expired-code&state={state_token}",
        flow_id=start.flow_id,
    )

    assert result.status == "error"
    assert result.error_message == "Invalid OAuth callback: state mismatch or missing code."
    # The stale verifier was never used to exchange the code.
    assert exchange_calls == []


@pytest.mark.parametrize("entry_point", ["status", "complete", "manual_callback", "handle_callback", "device_complete"])
@pytest.mark.asyncio
async def test_entry_points_honor_durable_terminal_over_local_pending(monkeypatch, entry_point):
    """Root-consolidation regression: EVERY entry point that resolves a flow from
    local state must consult the DB-authoritative status first, so a durable
    terminal written by another replica wins over this replica's stale local
    ``pending`` -- and a consumed authorization / device code is never replayed.
    """

    from aiohttp.test_utils import make_mocked_request

    async def fake_callback_server_start(self) -> None:
        return None

    async def fake_oauth_route():
        return None

    exchange_calls: list[str | None] = []

    async def fake_exchange_authorization_code(**kwargs):
        exchange_calls.append(kwargs.get("code"))
        raise AssertionError("a durable-terminal flow must never re-exchange the code")

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="TERM-CODE",
            device_auth_id="dev-term",
            interval_seconds=30,
            expires_in_seconds=300,
        )

    async def fake_exchange_device_token(**_):
        # The device poll task legitimately calls this once at start; it is not a
        # replay. Only authorization-code exchanges are tracked in exchange_calls.
        await asyncio.Event().wait()

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)
    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)

    replica = _make_replica_service(oauth_module.OAuthStateStore())
    force_method = "device" if entry_point == "device_complete" else "browser"
    start = await replica.start_oauth(oauth_module.OauthStartRequest(force_method=force_method))
    assert start.flow_id is not None
    state_token = _oauth_state_token(start.authorization_url or "") if force_method == "browser" else None

    # Origin replica still holds the flow as pending in memory.
    async with replica._store.lock:
        local = replica._store.get_flow_locked(start.flow_id)
        assert local is not None and local.status == "pending"

    # Another replica writes the durable terminal success.
    async with SessionLocal() as session:
        assert await OAuthFlowRepository(session, TokenEncryptor()).set_status(
            start.flow_id, status="success", error_message=None
        )

    if entry_point == "status":
        resp = await replica.oauth_status(start.flow_id)
        assert resp.status == "success"
    elif entry_point in ("complete", "device_complete"):
        resp = await replica.complete_oauth(oauth_module.OauthCompleteRequest(flow_id=start.flow_id))
        assert resp.status == "success"
    elif entry_point == "manual_callback":
        resp = await replica.manual_callback(
            f"http://localhost:1455/auth/callback?code=replay-code&state={state_token}",
            flow_id=start.flow_id,
        )
        assert resp.status == "success"
    elif entry_point == "handle_callback":
        request = make_mocked_request("GET", f"/auth/callback?code=replay-code&state={state_token}")
        response = await replica._handle_callback(request)
        assert response.status == 200
        assert response.text is not None and "Login failed" not in response.text

    # The origin replica's in-memory flow is reconciled to the durable terminal.
    async with replica._store.lock:
        local = replica._store.get_flow_locked(start.flow_id)
        assert local is not None and local.status == "success"

    # The consumed authorization / device code was never re-exchanged.
    assert exchange_calls == []

    # Clean up any parked device poll task.
    async with replica._store.lock:
        flow = replica._store.get_flow_locked(start.flow_id)
        task = flow.poll_task if flow is not None else None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_browser_callback_replay_on_origin_does_not_reexchange_consumed_code(monkeypatch):
    """The reported callback-replay class: replica A starts a browser flow (local
    pending); another replica completes it (durable success). A second browser
    redirect / pasted callback for the same state lands back on A. A must observe
    the durable success instead of reusing the already-consumed authorization
    code (which upstream would reject, surfacing a spurious error to the user).
    """

    from aiohttp.test_utils import make_mocked_request

    async def fake_callback_server_start(self) -> None:
        return None

    async def fake_oauth_route():
        return None

    exchange_calls: list[str | None] = []

    async def fake_exchange_authorization_code(**kwargs):
        exchange_calls.append(kwargs.get("code"))
        raise AssertionError("replayed callback must not re-exchange the consumed code")

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    replica_a = _make_replica_service(oauth_module.OAuthStateStore())
    start = await replica_a.start_oauth(oauth_module.OauthStartRequest(force_method="browser"))
    assert start.flow_id is not None
    state_token = _oauth_state_token(start.authorization_url or "")

    # Another replica completed the flow: durable success in the shared DB.
    async with SessionLocal() as session:
        assert await OAuthFlowRepository(session, TokenEncryptor()).set_status(
            start.flow_id, status="success", error_message=None
        )

    # Replayed pasted callback on the origin replica.
    manual = await replica_a.manual_callback(
        f"http://localhost:1455/auth/callback?code=consumed-code&state={state_token}",
        flow_id=start.flow_id,
    )
    assert manual.status == "success"

    # Replayed browser redirect on the origin replica.
    request = make_mocked_request("GET", f"/auth/callback?code=consumed-code&state={state_token}")
    response = await replica_a._handle_callback(request)
    assert response.status == 200
    assert response.text is not None and "Login failed" not in response.text

    assert exchange_calls == []


@pytest.mark.asyncio
async def test_overlapping_same_replica_device_starts_later_start_wins_slot_and_poller(monkeypatch):
    """Finding 1: two device starts overlap on the SAME replica; the earlier
    start is superseded in the local store while awaiting its durable persist. It
    must NOT install a stale slot pointer or a poll task; the later start ends up
    as the current slot holder and the sole poller.
    """

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="OVERLAP-CODE",
            device_auth_id="dev-overlap",
            interval_seconds=30,
            expires_in_seconds=300,
        )

    async def fake_exchange_device_token(**_):
        await asyncio.Event().wait()  # never completes; keep pollers pending

    async def fake_oauth_route():
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)

    replica = _make_replica_service(oauth_module.OAuthStateStore())

    # Gate the FIRST start's durable persist so a second start can supersede it
    # while it is parked mid-persist (after it registered locally, before it
    # claims the slot / starts its poller).
    original_persist = replica._persist_flow_record
    persist_first_reached = asyncio.Event()
    release_first_persist = asyncio.Event()
    calls = {"n": 0}

    async def gated_persist(record):
        calls["n"] += 1
        if calls["n"] == 1:
            persist_first_reached.set()
            await release_first_persist.wait()
        await original_persist(record)

    monkeypatch.setattr(replica, "_persist_flow_record", gated_persist)

    first_task = asyncio.create_task(replica.start_oauth(oauth_module.OauthStartRequest(force_method="device")))
    await asyncio.wait_for(persist_first_reached.wait(), timeout=2)

    # Second start runs to completion: it supersedes the first locally, claims the
    # slot, and starts its poller.
    second = await replica.start_oauth(oauth_module.OauthStartRequest(force_method="device"))

    # Release the first start; it resumes past its persist and must detect it was
    # superseded (no stale claim, no poll task).
    release_first_persist.set()
    first = await asyncio.wait_for(first_task, timeout=2)

    assert first.flow_id is not None and second.flow_id is not None and first.flow_id != second.flow_id

    async with SessionLocal() as session:
        current = await OAuthFlowRepository(session, TokenEncryptor()).current_device_slot_flow_id()
    assert current == second.flow_id  # the later start holds the slot

    async with replica._store.lock:
        second_flow = replica._store.get_flow_locked(second.flow_id)
        first_flow = replica._store.get_flow_locked(first.flow_id)
        assert second_flow is not None and second_flow.poll_task is not None  # sole poller
        # The superseded first start neither remains current nor holds a poller.
        assert first_flow is None or first_flow.poll_task is None
        second_task = second_flow.poll_task

    if second_task is not None:
        second_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await second_task


@pytest.mark.asyncio
async def test_loser_device_poller_writes_no_terminal_during_winner_persist(monkeypatch):
    """Finding 2: only the slot holder may write a terminal status. A loser poller
    that received ``invalid_grant`` for the consumed code (while the winner is
    mid-persist, slot already consumed, success not yet written) MUST write NO
    terminal (no pending->error), so the dashboard keeps polling and the winner's
    later success is the durable outcome.
    """

    async def fake_oauth_route():
        return None

    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await OAuthFlowRepository(session, encryptor).create(
            oauth_module.OAuthFlowRecord(
                flow_id="race-terminal",
                method="device",
                status="pending",
                device_auth_id="dev-rt",
                user_code="RT-CODE",
                interval_seconds=0,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    service = _make_replica_service(oauth_module.OAuthStateStore())
    await service._claim_device_slot("race-terminal")

    # Winner consumes the slot (it is now mid-persist, success not yet written).
    assert await service._consume_device_slot("race-terminal") is True

    # Loser poll task: its exchange raises invalid_grant for the consumed code.
    async def fake_exchange_device_token(**_):
        raise OAuthError("invalid_grant", "Authorization code expired", status_code=400)

    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    loser_context = oauth_module.DevicePollContext(
        device_auth_id="dev-rt",
        user_code="RT-CODE",
        interval_seconds=0,
        expires_at=time.time() + 300,
    )
    await service._poll_device_tokens("race-terminal", loser_context)

    # The loser wrote NO terminal status; the flow is still pending.
    async with SessionLocal() as session:
        record = await OAuthFlowRepository(session, encryptor).get_by_flow_id("race-terminal")
    assert record is not None and record.status == "pending"

    # The winner's success (written after its persist) is the durable outcome.
    await service._set_success("race-terminal")
    async with SessionLocal() as session:
        record = await OAuthFlowRepository(session, encryptor).get_by_flow_id("race-terminal")
    assert record is not None and record.status == "success"


@pytest.mark.asyncio
async def test_non_originating_complete_reports_durable_status_without_second_poller(monkeypatch):
    """Reduced duplicate-poller surface: a device ``/complete`` served on a
    replica that did NOT originate the flow reports the durable status and does
    NOT spawn a second poll task for the single-use device code.
    """

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ORIG-CODE",
            device_auth_id="dev-orig",
            interval_seconds=30,
            expires_in_seconds=300,
        )

    async def fake_exchange_device_token(**_):
        await asyncio.Event().wait()

    async def fake_oauth_route():
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)

    origin = _make_replica_service(oauth_module.OAuthStateStore())
    other = _make_replica_service(oauth_module.OAuthStateStore())

    start = await origin.start_oauth(oauth_module.OauthStartRequest(force_method="device"))
    assert start.flow_id is not None

    # The non-originating replica reports the durable pending status ...
    resp = await other.complete_oauth(oauth_module.OauthCompleteRequest(flow_id=start.flow_id))
    assert resp.status == "pending"

    # ... and did NOT start a second poll task for the flow.
    async with other._store.lock:
        other_flow = other._store.get_flow_locked(start.flow_id)
    assert other_flow is None or other_flow.poll_task is None

    # Cross-replica durable terminal is still reported by /complete on the other
    # replica once written.
    async with SessionLocal() as session:
        assert await OAuthFlowRepository(session, TokenEncryptor()).set_status(
            start.flow_id, status="success", error_message=None
        )
    resp2 = await other.complete_oauth(oauth_module.OauthCompleteRequest(flow_id=start.flow_id))
    assert resp2.status == "success"

    # Clean up the origin's sole poll task.
    async with origin._store.lock:
        origin_flow = origin._store.get_flow_locked(start.flow_id)
        task = origin_flow.poll_task if origin_flow is not None else None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("path", ["manual_callback", "handle_callback"])
@pytest.mark.asyncio
async def test_loser_browser_callback_honors_durable_success_not_error(monkeypatch, path):
    """Browser-callback analog of the loser-writes-error bug: two callbacks race
    on the same single-use authorization code. The winner commits durable success
    WHILE the loser is exchanging; the loser's exchange then fails with
    ``invalid_grant``. The loser's terminal ERROR write is rejected by the
    monotonic guard (durable row already ``success``), so the loser MUST report
    the durable SUCCESS (not error) and MUST NOT leave the local flow in error.
    """

    from aiohttp.test_utils import make_mocked_request

    holder: dict[str, str] = {}

    async def fake_callback_server_start(self) -> None:
        return None

    async def fake_oauth_route():
        return None

    async def fake_exchange_authorization_code(**_kwargs):
        # The winner commits durable success DURING the loser's exchange (so the
        # top-of-callback reconciliation gate saw ``pending`` and the loser
        # actually reaches this exchange), then the loser's exchange of the
        # now-consumed code fails.
        async with SessionLocal() as session:
            await OAuthFlowRepository(session, TokenEncryptor()).set_status(
                holder["flow_id"], status="success", error_message=None
            )
        raise OAuthError("invalid_grant", "Authorization code expired", status_code=400)

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    replica = _make_replica_service(oauth_module.OAuthStateStore())
    start = await replica.start_oauth(oauth_module.OauthStartRequest(force_method="browser"))
    assert start.flow_id is not None
    holder["flow_id"] = start.flow_id
    state_token = _oauth_state_token(start.authorization_url or "")

    if path == "manual_callback":
        manual = await replica.manual_callback(
            f"http://localhost:1455/auth/callback?code=consumed-code&state={state_token}",
            flow_id=start.flow_id,
        )
        assert manual.status == "success"
    else:
        request = make_mocked_request("GET", f"/auth/callback?code=consumed-code&state={state_token}")
        response = await replica._handle_callback(request)
        assert response.status == 200
        assert response.text is not None and "Login failed" not in response.text

    # The loser must not leave the local flow in error; it honors durable success.
    async with replica._store.lock:
        local = replica._store.get_flow_locked(start.flow_id)
        assert local is not None and local.status == "success"
    async with SessionLocal() as session:
        record = await OAuthFlowRepository(session, TokenEncryptor()).get_by_flow_id(start.flow_id)
    assert record is not None and record.status == "success"
