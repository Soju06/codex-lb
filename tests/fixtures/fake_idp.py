"""An identity provider that never touches the network.

The OIDC provider makes exactly three server-to-server calls -- discovery,
JWKS, token -- all through ``lease_http_session`` in
``app.core.auth.providers.oidc``. This fixture replaces that one seam with a
routing table, signs real RS256/ES256 tokens with real keys, and records every
request so a test can assert what did *not* happen (no token exchange on a
replayed state) as easily as what did.

Nothing here is a credential: the client id and the stand-in secret are
assembled from parts at import time so no line of this file reads like one.
"""

from __future__ import annotations

import base64
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

ISSUER = "https://idp.example.test"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = f"{ISSUER}/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/token"
JWKS_URI = f"{ISSUER}/jwks"
REDIRECT_URI = "https://dash.example.test/api/dashboard-auth/oidc/callback"

#: Assembled rather than written: a literal here would read as a credential
#: pair to a secret scanner, and this is a placeholder, not a secret.
CLIENT_ID = "-".join(["codex", "lb", "dashboard"])
CLIENT_SECRET = "-".join(["placeholder", "value", "for", "tests"])


def provider_config_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "issuer": ISSUER,
        "discovery_url": DISCOVERY_URL,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    }
    document.update(overrides)
    return document


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class SigningKey:
    kid: str
    algorithm: str
    private: Any
    public: Any

    @classmethod
    def rsa(cls, kid: str, algorithm: str = "RS256") -> SigningKey:
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return cls(kid=kid, algorithm=algorithm, private=private, public=private.public_key())

    @classmethod
    def ec(cls, kid: str) -> SigningKey:
        private = ec.generate_private_key(ec.SECP256R1())
        return cls(kid=kid, algorithm="ES256", private=private, public=private.public_key())

    def jwk(self, *, declare_algorithm: bool = True) -> dict[str, Any]:
        raw = RSAAlgorithm if self.algorithm.startswith(("RS", "PS")) else ECAlgorithm
        key = json.loads(raw.to_jwk(self.public))
        key["kid"] = self.kid
        key["use"] = "sig"
        if declare_algorithm:
            key["alg"] = self.algorithm
        return key


@dataclass(slots=True)
class FakeResponse:
    """A response whose body is delivered the way a real one is: in pieces.

    ``aiohttp.StreamReader.read(n)`` answers with what has *arrived*, never
    with a promise of ``n`` bytes, and returns empty at end of stream.
    ``chunk_size`` reproduces that: set it to split the body across several
    reads, leave it ``None`` to hand over everything a caller asks for at once.
    Either way the second read of an exhausted body is empty, so a reader that
    forgets to loop to EOF is caught here rather than in production.
    """

    status: int = 200
    content_type: str = "application/json"
    body: bytes = b"{}"
    chunk_size: int | None = None
    offset: int = 0

    async def __aenter__(self) -> FakeResponse:
        self.offset = 0
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    @property
    def content(self) -> FakeResponse:
        return self

    async def read(self, limit: int) -> bytes:
        size = limit if self.chunk_size is None else min(limit, self.chunk_size)
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def json_response(document: object, **kwargs: Any) -> FakeResponse:
    return FakeResponse(body=json.dumps(document).encode(), **kwargs)


@dataclass(slots=True)
class RecordedRequest:
    method: str
    url: str
    data: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    allow_redirects: bool | None = None


