from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts.service import (
    DEFAULT_PROBE_MODEL,
    AccountNotProbableError,
    AccountsService,
)

pytestmark = pytest.mark.unit


_ACCOUNT_ID = "acc_test"
_CHATGPT_ACCOUNT_ID = "chatgpt-acc-1"
_PROBE_TOKEN_PLAINTEXT = "test-access-token-not-a-real-secret"


def _make_account(status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=_ACCOUNT_ID,
        chatgpt_account_id=_CHATGPT_ACCOUNT_ID,
        email="probe@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt(_PROBE_TOKEN_PLAINTEXT),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 5, 17),
        status=status,
        deactivation_reason=None,
    )


def _make_usage_row(used_percent: float, account_id: str = _ACCOUNT_ID) -> Any:
    return SimpleNamespace(used_percent=used_percent, account_id=account_id)


def _build_service(
    account: Account | None,
    *,
    primary_pct: float | None = None,
    secondary_pct: float | None = None,
    auth_manager: Any | None = None,
) -> AccountsService:
    repo = AsyncMock()
    repo.get_by_id.return_value = account

    usage_repo = AsyncMock()
    primary_entry = _make_usage_row(primary_pct) if primary_pct is not None else None
    secondary_entry = _make_usage_row(secondary_pct) if secondary_pct is not None else None

    async def _latest_entry_for_account(requested_account_id: str, *, window: str) -> Any:
        if requested_account_id != _ACCOUNT_ID:
            return None
        return primary_entry if window == "primary" else secondary_entry

    usage_repo.latest_entry_for_account.side_effect = _latest_entry_for_account

    service = AccountsService(repo=repo, usage_repo=usage_repo, auth_manager=auth_manager)
    # Stop the real UsageUpdater from running — the unit test asserts the
    # service-level orchestration, not the refresh internals.
    usage_updater = AsyncMock()
    usage_updater.force_refresh = AsyncMock(return_value=True)
    service._usage_updater = usage_updater
    return service


@pytest.mark.asyncio
async def test_probe_account_returns_none_for_missing_account():
    service = _build_service(account=None)
    result = await service.probe_account("missing")
    assert result is None


