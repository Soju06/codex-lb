"""Sign-in providers: how a request proves who is calling.

A provider either recognises the caller on every request from evidence the
request carries (``resolve_identity``: the reverse-proxy header today, a
signed assertion tomorrow) or runs a redirect dance (``begin_login`` /
``complete_login``: OIDC, Phase 3). Every provider hands the same
:class:`ExternalIdentity` to the shared identity resolver, which maps it to a
dashboard account; providers never touch accounts themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fastapi import Request

from app.core.auth.dashboard_mode import DashboardAuthMode, get_dashboard_request_auth
from app.db.models import AuthProviderKind
from app.modules.auth_providers.seed import DEFAULT_PROVIDER_KEY
from app.modules.dashboard_users.repository import is_valid_email, normalize_email

#: Column widths of ``dashboard_identities.subject`` / ``display_name``; the API validates the same bounds.
MAX_SUBJECT_LENGTH = 512
MAX_DISPLAY_NAME_LENGTH = 128

__all__ = [
    "DEFAULT_PROVIDER_KEY",
    "MAX_DISPLAY_NAME_LENGTH",
    "MAX_SUBJECT_LENGTH",
    "AuthProvider",
    "ExternalIdentity",
    "PasswordProvider",
    "TrustedHeaderProvider",
]


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """What a provider learned about the caller, before any account lookup.

    ``subject`` is the stable identifier the provider guarantees (the header
    value, an OIDC ``sub``); ``email`` and ``display_name`` are display and
    linking hints; ``groups`` is the IdP's group snapshot (case-folded), which
    role mappings match on.
    """

    provider: str
    provider_key: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    groups: tuple[str, ...] = ()


@runtime_checkable
class AuthProvider(Protocol):
    """One way of signing in.

    Header-style providers implement :meth:`resolve_identity`. Redirect-style
    providers (OIDC) add ``begin_login(request) -> RedirectResponse`` and
    ``complete_login(request) -> ExternalIdentity`` when they arrive; both
    still end in the shared identity resolver. ``kind`` matches
    ``dashboard_auth_providers.kind`` and ``provider_key`` the row's key.
    """

    @property
    def kind(self) -> AuthProviderKind: ...

    @property
    def provider_key(self) -> str: ...

    def resolve_identity(self, request: Request) -> ExternalIdentity | None:
        """The caller's identity when the request carries the provider's evidence, else ``None``."""
        ...


class PasswordProvider:
    """Local username/password sign-in. The login flow itself lives in
    ``dashboard_auth``; this marker exists so the provider list and the
    providers table have one entry per way of signing in."""

    kind = AuthProviderKind.PASSWORD
    provider_key = DEFAULT_PROVIDER_KEY

    def resolve_identity(self, request: Request) -> ExternalIdentity | None:
        del request
        return None


class TrustedHeaderProvider:
    """The reverse-proxy identity header (``CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER``).

    Reuses the request-auth resolution that already checks the trusted-proxy
    peer and the singular header. The subject is case-folded: two proxies
    spelling the same person differently must not become two accounts. The
    raw value is kept as the display name and, when it parses as an e-mail,
    as the identity's e-mail. Groups arrive on the same request-auth value,
    read from ``CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER``.
    """

    kind = AuthProviderKind.TRUSTED_HEADER
    provider_key = DEFAULT_PROVIDER_KEY

    def resolve_identity(self, request: Request) -> ExternalIdentity | None:
        request_auth = get_dashboard_request_auth(request)
        if request_auth is None or request_auth.mode != DashboardAuthMode.TRUSTED_HEADER or not request_auth.actor:
            return None
        return self.identity_from_subject(request_auth.actor, groups=request_auth.groups)

    def identity_from_subject(self, raw_subject: str, *, groups: tuple[str, ...] = ()) -> ExternalIdentity | None:
        """``None`` when the value cannot be an identity here (empty or wider than the subject column).

        ``groups`` defaults to none so the paths without a request (an invite's
        expected identity) can still name an identity.
        """

        subject = raw_subject.strip()
        if not subject or len(subject) > MAX_SUBJECT_LENGTH:
            return None
        email = normalize_email(subject)
        return ExternalIdentity(
            provider=self.kind.value,
            provider_key=self.provider_key,
            subject=subject.casefold(),
            email=email if email is not None and is_valid_email(email) else None,
            display_name=subject[:MAX_DISPLAY_NAME_LENGTH],
            groups=groups,
        )
