"""Durable state for one OIDC round trip, plus the cookie that binds it to a browser.

Two things hold a flow together and the callback needs both. The **row** is
race-free but binds to nobody: anyone holding a leaked ``state`` could finish a
flow somebody else started. The **cookie** binds to the browser but is not
single-use: two callbacks presenting it both read it, and clearing it is a
read-then-write. So the row is consumed by one conditional ``DELETE`` -- exactly
one caller in the fleet wins it -- and the cookie must carry the same ``state``
the URL does.

The clear ``state`` and ``nonce`` are never stored. The ``state`` comes back in
the URL and the ``nonce`` comes back inside the ID token, so both can be hashed
and compared; the PKCE verifier is the one value that must come back out, and
it is sealed with the product's single :class:`TokenEncryptor` exactly as the
account-linking flow seals its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from time import time
from typing import Any, Final

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import DashboardOidcLoginFlow

#: Short-lived, path-scoped, and ``SameSite=Lax`` rather than ``Strict``: the
#: callback is a top-level cross-site navigation, and ``Strict`` would drop the
#: cookie on exactly the request that needs it.
OIDC_FLOW_COOKIE: Final[str] = "codex_lb_oidc_flow"
OIDC_FLOW_COOKIE_PATH: Final[str] = "/api/dashboard-auth/oidc"
#: Where a sign-in through the identity provider begins. The login screen
#: advertises it as the provider's ``login_url``; it lives here rather than in
#: the route module so the session response can name it without importing the
#: routes (which import the session service).
OIDC_LOGIN_START_PATH: Final[str] = "/api/dashboard-auth/oidc/login/start"
#: Long enough for a password plus a second factor at the identity provider,
#: short enough that a captured authorization URL goes stale quickly.
OIDC_FLOW_TTL_SECONDS: Final[int] = 600


class OidcFlowPurpose(StrEnum):
    """Why the flow was started -- and, therefore, where the browser lands.

    The destination is derived from this stored value, which is how the flow
    manages without a ``next``/``return_to`` parameter the caller could aim at
    another site.
    """

    LOGIN = "login"
    TEST = "test"
    STEP_UP = "step_up"


@dataclass(slots=True)
class OidcFlowRecord:
    """One flow. ``code_verifier`` is in clear in memory only."""

    state_hash: str
    provider_id: str
    nonce_hash: str
    code_verifier: str
    purpose: str
    redirect_uri: str
    #: The digest of the connection document the flow was started against;
    #: :func:`app.modules.auth_providers.config.oidc_config_fingerprint`.
    config_fingerprint: str
    created_at: datetime
    expires_at: datetime
    acting_user_id: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        return to_utc_naive(self.expires_at) <= (now or utcnow())

    @property
    def created_at_epoch(self) -> int:
        """When this flow began, in epoch seconds, whatever the backend returned.

        SQLite hands back a naive datetime and PostgreSQL an aware one, and
        ``datetime.timestamp()`` reads a naive value as *local* time. On a host
        that is not UTC that would silently move the flow's start by the local
        offset -- and the ID token's ``iat`` is checked against it, so the error
        would widen the window a captured token is accepted in.
        """

        return naive_utc_to_epoch(to_utc_naive(self.created_at))


class OidcFlowRepository:
    """The flow table. The verifier is encrypted at this boundary and nowhere else."""

    def __init__(self, session: AsyncSession, encryptor: TokenEncryptor | None = None) -> None:
        self._session = session
        self._encryptor = encryptor or TokenEncryptor()

    async def create(
        self,
        *,
        state_hash: str,
        provider_id: str,
        nonce_hash: str,
        code_verifier: str,
        purpose: OidcFlowPurpose,
        redirect_uri: str,
        config_fingerprint: str,
        acting_user_id: str | None = None,
        ttl_seconds: int = OIDC_FLOW_TTL_SECONDS,
    ) -> OidcFlowRecord:
        now = utcnow()
        row = DashboardOidcLoginFlow(
            state_hash=state_hash,
            provider_id=provider_id,
            nonce_hash=nonce_hash,
            code_verifier_encrypted=self._encryptor.encrypt(code_verifier),
            purpose=purpose.value,
            acting_user_id=acting_user_id,
            redirect_uri=redirect_uri,
            config_fingerprint=config_fingerprint,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._session.add(row)
        await self._session.commit()
        return OidcFlowRecord(
            state_hash=state_hash,
            provider_id=provider_id,
            nonce_hash=nonce_hash,
            code_verifier=code_verifier,
            purpose=purpose.value,
            redirect_uri=redirect_uri,
            config_fingerprint=config_fingerprint,
            created_at=now,
            expires_at=row.expires_at,
            acting_user_id=acting_user_id,
        )

    async def consume(self, state_hash: str) -> OidcFlowRecord | None:
        """Take the flow, once, fleet-wide.

        One conditional ``DELETE ... RETURNING``: two callbacks racing on two
        replicas produce exactly one winner, and a replay finds nothing. An
        expired row is deleted and reported as absent, which is both the
        refusal and the purge.
        """

        statement = delete(DashboardOidcLoginFlow).where(DashboardOidcLoginFlow.state_hash == state_hash)
        result = await self._session.execute(
            statement.returning(
                DashboardOidcLoginFlow.state_hash,
                DashboardOidcLoginFlow.provider_id,
                DashboardOidcLoginFlow.nonce_hash,
                DashboardOidcLoginFlow.code_verifier_encrypted,
                DashboardOidcLoginFlow.purpose,
                DashboardOidcLoginFlow.acting_user_id,
                DashboardOidcLoginFlow.redirect_uri,
                DashboardOidcLoginFlow.config_fingerprint,
                DashboardOidcLoginFlow.created_at,
                DashboardOidcLoginFlow.expires_at,
            ).execution_options(synchronize_session=False)
        )
        row = result.first()
        await self._session.commit()
        if row is None:
            return None
        record = OidcFlowRecord(
            state_hash=row[0],
            provider_id=row[1],
            nonce_hash=row[2],
            code_verifier=self._encryptor.decrypt(row[3]),
            purpose=row[4],
            acting_user_id=row[5],
            redirect_uri=row[6],
            config_fingerprint=row[7],
            created_at=row[8],
            expires_at=row[9],
        )
        return None if record.is_expired() else record

    async def purge_expired(self) -> None:
        """Opportunistic: an abandoned flow is one row and expires on its own."""

        await self._session.execute(delete(DashboardOidcLoginFlow).where(DashboardOidcLoginFlow.expires_at <= utcnow()))
        await self._session.commit()


class OidcFlowCookieStore:
    """The browser's half of the flow: the ``state``, sealed, and nothing else.

    Same shape as the step-up cookie -- sealed JSON with a payload version and
    its own expiry -- so the browser cannot read, forge or extend it.
    """

    PAYLOAD_VERSION = 1

    def __init__(self) -> None:
        self._encryptor: TokenEncryptor | None = None

    def _get_encryptor(self) -> TokenEncryptor:
        if self._encryptor is None:
            self._encryptor = TokenEncryptor()
        return self._encryptor

    def create(self, state: str, *, ttl_seconds: int = OIDC_FLOW_TTL_SECONDS) -> str:
        payload: dict[str, Any] = {
            "v": self.PAYLOAD_VERSION,
            "st": state,
            "exp": int(time()) + ttl_seconds,
        }
        return self._get_encryptor().encrypt(json.dumps(payload, separators=(",", ":"))).decode("ascii")

    def get(self, token: str | None) -> str | None:
        """The ``state`` this browser started, or ``None`` for anything unreadable."""

        if not token:
            return None
        try:
            data = json.loads(self._get_encryptor().decrypt(token.strip().encode("ascii")))
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("v") != self.PAYLOAD_VERSION:
            return None
        expires_at = data.get("exp")
        if not isinstance(expires_at, int) or isinstance(expires_at, bool) or expires_at < int(time()):
            return None
        state = data.get("st")
        return state if isinstance(state, str) and state else None


_flow_cookie_store = OidcFlowCookieStore()


def get_oidc_flow_cookie_store() -> OidcFlowCookieStore:
    return _flow_cookie_store