@pytest.mark.asyncio
async def test_probe_account_rejects_paused_account():
    account = _make_account(status=AccountStatus.PAUSED)
    service = _build_service(account=account)
    with pytest.raises(AccountNotProbableError):
        await service.probe_account(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_probe_account_rejects_deactivated_account():
    account = _make_account(status=AccountStatus.DEACTIVATED)
    service = _build_service(account=account)
    with pytest.raises(AccountNotProbableError):
        await service.probe_account(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_probe_account_captures_before_after_snapshot(monkeypatch):
    account = _make_account(status=AccountStatus.RATE_LIMITED)
    service = _build_service(account=account, primary_pct=100.0, secondary_pct=80.0)

    captured_kwargs: dict[str, Any] = {}

    async def _fake_probe(**kwargs):
        captured_kwargs.update(kwargs)
        return 200

    monkeypatch.setattr(service, "_send_probe_request", _fake_probe)

    result = await service.probe_account(_ACCOUNT_ID, model="gpt-5.5-test")

    assert result is not None
    assert result.status == "probed"
    assert result.account_id == _ACCOUNT_ID
    assert result.probe_status_code == 200
    assert result.primary_used_percent_before == 100.0
    assert result.primary_used_percent_after == 100.0
    assert result.secondary_used_percent_before == 80.0
    assert result.secondary_used_percent_after == 80.0
    assert result.account_status_before == "rate_limited"
    assert result.account_status_after == "rate_limited"

    assert captured_kwargs["access_token"] == _PROBE_TOKEN_PLAINTEXT
    assert captured_kwargs["chatgpt_account_id"] == _CHATGPT_ACCOUNT_ID
    assert captured_kwargs["model"] == "gpt-5.5-test"

    assert service._usage_updater is not None
    force_refresh_mock = service._usage_updater.force_refresh
    assert isinstance(force_refresh_mock, AsyncMock)
    force_refresh_mock.assert_awaited_once_with(account)


@pytest.mark.asyncio
async def test_probe_account_refreshes_token_before_sending_probe(monkeypatch):
    stale_account = _make_account(status=AccountStatus.ACTIVE)
    fresh_account = _make_account(status=AccountStatus.ACTIVE)
    encryptor = TokenEncryptor()
    fresh_account.access_token_encrypted = encryptor.encrypt("fresh-access-token")
    auth_manager = SimpleNamespace(ensure_fresh=AsyncMock(return_value=fresh_account))
    service = _build_service(
        account=stale_account,
        primary_pct=95.0,
        secondary_pct=80.0,
        auth_manager=auth_manager,
    )

    captured_kwargs: dict[str, Any] = {}

    async def _fake_probe(**kwargs):
        captured_kwargs.update(kwargs)
        return 200

    monkeypatch.setattr(service, "_send_probe_request", _fake_probe)

    await service.probe_account(_ACCOUNT_ID)

    auth_manager.ensure_fresh.assert_awaited_once_with(stale_account, force=False)
    assert captured_kwargs["access_token"] == "fresh-access-token"


@pytest.mark.asyncio
async def test_probe_account_uses_default_model_when_omitted(monkeypatch):
    account = _make_account()
    service = _build_service(account=account, primary_pct=10.0, secondary_pct=20.0)

    captured_kwargs: dict[str, Any] = {}

    async def _fake_probe(**kwargs):
        captured_kwargs.update(kwargs)
        return 200

    monkeypatch.setattr(service, "_send_probe_request", _fake_probe)

    await service.probe_account(_ACCOUNT_ID)

    assert captured_kwargs["model"] == DEFAULT_PROBE_MODEL


@pytest.mark.asyncio
async def test_probe_account_never_logs_access_token(monkeypatch, caplog):
    account = _make_account()
    service = _build_service(account=account, primary_pct=5.0, secondary_pct=5.0)

    async def _fake_probe(**kwargs):
        # Simulate an upstream-side success without revealing the token.
        return 200

    monkeypatch.setattr(service, "_send_probe_request", _fake_probe)

    caplog.set_level("DEBUG")
    await service.probe_account(_ACCOUNT_ID)
    joined_log_output = "\n".join(record.getMessage() for record in caplog.records)
    assert _PROBE_TOKEN_PLAINTEXT not in joined_log_output


@pytest.mark.asyncio
async def test_probe_account_surfaces_network_failure_status(monkeypatch):
    account = _make_account()
    service = _build_service(account=account, primary_pct=0.0, secondary_pct=0.0)

    async def _fake_probe(**kwargs):
        return 0  # PROBE_NETWORK_FAILURE_STATUS sentinel

    monkeypatch.setattr(service, "_send_probe_request", _fake_probe)

    result = await service.probe_account(_ACCOUNT_ID)
    assert result is not None
    assert result.probe_status_code == 0


@pytest.mark.asyncio
async def test_send_probe_request_uses_shared_http_client(monkeypatch):
    account = _make_account()
    service = _build_service(account=account)
    captured: dict[str, Any] = {}

    class _Response:
        status = 204

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Session:
        def post(self, url: str, **kwargs: Any):
            captured["url"] = url
            captured.update(kwargs)
            return _Response()

    class _Lease:
        async def __aenter__(self):
            captured["leased"] = True
            return _Session()

        async def __aexit__(self, exc_type, exc, tb):
            captured["released"] = True
            return False

    monkeypatch.setattr("app.modules.accounts.service.lease_http_session", lambda: _Lease())

    status = await service._send_probe_request(
        access_token=_PROBE_TOKEN_PLAINTEXT,
        chatgpt_account_id=_CHATGPT_ACCOUNT_ID,
        model="gpt-5.5-test",
    )

    assert status == 204
    assert captured["leased"] is True
    assert captured["released"] is True
    assert captured["url"].endswith("/backend-api/codex/responses")
    assert captured["headers"]["Authorization"] == f"Bearer {_PROBE_TOKEN_PLAINTEXT}"
    assert captured["headers"]["chatgpt-account-id"] == _CHATGPT_ACCOUNT_ID
    assert captured["json"]["model"] == "gpt-5.5-test"
    assert captured["timeout"].total == 30.0
    assert captured["timeout"].connect is None
    assert captured["timeout"].sock_connect == 10.0
