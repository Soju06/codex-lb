from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetryOptions:
    """Configuration for exponential backoff retry behavior.

    Attributes:
        attempts: Total number of attempts (including initial request)
        start_timeout: Initial timeout in seconds
        max_timeout: Maximum timeout in seconds
        factor: Exponential growth factor for backoff
        retryable_statuses: HTTP status codes that trigger a retry
    """

    attempts: int = 3
    start_timeout: float = 0.5
    max_timeout: float = 2.0
    factor: float = 2.0
    retryable_statuses: frozenset[int] = field(default_factory=lambda: frozenset({408, 429, 500, 502, 503, 504}))


def calculate_backoff_delay(
    attempt: int,
    start_timeout: float,
    max_timeout: float,
    factor: float,
) -> float:
    """Calculate exponential backoff delay with jitter.

    Computes exponential backoff with jitter to avoid thundering herd.
    Jitter is applied as a random multiplier in the range [0.5, 1.0].

    Args:
        attempt: Current attempt number (0-indexed)
        start_timeout: Initial timeout in seconds
        max_timeout: Maximum timeout in seconds
        factor: Exponential growth factor

    Returns:
        Delay in seconds with jitter applied (50-100% of calculated delay)
    """
    delay = min(
        start_timeout * (factor**attempt),
        max_timeout,
    )
    # jitter: [50%, 100%] of delay
    delay *= 0.5 + random.random() * 0.5
    return delay
