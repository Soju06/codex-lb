from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import Account, DashboardSettings, UsageHistory
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.limit_warmup.service import (
    RESET_AT_JITTER_TOLERANCE_SECONDS,
    UsageResetEvidence,
    effective_warmup_usage_entry,
    selected_warmup_windows,
    usage_reset_confirmed,
)
from app.modules.usage.repository import UsageRepository


async def recover_current_reset_evidence(
    *,
    accounts: list[Account],
    settings: DashboardSettings,
    primary: dict[str, UsageHistory],
    secondary: dict[str, UsageHistory],
    usage_repo: UsageRepository,
    warmup_repo: LimitWarmupRepository,
    now: datetime,
) -> dict[tuple[str, str], UsageResetEvidence]:
    """Recover unconsumed reset pairs without changing the current usage snapshot."""
    evidence: dict[tuple[str, str], UsageResetEvidence] = {}
    if not settings.limit_warmup_enabled:
        return evidence
    now_epoch = now.replace(tzinfo=timezone.utc).timestamp()
    for account in accounts:
        if not account.limit_warmup_enabled:
            continue
        for selected_window in selected_warmup_windows(settings.limit_warmup_windows):
            latest = effective_warmup_usage_entry(
                account.id, window=selected_window, primary=primary, secondary=secondary
            )
            if latest is None or latest.reset_at is None or latest.reset_at <= now_epoch:
                continue
            window = latest.window or "primary"
            claim_window = "monthly" if window == "monthly" else selected_window
            if await warmup_repo.has_attempt(
                account.id, claim_window, latest.reset_at, tolerance_seconds=RESET_AT_JITTER_TOLERANCE_SECONDS
            ):
                continue
            since = await usage_repo.first_reset_observed_at(
                account.id, window, latest.reset_at, tolerance_seconds=RESET_AT_JITTER_TOLERANCE_SECONDS
            )
            if since is None:
                continue
            history = await usage_repo.history_since(account.id, window, since, include_predecessor=True)
            for before, after in zip(history, history[1:]):
                if (after.recorded_at, after.id) > (latest.recorded_at, latest.id):
                    break
                if (
                    after.reset_at is not None
                    and abs(after.reset_at - latest.reset_at) <= RESET_AT_JITTER_TOLERANCE_SECONDS
                    and usage_reset_confirmed(before=before, after=after)
                ):
                    evidence[account.id, window] = UsageResetEvidence(before=before, after=after)
    return evidence
