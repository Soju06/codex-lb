from __future__ import annotations

from pydantic import Field

from app.modules.accounts.schemas import AccountProxySummary
from app.modules.shared.schemas import DashboardModel


class OauthStartRequest(DashboardModel):
    force_method: str | None = None
    # When True, the OAuth attempt's three token-arrival paths
    # (auto callback / manual callback / device polling) stash the
    # acquired tokens in transient OAuth state instead of persisting
    # an Account. The dashboard's "Finish setup" step then calls
    # /api/oauth/complete with the operator-supplied proxy fields,
    # which atomically probes and persists. See spec
    # ``account-egress-proxy``: "OAuth add-account with proxy
    # validates before account activation".
    expect_proxy: bool = False
    proxy_host: str | None = Field(default=None, max_length=253)
    proxy_port: int | None = Field(default=None, ge=1, le=65535)
    proxy_username: str | None = Field(default=None, max_length=255)
    proxy_password: str | None = Field(default=None, max_length=1024)
    proxy_remote_dns: bool = True
    proxy_label: str | None = Field(default=None, max_length=128)


class OauthStartResponse(DashboardModel):
    flow_id: str | None = None
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
    flow_id: str | None = None
    device_auth_id: str | None = None
    user_code: str | None = None
    # Optional proxy fields mirroring ``AccountProxyInput`` for the
    # OAuth-with-proxy atomic-persistence path. ``tokens_ready`` OAuth
    # attempts require a complete proxy payload; full validation runs
    # after these flat fields are rebundled into ``AccountProxyInput``
    # inside the service layer.
    proxy_host: str | None = Field(default=None, max_length=253)
    proxy_port: int | None = Field(default=None, ge=1, le=65535)
    proxy_username: str | None = Field(default=None, max_length=255)
    proxy_password: str | None = Field(default=None, max_length=1024)
    proxy_remote_dns: bool = True
    proxy_label: str | None = Field(default=None, max_length=128)


class OauthCompleteResponse(DashboardModel):
    status: str
    account_id: str | None = None
    proxy: AccountProxySummary | None = None


class ManualCallbackRequest(DashboardModel):
    callback_url: str
    flow_id: str | None = None


class ManualCallbackResponse(DashboardModel):
    status: str
    error_message: str | None = None
