"""Probe orchestration: result table shape, verdicts, and the refusal paths.

The upstream sender is stubbed throughout; what is under test is the decision
layer — which accounts get called in which order, how ``cached_tokens`` becomes
a hit or a miss, which verdict that supports, and when the probe declines to
spend quota at all.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core import conversation_archive
from app.core.conversation_archive import archive_enabled
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.cache_isolation_probe import service as probe_service
from app.modules.cache_isolation_probe.sender import ProbeSendResult
from app.modules.cache_isolation_probe.service import (
    CACHE_HIT_RATIO,
    MAX_OTHER_ACCOUNTS,
    MAX_SEED_REPETITIONS,
    OTHER_ROLE,
    SEED_ROLE,
    VERDICT_CROSS_ACCOUNT_SHARING,
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_CROSS_ACCOUNT_HIT,
    CacheIsolationProbeService,
    CacheProbeRefused,
    assess_pool_pressure,
)

pytestmark = pytest.mark.unit

_INPUT_TOKENS = 28_168
_HIT_TOKENS = 28_032
_MISS_TOKENS = 0


def _account(
    account_id: str,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    alias: str | None = None,
    workspace: str | None = None,
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=workspace if workspace is not None else f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        alias=alias,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
        refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
        id_token_encrypted=encryptor.encrypt(f"id-{account_id}"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
    )


def test_seats_of_one_workspace_are_never_treated_as_separate_accounts() -> None:
    """Upstream identifies a caller by workspace, so two seats are one account.

    Including both would let a same-account cache hit be reported as
    ``cross_account_sharing`` — the probe's headline claim — and would make an
    after-isolation re-run read as a failure.
    """

    service = CacheIsolationProbeService.__new__(CacheIsolationProbeService)
    accounts = [
        _account("a1", workspace="ws-shared"),
        _account("a2", workspace="ws-shared"),
        _account("a3", workspace="ws-other"),
    ]

    seed, others = service._choose_accounts(accounts, 5)

    assert seed is not None and seed.id == "a1"
    assert [account.id for account in others] == ["a3"]


def test_accounts_without_a_workspace_id_are_each_their_own_group() -> None:
    service = CacheIsolationProbeService.__new__(CacheIsolationProbeService)
    accounts = [_account("b1", workspace=None), _account("b2", workspace=None)]

    seed, others = service._choose_accounts(accounts, 5)

    assert seed is not None and seed.id == "b1"
    assert [account.id for account in others] == ["b2"]


class _StubSender:
    """Answers each call from a scripted per-account queue and records the order."""

    def __init__(self, results: dict[str, list[ProbeSendResult]]) -> None:
        self._results = {account_id: list(queue) for account_id, queue in results.items()}
        self.calls: list[tuple[str, str]] = []
        self.prefixes: list[str] = []

    async def send(self, account_id: str, *, model: str, prefix: str) -> ProbeSendResult:
        self.calls.append((account_id, model))
        self.prefixes.append(prefix)
        queue = self._results.get(account_id)
        if not queue:
            return _miss()
        return queue.pop(0)


def _hit() -> ProbeSendResult:
    return ProbeSendResult(ok=True, latency_ms=1200, input_tokens=_INPUT_TOKENS, cached_tokens=_HIT_TOKENS)


def _miss() -> ProbeSendResult:
    return ProbeSendResult(ok=True, latency_ms=1100, input_tokens=_INPUT_TOKENS, cached_tokens=_MISS_TOKENS)


def _error() -> ProbeSendResult:
    return ProbeSendResult(ok=False, latency_ms=300, error_code="server_is_overloaded", error_message="overloaded")


def _build(monkeypatch, accounts: list[Account], sender: _StubSender) -> CacheIsolationProbeService:
    service = CacheIsolationProbeService(sender=sender)

    async def _list_accounts() -> list[Account]:
        return accounts

    monkeypatch.setattr(service, "_list_accounts", _list_accounts)
    monkeypatch.setattr(probe_service, "default_probe_model", lambda: "gpt-probe")
    return service


# --- result table -----------------------------------------------------------


async def test_run_reports_one_row_per_call_seed_first(monkeypatch) -> None:
    accounts = [_account("a-seed", alias="Seed"), _account("b-two"), _account("c-three")]
    sender = _StubSender({"a-seed": [_miss(), _hit()], "b-two": [_hit()], "c-three": [_miss()]})
    service = _build(monkeypatch, accounts, sender)

    result = await service.run(seed_repetitions=2, other_account_count=2)

    assert [call.sequence for call in result.calls] == [1, 2, 3, 4]
    assert [call.account_id for call in result.calls] == ["a-seed", "a-seed", "b-two", "c-three"]
    assert [call.role for call in result.calls] == [SEED_ROLE, SEED_ROLE, OTHER_ROLE, OTHER_ROLE]
    assert [call.status for call in result.calls] == ["miss", "hit", "hit", "miss"]
    assert [call.cache_hit for call in result.calls] == [False, True, True, False]
    assert [call.cached_tokens for call in result.calls] == [0, _HIT_TOKENS, _HIT_TOKENS, 0]
    assert all(call.input_tokens == _INPUT_TOKENS for call in result.calls)
    assert result.calls[0].account_label == "Seed"
    assert result.seed_call_count == 2
    assert result.seed_hit_count == 1
    assert result.other_call_count == 2
    assert result.other_hit_count == 1
    assert result.model == "gpt-probe"


async def test_every_call_in_a_run_sends_the_identical_prefix(monkeypatch) -> None:
    accounts = [_account("a-seed"), _account("b-two")]
    sender = _StubSender({})
    service = _build(monkeypatch, accounts, sender)

    await service.run(seed_repetitions=3, other_account_count=1)

    assert len(sender.prefixes) == 4
    assert len(set(sender.prefixes)) == 1


async def test_two_runs_never_reuse_a_prefix(monkeypatch) -> None:
    accounts = [_account("a-seed"), _account("b-two")]
    sender = _StubSender({})
    service = _build(monkeypatch, accounts, sender)

    await service.run(seed_repetitions=1, other_account_count=1)
    await service.run(seed_repetitions=1, other_account_count=1)

    assert len(set(sender.prefixes)) == 2


async def test_partial_cache_below_the_ratio_is_a_miss(monkeypatch) -> None:
    just_under = int(_INPUT_TOKENS * CACHE_HIT_RATIO) - 1
    accounts = [_account("a-seed"), _account("b-two")]
    sender = _StubSender(
        {
            "a-seed": [_hit()],
            "b-two": [replace(_miss(), cached_tokens=just_under)],
        }
    )
    service = _build(monkeypatch, accounts, sender)

    result = await service.run(seed_repetitions=1, other_account_count=1)

    assert result.calls[1].status == "miss"
    assert result.cross_account_hit is False


# --- verdicts ---------------------------------------------------------------


async def test_a_hit_on_any_other_account_proves_the_cache_is_shared(monkeypatch) -> None:
    accounts = [_account("a-seed"), _account("b-two"), _account("c-three")]
    sender = _StubSender({"a-seed": [_miss()], "b-two": [_miss()], "c-three": [_hit()]})
    service = _build(monkeypatch, accounts, sender)

    result = await service.run(seed_repetitions=1, other_account_count=2)

    assert result.cross_account_hit is True
    assert result.verdict == VERDICT_CROSS_ACCOUNT_SHARING


async def test_seed_hits_without_other_hits_reads_as_no_cross_account_hit(monkeypatch) -> None:
    accounts = [_account("a-seed"), _account("b-two")]
    sender = _StubSender({"a-seed": [_hit()], "b-two": [_miss()]})
    service = _build(monkeypatch, accounts, sender)

    result = await service.run(seed_repetitions=1, other_account_count=1)

    assert result.verdict == VERDICT_NO_CROSS_ACCOUNT_HIT


async def test_a_run_where_nothing_cached_at_all_is_inconclusive(monkeypatch) -> None:
    """Sporadic seed misses are expected, but if the seed never cached there is
    no evidence the prefix was cacheable, so silence proves nothing."""

    accounts = [_account("a-seed"), _account("b-two")]
    sender = _StubSender({"a-seed": [_miss(), _miss()], "b-two": [_miss()]})
    service = _build(monkeypatch, accounts, sender)

    result = await service.run(seed_repetitions=2, other_account_count=1)

    assert result.verdict == VERDICT_INCONCLUSIVE


async def test_all_other_calls_failing_is_inconclusive_not_isolation(monkeypatch) -> None:
    accounts = [_account("a-seed"), _account("b-two")]
    sender = _StubSender({"a-seed": [_hit()], "b-two": [_error()]})
    service = _build(monkeypatch, accounts, sender)

    result = await service.run(seed_repetitions=1, other_account_count=1)

    assert result.calls[1].status == "error"
    assert result.calls[1].error_code == "server_is_overloaded"
    assert result.verdict == VERDICT_INCONCLUSIVE


# --- caps -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seed_repetitions", "other_account_count", "code"),
    [
        (0, 1, "seed_repetitions_out_of_range"),
        (MAX_SEED_REPETITIONS + 1, 1, "seed_repetitions_out_of_range"),
        (1, 0, "other_accounts_out_of_range"),
        (1, MAX_OTHER_ACCOUNTS + 1, "other_accounts_out_of_range"),
    ],
)
async def test_run_caps_the_call_count(monkeypatch, seed_repetitions, other_account_count, code) -> None:
    accounts = [_account(f"a-{index}") for index in range(8)]
    sender = _StubSender({})
    service = _build(monkeypatch, accounts, sender)

    with pytest.raises(CacheProbeRefused) as excinfo:
        await service.run(seed_repetitions=seed_repetitions, other_account_count=other_account_count)

    assert excinfo.value.code == code
    assert sender.calls == []


# --- pool pressure ----------------------------------------------------------


def test_a_healthy_pool_is_not_under_pressure() -> None:
    accounts = [_account(f"a-{index}") for index in range(8)]

    pressure = assess_pool_pressure(accounts)

    assert pressure.under_pressure is False
    assert pressure.reason is None
    assert pressure.eligible_account_count == 8


def test_a_throttled_quarter_of_the_pool_is_pressure() -> None:
    accounts = [_account(f"a-{index}") for index in range(6)]
    accounts += [
        _account("b-0", status=AccountStatus.RATE_LIMITED),
        _account("b-1", status=AccountStatus.QUOTA_EXCEEDED),
    ]

    pressure = assess_pool_pressure(accounts)

    assert pressure.under_pressure is True
    assert pressure.reason == "pool_under_pressure"
    assert pressure.pressured_account_count == 2
    assert pressure.selectable_account_count == 8


def test_paused_and_deactivated_accounts_are_not_counted_as_pressure() -> None:
    accounts = [_account(f"a-{index}") for index in range(4)]
    accounts += [
        _account("p-0", status=AccountStatus.PAUSED),
        _account("p-1", status=AccountStatus.DEACTIVATED),
        _account("p-2", status=AccountStatus.REAUTH_REQUIRED),
    ]

    pressure = assess_pool_pressure(accounts)

    assert pressure.under_pressure is False
    assert pressure.selectable_account_count == 4


def test_a_single_account_pool_cannot_answer_the_question() -> None:
    pressure = assess_pool_pressure([_account("only")])

    assert pressure.under_pressure is True
    assert pressure.reason == "insufficient_eligible_accounts"


async def test_requesting_more_siblings_than_exist_runs_with_what_is_there(monkeypatch) -> None:
    """``other_account_count`` is a ceiling: a three-account pool still answers."""

    accounts = [_account(f"a-{index}") for index in range(3)]
    sender = _StubSender({})
    service = _build(monkeypatch, accounts, sender)

    result = await service.run(seed_repetitions=1, other_account_count=MAX_OTHER_ACCOUNTS)

    assert [call.account_id for call in result.calls] == ["a-0", "a-1", "a-2"]
    assert result.other_call_count == 2


def test_degraded_mode_refuses_before_counting_accounts(monkeypatch) -> None:
    monkeypatch.setattr(probe_service, "is_degraded", lambda: True)

    pressure = assess_pool_pressure([_account(f"a-{index}") for index in range(8)])

    assert pressure.under_pressure is True
    assert pressure.reason == "upstream_degraded"


async def test_run_refuses_and_sends_nothing_when_the_pool_is_pressured(monkeypatch) -> None:
    accounts = [
        _account("a-seed"),
        _account("b-two"),
        _account("c-limited", status=AccountStatus.RATE_LIMITED),
    ]
    sender = _StubSender({})
    service = _build(monkeypatch, accounts, sender)

    with pytest.raises(CacheProbeRefused) as excinfo:
        await service.run(seed_repetitions=1, other_account_count=1)

    assert excinfo.value.code == "pool_under_pressure"
    assert sender.calls == []


# --- plan -------------------------------------------------------------------


async def test_plan_prices_the_run_before_the_operator_confirms(monkeypatch) -> None:
    accounts = [_account(f"a-{index}") for index in range(6)]
    sender = _StubSender({})
    service = _build(monkeypatch, accounts, sender)

    plan = await service.plan(seed_repetitions=3, other_account_count=4)

    assert plan.seed_account is not None
    assert plan.seed_account.account_id == "a-0"
    # Every candidate the pool offers, so the dashboard can reprice a different
    # selection without another round trip.
    assert [account.account_id for account in plan.available_other_accounts] == ["a-1", "a-2", "a-3", "a-4", "a-5"]
    assert plan.total_calls == 7
    assert plan.estimated_total_input_tokens == plan.estimated_input_tokens_per_call * 7
    assert plan.estimated_input_tokens_per_call > 20_000
    assert plan.pressure.under_pressure is False
    assert sender.calls == []


async def test_plan_prices_only_the_siblings_a_small_pool_can_offer(monkeypatch) -> None:
    accounts = [_account(f"a-{index}") for index in range(3)]
    sender = _StubSender({})
    service = _build(monkeypatch, accounts, sender)

    plan = await service.plan(seed_repetitions=2, other_account_count=MAX_OTHER_ACCOUNTS)

    assert [account.account_id for account in plan.available_other_accounts] == ["a-1", "a-2"]
    assert plan.total_calls == 4


# --- privacy and partial failure ---------------------------------------------


async def test_the_generated_corpus_never_reaches_the_conversation_archive(monkeypatch) -> None:
    """The archive records what Codex and the upstream said; a locally
    generated 28k-token filler corpus is neither, and would bury the real
    traffic around it."""

    observed: list[bool] = []

    class _ArchiveObservingSender(_StubSender):
        async def send(self, account_id: str, *, model: str, prefix: str) -> ProbeSendResult:
            observed.append(archive_enabled())
            return await super().send(account_id, model=model, prefix=prefix)

    monkeypatch.setattr(conversation_archive, "resolve_archive_enabled", lambda *args, **kwargs: True)
    accounts = [_account("a-seed"), _account("b-two")]
    sender = _ArchiveObservingSender({})
    service = _build(monkeypatch, accounts, sender)

    assert archive_enabled() is True
    await service.run(seed_repetitions=1, other_account_count=1)

    assert observed == [False, False]
    # The suppression is scoped to the run, not a global switch.
    assert archive_enabled() is True


async def test_a_transport_failure_on_one_account_does_not_abort_the_run(monkeypatch) -> None:
    class _ExplodingSender(_StubSender):
        async def send(self, account_id: str, *, model: str, prefix: str) -> ProbeSendResult:
            if account_id == "b-two":
                raise RuntimeError("upstream socket died")
            return await super().send(account_id, model=model, prefix=prefix)

    accounts = [_account("a-seed"), _account("b-two"), _account("c-three")]
    sender = _ExplodingSender({"a-seed": [_hit()], "c-three": [_hit()]})
    service = _build(monkeypatch, accounts, sender)

    result = await service.run(seed_repetitions=1, other_account_count=2)

    assert [call.status for call in result.calls] == ["hit", "error", "hit"]
    assert result.calls[1].error_code == "probe_call_failed"
    # The message is the exception type, never its text: an unclassified
    # failure has not been proved credential-safe.
    assert result.calls[1].account_id == "b-two"
    assert result.verdict == VERDICT_CROSS_ACCOUNT_SHARING
