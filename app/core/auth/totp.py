from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass
from time import time
from urllib.parse import quote


@dataclass(frozen=True, slots=True)
class TotpVerificationResult:
    is_valid: bool
    matched_step: int | None


def generate_totp_secret(bytes_length: int = 20) -> str:
    if bytes_length <= 0:
        raise ValueError("bytes_length must be positive")
    raw = secrets.token_bytes(bytes_length)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def build_otpauth_uri(secret: str, *, account_name: str, issuer: str) -> str:
    normalized_secret = _normalize_secret(secret)
    label = quote(f"{issuer}:{account_name}", safe="")
    issuer_quoted = quote(issuer, safe="")
    return f"otpauth://totp/{label}?secret={normalized_secret}&issuer={issuer_quoted}&algorithm=SHA1&digits=6&period=30"


def verify_totp_code(
    secret: str,
    code: str,
    *,
    window: int = 1,
    now_epoch: int | None = None,
    last_verified_step: int | None = None,
) -> TotpVerificationResult:
    if window < 0:
        raise ValueError("window must be non-negative")
    normalized_secret = _normalize_secret(secret)
    normalized_code = _normalize_code(code)
    if len(normalized_code) != 6:
        return TotpVerificationResult(is_valid=False, matched_step=None)

    current_step = _time_step(now_epoch=now_epoch)
    for offset in range(-window, window + 1):
        step = current_step + offset
        if last_verified_step is not None and step <= last_verified_step:
            continue
        expected = _generate_code_for_step(normalized_secret, step)
        if hmac.compare_digest(expected, normalized_code):
            return TotpVerificationResult(is_valid=True, matched_step=step)
    return TotpVerificationResult(is_valid=False, matched_step=None)


def generate_totp_code(secret: str, *, now_epoch: int | None = None) -> str:
    normalized_secret = _normalize_secret(secret)
    return _generate_code_for_step(normalized_secret, _time_step(now_epoch=now_epoch))


def _normalize_secret(secret: str) -> str:
    compact = "".join(secret.split()).upper()
    if not compact:
        raise ValueError("secret is required")
    padding = "=" * (-len(compact) % 8)
    try:
        base64.b32decode(compact + padding, casefold=True)
    except Exception as exc:
        raise ValueError("Invalid TOTP secret") from exc
    return compact


def _normalize_code(code: str) -> str:
    return "".join(ch for ch in code if ch.isdigit())


def _time_step(*, now_epoch: int | None = None, period_seconds: int = 30) -> int:
    timestamp = int(time()) if now_epoch is None else int(now_epoch)
    return timestamp // period_seconds


def _generate_code_for_step(secret: str, step: int) -> str:
    padding = "=" * (-len(secret) % 8)
    key = base64.b32decode(secret + padding, casefold=True)
    msg = struct.pack(">Q", step)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"
