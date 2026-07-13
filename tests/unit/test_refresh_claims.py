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
    assert claimant_id.startswith(f"{get_settings().http_responses_session_bridge_instance_id}:"[:128])
    assert len(claimant_id) <= 128


def test_claim_ttl_must_cover_twice_the_refresh_timeout() -> None:
    with pytest.raises(ValidationError, match="token_refresh_claim_ttl_seconds"):
        Settings(token_refresh_timeout_seconds=8.0, token_refresh_claim_ttl_seconds=10.0)
    settings = Settings(token_refresh_timeout_seconds=8.0, token_refresh_claim_ttl_seconds=16.0)
    assert settings.token_refresh_claim_ttl_seconds == 16.0
