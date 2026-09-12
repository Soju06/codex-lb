from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import bcrypt
import pytest

from app.core.audit.service import AuditActor, AuditSeverity, AuditTarget
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, DashboardPermission, DashboardRole, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.providers import DEFAULT_PROVIDER_KEY, PasswordProvider
from app.core.auth.providers.registry import ActiveProvider
from app.db.models import (
    COMPAT_ADMIN_USERNAME,
    AuthProviderKind,
    DashboardAuthProvider,
    DashboardRoleRecord,
    DashboardUser,
)
from app.modules.dashboard_auth.service import (
    DashboardAuthService,
    DashboardSessionStore,
    InvalidCredentialsError,
    OtherUsersExistError,
    PasswordAlreadyConfiguredError,
    PasswordNotConfiguredError,
    UsernameRequiredError,
)
from app.modules.dashboard_users.break_glass import user_qualifies
from app.modules.dashboard_users.repository import DashboardUserCounts, LocalAuthState

pytestmark = pytest.mark.unit


@dataclass(slots=True, frozen=True)
class _AuditCall:
    action: str
    details: dict[str, Any]
    actor: AuditActor | None
    target: AuditTarget | None
    severity: AuditSeverity


@pytest.fixture(autouse=True)
def audit_events(monkeypatch: pytest.MonkeyPatch) -> list[_AuditCall]:
    """Record audit calls instead of scheduling background tasks that would outlive the test loop."""

    import app.modules.dashboard_auth.service as service_module

    events: list[_AuditCall] = []

    def _record(
        action: str,
        actor_ip: str | None = None,
        details: Any = None,
        request_id: str | None = None,
        *,
        actor: AuditActor | None = None,
        target: AuditTarget | None = None,
        severity: AuditSeverity = AuditSeverity.INFO,
    ) -> None:
        events.append(_AuditCall(action, dict(details or {}), actor, target, severity))

    monkeypatch.setattr(service_module.AuditService, "log_async", staticmethod(_record))
    return events


def _user_actor(user: DashboardUser, auth_method: str) -> AuditActor:
    return AuditActor(user_id=user.id, username=user.username, role_slug=user.role.slug, auth_method=auth_method)


def _calls(events: list[_AuditCall], action: str) -> list[_AuditCall]:
    return [call for call in events if call.action == action]


@dataclass(slots=True)
class _FakeSettings:
    guest_access_enabled: bool = False
    guest_password_hash: str | None = None
    guest_session_generation: int = 0
    dashboard_auth_mode: DashboardAuthMode = DashboardAuthMode.STANDARD
    totp_required_on_login: bool = False
    totp_required_for_admin_role: bool = False
    local_login_policy: str = "enabled"


def _role(slug: PresetRoleSlug) -> DashboardRoleRecord:
    return DashboardRoleRecord(id=PRESET_ROLE_IDS[slug], slug=slug.value, name=slug.value.title(), kind="preset")


def _make_user(
    username: str,
    *,
    slug: PresetRoleSlug = PresetRoleSlug.ADMIN,
    password_hash: str | None = "hash",
    status: str = "active",
) -> DashboardUser:
    role = _role(slug)
    user = DashboardUser(
        id=str(uuid.uuid4()),
        username=username,
        role_id=role.id,
        status=status,
        password_hash=password_hash,
        session_generation=0,
        must_change_password=False,
        is_break_glass=False,
    )
    user.role = role
    return user


