"""Default :class:`ProxyConfigProvider` backed by ``AccountsRepository``.

Resolved lazily by ``account_http`` to avoid importing the database session
at module import time (which would create a cycle with the outbound HTTP
client modules).
"""

from __future__ import annotations

import logging

from app.core.clients.account_http import AccountProxyConnection, EgressContext
from app.core.crypto import TokenEncryptor
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

logger = logging.getLogger(__name__)


class DatabaseProxyConfigProvider:
    """Reads the per-account proxy configuration from the live database.

    The provider is lock-free and stateless: each call opens a short-lived
    session, queries the :class:`Account` row, and decrypts the password
    via :class:`TokenEncryptor`. Callers (the registry) are expected to
    cache the result for the lifetime of a managed client.
    """

    __slots__ = ("_encryptor",)

    def __init__(self, encryptor: TokenEncryptor | None = None) -> None:
        self._encryptor = encryptor or TokenEncryptor()

    async def get(self, account_id: str) -> AccountProxyConnection | None:
        ctx = await self.get_egress(account_id)
        return ctx.proxy

    async def get_egress(self, account_id: str) -> EgressContext:
        """Return the full egress context."""

        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            record = await repo.get_proxy_config(account_id)
            if record is None:
                return EgressContext(proxy=None)

        password: str | None = None
        if record.password_encrypted is not None:
            try:
                password = self._encryptor.decrypt(record.password_encrypted)
            except Exception:
                # Password decryption failed — the stored proxy config is
                # unrecoverable (e.g. encryption key rotation).  We MUST NOT
                # silently fall back to direct egress because that leaks the
                # account's real source IP.  Instead we return the connection
                # descriptor *without* a password; the ProxyConnector will fail
                # the SOCKS5 handshake with an auth error, which the runtime
                # failure tracker will surface and auto-deactivate the account
                # after the configured threshold.  The operator sees a clear
                # proxy-auth failure in the dashboard and re-enters the
                # password.
                logger.warning(
                    "Failed to decrypt proxy password for account_id=%s — "
                    "returning proxy config without password so traffic does "
                    "not leak onto direct egress",
                    account_id,
                    exc_info=True,
                )
                password = None

        proxy = AccountProxyConnection(
            host=record.host,
            port=record.port,
            username=record.username,
            password=password,
            remote_dns=record.remote_dns,
        )
        return EgressContext(proxy=proxy)
