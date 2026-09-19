from __future__ import annotations

from datetime import datetime, timezone

from app.modules.accounts.schemas import (
    AccountFableScopedWeekly,
    AccountSubscriptionLedger,
    AccountSummary,
    AccountUsage,
)
from app.modules.pools.service import build_pools

_RESET = datetime(2026, 9, 23, 11, 0, 0, tzinfo=timezone.utc)
_LATER_RESET = datetime(2026, 9, 24, 11, 0, 0, tzinfo=timezone.utc)


def _summary(
    account_id: str,
    *,
    provider: str = "anthropic",
    status: str = "active",
    primary_remaining: float | None = None,
    secondary_remaining: float | None = None,
    reset_at_primary: datetime | None = None,
    reset_at_secondary: datetime | None = _RESET,
    fable_eligible: bool | None = None,
    scoped_used_percent: float | None = None,
    scoped_fresh: bool = True,
    scoped_reset_at: datetime | None = None,
    subscription_status: str | None = None,
) -> AccountSummary:
    return AccountSummary(
        account_id=account_id,
        provider=provider,
        email=f"{account_id}@example.com",
        display_name=account_id,
        plan_type="max",
        status=status,
        fable_eligible=fable_eligible,
        fable_scoped_weekly=(
            None
            if scoped_used_percent is None
            else AccountFableScopedWeekly(
                used_percent=scoped_used_percent,
                reset_at=scoped_reset_at,
                recorded_at=datetime(2026, 9, 19, 21, 0, 0, tzinfo=timezone.utc),
                fresh=scoped_fresh,
            )
        ),
        usage=AccountUsage(
            primary_remaining_percent=primary_remaining,
            secondary_remaining_percent=secondary_remaining,
        ),
        reset_at_primary=reset_at_primary,
        reset_at_secondary=reset_at_secondary,
        subscription=(None if subscription_status is None else AccountSubscriptionLedger(status=subscription_status)),
    )


def _pool(response, pool_id: str):
    return next(pool for pool in response.pools if pool.id == pool_id)


def test_every_contract_pool_is_present_in_order() -> None:
    response = build_pools([])

    assert [pool.id for pool in response.pools] == [
        "anthropic-fable",
        "anthropic-general",
        "openai-codex",
        "kimi",
        "glm",
    ]


def test_fresh_markers_drive_the_fable_pool_and_mark_the_source() -> None:
    summaries = [
        _summary("a", scoped_used_percent=87.0, fable_eligible=True, secondary_remaining=60.0),
        _summary("b", scoped_used_percent=100.0, fable_eligible=False, secondary_remaining=70.0),
    ]

    pool = _pool(build_pools(summaries), "anthropic-fable")

    assert pool.source == "scoped_marker"
    assert pool.kind == "fable_scoped"
    assert pool.accounts == 2
    assert pool.eligible_accounts == 1
    assert pool.headroom_percent == 13.0
    assert pool.aggregate_remaining_percent == 6.5
    assert pool.status == "low"


def test_stale_marker_falls_back_to_the_weekly_heuristic() -> None:
    summaries = [
        _summary(
            "a",
            scoped_used_percent=100.0,
            scoped_fresh=False,
            fable_eligible=True,
            secondary_remaining=70.0,
        )
    ]

    pool = _pool(build_pools(summaries), "anthropic-fable")

    assert pool.source == "weekly_heuristic"
    assert pool.headroom_percent == 70.0
    assert pool.status == "ok"


def test_one_fresh_marker_makes_the_pool_authoritative_and_mixes_per_account() -> None:
    summaries = [
        _summary("fresh", scoped_used_percent=20.0, fable_eligible=True, secondary_remaining=10.0),
        _summary("stale", scoped_used_percent=0.0, scoped_fresh=False, fable_eligible=True, secondary_remaining=40.0),
    ]

    pool = _pool(build_pools(summaries), "anthropic-fable")

    assert pool.source == "scoped_marker"
    # The fresh account is read from its marker (80 left), the stale one from
    # its weekly window (40 left) — never from its ignored marker.
    assert pool.headroom_percent == 80.0
    assert pool.aggregate_remaining_percent == 60.0


def test_a_held_account_marker_still_names_the_source() -> None:
    """C1 reads `source` off any fresh marker, usable or not: a paused account
    still proves the vendor's scoped signal is arriving, and the headroom must
    keep coming from the usable accounts only."""
    summaries = [
        _summary("paused", status="paused", scoped_used_percent=20.0, secondary_remaining=100.0),
        _summary("live", secondary_remaining=40.0, fable_eligible=True),
    ]

    pool = _pool(build_pools(summaries), "anthropic-fable")

    assert pool.source == "scoped_marker"
    assert pool.eligible_accounts == 1
    assert pool.headroom_percent == 40.0
    assert pool.aggregate_remaining_percent == 40.0


def test_a_canceled_account_marker_also_names_the_source() -> None:
    summaries = [
        _summary("canceled", subscription_status="canceled", scoped_used_percent=20.0),
        _summary("live", secondary_remaining=40.0, fable_eligible=True),
    ]

    assert _pool(build_pools(summaries), "anthropic-fable").source == "scoped_marker"


def test_only_stale_markers_on_held_accounts_stay_heuristic() -> None:
    summaries = [
        _summary("paused", status="paused", scoped_used_percent=20.0, scoped_fresh=False),
        _summary("live", secondary_remaining=40.0, fable_eligible=True),
    ]

    assert _pool(build_pools(summaries), "anthropic-fable").source == "weekly_heuristic"


