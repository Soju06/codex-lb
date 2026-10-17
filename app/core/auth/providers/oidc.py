"""The OpenID Connect provider: Authorization Code with PKCE, and nothing else.

This is the second implementation of the :class:`AuthProvider` protocol. Like
the first it ends in the shared identity resolver: it turns a *verified* ID
token into an :class:`ExternalIdentity` and stops. It creates no account, links
nothing, re-evaluates nothing and writes none of the identity audit events the
resolver already writes.

Three rules carry most of the security weight here, and each is written as code
rather than left to a library's defaults:

* **The signing key is chosen before the algorithm.** The candidate key is
  selected from the cached key set by ``kid``; the permitted algorithms are
  derived from *that key's own type*, intersected with what the identity
  provider advertises and with a fixed allow-list. The token header's ``alg``
  is never an input, which is what makes ``alg: none`` and "verify this RS256
  token as HS256 with the public key as the HMAC secret" impossible rather than
  merely refused.
* **Key rotation is a non-event.** Keys are cached with the discovery document
  for fifteen minutes; a token whose ``kid`` is unknown forces exactly one
  refresh, behind a per-provider cooldown so forged ``kid`` values cannot turn
  this endpoint into a request amplifier aimed at the identity provider.
* **The operator's issuer is a URL this server fetches**, so every fetch is
  ``https``, bounded in time and size, refuses redirects, and requires the
  document's own ``issuer`` claim to equal the configured issuer exactly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from base64 import b64encode
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote, urlencode

import aiohttp
import anyio
import jwt
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Request
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from app.core.auth.providers import (
    DEFAULT_PROVIDER_KEY,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_SUBJECT_LENGTH,
    ExternalIdentity,
)
from app.core.clients.http import lease_http_session
from app.db.models import AuthProviderKind, DashboardAuthProvider
from app.modules.auth_providers.config import OidcProviderConfig, oidc_config_fingerprint, validate_https_url
from app.modules.dashboard_users.repository import is_valid_email, normalize_email

logger = logging.getLogger(__name__)

#: How far apart this server's clock and the identity provider's may be before
#: a token is refused. A tolerance is not something an operator tunes
#: (PRINCIPLES P2, the ``OAUTH_TIMEOUT_SECONDS`` precedent), so it is a
#: constant: 60 s covers unsynchronised clocks without widening the window a
#: captured token is usable in, which the independent ``iat``-versus-flow rule
#: below bounds anyway.
OIDC_CLOCK_SKEW_SECONDS: Final[int] = 60

#: Every server-to-server fetch this module makes. The shared client session is
#: built with ``total=None``, so a call site that brings no timeout has none.
_FETCH_TIMEOUT_SECONDS: Final[float] = 10.0
_MAX_DOCUMENT_BYTES: Final[int] = 256 * 1024
_MAX_JWKS_KEYS: Final[int] = 20
_METADATA_TTL_SECONDS: Final[float] = 900.0
#: A ``kid`` this replica has never seen forces one refetch, then waits.
_REFRESH_COOLDOWN_SECONDS: Final[float] = 60.0
#: How long the last good key set keeps serving while refreshes fail. An
#: identity provider's five-minute outage must not lock everyone out; a key
#: revoked after a compromise must not keep validating forever.
_STALE_METADATA_MAX_AGE_SECONDS: Final[float] = 24 * 60 * 60
#: How long a *failed* refresh suppresses the next one while the cached key set
#: is still being served. Without it an identity provider whose discovery
#: endpoint times out while its token endpoint is healthy costs every start and
#: every callback another ten seconds, serialised behind the cache lock: the
#: outage of one endpoint becomes an outage of the whole sign-in path.
_FAILED_REFRESH_BACKOFF_SECONDS: Final[float] = 60.0

#: Asymmetric signature algorithms only. Every MAC algorithm and ``none`` is
#: absent by construction, not by a check that could be reordered away.
_SIGNING_ALGORITHMS: Final[frozenset[str]] = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)
_RSA_ALGORITHMS: Final[tuple[str, ...]] = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512")
_EC_ALGORITHMS: Final[dict[str, tuple[str, ...]]] = {
    "P-256": ("ES256",),
    "P-384": ("ES384",),
    "P-521": ("ES512",),
}

#: What the authorization request asks for. Group claims that need a scope are
#: configured at the identity provider against this client, not here: the
#: connection document names the claim, not the scope.
_SCOPE: Final[str] = "openid email profile"

__all__ = [
    "OIDC_CLOCK_SKEW_SECONDS",
    "OidcCompletion",
    "OidcError",
    "OidcExchangeError",
    "OidcMetadata",
    "OidcMetadataError",
    "OidcProvider",
    "OidcTokenError",
    "get_oidc_provider",
]


class OidcError(Exception):
    """A sign-in that cannot continue. ``stage`` is the coarse audit label.

    Never carries a value from the identity provider: the message is ours, the
    stage names where we were, and neither reaches the browser.
    """

    stage = "oidc"

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        if stage is not None:
            self.stage = stage


class OidcMetadataError(OidcError):
    """The discovery document or the key set could not be trusted."""

    stage = "metadata"


class OidcExchangeError(OidcError):
    """The authorization code could not be exchanged."""

    stage = "exchange"


class OidcTokenError(OidcError):
    """The ID token did not verify, or did not carry what this flow asked for."""

    stage = "id_token"


@dataclass(frozen=True, slots=True)
class OidcMetadata:
    """The discovery document and its key set, fetched and cached together."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    signing_algorithms: tuple[str, ...]
    token_auth_method: str
    keys: tuple[dict[str, Any], ...]

    def key_for(self, kid: str | None) -> dict[str, Any] | None:
        """The one candidate key, or ``None`` when the set does not name it.

        A ``kid``-less token is only served when the set holds exactly one key;
        guessing among several is how a retired key gets used.
        """

        if kid is not None:
            for key in self.keys:
                if key.get("kid") == kid:
                    return key
            return None
        return self.keys[0] if len(self.keys) == 1 else None