class _FakeRepository:
    """In-memory stand-in for ``DashboardAuthRepository`` (users are the truth, no legacy mirror)."""

    def __init__(self) -> None:
        self.settings = _FakeSettings()
        self.users: dict[str, DashboardUser] = {}
        self.identities: dict[str, int] = {}
        self.last_login: list[str] = []

    def add(self, user: DashboardUser) -> DashboardUser:
        self.users[user.id] = user
        return user

    def _active_password_users(self) -> list[DashboardUser]:
        return [u for u in self.users.values() if u.status == "active" and u.password_hash is not None]

    async def get_settings(self) -> _FakeSettings:
        return self.settings

    async def get_local_auth_state(self) -> LocalAuthState:
        password_users = self._active_password_users()
        active = [u for u in self.users.values() if u.status == "active"]
        return LocalAuthState(
            any_user=bool(self.users),
            active_users=len(active),
            active_local_password_users=len(password_users),
            requires_auth=bool(password_users) or any(self.identities.get(u.id, 0) for u in active),
            sole_local_password_user_id=password_users[0].id if len(password_users) == 1 else None,
        )

    async def get_user_by_id(self, user_id: str) -> DashboardUser | None:
        return self.users.get(user_id)

    async def get_user_by_username(self, normalized_username: str) -> DashboardUser | None:
        return next((u for u in self.users.values() if u.username == normalized_username), None)

    async def list_active_local_password_users(self) -> Sequence[DashboardUser]:
        return self._active_password_users()

    async def count_active_users(self) -> int:
        return sum(1 for u in self.users.values() if u.status == "active")

    async def count_user_identities(self, user_id: str) -> int:
        return self.identities.get(user_id, 0)

    async def count_live_invites(self) -> int:
        return 0

    async def acquire_write_intent(self) -> None:
        return None

    async def get_user_counts(self) -> DashboardUserCounts:
        users = list(self.users.values())
        return DashboardUserCounts(
            total=len(users),
            active=sum(1 for u in users if u.status == "active"),
            invited=sum(1 for u in users if u.status == "invited"),
            disabled=sum(1 for u in users if u.status == "disabled"),
            non_admin=sum(1 for u in users if u.role_id != PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]),
            pending_invites=0,
        )

    async def count_qualifying_break_glass(self, *, exclude_user_id: str | None = None) -> int:
        # The production predicate itself, not a hand-copied one. The repository's
        # SQL filter is kept in step with ``qualifies``; a second transcription
        # here is exactly how a fake drifts, and it already had: the password
        # term went missing, so a credential-less proxy admin counted as a way
        # back in and a removal production refuses would have passed here.
        return sum(1 for u in self.users.values() if u.id != exclude_user_id and user_qualifies(u))

    async def count_custom_roles(self) -> int:
        return 0

    async def count_role_mappings(self) -> int:
        return 0

    async def create_first_admin(self, password_hash: str) -> DashboardUser | None:
        if (await self.get_local_auth_state()).requires_auth:
            return None
        existing = await self.get_user_by_username(COMPAT_ADMIN_USERNAME)
        if existing is not None:
            existing.password_hash = password_hash
            return existing
        user = _make_user(COMPAT_ADMIN_USERNAME, password_hash=password_hash)
        user.is_break_glass = True
        return self.add(user)

    async def set_user_password_hash(self, user_id: str, password_hash: str) -> DashboardUser:
        self.users[user_id].password_hash = password_hash
        return self.users[user_id]

    async def rotate_user_password(self, user_id: str, password_hash: str) -> DashboardUser:
        user = self.users[user_id]
        user.password_hash = password_hash
        user.session_generation += 1
        return user

    async def set_user_totp_secret(
        self,
        user_id: str,
        secret_encrypted: bytes | None,
        *,
        bump_generation: bool = False,
        preserve_policy: bool = False,
    ) -> DashboardUser:
        user = self.users[user_id]
        user.totp_secret_encrypted = secret_encrypted
        user.totp_last_verified_step = None
        if secret_encrypted is None:
            self.settings.totp_required_on_login = False
        return user

    async def try_advance_user_totp_step(self, user_id: str, step: int) -> bool:
        user = self.users[user_id]
        if user.totp_last_verified_step is not None and user.totp_last_verified_step >= step:
            return False
        user.totp_last_verified_step = step
        return True

    async def bump_session_generation(self, user_id: str) -> int:
        self.users[user_id].session_generation += 1
        return self.users[user_id].session_generation

    async def clear_user_credentials(self, user_id: str) -> DashboardUser:
        user = self.users[user_id]
        user.password_hash = None
        user.totp_secret_encrypted = None
        user.totp_last_verified_step = None
        user.session_generation += 1
        self.identities.pop(user_id, None)
        self.settings.totp_required_on_login = False
        return user

    async def touch_last_login(self, user_id: str) -> None:
        self.last_login.append(user_id)

    async def set_guest_password_hash(self, password_hash: str) -> _FakeSettings:
        self.settings.guest_password_hash = password_hash
        self.settings.guest_session_generation += 1
        return self.settings

    async def clear_guest_password_hash(self) -> _FakeSettings:
        self.settings.guest_password_hash = None
        self.settings.guest_session_generation += 1
        return self.settings

    async def bump_guest_session_generation(self) -> _FakeSettings:
        self.settings.guest_session_generation += 1
        return self.settings


