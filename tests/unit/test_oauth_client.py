from __future__ import annotations

import base64
import hashlib
import urllib.parse

import pytest

from app.core.clients.oauth import build_authorization_url, pkce_challenge

pytestmark = pytest.mark.unit


def test_pkce_challenge_matches_sha256():
    verifier = "test_verifier"
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert pkce_challenge(verifier) == expected


def test_build_authorization_url_contains_required_params():
    url = build_authorization_url(
        state="state_123",
        code_challenge="challenge_456",
        base_url="https://auth.openai.com",
        client_id="client_id",
        redirect_uri="http://localhost:1455/auth/callback",
        scope="openid profile email offline_access",
    )

    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.openai.com"
    assert parsed.path == "/oauth/authorize"

    query = urllib.parse.parse_qs(parsed.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client_id"]
    assert query["redirect_uri"] == ["http://localhost:1455/auth/callback"]
    assert query["scope"] == ["openid profile email offline_access"]
    assert query["code_challenge"] == ["challenge_456"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state_123"]
    assert query["id_token_add_organizations"] == ["true"]
    assert query["codex_cli_simplified_flow"] == ["true"]
    assert query["originator"] == ["codex_cli_rs"]
