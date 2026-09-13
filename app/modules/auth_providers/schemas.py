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
    #: Masked provider configuration (``****last4`` per secret): the
    #: trusted-header row's two header names, the OIDC row's connection
    #: settings, empty for every other kind.
    config: dict[str, str] = Field(default_factory=dict)
    #: When an admin last completed a test sign-in against the stored
    #: configuration. Enabling an OIDC row needs one no older than ten minutes,
    #: and it belongs to the admin who ran it.
    test_login_verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OidcConfigRequest(DashboardModel):
    """The OIDC connection settings, written whole.

    A write replaces the stored document, so ``clientSecret`` is required every
    time rather than inherited from whatever was there before: a partial write
    that kept the old secret while repointing the issuer would send the
    operator's credential to a server they had not yet typed it for. The
    dashboard already holds every field in the connect wizard's form.

    URLs are only length-checked here; the scheme, host and path rules live in
    ``auth_providers.config`` so the flow and the settings API cannot disagree
    about what a usable issuer is.
    """

    model_config = ConfigDict(extra="forbid")

    issuer: str = Field(min_length=1, max_length=512)
    #: Defaults to the issuer's ``/.well-known/openid-configuration``.
    discovery_url: str | None = Field(default=None, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    client_secret: str = Field(min_length=1, max_length=256)
    #: Where the identity provider sends the browser back; must be this
    #: install's own callback path, and is the only source of the redirect URI
    #: the flow sends.
    redirect_uri: str = Field(min_length=1, max_length=512)
    subject_claim: str | None = Field(default=None, max_length=256)
    email_claim: str | None = Field(default=None, max_length=256)
    name_claim: str | None = Field(default=None, max_length=256)
    groups_claim: str | None = Field(default=None, max_length=256)


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
    #: The connection settings, on an ``oidc`` row only (422
    #: ``config_not_supported`` elsewhere -- the trusted-header row's headers
    #: are deployment topology and are read-only). Writing them clears any
    #: test-login proof, so a proof obtained against one identity provider
    #: cannot enable a different one.
    config: OidcConfigRequest | None = None
