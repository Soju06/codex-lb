"""Persistence for dashboard sign-in: user credentials, guest settings, bootstrap token.

The ``dashboard_users`` row is the sole authority for every credential: a
credential write has exactly one destination and the account's *name* never
decides whether it happens. ``dashboard_settings`` no longer carries a copy of
any credential; the install-wide settings a credential path still writes (the
bootstrap token, the two TOTP requirements) are decided by the operation, not
by who performed it.
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
    COMPAT_ADMIN_USER_ID,
    COMPAT_ADMIN_USERNAME,
    DashboardIdentity,
    DashboardSettings,
    DashboardUser,
    DashboardUserRoleSource,
    DashboardUserStatus,
)
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_users.repository import (
    DashboardUserCounts,
    DashboardUsersRepository,
    LocalAuthState,
    utc_now,
)
from app.modules.role_mappings.repository import RoleMappingsRepository
from app.modules.settings.repository import SettingsRepository

_SETTINGS_ID = 1


def _clear_bootstrap_token(row: DashboardSettings) -> None:
    """A password now exists, so the remote bootstrap token must not.

    The token grants first-run admin access and is inert the moment any account
    holds a password, so every password write clears it -- whichever account
    wrote it and whatever that account is called.
    """

    row.bootstrap_token_encrypted = None
    row.bootstrap_token_hash = None


def _clear_totp_required_on_login(row: DashboardSettings) -> None:
    row.totp_required_on_login = False


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

    async def _mutate_settings_with_retry(self, mutate: Callable[[DashboardSettings], None]) -> DashboardSettings:
        """Apply a single-purpose settings mutation, retrying once on a version conflict.

        These mutations are idempotent absolute writes (set/clear a field), so
        losing the optimistic version race to a concurrent settings update is
        benign: re-read the fresh row, re-apply the same mutation, and commit
        again instead of surfacing a 500.
        """
        row = await self._settings_repository.get_or_create()
        mutate(row)
        try:
            await self._settings_repository.commit_refresh(row)
        except DashboardSettingsConflictError:
            row = await self._settings_repository.get_or_create()
            await self._session.refresh(row)
            mutate(row)
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

    # --- users: writes ---

    async def create_first_admin(self, password_hash: str) -> DashboardUser | None:
        """First-run setup: give the install its bootstrap account.

        Refused (``None``) when an active user already holds a password. The
        users table is the only authority and the write is compare-and-set: a
        missing row is inserted (deterministic id + unique username make a
        concurrent insert fail), an existing row is re-armed only while it is
        not an *active password holder*; zero rows means another setup won the
        race and this one is refused.

        "Not an active password holder" is the whole condition because that is
        the same fact the gate above tests: anything else the bootstrap row may
        have been edited into -- disabled, demoted, credential-less -- is a
        leftover the install is entitled to bootstrap over, and the write puts
        the row back into the state setup promises (active, admin preset,
        manually sourced). A narrower ``password_hash IS NULL`` would leave a
        *disabled* row holding its old hash unmatched, so the install could
        neither sign in nor ever set a password again.

        The row that may be re-armed is found by its deterministic id and its
        break-glass designation, never by its name: the account is renameable,
        and a lookup by ``admin`` would miss a renamed row, collide on the id
        and refuse every re-bootstrap of that install forever. The bootstrap
        token is cleared in the same transaction, so the token that was meant
        to create this credential cannot outlive it.
        """

        await self._settings_repository.get_or_create()
        user_id: str | None = None
        for attempt in range(2):
            # Identity-only accounts (reverse-proxy users) do not count: the
            # local break-glass password login remains creatable.
            if (await self._users.local_auth_state()).active_local_password_users > 0:
                return None
            existing = (
                await self._session.execute(select(DashboardUser).where(DashboardUser.id == COMPAT_ADMIN_USER_ID))
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
                    # A row already holds that id or the reserved name.
                    await self._session.rollback()
                    return None
                user_id = COMPAT_ADMIN_USER_ID
            elif not existing.is_break_glass:
                # Only the bootstrapped break-glass row may be re-armed.
                return None
            else:
                armed = await self._session.execute(
                    update(DashboardUser)
                    .where(DashboardUser.id == existing.id)
                    # Re-read the designation inside the statement, so a
                    # concurrent write that cleared it cannot slip between the
                    # check above and this UPDATE.
                    .where(DashboardUser.is_break_glass.is_(True))
                    .where(
                        or_(
                            DashboardUser.password_hash.is_(None),
                            DashboardUser.status != DashboardUserStatus.ACTIVE.value,
                        )
                    )
                    .values(
                        password_hash=password_hash,
                        totp_secret_encrypted=None,
                        totp_last_verified_step=None,
                        # The account setup hands back is the install's way in,
                        # so the same statement undoes whatever an administrator
                        # edited it into before it stopped being able to sign in.
                        status=DashboardUserStatus.ACTIVE.value,
                        role_id=PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
                        role_source=DashboardUserRoleSource.MANUAL.value,
                    )
                    .returning(DashboardUser.id)
                )
                if armed.scalar_one_or_none() is None:
                    await self._session.rollback()
                    return None
                user_id = existing.id
            row = await self._settings_repository.get_or_create()
            _clear_bootstrap_token(row)
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
        *,
        mutate_settings: Callable[[DashboardSettings], None] | None = None,
        before: Callable[[], Awaitable[None]] | None = None,
        bump_generation: bool = False,
    ) -> DashboardUser:
        """Apply a credential mutation, and any install-wide settings it implies, in one transaction.

        ``mutate_settings`` never carries a credential: it is the install-wide
        consequence of the operation (the bootstrap token, the TOTP
        requirements), and it is decided by the caller, never by the account's
        name. The settings row carries an optimistic version; when the commit
        loses that race the whole write rolls back and is re-applied (including
        ``before``) so the account row and the settings row cannot diverge.
        ``bump_generation`` increments ``session_generation`` with an atomic
        ``SET x = x + 1`` in the same transaction, never from the possibly
        stale ORM value, so two concurrent revocations can never resurrect an
        already revoked cookie. Any other failure rolls the whole write back
        before propagating.
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
            if mutate_settings is not None:
                row = await self._settings_repository.get_or_create()
                mutate_settings(row)
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

    async def rotate_user_password(self, user_id: str, password_hash: str) -> DashboardUser:
        """Set a new password and revoke every existing session in one transaction."""

        def _user(user: DashboardUser) -> None:
            user.password_hash = password_hash

        return await self._write_user(user_id, _user, mutate_settings=_clear_bootstrap_token, bump_generation=True)

    async def set_user_totp_secret(
        self,
        user_id: str,
        secret_encrypted: bytes | None,
        *,
        bump_generation: bool = False,
        preserve_policy: bool = False,
    ) -> DashboardUser:
        """Set or clear the TOTP secret; an administrative reset also revokes every session.

        Clearing the secret turns the install-wide ``totp_required_on_login``
        off only on a one-account install, where "I turned two-factor off" and
        "this install no longer requires two-factor" are the same statement.
        ``/totp/disable`` carries no ``security:write``, so on any larger
        install the requirement is left alone and the account meets the
        enrolment gate on its next request. An administrative reset passes
        ``preserve_policy`` and never touches either requirement.

        "One-account install" counts every account the install holds, not only
        the active ones: a disabled colleague is an account that can be enabled
        again, and letting a self-service route turn an install-wide security
        requirement off because the other accounts happen to be disabled today
        is the same statement made about somebody else's sign-in.
        """

        def _user(user: DashboardUser) -> None:
            user.totp_secret_encrypted = secret_encrypted
            user.totp_last_verified_step = None

        mutate_settings: Callable[[DashboardSettings], None] | None = None
        if secret_encrypted is None and not preserve_policy:
            if (await self._users.counts()).total <= 1:
                mutate_settings = _clear_totp_required_on_login

        return await self._write_user(user_id, _user, mutate_settings=mutate_settings, bump_generation=bump_generation)

    async def try_advance_user_totp_step(self, user_id: str, step: int) -> bool:
        """Advance the replay counter; ``False`` means the code was already used.

        The conditional UPDATE is what decides: a code whose step the account
        row already reached changes zero rows, and the refusal rolls the
        transaction back so nothing half-written survives. Callers depend on
        that boundary -- ``disable_totp`` spends the code (and commits) before
        the break-glass guard takes its write-intent lock.
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
            .returning(DashboardUser.id)
        )
        if result.scalar_one_or_none() is None:
            await self._session.rollback()
            return False
        await self._session.commit()
        return True

    async def bump_session_generation(self, user_id: str) -> int:
        user = await self._write_user(user_id, lambda _user: None, bump_generation=True)
        return user.session_generation

    async def clear_user_credentials(self, user_id: str) -> DashboardUser:
        """Password removal on a solo install: drop every credential but keep the account row."""

        def _user(user: DashboardUser) -> None:
            user.password_hash = None
            user.totp_secret_encrypted = None
            user.totp_last_verified_step = None

        def _settings(row: DashboardSettings) -> None:
            # The route is already restricted to a one-account install, so the
            # install is passwordless after this write: a requirement left on
            # would make sign-in mandatory with no account able to present a
            # factor. Neither clause asks what the account is called.
            _clear_bootstrap_token(row)
            row.totp_required_on_login = False
            row.totp_required_for_admin_role = False

        async def _delete_identities() -> None:
            await self._session.execute(delete(DashboardIdentity).where(DashboardIdentity.user_id == user_id))

        return await self._write_user(
            user_id, _user, mutate_settings=_settings, before=_delete_identities, bump_generation=True
        )

    async def touch_last_login(self, user_id: str) -> None:
        await self._session.execute(
            update(DashboardUser).where(DashboardUser.id == user_id).values(last_login_at=utcnow())
        )
        await self._session.commit()
