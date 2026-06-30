from __future__ import annotations

import random
import re

# Units the retry hint may use, longest-first within each family so that, for
# example, ``ms`` is preferred over ``m`` and ``minutes`` over ``m``.
_UNIT_ALTERNATION = r"ms|milliseconds?|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s"

# A unit literal is only valid when it is not immediately followed by another
# letter, otherwise the single-letter alternatives swallow the prefix of an
# unsupported longer word (``m`` from ``month``, ``h`` from ``half``) and the
# hint is silently mis-scaled. Digits still follow units in compound hints
# (``6m0s``), so the boundary forbids letters only, not digits or whitespace.
_UNIT_BOUNDARY = r"(?![A-Za-z])"

# Seconds-per-unit for every literal ``_UNIT_ALTERNATION`` can capture.
_UNIT_SECONDS: dict[str, float] = {
    "ms": 0.001,
    "millisecond": 0.001,
    "milliseconds": 0.001,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
}

# Capture the contiguous run of ``<number><unit>`` components that immediately
# follows "try again in" so compound hints such as ``6m0s`` or ``1h2m3s`` are
# read in full instead of stopping at the first unit.
_RETRY_PATTERN = re.compile(rf"(?i)try again in\s*((?:\d+(?:\.\d+)?\s*(?:{_UNIT_ALTERNATION}){_UNIT_BOUNDARY}\s*)+)")
_DURATION_TOKEN = re.compile(rf"(?i)(\d+(?:\.\d+)?)\s*({_UNIT_ALTERNATION}){_UNIT_BOUNDARY}")

_BACKOFF_INITIAL_DELAY_MS = 200
_BACKOFF_FACTOR = 2.0
_BACKOFF_JITTER_MIN = 0.9
_BACKOFF_JITTER_MAX = 1.1


def parse_retry_after(message: str) -> float | None:
    match = _RETRY_PATTERN.search(message or "")
    if not match:
        return None
    total = 0.0
    matched = False
    for value, unit in _DURATION_TOKEN.findall(match.group(1)):
        multiplier = _UNIT_SECONDS.get(unit.lower())
        if multiplier is None:
            continue
        if unit.lower() == "ms":
            total += float(value) / 1000
        else:
            total += float(value) * multiplier
        matched = True
    if not matched:
        return None
    return total


def backoff_seconds(attempt: int) -> float:
    if attempt < 1:
        attempt = 1
    exponent = _BACKOFF_FACTOR ** (attempt - 1)
    base_ms = _BACKOFF_INITIAL_DELAY_MS * exponent
    jitter = random.uniform(_BACKOFF_JITTER_MIN, _BACKOFF_JITTER_MAX)
    return (base_ms * jitter) / 1000.0
