from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import ResourceClosedError

from app.db.session import SessionLocal
from app.modules.request_logs.mappers import to_request_log_entry
from app.modules.request_logs.repository import RequestLogsRepository


@pytest.mark.asyncio
async def test_add_log_ignores_closed_transaction(monkeypatch, db_setup) -> None:
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        async def _commit_failure() -> None:
            raise ResourceClosedError("This transaction is closed")

        async def _refresh_failure(_: object) -> None:
            raise AssertionError("refresh should not be called after commit failure")

        monkeypatch.setattr(session, "commit", _commit_failure)
        monkeypatch.setattr(session, "refresh", _refresh_failure)

        log = await repo.add_log(
            account_id="acc",
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
async def test_add_log_persists_response_create_slim_summary(db_setup) -> None:
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        log = await repo.add_log(
            account_id=None,
            request_id="req_slim",
            model="gpt-5.2",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=1,
            status="success",
            error_code=None,
            slim_summary={
                "historical_tool_outputs_slimmed": 2,
                "historical_images_slimmed": 1,
                "unrelated_counter": 99,
            },
        )

        assert log.slim_summary_json == '{"historical_images_slimmed":1,"historical_tool_outputs_slimmed":2}'
        entry = to_request_log_entry(log)
        assert entry.slim_summary == {
            "historical_tool_outputs_slimmed": 2,
            "historical_images_slimmed": 1,
        }


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
