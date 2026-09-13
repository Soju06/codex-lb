"""The one predicate every SQLite lock check routes through.

Before this module was shared, four call sites each carried their own
``"database is locked" in str(exc)`` variant and they had drifted: leader
election matched ``locked``/``busy``, the API-key usage writes additionally
matched SQLITE_LOCKED's ``table``/``schema`` wording and ``busy_snapshot``,
and the refresh-claim upsert matched only ``database is locked``. These tests
pin the union that all of them now share, and the two deliberate corrections
that unifying them carried: the driver exception is matched (not the rendered
statement and parameters), and the driver's ``sqlite_errorname`` is reported
so a production occurrence classifies itself.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import cast

import pytest
from sqlalchemy.exc import OperationalError

from app.db.sqlite_lock_retry import (
    is_sqlite_lock_error,
    should_retry_after_sqlite_lock,
    sqlite_error_name,
)

pytestmark = pytest.mark.unit

RETRY_LOGGER = "app.db.sqlite_lock_retry"


def _operational_error(driver_message: str, *, statement: str = "UPDATE api_keys") -> OperationalError:
    return OperationalError(statement, {}, Exception(driver_message))


@pytest.mark.parametrize(
    "driver_message",
    [
        # SQLITE_BUSY / SQLITE_BUSY_SNAPSHOT prose — every site matched these.
        "database is locked",
        "database is busy",
        # SQLITE_LOCKED prose — only the API-key writes matched these before.
        "database table is locked",
        "database schema is locked",
        # The extended result-code name, when a wrapper puts it in the message.
        "SQLITE_BUSY_SNAPSHOT",
    ],
)
def test_transient_lock_messages_are_classified_as_lock_failures(driver_message: str) -> None:
    assert is_sqlite_lock_error(_operational_error(driver_message)) is True


@pytest.mark.parametrize(
    "driver_message",
    ["no such table: runtime_sentinels", "disk I/O error", "database disk image is malformed"],
)
def test_non_lock_operational_errors_are_not_classified_as_lock_failures(driver_message: str) -> None:
    assert is_sqlite_lock_error(_operational_error(driver_message)) is False


def test_non_operational_errors_are_not_lock_failures() -> None:
    # Leader election catches bare ``Exception`` on its best-effort shutdown
    # writes, so the predicate — not the call site — owns the type narrowing.
    assert is_sqlite_lock_error(RuntimeError("database is locked")) is False


def test_lock_text_in_the_statement_or_parameters_is_not_a_lock_failure() -> None:
    # ``str(OperationalError)`` renders the failing SQL and its bound
    # parameters as well as the driver message, so the two call sites that
    # matched against it would classify this unrelated integrity failure as
    # transient and silently retry it. Only the driver exception is inspected.
    exc = OperationalError(
        "UPDATE api_keys SET name = ?",
        {"name": "database is locked"},
        Exception("no such column: name"),
    )
    assert is_sqlite_lock_error(exc) is False


def test_wrapper_without_a_driver_exception_is_not_a_lock_failure() -> None:
    # SQLAlchemy raises a wrapper with ``orig is None`` itself, so there is no
    # driver evidence to classify. Falling back to ``str(exc)`` there would
    # reintroduce the statement/parameter match this module exists to remove.
    # ``cast`` because the stub types ``orig`` as non-optional even though
    # SQLAlchemy leaves it unset on a wrapper it raises itself.
    exc = OperationalError("UPDATE api_keys SET name = ?", {"name": "database is locked"}, cast(BaseException, None))
    assert exc.orig is None
    assert is_sqlite_lock_error(exc) is False


def test_error_name_is_read_from_the_driver_exception() -> None:
    class _DriverLockError(sqlite3.OperationalError):
        def __init__(self) -> None:
            super().__init__("database is locked")
            self.sqlite_errorname = "SQLITE_BUSY_SNAPSHOT"

    assert sqlite_error_name(OperationalError("UPDATE x", {}, _DriverLockError())) == "SQLITE_BUSY_SNAPSHOT"
    # A constructed error has no name; absence must be reportable, not fatal.
    assert sqlite_error_name(_operational_error("database is locked")) is None
    assert sqlite_error_name(RuntimeError("boom")) is None


@pytest.mark.asyncio
async def test_should_retry_sleeps_the_call_sites_backoff_and_names_the_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    slept: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _record_sleep)
    with caplog.at_level("DEBUG", logger=RETRY_LOGGER):
        retry = await should_retry_after_sqlite_lock(
            _operational_error("database is locked"),
            what="touch_usage_reservation",
            attempt=2,
            max_attempts=4,
            base_delay_seconds=0.1,
        )

    assert retry is True
    # The caller's own exponential shape is preserved: base * 2**attempt.
    assert slept == [pytest.approx(0.4)]
    messages = [record.getMessage() for record in caplog.records]
    assert any("what=touch_usage_reservation" in message and "sqlite_errorname=None" in message for message in messages)


@pytest.mark.asyncio
async def test_should_retry_refuses_the_last_attempt_and_reports_the_error_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("DEBUG", logger=RETRY_LOGGER):
        retry = await should_retry_after_sqlite_lock(
            _operational_error("database is busy"),
            what="refresh_claim_upsert",
            attempt=3,
            max_attempts=4,
            base_delay_seconds=0.05,
        )

    assert retry is False
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("budget exhausted" in message and "what=refresh_claim_upsert" in message for message in warnings)


@pytest.mark.asyncio
async def test_should_retry_refuses_a_non_lock_failure_without_sleeping_or_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("DEBUG", logger=RETRY_LOGGER):
        retry = await should_retry_after_sqlite_lock(
            _operational_error("no such column: name"),
            what="update_key",
            attempt=0,
            max_attempts=4,
            base_delay_seconds=0.1,
        )

    assert retry is False
    assert caplog.records == []
