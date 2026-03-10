from __future__ import annotations

from app.db.models import Account, AccountStatus, ApiKeyLimit, LimitType, LimitWindow


def test_sqlalchemy_enums_use_string_values() -> None:
    assert Account.__table__.c.status.type.enums == [status.value for status in AccountStatus]
    assert ApiKeyLimit.__table__.c.limit_type.type.enums == [limit_type.value for limit_type in LimitType]
    assert ApiKeyLimit.__table__.c.limit_window.type.enums == [window.value for window in LimitWindow]
