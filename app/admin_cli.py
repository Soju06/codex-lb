"""Host recovery commands: the way back in when the dashboard cannot let anyone in.

PLAN §4.6 gives the identity work one non-negotiable escape hatch. Every gate
this release adds — a tightened ``local_login_policy``, a sign-in provider that
owns the login screen, the second factor a break-glass account must always
present — protects the install right up to the moment it is the thing that
broke. When the identity provider is unreachable, or the second-factor secret
went missing with the phone that held it, the only actor left to trust is
whoever can reach the host and its database.

So these commands take the shortest path that exists: the configured database
URL, a synchronous engine of their own, one transaction. No FastAPI app, no
``init_db()`` (it takes the SQLite lifetime lock and would fight a running
server), no environment variable of their own, no network call — and
deliberately no policy or provider gate, because they are the recovery path for
the very lockout those gates exist to prevent. Every run leaves one
``break_glass_cli_used`` row at critical severity naming the command and its
target: an operator on the host is unauthenticated by construction, so the
audit trail is the whole of the accountability.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.audit.types import AuditAuthMethod, AuditSeverity
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.config.settings import get_settings
from app.db.migration_url import to_sync_database_url
from app.db.models import (
    COMPAT_ADMIN_USERNAME,
    AuditLog,
    AuthProviderKind,
    DashboardAuthProvider,
    DashboardSettings,
    DashboardUser,
    DashboardUserStatus,
    LocalLoginPolicy,
)
from app.modules.dashboard_users.break_glass import local_login_admits
from app.modules.dashboard_users.repository import normalize_username

__all__ = ["BREAK_GLASS_CLI_ACTION", "run_admin_command"]

#: The one audit action every host recovery command writes.
BREAK_GLASS_CLI_ACTION = "break_glass_cli_used"

# Mirrors ``app/db/session.py``: sqlite3's own 5 s default would let a recovery
# command lose the writer race to the running server during exactly the
# incident it exists for.
_SQLITE_BUSY_TIMEOUT_SECONDS = 30

# bcrypt hashes at most 72 bytes and raises past that instead of truncating.
_MAX_PASSWORD_BYTES = 72

_SCHEMA_MISSING_HINT = (
    "This database has no codex-lb schema yet. Run `codex-lb-db upgrade` (or start the server once) and try again."
)

_CACHE_CONVERGENCE = "a running server picks this up within about five seconds; no restart needed"

#: The policy values that can refuse an account the local password form. Both
#: require the admin preset, which is what lets the reachability check narrow
#: its query; a value added later must be added here deliberately.
_RESTRICTED_POLICIES = frozenset({LocalLoginPolicy.ADMINS_ONLY.value, LocalLoginPolicy.BREAK_GLASS_ONLY.value})


@dataclass(frozen=True, slots=True)
class _Report:
    """What a command did, in the operator's words."""

    title: str
    facts: tuple[tuple[str, str], ...]


def run_admin_command(args: argparse.Namespace) -> None:
    """Dispatch one ``codex-lb admin`` sub-command and print what it did."""

    command = getattr(args, "admin_command", None)
    if command == "reset-password":
        report = _reset_password(args.username, clear_two_factor=bool(args.clear_two_factor))
    elif command == "local-login":
        if getattr(args, "admin_local_login_command", None) != "enable":
            raise SystemExit("admin local-login requires a subcommand: enable")
        report = _enable_local_login()
    elif command == "disable-provider":
        report = _disable_provider(args.provider_id)
    elif command == "reset-login-policy":
        _confirm_reset_login_policy(bool(args.yes))
        report = _reset_login_policy()
    else:
        raise SystemExit(
            "admin requires a subcommand: reset-password, local-login enable, disable-provider, reset-login-policy"
        )
    _print_report(report)


# --- the commands ---


