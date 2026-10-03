"""Replica-local selection for equivalent Responses sources; no owned async work."""

from __future__ import annotations

import hashlib
import math
import random
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from app.core.clock import REAL_CLOCK, Clock
from app.db.models import ModelSource
from app.modules.model_sources.forwarding import ModelSourceForwardingError
from app.modules.proxy.source_admission import get_source_bulkhead

MAX_SOURCE_ATTEMPTS = 5
MAX_SOURCE_STATES = 10_000


@dataclass(slots=True)
class _SourceState:
    revision: tuple[datetime | None, str | None, str, int | None]
    last_selected: int = 0
    cooldown_until: float = 0.0


def retryable_source_error(error: ModelSourceForwardingError) -> bool:
    status = error.upstream_status_code
    return error.connection_failed or status in (401, 403, 429) or (status is not None and 500 <= status < 600)


class SourcePool:
    def __init__(self, *, clock: Clock = REAL_CLOCK) -> None:
        self._clock = clock
        self._states: OrderedDict[str, _SourceState] = OrderedDict()
        self._sequence = 0

    def _state(self, source: ModelSource) -> _SourceState:
        state = self._states.get(source.id)
        # SQLite timestamps can be equal for two edits within one second.
        # Include transport identity without retaining decrypted credentials.
        revision = (
            source.updated_at,
            source.base_url,
            hashlib.sha256(source.api_key_encrypted or b"").hexdigest(),
            source.max_concurrency,
        )
        if state is None or state.revision != revision:
            state = _SourceState(revision=revision)
            self._states[source.id] = state
        self._states.move_to_end(source.id)
        while len(self._states) > MAX_SOURCE_STATES:
            self._states.popitem(last=False)
        return state

    def choose(self, sources: list[ModelSource], *, excluded: set[str]) -> ModelSource | None:
        bulkhead = get_source_bulkhead()
        now = self._clock.monotonic()
        available: list[tuple[tuple[int, int], ModelSource]] = []
        for source in sources:
            if source.id in excluded:
                continue
            state = self._state(source)
            active = bulkhead.in_flight(source.id)
            if state.cooldown_until > now or (source.max_concurrency is not None and active >= source.max_concurrency):
                continue
            available.append(((active, state.last_selected), source))
        if not available:
            return None
        best = min(rank for rank, _source in available)
        # Avoid synchronized first-source preference across independent replicas.
        selected = random.choice([source for rank, source in available if rank == best])
        self._sequence += 1
        self._state(selected).last_selected = self._sequence
        return selected

    def failed(self, source: ModelSource, error: ModelSourceForwardingError) -> None:
        """Call only after the attempt's reservation and admission are finalized."""
        if not retryable_source_error(error):
            return
        status = error.upstream_status_code
        seconds = 300.0 if status in (401, 403) else 60.0 if status == 429 else 5.0
        if error.retry_after:
            try:
                delay = float(error.retry_after)
            except ValueError:
                try:
                    date = parsedate_to_datetime(error.retry_after)
                    delay = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp() - self._clock.time()
                except (TypeError, ValueError, OverflowError):
                    delay = seconds
            if math.isfinite(delay):
                seconds = min(600.0, max(1.0, delay))
        state = self._state(source)
        state.cooldown_until = max(state.cooldown_until, self._clock.monotonic() + seconds)

    def retry_after(self, sources: list[ModelSource]) -> str:
        now = self._clock.monotonic()
        delays = [max(1.0, self._state(source).cooldown_until - now) for source in sources]
        return str(math.ceil(min(delays, default=1.0)))


_SOURCE_POOL = SourcePool()


def get_source_pool() -> SourcePool:
    return _SOURCE_POOL
