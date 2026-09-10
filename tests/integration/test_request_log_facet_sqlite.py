from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import event, insert, text

import app.db.session as db_session
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, ApiKey, RequestLog
from app.db.session import SessionLocal, engine

pytestmark = [pytest.mark.integration, pytest.mark.skipif(engine.dialect.name != "sqlite", reason="SQLite query plans")]


@pytest.mark.asyncio
@pytest.mark.parametrize("live_facet_indexes", [False, True])
async def test_options_avoid_repeated_live_cohort_scans(
    async_client: AsyncClient,
    db_setup: bool,
    live_facet_indexes: bool,
    record_property: Callable[[str, int], None],
) -> None:
    del db_setup
    now = datetime(2026, 9, 9)
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add_all(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=now,
                status=AccountStatus.ACTIVE,
            )
            for account_id in ("facet-a", "facet-b")
        )
        session.add(ApiKey(id="facet-key", name="Facet key", key_hash="facet-hash", key_prefix="sk-facet"))
        await session.commit()
        await session.execute(
            insert(RequestLog),
            [
                {
                    "request_id": f"facet-{index}",
                    "account_id": "facet-a" if index % 2 else "facet-b",
                    "api_key_id": "facet-key",
                    "requested_at": now,
                    "model": "model-a" if index % 2 else "model-b",
                    "reasoning_effort": "high" if index % 3 else None,
                    "status": "success" if index % 2 else "error",
                    "error_code": None if index % 2 else "rate_limit_exceeded",
                    "deleted_at": now if index % 5 == 0 else None,
                }
                for index in range(20_000)
            ],
        )
        await session.execute(
            insert(RequestLog),
            [
                {
                    "request_id": f"visibility-{index}",
                    "account_id": "facet-a",
                    "api_key_id": "facet-key",
                    "requested_at": now,
                    "model": model,
                    "reasoning_effort": effort,
                    "status": status,
                    "deleted_at": deleted_at,
                }
                for index, (model, effort, status, deleted_at) in enumerate(
                    (
                        ("model-a", "", "success", None),
                        ("model-a", "deleted-only", "success", now),
                        ("model-a", "unsupported-only", "internal", None),
                        ("deleted-model", "high", "success", now),
                        ("unsupported-model", "high", "internal", None),
                        ("", "high", "success", None),
                    )
                )
            ],
        )
        await session.commit()
        # Model metadata now creates these indexes by default (#2246).
        # Build both parameterized layouts explicitly so the pre-index case
        # stays covered and the indexed case cannot duplicate a schema index.
        for name, columns in (
            ("api_key", "api_key_id"),
            ("model_effort", "model, reasoning_effort"),
            ("status_error", "status, error_code"),
        ):
            await session.execute(text(f"DROP INDEX IF EXISTS idx_logs_live_{name}"))
            if live_facet_indexes:
                await session.execute(
                    text(f"CREATE INDEX idx_logs_live_{name} ON request_logs ({columns}) WHERE deleted_at IS NULL")
                )
        await session.commit()
        assert not (await session.execute(text("SELECT name FROM sqlite_master WHERE name = 'sqlite_stat1'"))).all()

    # Count SQLite VM work for the real HTTP endpoint, including its actual
    # connection lifecycle. This catches rescans without a wall-clock flake.
    operations = 0

    def count_operations() -> int:
        nonlocal operations
        operations += 100
        return 0

    def instrument_connection(connection, _record) -> None:
        connection.run_async(lambda driver: driver.set_progress_handler(count_operations, 100))

    background_engine = db_session._background_engine
    assert background_engine is not None
    observed_engines = {engine.sync_engine, background_engine.sync_engine}
    for observed in observed_engines:
        event.listen(observed, "connect", instrument_connection)
    try:
        response = await async_client.get("/api/request-logs/options")
    finally:
        for observed in observed_engines:
            event.remove(observed, "connect", instrument_connection)

    assert response.status_code == 200
    assert response.json() == {
        "accountIds": ["facet-a", "facet-b"],
        "apiKeys": [{"id": "facet-key", "name": "Facet key", "keyPrefix": "sk-facet"}],
        "modelOptions": [
            {"model": "model-a", "reasoningEffort": None},
            {"model": "model-a", "reasoningEffort": ""},
            {"model": "model-a", "reasoningEffort": "high"},
            {"model": "model-b", "reasoningEffort": None},
            {"model": "model-b", "reasoningEffort": "high"},
        ],
        "statuses": ["ok", "rate_limit"],
    }
    record_property("sqlite_vm_operations", operations)
    assert 0 < operations < 20_000, f"Unfiltered options executed {operations:,} SQLite VM operations"
