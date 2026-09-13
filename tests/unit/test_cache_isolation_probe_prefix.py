"""The probe's filler prefix must be identical inside a run and unique across runs.

Both properties are load-bearing. If two calls of one run differ by a byte the
second can never hit and the probe silently reports isolation that is not
there. If two runs share a prefix the second run measures the first run's
cache instead of cross-account sharing.
"""

from __future__ import annotations

import pytest

from app.modules.cache_isolation_probe.prefix import (
    DEFAULT_TARGET_PREFIX_TOKENS,
    MAX_TARGET_PREFIX_TOKENS,
    MIN_TARGET_PREFIX_TOKENS,
    build_probe_prefix,
    estimate_prefix_tokens,
    new_probe_nonce,
)

pytestmark = pytest.mark.unit

_HEADER_LINES = 5


def _body_lines(prefix: str) -> list[str]:
    return prefix.splitlines()[_HEADER_LINES:-1]


def test_prefix_is_byte_identical_for_the_same_nonce() -> None:
    nonce = new_probe_nonce()

    assert build_probe_prefix(nonce) == build_probe_prefix(nonce)


def test_prefix_differs_entirely_between_runs() -> None:
    first = build_probe_prefix(new_probe_nonce())
    second = build_probe_prefix(new_probe_nonce())

    assert first != second
    # Upstream matches on a prefix, so the *first* line must already diverge;
    # a nonce that only appears at the end would leave ~28k shared tokens.
    assert first.splitlines()[0] != second.splitlines()[0]
    assert set(_body_lines(first)).isdisjoint(_body_lines(second))


def test_generated_filler_lines_are_unique_within_a_run() -> None:
    body = _body_lines(build_probe_prefix(new_probe_nonce()))

    assert len(body) == len(set(body))


def test_prefix_marks_itself_inert_at_both_ends() -> None:
    nonce = new_probe_nonce()

    prefix = build_probe_prefix(nonce)

    assert prefix.startswith("BEGIN CODEX-LB CACHE ISOLATION PROBE REFERENCE CORPUS")
    assert prefix.rstrip().endswith(f"END CODEX-LB CACHE ISOLATION PROBE REFERENCE CORPUS run={nonce}")
    assert "Do not read it" in prefix
    assert prefix.count(nonce) >= 2


def test_prefix_size_tracks_the_estimate_used_for_the_cost_preview() -> None:
    prefix = build_probe_prefix(new_probe_nonce())
    estimate = estimate_prefix_tokens()

    # The estimate is what the operator is shown before confirming; it must not
    # understate the real size. One word per token is the working assumption.
    words = len(prefix.split())
    assert words <= estimate
    assert estimate == pytest.approx(DEFAULT_TARGET_PREFIX_TOKENS, rel=0.05)


@pytest.mark.parametrize("requested", [0, -5, MIN_TARGET_PREFIX_TOKENS, MAX_TARGET_PREFIX_TOKENS * 4])
def test_target_token_requests_are_clamped_to_the_supported_band(requested: int) -> None:
    estimate = estimate_prefix_tokens(requested)

    assert MIN_TARGET_PREFIX_TOKENS * 0.9 <= estimate <= MAX_TARGET_PREFIX_TOKENS * 1.1


def test_empty_nonce_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_probe_prefix("")
