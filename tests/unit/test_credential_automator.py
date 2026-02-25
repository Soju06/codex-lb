from __future__ import annotations

import base64
import hashlib
import json

import pytest

from app.modules.accounts.credential_automator import (
    CredentialAuthorizationError,
    _build_sentinel_header_value,
    _ensure_offline_access_scope,
    _extract_workspace_id_from_cookie_value,
    _parse_code_from_redirect,
    _pick_mfa_factor_id,
)

pytestmark = pytest.mark.unit


def _encode_base64url_json(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_parse_code_from_redirect_validates_state():
    code = _parse_code_from_redirect(
        "http://localhost:1455/auth/callback?code=abc123&state=state_ok",
        expected_state="state_ok",
    )
    assert code == "abc123"


def test_parse_code_from_redirect_rejects_state_mismatch():
    with pytest.raises(CredentialAuthorizationError, match="State mismatch"):
        _parse_code_from_redirect(
            "http://localhost:1455/auth/callback?code=abc123&state=other_state",
            expected_state="state_ok",
        )


def test_extract_workspace_prefers_personal_kind():
    payload = {
        "workspaces": [
            {"id": "ws_team", "kind": "team"},
            {"id": "ws_personal", "kind": "personal"},
        ]
    }
    raw_cookie = _encode_base64url_json(payload)
    assert _extract_workspace_id_from_cookie_value(raw_cookie) == "ws_personal"


def test_extract_workspace_reads_middle_segment_payload():
    payload = {"workspaces": [{"id": "ws_first", "kind": "team"}]}
    raw_cookie = f"header.{_encode_base64url_json(payload)}.sig"
    assert _extract_workspace_id_from_cookie_value(raw_cookie) == "ws_first"


def test_build_sentinel_header_value_contains_expected_hash():
    p_value = "test_payload_value"
    token = "sentinel_token"
    did = "device_id"
    header = _build_sentinel_header_value(
        p_value=p_value,
        token=token,
        device_id=did,
        flow="authorize_continue",
    )
    parsed = json.loads(header)
    expected_t = base64.urlsafe_b64encode(hashlib.sha256(p_value.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    assert parsed["p"] == p_value
    assert parsed["t"] == expected_t
    assert parsed["c"] == token
    assert parsed["id"] == did
    assert parsed["flow"] == "authorize_continue"


def test_pick_mfa_factor_id_requires_totp():
    factors = (
        {"id": "sms_1", "factor_type": "sms"},
        {"id": "totp_1", "factor_type": "totp"},
    )
    assert _pick_mfa_factor_id(factors, expected_type="totp") == "totp_1"

    with pytest.raises(CredentialAuthorizationError, match="MFA factor"):
        _pick_mfa_factor_id(({"id": "sms_1", "factor_type": "sms"},), expected_type="totp")


def test_ensure_offline_access_scope_appends_when_missing():
    scope = _ensure_offline_access_scope("openid profile email")
    assert scope == "openid profile email offline_access"


def test_ensure_offline_access_scope_keeps_existing_token():
    scope = _ensure_offline_access_scope("openid profile email offline_access")
    assert scope == "openid profile email offline_access"
