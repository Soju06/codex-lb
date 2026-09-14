"""Operator diagnostic: is the upstream prompt cache partitioned per account?

Background. A hand-run experiment on this deployment sent a ~28k-token
single-use prefix on one account and then the identical bytes on four
siblings, and the siblings reported near-full ``cached_tokens`` (28,032/28,168;
27,136/27,328; 28,416/28,588 across three runs). Neither ``prompt_cache_key``
nor the account gated the hit, and same-account repeats hit *less* often than
other accounts did, which is what a node-local prefix cache that is not
partitioned per account looks like. This module turns that experiment into a
button so the owner can re-run it -- in particular before and after enabling
per-account outbound cache-identity scoping -- instead of re-deriving it by
hand.

Reading the result. A hit on a non-seed account proves the cache is shared
across accounts. The converse is weaker: hits are sporadic (~20-40%) for every
caller, so a run with no cross-account hit is evidence, not proof, and a miss
on the *seed* account is expected and is not a probe failure. The verdicts
below say exactly that, and the run is refused outright when the seed never
cached at all, because then nothing can be concluded either way.

Cost. Every call spends real quota (~28k input tokens). The caller must
confirm explicitly, the counts are capped, the endpoint is rate-limited, and
a pool that is already under pressure refuses the run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.core.clients.proxy import override_stream_timeouts
from app.core.conversation_archive import suppress_conversation_archive
from app.core.openai.model_registry import get_model_registry
from app.core.resilience.circuit_breaker import are_all_account_circuit_breakers_open
from app.core.resilience.degradation import is_degraded
from app.core.usage.pricing import get_pricing_for_model
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import detach_session_objects, get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.cache_isolation_probe.prefix import (
    DEFAULT_TARGET_PREFIX_TOKENS,
    build_probe_prefix,
    estimate_prefix_tokens,
    new_probe_nonce,
)
from app.modules.cache_isolation_probe.sender import CacheProbeSender, CacheProbeSenderPort, ProbeSendResult

logger = logging.getLogger(__name__)

#: Caps on what one run may spend. Five repetitions plus five siblings is ten
#: calls, about 280k input tokens -- enough to see a sporadic ~20-40% hit rate
#: and small enough that a misclick is not an incident.
MAX_SEED_REPETITIONS = 5
MAX_OTHER_ACCOUNTS = 5
#: ``other_account_count`` is an upper bound, not a demand: a pool with fewer
#: siblings still answers the question, just with less statistical weight. Only
#: this floor -- a seed plus one sibling -- is non-negotiable.
MIN_PROBE_ACCOUNTS = 2
DEFAULT_SEED_REPETITIONS = 3
DEFAULT_OTHER_ACCOUNTS = 4

#: A call counts as a cache hit when most of its input was served from cache.
#: The hand runs landed at 99%+; a few hundred incidentally cached preamble
#: tokens must not read as a hit, so the bar sits far above that and far below
#: the observed signal.
CACHE_HIT_RATIO = 0.5

#: Share of the selectable pool that may be rate-limited or quota-exceeded
#: before the probe refuses to spend more quota. A quarter of the pool being
#: throttled already means the balancer is rerouting real traffic.
MAX_PRESSURED_POOL_SHARE = 0.25

#: Per-call stream budget. A ~28k-token prompt takes appreciably longer to
#: admit than the warm-up's four-token one, and the run is sequential, so a
#: generous ceiling costs nothing but bounds a wedged call.
_PROBE_CONNECT_TIMEOUT_SECONDS = 10.0
_PROBE_IDLE_TIMEOUT_SECONDS = 60.0
_PROBE_TOTAL_TIMEOUT_SECONDS = 180.0

#: Wire vocabulary, declared once here so the schemas and the dataclasses
#: cannot drift apart.
ProbeCallRole = Literal["seed", "other"]
ProbeCallStatus = Literal["hit", "miss", "error"]
ProbeVerdict = Literal["cross_account_sharing", "no_cross_account_hit", "inconclusive"]

SEED_ROLE: ProbeCallRole = "seed"
OTHER_ROLE: ProbeCallRole = "other"

CALL_STATUS_HIT: ProbeCallStatus = "hit"
CALL_STATUS_MISS: ProbeCallStatus = "miss"
CALL_STATUS_ERROR: ProbeCallStatus = "error"

VERDICT_CROSS_ACCOUNT_SHARING: ProbeVerdict = "cross_account_sharing"
VERDICT_NO_CROSS_ACCOUNT_HIT: ProbeVerdict = "no_cross_account_hit"
VERDICT_INCONCLUSIVE: ProbeVerdict = "inconclusive"

#: One run at a time on this replica. Replica-local on purpose: the endpoint's
#: rate limiter is database-backed on a fixed key, so the *spend* is already
#: bounded across replicas, and two runs cannot contaminate each other's
#: measurement because each generates its own nonce and therefore its own
#: prefix. What is left is concurrent load, which this keeps off any one
#: replica without a distributed lock held open for minutes.
_run_lock = asyncio.Lock()


class CacheProbeRefused(Exception):
    """The probe declined to spend quota. ``code`` is the machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProbeAccount:
    account_id: str
    label: str


