from __future__ import annotations

from dataclasses import dataclass as _dc
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.usage.depletion_service import (
    DepletionMetrics,
    attach_depletion_history_signature,
    compute_aggregate_depletion,
    compute_depletion_for_account,
    prune_depletion_cache,
    reset_ewma_state,
)

pytestmark = pytest.mark.unit

BASE_TIME = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)


@_dc
class _FakeEntry:
    account_id: str
    used_percent: float
    recorded_at: datetime
    reset_at: int | None
    window_minutes: int | None


def _entry(
    used_percent: float,
    recorded_at: datetime,
    reset_at: int | None = None,
    window_minutes: int | None = 300,
    account_id: str = "acc1",
) -> _FakeEntry:
    return _FakeEntry(
        account_id=account_id,
        used_percent=used_percent,
        recorded_at=recorded_at,
        reset_at=reset_at,
        window_minutes=window_minutes,
    )


def _signed(history: list[_FakeEntry]) -> list[_FakeEntry]:
    return attach_depletion_history_signature(history)


def test_depletion_metrics_dataclass_shape() -> None:
    m = DepletionMetrics(
        risk=0.5,
        risk_level="warning",
        rate_per_second=0.001,
        burn_rate=1.5,
        safe_usage_percent=50.0,
        projected_exhaustion_at=None,
        seconds_until_exhaustion=None,
    )
    assert m.risk == pytest.approx(0.5)
    assert m.risk_level == "warning"
    assert m.rate_per_second == pytest.approx(0.001)


def test_compute_depletion_insufficient_history() -> None:
    reset_ewma_state()
    history = [_entry(10.0, BASE_TIME)]  # only 1 point
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=5)
    )
    assert result is None


def test_compute_depletion_sufficient_history() -> None:
    reset_ewma_state()
    history = [
        _entry(10.0, BASE_TIME),
        _entry(15.0, BASE_TIME + timedelta(minutes=1)),
    ]
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=2)
    )
    assert result is not None
    assert isinstance(result, DepletionMetrics)
    assert 0.0 <= result.risk <= 1.0
    assert result.risk_level in ("safe", "warning", "danger", "critical")


def test_compute_depletion_zero_rate_is_safe() -> None:
    reset_ewma_state()
    # Flat usage — no increase → rate=0 → risk = used_percent/100
    history = [
        _entry(50.0, BASE_TIME),
        _entry(50.0, BASE_TIME + timedelta(minutes=1)),
        _entry(50.0, BASE_TIME + timedelta(minutes=2)),
    ]
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=3)
    )
    assert result is not None
    # used=50%, rate=0 → projected=50% → risk=0.5
    assert result.risk == pytest.approx(0.5, abs=0.01)


def test_compute_depletion_window_reset_handled() -> None:
    reset_ewma_state()
    # Usage drops from 90% to 5% — window reset
    history = [
        _entry(90.0, BASE_TIME),
        _entry(95.0, BASE_TIME + timedelta(minutes=1)),
        _entry(5.0, BASE_TIME + timedelta(minutes=2)),  # reset
    ]
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=3)
    )
    # After reset, EWMA state resets — may return None or low risk
    if result is not None:
        assert 0.0 <= result.risk <= 1.0


def test_compute_depletion_empty_history() -> None:
    reset_ewma_state()
    result = compute_depletion_for_account("acc1", "codex_other", "primary", [], now=BASE_TIME)
    assert result is None


def test_aggregate_depletion_max_risk() -> None:
    metrics = [
        DepletionMetrics(
            risk=0.3,
            risk_level="safe",
            rate_per_second=0.001,
            burn_rate=0.5,
            safe_usage_percent=50.0,
            projected_exhaustion_at=None,
            seconds_until_exhaustion=None,
        ),
        DepletionMetrics(
            risk=0.8,
            risk_level="danger",
            rate_per_second=0.005,
            burn_rate=2.0,
            safe_usage_percent=50.0,
            projected_exhaustion_at=None,
            seconds_until_exhaustion=None,
        ),
        DepletionMetrics(
            risk=0.5,
            risk_level="warning",
            rate_per_second=0.002,
            burn_rate=1.0,
            safe_usage_percent=50.0,
            projected_exhaustion_at=None,
            seconds_until_exhaustion=None,
        ),
    ]
    result = compute_aggregate_depletion(metrics)
    assert result is not None
    assert result.risk == pytest.approx(0.8)
    assert result.risk_level == "danger"


