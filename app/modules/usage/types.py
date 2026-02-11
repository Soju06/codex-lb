from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UsageEntryWrite:
    account_id: str
    used_percent: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    recorded_at: datetime | None = None
    window: str | None = None
    reset_at: int | None = None
    window_minutes: int | None = None
    credits_has: bool | None = None
    credits_unlimited: bool | None = None
    credits_balance: float | None = None
