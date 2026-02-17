from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.usage.types import BucketModelAggregate
from app.modules.usage.builders import build_trends_from_buckets

BUCKET_SECONDS = 21600  # 6 hours
SINCE = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
SINCE_EPOCH = int(SINCE.timestamp())
# first_bucket = floor(since / bucket) * bucket + bucket
FIRST_SLOT_EPOCH = (SINCE_EPOCH // BUCKET_SECONDS) * BUCKET_SECONDS + BUCKET_SECONDS


def _make_row(
    slot_index: int = 0,
    model: str = "gpt-5.1",
    request_count: int = 10,
    error_count: int = 1,
    input_tokens: int = 500,
    output_tokens: int = 200,
    cached_input_tokens: int = 50,
    reasoning_tokens: int = 0,
) -> BucketModelAggregate:
    return BucketModelAggregate(
        bucket_epoch=FIRST_SLOT_EPOCH + slot_index * BUCKET_SECONDS,
        model=model,
        request_count=request_count,
        error_count=error_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
    )


class TestBuildTrendsFromBuckets:
    def test_empty_rows_produce_zero_filled_trends(self):
        trends, metrics, cost = build_trends_from_buckets([], SINCE)

        assert len(trends.requests) == 28
        assert len(trends.tokens) == 28
        assert len(trends.cost) == 28
        assert len(trends.error_rate) == 28

        assert all(p.v == 0 for p in trends.requests)
        assert all(p.v == 0 for p in trends.tokens)
        assert all(p.v == 0 for p in trends.cost)
        assert all(p.v == 0 for p in trends.error_rate)

        assert metrics.requests_7d == 0
        assert metrics.tokens_secondary_window == 0
        assert metrics.error_rate_7d is None
        assert cost.total_usd_7d == 0.0

    def test_single_bucket_populates_correct_slot(self):
        rows = [_make_row(slot_index=2)]
        trends, metrics, cost = build_trends_from_buckets(rows, SINCE)

        assert trends.requests[2].v == 10
        assert trends.tokens[2].v == 700  # 500 + 200
        assert trends.error_rate[2].v == pytest.approx(0.1)

        # Other slots should be zero
        assert trends.requests[0].v == 0
        assert trends.requests[1].v == 0
        assert trends.requests[3].v == 0

    def test_multiple_models_in_same_bucket_are_summed(self):
        rows = [
            _make_row(slot_index=0, model="gpt-5.1", request_count=5, error_count=0),
            _make_row(slot_index=0, model="gpt-5.2", request_count=3, error_count=1),
        ]
        trends, metrics, _ = build_trends_from_buckets(rows, SINCE)

        assert trends.requests[0].v == 8
        assert trends.error_rate[0].v == pytest.approx(1 / 8, abs=0.001)
        assert metrics.requests_7d == 8

    def test_metrics_totals_are_correct(self):
        rows = [
            _make_row(
                slot_index=0,
                request_count=10,
                error_count=2,
                input_tokens=1000,
                output_tokens=500,
                cached_input_tokens=100,
            ),
            _make_row(
                slot_index=5,
                request_count=20,
                error_count=3,
                input_tokens=2000,
                output_tokens=1000,
                cached_input_tokens=200,
            ),
        ]
        _, metrics, _ = build_trends_from_buckets(rows, SINCE)

        assert metrics.requests_7d == 30
        assert metrics.tokens_secondary_window == 4500  # 1000+500+2000+1000
        assert metrics.cached_tokens_secondary_window == 300
        assert metrics.error_rate_7d == pytest.approx(5 / 30)

    def test_cost_is_computed_from_pricing(self):
        rows = [
            _make_row(
                slot_index=0,
                model="gpt-5.1",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cached_input_tokens=0,
            ),
        ]
        _, _, cost = build_trends_from_buckets(rows, SINCE)

        # gpt-5.1: $1.25/1M input + $10.0/1M output = $11.25
        assert cost.total_usd_7d == pytest.approx(11.25)

    def test_out_of_range_buckets_are_ignored(self):
        rows = [
            BucketModelAggregate(
                bucket_epoch=FIRST_SLOT_EPOCH + 100 * BUCKET_SECONDS,
                model="gpt-5.1",
                request_count=999,
                error_count=0,
                input_tokens=0,
                output_tokens=0,
                cached_input_tokens=0,
                reasoning_tokens=0,
            ),
        ]
        trends, metrics, _ = build_trends_from_buckets(rows, SINCE)

        assert all(p.v == 0 for p in trends.requests)
        assert metrics.requests_7d == 0

    def test_timestamps_are_utc(self):
        trends, _, _ = build_trends_from_buckets([], SINCE)

        for point in trends.requests:
            assert point.t.tzinfo is not None
            assert point.t.tzinfo == timezone.utc

    def test_last_slot_covers_recent_data(self):
        """Data near the end of the window (slot index 27) should be included."""
        rows = [_make_row(slot_index=27, request_count=5)]
        trends, metrics, _ = build_trends_from_buckets(rows, SINCE)

        assert trends.requests[27].v == 5
        assert metrics.requests_7d == 5
