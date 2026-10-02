from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.balancer import AccountState, select_account
from app.core.clients.proxy import ProxyResponseError
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.proxy._service.http_bridge.helpers import _http_bridge_session_account_active
from app.modules.proxy._service.websocket.mixin import _authorize_websocket_dispatch_owner
from app.modules.proxy.account_eligibility import stored_access_token_expires_at
from app.modules.proxy.load_balancer import _build_states
from app.modules.usage.authorization import OwnerAuthorization, OwnerAuthorizationKind

pytestmark = pytest.mark.unit


def _jwt(*, expires_at: int) -> str:
    def encode(payload: dict[str, object]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'none'})}.{encode({'exp': expires_at})}."


def test_stored_access_token_expiry_is_derived_from_encrypted_jwt() -> None:
    encryptor = TokenEncryptor()

    expires_at = stored_access_token_expires_at(
        encryptor.encrypt(_jwt(expires_at=1_700_000_123)),
        encryptor,
    )

    assert expires_at == 1_700_000_123.0


def test_build_states_carries_reauth_access_token_expiry() -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="reauth",
        chatgpt_account_id="chatgpt-reauth",
        email="reauth@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt(_jwt(expires_at=1_700_000_123)),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 1, 1),
        status=AccountStatus.REAUTH_REQUIRED,
    )

    states, _ = _build_states(
        accounts=[account],
        latest_primary={},
        latest_secondary={},
        latest_monthly={},
        runtime={},
        encryptor=encryptor,
    )

    assert states[0].access_token_expires_at == 1_700_000_123.0


def test_selection_skips_expired_reauth_account() -> None:
    now = 1_700_000_000.0
    states = [
        AccountState(
            "expired",
            AccountStatus.REAUTH_REQUIRED,
            used_percent=1.0,
            access_token_expires_at=now,
        ),
        AccountState("active", AccountStatus.ACTIVE, used_percent=50.0),
    ]

    result = select_account(states, now=now, routing_strategy="usage_weighted")

    assert result.account is not None
    assert result.account.account_id == "active"


def test_all_expired_reauth_accounts_report_reauthentication() -> None:
    now = 1_700_000_000.0
    states = [
        AccountState(
            "expired-a",
            AccountStatus.REAUTH_REQUIRED,
            access_token_expires_at=now - 1,
        ),
        AccountState(
            "expired-b",
            AccountStatus.REAUTH_REQUIRED,
            access_token_expires_at=now,
        ),
    ]

    result = select_account(states, now=now)

    assert result.account is None
    assert result.error_message == "All accounts require re-authentication"


def test_http_bridge_rejects_expired_reauth_session() -> None:
    session = SimpleNamespace(
        account=SimpleNamespace(
            id="expired-bridge-owner",
            status=AccountStatus.REAUTH_REQUIRED,
        ),
        access_token_expires_at=0.0,
    )

    assert not _http_bridge_session_account_active(cast(Any, session))


@pytest.mark.asyncio
@pytest.mark.parametrize("token_expired", [False, True])
async def test_websocket_reauth_owner_requires_usable_token_at_dispatch(token_expired: bool) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="reauth-websocket-owner",
        email="reauth-websocket-owner@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt(_jwt(expires_at=int(time.time()) + (-60 if token_expired else 60))),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 1, 1),
        status=AccountStatus.REAUTH_REQUIRED,
    )
    authorize = AsyncMock(
        return_value=OwnerAuthorization(
            OwnerAuthorizationKind.ALLOWED,
            owner_status=AccountStatus.REAUTH_REQUIRED,
        )
    )
    proxy = SimpleNamespace(_load_balancer=SimpleNamespace(authorize_account_fresh=authorize), _encryptor=encryptor)

    if token_expired:
        with pytest.raises(ProxyResponseError) as exc_info:
            await _authorize_websocket_dispatch_owner(cast(Any, proxy), account)
        assert exc_info.value.payload["error"]["code"] == "previous_response_owner_unavailable"
    else:
        await _authorize_websocket_dispatch_owner(cast(Any, proxy), account)
    authorize.assert_awaited_once_with(account.id)