def _reset_password(username: str, *, clear_two_factor: bool = False) -> _Report:
    """Set a new password for one account, revoke its sessions, optionally drop its second factor.

    The password is read from the terminal and never from ``argv``: a recovery
    password on the command line survives in the shell history, in ``ps`` and
    in whatever ships the host's logs.

    ``--clear-two-factor`` exists because a break-glass account that holds a
    secret must always present it, whatever the toggles say (PLAN §4.2). That
    rule is what makes the emergency account trustworthy — and it is also the
    one way a lost authenticator turns into a permanent lockout when the
    designated admin is the only one left. Clearing the secret from the host
    is the documented way out; the install-wide requirement is left alone, so
    an install that requires two-factor sends the account straight back to
    enrolment on its next sign-in.

    Clearing the secret is also what can take the account's *qualification*
    away, and under ``break_glass_only`` a designation that no longer qualifies
    is not admitted: the command would hand the operator a password for a form
    that refuses it. So the write is followed by the one question that matters
    — can anybody still use the local password form? — and when the answer is
    no, local sign-in is re-opened in the same transaction. A recovery command
    that can leave the install unreachable is not a recovery command; a policy
    that still has a working door is never touched.
    """

    normalized = normalize_username(username)
    password_hash = _hash_password(_prompt_new_password())

    with _transaction() as session:
        user = session.execute(select(DashboardUser).where(DashboardUser.username == normalized)).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"No account named {normalized!r} exists in this database.")
        was_enrolled = user.totp_secret_encrypted is not None
        user.password_hash = password_hash
        # Every cookie the account holds was minted against the old
        # credential; whoever locked it out may be holding one.
        user.session_generation += 1
        if clear_two_factor:
            user.totp_secret_encrypted = None
            user.totp_last_verified_step = None
        settings_row = _settings_row(session)
        if user.username == COMPAT_ADMIN_USERNAME and settings_row is not None:
            # The legacy shared-credential columns are still read by a replica
            # on the previous release, and the runtime write path mirrors them.
            # A host write that skipped the mirror would let the two disagree.
            settings_row.password_hash = password_hash
            settings_row.bootstrap_token_encrypted = None
            settings_row.bootstrap_token_hash = None
            if clear_two_factor:
                settings_row.totp_secret_encrypted = None
                settings_row.totp_last_verified_step = None
        reopened = _reopen_local_login_if_unreachable(session, settings_row)
        _audit(
            session,
            "reset-password",
            target_type="user",
            target_id=user.id,
            username=user.username,
            two_factor="cleared" if clear_two_factor else "kept",
            **({"local_login_reopened_from": reopened} if reopened is not None else {}),
        )
        facts = (
            ("Account", user.username),
            ("Status", user.status),
            ("Two-factor", _two_factor_fact(was_enrolled=was_enrolled, cleared=clear_two_factor)),
            ("Existing sessions", "revoked"),
        )
        if reopened is not None:
            facts += (
                (
                    "Local sign-in policy",
                    f"{reopened} -> {LocalLoginPolicy.ENABLED.value} "
                    "(re-opened: no account was left that the policy would let in)",
                ),
                ("Running servers", _CACHE_CONVERGENCE),
            )

    return _Report("Password reset", facts)


def _reopen_local_login_if_unreachable(session: Session, settings_row: DashboardSettings | None) -> str | None:
    """Put the policy back to ``enabled`` when no account can use the local form; returns the old value.

    The question is asked of the state this transaction is about to commit, so
    the flush the ``SELECT`` triggers is what makes the answer current. Only a
    restricted policy can produce a zero, and only a zero re-opens: an install
    that still has one admitted account keeps the door it chose to close.
    """

    if settings_row is None:
        return None
    policy = settings_row.local_login_policy
    if not _policy_admits_nobody(session, policy):
        return None
    settings_row.local_login_policy = LocalLoginPolicy.ENABLED.value
    return policy


def _policy_admits_nobody(session: Session, policy: str) -> bool:
    """Whether ``policy`` leaves no account that could sign in through the local form.

    ``enabled`` admits every active password account, so it can never be the
    thing standing in the way. The two restricted values both require the admin
    preset, which narrows the rows this has to look at to a handful; the
    admission rule itself is the same :func:`local_login_admits` the login path
    reads, so the two can never drift. A value this build does not know is
    never re-opened from here — silently relaxing a policy nobody can reason
    about is worse than the lockout it might be.
    """

    if policy not in _RESTRICTED_POLICIES:
        return False
    candidates = (
        session.execute(
            select(DashboardUser).where(
                DashboardUser.status == DashboardUserStatus.ACTIVE.value,
                DashboardUser.password_hash.is_not(None),
                DashboardUser.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
            )
        )
        .scalars()
        .all()
    )
    return not any(local_login_admits(candidate, policy) for candidate in candidates)


