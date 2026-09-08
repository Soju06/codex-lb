"""Per-source bulkhead and admission claims for model-source dispatch (#2123 WP-C1).

Interfaces contract only (design v3 §6, §8.4). A replica enforces
``ModelSource.max_concurrency`` (``NULL`` = unlimited) before the request
reserves API-key usage, so a saturated source answers ``503 model_source_busy``
with nothing owned. The claim is handed to exactly one ``SourceDispatch`` owner
(``transfer_to``) and released exactly once by whichever latch holds it (I13):
``release_if_unowned`` by the route helper when the owner was never built,
``release`` from the owner's ``finish()``. The ``trial`` slot is filled by the
overflow breaker (WP-C2); with ``trial=None`` a ``TrialResult`` records nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.db.models import ModelSource

TrialResult = Literal["success", "failure", "inconclusive"]


@dataclass(frozen=True, slots=True)
class BulkheadSlot:
    source_id: str


class SourceBulkhead:
    """Replica-local in-flight counter per source id."""

    def try_acquire(self, source_id: str, max_concurrency: int | None) -> BulkheadSlot | None:
        raise NotImplementedError

    def release(self, slot: BulkheadSlot) -> None:
        raise NotImplementedError

    def in_flight(self, source_id: str) -> int:
        raise NotImplementedError


def get_source_bulkhead() -> SourceBulkhead:
    """Process-wide bulkhead instance."""

    raise NotImplementedError


@dataclass(slots=True)
class SourceAdmission:
    """Everything claimed before the reservation; released exactly once."""

    slot: BulkheadSlot | None
    trial: object | None = None
    owner: object | None = None
    released: bool = False

    def transfer_to(self, owner: object) -> None:
        raise NotImplementedError

    def release_if_unowned(self) -> None:
        raise NotImplementedError

    def release(self, trial_result: TrialResult) -> None:
        raise NotImplementedError


def try_claim(source: ModelSource, *, bulkhead: SourceBulkhead | None = None) -> SourceAdmission | None:
    """Claim a bulkhead slot for ``source``; ``None`` when it is saturated (``503 model_source_busy``)."""

    raise NotImplementedError