def test_aggregate_depletion_empty_returns_none() -> None:
    result = compute_aggregate_depletion([])
    assert result is None


def test_aggregate_depletion_all_none_returns_none() -> None:
    result = compute_aggregate_depletion([None, None])
    assert result is None


def test_aggregate_depletion_single_metric() -> None:
    metrics = [
        DepletionMetrics(
            risk=0.7,
            risk_level="warning",
            rate_per_second=0.003,
            burn_rate=1.5,
            safe_usage_percent=60.0,
            projected_exhaustion_at=None,
            seconds_until_exhaustion=None,
        )
    ]
    result = compute_aggregate_depletion(metrics)
    assert result is not None
    assert result.risk == pytest.approx(0.7)
    assert result.risk_level == "warning"


def test_reset_ewma_state_clears_state() -> None:
    reset_ewma_state()
    history = [
        _entry(10.0, BASE_TIME),
        _entry(20.0, BASE_TIME + timedelta(minutes=1)),
    ]
    # First call — builds state
    compute_depletion_for_account("acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=2))
    # Reset
    reset_ewma_state()
    # After reset, single point returns None
    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", [_entry(10.0, BASE_TIME)], now=BASE_TIME + timedelta(minutes=3)
    )
    assert result is None


def test_repeated_calls_with_same_history_are_idempotent() -> None:
    """R5-F1: Replaying the same history must not cause EWMA drift."""
    reset_ewma_state()
    history = [
        _entry(10.0, BASE_TIME),
        _entry(15.0, BASE_TIME + timedelta(minutes=1)),
        _entry(20.0, BASE_TIME + timedelta(minutes=2)),
    ]
    now = BASE_TIME + timedelta(minutes=3)

    # First call computes initial metrics
    result1 = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert result1 is not None

    # Repeated calls with same history must return identical risk (no drift)
    result2 = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert result2 is not None
    assert result2.risk == pytest.approx(result1.risk)
    assert result2.rate_per_second == pytest.approx(result1.rate_per_second)

    result3 = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert result3 is not None
    assert result3.risk == pytest.approx(result1.risk)
    assert result3.rate_per_second == pytest.approx(result1.rate_per_second)


def test_new_entries_still_update_ewma_state() -> None:
    """R5-F1: New entries beyond the last timestamp must still be processed."""
    reset_ewma_state()
    history_batch1 = [
        _entry(10.0, BASE_TIME),
        _entry(15.0, BASE_TIME + timedelta(minutes=1)),
    ]
    now1 = BASE_TIME + timedelta(minutes=2)
    result1 = compute_depletion_for_account("acc1", "codex_other", "primary", history_batch1, now=now1)
    assert result1 is not None

    # Second call with additional newer entries
    history_batch2 = history_batch1 + [
        _entry(25.0, BASE_TIME + timedelta(minutes=2)),
        _entry(35.0, BASE_TIME + timedelta(minutes=3)),
    ]
    now2 = BASE_TIME + timedelta(minutes=4)
    result2 = compute_depletion_for_account("acc1", "codex_other", "primary", history_batch2, now=now2)
    assert result2 is not None
    # Rate should be higher now (usage accelerated from 5%/min to 10%/min)
    assert result2.rate_per_second > result1.rate_per_second


def test_aged_out_samples_do_not_keep_stale_ewma_influence() -> None:
    reset_ewma_state()
    full_window_history = [
        _entry(10.0, BASE_TIME),
        _entry(70.0, BASE_TIME + timedelta(minutes=1)),
        _entry(80.0, BASE_TIME + timedelta(minutes=2)),
    ]
    full_window_result = compute_depletion_for_account(
        "acc1",
        "codex_other",
        "primary",
        full_window_history,
        now=BASE_TIME + timedelta(minutes=3),
    )
    assert full_window_result is not None

    in_window_history = full_window_history[1:]
    in_window_result = compute_depletion_for_account(
        "acc1",
        "codex_other",
        "primary",
        in_window_history,
        now=BASE_TIME + timedelta(minutes=3),
    )
    assert in_window_result is not None
    assert in_window_result.rate_per_second == pytest.approx(10.0 / 60.0)
    assert in_window_result.rate_per_second < full_window_result.rate_per_second


