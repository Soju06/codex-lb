from __future__ import annotations

from datetime import timedelta
from email.utils import format_datetime

import pytest

from app.db.models import ModelSource
from app.modules.model_sources.forwarding import ModelSourceForwardingError
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.proxy.source_pool import SourcePool
from tests.simulation.virtual_time import VirtualClock

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("hint,seconds", [("date", 90), ("10000", 600), ("0", 1), ("invalid", 60), ("NaN", 60)])
def test_retry_after_bounds_and_half_open_availability(hint: str, seconds: int) -> None:
    clock = VirtualClock()
    source = ModelSource(id="source-retry-after", updated_at=clock.now())
    pool = SourcePool(clock=clock)
    header = format_datetime(clock.now() + timedelta(seconds=90), usegmt=True) if hint == "date" else hint
    pool.failed(
        source, ModelSourceForwardingError(status_code=429, payload={}, upstream_status_code=429, retry_after=header)
    )
    assert pool.choose([source], excluded=set()) is None
    assert pool.retry_after([source]) == str(seconds)
    clock.advance(seconds)
    assert pool.choose([source], excluded=set()) is source


def test_least_inflight_beats_rotation_even_without_concurrency_limit() -> None:
    pool = SourcePool()
    idle = ModelSource(id="source-idle")
    busy = ModelSource(id="source-busy")
    pool.choose([idle], excluded=set())
    slot = get_source_bulkhead().try_acquire(busy.id, None)
    assert slot is not None
    try:
        assert pool.choose([idle, busy], excluded=set()) is idle
    finally:
        get_source_bulkhead().release(slot)