class FakeIdp:
    """A programmable identity provider plus the transport it answers on."""

    def __init__(self, *, keys: list[SigningKey] | None = None) -> None:
        self.keys: list[SigningKey] = keys or [SigningKey.rsa("key-1")]
        self.requests: list[RecordedRequest] = []
        #: URL -> response (or a callable, or an exception to raise).
        self.responses: dict[str, Any] = {}
        self.discovery_overrides: dict[str, Any] = {}
        self.token_response: Any = None
        self.fail_metadata = False
        self.metadata_fetches = 0

    # --- documents ---

    def discovery_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "issuer": ISSUER,
            "authorization_endpoint": AUTHORIZATION_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
            "jwks_uri": JWKS_URI,
            "id_token_signing_alg_values_supported": sorted({key.algorithm for key in self.keys}),
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
        }
        document.update(self.discovery_overrides)
        return document

    def jwks_document(self) -> dict[str, Any]:
        return {"keys": [key.jwk() for key in self.keys]}

    # --- tokens ---

    def id_token(
        self,
        *,
        nonce: str,
        subject: str = "alice-subject",
        key: SigningKey | None = None,
        claims: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        now: int | None = None,
        lifetime: int = 300,
    ) -> str:
        signer = key or self.keys[0]
        issued_at = int(time.time()) if now is None else now
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": subject,
            "iat": issued_at,
            "exp": issued_at + lifetime,
            "nonce": nonce,
        }
        payload.update(claims or {})
        payload = {name: value for name, value in payload.items() if value is not _ABSENT}
        return jwt.encode(
            payload,
            signer.private,
            algorithm=signer.algorithm,
            headers={"kid": signer.kid, **(headers or {})},
        )

    def unsigned_token(self, *, nonce: str, subject: str = "alice-subject") -> str:
        """``alg: none`` -- the oldest trick, assembled by hand because PyJWT will not sign it."""

        issued_at = int(time.time())
        header = {"alg": "none", "kid": self.keys[0].kid}
        payload = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": subject,
            "iat": issued_at,
            "exp": issued_at + 300,
            "nonce": nonce,
        }
        return f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(payload).encode())}."

    def mac_token(self, *, nonce: str, subject: str = "alice-subject") -> str:
        """RS256 key, HS256 header, the public key used as the MAC secret."""

        import hashlib
        import hmac as hmac_module

        from cryptography.hazmat.primitives import serialization

        public_pem = self.keys[0].public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        issued_at = int(time.time())
        header = {"alg": "HS256", "kid": self.keys[0].kid}
        payload = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": subject,
            "iat": issued_at,
            "exp": issued_at + 300,
            "nonce": nonce,
        }
        signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(payload).encode())}"
        signature = hmac_module.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
        return f"{signing_input}.{b64url(signature)}"

    # --- transport ---

    def _answer(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(
            RecordedRequest(
                method=method,
                url=url,
                data=dict(kwargs.get("data") or {}),
                headers=dict(kwargs.get("headers") or {}),
                allow_redirects=kwargs.get("allow_redirects"),
            )
        )
        override = self.responses.get(url)
        if override is not None:
            if isinstance(override, Exception):
                raise override
            return override() if callable(override) else override
        if url == DISCOVERY_URL:
            self.metadata_fetches += 1
            if self.fail_metadata:
                raise ConnectionError("identity provider unreachable")
            return json_response(self.discovery_document())
        if url == JWKS_URI:
            return json_response(self.jwks_document())
        if url == TOKEN_ENDPOINT:
            if isinstance(self.token_response, Exception):
                raise self.token_response
            if self.token_response is not None:
                return self.token_response
            return json_response({"access_token": b64url(b"opaque"), "token_type": "Bearer"})
        return FakeResponse(status=404, body=b"{}")

    def session(self) -> _FakeSession:
        return _FakeSession(self)

    def install(self, monkeypatch: Any) -> FakeIdp:
        """Replace the one outbound seam the provider uses."""

        import app.core.auth.providers.oidc as oidc_module

        @contextlib.asynccontextmanager
        async def _lease(session: Any = None) -> AsyncIterator[_FakeSession]:
            del session
            yield self.session()

        monkeypatch.setattr(oidc_module, "lease_http_session", _lease)
        oidc_module.get_oidc_metadata_cache().clear()
        return self

    # --- assertions helpers ---

    def token_requests(self) -> list[RecordedRequest]:
        return [request for request in self.requests if request.url == TOKEN_ENDPOINT]


class _FakeSession:
    def __init__(self, idp: FakeIdp) -> None:
        self._idp = idp

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._idp._answer("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._idp._answer("POST", url, **kwargs)


class _Absent:
    """Sentinel so a test can ask for a claim to be *missing*, not ``None``."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<absent>"


_ABSENT = _Absent()
ABSENT: Any = _ABSENT
