from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from app.modules.shared.schemas import DashboardModel


class AuthProviderResponse(DashboardModel):
    """A provider row as the settings UI sees it; secrets are masked, never returned."""

    id: str
    kind: str
    provider_key: str
    label: str
    enabled: bool
    #: ``enabled`` and admitted by the current ``dashboard_auth_mode``.
    active: bool
    unknown_identity_role_id: str | None = None
    no_match_role_id: str | None = None
    link_by_email: bool
    skip_role_sync: bool
    idp_mfa_enforced: bool
    #: Masked provider configuration (``****last4`` per secret). Empty until OIDC ships.
    config: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class AuthProviderUpdateRequest(DashboardModel):
    """Fields left out are untouched; ``unknownIdentityRoleId: null`` means "refuse unknown identities"."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=64)
    #: Turning a password-less sign-in method on needs a qualifying
    #: break-glass account (409 ``break_glass_requires_totp``); turning one
    #: off is never gated, so the recovery direction is always open.
    enabled: bool | None = None
    unknown_identity_role_id: str | None = None
    no_match_role_id: str | None = None
    link_by_email: bool | None = None
    skip_role_sync: bool | None = None
    idp_mfa_enforced: bool | None = None