def test_no_weekly_sample_counts_as_a_fresh_window() -> None:
    pool = _pool(build_pools([_summary("a", fable_eligible=True)]), "anthropic-fable")

    assert pool.headroom_percent == 100.0
    assert pool.status == "ok"


def test_reset_at_comes_from_the_account_supplying_the_headroom() -> None:
    summaries = [
        _summary("low", scoped_used_percent=90.0, scoped_reset_at=_RESET),
        _summary("high", scoped_used_percent=10.0, scoped_reset_at=_LATER_RESET),
    ]

    assert _pool(build_pools(summaries), "anthropic-fable").reset_at == _LATER_RESET


def test_marker_without_its_own_reset_falls_back_to_the_weekly_reset() -> None:
    summaries = [_summary("a", scoped_used_percent=10.0, scoped_reset_at=None, reset_at_secondary=_RESET)]

    assert _pool(build_pools(summaries), "anthropic-fable").reset_at == _RESET


def test_canceled_and_held_accounts_leave_the_pool_arithmetic() -> None:
    summaries = [
        _summary("canceled", secondary_remaining=100.0, fable_eligible=True, subscription_status="canceled"),
        _summary("reauth", status="reauth_required", secondary_remaining=100.0, fable_eligible=True),
        _summary("paused", status="paused", secondary_remaining=100.0),
        _summary("deactivated", status="deactivated", secondary_remaining=100.0),
        _summary("live", secondary_remaining=30.0, fable_eligible=True),
    ]

    general = _pool(build_pools(summaries), "anthropic-general")
    fable = _pool(build_pools(summaries), "anthropic-fable")

    assert general.accounts == 5
    assert general.eligible_accounts == 1
    assert general.headroom_percent == 30.0
    assert fable.eligible_accounts == 1


def test_rate_limited_account_stays_in_and_reads_exhausted() -> None:
    summaries = [_summary("hot", status="rate_limited", secondary_remaining=0.0)]

    pool = _pool(build_pools(summaries), "anthropic-general")

    assert pool.eligible_accounts == 1
    assert pool.headroom_percent == 0.0
    assert pool.status == "exhausted"


def test_pool_with_no_usable_account_is_exhausted_with_null_numbers() -> None:
    summaries = [_summary("gone", status="deactivated", secondary_remaining=100.0)]

    pool = _pool(build_pools(summaries), "anthropic-general")

    assert pool.accounts == 1
    assert pool.eligible_accounts == 0
    assert pool.headroom_percent is None
    assert pool.aggregate_remaining_percent is None
    assert pool.reset_at is None
    assert pool.status == "exhausted"


def test_empty_pool_is_exhausted() -> None:
    pool = _pool(build_pools([]), "kimi")

    assert pool.accounts == 0
    assert pool.eligible_accounts == 0
    assert pool.headroom_percent is None
    assert pool.status == "exhausted"


def test_status_thresholds_are_inclusive_at_the_boundaries() -> None:
    def status_at(remaining: float) -> str:
        return _pool(build_pools([_summary("a", secondary_remaining=remaining)]), "anthropic-general").status

    assert status_at(25.0) == "ok"
    assert status_at(24.9) == "low"
    assert status_at(5.0) == "low"
    assert status_at(4.9) == "exhausted"


def test_openai_pool_reads_the_weekly_window() -> None:
    summaries = [
        _summary("codex-1", provider="openai", secondary_remaining=80.0),
        _summary("codex-2", provider="OpenAI", secondary_remaining=20.0),
    ]

    pool = _pool(build_pools(summaries), "openai-codex")

    assert pool.provider == "openai"
    assert pool.accounts == 2
    assert pool.headroom_percent == 80.0
    assert pool.aggregate_remaining_percent == 50.0
    assert pool.source is None


def test_glm_and_kimi_read_the_primary_window_and_count_unmetered_as_full() -> None:
    summaries = [
        _summary("glm-1", provider="glm", primary_remaining=40.0, reset_at_primary=_RESET),
        _summary("kimi-1", provider="kimi", primary_remaining=None),
    ]

    glm = _pool(build_pools(summaries), "glm")
    kimi = _pool(build_pools(summaries), "kimi")

    assert glm.headroom_percent == 40.0
    assert glm.reset_at == _RESET
    assert glm.status == "ok"
    assert kimi.headroom_percent == 100.0
    assert kimi.status == "ok"


def test_fable_and_general_pools_do_not_borrow_other_providers() -> None:
    summaries = [
        _summary("codex", provider="openai", secondary_remaining=100.0),
        _summary("claude", secondary_remaining=10.0, fable_eligible=True),
    ]

    response = build_pools(summaries)

    assert _pool(response, "anthropic-general").accounts == 1
    assert _pool(response, "anthropic-fable").headroom_percent == 10.0


def test_generated_at_is_honored_and_the_wire_shape_is_camel_case() -> None:
    generated_at = datetime(2026, 9, 19, 21, 0, 0, tzinfo=timezone.utc)

    payload = build_pools(
        [_summary("a", scoped_used_percent=87.0, fable_eligible=True)],
        generated_at=generated_at,
    ).model_dump(mode="json", by_alias=True)

    assert payload["generatedAt"] == "2026-09-19T21:00:00Z"
    assert set(payload["pools"][0]) == {
        "id",
        "provider",
        "kind",
        "accounts",
        "eligibleAccounts",
        "headroomPercent",
        "aggregateRemainingPercent",
        "resetAt",
        "status",
        "source",
    }
