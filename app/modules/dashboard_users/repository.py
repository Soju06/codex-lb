"""Read-side queries over ``dashboard_users`` shared by the users cache and the auth module."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.db.models import DashboardIdentity, DashboardRoleRecord, DashboardUser, DashboardUserStatus

_USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]{1,64}$")


def normalize_username(value: str) -> str:
    """Usernames are compared case-insensitively and stored normalized."""

    return value.strip().casefold()


def is_valid_username(value: str) -> bool:
    return _USERNAME_PATTERN.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class LocalAuthState:
    """What the users table says about whether this install requires sign-in.

    ``requires_auth`` is true when an active user can sign in at all (holds a
    password or an external identity). ``sole_local_password_user_id`` is set
    only when exactly one active user holds a password, which lets the login
    form omit the username field on single-user installs.
    """

    any_user: bool
    active_users: int
    active_local_password_users: int
    requires_auth: bool
    sole_local_password_user_id: str | None


@dataclass(frozen=True, slots=True)
class DashboardUserCounts:
    total: int
    active: int
    invited: int
    disabled: int
    non_admin: int


def _user_query():
    return select(DashboardUser).options(
        selectinload(DashboardUser.role).selectinload(DashboardRoleRecord.grants),
    )


class DashboardUsersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: str) -> DashboardUser | None:
        stmt = _user_query().where(DashboardUser.id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_username(self, normalized_username: str) -> DashboardUser | None:
        stmt = _user_query().where(DashboardUser.username == normalized_username)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_active_local_password_users(self) -> Sequence[DashboardUser]:
        stmt = (
            _user_query()
            .where(DashboardUser.status == DashboardUserStatus.ACTIVE.value)
            .where(DashboardUser.password_hash.is_not(None))
            .order_by(DashboardUser.created_at.asc(), DashboardUser.id.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def count_identities(self, user_id: str) -> int:
        stmt = select(func.count()).select_from(DashboardIdentity).where(DashboardIdentity.user_id == user_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def local_auth_state(self) -> LocalAuthState:
        active = DashboardUser.status == DashboardUserStatus.ACTIVE.value
        totals = (
            await self._session.execute(
                select(
                    func.count(DashboardUser.id),
                    func.count(DashboardUser.id).filter(active),
                )
            )
        ).one()
        total_users, active_users = int(totals[0]), int(totals[1])
        password_user_ids = (
            (
                await self._session.execute(
                    select(DashboardUser.id).where(active).where(DashboardUser.password_hash.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        identity_user_exists = (
            await self._session.execute(
                select(DashboardIdentity.id)
                .join(DashboardUser, DashboardUser.id == DashboardIdentity.user_id)
                .where(active)
                .limit(1)
            )
        ).first() is not None
        return LocalAuthState(
            any_user=total_users > 0,
            active_users=active_users,
            active_local_password_users=len(password_user_ids),
            requires_auth=bool(password_user_ids) or identity_user_exists,
            sole_local_password_user_id=password_user_ids[0] if len(password_user_ids) == 1 else None,
        )

    async def counts(self) -> DashboardUserCounts:
        status = DashboardUser.status
        row = (
            await self._session.execute(
                select(
                    func.count(DashboardUser.id),
                    func.count(DashboardUser.id).filter(status == DashboardUserStatus.ACTIVE.value),
                    func.count(DashboardUser.id).filter(status == DashboardUserStatus.INVITED.value),
                    func.count(DashboardUser.id).filter(status == DashboardUserStatus.DISABLED.value),
                    func.count(DashboardUser.id).filter(DashboardUser.role_id != PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]),
                )
            )
        ).one()
        return DashboardUserCounts(
            total=int(row[0]),
            active=int(row[1]),
            invited=int(row[2]),
            disabled=int(row[3]),
            non_admin=int(row[4]),
        )