async def _password_only_providers() -> list[ActiveProvider]:
    """The provider list these tests run against.

    The real one is a process cache over ``dashboard_auth_providers``; unit
    tests have no schema, so the service takes the list by injection.
    """

    row = DashboardAuthProvider(
        id="provider-password",
        kind=AuthProviderKind.PASSWORD.value,
        provider_key=DEFAULT_PROVIDER_KEY,
        enabled=True,
        label="Password",
    )
    return [ActiveProvider(row=row, provider=PasswordProvider())]


def _service(repository: _FakeRepository, store: DashboardSessionStore | None = None) -> DashboardAuthService:
    return DashboardAuthService(
        repository,
        store or DashboardSessionStore(),
        auth_state_provider=repository.get_local_auth_state,
        active_providers_provider=_password_only_providers,
    )


def _session_for(
    store: DashboardSessionStore, user: DashboardUser, *, totp_verified: bool = False, ttl: int = 3600
) -> str:
    return store.create_user_session(
        user.id, user.session_generation, password_verified=True, totp_verified=totp_verified, ttl_seconds=ttl
    )


@pytest.mark.asyncio
async def test_setup_password_creates_admin_and_rejects_duplicate() -> None:
    repository = _FakeRepository()
    service = _service(repository)

    user = await service.setup_password("password123")
    assert user.username == "admin"
    stored_hash = user.password_hash
    assert stored_hash is not None and stored_hash != "password123"
    assert bcrypt.checkpw(b"password123", stored_hash.encode("utf-8")) is True

    with pytest.raises(PasswordAlreadyConfiguredError):
        await service.setup_password("another-password")


@pytest.mark.asyncio
async def test_setup_password_raises_when_atomic_set_fails() -> None:
    repository = _FakeRepository()
    repository.add(_make_user("admin", password_hash="already-set-by-race"))
    service = _service(repository)

    with pytest.raises(PasswordAlreadyConfiguredError):
        await service.setup_password("password123")


@pytest.mark.asyncio
async def test_verify_and_change_password_bumps_generation() -> None:
    repository = _FakeRepository()
    service = _service(repository)
    user = await service.setup_password("password123")

    await service.verify_password("password123")
    with pytest.raises(InvalidCredentialsError):
        await service.verify_password("wrong-password")

    generation = await service.change_password(user, "password123", "new-password-456")
    assert generation == 1
    assert user.session_generation == 1
    await service.verify_password("new-password-456")
    with pytest.raises(InvalidCredentialsError):
        await service.verify_password("password123")
    with pytest.raises(InvalidCredentialsError):
        await service.change_password(user, "password123", "irrelevant-789")


@pytest.mark.asyncio
async def test_login_target_resolution_single_and_multi_user() -> None:
    repository = _FakeRepository()
    service = _service(repository)
    with pytest.raises(PasswordNotConfiguredError):
        await service.resolve_login_target(None)

    admin = await service.setup_password("password123")
    assert (await service.resolve_login_target(None)).user is admin
    assert (await service.resolve_login_target("  ADMIN ")).user is admin

    unknown = await service.resolve_login_target("nobody")
    assert unknown.user is None and unknown.username == "nobody"
    with pytest.raises(InvalidCredentialsError):
        await service.verify_user_password(unknown, "password123")
    bad_format = await service.resolve_login_target("has space")
    assert bad_format.user is None

    repository.add(_make_user("ops", slug=PresetRoleSlug.OPERATOR, password_hash="x"))
    with pytest.raises(UsernameRequiredError):
        await service.resolve_login_target(None)
    assert (await service.resolve_login_target("ops")).user is not None

    repository.add(_make_user("ghost", password_hash="x", status="disabled"))
    assert (await service.resolve_login_target("ghost")).user is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unknown_username", "inactive_account", "identity_only"])
