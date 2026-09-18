from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from app.core.providers import openrouter as openrouter_provider
from app.db.models import UsageHistory

OPENROUTER_CREDITS_URL = openrouter_provider.OPENROUTER_CREDITS_URL
OPENROUTER_PROVIDER_NAME = openrouter_provider.OPENROUTER_PROVIDER_NAME
OPENROUTER_UPSTREAM_BASE_URL = openrouter_provider.OPENROUTER_UPSTREAM_BASE_URL
SUPPORTS_UPSTREAM_COUNT_TOKENS = openrouter_provider.SUPPORTS_UPSTREAM_COUNT_TOKENS

logger = logging.getLogger(__name__)

CREDITS_USAGE_WINDOW = "credits"
OPENROUTER_CREDITS_QUOTA_KEY = "openrouter_credits"


@dataclass(frozen=True, slots=True)
class CreditsWindow:
    balance: float
    cap: float | None = None
    spent: float | None = None


def credits_exhausted(window: CreditsWindow) -> bool:
    """Balance at or below zero, or spent at the cap. Never a used-percent check."""
    if window.balance <= 0:
        return True
    if window.cap is not None and window.spent is not None and window.spent >= window.cap:
        return True
    return False


def window_from_usage(entry: UsageHistory | None) -> CreditsWindow | None:
    if entry is None or entry.credits_balance is None:
        return None
    return CreditsWindow(
        balance=float(entry.credits_balance),
        cap=None if entry.credits_cap is None else float(entry.credits_cap),
        spent=None if entry.credits_spent is None else float(entry.credits_spent),
    )


def window_from_parts(
    *,
    balance: float | None,
    cap: float | None,
    spent: float | None,
) -> CreditsWindow | None:
    if balance is None and cap is None and spent is None:
        return None
    if balance is None and cap is not None and spent is not None:
        balance = cap - spent
    if balance is None:
        return None
    if spent is None and cap is not None:
        spent = cap - balance
    return CreditsWindow(balance=float(balance), cap=cap, spent=spent)


async def fetch_openrouter_credits(api_key: str) -> CreditsWindow | None:
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                OPENROUTER_CREDITS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            ) as response:
                if response.status != 200:
                    logger.warning("openrouter_credits_fetch_failed status=%s", response.status)
                    return None
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        logger.warning("openrouter_credits_fetch_failed", exc_info=True)
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        logger.warning("openrouter_credits_fetch_failed reason=missing_data")
        return None
    total = data.get("total_credits")
    used = data.get("total_usage")
    if not isinstance(total, (int, float)) or not isinstance(used, (int, float)):
        logger.warning("openrouter_credits_fetch_failed reason=missing_totals")
        return None
    cap = float(total)
    spent = float(used)
    return CreditsWindow(balance=cap - spent, cap=cap, spent=spent)
