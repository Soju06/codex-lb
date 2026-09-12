"""The break-glass invariant: one predicate, one guard, one policy question.

PLAN §4.2 gives ``is_break_glass`` two stages. The flag itself is a
*designation* — the migration backfills it on the compat ``admin`` account,
which usually holds no second factor, so a flag that implied readiness would
ship a lie. An account is **qualifying** only when all five current facts
hold: it is designated, it is active, it holds the admin preset, it has a
TOTP secret, and it has a local password. Qualification is computed on every
read and never stored, so enrolling a second factor changes it with no second
write.

The local password is part of the list because the whole point of a
qualifying account is that it can open the *local password form* while the
identity provider is down: a proxy-provisioned admin that never set a
password can be designated and can enrol a second factor, and counting it
would let an install tighten the policy into a state where no account can
sign in locally at all.

Every gate counts qualifying accounts. Tightening ``local_login_policy`` and
enabling a password-less sign-in provider need at least one; while the policy
is not ``enabled``, no change may take the last one away
(:func:`assert_break_glass_remains`, called from every mutation that could).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.db.models import DashboardUser, DashboardUserStatus, LocalLoginPolicy


class LastBreakGlassProtectedError(ValueError):
    """The resulting state would leave the install with no qualifying break-glass account."""


class BreakGlassRequiresTotpError(ValueError):
    """A gate that needs a qualifying account found none; ``username`` is the one that would fix it."""

    def __init__(self, message: str, *, username: str | None = None) -> None:
        super().__init__(message)
        self.username = username


class BreakGlassRoleRequiredError(ValueError):
    """The designation is only meaningful on the admin preset."""


def is_admin_preset(role_id: str) -> bool:
    return role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]


def qualifies(*, is_break_glass: bool, status: str, role_id: str, has_totp: bool, has_password: bool) -> bool:
    """The five facts, spelled out once."""

    return (
        is_break_glass
        and status == DashboardUserStatus.ACTIVE.value
        and is_admin_preset(role_id)
        and has_totp
        and has_password
    )


def user_qualifies(user: DashboardUser) -> bool:
    return qualifies(
        is_break_glass=user.is_break_glass,
        status=user.status,
        role_id=user.role_id,
        has_totp=user.totp_secret_encrypted is not None,
        has_password=user.password_hash is not None,
    )


def break_glass_second_factor_required(user: DashboardUser) -> bool:
    """An emergency account that holds a secret always presents it, whatever the toggles say.

    The rule binds an account that *has* a second factor: no global or
    admin-level toggle may let it in on a password alone. An account with no
    secret is by definition not qualifying, nothing can depend on it, and it
    must still be able to sign in and enrol -- otherwise the migrated install
    could never reach a qualifying account at all.
    """

    return user.is_break_glass and user.totp_secret_encrypted is not None


def policy_is_tightened(policy: str) -> bool:
    return policy != LocalLoginPolicy.ENABLED.value


def local_login_admits(user: DashboardUser, policy: str) -> bool:
    """Whether ``policy`` lets this account in through the local password form.

    One function so the login path, the trusted-header fallback gate and the
    session response that describes that fallback can never disagree.

    ``break_glass_only`` admits a **qualifying** account, not a designated one.
    A designation alone is what every upgraded install carries on its ``admin``
    row, and admitting it would leave the strictest policy with a password-only
    admin door — the exact thing the policy is turned on to close. Reading the
    same predicate every risk gate reads is also what makes the two stages of
    PLAN §4.2 one rule instead of two.

    That is safe against lockout because the policy cannot be tightened while
    the qualifying count is zero and, while it is tightened, no mutation may
    take the last qualifying account away: ``break_glass_only`` therefore
    implies at least one admitted account. A designated admin that has not
    enrolled yet is still admitted under ``enabled`` and ``admins_only`` and
    can enrol there, which is the path the migrated install takes. The only
    way to reach ``break_glass_only`` with nothing qualifying is an
    out-of-band write to the database, and that state is repaired from the
    host with ``codex-lb admin local-login enable`` (PLAN §4.6, docs/sso.md).
    """

    if policy == LocalLoginPolicy.ADMINS_ONLY.value:
        return is_admin_preset(user.role_id)
    if policy == LocalLoginPolicy.BREAK_GLASS_ONLY.value:
        return user_qualifies(user)
    return True


async def assert_break_glass_remains(
    user: DashboardUser,
    *,
    policy: str,
    count_other_qualifying: Callable[[], Awaitable[int]],
    is_break_glass: bool | None = None,
    status: str | None = None,
    role_id: str | None = None,
    has_totp: bool | None = None,
    has_password: bool | None = None,
) -> bool:
    """Refuse a change whose *resulting* state would leave zero qualifying accounts.

    The check is post-state on purpose: half the call sites do not change an
    account's existence at all (removing a TOTP secret, clearing the
    designation), so "is this the last one?" would answer the wrong question.
    Each keyword is the value the field would carry *after* the write; an
    omitted one keeps the row's current value.

    A change is only refused when it takes qualification away from an account
    that has it: an install that already has no qualifying account (its policy
    was tightened from the host CLI, say, and the secret was then lost) must
    stay editable, or the guard would block the very repair it exists for.

    Returns whether the guard is *armed* for this write -- true when the
    change does remove qualification and another account currently carries
    it. The caller must then re-apply the count inside its conditional write,
    so a concurrent writer cannot take that other account away in between.
    """

    if not policy_is_tightened(policy) or not user_qualifies(user):
        return False
    after = qualifies(
        is_break_glass=user.is_break_glass if is_break_glass is None else is_break_glass,
        status=user.status if status is None else status,
        role_id=user.role_id if role_id is None else role_id,
        has_totp=(user.totp_secret_encrypted is not None) if has_totp is None else has_totp,
        has_password=(user.password_hash is not None) if has_password is None else has_password,
    )
    if after:
        return False
    if await count_other_qualifying() == 0:
        raise LastBreakGlassProtectedError(
            "This is the only emergency account that can still sign in while local sign-in is restricted; "
            "designate another admin with two-factor first"
        )
    return True
