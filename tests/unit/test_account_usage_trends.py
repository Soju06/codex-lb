from __future__ import annotations

from app.core.usage.types import UsageTrendBucket
from app.modules.accounts.mappers import build_account_usage_trends


def _bucket(epoch: int, account_id: str, window: str, avg_used: float, samples: int = 1) -> UsageTrendBucket:
    return UsageTrendBucket(
        bucket_epoch=epoch,
        account_id=account_id,
        window=window,
        avg_used_percent=avg_used,
        samples=samples,
    )


# Use a value already aligned to BUCKET_SECONDS so tests are predictable
BUCKET_SECONDS = 21600  # 6h
SINCE_EPOCH = (1_700_000_000 // BUCKET_SECONDS) * BUCKET_SECONDS
BUCKET_COUNT = 4  # 4 buckets → spans 24h


class TestBuildAccountUsageTrends:
    def test_empty_buckets_returns_empty(self):
        result = build_account_usage_trends([], SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)
        assert result == {}

    def test_single_account_single_window(self):
        buckets = [
            _bucket(SINCE_EPOCH, "a1", "primary", 20.0),
            _bucket(SINCE_EPOCH + BUCKET_SECONDS, "a1", "primary", 40.0),
        ]
        result = build_account_usage_trends(buckets, SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)

        assert "a1" in result
        trend = result["a1"]
        assert len(trend.primary) == BUCKET_COUNT

        # First bucket: 100 - 20 = 80
        assert trend.primary[0].v == 80.0
        # Second bucket: 100 - 40 = 60
        assert trend.primary[1].v == 60.0
        # Third and fourth buckets: forward-filled with last value (60)
        assert trend.primary[2].v == 60.0
        assert trend.primary[3].v == 60.0

    def test_values_are_remaining_percent(self):
        buckets = [_bucket(SINCE_EPOCH, "a1", "primary", 75.0)]
        result = build_account_usage_trends(buckets, SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)
        assert result["a1"].primary[0].v == 25.0

    def test_used_percent_clamped_to_0_100(self):
        buckets = [_bucket(SINCE_EPOCH, "a1", "primary", 110.0)]
        result = build_account_usage_trends(buckets, SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)
        # 100 - 110 = -10, clamped to 0
        assert result["a1"].primary[0].v == 0.0

    def test_missing_buckets_filled_with_default(self):
        # No data at all for bucket 0, data at bucket 1
        buckets = [_bucket(SINCE_EPOCH + BUCKET_SECONDS, "a1", "primary", 50.0)]
        result = build_account_usage_trends(buckets, SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)

        # Bucket 0: no data → default 100.0
        assert result["a1"].primary[0].v == 100.0
        # Bucket 1: 100 - 50 = 50
        assert result["a1"].primary[1].v == 50.0

    def test_dual_window(self):
        buckets = [
            _bucket(SINCE_EPOCH, "a1", "primary", 20.0),
            _bucket(SINCE_EPOCH, "a1", "secondary", 30.0),
        ]
        result = build_account_usage_trends(buckets, SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)
        trend = result["a1"]
        assert trend.primary[0].v == 80.0
        assert trend.secondary[0].v == 70.0

    def test_multiple_accounts(self):
        buckets = [
            _bucket(SINCE_EPOCH, "a1", "primary", 10.0),
            _bucket(SINCE_EPOCH, "a2", "primary", 90.0),
        ]
        result = build_account_usage_trends(buckets, SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)
        assert result["a1"].primary[0].v == 90.0
        assert result["a2"].primary[0].v == 10.0

    def test_missing_window_returns_empty_list(self):
        buckets = [_bucket(SINCE_EPOCH, "a1", "primary", 20.0)]
        result = build_account_usage_trends(buckets, SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)
        # secondary was not in any bucket → empty list
        assert result["a1"].secondary == []

    def test_timestamps_are_utc(self):
        buckets = [_bucket(SINCE_EPOCH, "a1", "primary", 0.0)]
        result = build_account_usage_trends(buckets, SINCE_EPOCH, BUCKET_SECONDS, BUCKET_COUNT)
        for point in result["a1"].primary:
            assert point.t.tzinfo is not None
