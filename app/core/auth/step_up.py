"""Step-up re-verification for sensitive dashboard mutations (PLAN §5 H5).

A signed-in account changing who may sign in (``security:write``,
``users:manage``, ``roles:manage``) or exporting account credentials
(``accounts:export``) must have re-proven a credential within the last five
minutes. What counts as re-proving depends on what the account holds: its
password (plus its TOTP code when it has a secret), or — for accounts that
sign in through a provider and hold no password — its own TOTP code. An
account holding neither a password nor a secret has one last option, and only
that one: re-authenticating at the identity provider it signs in with
(``prompt=login``). An account with none of the three cannot step up at all
and is told so (``step_up_unavailable``); there is no silent exemption, and a
provider's ``idp_mfa_enforced`` flag does not waive step-up.

The identity provider is an alternative to *nothing*, never to a factor the
account already holds: offering it to a password account would let a stolen
session cookie plus a still-live provider session change security settings
while presenting nothing the attacker does not already have.

This module holds the pure pieces (window, cookie name, availability,
freshness) plus the one lookup that turns "holds no credential" into "can
re-authenticate at its identity provider"; the cookie stores live next to the
session store in ``dashboard_auth.service`` and enforcement in
``auth.dependencies``.
"""

from __future__ import annotations

from typing import Final, Literal

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.db.models import AuthProviderKind, DashboardUser

#: How long a step-up verification is honoured, in seconds.
STEP_UP_MAX_AGE_SECONDS: Final = 300
#: Short-lived cookie carrying a step-up for principals that have no session
#: cookie to carry the claim (trusted-header accounts).
STEP_UP_COOKIE: Final = "codex_lb_step_up"
STEP_UP_UNAVAILABLE_MESSAGE: Final = "Set up two-factor authentication or a local password to change security settings"

StepUpMethod = Literal["password", "totp", "oidc"]


def step_up_methods(user: DashboardUser, *, oidc_identity: bool = False) -> list[StepUpMethod]:
    """The factors ``user`` must present to step up, in the order they are asked.

    Every listed factor is required: a password account with a TOTP secret
    presents both. ``oidc_identity`` says whether the account holds an identity
    on an active OIDC provider, and is consulted **only** when the account
    holds no credential of its own — the identity provider is the last resort,
    not a shortcut past a factor the account already has. Empty means the
    account cannot step up.
    """

    methods: list[StepUpMethod] = []
    if user.password_hash is not None:
        methods.append("password")
    if user.totp_secret_encrypted is not None:
        methods.append("totp")
    if methods:
        return methods
    return ["oidc"] if oidc_identity else []


async def account_step_up_methods(user: DashboardUser) -> list[StepUpMethod]:
    """:func:`step_up_methods` for ``user``, asking the database only when it must.

    An account holding a password or a TOTP secret is answered from the row
    alone, exactly as before. Only an account holding neither — the shape that
    is refused outright today — costs a lookup, and that lookup is what decides
    between "re-authenticate at your identity provider" and "you cannot step up
    at all". The gate, the ``403`` body and the session response all call this,
    so they cannot disagree about what the account may present.
    """

    methods = step_up_methods(user)
    if methods:
        return methods
    return step_up_methods(user, oidc_identity=await _has_active_oidc_identity(user))


async def _has_active_oidc_identity(user: DashboardUser) -> bool:
    """Whether ``user`` can actually reach an identity provider that would vouch for it.

    Both halves matter: an identity row on a provider that is disabled (or on
    an install whose auth mode does not admit it) is not a way to re-verify,
    and an active provider the account has never signed in through cannot
    vouch for it either.
    """

    # Imported here rather than at module scope: the registry reaches the
    # provider and user repositories, which sit above this module in the
    # layering, and everything else here is pure.
    from app.core.auth.providers import DEFAULT_PROVIDER_KEY
    from app.core.auth.providers.registry import get_auth_provider_registry
    from app.core.config.settings import get_settings
    from app.db.session import SessionLocal
    from app.modules.dashboard_users.repository import DashboardUsersRepository

    mode: DashboardAuthMode = get_settings().dashboard_auth_mode
    active = await get_auth_provider_registry().get_active(AuthProviderKind.OIDC, DEFAULT_PROVIDER_KEY, mode)
    if active is None:
        return False
    async with SessionLocal() as session:
        return await DashboardUsersRepository(session).has_identity(
            user.id, provider=AuthProviderKind.OIDC.value, provider_key=active.row.provider_key
        )


def is_step_up_fresh(verified_at: int | None, *, now: int) -> bool:
    """Whether a step-up recorded at ``verified_at`` still covers a request at ``now``."""

    return verified_at is not None and now - verified_at <= STEP_UP_MAX_AGE_SECONDS


def step_up_expires_at(verified_at: int) -> int:
    return verified_at + STEP_UP_MAX_AGE_SECONDS
