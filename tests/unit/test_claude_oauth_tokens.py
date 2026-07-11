"""Tests for ``app.modules.claude.oauth.tokens``.

Pure helpers:
- ``generate_pkce_pair()`` returns verifier + S256 challenge.
- ``decode_id_token(jwt) -> ClaudeOauthClaims`` extracts ClaudeOauthClaims
  with the documented priority chain and raises typed errors when required
  fields are missing or the JWT is malformed.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from app.modules.claude.oauth.tokens import (
    ClaudeOauthClaims,
    ClaudeOauthIdTokenError,
    decode_id_token,
    generate_pkce_pair,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def test_generate_pkce_pair_lengths() -> None:
    verifier, challenge = generate_pkce_pair()
    # Verifier is token_urlsafe(64) -> 86 chars.
    assert len(verifier) >= 43  # RFC 7636 §4.1 minimum
    # Challenge is base64url(sha256) without padding -> 43 chars.
    assert len(challenge) == 43


def test_generate_pkce_pair_challenge_is_s256_of_verifier() -> None:
    verifier, challenge = generate_pkce_pair()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_generate_pkce_pair_produces_unique_values() -> None:
    v1, _ = generate_pkce_pair()
    v2, _ = generate_pkce_pair()
    assert v1 != v2


# ---------------------------------------------------------------------------
# id_token decoding
# ---------------------------------------------------------------------------


def _make_jwt(payload: dict) -> str:
    """Encode a payload into a 3-segment JWT with fake header/sig."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"{header}.{body}.fakesig"


def test_decode_id_token_account_id_claim() -> None:
    jwt = _make_jwt(
        {
            "account_id": "acct-uuid-1",
            "email": "a@example.test",
            "organization_id": "org-uuid-1",
            "scope": "user:profile user:inference",
        }
    )
    claims = decode_id_token(jwt)
    assert isinstance(claims, ClaudeOauthClaims)
    assert claims.claude_account_uuid == "acct-uuid-1"
    assert claims.user_email == "a@example.test"
    assert claims.user_organization_uuid == "org-uuid-1"
    assert claims.scopes == ["user:profile", "user:inference"]


def test_decode_id_token_namespaced_claim_fallback() -> None:
    jwt = _make_jwt(
        {
            "https://api.anthropic.com/account_id": "acct-uuid-2",
            "https://api.anthropic.com/email": "b@example.test",
            "https://api.anthropic.com/organization_id": "org-uuid-2",
            "scp": "user:inference",
        }
    )
    claims = decode_id_token(jwt)
    assert claims.claude_account_uuid == "acct-uuid-2"
    assert claims.user_email == "b@example.test"
    assert claims.user_organization_uuid == "org-uuid-2"
    assert claims.scopes == ["user:inference"]


def test_decode_id_token_sub_falls_back_when_uuid_shaped() -> None:
    jwt = _make_jwt({"sub": "550e8400-e29b-41d4-a716-446655440000"})
    claims = decode_id_token(jwt)
    assert claims.claude_account_uuid == "550e8400-e29b-41d4-a716-446655440000"


def test_decode_id_token_sub_non_uuid_ignored() -> None:
    jwt = _make_jwt({"sub": "not-a-uuid-string"})
    with pytest.raises(ClaudeOauthIdTokenError) as exc_info:
        decode_id_token(jwt)
    assert exc_info.value.code == "id_token_claims_incomplete"


def test_decode_id_token_missing_required_field() -> None:
    jwt = _make_jwt({"email": "only@example.test"})
    with pytest.raises(ClaudeOauthIdTokenError) as exc_info:
        decode_id_token(jwt)
    assert exc_info.value.code == "id_token_claims_incomplete"


def test_decode_id_token_malformed_jwt() -> None:
    with pytest.raises(ClaudeOauthIdTokenError) as exc_info:
        decode_id_token("not.a.jwt.with.too.many.parts")
    assert exc_info.value.code == "id_token_malformed"


def test_decode_id_token_missing_uuid_raises_typed_error() -> None:
    jwt = _make_jwt({"account_id": ""})
    with pytest.raises(ClaudeOauthIdTokenError) as exc_info:
        decode_id_token(jwt)
    assert exc_info.value.code == "id_token_claims_incomplete"
