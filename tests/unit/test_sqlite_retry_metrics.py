from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import cast

import pytest

from app.db import sqlite_retry

pytestmark = pytest.mark.unit


class _ObservedCounter:
    def __init__(self) -> None:
        self.samples: list[dict[str, object]] = []

    def labels(self, **labels: str):
        sample: dict[str, object] = {"labels": dict(labels), "value": 0.0}
        self.samples.append(sample)

        def inc(amount: float = 1.0) -> None:
            sample["value"] = cast(float, sample["value"]) + amount

        return SimpleNamespace(inc=inc)


@pytest.mark.asyncio
async def test_sqlite_lock_retry_records_retry_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _ObservedCounter()
    monkeypatch.setattr(sqlite_retry, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(sqlite_retry, "sqlite_lock_retries_total", counter)

    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    result = await sqlite_retry.retry_sqlite_lock(
        operation,
        operation_name="request_log_add",
        delays_seconds=(0,),
    )

    assert result == "ok"
    assert counter.samples == [{"labels": {"operation": "request_log_add", "outcome": "retry"}, "value": 1.0}]


@pytest.mark.asyncio
async def test_sqlite_lock_retry_records_exhausted_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _ObservedCounter()
    monkeypatch.setattr(sqlite_retry, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(sqlite_retry, "sqlite_lock_retries_total", counter)

    async def operation() -> str:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        await sqlite_retry.retry_sqlite_lock(
            operation,
            operation_name="bridge_ring_heartbeat",
            delays_seconds=(),
        )

    assert counter.samples == [{"labels": {"operation": "bridge_ring_heartbeat", "outcome": "exhausted"}, "value": 1.0}]
