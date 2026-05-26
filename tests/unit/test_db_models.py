from __future__ import annotations

from typing import cast

from sqlalchemy import Enum as SqlEnum

from app.db.models import Account, AccountStatus, ApiKeyLimit, LimitType, LimitWindow


def test_sqlalchemy_enums_use_string_values() -> None:
    account_status_type = cast(SqlEnum, Account.__table__.c.status.type)
    limit_type_type = cast(SqlEnum, ApiKeyLimit.__table__.c.limit_type.type)
    limit_window_type = cast(SqlEnum, ApiKeyLimit.__table__.c.limit_window.type)

    assert account_status_type.enums == [status.value for status in AccountStatus]
    assert limit_type_type.enums == [limit_type.value for limit_type in LimitType]
    assert limit_window_type.enums == [window.value for window in LimitWindow]


def test_account_proxy_columns_are_nullable_with_remote_dns_default_true() -> None:
    columns = Account.__table__.c
    nullable_proxy_columns = (
        "proxy_host",
        "proxy_port",
        "proxy_username",
        "proxy_password_encrypted",
        "proxy_label",
        "proxy_last_validated_at",
    )
    for column_name in nullable_proxy_columns:
        column = columns[column_name]
        assert column.nullable is True, f"{column_name} must be nullable"

    remote_dns = columns["proxy_remote_dns"]
    assert remote_dns.nullable is False, "proxy_remote_dns must be NOT NULL"
    # SQLAlchemy stores Python defaults for new ORM rows; the server_default
    # is what backfills existing rows during migration. Both must coerce to
    # True so a row that omits the field defaults to remote DNS resolution.
    assert remote_dns.default is not None
    assert bool(getattr(remote_dns.default, "arg", remote_dns.default)) is True
    assert remote_dns.server_default is not None