async def test_failed_logins_always_cost_exactly_one_password_check(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    """No early return before bcrypt: unknown, inactive and password-less accounts take the same path."""

    import app.modules.dashboard_auth.service as service_module

    repository = _FakeRepository()
    service = _service(repository)
    await service.setup_password("password123")
    repository.add(_make_user("ghost", password_hash="x", status="disabled"))
    repository.add(_make_user("sso-only", password_hash=None))
    # An account that signs in through a provider only; the fixture above gives it no local credential.
    known = {"unknown_username": "nobody", "inactive_account": "ghost"}
    username = known.get(case, "sso-only")

    checks: list[str] = []

    def counting_check(password: str, password_hash: str) -> bool:
        checks.append(password_hash)
        return False

    monkeypatch.setattr(service_module, "_check_password", counting_check)
    target = await service.resolve_login_target(username)
    with pytest.raises(InvalidCredentialsError):
        await service.verify_user_password(target, "password123")

    assert len(checks) == 1
    assert checks[0] == service_module._dummy_password_hash()


@pytest.mark.asyncio
async def test_failed_login_audit_keeps_only_well_formed_usernames(audit_events: list[_AuditCall]) -> None:
    repository = _FakeRepository()
    service = _service(repository)
    await service.setup_password("password123")

    for username in ("ad\x01min", "has space", "x" * 65):
        with pytest.raises(InvalidCredentialsError):
            await service.verify_user_password(await service.resolve_login_target(username), "wrong")
    with pytest.raises(InvalidCredentialsError):
        await service.verify_user_password(await service.resolve_login_target("nobody"), "wrong")

    failures = _calls(audit_events, "login_failed")
    assert len(failures) == 4
    assert [call.details["username"] for call in failures] == [None, None, None, "nobody"]
    assert [call.details["reason"] for call in failures] == ["invalid_username"] * 3 + ["unknown_identity"]
    assert all(call.actor is None and call.severity is AuditSeverity.WARNING for call in failures)


@pytest.mark.asyncio
async def test_self_service_audit_names_the_account_and_session_method(audit_events: list[_AuditCall]) -> None:
    repository = _FakeRepository()
    service = _service(repository)
    admin = await service.setup_password("password123")
    expected_target = AuditTarget("user", admin.id)

    await service.verify_password("password123")
    (signed_in,) = _calls(audit_events, "login_success")
    assert signed_in.actor == _user_actor(admin, "password")
    assert signed_in.target == expected_target

    await service.change_password(admin, "password123", "new-password-456", auth_method="password")
    (changed,) = _calls(audit_events, "password_changed")
    assert changed.actor == _user_actor(admin, "password")
    assert changed.target == expected_target
    assert changed.severity is AuditSeverity.INFO

    # The session's own method is threaded through; the default is the password method.
    await service.revoke_user_sessions(admin, auth_method="trusted_header")
    await service.revoke_user_sessions(admin)
    threaded, defaulted = _calls(audit_events, "user_sessions_revoked")
    assert threaded.actor == _user_actor(admin, "trusted_header")
    assert defaulted.actor == _user_actor(admin, "password")
    assert threaded.target == defaulted.target == expected_target

    await service.remove_password(admin, "new-password-456", auth_method="password")
    (removed,) = _calls(audit_events, "password_removed")
    assert removed.actor == _user_actor(admin, "password")
    assert removed.target == expected_target
    assert removed.severity is AuditSeverity.INFO


@pytest.mark.asyncio
async def test_totp_audit_attribution(monkeypatch: pytest.MonkeyPatch, audit_events: list[_AuditCall]) -> None:
    import pyotp

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as service_module
    from app.modules.dashboard_auth.service import TotpInvalidCodeError

    current = {"value": 1_700_000_000}
    monkeypatch.setattr(service_module, "time", lambda: current["value"])
    monkeypatch.setattr(totp_module, "time", lambda: current["value"])

    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = _service(repository, store)
    admin = await service.setup_password("password123")
    expected_target = AuditTarget("user", admin.id)
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)

    await service.confirm_totp_setup(
        session_id=_session_for(store, admin), secret=secret, code=totp.at(current["value"])
    )
    (enabled,) = _calls(audit_events, "totp_enabled")
    assert enabled.actor == _user_actor(admin, "password")  # the password session's method
    assert enabled.target == expected_target

    code = totp.at(current["value"])
    verified, _ = await service.verify_totp(session_id=_session_for(store, admin), code=code, ttl_seconds=3600)
    (signed_in,) = _calls(audit_events, "login_success")
    assert signed_in.actor == _user_actor(admin, "totp")
    assert signed_in.target == expected_target
    assert signed_in.details == {"method": "totp", "username": admin.username}

    with pytest.raises(TotpInvalidCodeError):
        await service.verify_totp(session_id=_session_for(store, admin), code=code, ttl_seconds=3600)
    (replayed,) = _calls(audit_events, "login_failed")
    assert replayed.actor is None
    assert replayed.target is None
    assert replayed.severity is AuditSeverity.WARNING
    assert replayed.details == {"method": "totp", "username": admin.username, "reason": "bad_totp"}

    current["value"] += 60
    await service.disable_totp(session_id=verified, code=totp.at(current["value"]))
    (disabled,) = _calls(audit_events, "totp_disabled")
    assert disabled.actor == _user_actor(admin, "password")
    assert disabled.target == expected_target


