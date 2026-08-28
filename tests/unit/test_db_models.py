from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import Enum as SqlEnum

from app.db.models import Account, AccountStatus, ApiKeyLimit, LimitType, LimitWindow, new_codex_installation_id


def test_sqlalchemy_enums_use_string_values() -> None:
    account_status_type = cast(SqlEnum, Account.__table__.c.status.type)
    limit_type_type = cast(SqlEnum, ApiKeyLimit.__table__.c.limit_type.type)
    limit_window_type = cast(SqlEnum, ApiKeyLimit.__table__.c.limit_window.type)

    assert account_status_type.enums == [status.value for status in AccountStatus]
    assert limit_type_type.enums == [limit_type.value for limit_type in LimitType]
    assert limit_window_type.enums == [window.value for window in LimitWindow]


def test_account_codex_installation_id_has_uuid_default() -> None:
    column = Account.__table__.c.codex_installation_id
    default = column.default

    assert column.nullable is False
    assert default is not None
    assert callable(default.arg)
    generated = new_codex_installation_id()
    assert isinstance(generated, str)
    assert str(uuid.UUID(generated)) == generated
