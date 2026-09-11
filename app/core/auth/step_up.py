"""Step-up re-verification for sensitive dashboard mutations (PLAN §5 H5).

A signed-in account changing who may sign in (``security:write``,
``users:manage``, ``roles:manage``) or exporting account credentials
(``accounts:export``) must have re-proven a credential within the last five
minutes. What counts as re-proving depends on what the account holds: its
password (plus its TOTP code when it has a secret), or — for accounts that
sign in through a provider and hold no password — its own TOTP code. An
account holding neither cannot step up at all and is told so
(``step_up_unavailable``); there is no silent exemption, and a provider's
``idp_mfa_enforced`` flag does not waive step-up.

This module holds the pure pieces (window, cookie name, availability,
freshness); the cookie stores live next to the session store in
``dashboard_auth.service`` and enforcement in ``auth.dependencies``.
"""

from __future__ import annotations

from typing import Final, Literal

from app.db.models import DashboardUser

#: How long a step-up verification is honoured, in seconds.
STEP_UP_MAX_AGE_SECONDS: Final = 300
#: Short-lived cookie carrying a step-up for principals that have no session
#: cookie to carry the claim (trusted-header accounts).
STEP_UP_COOKIE: Final = "codex_lb_step_up"
STEP_UP_UNAVAILABLE_MESSAGE: Final = "Set up two-factor authentication or a local password to change security settings"

StepUpMethod = Literal["password", "totp"]


def step_up_methods(user: DashboardUser) -> list[StepUpMethod]:
    """The factors ``user`` must present to step up, in the order they are asked.

    Every listed factor is required: a password account with a TOTP secret
    presents both. Empty means the account cannot step up.
    """

    methods: list[StepUpMethod] = []
    if user.password_hash is not None:
        methods.append("password")
    if user.totp_secret_encrypted is not None:
        methods.append("totp")
    return methods


def is_step_up_fresh(verified_at: int | None, *, now: int) -> bool:
    """Whether a step-up recorded at ``verified_at`` still covers a request at ``now``."""

    return verified_at is not None and now - verified_at <= STEP_UP_MAX_AGE_SECONDS


def step_up_expires_at(verified_at: int) -> int:
    return verified_at + STEP_UP_MAX_AGE_SECONDS
