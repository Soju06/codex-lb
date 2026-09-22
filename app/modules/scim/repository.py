"""Queries and writes for SCIM bearer tokens and the accounts they provision.

Two concerns live here because they share one rule: every read and every write
is bound to the ``provider_key`` carried on the token row. An account the token
did not provision has no ``scim`` identity in that namespace and is therefore
not addressable at all — which is what stands in for ``assert_can_act_on`` on a
surface that has no principal.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AuditLog,
    DashboardIdentity,
    DashboardRoleRecord,
    DashboardScimToken,
    DashboardUser,
)

#: The provider string on every identity row this surface writes.
SCIM_PROVIDER = "scim"
#: How many resources one list page may carry, whatever the caller asks for.
#: RFC 7644 §3.4.2.4 lets a server return fewer than ``count``; clamping is the
#: bound on a read, the way the body cap is the bound on a write.
MAX_LIST_COUNT = 200


def _identity_query():
    return select(DashboardIdentity).options(
        selectinload(DashboardIdentity.user).selectinload(DashboardUser.role).selectinload(DashboardRoleRecord.grants)
    )


class ScimTokensRepository:
    """The credential rows. Nothing here ever returns or accepts a plaintext token."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_hash(self, token_hash: str) -> DashboardScimToken | None:
        stmt = select(DashboardScimToken).where(DashboardScimToken.token_hash == token_hash)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get(self, token_id: str) -> DashboardScimToken | None:
        return await self._session.get(DashboardScimToken, token_id)

    async def list_tokens(self) -> Sequence[DashboardScimToken]:
        stmt = select(DashboardScimToken).order_by(DashboardScimToken.created_at.asc(), DashboardScimToken.id.asc())
        return (await self._session.execute(stmt)).scalars().all()

    async def count_tokens(self) -> int:
        return int((await self._session.execute(select(func.count()).select_from(DashboardScimToken))).scalar_one())

    async def create(
        self,
        *,
        label: str,
        token_hash: str,
        token_prefix: str,
        provider_key: str,
        created_by_user_id: str | None,
    ) -> DashboardScimToken:
        row = DashboardScimToken(
            id=str(uuid.uuid4()),
            label=label,
            token_hash=token_hash,
            token_prefix=token_prefix,
            provider_key=provider_key,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def rotate(self, token_id: str, *, token_hash: str, token_prefix: str) -> DashboardScimToken | None:
        """Replace the verifier on the same row; the id, label and sync history survive."""

        updated = await self._session.execute(
            update(DashboardScimToken)
            .where(DashboardScimToken.id == token_id)
            .values(token_hash=token_hash, token_prefix=token_prefix, rotated_at=datetime.now(UTC))
            .returning(DashboardScimToken.id)
            .execution_options(synchronize_session=False)
        )
        if updated.scalar_one_or_none() is None:
            await self._session.rollback()
            return None
        await self._session.commit()
        self._session.expire_all()
        return await self.get(token_id)

    async def delete(self, token_id: str) -> bool:
        deleted = await self._session.execute(
            delete(DashboardScimToken)
            .where(DashboardScimToken.id == token_id)
            .returning(DashboardScimToken.id)
            .execution_options(synchronize_session=False)
        )
        if deleted.scalar_one_or_none() is None:
            await self._session.rollback()
            return False
        await self._session.commit()
        return True

    async def record_used(self, token_id: str, now: datetime) -> None:
        """Advance ``last_used_at`` monotonically, so replicas may write out of order.

        The guard is the whole point: two replicas serving two pushes can
        commit in either order, and a blind ``SET`` would let the older one win
        and make the card's "last sync" go backwards.
        """

        await self._session.execute(
            update(DashboardScimToken)
            .where(DashboardScimToken.id == token_id)
            .where(or_(DashboardScimToken.last_used_at.is_(None), DashboardScimToken.last_used_at < now))
            .values(last_used_at=now)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()


class ScimUsersRepository:
    """Accounts as this surface sees them: through their ``scim`` identity row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_identity_by_subject(self, provider_key: str, subject: str) -> DashboardIdentity | None:
        """The identity a push names. ``subject`` is compared exactly: it is opaque."""

        stmt = (
            _identity_query()
            .where(DashboardIdentity.provider == SCIM_PROVIDER)
            .where(DashboardIdentity.provider_key == provider_key)
            .where(DashboardIdentity.subject == subject)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_identity_by_user(self, provider_key: str, user_id: str) -> DashboardIdentity | None:
        """The resource a SCIM ``id`` names, or ``None`` when it is outside this namespace.

        An account without a ``scim`` identity here is not a resource of this
        token, whatever else it is on the install, and the route answers ``404``
        rather than distinguishing "no such account" from "not yours".
        """

        stmt = (
            _identity_query()
            .where(DashboardIdentity.provider == SCIM_PROVIDER)
            .where(DashboardIdentity.provider_key == provider_key)
            .where(DashboardIdentity.user_id == user_id)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def audit_row_exists(self, action: str, user_id: str) -> bool:
        """Whether this surface has already recorded ``action`` against that account.

        The audit row is the marker for the decisions this surface makes once
        per account rather than once per push — an identity provider re-pushes
        its whole directory on a schedule, and a row per push buries the one an
        operator needs. ``idx_audit_logs_target`` covers the predicate.
        """

        stmt = (
            select(AuditLog.id)
            .where(AuditLog.action == action)
            .where(AuditLog.target_type == "user")
            .where(AuditLog.target_id == user_id)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first() is not None

    async def list_identities(
        self, provider_key: str, *, user_name: str | None, offset: int, limit: int
    ) -> tuple[Sequence[DashboardIdentity], int]:
        """One page of this namespace's resources plus the unpaged total.

        Disabled and invited accounts are included on purpose: a deprovisioned
        person has to stay rediscoverable by the filter that found them.
        """

        base = (
            select(DashboardIdentity.id)
            .where(DashboardIdentity.provider == SCIM_PROVIDER)
            .where(DashboardIdentity.provider_key == provider_key)
        )
        if user_name is not None:
            base = base.where(func.lower(DashboardIdentity.user_name) == user_name.casefold())
        total = int((await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
        page = _identity_query().where(DashboardIdentity.id.in_(base))
        rows = (
            (
                await self._session.execute(
                    page.order_by(DashboardIdentity.created_at.asc(), DashboardIdentity.id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return rows, total
