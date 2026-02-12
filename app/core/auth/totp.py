from __future__ import annotations

import base64
import hmac
from dataclasses import dataclass
from time import time

import pyotp

_TOTP_PERIOD_SECONDS = 30
_TOTP_DIGITS = 6


@dataclass(frozen=True, slots=True)
class TotpVerificationResult:
    is_valid: bool
    matched_step: int | None


def generate_totp_secret(bytes_length: int = 20) -> str:
    if bytes_length <= 0:
        raise ValueError("bytes_length must be positive")
    chars = max(32, ((bytes_length * 8) + 4) // 5)
    return pyotp.random_base32(length=chars)


def build_otpauth_uri(secret: str, *, account_name: str, issuer: str) -> str:
    normalized_secret = _normalize_secret(secret)
    return _build_totp(normalized_secret).provisioning_uri(name=account_name, issuer_name=issuer)


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
    if len(normalized_code) != _TOTP_DIGITS:
        return TotpVerificationResult(is_valid=False, matched_step=None)
    totp = _build_totp(normalized_secret)

    current_step = _time_step(now_epoch=now_epoch)
    for offset in range(-window, window + 1):
        step = current_step + offset
        if last_verified_step is not None and step <= last_verified_step:
            continue
        expected = totp.at(step * _TOTP_PERIOD_SECONDS)
        if hmac.compare_digest(expected, normalized_code):
            return TotpVerificationResult(is_valid=True, matched_step=step)
    return TotpVerificationResult(is_valid=False, matched_step=None)


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


def _build_totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret, digits=_TOTP_DIGITS, interval=_TOTP_PERIOD_SECONDS)
