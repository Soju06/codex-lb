"""Request-budget arithmetic shared by the proxy service and its mixins.

Lives outside ``service.py`` to hold its line ceiling, and imports nothing
from ``app.modules.proxy._service`` so the architecture cross-domain check
treats it as its own leaf domain.
"""

from __future__ import annotations

from app.core.clock import REAL_CLOCK, Clock


class _ClockBudgetMixin:
    """Budget math for owners that carry an injected clock (``ProxyService``).

    Turn-path code reads ``self._remaining_budget_seconds`` (mixins) or
    ``proxy._remaining_budget_seconds`` (module functions with an owner) so a
    deadline computed from the injected clock is compared against that same
    clock. Mixing it with the wall clock would expire every budget immediately
    under virtual time.
    """

    _clock: Clock

    def _remaining_budget_seconds(self, deadline: float) -> float:
        return max(0.0, deadline - self._clock.monotonic())


def _remaining_budget_seconds(deadline: float) -> float:
    """Module-level legacy seam, read by name through ``_service_global``.

    Only endpoints outside the deterministic-simulation scope (compact,
    codex_control, file_ops, transcribe) still resolve this name from the
    service module. It reads the real clock by design; the proxy turn path
    uses ``_ClockBudgetMixin._remaining_budget_seconds`` instead.
    """

    return max(0.0, deadline - REAL_CLOCK.monotonic())
