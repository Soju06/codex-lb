from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError, ResourceClosedError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository


def _sqlite_locked_error() -> OperationalError:
    return OperationalError("insert request log", {}, Exception("database is locked"))


class _RetryCommitSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: object | None = None

    def add(self, row: object) -> None:
        self.added.append(row)

    def in_transaction(self) -> bool:
        return True

    def get_bind(self) -> SimpleNamespace:
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def commit(self) -> None:
        self.commits += 1
        if self.commits == 1:
            raise _sqlite_locked_error()

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, row: object) -> None:
        self.refreshed = row


class _ScalarRows:
    def __init__(self, rows: list[RequestLog]) -> None:
        self._rows = rows

    def scalars(self) -> list[RequestLog]:
        return self._rows


class _RetryUpdateSession:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.commits = 0
        self.rollbacks = 0
        self.logs: list[RequestLog] = []

    def in_transaction(self) -> bool:
        return True

    def get_bind(self) -> SimpleNamespace:
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def execute(self, _: object) -> _ScalarRows:
        self.execute_calls += 1
        log = RequestLog(
            account_id=None,
            request_id="req_retry_model_update",
            model="internal-host-model",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=1,
            status="success",
            error_code=None,
        )
        self.logs.append(log)
        return _ScalarRows([log])

    async def commit(self) -> None:
        self.commits += 1
        if self.commits == 1:
            raise _sqlite_locked_error()

    async def rollback(self) -> None:
        self.rollbacks += 1


async def _no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_add_log_ignores_closed_transaction(monkeypatch) -> None:
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        async def _commit_failure() -> None:
            raise ResourceClosedError("This transaction is closed")

        async def _refresh_failure(_: object) -> None:
            raise AssertionError("refresh should not be called after commit failure")

        monkeypatch.setattr(session, "commit", _commit_failure)
        monkeypatch.setattr(session, "refresh", _refresh_failure)

        log = await repo.add_log(
            account_id=None,
            request_id="req",
            model="gpt-5.2",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=1,
            status="success",
            error_code=None,
        )

        assert log.request_id == "req"
        assert log.cost_usd is not None


@pytest.mark.asyncio
async def test_add_log_retries_sqlite_lock_with_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.sqlite_retry.asyncio.sleep", _no_sleep)
    session = _RetryCommitSession()
    repo = RequestLogsRepository(cast(AsyncSession, session))

    log = await repo.add_log(
        account_id=None,
        request_id="req_retry_log",
        model="gpt-5.2",
        input_tokens=1000,
        output_tokens=500,
        latency_ms=1,
        status="success",
        error_code=None,
    )

    assert isinstance(log, RequestLog)
    assert session.commits == 2
    assert session.rollbacks == 1
    assert len(session.added) == 2
    assert session.refreshed is log


@pytest.mark.asyncio
async def test_update_model_for_request_retries_sqlite_lock_with_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.sqlite_retry.asyncio.sleep", _no_sleep)
    session = _RetryUpdateSession()
    repo = RequestLogsRepository(cast(AsyncSession, session))

    updated = await repo.update_model_for_request("req_retry_model_update", "gpt-5.4")

    assert updated == 1
    assert session.execute_calls == 2
    assert session.commits == 2
    assert session.rollbacks == 1
    assert session.logs[-1].model == "gpt-5.4"


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_prefers_session_then_falls_back_to_api_key_scope() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)
    executed_sql: list[str] = []
    returned_values = iter(
        [
            "acc_latest",
            "acc_scoped",
            "acc_session",
            None,
            "acc_scoped",
            None,
        ]
    )

    async def _execute(statement):
        executed_sql.append(str(statement))
        value = next(returned_values)
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    session.execute.side_effect = _execute

    owner_any = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id=None,
    )
    owner_scoped = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
    )
    owner_session = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
        session_id="sid_terminal_a",
    )
    owner_session_fallback = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
        session_id="sid_terminal_b",
    )
    owner_missing = await repo.find_latest_account_id_for_response_id(
        response_id="resp_missing_owner",
        api_key_id=None,
    )

    assert owner_any == "acc_latest"
    assert owner_scoped == "acc_scoped"
    assert owner_session == "acc_session"
    assert owner_session_fallback == "acc_scoped"
    assert owner_missing is None
    assert "request_logs.api_key_id = :api_key_id_1" not in executed_sql[0]
    assert "request_logs.api_key_id = :api_key_id_1" in executed_sql[1]
    assert "request_logs.session_id = :session_id_1" in executed_sql[2]
    assert "request_logs.session_id = :session_id_1" in executed_sql[3]
    assert "request_logs.session_id = :session_id_1" not in executed_sql[4]


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_ignores_blank_response_id() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)

    owner = await repo.find_latest_account_id_for_response_id(
        response_id="   ",
        api_key_id="api_key_1",
        session_id="sid_terminal_a",
    )

    assert owner is None
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_ignores_blank_session_id_scope() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)
    executed_sql: list[str] = []

    async def _execute(statement):
        executed_sql.append(str(statement))
        return SimpleNamespace(scalar_one_or_none=lambda: "acc_scoped")

    session.execute.side_effect = _execute

    owner = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
        session_id="   ",
    )

    assert owner == "acc_scoped"
    assert len(executed_sql) == 1
    assert "request_logs.session_id = :session_id_1" not in executed_sql[0]


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_falls_back_when_session_scope_owner_is_blank() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)
    executed_sql: list[str] = []
    returned_values = iter(["   ", "acc_fallback"])

    async def _execute(statement):
        executed_sql.append(str(statement))
        return SimpleNamespace(scalar_one_or_none=lambda: next(returned_values))

    session.execute.side_effect = _execute

    owner = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
        session_id="sid_terminal_a",
    )

    assert owner == "acc_fallback"
    assert len(executed_sql) == 2
    assert "request_logs.session_id = :session_id_1" in executed_sql[0]
    assert "request_logs.session_id = :session_id_1" not in executed_sql[1]
