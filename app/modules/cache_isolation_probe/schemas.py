"""Wire contract for the cross-account prompt cache isolation probe.

Only numbers, identifiers and enumerated statuses cross this boundary. The
generated prefix, the run nonce and every byte of upstream response content
stay inside the service.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.cache_isolation_probe.service import (
    DEFAULT_OTHER_ACCOUNTS,
    DEFAULT_SEED_REPETITIONS,
    MAX_OTHER_ACCOUNTS,
    MAX_SEED_REPETITIONS,
    ProbeCallRole,
    ProbeCallStatus,
    ProbeVerdict,
)
from app.modules.shared.schemas import DashboardModel


class CacheProbeAccountResponse(DashboardModel):
    account_id: str
    label: str


class CacheProbePressureResponse(DashboardModel):
    under_pressure: bool
    reason: str | None = None
    detail: str | None = None
    selectable_account_count: int
    eligible_account_count: int
    pressured_account_count: int


class CacheProbePlanResponse(DashboardModel):
    model: str | None = None
    seed_account: CacheProbeAccountResponse | None = None
    #: Every sibling the pool can offer, capped; the client picks how many of
    #: them a run should use.
    available_other_accounts: list[CacheProbeAccountResponse] = Field(default_factory=list)
    seed_repetitions: int
    total_calls: int
    estimated_input_tokens_per_call: int
    estimated_total_input_tokens: int
    max_seed_repetitions: int
    max_other_accounts: int
    pressure: CacheProbePressureResponse


class CacheProbeRunRequest(DashboardModel):
    #: Explicit, typed acknowledgement that the run spends real quota. The
    #: endpoint refuses anything but ``true`` so a bare POST cannot bill the
    #: pool by accident.
    confirm: bool = False
    model: str | None = None
    seed_repetitions: int = Field(default=DEFAULT_SEED_REPETITIONS, ge=1, le=MAX_SEED_REPETITIONS)
    #: Upper bound on sibling accounts, not a demand: a smaller pool still
    #: runs, with fewer rows. The plan response lists what will actually be
    #: called.
    other_account_count: int = Field(default=DEFAULT_OTHER_ACCOUNTS, ge=1, le=MAX_OTHER_ACCOUNTS)


class CacheProbeCallResponse(DashboardModel):
    sequence: int
    account_id: str
    account_label: str
    role: ProbeCallRole
    status: ProbeCallStatus
    cache_hit: bool
    input_tokens: int | None = None
    cached_tokens: int | None = None
    latency_ms: int
    error_code: str | None = None


class CacheProbeRunResponse(DashboardModel):
    run_id: str
    model: str
    started_at: datetime
    completed_at: datetime
    seed_account: CacheProbeAccountResponse
    seed_repetitions: int
    calls: list[CacheProbeCallResponse] = Field(default_factory=list)
    seed_hit_count: int
    seed_call_count: int
    other_hit_count: int
    other_call_count: int
    cross_account_hit: bool
    verdict: ProbeVerdict
