"""Persistence for dashboard sign-in: user credentials, guest settings, bootstrap token.

The ``dashboard_users`` row is the source of truth for every credential. During
the expand/contract release the legacy ``dashboard_settings`` credential
columns are kept as a *write-only projection* of the ``admin`` (compat) user so
replicas still running the previous release keep working: every write to the
compat user's credential is mirrored onto the legacy columns in the same
transaction, and nothing here reads the legacy credential columns back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.exceptions import DashboardSettingsConflictError
from app.core.utils.time import utcnow
from app.db.models import (
    COMPAT_ADMIN_USERNAME,
    DashboardIdentity,
    DashboardSettings,
    DashboardUser,
    DashboardUserRoleSource,
    DashboardUserStatus,
)
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_users.compat import COMPAT_ADMIN_USER_ID
from app.modules.dashboard_users.repository import (
    DashboardUserCounts,
    DashboardUsersRepository,
    LocalAuthState,
    utc_now,
)
from app.modules.role_mappings.repository import RoleMappingsRepository
from app.modules.settings.repository import SettingsRepository

_SETTINGS_ID = 1


class DashboardAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings_repository = SettingsRepository(session)
        self._users = DashboardUsersRepository(session)
        self._roles = DashboardRolesRepository(session)
        self._mappings = RoleMappingsRepository(session)

    # --- settings (guest access, policy flags, bootstrap token) ---

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

    async def store_bootstrap_token_if_absent(self, token_encrypted: bytes, token_hash: bytes) -> bool:
        await self._settings_repository.get_or_create()
        result = await self._session.execute(
            update(DashboardSettings)
            .where(DashboardSettings.id == _SETTINGS_ID)
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

    # --- users: reads ---

    async def get_local_auth_state(self) -> LocalAuthState:
        return await self._users.local_auth_state()

    async def get_user_by_id(self, user_id: str) -> DashboardUser | None:
        return await self._users.get_by_id(user_id)

    async def get_user_by_username(self, normalized_username: str) -> DashboardUser | None:
        return await self._users.get_by_username(normalized_username)

    async def list_active_local_password_users(self) -> Sequence[DashboardUser]:
        return await self._users.list_active_local_password_users()

    async def count_active_users(self) -> int:
        return (await self._users.counts()).active

    async def count_user_identities(self, user_id: str) -> int:
        return await self._users.count_identities(user_id)

    async def count_live_invites(self) -> int:
        return len(await self._users.list_live_invites(utc_now()))

    async def acquire_write_intent(self) -> None:
        await self._users.acquire_write_intent()

    async def get_user_counts(self) -> DashboardUserCounts:
        return await self._users.counts()

    async def count_qualifying_break_glass(self, *, exclude_user_id: str | None = None) -> int:
        return await self._users.count_qualifying_break_glass(exclude_user_id=exclude_user_id)

    async def count_custom_roles(self) -> int:
        return await self._roles.count_custom_roles()

    async def count_role_mappings(self) -> int:
        return await self._mappings.count_mappings()

    # --- users: writes (compat admin mirrored to the legacy columns) ---

    async def create_first_admin(self, password_hash: str) -> DashboardUser | None:
        """First-run setup: give the install its ``admin`` account.

        Refused (``None``) when an active user already holds a password. The users
        table is the only authority and the write is compare-and-set: a missing
        row is inserted (deterministic id + unique username make a concurrent
        insert fail), an existing credential-less row is re-armed with
        ``UPDATE ... WHERE password_hash IS NULL``; zero rows means another
        setup won the race and this one is refused. Only then are the legacy
        columns mirrored and the bootstrap token cleared, in the same
        transaction, so a stale legacy hash left behind by a previous-release
        replica can never wedge setup.
        """

        await self._settings_repository.get_or_create()
        user_id: str | None = None
        for attempt in range(2):
            # Identity-only accounts (reverse-proxy users) do not count: the
            # local ``admin`` remains creatable as the break-glass password login.
            if (await self._users.local_auth_state()).active_local_password_users > 0:
                return None
            existing = (
                await self._session.execute(
                    select(DashboardUser).where(DashboardUser.username == COMPAT_ADMIN_USERNAME)
                )
            ).scalar_one_or_none()
            if existing is None:
                self._session.add(
                    DashboardUser(
                        id=COMPAT_ADMIN_USER_ID,
                        username=COMPAT_ADMIN_USERNAME,
                        role_id=PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
                        role_source=DashboardUserRoleSource.MANUAL.value,
                        status=DashboardUserStatus.ACTIVE.value,
                        password_hash=password_hash,
                        is_break_glass=True,
                    )
                )
                try:
                    await self._session.flush()
                except IntegrityError:
                    await self._session.rollback()
                    return None
                user_id = COMPAT_ADMIN_USER_ID
            elif existing.id != COMPAT_ADMIN_USER_ID or not existing.is_break_glass:
                # Only the migrated/bootstrapped break-glass row may be re-armed.
                return None
            else:
                armed = await self._session.execute(
                    update(DashboardUser)
                    .where(DashboardUser.id == existing.id)
                    .where(DashboardUser.password_hash.is_(None))
                    .values(password_hash=password_hash, totp_secret_encrypted=None, totp_last_verified_step=None)
                    .returning(DashboardUser.id)
                )
                if armed.scalar_one_or_none() is None:
                    await self._session.rollback()
                    return None
                user_id = existing.id
            row = await self._settings_repository.get_or_create()
            row.password_hash = password_hash
            row.bootstrap_token_encrypted = None
            row.bootstrap_token_hash = None
            try:
                await self._settings_repository.commit_refresh(row)
                break
            except DashboardSettingsConflictError:
                # The conflict rollback discarded the user write too; re-run both.
                if attempt == 1:
                    raise
        assert user_id is not None
        # Reload with the role eagerly attached; expire first so the re-armed
        # identity-mapped row cannot serve its pre-update attributes.
        self._session.expire_all()
        created = await self._users.get_by_id(user_id)
        if created is None:  # pragma: no cover - the row was committed a statement ago
            raise RuntimeError("dashboard admin user vanished after commit")
        return created

    async def _load_user(self, user_id: str) -> DashboardUser:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise LookupError(f"dashboard user {user_id} does not exist")
        return user

    async def _write_user(
        self,
        user_id: str,
        mutate_user: Callable[[DashboardUser], None],
        mirror_legacy: Callable[[DashboardSettings], None] | None,
        *,
        before: Callable[[], Awaitable[None]] | None = None,
        bump_generation: bool = False,
    ) -> DashboardUser:
        """Apply a user mutation and, for the compat admin, the legacy mirror in one transaction.

        The legacy row carries an optimistic version; when the commit loses
        that race both writes roll back and are re-applied together (including
        ``before``) so the two rows can never diverge. ``bump_generation``
        increments ``session_generation`` with an atomic ``SET x = x + 1`` in
        the same transaction, never from the possibly stale ORM value, so two
        concurrent revocations can never resurrect an already revoked cookie.
        Any other failure rolls the whole write back before propagating.
        """

        async def _apply() -> DashboardUser:
            if before is not None:
                await before()
            user = await self._load_user(user_id)
            mutate_user(user)
            if bump_generation:
                await self._session.flush()
                await self._session.execute(
                    update(DashboardUser)
                    .where(DashboardUser.id == user_id)
                    .values(session_generation=DashboardUser.session_generation + 1)
                    .returning(DashboardUser.session_generation)
                )
            if mirror_legacy is not None and user.username == COMPAT_ADMIN_USERNAME:
                row = await self._settings_repository.get_or_create()
                mirror_legacy(row)
                await self._settings_repository.commit_refresh(row)
            else:
                await self._session.commit()
            await self._session.refresh(user)
            return user

        try:
            try:
                return await _apply()
            except DashboardSettingsConflictError:
                return await _apply()
        except Exception:
            await self._session.rollback()
            raise

    async def set_user_password_hash(self, user_id: str, password_hash: str) -> DashboardUser:
        def _user(user: DashboardUser) -> None:
            user.password_hash = password_hash

        def _legacy(row: DashboardSettings) -> None:
            row.password_hash = password_hash
            row.bootstrap_token_encrypted = None
            row.bootstrap_token_hash = None

        return await self._write_user(user_id, _user, _legacy)

    async def rotate_user_password(self, user_id: str, password_hash: str) -> DashboardUser:
        """Set a new password and revoke every existing session in one transaction."""

        def _user(user: DashboardUser) -> None:
            user.password_hash = password_hash

        def _legacy(row: DashboardSettings) -> None:
            row.password_hash = password_hash
            row.bootstrap_token_encrypted = None
            row.bootstrap_token_hash = None

        return await self._write_user(user_id, _user, _legacy, bump_generation=True)

    async def set_user_totp_secret(
        self,
        user_id: str,
        secret_encrypted: bytes | None,
        *,
        bump_generation: bool = False,
        preserve_policy: bool = False,
    ) -> DashboardUser:
        """Set or clear the TOTP secret; an administrative reset also revokes every session.

        Self-service disable on the compat admin also turns the install-wide
        ``totp_required_on_login`` off in the legacy mirror (today's behaviour);
        an administrative reset passes ``preserve_policy`` so the mirror only
        clears the secret and counter and the policy stays as configured.
        """

        def _user(user: DashboardUser) -> None:
            user.totp_secret_encrypted = secret_encrypted
            user.totp_last_verified_step = None

        def _legacy(row: DashboardSettings) -> None:
            row.totp_secret_encrypted = secret_encrypted
            row.totp_last_verified_step = None
            if secret_encrypted is None and not preserve_policy:
                row.totp_required_on_login = False

        return await self._write_user(user_id, _user, _legacy, bump_generation=bump_generation)

    async def try_advance_user_totp_step(self, user_id: str, step: int) -> bool:
        """Advance the replay counter; ``False`` means the code was already used.

        For the compat admin the legacy column must advance too (both or
        neither): a code consumed by a previous-release replica is a replay
        here, and vice versa.
        """

        result = await self._session.execute(
            update(DashboardUser)
            .where(DashboardUser.id == user_id)
            .where(
                or_(
                    DashboardUser.totp_last_verified_step.is_(None),
                    DashboardUser.totp_last_verified_step < step,
                )
            )
            .values(totp_last_verified_step=step)
            .returning(DashboardUser.username)
        )
        username = result.scalar_one_or_none()
        if username is None:
            await self._session.rollback()
            return False
        if username == COMPAT_ADMIN_USERNAME:
            await self._settings_repository.get_or_create()
            mirrored = await self._session.execute(
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
            if mirrored.scalar_one_or_none() is None:
                await self._session.rollback()
                return False
        await self._session.commit()
        return True

    async def bump_session_generation(self, user_id: str) -> int:
        user = await self._write_user(user_id, lambda _user: None, None, bump_generation=True)
        return user.session_generation

    async def clear_user_credentials(self, user_id: str) -> DashboardUser:
        """Password removal on a solo install: drop every credential but keep the account row."""

        def _user(user: DashboardUser) -> None:
            user.password_hash = None
            user.totp_secret_encrypted = None
            user.totp_last_verified_step = None

        def _legacy(row: DashboardSettings) -> None:
            row.password_hash = None
            row.bootstrap_token_encrypted = None
            row.bootstrap_token_hash = None
            row.totp_required_on_login = False
            row.totp_required_for_admin_role = False
            row.totp_secret_encrypted = None
            row.totp_last_verified_step = None

        async def _delete_identities() -> None:
            await self._session.execute(delete(DashboardIdentity).where(DashboardIdentity.user_id == user_id))

        return await self._write_user(user_id, _user, _legacy, before=_delete_identities, bump_generation=True)

    async def touch_last_login(self, user_id: str) -> None:
        await self._session.execute(
            update(DashboardUser).where(DashboardUser.id == user_id).values(last_login_at=utcnow())
        )
        await self._session.commit()
