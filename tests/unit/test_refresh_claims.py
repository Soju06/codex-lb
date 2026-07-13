from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Dialect

from app.core.config.settings import Settings
from app.core.utils.time import utcnow
from app.modules.accounts.refresh_claims import (
    build_refresh_claim_upsert,
    default_refresh_claimant_id,
)

pytestmark = pytest.mark.unit


def _compile(dialect_name: str, dialect: Dialect) -> str:
    now = utcnow()
    stmt = build_refresh_claim_upsert(
        dialect_name=dialect_name,
        account_id="acc_1",
        claimed_by="replica-a",
        now=now,
        claim_expires_at=now,
    )
    return str(stmt.compile(dialect=dialect))


def test_claim_upsert_compiles_for_postgresql() -> None:
    sql = _compile("postgresql", postgresql.dialect())
    assert "INSERT INTO account_refresh_claims" in sql
    assert "ON CONFLICT (account_id) DO UPDATE" in sql
    assert "claim_expires_at <" in sql
    assert "claimed_by =" in sql
    assert "RETURNING account_refresh_claims.account_id" in sql


def test_claim_upsert_compiles_for_sqlite() -> None:
    sql = _compile("sqlite", sqlite.dialect())
    assert "INSERT INTO account_refresh_claims" in sql
    assert "ON CONFLICT (account_id) DO UPDATE" in sql
    assert "claim_expires_at <" in sql
    assert "RETURNING account_id" in sql


def test_claim_upsert_rejects_unknown_dialect() -> None:
    now = utcnow()
    with pytest.raises(RuntimeError):
        build_refresh_claim_upsert(
            dialect_name="mysql",
            account_id="acc_1",
            claimed_by="replica-a",
            now=now,
            claim_expires_at=now,
        )


def test_default_claimant_id_includes_instance_id_and_fits_column() -> None:
    from app.core.config.settings import get_settings

    claimant_id = default_refresh_claimant_id()
    assert claimant_id.startswith(f"{get_settings().http_responses_session_bridge_instance_id}:")
    assert len(claimant_id) <= 128


def test_long_instance_id_truncates_base_and_preserves_process_and_owner_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a 128+ char instance id used to swallow the per-process
    suffix, collapsing all workers of one replica into a single claimant (the
    re-entrant claim upsert then granted the claim to several processes). The
    claimant id must also reserve room for the per-refresh owner suffix so the
    composed ``claimed_by`` fits the column without truncating either suffix."""
    from app.core.config.settings import get_settings
    from app.modules.accounts.refresh_claims import (
        _CLAIMANT_ID_MAX_LEN,
        _PROCESS_SUFFIX,
        _compose_claimed_by,
    )

    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "i" * 200)
    get_settings.cache_clear()
    try:
        claimant_id = default_refresh_claimant_id()
    finally:
        get_settings.cache_clear()

    assert len(claimant_id) == _CLAIMANT_ID_MAX_LEN
    assert claimant_id.endswith(f":{_PROCESS_SUFFIX}")
    # Composing with a full-length (64 hex) owner fingerprint still fits 128 and
    # preserves both the process suffix and the owner discriminator.
    composed = _compose_claimed_by(claimant_id, "f" * 64)
    assert len(composed) <= 128
    assert f":{_PROCESS_SUFFIX}" in composed


def test_compose_claimed_by_distinguishes_owners_and_preserves_owner_token() -> None:
    """Two owners on one claimant must yield distinct ``claimed_by`` values with
    the owner token preserved in full even when the claimant fills the column."""
    from app.modules.accounts.refresh_claims import _CLAIM_OWNER_TOKEN_LEN, _compose_claimed_by

    owner_a = "a" * 40
    owner_b = "b" * 40
    long_claimant = "c" * 200

    composed_a = _compose_claimed_by(long_claimant, owner_a)
    composed_b = _compose_claimed_by(long_claimant, owner_b)

    assert composed_a != composed_b
    assert len(composed_a) <= 128
    assert composed_a.endswith("a" * _CLAIM_OWNER_TOKEN_LEN)
    assert composed_b.endswith("b" * _CLAIM_OWNER_TOKEN_LEN)


def test_claim_ttl_must_cover_admission_wait_plus_twice_the_refresh_timeout() -> None:
    # The claim is held across the refresh-admission wait AND the OAuth
    # exchange, so a TTL sized only around the HTTP timeout is rejected.
    with pytest.raises(ValidationError, match="token_refresh_claim_ttl_seconds"):
        Settings(
            token_refresh_timeout_seconds=8.0,
            proxy_admission_wait_timeout_seconds=10.0,
            token_refresh_claim_ttl_seconds=16.0,
        )
    settings = Settings(
        token_refresh_timeout_seconds=8.0,
        proxy_admission_wait_timeout_seconds=10.0,
        token_refresh_claim_ttl_seconds=26.0,
    )
    assert settings.token_refresh_claim_ttl_seconds == 26.0