@dataclass(frozen=True, slots=True)
class OidcCompletion:
    """What a completed round trip proved: an identity, and when it was proved.

    ``auth_time`` is the identity provider's own statement of when the person
    last authenticated; the step-up flow requires it, ordinary sign-in ignores
    it. The ID token itself never leaves this module.
    """

    identity: ExternalIdentity
    auth_time: int | None


@dataclass(slots=True)
class _CacheEntry:
    metadata: OidcMetadata
    #: When the key set this entry holds was last fetched *successfully*. Both
    #: the positive lifetime and the 24-hour trust ceiling are measured from it,
    #: which is why a failed refresh must never move it.
    fetched_at: float
    forced_refresh_at: float
    #: When a refresh last failed, or ``0.0`` since the last success. Held
    #: apart from ``fetched_at`` so backing off a broken discovery endpoint
    #: cannot extend how long keys of unknown age stay trusted.
    failed_refresh_at: float = 0.0

    def is_fresh(self, now: float) -> bool:
        return now - self.fetched_at < _METADATA_TTL_SECONDS

    def is_trusted(self, now: float) -> bool:
        return now - self.fetched_at <= _STALE_METADATA_MAX_AGE_SECONDS

    def is_backing_off(self, now: float) -> bool:
        return self.failed_refresh_at > 0.0 and now - self.failed_refresh_at < _FAILED_REFRESH_BACKOFF_SECONDS

    def serves(self, now: float) -> bool:
        """Whether this entry answers without attempting a fetch first.

        Either it is inside its positive lifetime, or its last refresh failed
        recently and it is still inside the trust ceiling -- in which case a
        fetch would only queue another timeout behind the cache lock to reach
        the same stale answer.
        """

        return self.is_fresh(now) or (self.is_backing_off(now) and self.is_trusted(now))


# --- fetching -------------------------------------------------------------


async def _read_bounded(content: aiohttp.StreamReader) -> bytes:
    """Everything the response carries, up to one byte past the cap.

    ``StreamReader.read(n)`` answers with what has *arrived*, not with ``n``
    bytes: a discovery document split across two network writes comes back
    truncated from a single call and then fails to parse as JSON. Reading to
    EOF is what makes the parse deterministic; the cap still bounds it, because
    the loop stops as soon as it holds one byte more than the caller will
    accept, and the request's own total timeout still bounds the wait.
    """

    limit = _MAX_DOCUMENT_BYTES + 1
    chunks: list[bytes] = []
    received = 0
    while received < limit:
        chunk = await content.read(limit - received)
        if not chunk:
            break
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


