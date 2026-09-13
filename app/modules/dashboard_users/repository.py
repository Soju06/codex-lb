"""Queries and writes over ``dashboard_users`` and ``dashboard_user_invites``.

The read side is shared with the users cache and the auth module. Writes here
never touch credentials of the compat ``admin`` (those go through
``DashboardAuthRepository`` so the legacy mirror stays consistent); they cover
account lifecycle (invite, edit, disable, delete), the invite rows, and the
owner-driven API-key cascade, all inside the caller's session.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.db.models import (
    ApiKey,
    ApiKeyDeactivatedReason,
    DashboardIdentity,
    DashboardRoleRecord,
    DashboardUser,
    DashboardUserInvite,
    DashboardUserStatus,
)

_USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]{1,64}$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utc_now() -> datetime:
    """The clock every invite-liveness decision reads (one seam for tests)."""

    return datetime.now(UTC)


def normalize_username(value: str) -> str:
    """Usernames are compared case-insensitively and stored normalized."""

    return value.strip().casefold()


def is_valid_username(value: str) -> bool:
    return _USERNAME_PATTERN.fullmatch(value) is not None


def normalize_email(value: str | None) -> str | None:
    """E-mails are stored lower-cased; blank means "no e-mail"."""

    normalized = (value or "").strip().casefold()
    return normalized or None


def is_valid_email(value: str) -> bool:
    return len(value) <= 320 and _EMAIL_PATTERN.fullmatch(value) is not None


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
    pending_invites: int


def _user_query():
    return select(DashboardUser).options(
        selectinload(DashboardUser.role).selectinload(DashboardRoleRecord.grants),
    )


def _invite_query():
    return select(DashboardUserInvite).options(
        selectinload(DashboardUserInvite.user).selectinload(DashboardUser.role).selectinload(DashboardRoleRecord.grants)
    )


def live_invite_filter(now: datetime) -> ColumnElement[bool]:
    """An invite that can still be accepted: not consumed, not revoked, and not expired.

    An SSO-only invite has no link to expire: the account waits for its first
    provider sign-in for as long as the administrator leaves it in place.
    """

    return and_(
        DashboardUserInvite.consumed_at.is_(None),
        DashboardUserInvite.revoked_at.is_(None),
        or_(DashboardUserInvite.sso_only.is_(True), DashboardUserInvite.expires_at > now),
    )


class DashboardUsersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- reads ---

    async def get_by_id(self, user_id: str) -> DashboardUser | None:
        stmt = _user_query().where(DashboardUser.id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_username(self, normalized_username: str) -> DashboardUser | None:
        stmt = _user_query().where(DashboardUser.username == normalized_username)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, normalized_email: str) -> DashboardUser | None:
        stmt = _user_query().where(DashboardUser.email == normalized_email)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_identity(self, provider: str, provider_key: str, subject: str) -> DashboardIdentity | None:
        """The identity row for a provider triple, with its account and role loaded."""

        stmt = (
            select(DashboardIdentity)
            .options(
                selectinload(DashboardIdentity.user)
                .selectinload(DashboardUser.role)
                .selectinload(DashboardRoleRecord.grants)
            )
            .where(DashboardIdentity.provider == provider)
            .where(DashboardIdentity.provider_key == provider_key)
            .where(DashboardIdentity.subject == subject)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_users(self) -> Sequence[DashboardUser]:
        stmt = _user_query().order_by(DashboardUser.created_at.asc(), DashboardUser.id.asc())
        return (await self._session.execute(stmt)).scalars().all()

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

    async def primary_identity_provider(self, user_id: str) -> str | None:
        """The provider of the account's oldest identity (which sign-in method manages it)."""

        stmt = (
            select(DashboardIdentity.provider)
            .where(DashboardIdentity.user_id == user_id)
            .order_by(DashboardIdentity.created_at.asc(), DashboardIdentity.id.asc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def count_active_admins(self, *, exclude_user_id: str | None = None) -> int:
        """Active accounts holding the admin *preset* (custom roles never count, however wide)."""

        stmt = (
            select(func.count())
            .select_from(DashboardUser)
            .where(DashboardUser.status == DashboardUserStatus.ACTIVE.value)
            .where(DashboardUser.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN])
        )
        if exclude_user_id is not None:
            stmt = stmt.where(DashboardUser.id != exclude_user_id)
        return int((await self._session.execute(stmt)).scalar_one())

    def _qualifying_break_glass_filter(self) -> ColumnElement[bool]:
        """The five facts of a qualifying break-glass account, as a WHERE clause.

        Kept in step with :func:`app.modules.dashboard_users.break_glass.qualifies`;
        the password term is what stops a proxy-provisioned admin with no local
        credential from counting as a way back in.
        """

        return and_(
            DashboardUser.is_break_glass.is_(True),
            DashboardUser.status == DashboardUserStatus.ACTIVE.value,
            DashboardUser.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
            DashboardUser.totp_secret_encrypted.is_not(None),
            DashboardUser.password_hash.is_not(None),
        )

    async def count_qualifying_break_glass(self, *, exclude_user_id: str | None = None) -> int:
        """Accounts that could still open the door while local sign-in is restricted."""

        stmt = select(func.count()).select_from(DashboardUser).where(self._qualifying_break_glass_filter())
        if exclude_user_id is not None:
            stmt = stmt.where(DashboardUser.id != exclude_user_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_break_glass_designations(self) -> Sequence[DashboardUser]:
        """Every designated account, qualifying or not, so a refusal can name the one that would fix it."""

        stmt = (
            _user_query()
            .where(DashboardUser.is_break_glass.is_(True))
            .order_by(DashboardUser.created_at.asc(), DashboardUser.id.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

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

    async def counts(self, *, now: datetime | None = None) -> DashboardUserCounts:
        """Team-size facts. Invited accounts whose invite is no longer live are
        zombies awaiting the lazy purge and are not counted anywhere."""

        status = DashboardUser.status
        live = self._live_account_filter(now or utc_now())
        row = (
            await self._session.execute(
                select(
                    func.count(DashboardUser.id),
                    func.count(DashboardUser.id).filter(status == DashboardUserStatus.ACTIVE.value),
                    func.count(DashboardUser.id).filter(status == DashboardUserStatus.INVITED.value),
                    func.count(DashboardUser.id).filter(status == DashboardUserStatus.DISABLED.value),
                    func.count(DashboardUser.id).filter(DashboardUser.role_id != PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]),
                ).where(live)
            )
        ).one()
        return DashboardUserCounts(
            total=int(row[0]),
            active=int(row[1]),
            invited=int(row[2]),
            disabled=int(row[3]),
            non_admin=int(row[4]),
            pending_invites=int(row[2]),
        )

    @staticmethod
    def _live_account_filter(now: datetime) -> ColumnElement[bool]:
        live_invite = (
            select(DashboardUserInvite.id)
            .where(DashboardUserInvite.user_id == DashboardUser.id)
            .where(live_invite_filter(now))
            .exists()
        )
        return or_(DashboardUser.status != DashboardUserStatus.INVITED.value, live_invite)

    # --- write serialisation and guarded writes ---

    async def acquire_write_intent(self) -> None:
        """Serialise concurrent account mutations before any invariant is read.

        SQLite: ``BEGIN IMMEDIATE`` takes the database write lock up front (the
        same pattern the accounts repository uses), so two admins disabling
        each other queue instead of both passing the last-admin check.
        PostgreSQL: lock the active admin rows ``FOR UPDATE``; every mutation
        that could change who counts as an admin must go through them.

        ``BEGIN IMMEDIATE`` cannot run inside an open transaction, so a caller
        that has already read something falls back to a row-less ``UPDATE``.
        That statement is not a no-op for locking: SQLite opens the table for
        writing and takes the RESERVED lock before evaluating the predicate,
        which is the whole point of the fallback. Callers should still acquire
        before their first read, so the two dialects take the lock at the same
        moment and neither depends on that subtlety.
        """

        if self._session.get_bind().dialect.name == "sqlite":
            try:
                await self._session.execute(text("BEGIN IMMEDIATE"))
            except OperationalError as exc:
                if "within a transaction" not in str(exc).lower():
                    raise
                await self._session.execute(text("UPDATE dashboard_users SET id = id WHERE 1 = 0"))
            return
        await self._session.execute(
            select(DashboardUser.id)
            .where(DashboardUser.status == DashboardUserStatus.ACTIVE.value)
            .where(DashboardUser.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN])
            .with_for_update()
        )

    @staticmethod
    def _other_active_admin_exists(user_id: str) -> ColumnElement[bool]:
        other = aliased(DashboardUser)
        return (
            select(other.id)
            .where(other.id != user_id)
            .where(other.status == DashboardUserStatus.ACTIVE.value)
            .where(other.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN])
            .exists()
        )

    def _other_qualifying_break_glass_exists(self, user_id: str) -> ColumnElement[bool]:
        other = aliased(DashboardUser)
        return (
            select(other.id)
            .where(other.id != user_id)
            .where(other.is_break_glass.is_(True))
            .where(other.status == DashboardUserStatus.ACTIVE.value)
            .where(other.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN])
            .where(other.totp_secret_encrypted.is_not(None))
            .where(other.password_hash.is_not(None))
            .exists()
        )

    async def update_role_status_guarded(
        self,
        user_id: str,
        *,
        role_id: str,
        status: str,
        is_break_glass: bool | None = None,
        require_other_admin: bool = True,
        require_other_break_glass: bool = False,
    ) -> bool:
        """Write role/status (and the designation) only while the invariants still hold (no commit).

        ``False`` means the conditional UPDATE matched no row: another writer
        removed the last other admin, or the last other qualifying break-glass
        account, between the read and this statement.
        """

        stmt = update(DashboardUser).where(DashboardUser.id == user_id)
        if require_other_admin:
            stmt = stmt.where(self._other_active_admin_exists(user_id))
        if require_other_break_glass:
            stmt = stmt.where(self._other_qualifying_break_glass_exists(user_id))
        values: dict[str, object] = {"role_id": role_id, "status": status}
        if is_break_glass is not None:
            values["is_break_glass"] = is_break_glass
        result = await self._session.execute(
            stmt.values(**values).returning(DashboardUser.id).execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    # --- invites ---

    async def list_live_invites(self, now: datetime) -> Sequence[DashboardUserInvite]:
        stmt = (
            _invite_query()
            .join(DashboardUser, DashboardUser.id == DashboardUserInvite.user_id)
            .where(live_invite_filter(now))
            .where(DashboardUser.status == DashboardUserStatus.INVITED.value)
            .order_by(DashboardUserInvite.created_at.asc(), DashboardUserInvite.id.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get_invite_by_token_hash(self, token_hash: bytes) -> DashboardUserInvite | None:
        stmt = _invite_query().where(DashboardUserInvite.token_hash == token_hash)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_invite_for_user(self, user_id: str) -> DashboardUserInvite | None:
        stmt = _invite_query().where(DashboardUserInvite.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def consume_invite(self, invite_id: str, *, token_hash: bytes, now: datetime) -> bool:
        """Compare-and-set the invite as consumed; ``False`` when it is no longer live (no commit).

        The presented token hash is part of the predicate so a resend that
        rotated the token between lookup and consume fails the accept.
        """

        # The invite row is already in the identity map (loaded with a naive
        # SQLite timestamp), so the ORM must not try to evaluate the aware
        # ``expires_at > now`` criterion in Python; the database decides.
        result = await self._session.execute(
            update(DashboardUserInvite)
            .where(DashboardUserInvite.id == invite_id)
            .where(DashboardUserInvite.token_hash == token_hash)
            .where(live_invite_filter(now))
            .values(consumed_at=now)
            .returning(DashboardUserInvite.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def find_invite_expecting_identity(
        self, provider: str, provider_key: str, subject: str, now: datetime
    ) -> DashboardUserInvite | None:
        """The live invite of an ``invited`` account pre-created for exactly this identity triple."""

        stmt = (
            _invite_query()
            .join(DashboardUser, DashboardUser.id == DashboardUserInvite.user_id)
            .where(DashboardUser.status == DashboardUserStatus.INVITED.value)
            .where(live_invite_filter(now))
            .where(DashboardUserInvite.expected_provider == provider)
            .where(DashboardUserInvite.expected_provider_key == provider_key)
            .where(DashboardUserInvite.expected_subject == subject)
            .order_by(DashboardUserInvite.created_at.asc(), DashboardUserInvite.id.asc())
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def consume_invite_by_identity(self, invite_id: str, *, now: datetime) -> bool:
        """Consume the invite via its expected identity (compare-and-set); ``False`` = no longer live (no commit)."""

        result = await self._session.execute(
            update(DashboardUserInvite)
            .where(DashboardUserInvite.id == invite_id)
            .where(live_invite_filter(now))
            .values(consumed_at=now)
            .returning(DashboardUserInvite.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def live_invite_for_user(self, user_id: str, now: datetime) -> DashboardUserInvite | None:
        """The live invite of a still-``invited`` account, or ``None``."""

        stmt = (
            _invite_query()
            .join(DashboardUser, DashboardUser.id == DashboardUserInvite.user_id)
            .where(DashboardUserInvite.user_id == user_id)
            .where(DashboardUser.status == DashboardUserStatus.INVITED.value)
            .where(live_invite_filter(now))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def conflicting_field(self, username: str, email: str | None) -> str | None:
        """After a unique violation: which of the two candidate values already exists."""

        taken = select(DashboardUser.id).where(DashboardUser.username == username)
        if (await self._session.execute(taken)).first() is not None:
            return "username"
        if email is not None:
            taken = select(DashboardUser.id).where(DashboardUser.email == email)
            if (await self._session.execute(taken)).first() is not None:
                return "email"
        return None

    async def purge_expired_invited_users(self, now: datetime) -> int:
        """Delete ``invited`` accounts whose invite expired (row + invite); returns how many.

        One conditional DELETE evaluates ``status = 'invited'`` and "no live
        invite" in the deleting transaction itself, so an acceptance that
        committed in the meantime (status now ``active``) is never swept up.
        """

        expired_invite = (
            select(DashboardUserInvite.id)
            .where(DashboardUserInvite.user_id == DashboardUser.id)
            .where(DashboardUserInvite.consumed_at.is_(None))
            .where(DashboardUserInvite.sso_only.is_(False))
            .where(DashboardUserInvite.expires_at <= now)
            .exists()
        )
        live_invite = (
            select(DashboardUserInvite.id)
            .where(DashboardUserInvite.user_id == DashboardUser.id)
            .where(live_invite_filter(now))
            .exists()
        )
        deleted = (
            await self._session.execute(
                delete(DashboardUser)
                .where(DashboardUser.status == DashboardUserStatus.INVITED.value)
                .where(expired_invite)
                .where(~live_invite)
                .returning(DashboardUser.id)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        purged = list(deleted.all())
        if not purged:
            await self._session.rollback()
            return 0
        # The FK cascades on both dialects; delete explicitly so no ORM state lingers.
        await self._session.execute(delete(DashboardUserInvite).where(DashboardUserInvite.user_id.in_(purged)))
        await self._session.commit()
        return len(purged)

    async def rotate_invite(self, user_id: str, *, token_hash: bytes, expires_at: datetime) -> bool:
        """Rotate the invite only while the account is still ``invited`` (no commit); ``False`` = refused."""

        still_invited = (
            select(DashboardUser.id)
            .where(DashboardUser.id == DashboardUserInvite.user_id)
            .where(DashboardUser.status == DashboardUserStatus.INVITED.value)
            .exists()
        )
        result = await self._session.execute(
            update(DashboardUserInvite)
            .where(DashboardUserInvite.user_id == user_id)
            .where(still_invited)
            .values(token_hash=token_hash, expires_at=expires_at, consumed_at=None, revoked_at=None)
            .returning(DashboardUserInvite.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    # --- writes ---

    def add(self, *rows: DashboardUser | DashboardUserInvite | DashboardIdentity) -> None:
        self._session.add_all(rows)

    async def commit_user(self, user_id: str, *, bump_generation: bool = False) -> DashboardUser:
        """Commit pending changes, optionally bumping ``session_generation`` atomically
        (``SET x = x + 1`` in the same transaction, never from a stale ORM value),
        and return the freshly loaded account. Any failure rolls the whole write back."""

        try:
            await self._session.flush()
            if bump_generation:
                await self._session.execute(
                    update(DashboardUser)
                    .where(DashboardUser.id == user_id)
                    .values(session_generation=DashboardUser.session_generation + 1)
                )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        self._session.expire_all()
        user = await self.get_by_id(user_id)
        if user is None:  # pragma: no cover - the row was committed a statement ago
            raise RuntimeError(f"dashboard user {user_id} vanished after commit")
        return user

    async def rollback(self) -> None:
        await self._session.rollback()

    async def deactivate_owned_keys(self, user_id: str) -> list[str]:
        """Turn off every active key the account owns, recording why (no commit); returns their hashes."""

        result = await self._session.execute(
            update(ApiKey)
            .where(ApiKey.owner_user_id == user_id)
            .where(ApiKey.is_active.is_(True))
            .values(is_active=False, deactivated_reason=ApiKeyDeactivatedReason.OWNER_DISABLED.value)
            .returning(ApiKey.key_hash)
        )
        return list(result.scalars().all())

    async def reactivate_owner_disabled_keys(self, user_id: str) -> list[str] | None:
        """Restore only the keys the owner cascade turned off; manual blocks stay off.

        The UPDATE is conditional on the owner being active at that moment;
        ``None`` means the owner is not active (nothing was written).
        """

        owner_active = (
            select(DashboardUser.id)
            .where(DashboardUser.id == ApiKey.owner_user_id)
            .where(DashboardUser.status == DashboardUserStatus.ACTIVE.value)
            .exists()
        )
        result = await self._session.execute(
            update(ApiKey)
            .where(ApiKey.owner_user_id == user_id)
            .where(ApiKey.is_active.is_(False))
            .where(ApiKey.deactivated_reason == ApiKeyDeactivatedReason.OWNER_DISABLED.value)
            .where(owner_active)
            .values(is_active=True, deactivated_reason=None)
            .returning(ApiKey.key_hash)
            .execution_options(synchronize_session=False)
        )
        hashes = list(result.scalars().all())
        if not hashes:
            status = (
                await self._session.execute(select(DashboardUser.status).where(DashboardUser.id == user_id))
            ).scalar_one_or_none()
            if status != DashboardUserStatus.ACTIVE.value:
                await self._session.rollback()
                return None
        await self._session.commit()
        return hashes

    async def delete_user(
        self,
        user: DashboardUser,
        *,
        require_other_admin: bool = False,
        require_other_break_glass: bool = False,
        only_while_invited: bool = False,
    ) -> list[str] | None:
        """Delete the account; its keys stay (owner cleared) but are inactive with the owner reason.

        With ``require_other_admin`` the DELETE only applies while another
        active admin exists; with ``only_while_invited`` only while the account
        is still ``invited`` (an acceptance that committed meanwhile survives).
        ``None`` means it was refused and everything (including the key
        cascade) was rolled back.
        """

        try:
            hashes = await self.deactivate_owned_keys(user.id)
            await self._session.execute(
                update(ApiKey).where(ApiKey.owner_user_id == user.id).values(owner_user_id=None)
            )
            await self._session.execute(delete(DashboardUserInvite).where(DashboardUserInvite.user_id == user.id))
            await self._session.execute(delete(DashboardIdentity).where(DashboardIdentity.user_id == user.id))
            stmt = delete(DashboardUser).where(DashboardUser.id == user.id)
            if require_other_admin:
                stmt = stmt.where(self._other_active_admin_exists(user.id))
            if require_other_break_glass:
                stmt = stmt.where(self._other_qualifying_break_glass_exists(user.id))
            if only_while_invited:
                stmt = stmt.where(DashboardUser.status == DashboardUserStatus.INVITED.value)
            deleted = await self._session.execute(
                stmt.returning(DashboardUser.id).execution_options(synchronize_session=False)
            )
            if deleted.scalar_one_or_none() is None:
                await self._session.rollback()
                return None
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        return hashes
