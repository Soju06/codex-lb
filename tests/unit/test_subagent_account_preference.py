from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast

import pytest

from app.core.openai.requests import ResponsesRequest
from app.db.models import StickySessionKind
from app.modules.proxy import affinity as proxy_affinity
from app.modules.proxy import service as proxy_service
from app.modules.proxy import subagent_preference
from app.modules.proxy.load_balancer import AccountSelection, LoadBalancer


def _policy(body: dict[str, Any], *, thread_id: str = "child") -> proxy_affinity._AffinityPolicy:
    return proxy_affinity._sticky_key_for_responses_request(
        ResponsesRequest.model_validate({"instructions": "test", **body}),
        {
            "session-id": "process",
            "thread-id": thread_id,
            "x-openai-subagent": "collab_spawn",
            "x-codex-parent-thread-id": "parent",
        },
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )


class _StickyRepository:
    def __init__(self, rows: dict[tuple[str, StickySessionKind], str]) -> None:
        self.rows = rows

    async def get_account_id(self, key: str, *, kind: StickySessionKind, **_: object) -> str | None:
        return self.rows.get((key, kind))

    async def upsert(self, key: str, account_id: str, *, kind: StickySessionKind) -> None:
        self.rows[(key, kind)] = account_id


class _SettingsCache:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def get(self) -> SimpleNamespace:
        return SimpleNamespace(subagent_account_preference=self.mode)


def _service(
    sticky: _StickyRepository,
    selector: Any,
) -> proxy_service.ProxyService:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(sticky_sessions=sticky)

    return cast(
        proxy_service.ProxyService,
        SimpleNamespace(_repo_factory=repo_factory, _select_account_with_budget=selector),
    )


def test_affinity_derives_lineage_without_raw_ids_and_rejects_hard_child() -> None:
    fresh = _policy({"model": "gpt-5.6-sol", "input": "work"})
    hard = _policy({"model": "gpt-5.6-sol", "input": "continue", "previous_response_id": "resp_secret"})

    assert fresh.subagent_parent_selection_key is not None
    assert fresh.subagent_parent_response_marker_key is not None
    assert "parent" not in fresh.subagent_parent_response_marker_key
    assert hard.subagent_parent_selection_key is None
    assert hard.response_bound_thread_marker_key is not None
    assert "resp_secret" not in hard.response_bound_thread_marker_key

    explicit_turn = proxy_affinity._sticky_key_for_responses_request(
        ResponsesRequest.model_validate({"model": "gpt-5.6-sol", "instructions": "test", "input": "work"}),
        {
            "session-id": "process",
            "thread-id": "child",
            "x-codex-turn-state": "hard-turn",
            "x-openai-subagent": "collab_spawn",
            "x-codex-parent-thread-id": "parent",
        },
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )
    assert explicit_turn.subagent_parent_selection_key is None


def test_lineage_keys_never_reach_the_load_balancer() -> None:
    accepted = set(inspect.signature(LoadBalancer.select_account).parameters)
    for policy in (
        _policy({"model": "gpt-5.6-sol", "input": "work"}),
        _policy({"model": "gpt-5.6-sol", "input": "continue", "previous_response_id": "resp_owner"}),
    ):
        assert set(policy.selection_kwargs()) <= accepted


@pytest.mark.asyncio
async def test_parent_bound_mode_prefers_another_account(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _policy({"model": "gpt-5.6-sol", "input": "work"})
    assert policy.subagent_parent_response_marker_key is not None
    sticky = _StickyRepository(
        {(policy.subagent_parent_response_marker_key, StickySessionKind.CODEX_SESSION): "account-parent"}
    )
    calls: list[set[str]] = []

    async def selector(_deadline: float, **kwargs: object) -> AccountSelection:
        excluded = set(cast(set[str], kwargs.get("exclude_account_ids") or set()))
        calls.append(excluded)
        account_id = "account-child" if "account-parent" in excluded else "account-parent"
        return AccountSelection(account=cast(Any, SimpleNamespace(id=account_id)), error_message=None)

    monkeypatch.setattr(subagent_preference, "get_settings_cache", lambda: _SettingsCache("parent_bound_only"))
    result = await proxy_service.ProxyService._select_account_with_budget_compatible(
        _service(sticky, selector),
        10.0,
        affinity_policy=policy,
        request_stage="first_turn",
    )

    assert result.account is not None and result.account.id == "account-child"
    assert calls == [{"account-parent"}]


@pytest.mark.asyncio
async def test_preference_falls_back_to_parent_and_records_response_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _policy({"model": "gpt-5.6-sol", "input": "work"})
    assert child.subagent_parent_response_marker_key is not None
    sticky = _StickyRepository(
        {(child.subagent_parent_response_marker_key, StickySessionKind.CODEX_SESSION): "account-parent"}
    )
    calls: list[set[str]] = []

    async def selector(_deadline: float, **kwargs: object) -> AccountSelection:
        excluded = set(cast(set[str], kwargs.get("exclude_account_ids") or set()))
        calls.append(excluded)
        if excluded:
            return AccountSelection(account=None, error_message="no alternate", error_code="no_accounts")
        return AccountSelection(account=cast(Any, SimpleNamespace(id="account-parent")), error_message=None)

    monkeypatch.setattr(subagent_preference, "get_settings_cache", lambda: _SettingsCache("parent_bound_only"))
    result = await proxy_service.ProxyService._select_account_with_budget_compatible(
        _service(sticky, selector),
        10.0,
        affinity_policy=child,
        request_stage="first_turn",
    )
    assert result.account is not None and result.account.id == "account-parent"
    assert calls == [{"account-parent"}, set()]

    parent = _policy(
        {"model": "gpt-5.6-sol", "input": "continue", "previous_response_id": "resp_owner"},
        thread_id="parent",
    )
    assert parent.response_bound_thread_marker_key is not None
    await proxy_service.ProxyService._select_account_with_budget_compatible(
        _service(sticky, selector),
        10.0,
        affinity_policy=parent,
        request_stage="follow_up",
        preferred_account_id="account-parent",
        preferred_account_is_continuity_owner=True,
    )
    assert sticky.rows[(parent.response_bound_thread_marker_key, StickySessionKind.CODEX_SESSION)] == "account-parent"
