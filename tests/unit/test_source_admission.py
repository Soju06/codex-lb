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
        self.first_items = 0

    def observe_first_output_item(self) -> None:
        self.first_items += 1

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
        def observe_first_output_item(self) -> None:
            raise RuntimeError("breaker unavailable")

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


class _LabelRecorder:
    """Records ``labels(**kw).inc()`` so a scenario can assert the exact label set."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.incs = 0

    def labels(self, **labels: str) -> "_LabelRecorder":
        self.calls.append(labels)
        return self

    def inc(self, amount: float = 1) -> None:
        self.incs += 1


def test_saturated_source_increments_bulkhead_rejections_with_source_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """proxy-runtime-observability 'Saturated source answers busy': ``…bulkhead_rejections_total{source_id}``."""

    counter = _LabelRecorder()
    monkeypatch.setattr(admission_module, "model_source_bulkhead_rejections_total", counter)
    bulkhead = SourceBulkhead()
    assert bulkhead.try_acquire("src_busy", 1) is not None
    # The second claim is over the limit: rejected and counted, once, for that source id.
    assert bulkhead.try_acquire("src_busy", 1) is None

    assert counter.calls == [{"source_id": "src_busy"}]
    assert counter.incs == 1


# --- overflow breaker token riding in the claims (#2123 WP-C2, C1 gap 1/2) ---------------------


def test_try_claim_carries_the_trial_and_settles_it_with_the_slot() -> None:
    trial = _Trial()
    bulkhead = SourceBulkhead()
    source = _source(max_concurrency=1)
    claims = try_claim(source, bulkhead=bulkhead, trial=trial)
    assert claims is not None
    assert claims.trial is trial
    claims.release("success")
    assert trial.results == ["success"]
    assert bulkhead.in_flight(source.id) == 0


def test_try_claim_saturated_returns_none_without_settling_the_trial() -> None:
    """The caller still holds the token and releases it (``try_claim_overflow``)."""

    trial = _Trial()
    bulkhead = SourceBulkhead()
    source = _source(max_concurrency=1)
    first = try_claim(source, bulkhead=bulkhead)
    assert first is not None
    assert try_claim(source, bulkhead=bulkhead, trial=trial) is None
    assert trial.results == []
    first.release_if_unowned()


def test_try_claim_without_a_trial_keeps_direct_routing_tokenless() -> None:
    claims = try_claim(_source(max_concurrency=None), bulkhead=SourceBulkhead())
    assert claims is not None
    assert claims.trial is None
    claims.observe_first_output_item()
    claims.release("failure")


def test_first_output_item_reaches_the_trial_and_keeps_the_slot() -> None:
    """Design §8.3: the trial closes the breaker at the first output item; the slot stays with the owner's latch."""

    trial = _Trial()
    bulkhead = SourceBulkhead()
    source = _source(max_concurrency=1)
    claims = try_claim(source, bulkhead=bulkhead, trial=trial)
    assert claims is not None
    claims.observe_first_output_item()
    assert trial.first_items == 1 and trial.results == []
    assert bulkhead.in_flight(source.id) == 1
    claims.release("failure")
    assert trial.results == ["failure"]
    assert bulkhead.in_flight(source.id) == 0
    # After the latch released the claims nothing reaches the trial any more.
    claims.observe_first_output_item()
    assert trial.first_items == 1
