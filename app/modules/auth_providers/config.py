"""The OIDC provider's connection settings: one sealed document, no environment.

Owner decision D6: an identity provider is connected from the dashboard, so
the issuer, the client credentials and the claim names live in
``dashboard_auth_providers.config_encrypted`` -- one JSON document sealed with
the product's single :class:`TokenEncryptor` -- and nowhere else. There is no
``Settings`` field, no ``.env.example`` line and no configuration tier for any
of it.

Everything an operator types that the server will later *fetch* is validated
here, once, at write time: the callback carries an authorization code and the
token request carries the client secret, so both legs must be ``https``, and a
URL aimed at the instance metadata endpoint or at a private-range address is
refused rather than fetched. This is a configuration-time check over the name
the operator wrote; it does not resolve DNS and so does not defend against
rebinding (see the change's design notes). It *does* normalise the name first,
because a check that refuses ``169.254.169.254`` and accepts ``2852039166`` --
the same address, spelled as one decimal integer, which is what the resolver
hands the socket -- is not a check at all. The redirect URI is validated the
same way and is the *only* source of the redirect URI the flow sends: nothing
in the stack validates or rewrites ``Host``, so a redirect URI reflected from
the request would be attacker-controllable.

:func:`validate_https_url` is also the gate for the endpoints the *discovery
document* advertises (``jwks_uri``, ``authorization_endpoint``,
``token_endpoint``), which is why the spelling gap mattered beyond the operator
who typed the issuer: the token endpoint is where the client secret is sent.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Final
from urllib.parse import SplitResult, urlsplit

from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardValidationError
from app.db.models import DashboardAuthProvider

#: Where the identity provider sends the browser back. The stored redirect URI
#: must end here, so a provider can only be configured to return to this flow.
OIDC_CALLBACK_PATH: Final[str] = "/api/dashboard-auth/oidc/callback"
#: Where an operator's issuer publishes its metadata when they do not say.
OIDC_DISCOVERY_SUFFIX: Final[str] = "/.well-known/openid-configuration"

INVALID_PROVIDER_CONFIG_CODE: Final[str] = "invalid_provider_config"
CONFIG_NOT_SUPPORTED_CODE: Final[str] = "config_not_supported"

#: Host names that never name an identity provider and do name something we
#: must not fetch. ``metadata.google.internal`` is the cloud metadata endpoint;
#: ``localhost`` and anything under it resolve to the loopback interface.
_REFUSED_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "metadata.google.internal"})

_MAX_URL_LENGTH: Final[int] = 512
_MAX_FIELD_LENGTH: Final[int] = 256


class InvalidProviderConfigError(DashboardValidationError):
    """A connection field the server would have to fetch, or a missing one."""

    code = INVALID_PROVIDER_CONFIG_CODE


class ConfigNotSupportedError(DashboardValidationError):
    """A connection document written to a row of a kind that has none.

    The password row has no configuration at all and the trusted-header row's
    is the deployment's own topology (two header names from the environment),
    which the settings API reports and never accepts.
    """

    code = CONFIG_NOT_SUPPORTED_CODE


def _refuse(field: str, reason: str) -> InvalidProviderConfigError:
    return InvalidProviderConfigError(f"{field}: {reason}", param=field)


def _split(value: str, *, field: str) -> SplitResult:
    """``urlsplit`` as a provider-configuration rule rather than a raw exception.

    ``urlsplit`` raises ``ValueError`` on a handful of shapes it cannot parse at
    all -- an unterminated IPv6 literal (``https://[broken``) is the usual one.
    Unparsed, that escapes this module entirely and answers ``500`` instead of
    the field-named ``422`` every other malformed URL gets, so the one operator
    typo that crashes the endpoint would be the one the error message could not
    name.

    ``port`` is touched here for the same reason, and it has to be touched
    deliberately: ``SplitResult.port`` is a lazy property, so ``:port``,
    ``:-1`` and ``:99999`` parse without complaint and raise only when somebody
    reads them. Nothing in this module reads them, so an unchecked one would be
    stored and then raise out of yarl at fetch time -- an operator typo
    surfacing as "the identity provider could not be reached" instead of as the
    field that is wrong.
    """

    try:
        parts = urlsplit(value)
        # Deliberately read and discarded: the parsing *is* the property.
        _ = parts.port
    except ValueError as exc:
        raise _refuse(field, "is not a usable URL") from exc
    return parts


def validate_https_url(raw: str, *, field: str, allow_query: bool = True) -> str:
    """The one URL rule for every value this provider fetches or sends a browser to."""

    value = raw.strip()
    if not value:
        raise _refuse(field, "is required")
    if len(value) > _MAX_URL_LENGTH:
        raise _refuse(field, f"must be at most {_MAX_URL_LENGTH} characters")
    parts = _split(value, field=field)
    if parts.scheme != "https":
        raise _refuse(field, "must be an https URL")
    if parts.username or parts.password:
        raise _refuse(field, "must not carry a userinfo component")
    if parts.fragment:
        raise _refuse(field, "must not carry a fragment")
    if not allow_query and parts.query:
        raise _refuse(field, "must not carry a query string")
    host = parts.hostname
    if not host:
        raise _refuse(field, "must name a host")
    _assert_public_host(host, field=field)
    return value


def _literal_address(name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address ``name`` *is*, in any spelling a resolver would accept, or ``None``.

    ``ipaddress`` only parses the canonical dotted quad, but ``getaddrinfo``
    hands the socket an address for three more spellings of the same thing:
    one decimal integer (``2130706433``), octal octets (``0177.0.0.1``) and
    hexadecimal ones (``0x7f.0.0.1``) -- plus the short forms (``127.1``).
    ``inet_aton`` is exactly the parser the resolver reaches for first, so
    asking it is asking the question the connection will answer. It resolves
    nothing: a name it cannot parse is a name, not an address, and is left to
    the rule above and to the DNS caveat in this module's docstring.
    """

    try:
        return ipaddress.ip_address(name)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(name))
    except (OSError, ValueError):
        return None


