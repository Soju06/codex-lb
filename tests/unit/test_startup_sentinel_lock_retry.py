"""Startup sentinel writes survive a transient SQLite writer-slot loss (issue #1949).

Both startup sentinel stamps — the encryption-key fingerprint and the
hard-sticky outage-grace marker — are the first statement of their
transaction on a fresh connection, so they can simply lose SQLite's single
writer slot on a loaded host. These tests pin the shared bounded retry and
the diagnostic that classifies the failure for the next occurrence: the
driver's ``sqlite_errorname`` separates an instant ``SQLITE_BUSY_SNAPSHOT``
from a ``SQLITE_BUSY`` that only returns after the full busy timeout.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.config.key_fingerprint as key_fingerprint_module
from app.core.config.key_fingerprint import verify_encryption_key_fingerprint
from app.db import sqlite_lock_retry
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.unit

RETRY_LOGGER = "app.db.sqlite_lock_retry"


class _DriverLockError(sqlite3.OperationalError):
    """The driver-raised shape: ``sqlite3`` sets ``sqlite_errorname`` itself.

    A constructed ``sqlite3.OperationalError`` has no such attribute, which
    is why the helper reads it with ``getattr`` rather than ``hasattr``.
    """

    def __init__(self, error_name: str) -> None:
        super().__init__("database is locked")
        self.sqlite_errorname = error_name


def _lock_error(error_name: str | None) -> OperationalError:
    orig: Exception = _DriverLockError(error_name) if error_name else sqlite3.OperationalError("database is locked")
    return OperationalError("INSERT INTO runtime_sentinels ...", {}, orig)


class _StampResult:
    def scalar_one_or_none(self) -> str | None:
        return "hard_sticky_outage_grace_seeded"


class _ScalarsResult:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def all(self) -> list[str]:
        return self._ids


class _FakeBind:
    dialect = type("_Dialect", (), {"name": "sqlite"})()


class _FakeSeedSession:
    """The seed's session surface, failing its stamp a fixed number of times."""

    def __init__(self, *, failures: int, error_name: str | None = "SQLITE_BUSY_SNAPSHOT") -> None:
        self._failures = failures
        self._error_name = error_name
        self.stamp_attempts = 0
        self.rollbacks = 0
        self.commits = 0
        self.refreshed_account_ids: list[str] = []

    def get_bind(self) -> _FakeBind:
        return _FakeBind()

    async def execute(self, statement: Any) -> _StampResult:
        compiled = str(statement).lower()
        if "insert into runtime_sentinels" in compiled:
            self.stamp_attempts += 1
            if self.stamp_attempts <= self._failures:
                raise _lock_error(self._error_name)
            return _StampResult()
        self.refreshed_account_ids.append("refresh")
        return _StampResult()

    async def scalars(self, statement: Any) -> _ScalarsResult:
        return _ScalarsResult(["account-a", "account-b"])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def instant_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the retried tests fast.

    Deliberately not autouse: ``test_retry_budget_stays_three_attempts_under_a_second``
    asserts the shipped budget, and an autouse override would make it assert
    the override instead.
    """
    monkeypatch.setattr(sqlite_lock_retry, "SQLITE_LOCK_RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0))


def test_retry_budget_stays_three_attempts_under_a_second() -> None:
    # The budget is deliberately small: long enough to outlast the
    # sub-second snapshot-upgrade class, short enough that a wedged writer
    # slot still surfaces at startup instead of hiding behind a long sleep.
    delays = sqlite_lock_retry.SQLITE_LOCK_RETRY_DELAYS_SECONDS
    assert len(delays) == 3
    assert sum(delays) < 1.0


async def test_seed_retries_a_transient_lock_and_completes(
    instant_retries: None, caplog: pytest.LogCaptureFixture
) -> None:
    session = _FakeSeedSession(failures=1)
    repository = AccountsRepository(cast(AsyncSession, session))

    with caplog.at_level(logging.DEBUG, logger=RETRY_LOGGER):
        seeded = await repository.seed_hard_sticky_outage_grace_on_startup()

    assert seeded == 2
    assert session.stamp_attempts == 2
    # The failed attempt left the transaction dirty; the next one starts clean.
    assert session.rollbacks == 1
    retry_logs = [record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG]
    assert any("sqlite_errorname=SQLITE_BUSY_SNAPSHOT" in message for message in retry_logs)


async def test_seed_still_fails_startup_when_the_lock_outlives_the_budget(
    instant_retries: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _FakeSeedSession(failures=99, error_name="SQLITE_BUSY")
    repository = AccountsRepository(cast(AsyncSession, session))

    with caplog.at_level(logging.WARNING, logger=RETRY_LOGGER):
        with pytest.raises(OperationalError):
            await repository.seed_hard_sticky_outage_grace_on_startup()

    assert session.stamp_attempts == 4
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("sqlite_errorname=SQLITE_BUSY" in message for message in warnings)
    assert any("total_seconds=" in message for message in warnings)


async def test_seed_gives_up_without_an_error_name_from_a_constructed_exception(
    instant_retries: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # ``sqlite3`` only populates ``sqlite_errorname`` on exceptions it raises
    # itself; reading it must not blow up when the attribute is absent.
    session = _FakeSeedSession(failures=99, error_name=None)
    repository = AccountsRepository(cast(AsyncSession, session))

    with caplog.at_level(logging.WARNING, logger=RETRY_LOGGER):
        with pytest.raises(OperationalError):
            await repository.seed_hard_sticky_outage_grace_on_startup()

    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("sqlite_errorname=None" in message for message in warnings)


async def test_seed_missing_table_still_degrades_to_a_no_op() -> None:
    class _MissingTableSession(_FakeSeedSession):
        async def execute(self, statement: Any) -> _StampResult:
            self.stamp_attempts += 1
            raise OperationalError(
                "INSERT INTO runtime_sentinels ...",
                {},
                sqlite3.OperationalError("no such table: runtime_sentinels"),
            )

    session = _MissingTableSession(failures=0)
    repository = AccountsRepository(cast(AsyncSession, session))

    assert await repository.seed_hard_sticky_outage_grace_on_startup() == 0
    # Not a lock error: no retry, and the pre-existing degrade path stands.
    assert session.stamp_attempts == 1
    assert session.rollbacks == 1


class _FakeFingerprintSession:
    def __init__(self, stored: str | None) -> None:
        self._stored = stored

    async def __aenter__(self) -> _FakeFingerprintSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def scalar(self, statement: Any) -> str | None:
        return self._stored


async def test_fingerprint_retries_a_transient_lock_and_still_verifies(
    instant_retries: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    attempts = 0

    async def stamp(session: Any, fingerprint: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _lock_error("SQLITE_BUSY_SNAPSHOT")

    monkeypatch.setattr(key_fingerprint_module, "_stamp_if_absent", stamp)
    key_file = tmp_path / "encryption.key"
    stored = key_fingerprint_module.compute_encryption_key_fingerprint(key_file)

    await verify_encryption_key_fingerprint(
        lambda: cast(AsyncSession, _FakeFingerprintSession(stored)),
        key_file=key_file,
        mode="enforce",
    )

    assert attempts == 2


async def test_fingerprint_still_raises_when_the_lock_outlives_the_budget(
    instant_retries: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    attempts = 0

    async def always_locked(session: Any, fingerprint: str) -> None:
        nonlocal attempts
        attempts += 1
        raise _lock_error("SQLITE_BUSY")

    monkeypatch.setattr(key_fingerprint_module, "_stamp_if_absent", always_locked)
    key_file = tmp_path / "encryption.key"

    with caplog.at_level(logging.WARNING, logger=RETRY_LOGGER):
        with pytest.raises(OperationalError):
            await verify_encryption_key_fingerprint(
                lambda: cast(AsyncSession, _FakeFingerprintSession(None)),
                key_file=key_file,
                mode="enforce",
            )

    assert attempts == 4
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("encryption-key fingerprint stamp" in message for message in warnings)
    assert any("sqlite_errorname=SQLITE_BUSY" in message for message in warnings)
