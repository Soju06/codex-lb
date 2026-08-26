from __future__ import annotations

import time
from collections.abc import Collection

from app.core.auth import token_expiry_epoch_ms
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus

ROUTABLE_STATUSES = (AccountStatus.ACTIVE, AccountStatus.REAUTH_REQUIRED)
"""Statuses whose sessions keep serving requests (reauth stays routable until expiry)."""


def stored_access_token_expires_at(
    encrypted_access_token: bytes | None,
    encryptor: TokenEncryptor,
) -> float | None:
    """Return the stored access token's JWT expiry in epoch seconds when known."""
    if not encrypted_access_token:
        return None
    try:
        access_token = encryptor.decrypt(encrypted_access_token)
    except Exception:
        return None
    expires_ms = token_expiry_epoch_ms(access_token)
    return None if expires_ms is None else expires_ms / 1000.0


def account_access_token_expires_at(account: Account, encryptor: TokenEncryptor) -> float | None:
    """Return an account snapshot's known access-token expiry."""
    encrypted_access_token = getattr(account, "access_token_encrypted", None)
    return stored_access_token_expires_at(encrypted_access_token, encryptor)


def reauth_access_token_is_expired(
    status: AccountStatus,
    access_token_expires_at: float | None,
    *,
    now: float | None = None,
) -> bool:
    """Return whether a reauthentication-warning account is known unusable."""
    return (
        status == AccountStatus.REAUTH_REQUIRED
        and access_token_expires_at is not None
        and access_token_expires_at <= (time.time() if now is None else now)
    )


def all_accounts_require_reauthentication(
    accounts: Collection[Account],
    encryptor: TokenEncryptor,
) -> bool:
    """Return whether every candidate is reauthentication-blocked by known expiry."""
    now = time.time()
    return bool(accounts) and all(
        reauth_access_token_is_expired(
            account.status,
            account_access_token_expires_at(account, encryptor),
            now=now,
        )
        for account in accounts
    )
