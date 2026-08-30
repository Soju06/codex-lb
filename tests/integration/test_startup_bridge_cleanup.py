from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

import app.main as main_module
from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import HttpBridgeSessionRecord, HttpBridgeSessionState
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_lifespan_startup_purges_abandoned_ownerless_bridge_rows(db_setup, monkeypatch) -> None:
    del db_setup

    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "startup-instance")
    get_settings.cache_clear()

    now = utcnow()
    stale_time = now - timedelta(hours=3)
    async with SessionLocal() as session:
        session.add_all(
            [
                HttpBridgeSessionRecord(
                    session_key_kind="session_header",
                    session_key_value="sid-stale-ownerless-startup",
                    session_key_hash="hash-stale-ownerless-startup",
                    api_key_scope="__anonymous__",
                    owner_instance_id=None,
                    owner_epoch=1,
                    lease_expires_at=stale_time,
                    state=HttpBridgeSessionState.ACTIVE,
                    account_id=None,
                    model="gpt-5.4",
                    last_seen_at=stale_time,
                    closed_at=None,
                ),
                HttpBridgeSessionRecord(
                    session_key_kind="session_header",
                    session_key_value="sid-recent-ownerless-startup",
                    session_key_hash="hash-recent-ownerless-startup",
                    api_key_scope="__anonymous__",
                    owner_instance_id=None,
                    owner_epoch=1,
                    lease_expires_at=now + timedelta(minutes=5),
                    state=HttpBridgeSessionState.ACTIVE,
                    account_id=None,
                    model="gpt-5.4",
                    last_seen_at=now,
                    closed_at=None,
                ),
            ]
        )
        await session.commit()

    app = main_module.create_app()
    async with app.router.lifespan_context(app):
        async with SessionLocal() as session:
            remaining_keys = set(
                await session.scalars(
                    select(HttpBridgeSessionRecord.session_key_value).where(
                        HttpBridgeSessionRecord.session_key_value.in_(
                            [
                                "sid-stale-ownerless-startup",
                                "sid-recent-ownerless-startup",
                            ]
                        )
                    )
                )
            )

    assert remaining_keys == {"sid-recent-ownerless-startup"}


@pytest.mark.asyncio
async def test_startup_operation_retention_failure_records_sanitized_aggregate(monkeypatch, caplog) -> None:
    recorded = Mock()
    purge = AsyncMock(side_effect=RuntimeError("operation_id=secret SQL DELETE FROM transcript"))
    coordinator = SimpleNamespace(purge_operation_spool_batch=purge)

    monkeypatch.setattr(main_module, "DurableBridgeSessionCoordinator", lambda _session_factory: coordinator)
    monkeypatch.setattr(main_module, "_record_operation_retention_cleanup", recorded)

    with caplog.at_level("WARNING", logger=main_module.__name__):
        with pytest.raises(RuntimeError, match="startup retention failed") as captured:
            await main_module._purge_operation_spool_on_startup(retention_seconds=60.0)

    assert captured.value.__cause__ is None
    purge.assert_awaited_once()
    result = recorded.call_args.args[0]
    assert result.deleted_operations == 0
    assert result.batches == 0
    assert result.backlog_likely is True
    assert result.outcome == "failed"
    assert "error_type=RuntimeError" in caplog.text
    assert "operation_id=secret" not in caplog.text
    assert "DELETE FROM" not in caplog.text


@pytest.mark.asyncio
async def test_startup_operation_retention_success_logs_aggregate_without_prometheus(monkeypatch, caplog) -> None:
    recorded = Mock()
    purge = AsyncMock(return_value=SimpleNamespace(deleted_operations=0, selected_operations=0))
    coordinator = SimpleNamespace(purge_operation_spool_batch=purge)

    monkeypatch.setattr(main_module, "DurableBridgeSessionCoordinator", lambda _session_factory: coordinator)
    monkeypatch.setattr(main_module, "PROMETHEUS_AVAILABLE", False)
    monkeypatch.setattr(main_module, "_record_operation_retention_cleanup", recorded)

    with caplog.at_level("INFO", logger=main_module.__name__):
        assert await main_module._purge_operation_spool_on_startup(retention_seconds=60.0) == 0

    purge.assert_awaited_once()
    result = recorded.call_args.args[0]
    assert result.deleted_operations == 0
    assert result.batches == 1
    assert result.backlog_likely is False
    assert result.outcome == "completed"
    assert "deleted_operations=0 batches=1 outcome=completed backlog_likely=False" in caplog.text
    assert "duration_seconds=" in caplog.text


@pytest.mark.asyncio
async def test_startup_operation_retention_success_logs_aggregate_when_metrics_disabled(monkeypatch, caplog) -> None:
    recorded = Mock()
    purge = AsyncMock(return_value=SimpleNamespace(deleted_operations=0, selected_operations=0))
    coordinator = SimpleNamespace(purge_operation_spool_batch=purge)

    monkeypatch.setattr(main_module, "DurableBridgeSessionCoordinator", lambda _session_factory: coordinator)
    monkeypatch.setattr(main_module, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(main_module, "operation_retention_metrics_enabled", lambda: False)
    monkeypatch.setattr(main_module, "_record_operation_retention_cleanup", recorded)

    with caplog.at_level("INFO", logger=main_module.__name__):
        assert await main_module._purge_operation_spool_on_startup(retention_seconds=60.0) == 0

    recorded.assert_called_once()
    assert "deleted_operations=0 batches=1 outcome=completed backlog_likely=False" in caplog.text
