"""Pure crypto/claim helpers for the Claude OAuth link flow.

Two responsibilities, both stateless:

- PKCE pair generation (S256 only; RFC 7636).
- ``id_token`` decode + ClaudeOauthClaims extraction.

The id_token is decoded without signature verification. Matches the project's
existing convention (``app/core/auth/models.py::extract_id_token_claims``):
the only consumer of the claims is the local account-creation path, and the
HTTPS transport + the PKCE binding make third-party injection infeasible.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` for PKCE S256.

    Verifier uses ``secrets.token_urlsafe(64)`` (~86 chars, comfortably above
    the RFC 7636 §4.1 minimum of 43).
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@dataclass(frozen=True)
class ClaudeOauthClaims:
    """Typed claim mapping extracted from the Anthropic ``id_token``."""

    claude_account_uuid: str
    user_email: str | None = None
    user_organization_uuid: str | None = None
    scopes: list[str] | None = None
    raw_claims: dict[str, Any] = field(default_factory=dict)


class ClaudeOauthIdTokenError(Exception):
    """Typed error raised by :func:`decode_id_token`.

    ``code`` is one of the documented flow-level error codes:

    - ``id_token_malformed``       — JWT structure invalid / unparseable.
    - ``id_token_claims_incomplete`` — JSON parsed but required field missing.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def decode_id_token(jwt: str) -> ClaudeOauthClaims:
    """Decode a JWT ``id_token`` (no signature verification) and map claims.

    Required field: ``claude_account_uuid``. Optional: ``user_email``,
    ``user_organization_uuid``, ``scopes``.
    """
    if not isinstance(jwt, str) or jwt.count(".") != 2:
        raise ClaudeOauthIdTokenError("id_token_malformed", "id_token is not a 3-segment JWT")

    try:
        _header_b64, payload_b64, _sig = jwt.split(".")
        # Add padding back for base64 decode.
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload_json = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ClaudeOauthIdTokenError("id_token_malformed", f"id_token decode failed: {exc}") from exc

    try:
        claims = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ClaudeOauthIdTokenError("id_token_malformed", f"id_token payload not JSON: {exc}") from exc

    if not isinstance(claims, dict):
        raise ClaudeOauthIdTokenError("id_token_malformed", "id_token payload is not an object")

    claude_account_uuid = _first_present(claims, ("account_id",))
    if not claude_account_uuid:
        sub = claims.get("sub")
        if isinstance(sub, str) and _UUID_RE.match(sub):
            claude_account_uuid = sub
    if not claude_account_uuid:
        # Try namespaced claims.
        namespaced_account = claims.get("https://api.anthropic.com/account_id")
        if isinstance(namespaced_account, str) and namespaced_account:
            claude_account_uuid = namespaced_account

    if not claude_account_uuid or not isinstance(claude_account_uuid, str):
        raise ClaudeOauthIdTokenError(
            "id_token_claims_incomplete",
            "id_token does not contain a usable claude_account_uuid claim",
        )

    user_email = _first_present(claims, ("email", "https://api.anthropic.com/email"))
    user_org = _first_present(
        claims,
        ("organization_id", "org_id", "https://api.anthropic.com/organization_id"),
    )

    scope_raw = claims.get("scope") or claims.get("scp")
    scopes: list[str] | None = None
    if isinstance(scope_raw, str) and scope_raw.strip():
        scopes = [s for s in scope_raw.split() if s]

    return ClaudeOauthClaims(
        claude_account_uuid=claude_account_uuid,
        user_email=user_email if isinstance(user_email, str) else None,
        user_organization_uuid=user_org if isinstance(user_org, str) else None,
        scopes=scopes,
        raw_claims=claims,
    )


def _first_present(claims: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = claims.get(k)
        if isinstance(v, str) and v:
            return v
    return None