@dataclass(frozen=True, slots=True)
class PoolPressure:
    """Why the probe may or may not spend quota right now."""

    under_pressure: bool
    reason: str | None
    detail: str | None
    selectable_account_count: int
    eligible_account_count: int
    pressured_account_count: int


@dataclass(frozen=True, slots=True)
class ProbePlan:
    """What a run would do and cost, shown before the operator confirms."""

    model: str | None
    seed_account: ProbeAccount | None
    #: Every sibling the pool can offer, capped at ``MAX_OTHER_ACCOUNTS`` -- not
    #: the requested slice. The dashboard recomputes the cost as the operator
    #: changes the count, which it can only do correctly if it knows how many
    #: accounts actually exist.
    available_other_accounts: tuple[ProbeAccount, ...]
    seed_repetitions: int
    total_calls: int
    estimated_input_tokens_per_call: int
    estimated_total_input_tokens: int
    max_seed_repetitions: int
    max_other_accounts: int
    pressure: PoolPressure


@dataclass(frozen=True, slots=True)
class ProbeCall:
    """One row of the result table. Numeric and identifier fields only."""

    sequence: int
    account_id: str
    account_label: str
    role: ProbeCallRole
    status: ProbeCallStatus
    cache_hit: bool
    input_tokens: int | None
    cached_tokens: int | None
    latency_ms: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    run_id: str
    model: str
    started_at: datetime
    completed_at: datetime
    seed_account: ProbeAccount
    seed_repetitions: int
    calls: tuple[ProbeCall, ...]
    seed_hit_count: int
    seed_call_count: int
    other_hit_count: int
    other_call_count: int
    cross_account_hit: bool
    verdict: ProbeVerdict


def default_probe_model() -> str | None:
    """Cheapest public text model by *input* price, or ``None`` if unknown.

    The probe pays almost entirely for input tokens (~28k in, at most 16 out),
    so unlike the limit warm-up -- which ranks on input plus output because it
    sends a four-token prompt -- input price alone is the right ordering here.
    Ties break on the slug so two runs pick the same model.
    """

    candidates: list[tuple[float, str]] = []
    for model in get_model_registry().get_models_with_fallback().values():
        if not model.supported_in_api:
            continue
        if model.input_modalities and "text" not in {modality.lower() for modality in model.input_modalities}:
            continue
        resolved_price = get_pricing_for_model(model.slug)
        if resolved_price is None:
            continue
        candidates.append((resolved_price[1].input_per_1m, model.slug))
    if not candidates:
        return None
    return min(candidates)[1]


@contextlib.asynccontextmanager
async def _accounts_repo() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        try:
            yield AccountsRepository(session)
        finally:
            detach_session_objects(session)


