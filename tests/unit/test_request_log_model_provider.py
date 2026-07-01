from __future__ import annotations

from app.db.models import RequestLog


def test_request_log_has_provider_column_nullable() -> None:
    column = RequestLog.__table__.c.provider
    assert column is not None
    assert column.nullable is True