@pytest.mark.asyncio
async def test_verify_user_password_records_last_login() -> None:
    repository = _FakeRepository()
    service = _service(repository)
    admin = await service.setup_password("password123")

    verified = await service.verify_user_password(await service.resolve_login_target(None), "password123")

    assert verified is admin
    assert repository.last_login == [admin.id]


@pytest.mark.asyncio
async def test_session_state_preserves_admin_without_password_when_guest_access_is_open() -> None:
    repository = _FakeRepository()
    repository.settings.guest_access_enabled = True
    service = _service(repository)

    session = await service.get_session_state(None)

    assert session.authenticated is True
    assert session.password_required is False
    assert session.guest_access_enabled is True
    assert session.guest_password_required is False
    assert session.role == DashboardRole.ADMIN
    assert session.permissions[:2] == [DashboardPermission.READ.value, DashboardPermission.WRITE.value]
    assert "users:manage:all" in session.permissions
    assert session.user is None
    assert session.login is not None and session.login.username_field == "shown"


@pytest.mark.asyncio
async def test_trusted_header_session_state_does_not_advertise_public_guest_without_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.dashboard_auth.service as service_module

    repository = _FakeRepository()
    repository.add(_make_user("admin", password_hash="configured"))
    repository.settings.guest_access_enabled = True
    monkeypatch.setattr(
        service_module,
        "get_settings",
        lambda: _FakeSettings(dashboard_auth_mode=DashboardAuthMode.TRUSTED_HEADER),
    )
    service = _service(repository)

    session = await service.get_session_state(None)

    assert session.authenticated is False
    assert session.guest_access_enabled is True
    assert session.guest_password_required is False
    assert session.role == DashboardRole.ADMIN
    assert session.permissions[:2] == [DashboardPermission.READ.value, DashboardPermission.WRITE.value]
    assert session.access_summary is None
    assert session.assignable_role_ids == []


@pytest.mark.asyncio
async def test_session_state_for_signed_in_admin_carries_user_and_access_summary() -> None:
    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = _service(repository, store)
    admin = await service.setup_password("password123")
    repository.add(_make_user("viewer", slug=PresetRoleSlug.VIEWER, password_hash=None, status="invited"))

    session = await service.get_session_state(_session_for(store, admin))

    assert session.authenticated is True
    assert session.user is not None
    assert session.user.username == "admin"
    assert session.user.role.slug == "admin"
    assert session.auth_method == "password"
    assert session.login is not None and session.login.username_field == "hidden"
    assert session.access_summary is not None
    assert session.access_summary.users_total == 2
    assert session.access_summary.users_invited == 1
    assert session.access_summary.non_admin_users == 1
    assert session.assignable_role_ids == [
        PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
        PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR],
        PRESET_ROLE_IDS[PresetRoleSlug.VIEWER],
    ]


@pytest.mark.asyncio
async def test_session_state_hides_access_summary_from_non_managers() -> None:
    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = _service(repository, store)
    await service.setup_password("password123")
    viewer = repository.add(_make_user("viewer", slug=PresetRoleSlug.VIEWER, password_hash="x"))

    session = await service.get_session_state(_session_for(store, viewer))

    assert session.authenticated is True
    assert session.role == DashboardRole.ADMIN  # wire role stays coarse
    assert session.user is not None and session.user.role.slug == "viewer"
    assert session.permissions == ["read", "accounts:read:all", "dashboard:read:all"]
    assert session.access_summary is None
    assert session.assignable_role_ids == []
    assert session.login is not None and session.login.username_field == "shown"