def test_repeated_call_with_unchanged_history_skips_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #537: dashboard polls must reuse the cached EWMA state when the
    in-window history is unchanged, instead of replaying every usage row."""
    from app.modules.usage import depletion_service

    reset_ewma_state()
    history = _signed(
        [
            _entry(10.0, BASE_TIME),
            _entry(20.0, BASE_TIME + timedelta(minutes=1)),
            _entry(30.0, BASE_TIME + timedelta(minutes=2)),
        ]
    )
    now = BASE_TIME + timedelta(minutes=3)

    rebuild_calls = 0
    digest_rebuild_calls = 0
    real_rebuild = depletion_service._rebuild_ewma_state
    real_digest_rebuild = depletion_service._history_signature_from_rows

    def _counting_rebuild(history_arg):
        nonlocal rebuild_calls
        rebuild_calls += 1
        return real_rebuild(history_arg)

    def _counting_digest_rebuild(history_arg):
        nonlocal digest_rebuild_calls
        digest_rebuild_calls += 1
        return real_digest_rebuild(history_arg)

    monkeypatch.setattr(depletion_service, "_rebuild_ewma_state", _counting_rebuild)
    monkeypatch.setattr(depletion_service, "_history_signature_from_rows", _counting_digest_rebuild)

    first = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert first is not None
    assert rebuild_calls == 1
    assert digest_rebuild_calls == 0

    # Subsequent polls with the exact same in-window history must not re-walk
    # the history or rebuild a full-row signature. The result must remain
    # identical.
    second = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert second is not None
    assert rebuild_calls == 1
    assert digest_rebuild_calls == 0
    assert second.rate_per_second == pytest.approx(first.rate_per_second)
    assert second.risk == pytest.approx(first.risk)

    third = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert third is not None
    assert rebuild_calls == 1
    assert digest_rebuild_calls == 0


def test_signature_cache_stores_compact_digest_not_per_row_tuple() -> None:
    """The retained cache signature should stay bounded for large histories."""
    from app.modules.usage import depletion_service

    reset_ewma_state()
    history = _signed([_entry(float(index), BASE_TIME + timedelta(minutes=index)) for index in range(100)])

    result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=101)
    )

    assert result is not None
    signature = depletion_service._history_signatures[("acc1", "codex_other", "primary")]
    assert signature.row_count == 100
    # A single fixed-width hash, not a per-row structure.
    assert isinstance(signature.content_digest, int)


def test_prune_depletion_cache_drops_absent_account_window_entries() -> None:
    """Dashboard lifecycle pruning prevents churned account/window keys from
    accumulating in the EWMA and signature caches."""
    from app.modules.usage import depletion_service

    reset_ewma_state()
    primary_history = _signed(
        [
            _entry(10.0, BASE_TIME, account_id="acc1"),
            _entry(20.0, BASE_TIME + timedelta(minutes=1), account_id="acc1"),
        ]
    )
    secondary_history = _signed(
        [
            _entry(30.0, BASE_TIME, account_id="acc2"),
            _entry(40.0, BASE_TIME + timedelta(minutes=1), account_id="acc2"),
        ]
    )

    primary = compute_depletion_for_account(
        "acc1", "codex_other", "primary", primary_history, now=BASE_TIME + timedelta(minutes=2)
    )
    secondary = compute_depletion_for_account(
        "acc2", "codex_other", "secondary", secondary_history, now=BASE_TIME + timedelta(minutes=2)
    )

    assert primary is not None
    assert secondary is not None
    assert set(depletion_service._history_signatures) == {
        ("acc1", "codex_other", "primary"),
        ("acc2", "codex_other", "secondary"),
    }

    prune_depletion_cache({("acc1", "codex_other", "primary")})

    assert set(depletion_service._history_signatures) == {("acc1", "codex_other", "primary")}
    assert set(depletion_service._ewma_states) == {("acc1", "codex_other", "primary")}


def test_new_history_row_invalidates_memoized_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a new usage row lands the cache must rebuild so the rate reflects
    the latest observations."""
    from app.modules.usage import depletion_service

    reset_ewma_state()
    history = _signed(
        [
            _entry(10.0, BASE_TIME),
            _entry(15.0, BASE_TIME + timedelta(minutes=1)),
        ]
    )

    rebuild_calls = 0
    real_rebuild = depletion_service._rebuild_ewma_state

    def _counting_rebuild(history_arg):
        nonlocal rebuild_calls
        rebuild_calls += 1
        return real_rebuild(history_arg)

    monkeypatch.setattr(depletion_service, "_rebuild_ewma_state", _counting_rebuild)

    first = compute_depletion_for_account(
        "acc1", "codex_other", "primary", history, now=BASE_TIME + timedelta(minutes=2)
    )
    assert first is not None
    assert rebuild_calls == 1

    appended_history = _signed([*history, _entry(40.0, BASE_TIME + timedelta(minutes=2))])
    second = compute_depletion_for_account(
        "acc1", "codex_other", "primary", appended_history, now=BASE_TIME + timedelta(minutes=3)
    )
    assert second is not None
    # Signature changed (new row appended) -> rebuild executed.
    assert rebuild_calls == 2
    # Rate must rise to reflect the steeper observation.
    assert second.rate_per_second > first.rate_per_second


