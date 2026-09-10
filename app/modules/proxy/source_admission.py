"""Per-source bulkhead and admission claims for model-source dispatch (#2123 WP-C1).

A replica enforces ``ModelSource.max_concurrency`` (``NULL`` = unlimited)
before the request reserves API-key usage, so a saturated source answers
``503 model_source_busy`` with nothing owned (design v3 §6, §8.4). The claim is
handed to exactly one ``SourceDispatch`` owner (``transfer_to``) and released
exactly once by whichever latch holds it (I13): ``release_if_unowned`` by the
route helper when the owner was never built, ``release`` from the owner's
``finish()``. The ``trial`` slot is filled by the overflow breaker (WP-C2);
with ``trial=None`` a ``TrialResult`` records nothing.

The bulkhead is a plain dictionary increment on the request path (design §12):
no lock is needed because every mutation happens synchronously on the event
loop between two awaits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.metrics.prometheus import (
    model_source_bulkhead_in_flight,
    model_source_bulkhead_rejections_total,
)
from app.db.models import ModelSource

logger = logging.getLogger(__name__)

TrialResult = Literal["success", "failure", "inconclusive"]


@dataclass(frozen=True, slots=True)
class BulkheadSlot:
    source_id: str


class SourceBulkhead:
    """Replica-local in-flight counter per source id."""

    def __init__(self) -> None:
        self._in_flight: dict[str, int] = {}

    def try_acquire(self, source_id: str, max_concurrency: int | None) -> BulkheadSlot | None:
        current = self._in_flight.get(source_id, 0)
        if max_concurrency is not None and current >= max_concurrency:
            if model_source_bulkhead_rejections_total is not None:
                model_source_bulkhead_rejections_total.labels(source_id=source_id).inc()
            return None
        self._in_flight[source_id] = current + 1
        self._publish(source_id)
        return BulkheadSlot(source_id=source_id)

    def release(self, slot: BulkheadSlot) -> None:
        current = self._in_flight.get(slot.source_id, 0)
        if current <= 1:
            self._in_flight.pop(slot.source_id, None)
        else:
            self._in_flight[slot.source_id] = current - 1
        self._publish(slot.source_id)

    def in_flight(self, source_id: str) -> int:
        return self._in_flight.get(source_id, 0)

    def _publish(self, source_id: str) -> None:
        if model_source_bulkhead_in_flight is not None:
            model_source_bulkhead_in_flight.labels(source_id=source_id).set(self._in_flight.get(source_id, 0))


_BULKHEAD: SourceBulkhead | None = None


def get_source_bulkhead() -> SourceBulkhead:
    """Process-wide bulkhead instance (one per replica worker)."""

    global _BULKHEAD
    if _BULKHEAD is None:
        _BULKHEAD = SourceBulkhead()
    return _BULKHEAD


class TrialClaim(Protocol):
    """Half-open breaker trial lease (WP-C2): told the first output item once and the dispatch outcome exactly once."""

    def observe_first_output_item(self) -> None: ...

    def settle(self, result: TrialResult) -> None: ...


@dataclass(slots=True)
class SourceAdmission:
    """Everything claimed before the reservation; released exactly once."""

    slot: BulkheadSlot | None
    trial: TrialClaim | None = None
    owner: object | None = None
    released: bool = False
    bulkhead: SourceBulkhead | None = None

    def transfer_to(self, owner: object) -> None:
        if self.owner is not None and self.owner is not owner:
            raise RuntimeError("source admission claims already belong to another owner")
        self.owner = owner

    def release_if_unowned(self) -> None:
        """Route-helper latch: release only when no ``SourceDispatch`` took over the claims."""

        if self.owner is None:
            self._release("inconclusive")

    def observe_first_output_item(self) -> None:
        """Owner hook at the source's first output item: a half-open trial closes the breaker now (design §8.3).

        The concurrency slot stays held -- only the owner's terminal latch
        releases it -- and the trial is still settled with the slot, so a
        counted failure later in the same stream reaches the breaker.
        """

        if self.released or self.trial is None:
            return
        self.trial.observe_first_output_item()

    def release(self, trial_result: TrialResult) -> None:
        """Owner latch: release the slot and hand the trial outcome to the breaker (when there is one)."""

        self._release(trial_result)

    def _release(self, trial_result: TrialResult) -> None:
        if self.released:
            return
        self.released = True
        try:
            if self.trial is not None:
                self.trial.settle(trial_result)
        finally:
            if self.slot is not None:
                (self.bulkhead or get_source_bulkhead()).release(self.slot)


def try_claim(
    source: ModelSource,
    *,
    bulkhead: SourceBulkhead | None = None,
    trial: TrialClaim | None = None,
) -> SourceAdmission | None:
    """Claim a bulkhead slot for ``source``; ``None`` when it is saturated (``503 model_source_busy``).

    ``trial`` is the overflow breaker's token (WP-C2): it rides in the claims
    and is settled by the same latch that releases the slot. A saturated source
    returns ``None`` without settling it -- the caller still holds the token and
    releases it (``try_claim_overflow``).
    """

    active = bulkhead if bulkhead is not None else get_source_bulkhead()
    slot = active.try_acquire(source.id, source.max_concurrency)
    if slot is None:
        logger.warning(
            "model_source_bulkhead_rejected source_id=%s max_concurrency=%s in_flight=%d",
            source.id,
            source.max_concurrency,
            active.in_flight(source.id),
        )
        return None
    return SourceAdmission(slot=slot, trial=trial, bulkhead=active)
