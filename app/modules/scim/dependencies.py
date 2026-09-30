"""Authenticating ``/scim/v2``: a bearer token, and deliberately nothing else.

The dependency returns its own frozen record and never writes
``request.state.dashboard_principal``. That is not a stylistic choice: there is
no empty-grant :class:`DashboardPrincipal` constructor, and adding one would
make a machine token indistinguishable from a signed-in person at every
``principal.has(...)`` call site in the product. The shape copies the fleet API,
which reads every scoping decision off the key row's own columns.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import DashboardRateLimitError
from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter
from app.db.session import get_background_session
from app.modules.scim import errors
from app.modules.scim.repository import ScimTokensRepository
from app.modules.scim.tokens import hash_token

_bearer = HTTPBearer(description="SCIM bearer token", auto_error=False)

#: Two buckets, the shape the password login already documents: a coarse
#: ceiling on the peer address that an unauthenticated caller spends without a
#: row to key on, and a narrower one on the token row once there is one. The
#: narrow key is the row **id**, never the token or its digest —
#: ``rate_limit_attempts.key`` is stored in clear and the digest is the
#: verifier, so keying on it would file a credential-equivalent in the table
#: the limiter sweeps.
#: The coarse bucket is deliberately the wider of the two, so the narrow one is
#: what an identity provider actually meets: four pushes a second is far above
#: what a connector sends during a full reconcile, and a ``429`` with
#: ``Retry-After`` is the back-pressure signal RFC 7644 clients already honour.
_address_rate_limiter = DatabaseRateLimiter(max_attempts=600, window_seconds=60, type="scim_address")
_token_rate_limiter = DatabaseRateLimiter(max_attempts=240, window_seconds=60, type="scim_token")


def get_scim_address_rate_limiter() -> DatabaseRateLimiter:
    return _address_rate_limiter


def get_scim_token_rate_limiter() -> DatabaseRateLimiter:
    return _token_rate_limiter


@dataclass(frozen=True, slots=True)
class ScimTokenData:
    """What a verified SCIM credential authorises. No grants, by construction.

    ``provider_key`` is the identity namespace this token owns. It is read from
    the row and never from the request, which is the whole of "no confusion
    between tokens": a resource outside the namespace is not addressable, so a
    token cannot read, rename or disable an account another token provisioned —
    or any account this surface did not create.
    """

    id: str
    label: str
    provider_key: str


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def validate_scim_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ScimTokenData:
    """Verify the bearer, spend the rate-limit budget, stamp the last sync.

    Everything here runs on its own session and commits before the handler
    opens the account transaction. That ordering is load-bearing rather than
    tidy: the limiter commits, a commit releases the accounts write intent, and
    the break-glass guard requires its count and its conditional write to sit in
    one uncommitted transaction.
    """

    presented = credentials.credentials.strip() if credentials is not None else ""
    async with get_background_session() as session:
        try:
            await get_scim_address_rate_limiter().check_and_increment(f"scim:{_peer(request)}", session)
        except DashboardRateLimitError as exc:
            raise errors.rate_limited(exc.retry_after) from exc
        if not presented:
            raise errors.unauthenticated()

        presented_hash = hash_token(presented)
        repository = ScimTokensRepository(session)
        row = await repository.get_by_hash(presented_hash)
        # The lookup is an equality on a unique index over a digest, which is
        # the house pattern and the correct one: a wrong guess produces an
        # uncorrelated digest, so there is no byte-prefix oracle, and a
        # constant-time scan of every row would be strictly worse. The compare
        # below is redundant belt-and-braces, and keeps the property true if a
        # later change stops the lookup being an index equality.
        if row is None or not hmac.compare_digest(row.token_hash, presented_hash):
            raise errors.unauthenticated()

        token = ScimTokenData(id=row.id, label=row.label, provider_key=row.provider_key)
        try:
            await get_scim_token_rate_limiter().check_and_increment(f"scim-token:{token.id}", session)
        except DashboardRateLimitError as exc:
            raise errors.rate_limited(exc.retry_after) from exc
        await repository.record_used(token.id, datetime.now(UTC))
    return token