def _account_label(account: Account) -> str:
    return account.alias or account.email or account.id


def _is_selectable(account: Account) -> bool:
    """Accounts the balancer could route to today, ignoring momentary health.

    Paused, deactivated and reauth-required accounts are operator or credential
    state, not pressure, so they leave the denominator entirely.
    """

    if account.delete_requested_at is not None or not account.chatgpt_account_id:
        return False
    return account.status in {AccountStatus.ACTIVE, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED}


def assess_pool_pressure(accounts: Sequence[Account]) -> PoolPressure:
    """Decide whether the pool can afford a diagnostic right now."""

    selectable = [account for account in accounts if _is_selectable(account)]
    eligible = [account for account in selectable if account.status == AccountStatus.ACTIVE]
    pressured = [account for account in selectable if account.status != AccountStatus.ACTIVE]

    def _verdict(reason: str | None, detail: str | None) -> PoolPressure:
        return PoolPressure(
            under_pressure=reason is not None,
            reason=reason,
            detail=detail,
            selectable_account_count=len(selectable),
            eligible_account_count=len(eligible),
            pressured_account_count=len(pressured),
        )

    if is_degraded():
        return _verdict("upstream_degraded", "The proxy is running in degraded mode.")
    if are_all_account_circuit_breakers_open():
        return _verdict("circuit_breakers_open", "Every account circuit breaker is open.")
    if len(eligible) < MIN_PROBE_ACCOUNTS:
        return _verdict(
            "insufficient_eligible_accounts",
            f"The probe needs a seed plus at least one other active account; {len(eligible)} are active.",
        )
    if selectable and len(pressured) / len(selectable) >= MAX_PRESSURED_POOL_SHARE:
        return _verdict(
            "pool_under_pressure",
            f"{len(pressured)} of {len(selectable)} routable accounts are rate-limited or quota-exceeded.",
        )
    return _verdict(None, None)


def _classify(result: ProbeSendResult) -> tuple[ProbeCallStatus, bool]:
    if not result.ok:
        return CALL_STATUS_ERROR, False
    cached = result.cached_tokens or 0
    total = result.input_tokens or 0
    hit = total > 0 and cached >= total * CACHE_HIT_RATIO
    return (CALL_STATUS_HIT if hit else CALL_STATUS_MISS), hit


def _verdict_for(*, cross_account_hit: bool, seed_hit_count: int, other_call_count: int) -> ProbeVerdict:
    if cross_account_hit:
        return VERDICT_CROSS_ACCOUNT_SHARING
    if seed_hit_count == 0 or other_call_count == 0:
        # Without a single same-account hit there is no evidence the prefix was
        # cacheable at all, so "no cross-account hit" says nothing about
        # partitioning.
        return VERDICT_INCONCLUSIVE
    return VERDICT_NO_CROSS_ACCOUNT_HIT