def test_aged_out_row_invalidates_memoized_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the leading row of the in-window history drops away the cache must
    rebuild so the rate does not retain influence from samples now outside the
    window."""
    from app.modules.usage import depletion_service

    reset_ewma_state()
    full_history = _signed(
        [
            _entry(10.0, BASE_TIME),
            _entry(70.0, BASE_TIME + timedelta(minutes=1)),
            _entry(80.0, BASE_TIME + timedelta(minutes=2)),
        ]
    )

    rebuild_calls = 0
    real_rebuild = depletion_service._rebuild_ewma_state

    def _counting_rebuild(history_arg):
        nonlocal rebuild_calls
        rebuild_calls += 1
        return real_rebuild(history_arg)

    monkeypatch.setattr(depletion_service, "_rebuild_ewma_state", _counting_rebuild)

    full_result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", full_history, now=BASE_TIME + timedelta(minutes=3)
    )
    assert full_result is not None
    assert rebuild_calls == 1

    in_window_history = _signed(full_history[1:])
    truncated_result = compute_depletion_for_account(
        "acc1", "codex_other", "primary", in_window_history, now=BASE_TIME + timedelta(minutes=3)
    )
    assert truncated_result is not None
    assert rebuild_calls == 2
    assert truncated_result.rate_per_second != pytest.approx(full_result.rate_per_second)


def test_inplace_value_correction_invalidates_memoized_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review on #588: when a row's `used_percent` is corrected in place
    (same timestamps, same row count) the cache must rebuild — otherwise we
    keep returning depletion metrics derived from the stale value."""
    from app.modules.usage import depletion_service

    reset_ewma_state()
    history = _signed(
        [
            _entry(10.0, BASE_TIME),
            _entry(20.0, BASE_TIME + timedelta(minutes=1)),
            _entry(30.0, BASE_TIME + timedelta(minutes=2)),
        ]
    )
    now = BASE_TIME + timedelta(minutes=3)

    rebuild_calls = 0
    real_rebuild = depletion_service._rebuild_ewma_state

    def _counting_rebuild(history_arg):
        nonlocal rebuild_calls
        rebuild_calls += 1
        return real_rebuild(history_arg)

    monkeypatch.setattr(depletion_service, "_rebuild_ewma_state", _counting_rebuild)

    first = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert first is not None
    assert rebuild_calls == 1

    # Same window endpoints and row count, but the middle row's used_percent is
    # corrected upward (e.g. backfill of a previously underreported sample) so
    # the corrected series remains monotonically non-decreasing. Window-
    # endpoint-only signatures would treat this as unchanged and reuse the
    # stale EWMA state.
    corrected_history = _signed(
        [
            history[0],
            _entry(25.0, BASE_TIME + timedelta(minutes=1)),
            history[2],
        ]
    )
    second = compute_depletion_for_account("acc1", "codex_other", "primary", corrected_history, now=now)
    assert second is not None
    assert rebuild_calls == 2
    # Rate must reflect the correction, not the stale cached state.
    assert second.rate_per_second != pytest.approx(first.rate_per_second)


