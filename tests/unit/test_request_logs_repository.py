from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import ResourceClosedError

from app.db.models import ModelSource, RequestLog
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository


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
            plan_type="plus",
        )

        assert log.request_id == "req"
        assert log.cost_usd is not None


@pytest.mark.asyncio
async def test_add_log_persists_request_kind(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        saved = await repo.add_log(
            account_id=None,
            request_id="req_kind",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
            request_kind="warmup",
        )

        persisted = await session.scalar(select(RequestLog).where(RequestLog.id == saved.id))
        assert persisted is not None
        assert persisted.request_kind == "warmup"


@pytest.mark.asyncio
async def test_add_log_persists_normalized_conversation_id(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        saved = await repo.add_log(
            account_id=None,
            request_id="req_conversation",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
            conversation_id=" conv-a ",
        )
        empty = await repo.add_log(
            account_id=None,
            request_id="req_conversation_empty",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
            conversation_id=" ",
        )

        persisted = await session.scalar(select(RequestLog).where(RequestLog.id == saved.id))
        empty_persisted = await session.scalar(select(RequestLog).where(RequestLog.id == empty.id))

    assert persisted is not None
    assert persisted.conversation_id == "conv-a"
    assert empty_persisted is not None
    assert empty_persisted.conversation_id is None


@pytest.mark.asyncio
async def test_aggregate_conversations_by_bucket_deduplicates_model_service_groups_and_excludes_warmups(
    db_setup,
) -> None:
    del db_setup
    since = datetime(2026, 7, 21, 0, 0, 0)
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        for request_id, model, service_tier, conversation_id, request_kind in (
            ("conversation_bucket_1", "gpt-5.1", None, "conv-a", "normal"),
            ("conversation_bucket_2", "gpt-5.2", "priority", " conv-a ", "normal"),
            ("conversation_bucket_3", "gpt-5.1", None, "conv-b", "normal"),
            ("conversation_bucket_warmup", "gpt-5.1", None, "conv-warmup", "warmup"),
        ):
            await repo.add_log(
                account_id=None,
                request_id=request_id,
                model=model,
                service_tier=service_tier,
                input_tokens=0,
                output_tokens=0,
                latency_ms=1,
                status="success",
                error_code=None,
                conversation_id=conversation_id,
                request_kind=request_kind,
                requested_at=since + timedelta(minutes=5),
            )

        buckets = await repo.aggregate_conversations_by_bucket(since, bucket_seconds=3600)

    assert [(bucket.bucket_epoch, bucket.conversation_count) for bucket in buckets] == [
        (int(since.replace(tzinfo=timezone.utc).timestamp()), 2),
    ]


@pytest.mark.asyncio
async def test_aggregate_activity_counts_only_nonblank_conversation_requests(db_setup) -> None:
    del db_setup
    since = datetime(2026, 7, 21, 0, 0, 0)
    until = since + timedelta(hours=1)

    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        for request_id, conversation_id, request_kind in (
            ("conv-request-1", "conv-a", "normal"),
            ("conv-request-2", " conv-a ", "normal"),
            ("conv-request-3", "conv-b", "normal"),
            ("conv-request-4", "conv-b", "normal"),
            ("no-conversation-1", None, "normal"),
            ("no-conversation-2", "   ", "normal"),
            ("warmup-conversation", "conv-warmup", "warmup"),
        ):
            await repo.add_log(
                account_id=None,
                request_id=request_id,
                model="gpt-5.2",
                input_tokens=0,
                output_tokens=0,
                latency_ms=1,
                status="success",
                error_code=None,
                conversation_id=conversation_id,
                request_kind=request_kind,
                requested_at=since + timedelta(minutes=5),
            )

        aggregate = await repo.aggregate_activity_between(since, until)

    assert aggregate.request_count == 6
    assert aggregate.conversation_count == 2
    assert aggregate.conversation_request_count == 4


@pytest.mark.asyncio
async def test_add_log_does_not_recalculate_unpriced_model_source_cost(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        session.add(
            ModelSource(
                id="source_unpriced",
                name="source unpriced",
                base_url="https://source-unpriced.example.invalid/v1",
            )
        )
        await session.commit()
        repo = RequestLogsRepository(session)

        saved = await repo.add_log(
            account_id=None,
            model_source_id="source_unpriced",
            request_id="req_source_unpriced",
            model="gpt-5.2",
            input_tokens=10_000,
            output_tokens=5_000,
            latency_ms=1,
            status="success",
            error_code=None,
            cost_usd=None,
        )

        persisted = await session.scalar(select(RequestLog).where(RequestLog.id == saved.id))
        assert persisted is not None
        assert persisted.cost_usd == 0.0


@pytest.mark.asyncio
async def test_add_log_persists_archive_request_id(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        explicit = await repo.add_log(
            account_id=None,
            request_id="resp_downstream",
            archive_request_id="req_archive_origin",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
        )
        fallback = await repo.add_log(
            account_id=None,
            request_id="req_archive_same",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
        )

        explicit_persisted = await session.scalar(select(RequestLog).where(RequestLog.id == explicit.id))
        fallback_persisted = await session.scalar(select(RequestLog).where(RequestLog.id == fallback.id))

    assert explicit_persisted is not None
    assert explicit_persisted.request_id == "resp_downstream"
    assert explicit_persisted.archive_request_id == "req_archive_origin"
    assert fallback_persisted is not None
    assert fallback_persisted.archive_request_id == "req_archive_same"


@pytest.mark.asyncio
async def test_add_log_persists_ttft_phase_and_prewarm_fields(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        saved = await repo.add_log(
            account_id=None,
            request_id="req_phase",
            model="gpt-5.2",
            input_tokens=50000,
            output_tokens=5,
            latency_ms=1000,
            status="success",
            error_code=None,
            latency_first_token_ms=900,
            latency_queue_ms=77,
            latency_response_created_ms=210,
            latency_first_upstream_event_ms=180,
            latency_response_create_gate_wait_ms=50,
            latency_bridge_queue_wait_ms=40,
            prewarm_status="success",
            prewarm_latency_ms=120,
            session_previous_gap_ms=180000,
        )

        persisted = await session.scalar(select(RequestLog).where(RequestLog.id == saved.id))

    assert persisted is not None
    assert persisted.latency_queue_ms == 77
    assert persisted.latency_response_created_ms == 210
    assert persisted.latency_first_upstream_event_ms == 180
    assert persisted.latency_response_create_gate_wait_ms == 50
    assert persisted.latency_bridge_queue_wait_ms == 40
    assert persisted.prewarm_status == "success"
    assert persisted.prewarm_latency_ms == 120
    assert persisted.session_previous_gap_ms == 180000


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_prefers_session_then_falls_back_to_api_key_scope() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)
    executed_sql: list[str] = []
    owner_requested_at = datetime(2026, 7, 11, 12, 0, 0)
    returned_values = iter(
        [
            ("acc_latest", None, None),
            ("acc_scoped", None, None),
            ("acc_session", owner_requested_at, "sid_terminal_a"),
            None,
            ("acc_scoped", None, None),
            None,
        ]
    )

    async def _execute(statement):
        executed_sql.append(str(statement))
        value = next(returned_values)
        return SimpleNamespace(one_or_none=lambda: value)

    session.execute.side_effect = _execute

    owner_any = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id=None,
    )
    owner_scoped = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
    )
    owner_session = await repo.find_latest_owner_record_for_response_id(
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
    assert owner_session is not None
    assert owner_session.account_id == "acc_session"
    assert owner_session.requested_at == owner_requested_at
    assert owner_session.session_id == "sid_terminal_a"
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
        return SimpleNamespace(one_or_none=lambda: ("acc_scoped", None, None))

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
    returned_values = iter([("   ", None, "sid_terminal_a"), ("acc_fallback", None, None)])

    async def _execute(statement):
        executed_sql.append(str(statement))
        return SimpleNamespace(one_or_none=lambda: next(returned_values))

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