class CacheIsolationProbeService:
    """Plans and runs the cross-account prompt cache isolation probe."""

    def __init__(self, sender: CacheProbeSenderPort | None = None) -> None:
        self._sender = sender or CacheProbeSender(_accounts_repo)

    async def _list_accounts(self) -> list[Account]:
        async with _accounts_repo() as repo:
            return await repo.list_accounts()

    async def plan(
        self,
        *,
        seed_repetitions: int = DEFAULT_SEED_REPETITIONS,
        other_account_count: int = DEFAULT_OTHER_ACCOUNTS,
        target_prefix_tokens: int = DEFAULT_TARGET_PREFIX_TOKENS,
    ) -> ProbePlan:
        accounts = await self._list_accounts()
        seed, available = self._choose_accounts(accounts, MAX_OTHER_ACCOUNTS)
        pressure = assess_pool_pressure(accounts)
        per_call = estimate_prefix_tokens(target_prefix_tokens)
        total_calls = seed_repetitions + min(other_account_count, len(available))
        return ProbePlan(
            model=default_probe_model(),
            seed_account=(ProbeAccount(seed.id, _account_label(seed)) if seed is not None else None),
            available_other_accounts=tuple(ProbeAccount(account.id, _account_label(account)) for account in available),
            seed_repetitions=seed_repetitions,
            total_calls=total_calls,
            estimated_input_tokens_per_call=per_call,
            estimated_total_input_tokens=per_call * total_calls,
            max_seed_repetitions=MAX_SEED_REPETITIONS,
            max_other_accounts=MAX_OTHER_ACCOUNTS,
            pressure=pressure,
        )

    async def run(
        self,
        *,
        model: str | None = None,
        seed_repetitions: int = DEFAULT_SEED_REPETITIONS,
        other_account_count: int = DEFAULT_OTHER_ACCOUNTS,
        target_prefix_tokens: int = DEFAULT_TARGET_PREFIX_TOKENS,
    ) -> ProbeResult:
        resolved_model = (model or "").strip() or default_probe_model()
        if not resolved_model:
            raise CacheProbeRefused(
                "probe_model_unavailable",
                "No public text model is available to probe with.",
            )
        if not 1 <= seed_repetitions <= MAX_SEED_REPETITIONS:
            raise CacheProbeRefused(
                "seed_repetitions_out_of_range",
                f"Seed repetitions must be between 1 and {MAX_SEED_REPETITIONS}.",
            )
        if not 1 <= other_account_count <= MAX_OTHER_ACCOUNTS:
            raise CacheProbeRefused(
                "other_accounts_out_of_range",
                f"Other account count must be between 1 and {MAX_OTHER_ACCOUNTS}.",
            )

        if _run_lock.locked():
            raise CacheProbeRefused("probe_already_running", "A cache isolation probe is already running.")
        async with _run_lock:
            accounts = await self._list_accounts()
            pressure = assess_pool_pressure(accounts)
            if pressure.under_pressure:
                raise CacheProbeRefused(
                    pressure.reason or "pool_under_pressure",
                    pressure.detail or "The account pool is under pressure.",
                )
            seed, others = self._choose_accounts(accounts, other_account_count)
            if seed is None or not others:
                raise CacheProbeRefused(
                    "insufficient_eligible_accounts",
                    "The probe needs a seed plus at least one other active account.",
                )
            return await self._execute(
                model=resolved_model,
                seed=seed,
                others=others,
                seed_repetitions=seed_repetitions,
                target_prefix_tokens=target_prefix_tokens,
            )

    async def _execute(
        self,
        *,
        model: str,
        seed: Account,
        others: list[Account],
        seed_repetitions: int,
        target_prefix_tokens: int,
    ) -> ProbeResult:
        # The nonce never leaves this frame and the prefix it generates is never
        # written anywhere: only the token counts below are reported or audited.
        nonce = new_probe_nonce()
        prefix = build_probe_prefix(nonce, target_tokens=target_prefix_tokens)
        run_id = uuid.uuid4().hex
        started_at = utcnow()

        calls: list[ProbeCall] = []
        sequence = 0
        # Sequential by design: the seed must finish warming the prefix before a
        # sibling asks for it, and one upstream call in flight is also the
        # gentlest possible load on a pool that is serving real traffic.
        with (
            override_stream_timeouts(
                connect_timeout_seconds=_PROBE_CONNECT_TIMEOUT_SECONDS,
                idle_timeout_seconds=_PROBE_IDLE_TIMEOUT_SECONDS,
                total_timeout_seconds=_PROBE_TOTAL_TIMEOUT_SECONDS,
            ),
            # The corpus is generated filler, not a record of anything Codex
            # said, and ten 28k-token calls would bury the real traffic around
            # them. Keep it out of the archive even when the operator has
            # archiving on.
            suppress_conversation_archive(),
        ):
            for _ in range(seed_repetitions):
                sequence += 1
                calls.append(await self._one_call(sequence, seed, SEED_ROLE, model=model, prefix=prefix))
            for account in others:
                sequence += 1
                calls.append(await self._one_call(sequence, account, OTHER_ROLE, model=model, prefix=prefix))

        seed_calls = [call for call in calls if call.role == SEED_ROLE]
        other_calls = [call for call in calls if call.role == OTHER_ROLE]
        seed_hits = sum(1 for call in seed_calls if call.cache_hit)
        other_hits = sum(1 for call in other_calls if call.cache_hit)
        cross_account_hit = other_hits > 0
        answered_other_calls = sum(1 for call in other_calls if call.status != CALL_STATUS_ERROR)

        result = ProbeResult(
            run_id=run_id,
            model=model,
            started_at=started_at,
            completed_at=utcnow(),
            seed_account=ProbeAccount(seed.id, _account_label(seed)),
            seed_repetitions=seed_repetitions,
            calls=tuple(calls),
            seed_hit_count=seed_hits,
            seed_call_count=len(seed_calls),
            other_hit_count=other_hits,
            other_call_count=len(other_calls),
            cross_account_hit=cross_account_hit,
            verdict=_verdict_for(
                cross_account_hit=cross_account_hit,
                seed_hit_count=seed_hits,
                other_call_count=answered_other_calls,
            ),
        )
        logger.info(
            "Cache isolation probe %s finished: verdict=%s seed_hits=%d/%d other_hits=%d/%d",
            run_id,
            result.verdict,
            seed_hits,
            len(seed_calls),
            other_hits,
            len(other_calls),
        )
        return result

    async def _one_call(
        self,
        sequence: int,
        account: Account,
        role: ProbeCallRole,
        *,
        model: str,
        prefix: str,
    ) -> ProbeCall:
        # One account failing must not abort the run: the rows already
        # collected are the measurement and the remaining accounts still carry
        # signal. The sender classifies the failures it understands; anything
        # else becomes an untyped failed row here, reported by exception type
        # only because its message has not been proved credential-safe.
        try:
            send_result = await self._sender.send(account.id, model=model, prefix=prefix)
        except Exception as exc:
            logger.warning("Cache isolation probe call failed for account %s", account.id, exc_info=True)
            send_result = ProbeSendResult(
                ok=False,
                latency_ms=0,
                error_code="probe_call_failed",
                error_message=type(exc).__name__,
            )
        status, hit = _classify(send_result)
        return ProbeCall(
            sequence=sequence,
            account_id=account.id,
            account_label=_account_label(account),
            role=role,
            status=status,
            cache_hit=hit,
            input_tokens=send_result.input_tokens,
            cached_tokens=send_result.cached_tokens,
            latency_ms=send_result.latency_ms,
            error_code=send_result.error_code,
        )

    def _choose_accounts(
        self, accounts: Sequence[Account], other_account_count: int
    ) -> tuple[Account | None, list[Account]]:
        """Pick the seed and its siblings deterministically by account id.

        A stable order means two runs compare like with like, which is the whole
        point of running the probe before and after a settings change.
        """

        eligible = sorted(
            (account for account in accounts if _is_selectable(account) and account.status == AccountStatus.ACTIVE),
            key=lambda account: account.id,
        )
        # Upstream identifies a caller by `chatgpt_account_id`, the Team/Business
        # WORKSPACE identity: two seats of one workspace are one account there.
        # Including both would let a same-account cache hit be reported as
        # `cross_account_sharing`, which is the probe's headline claim — and it
        # would make an after-isolation re-run read as a failure. Keep the first
        # seat of each workspace; a row with no workspace id is its own group.
        deduped: list[Account] = []
        seen_workspaces: set[str] = set()
        for account in eligible:
            workspace = account.chatgpt_account_id
            if workspace is not None:
                if workspace in seen_workspaces:
                    continue
                seen_workspaces.add(workspace)
            deduped.append(account)
        if not deduped:
            return None, []
        return deduped[0], deduped[1 : 1 + other_account_count]