@pytest.mark.asyncio
async def test_stale_generation_and_disabled_user_sessions_are_unauthenticated() -> None:
    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = _service(repository, store)
    admin = await service.setup_password("password123")
    session_id = _session_for(store, admin)

    await service.revoke_user_sessions(admin)
    assert (await service.get_session_state(session_id)).authenticated is False

    fresh = _session_for(store, admin)
    assert (await service.get_session_state(fresh)).authenticated is True
    admin.status = "disabled"
    assert await service.resolve_user_session(fresh) is None


@pytest.mark.asyncio
async def test_the_fake_counts_qualifying_accounts_the_way_the_repository_does() -> None:
    """A designation is not a way back in until all five facts hold.

    This pins the stand-in to the production predicate the repository's SQL
    filter mirrors. A hand-copied predicate here dropped the password term
    once, which made every removal test in this file agree with a production
    refusal it was no longer reproducing.
    """

    repository = _FakeRepository()
    proxy_admin = repository.add(_make_user("proxy-admin", password_hash=None))
    proxy_admin.is_break_glass = True
    proxy_admin.totp_secret_encrypted = b"secret"
    assert await repository.count_qualifying_break_glass() == 0

    proxy_admin.password_hash = "hash"
    assert await repository.count_qualifying_break_glass() == 1
    assert await repository.count_qualifying_break_glass(exclude_user_id=proxy_admin.id) == 0

    for attribute, value in (("totp_secret_encrypted", None), ("status", "disabled"), ("is_break_glass", False)):
        original = getattr(proxy_admin, attribute)
        setattr(proxy_admin, attribute, value)
        assert await repository.count_qualifying_break_glass() == 0, attribute
        setattr(proxy_admin, attribute, original)
    proxy_admin.role_id = PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
    assert await repository.count_qualifying_break_glass() == 0


@pytest.mark.asyncio
async def test_remove_password_clears_credentials_only_on_solo_installs() -> None:
    repository = _FakeRepository()
    service = _service(repository)
    admin = await service.setup_password("password123")
    repository.settings.totp_required_on_login = True
    admin.totp_secret_encrypted = b"secret"
    admin.totp_last_verified_step = 123

    with pytest.raises(InvalidCredentialsError):
        await service.remove_password(admin, "wrong")

    other = repository.add(_make_user("ops", slug=PresetRoleSlug.OPERATOR, password_hash="x"))
    with pytest.raises(OtherUsersExistError):
        await service.remove_password(admin, "password123")
    del repository.users[other.id]
    repository.identities[admin.id] = 1
    with pytest.raises(OtherUsersExistError):
        await service.remove_password(admin, "password123")
    repository.identities.clear()

    await service.remove_password(admin, "password123")
    assert admin.password_hash is None
    assert admin.totp_secret_encrypted is None
    assert admin.totp_last_verified_step is None
    assert repository.settings.totp_required_on_login is False
    with pytest.raises(PasswordNotConfiguredError):
        await service.verify_password("password123")
    # The install is passwordless again, so first-run setup re-arms the same account.
    again = await service.setup_password("password456")
    assert again is admin


@pytest.mark.asyncio
async def test_verify_totp_inherits_existing_password_session_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyotp

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as service_module
    from app.core.crypto import TokenEncryptor

    current = {"value": 1_700_000_000}
    monkeypatch.setattr(service_module, "time", lambda: current["value"])
    monkeypatch.setattr(totp_module, "time", lambda: current["value"])

    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = _service(repository, store)
    admin = await service.setup_password("password123")

    secret = pyotp.random_base32()
    admin.totp_secret_encrypted = TokenEncryptor().encrypt(secret)

    # Issue an existing password session with the previous TTL setting
    # (12 hours), then change the TTL setting on the operator side and submit
    # TOTP. The new session must inherit the original session's remaining
    # lifetime, not adopt the new TTL.
    original_ttl = 12 * 60 * 60
    new_ttl_after_change = 24 * 60 * 60
    password_session_id = _session_for(store, admin, ttl=original_ttl)
    expected_remaining = original_ttl  # nothing has elapsed yet

    code = pyotp.TOTP(secret).at(current["value"])
    new_session_id, applied_ttl = await service.verify_totp(
        session_id=password_session_id,
        code=code,
        ttl_seconds=new_ttl_after_change,
    )

    assert applied_ttl == expected_remaining
    state = store.get(new_session_id)
    assert state is not None
    assert state.user_id == admin.id
    assert state.password_verified is True
    assert state.totp_verified is True
    assert state.expires_at == current["value"] + expected_remaining