def _assert_public_host(host: str, *, field: str) -> None:
    name = host.rstrip(".").casefold()
    if name in _REFUSED_HOSTS or name.endswith(".localhost"):
        raise _refuse(field, "must not name the local host")
    address = _literal_address(name)
    if address is None:
        return
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
        or address.is_unspecified
    ):
        raise _refuse(field, "must not name a loopback, link-local or private address")


def default_discovery_url(issuer: str) -> str:
    return issuer.rstrip("/") + OIDC_DISCOVERY_SUFFIX


@dataclass(frozen=True, slots=True)
class OidcProviderConfig:
    """What an operator connected, as the flow reads it.

    ``client_secret`` is in clear only inside this object and only between the
    decrypt and the token request; no API returns it, no log line prints it and
    no audit detail carries it.
    """

    issuer: str
    discovery_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    subject_claim: str = "sub"
    email_claim: str = "email"
    name_claim: str = "name"
    groups_claim: str = "groups"


def _required_text(raw: object, *, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _refuse(field, "is required")
    value = raw.strip()
    if len(value) > _MAX_FIELD_LENGTH:
        raise _refuse(field, f"must be at most {_MAX_FIELD_LENGTH} characters")
    return value


def _claim_name(raw: object, *, field: str, default: str) -> str:
    if raw is None:
        return default
    value = _required_text(raw, field=field)
    if any(character.isspace() for character in value):
        raise _refuse(field, "must not contain whitespace")
    return value


def build_oidc_config(document: dict[str, object]) -> OidcProviderConfig:
    """Validate an operator-supplied document into the typed configuration.

    Raises :class:`InvalidProviderConfigError` (422, naming the field) for
    anything the flow would otherwise have to fetch or send blindly.
    """

    issuer = validate_https_url(
        _required_text(document.get("issuer"), field="issuer"), field="issuer", allow_query=False
    )
    raw_discovery = document.get("discovery_url")
    discovery_url = validate_https_url(
        _required_text(raw_discovery, field="discovery_url")
        if raw_discovery is not None
        else default_discovery_url(issuer),
        field="discovery_url",
    )
    redirect_uri = validate_https_url(
        _required_text(document.get("redirect_uri"), field="redirect_uri"), field="redirect_uri", allow_query=False
    )
    if _split(redirect_uri, field="redirect_uri").path != OIDC_CALLBACK_PATH:
        raise _refuse("redirect_uri", f"must end with {OIDC_CALLBACK_PATH}")
    return OidcProviderConfig(
        issuer=issuer,
        discovery_url=discovery_url,
        client_id=_required_text(document.get("client_id"), field="client_id"),
        client_secret=_required_text(document.get("client_secret"), field="client_secret"),
        redirect_uri=redirect_uri,
        subject_claim=_claim_name(document.get("subject_claim"), field="subject_claim", default="sub"),
        email_claim=_claim_name(document.get("email_claim"), field="email_claim", default="email"),
        name_claim=_claim_name(document.get("name_claim"), field="name_claim", default="name"),
        groups_claim=_claim_name(document.get("groups_claim"), field="groups_claim", default="groups"),
    )


def _document(config: OidcProviderConfig) -> dict[str, str]:
    """The configuration as one ordered JSON-ready mapping. One writer, one reader."""

    return {
        "issuer": config.issuer,
        "discovery_url": config.discovery_url,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": config.redirect_uri,
        "subject_claim": config.subject_claim,
        "email_claim": config.email_claim,
        "name_claim": config.name_claim,
        "groups_claim": config.groups_claim,
    }


def oidc_config_fingerprint(config: OidcProviderConfig) -> str:
    """A collision-free name for *this exact connection document*.

    Two things need to tell one configuration from another and neither can use
    the row's ``updated_at``, whose resolution is one second on SQLite: the
    metadata cache (two writes inside the same second would otherwise share a
    key and serve the previous issuer's endpoints to the new client secret) and
    the test-login proof (a callback must only stamp the configuration it
    actually verified). Both ask this function, so they cannot disagree about
    what "the same configuration" means.

    The digest is over the whole document including the secret, because a
    rotated secret against an unchanged issuer is a different connection: the
    cached token endpoint is fine, but the proof that the old secret worked
    says nothing about the new one.
    """

    return hashlib.sha256(json.dumps(_document(config), separators=(",", ":")).encode("utf-8")).hexdigest()


def seal_oidc_config(config: OidcProviderConfig, encryptor: TokenEncryptor | None = None) -> bytes:
    """The document as it is stored: one sealed JSON blob, like the session cookie."""

    return (encryptor or TokenEncryptor()).encrypt(json.dumps(_document(config), separators=(",", ":")))


def load_oidc_config(
    provider: DashboardAuthProvider, encryptor: TokenEncryptor | None = None
) -> OidcProviderConfig | None:
    """The stored configuration, or ``None`` when the row was never configured.

    A blob that cannot be opened or does not validate is ``None`` too: an
    unreadable configuration must refuse sign-ins, not raise out of a public
    route where the difference would be observable.
    """

    blob = provider.config_encrypted
    if not blob:
        return None
    try:
        document = json.loads((encryptor or TokenEncryptor()).decrypt(blob))
    except Exception:
        return None
    if not isinstance(document, dict):
        return None
    try:
        return build_oidc_config(document)
    except InvalidProviderConfigError:
        return None


def mask_secret(secret: str) -> str:
    """``****`` plus the last four characters, so an operator can tell which secret is stored."""

    return f"****{secret[-4:]}" if len(secret) > 4 else "****"


def mask_oidc_config(config: OidcProviderConfig) -> dict[str, str]:
    """The settings API projection: everything in clear except the secret.

    The client id is public by specification -- it rides in every authorization
    URL the browser follows -- and the wizard has to show it, so masking it
    would make the UI worse without hiding anything.
    """

    return {
        "issuer": config.issuer,
        "discoveryUrl": config.discovery_url,
        "clientId": config.client_id,
        "clientSecret": mask_secret(config.client_secret),
        "redirectUri": config.redirect_uri,
        "subjectClaim": config.subject_claim,
        "emailClaim": config.email_claim,
        "nameClaim": config.name_claim,
        "groupsClaim": config.groups_claim,
    }
