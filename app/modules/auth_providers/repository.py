from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DashboardAuthProvider, DashboardUser
from app.modules.dashboard_users.repository import DashboardUsersRepository


class AuthProvidersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_account_write_intent(self) -> None:
        """Serialise a provider enable against the account mutations its gate counts."""

        await DashboardUsersRepository(self._session).acquire_write_intent()

    async def count_qualifying_break_glass(self) -> int:
        return await DashboardUsersRepository(self._session).count_qualifying_break_glass()

    async def list_break_glass_designations(self) -> Sequence[DashboardUser]:
        return await DashboardUsersRepository(self._session).list_break_glass_designations()

    async def list_providers(self) -> Sequence[DashboardAuthProvider]:
        stmt = select(DashboardAuthProvider).order_by(
            DashboardAuthProvider.created_at.asc(), DashboardAuthProvider.id.asc()
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get_provider(self, provider_id: str) -> DashboardAuthProvider | None:
        return await self._session.get(DashboardAuthProvider, provider_id)

    async def get_by_kind(self, kind: str, provider_key: str) -> DashboardAuthProvider | None:
        """The one row of a kind, whether or not it is enabled.

        The pre-flight test sign-in needs exactly this: it runs *against* a row
        that is still disabled, which is why it cannot go through the registry
        (which only ever hands out active providers).
        """

        stmt = (
            select(DashboardAuthProvider)
            .where(DashboardAuthProvider.kind == kind)
            .where(DashboardAuthProvider.provider_key == provider_key)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def record_test_login(
        self,
        provider_id: str,
        *,
        user_id: str,
        verified_at: datetime,
        verified_config: bytes | None,
    ) -> bool:
        """Stamp a completed pre-flight sign-in on the row; ``False`` if it no longer applies.

        One targeted ``UPDATE`` rather than a read-modify-write: the callback
        that stamps this runs concurrently with whatever the settings page is
        doing, and it has no business touching any other column.

        ``verified_config`` is the sealed document the round trip was actually
        completed against, and the ``WHERE`` requires the row to still hold it.
        Without that condition the stamp is not bound to anything: a
        configuration write that commits while the callback is out at the
        identity provider clears the proof, and this ``UPDATE`` would then
        write it straight back against an issuer nobody has tested. The
        ciphertext compares by identity rather than by value -- the sealing is
        randomised -- which is exactly the conservative direction: a
        re-save of a byte-identical document refuses the stamp instead of
        accepting it.
        """

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(DashboardAuthProvider)
                .where(DashboardAuthProvider.id == provider_id)
                .where(DashboardAuthProvider.config_encrypted == verified_config)
                .values(test_login_user_id=user_id, test_login_verified_at=verified_at)
                .execution_options(synchronize_session=False)
            ),
        )
        await self._session.commit()
        return int(result.rowcount or 0) > 0

    async def commit(self, provider: DashboardAuthProvider) -> DashboardAuthProvider:
        """Commit and reload the row (``expire_on_commit`` would otherwise lazy-load it outside the loop)."""

        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(provider)
        return provider