async def _fetch_json(url: str, *, stage: str) -> dict[str, Any]:
    """One bounded, redirect-refusing, JSON-only GET against an operator URL."""

    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS)
    try:
        async with lease_http_session() as session:
            async with session.get(
                url,
                allow_redirects=False,
                timeout=timeout,
                headers={"Accept": "application/json"},
            ) as response:
                if response.status != 200:
                    # A redirect lands here too, deliberately: a redirect is how
                    # a host that passed validation becomes one that would not.
                    raise OidcMetadataError(f"fetch answered status {response.status}", stage=stage)
                if not _is_json(response.content_type):
                    raise OidcMetadataError("fetch answered a non-JSON body", stage=stage)
                body = await _read_bounded(response.content)
    except OidcError:
        raise
    except Exception as exc:  # network, TLS, timeout
        raise OidcMetadataError("fetch failed", stage=stage) from exc
    if len(body) > _MAX_DOCUMENT_BYTES:
        raise OidcMetadataError("fetch answered an oversized body", stage=stage)
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise OidcMetadataError("fetch answered invalid JSON", stage=stage) from exc
    if not isinstance(document, dict):
        raise OidcMetadataError("fetch answered a non-object body", stage=stage)
    return document


def _is_json(content_type: str | None) -> bool:
    value = (content_type or "").casefold()
    return value == "application/json" or value.endswith("+json")


def _endpoint(document: dict[str, Any], field: str) -> str:
    raw = document.get(field)
    if not isinstance(raw, str):
        raise OidcMetadataError(f"discovery document has no {field}")
    try:
        # The advertised endpoints pass the same scheme and host rules as the
        # issuer, but are NOT required to share its origin: Google Workspace
        # publishes its keys on a different host from its issuer, and a rule
        # operators must switch off for a target identity provider is not a
        # rule. The ``issuer``-claim equality check below is what makes the
        # document trustworthy enough to name them.
        return validate_https_url(raw, field=field)
    except Exception as exc:
        raise OidcMetadataError(f"discovery document's {field} is not a usable URL") from exc


async def fetch_metadata(config: OidcProviderConfig) -> OidcMetadata:
    document = await _fetch_json(config.discovery_url, stage="discovery")
    if document.get("issuer") != config.issuer:
        # OIDC Discovery mandates this equality; it is what binds the returned
        # document to the issuer the operator typed.
        raise OidcMetadataError("discovery document names another issuer")
    # Every advertised endpoint is validated before any of them is used.
    jwks_uri = _endpoint(document, "jwks_uri")
    authorization_endpoint = _endpoint(document, "authorization_endpoint")
    token_endpoint = _endpoint(document, "token_endpoint")
    advertised = document.get("id_token_signing_alg_values_supported")
    signing_algorithms = (
        tuple(value for value in advertised if isinstance(value, str)) if isinstance(advertised, list) else ()
    )
    auth_methods = document.get("token_endpoint_auth_methods_supported")
    token_auth_method = (
        "client_secret_post"
        if isinstance(auth_methods, list) and "client_secret_post" in auth_methods
        else "client_secret_basic"
    )
    keys = _signing_keys(await _fetch_json(jwks_uri, stage="jwks"))
    return OidcMetadata(
        issuer=config.issuer,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        jwks_uri=jwks_uri,
        signing_algorithms=signing_algorithms,
        token_auth_method=token_auth_method,
        keys=keys,
    )


