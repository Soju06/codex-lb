"""Read-only pool-exhaustion probe (#2123 WP-C1, design v3 §4.3).

The overflow trigger is exactly the spec's 429 predicate: the pool is exhausted
iff the real selector, asked the same question the request would ask (same
model, same ``service_tier``, same API-key account scope, ``lease_kind=None`` so
local account caps do not apply), answers ``usage_limit_reached``. Every other
answer — an admitted account, a closed burn window, ``no_accounts``, a plan or
quota gate — is "not exhausted" and leaves today's behaviour to the caller.

The probe touches no account state (I4): it asks for an ``observe_only``
check, which builds the selector's states from a detached snapshot of the
balancer runtime, so it acquires no lease, refreshes no health tier, bumps no
runtime version and writes no sticky or persisted state. It returns the
selector's own ``AccountSelection`` so the caller can rebuild today's structured
429 (message and ``resets_at``) verbatim.

Takes a Protocol rather than ``ProxyService`` to stay import-cycle free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.core.balancer import USAGE_LIMIT_REACHED
from app.modules.api_keys.service import ApiKeyData

if TYPE_CHECKING:
    from app.modules.proxy._load_balancer.types import AccountLeaseKind
    from app.modules.proxy.load_balancer import AccountSelection


@dataclass(frozen=True, slots=True)
class PoolExhaustion:
    """The selector's own ``usage_limit_reached`` answer, retained so the caller can rebuild today's 429."""

    resets_at: int | None
    selection: AccountSelection


class AdmissionProbeService(Protocol):
    async def check_opportunistic_admission(
        self,
        *,
        api_key: ApiKeyData | None,
        model: str | None,
        service_tier: str | None,
        lease_kind: AccountLeaseKind | None,
        observe_only: bool,
    ) -> AccountSelection: ...


async def probe_pool_usage_exhaustion(
    service: AdmissionProbeService,
    *,
    api_key: ApiKeyData | None,
    model: str | None,
    service_tier: str | None,
) -> PoolExhaustion | None:
    """``PoolExhaustion`` iff ``selection.error_code == USAGE_LIMIT_REACHED``; ``None`` for every other answer."""

    selection = await service.check_opportunistic_admission(
        api_key=api_key,
        model=model,
        service_tier=service_tier,
        lease_kind=None,
        observe_only=True,
    )
    if selection.error_code != USAGE_LIMIT_REACHED:
        return None
    return PoolExhaustion(resets_at=selection.resets_at, selection=selection)
