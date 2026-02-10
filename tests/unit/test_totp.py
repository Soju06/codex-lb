from __future__ import annotations

import base64

import pytest

from app.core.auth.totp import generate_totp_code, generate_totp_secret, verify_totp_code

pytestmark = pytest.mark.unit


def test_generate_totp_secret_is_valid_base32() -> None:
    secret = generate_totp_secret()
    assert secret
    assert secret == secret.upper()
    padding = "=" * (-len(secret) % 8)
    decoded = base64.b32decode(secret + padding, casefold=True)
    assert len(decoded) == 20


def test_verify_totp_blocks_replayed_time_step() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    epoch = 1_700_000_000
    code = generate_totp_code(secret, now_epoch=epoch)

    first = verify_totp_code(secret, code, now_epoch=epoch, window=0)
    assert first.is_valid is True
    assert first.matched_step is not None

    replay = verify_totp_code(
        secret,
        code,
        now_epoch=epoch,
        window=0,
        last_verified_step=first.matched_step,
    )
    assert replay.is_valid is False
    assert replay.matched_step is None
