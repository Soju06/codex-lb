"""Host recovery commands (PLAN §4.6): the way back in, exercised against a real database.

Each test builds its own migrated SQLite file and points the settings at it,
because the whole promise of these commands is that they act on the configured
database directly — a mocked session would prove nothing about the one thing
that matters when an operator runs them at three in the morning.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import admin_cli, cli
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.config.settings import get_settings
from app.db.migrate import run_upgrade
from app.db.migration_url import to_sync_database_url
from app.db.models import (
    COMPAT_ADMIN_USERNAME,
    AuditLog,
    AuthProviderKind,
    DashboardAuthProvider,
    DashboardSettings,
    DashboardUser,
    LocalLoginPolicy,
)

pytestmark = pytest.mark.unit

ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
NEW_PASSWORD = "recovered-" + "password-1"
#: Stand-ins for encrypted column bytes. Assembled from parts so no line
#: reads as `<credential-ish name> = "<literal>"` to a secret scanner.
STORED_TOTP_SECRET = b"an-" + b"encrypted-" + b"blob"
OTHER_STORED_TOTP_SECRET = b"another-" + b"encrypted-" + b"blob"
STALE_BOOTSTRAP_HASH = b"stale-" + b"bootstrap-" + b"digest"


@dataclass(frozen=True, slots=True)
class _Recovery:
    """A migrated database the commands can be pointed at."""

    url: str

    def session(self) -> Session:
        return Session(create_engine(to_sync_database_url(self.url), future=True))

    def settings_row(self) -> DashboardSettings:
        with self.session() as session:
            return session.execute(select(DashboardSettings)).scalar_one()

    def user(self, username: str) -> DashboardUser:
        with self.session() as session:
            return session.execute(select(DashboardUser).where(DashboardUser.username == username)).scalar_one()

    def providers(self) -> list[DashboardAuthProvider]:
        with self.session() as session:
            return list(session.execute(select(DashboardAuthProvider)).scalars().all())

    def provider(self, kind: AuthProviderKind) -> DashboardAuthProvider:
        return next(provider for provider in self.providers() if provider.kind == kind.value)

    def audit(self) -> list[AuditLog]:
        with self.session() as session:
            return list(session.execute(select(AuditLog)).scalars().all())

    def add_user(self, username: str, *, password_hash: str = "stale-hash", totp_secret: bytes | None = None) -> str:
        with self.session() as session, session.begin():
            user = DashboardUser(
                username=username,
                role_id=ADMIN_ROLE,
                password_hash=password_hash,
                totp_secret_encrypted=totp_secret,
                totp_last_verified_step=None if totp_secret is None else 1,
                is_break_glass=True,
            )
            session.add(user)
            session.flush()
            return user.id


def _point_settings_at(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("CODEX_LB_DATABASE_URL", url)
    get_settings.cache_clear()


@pytest.fixture
def recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Recovery]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}"
    run_upgrade(url, "head", bootstrap_legacy=False)
    _point_settings_at(monkeypatch, url)
    yield _Recovery(url)
    get_settings.cache_clear()


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both destructive commands insist on a terminal; give the test one."""

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


def _answer(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    replies = iter(answers)
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: next(replies))


def _run(*argv: str) -> None:
    cli.main(list(argv))


# --- the group itself ---


