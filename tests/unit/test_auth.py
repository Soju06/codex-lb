from __future__ import annotations

import base64
import json
import stat

import pytest
from cryptography.fernet import InvalidToken

from app.core.auth import claims_from_auth, extract_id_token_claims, parse_auth_json
from app.core.crypto import TokenEncryptor, get_or_create_key

pytestmark = pytest.mark.unit


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def test_extract_id_token_claims_valid_payload():
    payload = {"email": "user@example.com", "chatgpt_account_id": "acc_123"}
    token = _encode_jwt(payload)
    claims = extract_id_token_claims(token)
    assert claims.email == "user@example.com"
    assert claims.chatgpt_account_id == "acc_123"


def test_claims_from_auth_prefers_token_account_id():
    payload = {
        "email": "user@example.com",
        "chatgpt_account_id": "acc_payload",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    token = _encode_jwt(payload)
    auth_json = {
        "tokens": {
            "idToken": token,
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": "acc_explicit",
        },
        "lastRefreshAt": "2024-01-01T00:00:00Z",
    }
    auth = parse_auth_json(json.dumps(auth_json).encode("utf-8"))
    claims = claims_from_auth(auth)
    assert claims.account_id == "acc_explicit"
    assert claims.email == "user@example.com"
    assert claims.plan_type == "plus"


def test_key_file_permissions_and_reuse(temp_key_file):
    first = get_or_create_key()
    second = get_or_create_key()
    assert first == second
    mode = stat.S_IMODE(temp_key_file.stat().st_mode)
    assert mode == 0o600


def test_token_encryptor_round_trip():
    encryptor = TokenEncryptor()
    value = "secret-token"
    encrypted = encryptor.encrypt(value)
    assert encryptor.decrypt(encrypted) == value


def test_token_encryptor_invalid_token_raises():
    encryptor = TokenEncryptor()
    with pytest.raises(InvalidToken):
        encryptor.decrypt(b"not-a-token")
