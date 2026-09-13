"""Live-pin gauge sampled on the hourly retention tick (#2123 WP-C2, owner decision 2026-09-09).

No dedicated task: ``run_retention_pass`` (leader-gated, hourly) samples
``codex_lb_model_source_live_pins{kind}`` right after the pin purge. Every kind
is published on each sample so an emptied kind reads ``0`` instead of its last
value; a failing sample is logged and never fails the pass.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import app.core.retention.job as retention_job
from app.core.retention.job import (
    LIVE_PIN_KINDS,
    EffectiveRetention,
    run_retention_pass,
    sample_model_source_live_pins,
)
from app.modules.proxy.model_source_pins import PIN_KIND_ANCHOR, PIN_KIND_BOUNCE, PIN_KIND_THREAD

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


class _Gauge:
    def __init__(self) -> None:
        self.values: dict[str, float] = {}
        self._kind: str | None = None

    def labels(self, **labels: str) -> _Gauge:
        self._kind = labels["kind"]
        return self

    def set(self, value: float) -> None:
        assert self._kind is not None
        self.values[self._kind] = value


def _install_repository(monkeypatch: pytest.MonkeyPatch, counts: dict[str, int] | Exception) -> list[datetime]:
    seen: list[datetime] = []

    class _Repository:
        def __init__(self, session: object) -> None:
            del session

        async def count_live_by_kind(self, *, now: datetime) -> dict[str, int]:
            seen.append(now)
            if isinstance(counts, Exception):
                raise counts
            return counts

    @asynccontextmanager
    async def session() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(retention_job, "ModelSourcePinRepository", _Repository)
    monkeypatch.setattr(retention_job, "get_background_session", session)
    return seen


def test_live_pin_kinds_cover_every_pin_kind() -> None:
    assert set(LIVE_PIN_KINDS) == {PIN_KIND_THREAD, PIN_KIND_ANCHOR, PIN_KIND_BOUNCE}


@pytest.mark.asyncio
async def test_sample_publishes_every_kind_with_zero_for_absent_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    gauge = _Gauge()
    monkeypatch.setattr(retention_job, "model_source_live_pins", gauge)
    seen = _install_repository(monkeypatch, {PIN_KIND_THREAD: 7, PIN_KIND_ANCHOR: 2})

    sample = await sample_model_source_live_pins(now=_NOW)

    assert seen == [_NOW]
    assert sample == {PIN_KIND_THREAD: 7, PIN_KIND_ANCHOR: 2, PIN_KIND_BOUNCE: 0}
    assert gauge.values == {PIN_KIND_THREAD: 7, PIN_KIND_ANCHOR: 2, PIN_KIND_BOUNCE: 0}


@pytest.mark.asyncio
async def test_sample_without_prometheus_still_returns_the_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retention_job, "model_source_live_pins", None)
    _install_repository(monkeypatch, {PIN_KIND_BOUNCE: 1})
    assert await sample_model_source_live_pins(now=_NOW) == {PIN_KIND_THREAD: 0, PIN_KIND_ANCHOR: 0, PIN_KIND_BOUNCE: 1}


@pytest.mark.asyncio
async def test_sample_failure_is_logged_and_publishes_nothing(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    gauge = _Gauge()
    monkeypatch.setattr(retention_job, "model_source_live_pins", gauge)
    _install_repository(monkeypatch, RuntimeError("db down"))
    caplog.set_level(logging.ERROR, logger="app.core.retention.job")

    assert await sample_model_source_live_pins(now=_NOW) == {}
    assert gauge.values == {}
    assert "live-pin sample failed" in caplog.text


@pytest.mark.asyncio
async def test_retention_pass_samples_after_the_purge_on_every_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gauge rides the tick whatever the retention windows say (they are off here)."""

    async def _resolve() -> EffectiveRetention:
        return EffectiveRetention(request_log_days=0, usage_history_days=0)

    order: list[str] = []

    async def prune_pins() -> int:
        order.append("purge")
        return 0

    async def sample(*, now: datetime) -> dict[str, int]:
        order.append("sample")
        assert now == _NOW
        return {}

    monkeypatch.setattr(retention_job, "get_effective_retention", _resolve)
    monkeypatch.setattr(retention_job, "prune_model_source_pins", prune_pins)
    monkeypatch.setattr(retention_job, "sample_model_source_live_pins", sample)
    monkeypatch.setattr(retention_job, "_warn_on_model_source_pin_drain_invariant", AsyncMock())

    deleted = await run_retention_pass(now=_NOW)

    assert deleted["model_source_pins"] == 0
    assert order == ["purge", "sample"]