def test_inplace_reset_at_correction_invalidates_memoized_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review on #588: corrections to value-bearing non-`used_percent`
    fields (here `reset_at`) must also invalidate the cache."""
    from app.modules.usage import depletion_service

    reset_ewma_state()
    reset_epoch = int((BASE_TIME + timedelta(minutes=30)).timestamp())
    history = _signed(
        [
            _entry(10.0, BASE_TIME, reset_at=reset_epoch, window_minutes=60),
            _entry(20.0, BASE_TIME + timedelta(minutes=1), reset_at=reset_epoch, window_minutes=60),
            _entry(30.0, BASE_TIME + timedelta(minutes=2), reset_at=reset_epoch, window_minutes=60),
        ]
    )
    now = BASE_TIME + timedelta(minutes=3)

    rebuild_calls = 0
    real_rebuild = depletion_service._rebuild_ewma_state

    def _counting_rebuild(history_arg):
        nonlocal rebuild_calls
        rebuild_calls += 1
        return real_rebuild(history_arg)

    monkeypatch.setattr(depletion_service, "_rebuild_ewma_state", _counting_rebuild)

    first = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert first is not None
    assert rebuild_calls == 1

    # Upstream extended the window — reset_at moved later on every row
    # (per-row consistency keeps ewma_update from treating it as a mid-stream
    # window change and dropping the rate).
    extended_reset = int((BASE_TIME + timedelta(minutes=90)).timestamp())
    corrected_history = _signed(
        [_entry(e.used_percent, e.recorded_at, reset_at=extended_reset, window_minutes=60) for e in history]
    )
    second = compute_depletion_for_account("acc1", "codex_other", "primary", corrected_history, now=now)
    assert second is not None
    assert rebuild_calls == 2
    # Extending reset_at lengthens seconds_until_reset which lowers the
    # sustainable_rate denominator in compute_burn_rate, so burn_rate must
    # rise; the cached pre-correction state would have produced the old,
    # lower burn_rate.
    assert second.burn_rate != pytest.approx(first.burn_rate)
    assert second.burn_rate > first.burn_rate


def test_post_reset_window_returns_none() -> None:
    """R30-F1: When reset_at is in the past, depletion should be None (window expired)."""
    reset_ewma_state()
    reset_epoch = int((BASE_TIME + timedelta(minutes=5)).timestamp())
    history = [
        _entry(10.0, BASE_TIME, reset_at=reset_epoch, window_minutes=300),
        _entry(50.0, BASE_TIME + timedelta(minutes=1), reset_at=reset_epoch, window_minutes=300),
        _entry(80.0, BASE_TIME + timedelta(minutes=2), reset_at=reset_epoch, window_minutes=300),
    ]
    # 'now' is after the reset — the window has already expired
    now = BASE_TIME + timedelta(minutes=10)
    result = compute_depletion_for_account("acc1", "codex_other", "primary", history, now=now)
    assert result is None


def _dense_history(
    row_count: int,
    *,
    reset_at: int,
    start: datetime,
    reset_drop_at: int | None = None,
) -> list[_FakeEntry]:
    """Monotonic 1-minute cadence with a non-uniform slope; optionally one
    usage drop (window reset) ``reset_drop_at`` rows before the end."""
    rows: list[_FakeEntry] = []
    used = 5.0
    for index in range(row_count):
        used += 0.012 + 0.006 * ((index * 7919) % 13) / 13.0
        if reset_drop_at is not None and index == row_count - reset_drop_at:
            used = 1.0
        rows.append(_entry(round(used, 6), start + timedelta(minutes=index), reset_at=reset_at, window_minutes=10080))
    return rows


@pytest.mark.parametrize("reset_drop_at", [None, 20])
def test_depletion_over_ewma_tail_matches_full_history_replay(reset_drop_at: int | None) -> None:
    """The dashboard fetch caps rows older than the equal-weight floor to the
    newest 64: the first tail row seeds the EWMA and the other 63 update it,
    so with alpha 0.4 the pre-tail residual on the rate is at most 0.6**63
    times the largest per-second slope (~1e-14 %/s at this history's slopes),
    and at this per-minute cadence (one update per row) the tail replay must
    reproduce the full replay to floating-point noise.
    A usage drop (window reset) inside the tail discards all earlier state,
    so those cases must match exactly."""
    from app.modules.dashboard.service import _PROJECTION_EWMA_TAIL_ROWS

    start = BASE_TIME - timedelta(minutes=5000)
    now = BASE_TIME + timedelta(seconds=30)
    reset_epoch = int((BASE_TIME + timedelta(days=2)).timestamp())
    full = _dense_history(5000, reset_at=reset_epoch, start=start, reset_drop_at=reset_drop_at)
    tail = full[-_PROJECTION_EWMA_TAIL_ROWS:]

    reset_ewma_state()
    full_result = compute_depletion_for_account("acc1", "standard", "secondary", _signed(full), now=now)
    reset_ewma_state()
    tail_result = compute_depletion_for_account("acc1", "standard", "secondary", _signed(tail), now=now)

    assert full_result is not None
    assert tail_result is not None
    assert tail_result.risk_level == full_result.risk_level
    assert tail_result.safe_usage_percent == full_result.safe_usage_percent
    if reset_drop_at is not None:
        # The reset inside the tail makes both replays start from the same row.
        assert tail_result.rate_per_second == full_result.rate_per_second
        assert tail_result.burn_rate == full_result.burn_rate
        assert tail_result.risk == full_result.risk
        assert tail_result.seconds_until_exhaustion == full_result.seconds_until_exhaustion
        return
    assert tail_result.rate_per_second == pytest.approx(full_result.rate_per_second, abs=1e-12, rel=1e-12)
    assert tail_result.burn_rate == pytest.approx(full_result.burn_rate, abs=1e-12, rel=1e-12)
    assert tail_result.risk == pytest.approx(full_result.risk, abs=1e-12, rel=1e-12)
    assert full_result.seconds_until_exhaustion is not None
    assert tail_result.seconds_until_exhaustion == pytest.approx(full_result.seconds_until_exhaustion, rel=1e-12)


def _burst_history(rows_per_second: int, *, seconds: int, reset_at: int, end: datetime) -> list[_FakeEntry]:
    """48h of sparse rows followed by ``seconds`` recorded seconds each
    holding ``rows_per_second`` fingerprint-changing rows, ending at ``end``."""
    burst_start = end - timedelta(seconds=seconds)
    rows: list[_FakeEntry] = []
    used = 1.0
    for index in range(288):
        used += 0.01
        recorded_at = burst_start - timedelta(minutes=10 * (288 - index))
        rows.append(_entry(round(used, 6), recorded_at, reset_at=reset_at, window_minutes=10080))
    for second in range(seconds):
        for slot in range(rows_per_second):
            used += 0.01 + 0.003 * ((second * 31 + slot * 7) % 5)
            recorded_at = burst_start + timedelta(seconds=second, microseconds=(slot * 1_000_000) // rows_per_second)
            rows.append(_entry(round(used, 6), recorded_at, reset_at=reset_at, window_minutes=10080))
    return rows


@pytest.mark.parametrize("rows_per_second", [1, 3, 8])
def test_depletion_ewma_tail_guarantee_counts_distinct_recorded_seconds(rows_per_second: int) -> None:
    """``ewma_update`` advances once per distinct integer epoch second
    (``naive_utc_to_epoch`` truncates and ``dt == 0`` keeps the state), so
    the tail cap's ``0.6**64`` argument counts distinct recorded seconds, not
    rows. A tail spanning 64 distinct seconds must match the full replay
    within 1e-12 however many rows share each second. A 64-row tail packed
    into fewer distinct seconds is the documented boundary of the guarantee:
    it diverges, which is why the spec states the bound per distinct second.
    The burst is followed by 3h of idleness so nothing newer decays it — the
    shape the projections fetch hands the depletion EWMA."""
    from app.modules.dashboard.service import _PROJECTION_EWMA_TAIL_ROWS

    cap = _PROJECTION_EWMA_TAIL_ROWS
    burst_end = BASE_TIME - timedelta(hours=3)
    now = BASE_TIME
    reset_epoch = int((BASE_TIME + timedelta(days=2)).timestamp())
    full = _burst_history(rows_per_second, seconds=cap, reset_at=reset_epoch, end=burst_end)

    def _depletion(history: list[_FakeEntry]) -> DepletionMetrics | None:
        reset_ewma_state()
        return compute_depletion_for_account("acc1", "standard", "secondary", _signed(history), now=now)

    full_result = _depletion(full)
    assert full_result is not None

    # cap-many distinct seconds, regardless of rows per second: equivalent.
    by_seconds = _depletion(full[-(cap * rows_per_second) :])
    assert by_seconds is not None
    assert by_seconds.rate_per_second == pytest.approx(full_result.rate_per_second, abs=1e-12, rel=1e-12)
    assert by_seconds.burn_rate == pytest.approx(full_result.burn_rate, abs=1e-12, rel=1e-12)
    assert by_seconds.risk == pytest.approx(full_result.risk, abs=1e-12, rel=1e-12)

    # cap-many rows: only equivalent when that is also cap-many distinct seconds.
    by_rows = _depletion(full[-cap:])
    assert by_rows is not None
    if rows_per_second == 1:
        assert by_rows.rate_per_second == pytest.approx(full_result.rate_per_second, abs=1e-12, rel=1e-12)
        return
    assert full_result.rate_per_second is not None and by_rows.rate_per_second is not None
    assert abs(by_rows.rate_per_second - full_result.rate_per_second) > 1e-12 * abs(full_result.rate_per_second)


def test_depletion_ewma_tail_residual_is_bounded_by_seed_row_arithmetic() -> None:
    """The first tail row only seeds the EWMA, so a 64-row tail performs 63
    updates and the pre-tail residual on the rate is bounded by ``0.6**63``
    times the largest per-second sample slope — not ``0.6**64``. Pin the
    boundary with the worst-case history: one 0 -> 99 step in a single
    recorded second, then 64 flat rows one second apart older than the floor.
    The full replay keeps a ghost rate of ``99 * 0.6**63`` (~1.05e-12 %/s,
    above a flat 1e-12 bound) while the tail decays to exactly 0.0; burn rate
    inherits the residual scaled by seconds-until-reset over remaining
    percent. If ``ewma_update`` ever blends the first sample instead of
    seeding, this assertion of divergence fails on purpose so the spec's
    stated bound gets revisited."""
    from app.modules.dashboard.service import _PROJECTION_EWMA_TAIL_ROWS

    cap = _PROJECTION_EWMA_TAIL_ROWS
    burst_end = BASE_TIME - timedelta(hours=3)
    now = BASE_TIME
    reset_epoch = int((BASE_TIME + timedelta(days=2)).timestamp())
    step_slope = 99.0  # percent per second: the only positive sample slope in the history
    full = [_entry(0.0, burst_end - timedelta(seconds=cap), reset_at=reset_epoch, window_minutes=10080)]
    full += [
        _entry(99.0, burst_end - timedelta(seconds=cap - 1 - index), reset_at=reset_epoch, window_minutes=10080)
        for index in range(cap)
    ]

    def _depletion(history: list[_FakeEntry]) -> DepletionMetrics | None:
        reset_ewma_state()
        return compute_depletion_for_account("acc1", "standard", "secondary", _signed(history), now=now)

    full_result = _depletion(full)
    tail_result = _depletion(full[-cap:])
    assert full_result is not None and tail_result is not None
    assert full_result.rate_per_second is not None and tail_result.rate_per_second is not None

    residual_bound = 0.6 ** (cap - 1) * step_slope
    rate_diff = abs(full_result.rate_per_second - tail_result.rate_per_second)
    assert tail_result.rate_per_second == 0.0
    assert rate_diff > 1e-12, "seed-row residual must exceed a flat 1e-12 bound for this history"
    assert rate_diff <= residual_bound
    assert rate_diff > 0.6**cap * step_slope, "the residual is governed by cap-1 updates, not cap"

    seconds_until_reset = reset_epoch - int(now.replace(tzinfo=timezone.utc).timestamp())
    remaining_percent = 100.0 - 99.0
    burn_diff = abs(full_result.burn_rate - tail_result.burn_rate)
    assert tail_result.burn_rate == 0.0
    assert burn_diff <= residual_bound * seconds_until_reset / remaining_percent
    assert abs(full_result.risk - tail_result.risk) <= residual_bound * seconds_until_reset / 100.0
    assert tail_result.risk_level == full_result.risk_level
    # A ghost rate this small never projects exhaustion inside the window either.
    assert full_result.seconds_until_exhaustion is None
    assert tail_result.seconds_until_exhaustion is None


def test_depletion_saturated_account_tail_replay_reports_no_exhaustion_eta() -> None:
    """An account flat at 100% for longer than the floor: the full replay
    keeps a positive ghost rate (``0.6 * r`` never reaches 0.0 — it is sticky
    at the smallest denormal), so ``remaining / rate`` yields an immediate
    exhaustion ETA (0.0 s, now), while the 64-row tail sees only flat rows,
    replays a rate of exactly 0.0, and reports no ETA. Risk and burn rate
    are identical (1.0 and 0.0) either way. Pins the documented divergence
    so the spec's MAY stays honest."""
    from app.modules.dashboard.service import _PROJECTION_EWMA_TAIL_ROWS

    cap = _PROJECTION_EWMA_TAIL_ROWS
    reset_epoch = int((BASE_TIME + timedelta(days=2)).timestamp())
    start = BASE_TIME - timedelta(hours=60)
    rows: list[_FakeEntry] = []
    used = 0.0
    index = 0
    while used < 100.0:
        rows.append(_entry(used, start + timedelta(minutes=index), reset_at=reset_epoch, window_minutes=10080))
        used += 0.5
        index += 1
    for _ in range(48 * 60):  # 48h saturated at the 60s poller cadence
        rows.append(_entry(100.0, start + timedelta(minutes=index), reset_at=reset_epoch, window_minutes=10080))
        index += 1
    now = rows[-1].recorded_at + timedelta(seconds=30)
    floor = now - timedelta(hours=3)

    def _fetch(history: list[_FakeEntry], row_cap: int, uncapped_floor: datetime) -> list[_FakeEntry]:
        older = [row for row in history if row.recorded_at < uncapped_floor]
        recent = [row for row in history if row.recorded_at >= uncapped_floor]
        return older[-row_cap:] + recent

    def _depletion(history: list[_FakeEntry]) -> DepletionMetrics | None:
        reset_ewma_state()
        return compute_depletion_for_account("acc1", "standard", "secondary", _signed(history), now=now)

    full_result = _depletion(rows)
    tail_result = _depletion(_fetch(rows, cap, floor))
    assert full_result is not None and tail_result is not None
    assert full_result.rate_per_second is not None and full_result.rate_per_second > 0.0
    assert full_result.rate_per_second <= 0.6 ** (cap - 1) * 0.5 / 60.0
    assert tail_result.rate_per_second == 0.0

    assert tail_result.risk == full_result.risk == 1.0
    assert tail_result.burn_rate == full_result.burn_rate == 0.0
    assert tail_result.risk_level == full_result.risk_level
    assert full_result.seconds_until_exhaustion == 0.0
    assert full_result.projected_exhaustion_at == now
    assert tail_result.seconds_until_exhaustion is None
    assert tail_result.projected_exhaustion_at is None


def test_history_signature_content_hash_tracks_row_content() -> None:
    """The compact signature must be stable for identical content and change
    for any value-bearing field, including ``None`` variants (id/reset_at)."""
    from app.modules.usage.depletion_service import _history_signature_from_rows

    reset_epoch = int((BASE_TIME + timedelta(minutes=30)).timestamp())
    rows = [
        _entry(10.0, BASE_TIME, reset_at=reset_epoch, window_minutes=60),
        _entry(20.0, BASE_TIME + timedelta(minutes=1), reset_at=reset_epoch, window_minutes=60),
        _entry(30.0, BASE_TIME + timedelta(minutes=2), reset_at=reset_epoch, window_minutes=60),
    ]
    baseline = _history_signature_from_rows(rows)
    assert _history_signature_from_rows(list(rows)) == baseline
    assert isinstance(baseline.content_digest, int)

    middle = rows[1]
    variants = [
        [rows[0], _entry(25.0, middle.recorded_at, reset_at=reset_epoch, window_minutes=60), rows[2]],
        [rows[0], _entry(20.0, middle.recorded_at, reset_at=None, window_minutes=60), rows[2]],
        [rows[0], _entry(20.0, middle.recorded_at, reset_at=reset_epoch, window_minutes=None), rows[2]],
        [
            rows[0],
            _entry(20.0, middle.recorded_at + timedelta(seconds=1), reset_at=reset_epoch, window_minutes=60),
            rows[2],
        ],
    ]
    for variant in variants:
        changed = _history_signature_from_rows(variant)
        assert changed.row_count == baseline.row_count
        assert changed.first == baseline.first
        assert changed.latest == baseline.latest
        assert changed.content_digest != baseline.content_digest
