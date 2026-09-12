from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.core.auth.dashboard_access import DashboardPermission, DashboardRole
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.step_up import StepUpMethod
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


LoginProviderKind = Literal["password", "trusted_header"]
#: ``dashboard_settings.local_login_policy`` on the wire (PLAN §4.6).
LocalLoginPolicyValue = Literal["enabled", "admins_only", "break_glass_only"]


class DashboardLoginProvider(DashboardModel):
    kind: LoginProviderKind
    provider_key: str = "default"
    label: str
    login_url: str | None = None


class DashboardLoginHint(DashboardModel):
    """Login-screen hints served to unauthenticated clients too; never carries a username.

    ``pending_identity`` is true when the request carried a provider-asserted
    identity that has no account here (refused or not provisioned): the client
    shows "your account is not ready yet" instead of a login form.
    """

    username_field: Literal["hidden", "shown"]
    providers: list[DashboardLoginProvider]
    #: Whether the local password form is shown, collapsed behind a link
    #: (``admins_only``) or reachable only at ``/login?local=1``
    #: (``break_glass_only``). The account name is never sent here.
    local_login: LocalLoginPolicyValue = "enabled"
    pending_identity: bool = False


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
    local_login_policy: LocalLoginPolicyValue


class DashboardStepUpState(DashboardModel):
    """Whether the account has recently re-verified for sensitive changes, and how it can.

    ``methods`` lists every factor the account must present to ``/step-up``
    (``password`` and/or ``totp``); empty means it cannot step up until it
    enrols two-factor or sets a local password.
    """

    verified_at: int | None = None
    expires_at: int | None = None
    methods: list[StepUpMethod] = Field(default_factory=list)


class DashboardAuthSessionResponse(DashboardModel):
    authenticated: bool
    password_required: bool
    #: An active account holds a password (drives the Password card; accounts that
    #: sign in through a provider alone do not count).
    local_password_configured: bool = False
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
    #: Present for signed-in accounts only; principals without an account have nothing to re-verify.
    step_up: DashboardStepUpState | None = None
    #: True when the session was minted for an account carrying the
    #: break-glass designation, so the header can show the emergency pill.
    break_glass_session: bool = False


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


class StepUpRequest(DashboardModel):
    """The factors offered to ``POST /api/dashboard-auth/step-up``; which are needed depends on the account."""

    password: str | None = None
    code: str | None = Field(default=None, max_length=16)


class StepUpResponse(DashboardModel):
    verified_at: int
    expires_at: int


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


class InviteDescriptionResponse(DashboardModel):
    """What the acceptance screen may learn from a valid invite token."""

    role_name: str
    inviter_display_name: str | None = None
    suggested_username: str
    username_locked: bool
    expires_at: datetime


class InviteAcceptRequest(DashboardModel):
    token: str = Field(max_length=256)
    username: str | None = Field(default=None, max_length=64)
    password: str
    display_name: str | None = Field(default=None, max_length=128)
