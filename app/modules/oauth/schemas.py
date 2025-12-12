from __future__ import annotations

from app.modules.shared.schemas import DashboardModel


class OauthStartRequest(DashboardModel):
    force_method: str | None = None


class OauthStartResponse(DashboardModel):
    method: str
    authorization_url: str | None = None
    callback_url: str | None = None
    verification_url: str | None = None
    user_code: str | None = None
    device_auth_id: str | None = None
    interval_seconds: int | None = None
    expires_in_seconds: int | None = None


class OauthStatusResponse(DashboardModel):
    status: str
    error_message: str | None = None


class OauthCompleteRequest(DashboardModel):
    device_auth_id: str | None = None
    user_code: str | None = None


class OauthCompleteResponse(DashboardModel):
    status: str
