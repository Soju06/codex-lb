"""The terminal answer of an account walk that ended without a served response.

One rejection per attempted account and no response is not, by itself, the
pool's answer. These tests pin who gets to decide. The bound that ended the walk
decides first: a failure no account can route around is the client's own, a
spent request budget is a timeout, and only an ending the pool caused reaches
the exhaustion probe, which -- asked once -- renders the canonical usage-limit
429 or hands the last attempted account's failure back untouched.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.balancer import PoolWalkBound
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._load_balancer.types import AccountLeaseKind
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.pool_terminal import PoolUsageLimit, resolve_pool_terminal_failure

pytestmark = pytest.mark.unit

_MODEL = "gpt-5.4"
_RESETS_AT = 1_800_000_000

# The endings that leave "could another account have served this?" open, and so
# are the pool's to answer.
_BOUNDS_THAT_ASK_THE_POOL: tuple[PoolWalkBound, ...] = ("pool_exhausted", "ceiling", "no_progress")


class _StubAdmission:
    """The selector seam the probe talks to, recording every question it is asked."""

    def __init__(self, selection: AccountSelection) -> None:
        self._selection = selection
        self.calls: list[dict[str, object]] = []

    async def check_opportunistic_admission(
        self,
        *,
        api_key: ApiKeyData | None,
        model: str | None,
        service_tier: str | None,
        lease_kind: AccountLeaseKind | None,
        observe_only: bool,
    ) -> AccountSelection:
        self.calls.append(
            {
                "api_key": api_key,
                "model": model,
                "service_tier": service_tier,
                "lease_kind": lease_kind,
                "observe_only": observe_only,
            }
        )
        return self._selection


class _RaisingAdmission:
    def __init__(self) -> None:
        self.calls = 0

    async def check_opportunistic_admission(
        self,
        *,
        api_key: ApiKeyData | None,
        model: str | None,
        service_tier: str | None,
        lease_kind: AccountLeaseKind | None,
        observe_only: bool,
    ) -> AccountSelection:
        self.calls += 1
        raise RuntimeError("selection inputs unavailable")


def _exhausted(*, resets_at: int | None = _RESETS_AT) -> AccountSelection:
    return AccountSelection(
        account=None,
        error_message="All accounts have reached their usage limit",
        error_code="usage_limit_reached",
        resets_at=resets_at,
    )


def _no_accounts() -> AccountSelection:
    return AccountSelection(account=None, error_message="No active accounts available", error_code="no_accounts")


def _settings(routing_strategy: str = "capacity_weighted") -> SimpleNamespace:
    return SimpleNamespace(routing_strategy=routing_strategy)


def _api_key() -> ApiKeyData:
    """A key whose account scope narrows the pool, so forwarding it is observable."""

    return ApiKeyData(
        id="key_pool_terminal",
        name="pool-terminal",
        key_prefix="sk-poolter",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        last_used_at=None,
        account_assignment_scope_enabled=True,
        assigned_account_ids=["acc_scoped_a", "acc_scoped_b"],
    )


def _account_failure() -> ProxyResponseError:
    """The burst 429 one account answered with, preserved exactly as the transport caught it."""

    return ProxyResponseError(
        429,
        openai_error("upstream_error", "Too Many Requests", error_type="upstream_error"),
        retry_after_seconds=5,
        retry_after_header="5",
        upstream_status_code=429,
    )


def _error_code(exc: ProxyResponseError) -> str | None:
    error = exc.payload.get("error") if isinstance(exc.payload, dict) else None
    return error.get("code") if isinstance(error, dict) else None


async def test_exhausted_pool_renders_the_canonical_usage_limit_rejection() -> None:
    service = _StubAdmission(_exhausted())
    last_account_failure = _account_failure()

    terminal = await resolve_pool_terminal_failure(
        service,
        settings=_settings(),
        api_key=None,
        model=_MODEL,
        service_tier=None,
        last_account_failure=last_account_failure,
        ended_by="pool_exhausted",
    )

    assert isinstance(terminal, PoolUsageLimit)
    assert terminal.status_code == 429
    assert terminal.payload["error"]["code"] == "usage_limit_reached"
    assert terminal.payload["error"]["type"] == "usage_limit_reached"
    assert terminal.payload["error"]["resets_at"] == _RESETS_AT
    assert terminal.resets_at == _RESETS_AT
    assert terminal.message == "All accounts have reached their usage limit"
    # The reset deadline is the whole retry hint: no Retry-After travels with
    # this rejection, so the rendering carries no retry field to forward.
    assert set(terminal.payload["error"]) == {"code", "type", "message", "resets_at"}
    assert [field.name for field in fields(terminal) if "retry" in field.name] == []


async def test_exhausted_pool_without_a_reset_deadline_omits_resets_at() -> None:
    service = _StubAdmission(_exhausted(resets_at=None))

    terminal = await resolve_pool_terminal_failure(
        service,
        settings=_settings(),
        api_key=None,
        model=_MODEL,
        service_tier=None,
        last_account_failure=_account_failure(),
        ended_by="pool_exhausted",
    )

    assert isinstance(terminal, PoolUsageLimit)
    assert terminal.resets_at is None
    assert "resets_at" not in terminal.payload["error"]


async def test_probe_is_asked_once_with_the_request_own_eligibility() -> None:
    service = _StubAdmission(_exhausted())
    api_key = _api_key()

    await resolve_pool_terminal_failure(
        service,
        settings=_settings(),
        api_key=api_key,
        model=_MODEL,
        service_tier="priority",
        last_account_failure=_account_failure(),
        ended_by="pool_exhausted",
    )

    # The key is the request's own, not a stand-in: an account-scoped key that
    # did not reach the probe would have it answer over the whole fleet and
    # call a pool the request could never have used "not exhausted".
    assert service.calls == [
        {
            "api_key": api_key,
            "model": _MODEL,
            "service_tier": "priority",
            "lease_kind": None,
            "observe_only": True,
        }
    ]
    assert service.calls[0]["api_key"] is api_key


async def test_unexhausted_pool_returns_the_last_account_failure_untouched() -> None:
    service = _StubAdmission(_no_accounts())
    last_account_failure = _account_failure()
    payload_before = deepcopy(last_account_failure.payload)

    terminal = await resolve_pool_terminal_failure(
        service,
        settings=_settings(),
        api_key=None,
        model=_MODEL,
        service_tier=None,
        last_account_failure=last_account_failure,
        ended_by="pool_exhausted",
    )

    assert terminal is last_account_failure
    assert terminal.status_code == 429
    assert terminal.payload == payload_before
    assert terminal.retry_after_seconds == 5
    assert terminal.retry_after_header == "5"
    assert len(service.calls) == 1


async def test_drain_strategy_decline_keeps_the_per_account_failure() -> None:
    # The probe declines without consulting the selector under a drain
    # strategy, so a draining deployment answers exactly as it does today.
    service = _StubAdmission(_exhausted())
    last_account_failure = _account_failure()

    for routing_strategy in ("sequential_drain", "reset_drain", "single_account"):
        terminal = await resolve_pool_terminal_failure(
            service,
            settings=_settings(routing_strategy),
            api_key=None,
            model=_MODEL,
            service_tier=None,
            last_account_failure=last_account_failure,
            ended_by="pool_exhausted",
        )
        assert terminal is last_account_failure

    assert service.calls == []


async def test_walk_without_a_preserved_failure_leaves_the_caller_to_selection() -> None:
    service = _StubAdmission(_no_accounts())

    terminal = await resolve_pool_terminal_failure(
        service,
        settings=_settings(),
        api_key=None,
        model=_MODEL,
        service_tier=None,
        last_account_failure=None,
        ended_by="pool_exhausted",
    )

    assert terminal is None


async def test_a_probe_that_cannot_answer_does_not_replace_the_account_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _RaisingAdmission()
    last_account_failure = _account_failure()

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.pool_terminal"):
        terminal = await resolve_pool_terminal_failure(
            service,
            settings=_settings(),
            api_key=None,
            model=_MODEL,
            service_tier=None,
            last_account_failure=last_account_failure,
            ended_by="pool_exhausted",
        )

    assert terminal is last_account_failure
    assert service.calls == 1
    assert "pool_terminal_probe_failed" in caplog.text


class TestTheBoundThatEndedTheWalkDecides:
    """Not every ending may be answered with the pool's rejection."""

    async def test_budget_exhaustion_ends_the_walk_with_a_timeout(self) -> None:
        # Even with every account out of quota, a request that ran out of time
        # ran out of time: "Budget exhaustion during a walk ends the walk"
        # requires upstream_request_timeout rather than a usage-limit 429.
        service = _StubAdmission(_exhausted())
        last_account_failure = _account_failure()

        terminal = await resolve_pool_terminal_failure(
            service,
            settings=_settings(),
            api_key=None,
            model=_MODEL,
            service_tier=None,
            last_account_failure=last_account_failure,
            ended_by="deadline",
        )

        assert isinstance(terminal, ProxyResponseError)
        assert terminal is not last_account_failure
        assert terminal.status_code == 502
        assert _error_code(terminal) == "upstream_request_timeout"
        assert terminal.payload["error"]["message"] == "Proxy request budget exhausted"
        assert service.calls == []

    async def test_budget_exhaustion_answers_even_without_a_preserved_failure(self) -> None:
        service = _StubAdmission(_exhausted())

        terminal = await resolve_pool_terminal_failure(
            service,
            settings=_settings(),
            api_key=None,
            model=_MODEL,
            service_tier=None,
            last_account_failure=None,
            ended_by="deadline",
        )

        assert isinstance(terminal, ProxyResponseError)
        assert _error_code(terminal) == "upstream_request_timeout"

    async def test_a_non_retryable_failure_surfaces_as_itself(self) -> None:
        # A request no account can route around is the client's own failure.
        # Rendering the pool over it would answer a bad request with a reset
        # deadline that will not make it good.
        service = _StubAdmission(_exhausted())
        last_account_failure = ProxyResponseError(
            400,
            openai_error("invalid_request_error", "Unknown parameter: 'foo'", error_type="invalid_request_error"),
        )

        terminal = await resolve_pool_terminal_failure(
            service,
            settings=_settings(),
            api_key=None,
            model=_MODEL,
            service_tier=None,
            last_account_failure=last_account_failure,
            ended_by="non_retryable",
        )

        assert terminal is last_account_failure
        assert terminal.status_code == 400
        assert service.calls == []

    @pytest.mark.parametrize("ended_by", _BOUNDS_THAT_ASK_THE_POOL)
    async def test_endings_the_pool_owns_reach_the_probe(self, ended_by: PoolWalkBound) -> None:
        service = _StubAdmission(_exhausted())

        terminal = await resolve_pool_terminal_failure(
            service,
            settings=_settings(),
            api_key=None,
            model=_MODEL,
            service_tier=None,
            last_account_failure=_account_failure(),
            ended_by=ended_by,
        )

        assert isinstance(terminal, PoolUsageLimit)
        assert len(service.calls) == 1

    @pytest.mark.parametrize(
        ("ended_by", "answer"),
        [
            ("pool_exhausted", "pool_usage_limit"),
            ("ceiling", "pool_usage_limit"),
            ("no_progress", "pool_usage_limit"),
            ("deadline", "budget_exhausted"),
            ("non_retryable", "account_failure"),
        ],
    )
    async def test_every_ending_records_the_bound_that_caused_it(
        self,
        ended_by: PoolWalkBound,
        answer: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Collapsing the endings into one undifferentiated "surface" leaves an
        # operator unable to tell a bad request from an exhausted fleet.
        service = _StubAdmission(_exhausted())

        with caplog.at_level(logging.INFO, logger="app.modules.proxy.pool_terminal"):
            await resolve_pool_terminal_failure(
                service,
                settings=_settings(),
                api_key=None,
                model=_MODEL,
                service_tier=None,
                last_account_failure=_account_failure(),
                ended_by=ended_by,
            )

        recorded = [record.getMessage() for record in caplog.records if "pool_walk_terminal" in record.getMessage()]
        assert len(recorded) == 1
        assert f"ended_by={ended_by}" in recorded[0]
        assert f"answer={answer}" in recorded[0]
