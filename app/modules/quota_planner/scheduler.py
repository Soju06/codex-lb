from __future__ import annotations

import asyncio
import importlib
import logging
from datetime import datetime, timezone
from typing import Protocol, cast

from app.core.config.settings import get_settings
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.load_balancer import _build_states
from app.modules.quota_planner.logic import build_demand_forecast, plan_shadow_actions, simulate_pool
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.quota_planner.warmup import QuotaWarmupService
from app.modules.usage.repository import UsageRepository

logger = logging.getLogger(__name__)


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


class QuotaPlannerScheduler:
    def __init__(self, *, interval_seconds: int = 300, enabled: bool = True) -> None:
        self._interval_seconds = interval_seconds
        self._enabled = enabled
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.warning("Quota planner tick failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> None:
        if not await _get_leader_election().try_acquire():
            return
        async with get_background_session() as session:
            planner_repo = QuotaPlannerRepository(session)
            settings = await planner_repo.get_settings()
            if settings.mode == "off":
                return
            accounts_repo = AccountsRepository(session)
            usage_repo = UsageRepository(session)
            accounts = await accounts_repo.list_accounts()
            latest_primary, latest_secondary = await asyncio.gather(
                usage_repo.latest_by_account(),
                usage_repo.latest_by_account(window="secondary"),
            )
            states, _ = _build_states(
                accounts=accounts,
                latest_primary=latest_primary,
                latest_secondary=latest_secondary,
                runtime={},
            )
            now = datetime.now(timezone.utc)
            demand_bins = await planner_repo.aggregate_demand_bins()
            forecast = build_demand_forecast(settings=settings, bins=demand_bins, now=now)
            base_simulation = simulate_pool(settings=settings, states=states, demand_forecast=forecast, now=now)
            actions = plan_shadow_actions(settings=settings, states=states, demand_forecast=forecast, now=now)
            if not actions:
                await planner_repo.log_decision(
                    mode=settings.mode,
                    action="no_op",
                    scheduled_at=None,
                    score=0.0,
                    reason=f"loss={base_simulation.loss:.2f};unmet={base_simulation.unmet_demand:.2f}",
                    status="skipped",
                    idempotency_key=f"{now:%Y%m%d%H%M}:{settings.mode}:no_op",
                )
                return
            scenario = simulate_pool(
                settings=settings,
                states=states,
                demand_forecast=forecast,
                planned_warmups=actions,
                now=now,
            )
            expected_gain = max(0.0, base_simulation.loss - scenario.loss)
            warmup_service = QuotaWarmupService(session)
            for action in actions:
                key = f"{now:%Y%m%d%H%M}:{settings.mode}:{action.account_id}:{action.action}"
                await planner_repo.log_decision(
                    mode=settings.mode,
                    action=action.action,
                    account_id=action.account_id,
                    scheduled_at=action.scheduled_at,
                    score=action.score + expected_gain,
                    reason=f"{action.reason};gain={expected_gain:.2f};unmet={scenario.unmet_demand:.2f}",
                    status="planned" if settings.mode in {"suggest", "auto"} else "skipped",
                    idempotency_key=key,
                )
                if settings.mode == "auto" and action.action == "warmup":
                    await warmup_service.warm_now(account_id=action.account_id)


def build_quota_planner_scheduler() -> QuotaPlannerScheduler:
    settings = get_settings()
    return QuotaPlannerScheduler(
        interval_seconds=max(60, getattr(settings, "quota_planner_tick_seconds", 300)),
        enabled=getattr(settings, "quota_planner_scheduler_enabled", True),
    )