def _two_factor_fact(*, was_enrolled: bool, cleared: bool) -> str:
    if cleared:
        return "cleared — enrol again from Settings -> Access -> My sign-in" if was_enrolled else "not enrolled"
    return "enrolled (unchanged)" if was_enrolled else "not enrolled"


def _enable_local_login() -> _Report:
    """Put ``local_login_policy`` back to ``enabled`` with no qualifying check."""

    with _transaction() as session:
        settings_row = _settings_row(session)
        previous = LocalLoginPolicy.ENABLED.value if settings_row is None else settings_row.local_login_policy
        if settings_row is not None:
            settings_row.local_login_policy = LocalLoginPolicy.ENABLED.value
        _audit(
            session,
            "local-login enable",
            target_type="settings",
            target_id="local_login_policy",
            **{"from": previous, "to": LocalLoginPolicy.ENABLED.value},
        )

    return _Report(
        "Local sign-in re-opened",
        (
            ("Local sign-in policy", f"{previous} -> {LocalLoginPolicy.ENABLED.value}"),
            ("Effect", "every active account that holds a password may sign in again"),
            ("Running servers", _CACHE_CONVERGENCE),
        ),
    )


def _disable_provider(provider_id: str) -> _Report:
    """Turn one sign-in provider off so it stops claiming the login screen."""

    with _transaction() as session:
        provider = session.get(DashboardAuthProvider, provider_id)
        if provider is None:
            raise SystemExit(f"No sign-in provider {provider_id!r} exists in this database.{_known_providers(session)}")
        if provider.kind == AuthProviderKind.PASSWORD.value:
            raise SystemExit(
                "That is the local password provider. Disabling it would remove the recovery path itself; "
                "use `codex-lb admin local-login enable` to re-open local sign-in instead."
            )
        was_enabled = provider.enabled
        provider.enabled = False
        _audit(
            session,
            "disable-provider",
            target_type="auth_provider",
            target_id=provider.id,
            kind=provider.kind,
            provider_key=provider.provider_key,
        )
        facts = (
            ("Provider", f"{provider.label} ({provider.kind})"),
            ("Id", provider.id),
            ("Enabled", "true -> false" if was_enabled else "already false"),
            ("Running servers", _CACHE_CONVERGENCE),
        )

    return _Report("Sign-in provider disabled", facts)


def _reset_login_policy() -> _Report:
    """The big hammer: local sign-in back to open, every company provider off.

    ``local-login enable`` alone is not enough when the provider is the thing
    that is broken — an enabled trusted-header provider still resolves (and
    refuses) the identity on every request, so the local session it pre-empts
    never gets a chance. This command undoes the whole tightening in one step
    and leaves the password provider, which is the way back in.
    """

    with _transaction() as session:
        settings_row = _settings_row(session)
        previous = LocalLoginPolicy.ENABLED.value if settings_row is None else settings_row.local_login_policy
        if settings_row is not None:
            settings_row.local_login_policy = LocalLoginPolicy.ENABLED.value
        disabled: list[str] = []
        providers = session.execute(select(DashboardAuthProvider)).scalars().all()
        for provider in providers:
            if provider.kind == AuthProviderKind.PASSWORD.value or not provider.enabled:
                continue
            provider.enabled = False
            disabled.append(f"{provider.label} ({provider.kind})")
        _audit(
            session,
            "reset-login-policy",
            target_type="settings",
            target_id="local_login_policy",
            providers_disabled=len(disabled),
            **{"from": previous, "to": LocalLoginPolicy.ENABLED.value},
        )

    return _Report(
        "Sign-in policy reset",
        (
            ("Local sign-in policy", f"{previous} -> {LocalLoginPolicy.ENABLED.value}"),
            ("Providers disabled", ", ".join(disabled) if disabled else "none"),
            ("Kept", "the local password provider"),
            ("Running servers", _CACHE_CONVERGENCE),
        ),
    )


# --- plumbing ---


