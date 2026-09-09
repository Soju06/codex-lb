from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.auth.dashboard_access import DashboardPermission, DashboardRole
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.modules.shared.schemas import DashboardModel


class DashboardUserRoleSummary(DashboardModel):
    id: str
    slug: str
    name: str
    kind: str


class DashboardSessionUser(DashboardModel):
    id: str
    username: str
    display_name: str | None = None
    role: DashboardUserRoleSummary


class DashboardLoginProvider(DashboardModel):
    kind: Literal["password"]
    label: str
    login_url: str | None = None


class DashboardLoginHint(DashboardModel):
    """Login-screen hints served to unauthenticated clients too; never carries a username."""

    username_field: Literal["hidden", "shown"]
    providers: list[DashboardLoginProvider]
    local_login: Literal["enabled"] = "enabled"


class DashboardAccessSummary(DashboardModel):
    """Team-size facts for `users:manage` holders only (drives the frontend disclosure tier)."""

    users_total: int
    users_active: int
    users_invited: int
    users_disabled: int
    pending_invites: int
    non_admin_users: int
    custom_roles: int
    providers_enabled: list[str]
    role_mappings: int
    scim_tokens: int
    audit_sinks: int
    local_login_policy: Literal["enabled"]


class DashboardAuthSessionResponse(DashboardModel):
    authenticated: bool
    password_required: bool
    totp_required_on_login: bool
    totp_configured: bool
    bootstrap_required: bool = False
    bootstrap_token_configured: bool = False
    auth_mode: DashboardAuthMode = DashboardAuthMode.STANDARD
    password_management_enabled: bool = True
    password_session_active: bool = False
    #: Coarse wire role; every signed-in account is ``admin`` here, ``user.role`` carries the real one.
    role: DashboardRole = DashboardRole.ADMIN
    #: Legacy ``read``/``write`` aliases followed by ``<permission>:<scope>`` entries.
    permissions: list[str] = Field(
        default_factory=lambda: [DashboardPermission.READ.value, DashboardPermission.WRITE.value]
    )
    guest_access_enabled: bool = False
    guest_password_required: bool = False
    user: DashboardSessionUser | None = None
    auth_method: str | None = None
    must_change_password: bool = False
    totp_enrollment_required: bool = False
    login: DashboardLoginHint | None = None
    access_summary: DashboardAccessSummary | None = None
    assignable_role_ids: list[str] = Field(default_factory=list)


class DashboardMeResponse(DashboardModel):
    id: str
    username: str
    display_name: str | None = None
    email: str | None = None
    role: DashboardUserRoleSummary
    auth_method: str | None = None
    totp_configured: bool
    must_change_password: bool


class TotpSetupStartResponse(DashboardModel):
    secret: str
    otpauth_uri: str
    qr_svg_data_uri: str


class TotpSetupConfirmRequest(DashboardModel):
    secret: str
    code: str


class TotpVerifyRequest(DashboardModel):
    code: str


class PasswordSetupRequest(DashboardModel):
    password: str
    bootstrap_token: str | None = None


class PasswordLoginRequest(DashboardModel):
    #: Optional on single-user installs; required once more than one account holds a password.
    username: str | None = Field(default=None, max_length=64)
    password: str


class GuestLoginRequest(DashboardModel):
    password: str | None = None


class GuestPasswordSetRequest(DashboardModel):
    password: str


class PasswordChangeRequest(DashboardModel):
    current_password: str
    new_password: str


class PasswordRemoveRequest(DashboardModel):
    password: str
