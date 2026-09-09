from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DashboardSettingsConflictError
from app.db.models import DashboardSettings
from app.modules.dashboard_users.compat import CompatAdminProjection
from app.modules.settings.repository import SettingsRepository

_SETTINGS_ID = 1


class DashboardAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings_repository = SettingsRepository(session)
        # Release N: legacy credential columns are a projection of the compat
        # `admin` user row; every credential write is mirrored before commit.
        self._compat = CompatAdminProjection(session)

    async def get_settings(self) -> DashboardSettings:
        return await self._settings_repository.get_or_create()

    async def _mutate_settings_with_retry(
        self,
        mutate: Callable[[DashboardSettings], None],
        *,
        mirror: Callable[[DashboardSettings], Awaitable[None]] | None = None,
    ) -> DashboardSettings:
        """Apply a single-purpose settings mutation, retrying once on a version conflict.

        These mutations are idempotent absolute writes (set/clear a credential
        field), so losing the optimistic version race to a concurrent settings
        update is benign: re-read the fresh row, re-apply the same mutation,
        and commit again instead of surfacing a 500.

        ``mirror`` re-applies the compat-admin projection of the same write
        (it receives the mutated legacy row). It runs before every commit
        attempt because the conflict rollback discards the flushed user-row
        update together with the legacy one.
        """
        row = await self._settings_repository.get_or_create()
        mutate(row)
        if mirror is not None:
            await mirror(row)
        try:
            await self._settings_repository.commit_refresh(row)
        except DashboardSettingsConflictError:
            row = await self._settings_repository.get_or_create()
            await self._session.refresh(row)
            mutate(row)
            if mirror is not None:
                await mirror(row)
            await self._settings_repository.commit_refresh(row)
        return row

    async def set_totp_secret(self, secret_encrypted: bytes | None) -> DashboardSettings:
        def _mutate(row: DashboardSettings) -> None:
            row.totp_secret_encrypted = secret_encrypted
            row.totp_last_verified_step = None
            if secret_encrypted is None:
                row.totp_required_on_login = False

        return await self._mutate_settings_with_retry(
            _mutate,
            mirror=lambda row: self._compat.set_totp_secret(secret_encrypted, legacy_password_hash=row.password_hash),
        )

    async def set_password_hash(self, password_hash: str) -> DashboardSettings:
        def _mutate(row: DashboardSettings) -> None:
            row.password_hash = password_hash
            row.bootstrap_token_encrypted = None
            row.bootstrap_token_hash = None

        return await self._mutate_settings_with_retry(
            _mutate, mirror=lambda _row: self._compat.set_password_hash(password_hash)
        )

    async def set_guest_password_hash(self, password_hash: str) -> DashboardSettings:
        def _mutate(row: DashboardSettings) -> None:
            row.guest_password_hash = password_hash
            # Changing the guest credential must log every current guest out.
            row.guest_session_generation += 1

        return await self._mutate_settings_with_retry(_mutate)

    async def clear_guest_password_hash(self) -> DashboardSettings:
        def _mutate(row: DashboardSettings) -> None:
            row.guest_password_hash = None
            row.guest_session_generation += 1

        return await self._mutate_settings_with_retry(_mutate)

    async def bump_guest_session_generation(self) -> DashboardSettings:
        def _mutate(row: DashboardSettings) -> None:
            row.guest_session_generation += 1

        return await self._mutate_settings_with_retry(_mutate)

    async def try_set_password_hash(self, password_hash: str) -> bool:
        await self._settings_repository.get_or_create()
        result = await self._session.execute(
            update(DashboardSettings)
            .where(DashboardSettings.id == _SETTINGS_ID)
            .where(DashboardSettings.password_hash.is_(None))
            .values(password_hash=password_hash, bootstrap_token_encrypted=None, bootstrap_token_hash=None)
            .returning(DashboardSettings.id)
        )
        configured = result.scalar_one_or_none() is not None
        if configured:
            # First-run setup creates the `admin` account the credential belongs to.
            await self._compat.ensure_exists(password_hash=password_hash)
        await self._session.commit()
        return configured

    async def get_password_hash(self) -> str | None:
        row = await self._settings_repository.get_or_create()
        return row.password_hash

    async def clear_password_and_totp(self) -> DashboardSettings:
        def _mutate(row: DashboardSettings) -> None:
            row.password_hash = None
            row.bootstrap_token_encrypted = None
            row.bootstrap_token_hash = None
            row.totp_required_on_login = False
            row.totp_secret_encrypted = None
            row.totp_last_verified_step = None

        async def _mirror(_row: DashboardSettings) -> None:
            await self._compat.set_password_hash(None)
            await self._compat.set_totp_secret(None, legacy_password_hash=None)

        return await self._mutate_settings_with_retry(_mutate, mirror=_mirror)

    async def store_bootstrap_token_if_absent(self, token_encrypted: bytes, token_hash: bytes) -> bool:
        await self._settings_repository.get_or_create()
        result = await self._session.execute(
            update(DashboardSettings)
            .where(DashboardSettings.id == _SETTINGS_ID)
            .where(DashboardSettings.password_hash.is_(None))
            .where(DashboardSettings.bootstrap_token_hash.is_(None))
            .values(bootstrap_token_encrypted=token_encrypted, bootstrap_token_hash=token_hash)
            .returning(DashboardSettings.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def clear_bootstrap_token(self) -> bool:
        await self._settings_repository.get_or_create()
        result = await self._session.execute(
            update(DashboardSettings)
            .where(DashboardSettings.id == _SETTINGS_ID)
            .where(DashboardSettings.bootstrap_token_hash.is_not(None))
            .values(bootstrap_token_encrypted=None, bootstrap_token_hash=None)
            .returning(DashboardSettings.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def try_advance_totp_last_verified_step(self, step: int) -> bool:
        await self._settings_repository.get_or_create()
        result = await self._session.execute(
            update(DashboardSettings)
            .where(DashboardSettings.id == _SETTINGS_ID)
            .where(
                or_(
                    DashboardSettings.totp_last_verified_step.is_(None),
                    DashboardSettings.totp_last_verified_step < step,
                )
            )
            .values(totp_last_verified_step=step)
            .returning(DashboardSettings.id)
        )
        advanced = result.scalar_one_or_none() is not None
        # The replay counter must advance in both places or in neither: a code
        # accepted by the legacy column but already used on the user row (or
        # vice versa) is a replay.
        mirrored = await self._compat.try_advance_totp_step(step)
        if not advanced or mirrored is False:
            await self._session.rollback()
            return False
        await self._session.commit()
        return True
