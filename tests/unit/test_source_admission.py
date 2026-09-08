"""Per-source bulkhead and admission claims (#2123 WP-C1, design v3 §6, §8.4)."""

from __future__ import annotations

import pytest

from app.db.models import ModelSource
from app.modules.proxy import source_admission as admission_module
from app.modules.proxy.source_admission import (
    SourceAdmission,
    SourceBulkhead,
    get_source_bulkhead,
    try_claim,
)

pytestmark = pytest.mark.unit


def _source(source_id: str = "src_bulkhead", max_concurrency: int | None = 1) -> ModelSource:
    return ModelSource(
        id=source_id,
        name=source_id,
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
        max_concurrency=max_concurrency,
    )


class _Trial:
    def __init__(self) -> None:
        self.results: list[str] = []

    def settle(self, result: str) -> None:
        self.results.append(result)


def test_bulkhead_enforces_max_concurrency_per_source() -> None:
    bulkhead = SourceBulkhead()
    first = bulkhead.try_acquire("src_a", 1)
    assert first is not None
    assert bulkhead.try_acquire("src_a", 1) is None
    # Other sources are independent.
    other = bulkhead.try_acquire("src_b", 1)
    assert other is not None
    assert bulkhead.in_flight("src_a") == 1
    bulkhead.release(first)
    assert bulkhead.in_flight("src_a") == 0
    assert bulkhead.try_acquire("src_a", 1) is not None


def test_bulkhead_null_max_concurrency_is_unlimited() -> None:
    bulkhead = SourceBulkhead()
    slots = [bulkhead.try_acquire("src_unlimited", None) for _ in range(250)]
    assert all(slot is not None for slot in slots)
    assert bulkhead.in_flight("src_unlimited") == 250


def test_bulkhead_release_never_goes_negative() -> None:
    bulkhead = SourceBulkhead()
    slot = bulkhead.try_acquire("src_a", 2)
    assert slot is not None
    bulkhead.release(slot)
    bulkhead.release(slot)
    assert bulkhead.in_flight("src_a") == 0
    assert bulkhead.try_acquire("src_a", 2) is not None
    assert bulkhead.try_acquire("src_a", 2) is not None
    assert bulkhead.try_acquire("src_a", 2) is None


def test_try_claim_returns_none_when_saturated_and_claims_release_exactly_once() -> None:
    bulkhead = SourceBulkhead()
    source = _source(max_concurrency=1)
    claims = try_claim(source, bulkhead=bulkhead)
    assert claims is not None
    assert try_claim(source, bulkhead=bulkhead) is None
    assert bulkhead.in_flight(source.id) == 1

    owner = object()
    claims.transfer_to(owner)
    # The route-helper latch is a no-op once an owner holds the claims.
    claims.release_if_unowned()
    assert bulkhead.in_flight(source.id) == 1
    claims.release("success")
    assert bulkhead.in_flight(source.id) == 0
    assert claims.released is True
    # A second release (owner latch re-entered) changes nothing.
    claims.release("failure")
    assert bulkhead.in_flight(source.id) == 0
    assert try_claim(source, bulkhead=bulkhead) is not None


def test_release_if_unowned_releases_when_the_owner_was_never_built() -> None:
    bulkhead = SourceBulkhead()
    source = _source(max_concurrency=1)
    claims = try_claim(source, bulkhead=bulkhead)
    assert claims is not None
    claims.release_if_unowned()
    assert bulkhead.in_flight(source.id) == 0
    assert claims.released is True


def test_transfer_to_rejects_a_second_owner() -> None:
    claims = SourceAdmission(slot=None)
    first = object()
    claims.transfer_to(first)
    claims.transfer_to(first)
    with pytest.raises(RuntimeError):
        claims.transfer_to(object())


def test_trial_result_reaches_the_trial_exactly_once() -> None:
    trial = _Trial()
    bulkhead = SourceBulkhead()
    slot = bulkhead.try_acquire("src_trial", None)
    claims = SourceAdmission(slot=slot, trial=trial, bulkhead=bulkhead)
    claims.release("failure")
    claims.release("success")
    assert trial.results == ["failure"]
    assert bulkhead.in_flight("src_trial") == 0


def test_trial_settle_failure_still_releases_the_slot() -> None:
    class _Broken:
        def settle(self, result: str) -> None:
            raise RuntimeError("breaker unavailable")

    bulkhead = SourceBulkhead()
    slot = bulkhead.try_acquire("src_trial", 1)
    claims = SourceAdmission(slot=slot, trial=_Broken(), bulkhead=bulkhead)
    with pytest.raises(RuntimeError):
        claims.release("inconclusive")
    assert bulkhead.in_flight("src_trial") == 0
    assert claims.released is True


def test_get_source_bulkhead_is_a_process_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admission_module, "_BULKHEAD", None)
    first = get_source_bulkhead()
    assert get_source_bulkhead() is first
    source = _source(source_id="src_singleton", max_concurrency=1)
    claims = try_claim(source)
    assert claims is not None
    assert first.in_flight(source.id) == 1
    claims.release_if_unowned()
    assert first.in_flight(source.id) == 0
