"""Expand/contract bridge between the legacy shared admin password and ``dashboard_users``.

Release N stores the credential in both places: the ``admin`` user row (the new
source of truth from the next change on) and the legacy ``dashboard_settings``
columns that replicas still running the previous release read. Every write to
the legacy credential goes through :class:`CompatAdminProjection` so the two
never drift; release N+1 removes the projection and the legacy columns.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.db.models import COMPAT_ADMIN_USERNAME, DashboardUser, DashboardUserRoleSource, DashboardUserStatus

__all__ = ["COMPAT_ADMIN_USERNAME", "COMPAT_ADMIN_USER_ID", "CompatAdminProjection"]

#: Deterministic id of the migrated admin so the migration and the runtime
#: bootstrap path create the same row and re-runs stay idempotent.
COMPAT_ADMIN_USER_ID = str(
    uuid.uuid5(uuid.UUID("6f1c0e4e-2b4a-4c1e-9c3b-7a5d2e8f0a11"), "codex-lb:dashboard-user:compat-admin")
)


class CompatAdminProjection:
    """Mirror legacy-credential writes onto the ``admin`` user row (no commit)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> DashboardUser | None:
        stmt = select(DashboardUser).where(DashboardUser.username == COMPAT_ADMIN_USERNAME)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def ensure_exists(
        self,
        *,
        password_hash: str,
        totp_secret_encrypted: bytes | None = None,
        totp_last_verified_step: int | None = None,
    ) -> DashboardUser:
        """Create the ``admin`` row, or re-arm an existing credential-less one.

        Password removal keeps the row (with ``password_hash`` NULL) so the
        install is passwordless again; the next first-run setup must give that
        same row its new password instead of failing on the unique username.
        """

        user = await self.get()
        if user is not None:
            # Re-arming after a password removal: the row survives with NULL
            # credentials and must pick up the new ones.
            user.password_hash = password_hash
            user.totp_secret_encrypted = totp_secret_encrypted
            user.totp_last_verified_step = totp_last_verified_step
            await self._session.flush()
            return user
        user = DashboardUser(
            id=COMPAT_ADMIN_USER_ID,
            username=COMPAT_ADMIN_USERNAME,
            role_id=PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
            role_source=DashboardUserRoleSource.MANUAL.value,
            status=DashboardUserStatus.ACTIVE.value,
            password_hash=password_hash,
            totp_secret_encrypted=totp_secret_encrypted,
            totp_last_verified_step=totp_last_verified_step,
            is_break_glass=True,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def set_password_hash(self, password_hash: str | None) -> None:
        user = await self.get()
        if user is not None:
            user.password_hash = password_hash
        elif password_hash is not None:
            # A replica of the previous release set the legacy password without
            # a user row; the first mirrored write self-heals the projection.
            await self.ensure_exists(password_hash=password_hash)

    async def set_totp_secret(self, secret_encrypted: bytes | None, *, legacy_password_hash: str | None) -> None:
        user = await self.get()
        if user is not None:
            user.totp_secret_encrypted = secret_encrypted
            user.totp_last_verified_step = None
        elif secret_encrypted is not None and legacy_password_hash is not None:
            await self.ensure_exists(password_hash=legacy_password_hash, totp_secret_encrypted=secret_encrypted)

    async def try_advance_totp_step(self, step: int) -> bool | None:
        """Advance the replay counter; ``None`` when there is no compat user to mirror."""

        result = await self._session.execute(
            update(DashboardUser)
            .where(DashboardUser.username == COMPAT_ADMIN_USERNAME)
            .where(
                or_(
                    DashboardUser.totp_last_verified_step.is_(None),
                    DashboardUser.totp_last_verified_step < step,
                )
            )
            .values(totp_last_verified_step=step)
            .returning(DashboardUser.id)
        )
        advanced = result.scalar_one_or_none() is not None
        if advanced:
            return True
        exists = (await self.get()) is not None
        return False if exists else None
