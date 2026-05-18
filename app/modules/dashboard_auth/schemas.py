from __future__ import annotations

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.modules.shared.schemas import DashboardModel


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
    password: str


class PasswordChangeRequest(DashboardModel):
    current_password: str
    new_password: str


class PasswordRemoveRequest(DashboardModel):
    password: str
