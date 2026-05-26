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
    assert isinstance(signature.content_digest, str)
    assert len(signature.content_digest) == 32


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