@contextmanager
def _transaction() -> Iterator[Session]:
    """One engine, one transaction, disposed on the way out (as ``codex-lb-db`` does)."""

    url = to_sync_database_url(get_settings().database_url)
    connect_args: dict[str, object] = {"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS} if url.startswith("sqlite") else {}
    engine = create_engine(url, future=True, connect_args=connect_args)
    try:
        with Session(engine) as session, session.begin():
            yield session
    except StaleDataError as exc:
        # The settings row carries an optimistic version and the running
        # server writes it too. Losing that race costs nothing: nothing was
        # applied, and these commands are safe to repeat.
        raise SystemExit(
            "The dashboard changed the same row while this ran. Nothing was written; run it again."
        ) from exc
    except DatabaseError as exc:
        raise SystemExit(_database_error_message(exc)) from exc
    finally:
        engine.dispose()


def _database_error_message(exc: DatabaseError) -> str:
    """A never-migrated database is the likely case; say so instead of a traceback."""

    message = str(exc)
    lowered = message.casefold()
    if "no such table" in lowered or "does not exist" in lowered or "undefinedtable" in lowered:
        return _SCHEMA_MISSING_HINT
    if "locked" in lowered:
        return (
            f"{message}\nThe database stayed locked for {_SQLITE_BUSY_TIMEOUT_SECONDS}s. "
            "Something is holding a long write; stop the server and run this again."
        )
    return f"Database error: {message}"


def _settings_row(session: Session) -> DashboardSettings | None:
    """The settings singleton, or ``None`` on an install that never wrote one.

    A missing row is not an error here: the column default is ``enabled``, so
    "local sign-in is open afterwards" already holds.
    """

    return session.execute(select(DashboardSettings).limit(1)).scalar_one_or_none()


def _known_providers(session: Session) -> str:
    providers = session.execute(select(DashboardAuthProvider)).scalars().all()
    if not providers:
        return ""
    known = "\n".join(
        f"  {provider.id}  {provider.kind}  {'enabled' if provider.enabled else 'disabled'}" for provider in providers
    )
    return f"\nSign-in providers in this database:\n{known}"


def _audit(
    session: Session,
    command: str,
    *,
    target_type: str,
    target_id: str,
    **details: str | int,
) -> None:
    """One critical row per run, in the same transaction as the change it describes.

    ``AuditService.log_async`` wants a running loop and drops events once
    shutdown admission closes, so the row is inserted directly. The trade-off
    is the sink fan-out Phase 4 adds — which a host command could not reach in
    the first place — against an append-only record whose whole point is the
    database it is written to. The actor columns stay NULL: nobody
    authenticated, and inventing an actor would be a lie in the one place that
    must not hold any.
    """

    session.add(
        AuditLog(
            timestamp=datetime.now(UTC),
            action=BREAK_GLASS_CLI_ACTION,
            details=json.dumps({"command": command, **details}),
            auth_method=AuditAuthMethod.CLI.value,
            target_type=target_type,
            target_id=target_id,
            severity=AuditSeverity.CRITICAL.value,
        )
    )


def _prompt_new_password() -> str:
    if not sys.stdin.isatty():
        raise SystemExit(
            "Run `codex-lb admin reset-password` from a terminal (docker exec -it ...): "
            "the new password is prompted for, never taken from the command line."
        )
    password = getpass.getpass("New password: ")
    if password == "":
        raise SystemExit("Aborted: the new password is empty.")
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise SystemExit(f"Aborted: the new password is longer than {_MAX_PASSWORD_BYTES} bytes.")
    if getpass.getpass("Repeat new password: ") != password:
        raise SystemExit("Aborted: the two passwords do not match.")
    return password


def _confirm_reset_login_policy(yes: bool) -> None:
    warning = (
        "This re-opens local password sign-in and disables every company sign-in provider.\n"
        "People who sign in through the identity provider will have to use a local password until you turn it "
        "back on in the dashboard."
    )
    print(warning, file=sys.stderr)
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit("Refusing to write without --yes in a non-interactive shell.")
    if input("Continue? [y/N] ").strip().casefold() not in {"y", "yes"}:
        raise SystemExit("Aborted.")


def _hash_password(password: str) -> str:
    """The one hashing function the sign-in path uses; imported late to keep the CLI light."""

    from app.modules.dashboard_auth.service import hash_password

    return hash_password(password)


def _print_report(report: _Report) -> None:
    print("")
    print(report.title)
    for label, value in report.facts:
        print(f"- {label}: {value}")
    print(f"- Audited: {BREAK_GLASS_CLI_ACTION} (critical)")
