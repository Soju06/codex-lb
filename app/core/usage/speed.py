from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

# A sampling boundary, never a cap on observed speed or a padded denominator.
MIN_GENERATION_WINDOW_MS = 100
MIN_OUTPUT_DELTA_COUNT = 2

GenerationTpsStatus = Literal[
    "estimated",
    "legacy_estimate",
    "insufficient_sample",
    "missing_usage",
    "missing_timing",
    "invalid_sample",
    "incomplete",
]


class GenerationSpeedLog(Protocol):
    status: str
    output_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: int | None
    latency_upstream_terminal_ms: int | None
    latency_first_token_ms: int | None
    latency_first_output_ms: int | None
    output_delta_count: int | None


@dataclass(frozen=True)
class GenerationSpeed:
    tps: float | None
    status: GenerationTpsStatus


def generation_speed_from_log(log: GenerationSpeedLog) -> GenerationSpeed:
    """Estimate observed non-reasoning output speed without inventing evidence.

    Even qualified samples include network framing effects. Legacy rows retain
    their historical TTFT-based estimate but must not enter qualified medians.
    """
    if log.status != "success":
        return GenerationSpeed(None, "incomplete")
    if log.output_tokens is None or log.reasoning_tokens is None:
        return GenerationSpeed(None, "missing_usage")
    if log.reasoning_tokens < 0 or log.output_tokens < log.reasoning_tokens:
        return GenerationSpeed(None, "invalid_sample")
    output_count = log.output_tokens - log.reasoning_tokens
    if output_count == 0:
        return GenerationSpeed(None, "insufficient_sample")
    total_ms = log.latency_ms
    ttft_ms = log.latency_first_token_ms
    if total_ms is None or ttft_ms is None:
        return GenerationSpeed(None, "missing_timing")
    if ttft_ms < 0 or total_ms < ttft_ms:
        return GenerationSpeed(None, "invalid_sample")

    first_output_ms = log.latency_first_output_ms
    chunks = log.output_delta_count
    terminal_ms = log.latency_upstream_terminal_ms
    legacy = first_output_ms is None and chunks is None and terminal_ms is None
    if legacy:
        first_output_ms = ttft_ms
        terminal_ms = total_ms
    else:
        if chunks is not None and chunks < 0:
            return GenerationSpeed(None, "invalid_sample")
        if chunks is not None and chunks < MIN_OUTPUT_DELTA_COUNT:
            return GenerationSpeed(None, "insufficient_sample")
        if first_output_ms is None or chunks is None or terminal_ms is None:
            return GenerationSpeed(None, "missing_timing")
        if not ttft_ms <= first_output_ms <= terminal_ms <= total_ms:
            return GenerationSpeed(None, "invalid_sample")

    generation_ms = terminal_ms - first_output_ms
    if generation_ms < MIN_GENERATION_WINDOW_MS:
        return GenerationSpeed(None, "insufficient_sample")
    return GenerationSpeed(output_count * 1000.0 / generation_ms, "legacy_estimate" if legacy else "estimated")
