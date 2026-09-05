from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy import Table, create_engine, inspect
from sqlalchemy.orm import Session, class_mapper
from sqlalchemy.orm.exc import DetachedInstanceError

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, Base, UsageHistory
from app.db.snapshot import clone_row

_ACCOUNT_COLUMN_KEYS = [attr.key for attr in class_mapper(Account).column_attrs]


def _make_account(**overrides: object) -> Account:
    encryptor = TokenEncryptor()
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": "acc-snapshot",
        "chatgpt_account_id": "workspace-acc-snapshot",
        "chatgpt_user_id": "user-acc-snapshot",
        "codex_installation_id": "install-acc-snapshot",
        "email": "snapshot@example.com",
        "alias": "snap",
        "workspace_id": "ws-1",
        "workspace_label": "Workspace",
        "seat_type": "member",
        "plan_type": "plus",
        "access_token_encrypted": encryptor.encrypt("access"),
        "refresh_token_encrypted": encryptor.encrypt("refresh"),
        "id_token_encrypted": encryptor.encrypt("id"),
        "last_refresh": now,
        "created_at": now,
        "status": AccountStatus.ACTIVE,
        "deactivation_reason": None,
        "reset_at": 123,
        "blocked_at": None,
    }
    values.update(overrides)
    return Account(**values)


def _legacy_clone(account: Account) -> Account:
    data = {column.name: getattr(account, column.name) for column in Account.__table__.columns}
    return Account(**data)


def _column_values(row: object) -> dict[str, object]:
    return {attr.key: getattr(row, attr.key) for attr in class_mapper(type(row)).column_attrs}


def test_clone_row_copies_every_column_attribute_and_matches_legacy_constructor_clone() -> None:
    account = _make_account()

    clone = clone_row(account)

    assert clone is not account
    assert type(clone) is Account
    assert _column_values(clone) == _column_values(account)
    assert _column_values(clone) == _column_values(_legacy_clone(account))
    assert set(inspect(clone).dict) >= set(_ACCOUNT_COLUMN_KEYS)


def test_clone_row_is_transient_and_writes_do_not_leak_to_source() -> None:
    account = _make_account()

    clone = clone_row(account)
    state = inspect(clone)
    assert state.transient is True
    assert state.session is None
    assert state.modified is False

    clone.status = AccountStatus.PAUSED
    clone.reset_at = 999
    assert account.status is AccountStatus.ACTIVE
    assert account.reset_at == 123
    # Instrumentation still tracks writes on the clone's fresh InstanceState.
    assert inspect(clone).modified is True
    assert inspect(clone).attrs.status.history.added == [AccountStatus.PAUSED]

    account.status = AccountStatus.DEACTIVATED
    assert clone.status is AccountStatus.PAUSED


def test_clone_row_unset_transient_attributes_resolve_like_getattr() -> None:
    account = _make_account()
    # Never assigned: absent from the instance dict, reads as None on the source.
    del account.__dict__["created_at"]
    assert "created_at" not in inspect(account).dict
    assert account.created_at is None

    clone = clone_row(account)

    assert clone.created_at is None
    assert _column_values(clone) == _column_values(_legacy_clone(account))


def test_clone_row_preserves_usage_history_subclasses() -> None:
    now = datetime.now(UTC)
    standard = UsageHistory(
        id=1, account_id="acc", recorded_at=now, window="primary", used_percent=15.0, reset_at=5, window_minutes=5
    )
    additional = AdditionalUsageHistory(
        id=2,
        account_id="acc",
        limit_name="pro_tier",
        recorded_at=now,
        window="primary",
        used_percent=40.0,
        reset_at=7,
        window_minutes=5,
    )

    standard_clone = clone_row(standard)
    additional_clone = clone_row(additional)

    assert type(standard_clone) is UsageHistory
    assert type(additional_clone) is AdditionalUsageHistory
    assert _column_values(standard_clone) == _column_values(standard)
    assert _column_values(additional_clone) == _column_values(additional)
    assert inspect(standard_clone).transient and inspect(additional_clone).transient


@pytest.fixture
def account_session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[cast(Table, Account.__table__)])
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_clone_row_expired_persistent_row_refreshes_through_getattr(account_session: Session) -> None:
    account = _make_account()
    account_session.add(account)
    account_session.commit()  # expire_on_commit: every column is now unloaded
    assert inspect(account).expired_attributes >= set(_ACCOUNT_COLUMN_KEYS)

    clone = clone_row(account)

    assert clone.id == "acc-snapshot"
    assert clone.status is AccountStatus.ACTIVE
    assert _column_values(clone) == _column_values(account)
    assert inspect(clone).transient is True
    assert clone not in account_session
    assert not account_session.new


def test_clone_row_expired_detached_row_raises_like_getattr(account_session: Session) -> None:
    account = _make_account()
    account_session.add(account)
    account_session.commit()
    account_session.close()

    with pytest.raises(DetachedInstanceError):
        _ = account.email
    with pytest.raises(DetachedInstanceError):
        clone_row(account)
