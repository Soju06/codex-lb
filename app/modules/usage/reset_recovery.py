from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence

from app.core.clients.usage_probe import DEFAULT_PROBE_MODEL
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import get_background_session
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.usage.repository import UsageWindowWrite

RECOVERABLE_STATUSES = (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)
HARD_BLOCKED_STATUSES = (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED)


def reset_confirmed(
    account: Account,
    before: Mapping[str, UsageHistory | None],
    after: Sequence[UsageWindowWrite],
    *,
    operator_reset: bool,
) -> bool:
    """Require a reopened corresponding window, never just a healthy reading."""
    if account.status not in RECOVERABLE_STATUSES or not after:
        return False
    if any(not math.isfinite(row.used_percent) or not 0 <= row.used_percent < 100 for row in after):
        return False
    reported = {row.window for row in after}
    if any(
        row is not None
        and row.used_percent >= 100
        and row.reset_at is not None
        and row.reset_at > time.time()
        and window not in reported
        for window, row in before.items()
    ):
        return False
    for row in after:
        previous = before.get(row.window)
        if previous is None or previous.reset_at is None or row.reset_at is None:
            continue
        if previous.window_minutes != row.window_minutes or not row.window_minutes:
            continue
        if not math.isfinite(previous.used_percent) or row.used_percent >= previous.used_percent:
            continue
        if row.reset_at < previous.reset_at + 60 or row.reset_at <= time.time():
            continue
        if (
            not operator_reset
            and account.blocked_at is not None
            and naive_utc_to_epoch(previous.recorded_at) <= account.blocked_at
        ):
            continue
        if not operator_reset and (account.reset_at is None or abs(previous.reset_at - account.reset_at) > 5):
            continue
        return True
    return False


async def claim_rollover_wake(account: Account, windows: Sequence[UsageWindowWrite]) -> int | None:
    """Persist the attempt before I/O, including failures and worker restarts."""
    if account.status in HARD_BLOCKED_STATUSES or not account.chatgpt_account_id:
        return None
    for row in windows:
        if row.used_percent < 100 or row.reset_at is None or row.reset_at > time.time():
            continue
        async with get_background_session() as session:
            attempt = await LimitWarmupRepository(session).try_create_attempt(
                account_id=account.id,
                window=f"reset_recovery:{row.window}",
                reset_at=row.reset_at,
                model=DEFAULT_PROBE_MODEL,
                attempted_at=utcnow(),
                reset_at_tolerance_seconds=5,
            )
            if attempt is not None:
                return attempt.id
    return None


async def complete_rollover_wake(attempt_id: int, *, succeeded: bool) -> None:
    async with get_background_session() as session:
        await LimitWarmupRepository(session).complete_attempt(
            attempt_id,
            status="success" if succeeded else "failed",
            completed_at=utcnow(),
            error_code=None if succeeded else "reset_not_confirmed",
        )