@pytest.mark.asyncio
async def test_verify_totp_caps_inherited_password_session_to_requested_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pyotp

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as service_module
    from app.core.crypto import TokenEncryptor

    current = {"value": 1_700_000_000}
    monkeypatch.setattr(service_module, "time", lambda: current["value"])
    monkeypatch.setattr(totp_module, "time", lambda: current["value"])

    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = _service(repository, store)
    admin = await service.setup_password("password123")

    secret = pyotp.random_base32()
    admin.totp_secret_encrypted = TokenEncryptor().encrypt(secret)

    long_password_ttl = 365 * 24 * 60 * 60
    remote_request_ttl = 12 * 60 * 60
    password_session_id = _session_for(store, admin, ttl=long_password_ttl)

    code = pyotp.TOTP(secret).at(current["value"])
    new_session_id, applied_ttl = await service.verify_totp(
        session_id=password_session_id,
        code=code,
        ttl_seconds=remote_request_ttl,
    )

    assert applied_ttl == remote_request_ttl
    state = store.get(new_session_id)
    assert state is not None
    assert state.totp_verified is True
    assert state.expires_at == current["value"] + remote_request_ttl


@pytest.mark.asyncio
async def test_verify_totp_does_not_call_session_store_get_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pyotp

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as service_module
    from app.core.crypto import TokenEncryptor

    current = {"value": 1_700_000_000}
    monkeypatch.setattr(service_module, "time", lambda: current["value"])
    monkeypatch.setattr(totp_module, "time", lambda: current["value"])

    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = _service(repository, store)
    admin = await service.setup_password("password123")

    secret = pyotp.random_base32()
    admin.totp_secret_encrypted = TokenEncryptor().encrypt(secret)

    # Regression for the race between two store.get(session_id) calls in
    # verify_totp: the inherited-TTL path must reuse the live state captured
    # during the active-session check rather than re-querying the store.
    original_ttl = 12 * 60 * 60
    new_ttl_after_change = 24 * 60 * 60
    password_session_id = _session_for(store, admin, ttl=original_ttl)

    real_get = store.get
    get_calls: list[str | None] = []

    def counted_get(session_id):
        get_calls.append(session_id)
        return real_get(session_id)

    monkeypatch.setattr(store, "get", counted_get)

    code = pyotp.TOTP(secret).at(current["value"])
    _, applied_ttl = await service.verify_totp(
        session_id=password_session_id,
        code=code,
        ttl_seconds=new_ttl_after_change,
    )

    assert applied_ttl == original_ttl
    assert get_calls == [password_session_id]


@pytest.mark.asyncio
async def test_totp_replay_is_refused_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyotp

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as service_module
    from app.core.crypto import TokenEncryptor
    from app.modules.dashboard_auth.service import TotpInvalidCodeError

    current = {"value": 1_700_000_000}
    monkeypatch.setattr(service_module, "time", lambda: current["value"])
    monkeypatch.setattr(totp_module, "time", lambda: current["value"])

    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = _service(repository, store)
    admin = await service.setup_password("password123")
    secret = pyotp.random_base32()
    admin.totp_secret_encrypted = TokenEncryptor().encrypt(secret)
    code = pyotp.TOTP(secret).at(current["value"])

    verified, _ = await service.verify_totp(session_id=_session_for(store, admin), code=code, ttl_seconds=3600)
    assert admin.totp_last_verified_step is not None
    with pytest.raises(TotpInvalidCodeError):
        await service.verify_totp(session_id=_session_for(store, admin), code=code, ttl_seconds=3600)
    with pytest.raises(TotpInvalidCodeError):
        await service.disable_totp(session_id=verified, code=code)
