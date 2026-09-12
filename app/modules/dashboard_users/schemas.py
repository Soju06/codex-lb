from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from app.core.auth.providers import MAX_SUBJECT_LENGTH
from app.modules.dashboard_auth.schemas import DashboardUserRoleSummary
from app.modules.shared.schemas import DashboardModel


class PendingInviteSummary(DashboardModel):
    #: ``null`` for an SSO-only account: it waits for its first provider sign-in and never expires.
    expires_at: datetime | None = None
    sso_only: bool = False


class DashboardUserResponse(DashboardModel):
    """An account as the people list shows it: never a hash, a secret, or a token."""

    id: str
    username: str
    display_name: str | None = None
    email: str | None = None
    role: DashboardUserRoleSummary
    role_source: str
    status: str
    is_break_glass: bool
    totp_configured: bool
    has_password: bool
    created_at: datetime
    last_login_at: datetime | None = None
    pending_invite: PendingInviteSummary | None = None


class IssuedInviteResponse(DashboardModel):
    """The one time the plaintext invite token leaves the server."""

    token: str
    expires_at: datetime


class ExpectedIdentityRequest(DashboardModel):
    """The external identity a pre-created account waits for (exact triple match)."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(max_length=32)
    provider_key: str = Field(default="default", max_length=128)
    subject: str = Field(min_length=1, max_length=MAX_SUBJECT_LENGTH)


class DashboardUserCreateRequest(DashboardModel):
    """``sso_only`` / ``expected_identity`` need an active non-password provider (``409 sso_not_available``)."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)
    role_id: str
    username_locked: bool = False
    #: No invite link: the account is linked and activated by its first provider sign-in.
    sso_only: bool = False
    expected_identity: ExpectedIdentityRequest | None = None


class DashboardUserCreateResponse(DashboardModel):
    user: DashboardUserResponse
    #: ``null`` for an SSO-only account: there is no link to hand over.
    invite: IssuedInviteResponse | None


class DashboardUserUpdateRequest(DashboardModel):
    """Fields left out are untouched; ``displayName: null`` / ``email: null`` clear the value."""

    model_config = ConfigDict(extra="forbid")

    role_id: str | None = None
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)
    status: Literal["active", "disabled"] | None = None
    #: The emergency-access designation (PLAN §4.2). Only meaningful on the
    #: admin preset, and an account that carries it is always ``manual``:
    #: no sign-in provider re-evaluation may move an emergency account.
    is_break_glass: bool | None = None
    #: Take a role a sign-in provider manages over by hand: the change is
    #: applied and the account becomes ``manual``, so no later re-evaluation
    #: moves it again. Only meaningful together with ``roleId``.
    force: bool = False


class ProfileUpdateRequest(DashboardModel):
    """Self-service edit of the signed-in account (``PATCH /api/dashboard-auth/me``)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)


class PendingInviteResponse(DashboardModel):
    user_id: str
    username: str
    role_id: str
    expires_at: datetime | None = None
    created_by_user_id: str
    sso_only: bool = False


class ReactivateKeysResponse(DashboardModel):
    reactivated: int
