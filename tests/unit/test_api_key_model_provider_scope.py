from __future__ import annotations

from app.db.models import ApiKey


def test_api_key_has_provider_scope_column() -> None:
    column = ApiKey.__table__.c.provider_scope
    assert column is not None
    assert column.nullable is False
    # Server default MUST be 'codex' so existing rows backfill on migration.
    default = column.server_default
    assert default is not None
    assert "codex" in str(default.arg).lower()