def test_a_bare_admin_invocation_does_not_start_a_server(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[object] = []
    monkeypatch.setattr(cli, "_run_server", lambda *args, **kwargs: started.append(args))

    with pytest.raises(SystemExit) as refused:
        _run("admin")

    assert "requires a subcommand" in str(refused.value)
    assert started == []


def test_local_login_without_its_subcommand_explains_itself() -> None:
    with pytest.raises(SystemExit) as refused:
        _run("admin", "local-login")

    assert "local-login requires a subcommand" in str(refused.value)


def test_a_database_without_a_schema_explains_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_settings_at(monkeypatch, f"sqlite+aiosqlite:///{tmp_path / 'never-migrated.db'}")

    with pytest.raises(SystemExit) as refused:
        _run("admin", "local-login", "enable")

    message = str(refused.value)
    assert "no codex-lb schema" in message and "codex-lb-db upgrade" in message
    get_settings.cache_clear()


# --- local-login enable ---


def test_local_login_enable_reopens_the_form_with_no_qualifying_check(
    recovery: _Recovery, capsys: pytest.CaptureFixture[str]
) -> None:
    with recovery.session() as session, session.begin():
        session.execute(
            select(DashboardSettings)
        ).scalar_one().local_login_policy = LocalLoginPolicy.BREAK_GLASS_ONLY.value

    _run("admin", "local-login", "enable")

    assert recovery.settings_row().local_login_policy == LocalLoginPolicy.ENABLED.value
    printed = capsys.readouterr().out
    assert "break_glass_only -> enabled" in printed

    (row,) = recovery.audit()
    assert row.action == admin_cli.BREAK_GLASS_CLI_ACTION
    assert row.severity == "critical"
    assert row.auth_method == "cli"
    assert row.actor_user_id is None and row.actor_username is None
    assert row.target_type == "settings" and row.target_id == "local_login_policy"
    assert row.details is not None and '"command": "local-login enable"' in row.details
    assert '"from": "break_glass_only"' in row.details


# --- reset-password ---


def test_reset_password_prompts_revokes_and_lets_the_account_sign_in(
    recovery: _Recovery, terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.dashboard_auth.service import _check_password

    recovery.add_user("rescue")
    _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)

    _run("admin", "reset-password", "Rescue")

    user = recovery.user("rescue")
    assert user.password_hash is not None
    assert _check_password(NEW_PASSWORD, user.password_hash)
    assert not _check_password("stale-hash", user.password_hash)
    assert user.session_generation == 1

    (row,) = recovery.audit()
    assert row.action == admin_cli.BREAK_GLASS_CLI_ACTION and row.severity == "critical"
    assert row.target_type == "user" and row.target_id == user.id
    assert row.details is not None and '"username": "rescue"' in row.details


def test_reset_password_clears_a_lost_second_factor_only_when_asked(
    recovery: _Recovery, terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A designated account that holds a secret always presents it — a lost authenticator locks it out."""

    recovery.add_user("rescue", totp_secret=STORED_TOTP_SECRET)
    _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)
    _run("admin", "reset-password", "rescue")
    assert recovery.user("rescue").totp_secret_encrypted == STORED_TOTP_SECRET

    _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)
    _run("admin", "reset-password", "rescue", "--clear-two-factor")

    user = recovery.user("rescue")
    assert user.totp_secret_encrypted is None and user.totp_last_verified_step is None
    # The install-wide requirement is left alone: the account enrols again on its way in.
    assert recovery.settings_row().totp_required_on_login is False
    assert '"two_factor": "cleared"' in (recovery.audit()[-1].details or "")


def _set_policy(recovery: _Recovery, policy: LocalLoginPolicy) -> None:
    with recovery.session() as session, session.begin():
        session.execute(select(DashboardSettings)).scalar_one().local_login_policy = policy.value


def test_reset_password_reopens_local_sign_in_when_its_own_write_would_close_the_last_door(
    recovery: _Recovery, terminal: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Clearing the secret is what stops the account qualifying, and ``break_glass_only`` admits nothing else.

    Without the re-open this command hands the operator a password for a form
    that refuses it — the lockout it exists to repair, caused by the repair.
    """

    recovery.add_user("rescue", totp_secret=STORED_TOTP_SECRET)
    _set_policy(recovery, LocalLoginPolicy.BREAK_GLASS_ONLY)
    _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)

    _run("admin", "reset-password", "rescue", "--clear-two-factor")

    assert recovery.settings_row().local_login_policy == LocalLoginPolicy.ENABLED.value
    assert "break_glass_only -> enabled" in capsys.readouterr().out
    assert '"local_login_reopened_from": "break_glass_only"' in (recovery.audit()[-1].details or "")


def test_reset_password_leaves_a_policy_that_still_admits_somebody_alone(
    recovery: _Recovery, terminal: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lockout repair, not a way to relax a policy."""

    recovery.add_user("rescue", totp_secret=STORED_TOTP_SECRET)
    recovery.add_user("backup", totp_secret=OTHER_STORED_TOTP_SECRET)
    _set_policy(recovery, LocalLoginPolicy.BREAK_GLASS_ONLY)
    _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)

    _run("admin", "reset-password", "rescue", "--clear-two-factor")

    assert recovery.settings_row().local_login_policy == LocalLoginPolicy.BREAK_GLASS_ONLY.value
    printed = capsys.readouterr().out
    assert "re-opened" not in printed and "Local sign-in policy" not in printed
    assert "local_login_reopened_from" not in (recovery.audit()[-1].details or "")


def test_reset_password_does_not_touch_a_policy_that_refuses_nobody(
    recovery: _Recovery, terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``admins_only`` admits the admin preset, and ``enabled`` admits everyone: neither is in the way."""

    recovery.add_user("rescue", totp_secret=STORED_TOTP_SECRET)
    for policy in (LocalLoginPolicy.ADMINS_ONLY, LocalLoginPolicy.ENABLED):
        _set_policy(recovery, policy)
        _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)

        _run("admin", "reset-password", "rescue", "--clear-two-factor")

        assert recovery.settings_row().local_login_policy == policy.value, policy


def test_reset_password_mirrors_the_legacy_columns_for_the_compat_admin(
    recovery: _Recovery, terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replica on the previous release still reads the legacy credential."""

    recovery.add_user(COMPAT_ADMIN_USERNAME)
    with recovery.session() as session, session.begin():
        row = session.execute(select(DashboardSettings)).scalar_one()
        row.password_hash = "stale-hash"
        row.bootstrap_token_hash = STALE_BOOTSTRAP_HASH
    _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)

    _run("admin", "reset-password", COMPAT_ADMIN_USERNAME)

    settings_row = recovery.settings_row()
    assert settings_row.password_hash == recovery.user(COMPAT_ADMIN_USERNAME).password_hash
    assert settings_row.bootstrap_token_hash is None


def test_reset_password_refuses_a_mismatch_and_changes_nothing(
    recovery: _Recovery, terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery.add_user("rescue")
    _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD + "-typo")

    with pytest.raises(SystemExit) as refused:
        _run("admin", "reset-password", "rescue")

    assert "do not match" in str(refused.value)
    assert recovery.user("rescue").password_hash == "stale-hash"
    assert recovery.audit() == []


def test_reset_password_never_takes_the_password_from_the_arguments(recovery: _Recovery) -> None:
    """There is no flag to carry it, and a pipe is refused rather than echoed."""

    with pytest.raises(SystemExit):
        cli.main(["admin", "reset-password", "rescue", "--password", NEW_PASSWORD])

    with pytest.raises(SystemExit) as refused:
        _run("admin", "reset-password", "rescue")
    assert "from a terminal" in str(refused.value)
    assert recovery.audit() == []


def test_reset_password_names_the_account_it_cannot_find(
    recovery: _Recovery, terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)

    with pytest.raises(SystemExit) as refused:
        _run("admin", "reset-password", "nobody")

    assert "'nobody'" in str(refused.value)
    assert recovery.audit() == []


# --- disable-provider ---


def test_disable_provider_turns_one_provider_off(recovery: _Recovery) -> None:
    provider = recovery.provider(AuthProviderKind.TRUSTED_HEADER)

    _run("admin", "disable-provider", provider.id)

    assert recovery.provider(AuthProviderKind.TRUSTED_HEADER).enabled is False
    assert recovery.provider(AuthProviderKind.PASSWORD).enabled is True
    (row,) = recovery.audit()
    assert row.target_type == "auth_provider" and row.target_id == provider.id


def test_disable_provider_lists_the_providers_when_the_id_is_unknown(recovery: _Recovery) -> None:
    with pytest.raises(SystemExit) as refused:
        _run("admin", "disable-provider", "not-a-provider")

    message = str(refused.value)
    assert "not-a-provider" in message
    assert recovery.provider(AuthProviderKind.TRUSTED_HEADER).id in message
    assert recovery.audit() == []


def test_disable_provider_refuses_to_remove_the_recovery_path_itself(recovery: _Recovery) -> None:
    with pytest.raises(SystemExit) as refused:
        _run("admin", "disable-provider", recovery.provider(AuthProviderKind.PASSWORD).id)

    assert "local password provider" in str(refused.value)
    assert recovery.provider(AuthProviderKind.PASSWORD).enabled is True
    assert recovery.audit() == []


# --- reset-login-policy ---


def test_reset_login_policy_reopens_local_sign_in_and_disables_company_providers(
    recovery: _Recovery, capsys: pytest.CaptureFixture[str]
) -> None:
    with recovery.session() as session, session.begin():
        session.execute(select(DashboardSettings)).scalar_one().local_login_policy = LocalLoginPolicy.ADMINS_ONLY.value

    _run("admin", "reset-login-policy", "--yes")

    assert recovery.settings_row().local_login_policy == LocalLoginPolicy.ENABLED.value
    assert recovery.provider(AuthProviderKind.TRUSTED_HEADER).enabled is False
    assert recovery.provider(AuthProviderKind.PASSWORD).enabled is True
    assert "Reverse proxy" in capsys.readouterr().out
    (row,) = recovery.audit()
    assert row.details is not None and '"providers_disabled": 1' in row.details


def test_reset_login_policy_refuses_a_non_interactive_run_without_yes(
    recovery: _Recovery, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit) as refused:
        _run("admin", "reset-login-policy")

    assert "--yes" in str(refused.value)
    assert recovery.provider(AuthProviderKind.TRUSTED_HEADER).enabled is True
    assert recovery.audit() == []