def _signing_keys(document: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_keys = document.get("keys")
    if not isinstance(raw_keys, list):
        raise OidcMetadataError("key set has no keys", stage="jwks")
    if len(raw_keys) > _MAX_JWKS_KEYS:
        raise OidcMetadataError("key set is too large", stage="jwks")
    keys = [key for key in raw_keys if isinstance(key, dict) and _is_verification_key(key)]
    if not keys:
        raise OidcMetadataError("key set holds no usable verification key", stage="jwks")
    return tuple(keys)


def _is_verification_key(key: dict[str, Any]) -> bool:
    if key.get("kty") not in {"RSA", "EC"}:
        return False
    if key.get("use") not in {None, "sig"}:
        return False
    key_ops = key.get("key_ops")
    if isinstance(key_ops, list) and "verify" not in key_ops:
        return False
    # A key set that ships private material is malformed; refusing it keeps a
    # private exponent from ever reaching the JWK parser.
    return "d" not in key


# --- the cache ------------------------------------------------------------


class OidcMetadataCache:
    """One entry per provider *configuration*, so a configuration write invalidates itself.

    The key carries a digest of the connection document, not the row's
    ``updated_at``: that column has one-second resolution on SQLite, so two
    writes inside the same second would share a key and a repointed provider
    would keep serving the previous issuer's endpoints -- which is how the new
    client secret ends up posted to the old identity provider's token endpoint,
    a leg that runs *before* the issuer check could refuse the answer. A digest
    of what the flow will actually use has no such window.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _CacheEntry] = {}
        self._lock = anyio.Lock()

    @staticmethod
    def _key(provider: DashboardAuthProvider, config: OidcProviderConfig) -> tuple[str, str]:
        return (provider.id, oidc_config_fingerprint(config))

    async def get(self, provider: DashboardAuthProvider, config: OidcProviderConfig) -> OidcMetadata:
        key = self._key(provider, config)
        now = time.monotonic()
        entry = self._entries.get(key)
        if entry is not None and entry.serves(now):
            return entry.metadata
        async with self._lock:
            now = time.monotonic()
            entry = self._entries.get(key)
            if entry is not None and entry.serves(now):
                return entry.metadata
            return await self._fetch_locked(key, config, entry, now)

    async def refresh_for_kid(
        self, provider: DashboardAuthProvider, config: OidcProviderConfig, kid: str | None
    ) -> OidcMetadata | None:
        """One out-of-band refresh for an unknown ``kid``, or ``None`` while cooling down.

        Without the cooldown, ID tokens carrying random ``kid`` values would be
        a free request amplifier aimed at the identity provider; with it, a
        published rotation still converges on the first token that uses the new
        key.
        """

        key = self._key(provider, config)
        async with self._lock:
            now = time.monotonic()
            entry = self._entries.get(key)
            if entry is not None:
                # Whoever held the lock before this caller may have refreshed
                # for the very same rotation. Answering from what is cached now
                # -- rather than from the cooldown, which that refresh set --
                # is the difference between two callbacks meeting a rotated key
                # and only the first of them signing in.
                cached = entry.metadata.key_for(kid)
                if cached is not None:
                    return entry.metadata
                if now - entry.forced_refresh_at < _REFRESH_COOLDOWN_SECONDS:
                    return None
                entry.forced_refresh_at = now
            try:
                metadata = await self._fetch_locked(key, config, entry, now, forced=True)
            except OidcError:
                return None
        return metadata if metadata.key_for(kid) is not None else None

    async def _fetch_locked(
        self,
        key: tuple[str, str],
        config: OidcProviderConfig,
        entry: _CacheEntry | None,
        now: float,
        *,
        forced: bool = False,
    ) -> OidcMetadata:
        try:
            metadata = await fetch_metadata(config)
        except OidcError:
            if entry is not None and entry.is_trusted(now):
                # Serving the last good key set through a brief outage keeps
                # this software from being less available than the identity
                # provider it depends on. The cap is what keeps a key revoked
                # after a compromise from being trusted indefinitely, and the
                # backoff below is what keeps the outage from costing every
                # request a fresh ten-second timeout behind this lock -- the
                # stamp goes on its own field so the cap still counts from the
                # last *successful* fetch.
                entry.failed_refresh_at = now
                logger.warning("OIDC metadata refresh failed; serving the cached key set")
                return entry.metadata
            raise
        # One entry per provider, not per configuration: the key carries the
        # document's digest so a write invalidates itself, and dropping the
        # older entries of the same provider keeps a long-lived process from
        # accumulating one entry per configuration edit it ever saw.
        self._entries = {other: value for other, value in self._entries.items() if other[0] != key[0]}
        self._entries[key] = _CacheEntry(
            metadata=metadata,
            fetched_at=now,
            forced_refresh_at=now if forced else (entry.forced_refresh_at if entry is not None else 0.0),
        )
        return metadata

    def clear(self) -> None:
        self._entries.clear()


_metadata_cache = OidcMetadataCache()


def get_oidc_metadata_cache() -> OidcMetadataCache:
    return _metadata_cache


# --- verification ---------------------------------------------------------


def _permitted_algorithms(key: dict[str, Any], advertised: tuple[str, ...]) -> tuple[str, ...]:
    """What this key may have signed with -- derived from the key, never from the token."""

    if key.get("kty") == "RSA":
        family: tuple[str, ...] = _RSA_ALGORITHMS
    elif key.get("kty") == "EC":
        curve = key.get("crv")
        family = _EC_ALGORITHMS.get(curve, ()) if isinstance(curve, str) else ()
    else:
        family = ()
    declared = key.get("alg")
    if isinstance(declared, str):
        family = tuple(name for name in family if name == declared)
    permitted = tuple(name for name in family if name in _SIGNING_ALGORITHMS)
    if advertised:
        permitted = tuple(name for name in permitted if name in advertised)
    return permitted


def _public_key(key: dict[str, Any]) -> RSAPublicKey | EllipticCurvePublicKey:
    serialized = json.dumps(key)
    try:
        if key.get("kty") == "RSA":
            loaded = RSAAlgorithm.from_jwk(serialized)
        else:
            loaded = ECAlgorithm.from_jwk(serialized)
    except Exception as exc:
        raise OidcTokenError("signing key could not be parsed") from exc
    if not isinstance(loaded, RSAPublicKey | EllipticCurvePublicKey):
        raise OidcTokenError("signing key is not a public key")
    return loaded


def verify_id_token(
    id_token: str,
    *,
    config: OidcProviderConfig,
    metadata: OidcMetadata,
    key: dict[str, Any],
    nonce_hash: str,
    flow_created_at: int,
    now: int,
) -> dict[str, Any]:
    """The verified claims, or :class:`OidcTokenError`. Nothing else reads a claim."""

    algorithms = _permitted_algorithms(key, metadata.signing_algorithms)
    if not algorithms:
        raise OidcTokenError("no permitted algorithm for the selected signing key")
    try:
        claims = jwt.decode(
            id_token,
            key=_public_key(key),
            algorithms=list(algorithms),
            audience=config.client_id,
            issuer=config.issuer,
            leeway=OIDC_CLOCK_SKEW_SECONDS,
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
        )
    except OidcError:
        raise
    except Exception as exc:
        raise OidcTokenError("ID token did not verify") from exc

    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != config.client_id:
        raise OidcTokenError("ID token names several audiences without a matching azp")

    issued_at = claims.get("iat")
    if not isinstance(issued_at, int | float) or isinstance(issued_at, bool):
        raise OidcTokenError("ID token has no usable iat")
    if issued_at > now + OIDC_CLOCK_SKEW_SECONDS:
        raise OidcTokenError("ID token was issued too far in the future")
    # Independent of the skew tolerance: a token minted before this flow began
    # is not the token this flow asked for, however distant its expiry. The
    # flow lives ten minutes, so a token accepted here is minutes old.
    if issued_at < flow_created_at - OIDC_CLOCK_SKEW_SECONDS:
        raise OidcTokenError("ID token predates the flow it is answering")

    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not hmac.compare_digest(hash_flow_value(nonce), nonce_hash):
        raise OidcTokenError("ID token carries the wrong nonce")
    return claims


def hash_flow_value(value: str) -> str:
    """The only form a ``state`` or a ``nonce`` is stored or rate-limited in."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- claims to identity ---------------------------------------------------


def _groups(raw: object) -> tuple[str, ...]:
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    groups: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        folded = value.strip().casefold()
        if folded and folded not in groups:
            groups.append(folded)
    return tuple(groups)


def _claims_email_verified(claims: dict[str, Any]) -> bool:
    """Whether the identity provider *affirmed* the address, for a claim that is present.

    Only the JSON boolean ``true`` and the string ``"true"`` count. The string
    spelling is not tolerance for sloppiness: Google's documented ID token
    carries ``"email_verified": "true"``, Apple documents the claim as either a
    string or a boolean, and Cognito emits strings for attributes it maps from
    an upstream provider -- so an identity check that accepts only ``True``
    would drop the address of every legitimately verified user at three of the
    identity providers this feature exists for.

    Everything else present is false, including ``"false"``, ``0``, ``null`` and
    a container. This is the one claim in this module that used to fail open,
    and the value it guards is the one the resolver links accounts by.
    """

    value = claims.get("email_verified")
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().casefold() == "true"


def identity_from_claims(claims: dict[str, Any], config: OidcProviderConfig) -> ExternalIdentity | None:
    """``None`` when the token names nobody this system can hold as an identity."""

    subject = claims.get(config.subject_claim)
    # Verbatim: not case-folded (an OIDC ``sub`` is case-sensitive by
    # specification, and folding would merge two distinct subjects at an
    # identity provider that issues both spellings) and not trimmed either.
    # Trimming is the same defect one character over: an identity provider that
    # issues ``alice`` and ``<space>alice<space>`` as two subjects would have
    # them collapse onto one identity here, and the second person would be
    # signed into the first's account. Emptiness and length are *checked*
    # against the stripped and the raw value; neither check rewrites the
    # identifier that is looked up.
    if not isinstance(subject, str) or not subject.strip() or len(subject) > MAX_SUBJECT_LENGTH:
        return None

    email: str | None = None
    # ``email_verified`` present and not affirmative means the identity provider
    # is telling us the address is self-asserted. The resolver would use it for
    # e-mail linking, for the new account's address and for ``email_domain``
    # role rules -- the last of which is a role escalation. Absent is not false:
    # many providers omit the claim, and linking is off by default.
    if "email_verified" not in claims or _claims_email_verified(claims):
        candidate = claims.get(config.email_claim)
        if isinstance(candidate, str):
            normalized = normalize_email(candidate)
            email = normalized if normalized is not None and is_valid_email(normalized) else None

    raw_name = claims.get(config.name_claim)
    display_name = (
        raw_name.strip()[:MAX_DISPLAY_NAME_LENGTH] if isinstance(raw_name, str) and raw_name.strip() else None
    )

    return ExternalIdentity(
        provider=AuthProviderKind.OIDC.value,
        provider_key=DEFAULT_PROVIDER_KEY,
        subject=subject,
        email=email,
        display_name=display_name,
        groups=_groups(claims.get(config.groups_claim)),
    )


# --- the provider ---------------------------------------------------------


class OidcProvider:
    """Redirect-style sign-in at an operator-configured identity provider.

    ``resolve_identity`` returns nothing because a bare request carries no
    evidence this provider recognises; the round trip is ``begin_login`` (the
    authorization URL) and ``complete_login`` (the code exchange and the ID
    token), both driven by the public routes in ``dashboard_auth``.
    """

    kind = AuthProviderKind.OIDC
    provider_key = DEFAULT_PROVIDER_KEY

    def resolve_identity(self, request: Request) -> ExternalIdentity | None:
        del request
        return None

    async def begin_login(
        self,
        *,
        provider: DashboardAuthProvider,
        config: OidcProviderConfig,
        state: str,
        nonce: str,
        code_challenge: str,
        prompt_login: bool = False,
    ) -> str:
        """The authorization URL to send the browser to. PKCE is always S256."""

        metadata = await get_oidc_metadata_cache().get(provider, config)
        parameters = [
            ("response_type", "code"),
            ("client_id", config.client_id),
            ("redirect_uri", config.redirect_uri),
            ("scope", _SCOPE),
            ("state", state),
            ("nonce", nonce),
            ("code_challenge", code_challenge),
            ("code_challenge_method", "S256"),
        ]
        if prompt_login:
            # Re-authentication as a step-up factor: ask for a fresh sign-in and
            # for the identity provider to say when it happened.
            parameters += [("prompt", "login"), ("max_age", "0")]
        separator = "&" if "?" in metadata.authorization_endpoint else "?"
        return metadata.authorization_endpoint + separator + urlencode(parameters)

    async def complete_login(
        self,
        *,
        provider: DashboardAuthProvider,
        config: OidcProviderConfig,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        nonce_hash: str,
        flow_created_at: int,
        now: int | None = None,
    ) -> OidcCompletion:
        """Exchange the code once, verify the ID token, and build the identity."""

        metadata = await get_oidc_metadata_cache().get(provider, config)
        id_token = await self._exchange(
            config=config,
            metadata=metadata,
            code=code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
        )
        key, metadata = await self._select_key(provider, config, metadata, id_token)
        claims = verify_id_token(
            id_token,
            config=config,
            metadata=metadata,
            key=key,
            nonce_hash=nonce_hash,
            flow_created_at=flow_created_at,
            now=int(time.time()) if now is None else now,
        )
        identity = identity_from_claims(claims, config)
        if identity is None:
            raise OidcTokenError("ID token names no usable subject")
        auth_time = claims.get("auth_time")
        return OidcCompletion(
            identity=identity,
            auth_time=int(auth_time)
            if isinstance(auth_time, int | float) and not isinstance(auth_time, bool)
            else None,
        )

    async def _select_key(
        self,
        provider: DashboardAuthProvider,
        config: OidcProviderConfig,
        metadata: OidcMetadata,
        id_token: str,
    ) -> tuple[dict[str, Any], OidcMetadata]:
        try:
            header = jwt.get_unverified_header(id_token)
        except Exception as exc:
            raise OidcTokenError("ID token header could not be read") from exc
        raw_kid = header.get("kid")
        kid = raw_kid if isinstance(raw_kid, str) else None
        key = metadata.key_for(kid)
        if key is None:
            refreshed = await get_oidc_metadata_cache().refresh_for_kid(provider, config, kid)
            if refreshed is None:
                raise OidcTokenError("ID token names an unknown signing key")
            metadata = refreshed
            key = metadata.key_for(kid)
            if key is None:  # pragma: no cover - refresh_for_kid already checked
                raise OidcTokenError("ID token names an unknown signing key")
        return key, metadata

    async def _exchange(
        self,
        *,
        config: OidcProviderConfig,
        metadata: OidcMetadata,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> str:
        """One token request, on the non-retrying session.

        The flow row is already consumed by the time this runs, so a replayed
        authorization code never reaches the token endpoint twice through this
        system -- and for the same reason this request is never retried.
        """

        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": config.client_id,
            "code_verifier": code_verifier,
        }
        headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
        if metadata.token_auth_method == "client_secret_post":
            form["client_secret"] = config.client_secret
        else:
            # RFC 6749 §2.3.1: both halves are form-urlencoded before they are
            # joined and base64ed. Assembled from a list so no line of this
            # file ever reads as a literal credential pair.
            parts = [quote(config.client_id, safe=""), quote(config.client_secret, safe="")]
            headers["Authorization"] = "Basic " + b64encode(":".join(parts).encode()).decode("ascii")
        timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS)
        try:
            async with lease_http_session() as session:
                async with session.post(
                    metadata.token_endpoint,
                    data=form,
                    headers=headers,
                    allow_redirects=False,
                    timeout=timeout,
                ) as response:
                    status = response.status
                    # Read to EOF, not "whatever arrived": a token response
                    # split across two writes would otherwise fail to parse and
                    # burn an authorization code that was perfectly good, and
                    # the code cannot be presented twice.
                    body = await _read_bounded(response.content)
        except Exception as exc:
            raise OidcExchangeError("token request failed") from exc
        if status != 200 or len(body) > _MAX_DOCUMENT_BYTES:
            # The identity provider's ``error_description`` is deliberately not
            # read: nothing from it reaches a response, a log line or an audit
            # row, and neither does the request body that carried the secret.
            raise OidcExchangeError("token request was refused")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise OidcExchangeError("token response was not JSON") from exc
        id_token = payload.get("id_token") if isinstance(payload, dict) else None
        if not isinstance(id_token, str) or not id_token:
            raise OidcExchangeError("token response carried no ID token")
        return id_token


_provider = OidcProvider()


def get_oidc_provider() -> OidcProvider:
    return _provider
