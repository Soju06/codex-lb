from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from app.modules.dashboard_auth.schemas import DashboardUserRoleSummary
from app.modules.shared.schemas import DashboardModel


class PendingInviteSummary(DashboardModel):
    expires_at: datetime


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


class DashboardUserCreateRequest(DashboardModel):
    #: ``sso_only`` / ``expected_identity`` are not accepted until a non-password provider exists.
    model_config = ConfigDict(extra="forbid")

    username: str = Field(max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)
    role_id: str
    username_locked: bool = False


class DashboardUserCreateResponse(DashboardModel):
    user: DashboardUserResponse
    invite: IssuedInviteResponse


class DashboardUserUpdateRequest(DashboardModel):
    """Fields left out are untouched; ``displayName: null`` / ``email: null`` clear the value."""

    model_config = ConfigDict(extra="forbid")

    role_id: str | None = None
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)
    status: Literal["active", "disabled"] | None = None


class ProfileUpdateRequest(DashboardModel):
    """Self-service edit of the signed-in account (``PATCH /api/dashboard-auth/me``)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)


class PendingInviteResponse(DashboardModel):
    user_id: str
    username: str
    role_id: str
    expires_at: datetime
    created_by_user_id: str


class ReactivateKeysResponse(DashboardModel):
    reactivated: int
