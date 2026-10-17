"""HTTP retry-loop coverage for request-local soft sticky spillover.

The native Codex HTTP path re-selects an account on every retry attempt. When
the prompt-cache thread owner fails with a transient upstream error, the retry
loop excludes it for this request only; the sticky row must survive so the
next turn returns to its warm owner. These tests drive the real streaming
retry loop into the real ``LoadBalancer`` (stub repositories only) so the
plumbing between the two -- exclusion without ``reallocate_sticky`` and the
thread affinity's TTL -- stays pinned.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.core.clients.proxy as proxy_module
import app.modules.proxy.load_balancer as load_balancer_module
from app.core.errors import openai_error
from app.core.openai.requests import ResponsesRequest
from app.db.models import Account, StickySessionKind
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy import affinity as proxy_affinity
from app.modules.proxy import service as proxy_service
from app.modules.proxy.capability_lineage_repository import CapabilityLineageRepository
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository
from tests.unit.test_load_balancer_concurrency import (
    _make_account,
    _StubAccountsRepository,
    _StubStickySessionsRepository,
    _StubUsageRepository,
    _usage_row,
)
from tests.unit.test_proxy_utils import _make_proxy_settings, _RequestLogsRecorder, _SettingsCache

pytestmark = pytest.mark.unit

_MODEL_UNSUPPORTED_MESSAGE = "The 'gpt-5.6-sol' model is not supported when using Codex with a ChatGPT account."


class _Harness:
    def __init__(self, prefix: str) -> None:
        self.owner = _make_account(f"{prefix}-owner")
        self.alternate = _make_account(f"{prefix}-alternate")
        accounts = [self.owner, self.alternate]
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
        usage_rows = {
            account.id: _usage_row(index + 100, account.id, window="primary", reset_at=now_epoch + 300)
            for index, account in enumerate(accounts)
        }
        secondary_rows = {
            account.id: _usage_row(index + 200, account.id, window="secondary", reset_at=now_epoch + 3600)
            for index, account in enumerate(accounts)
        }
        self.sticky_repo = _StubStickySessionsRepository()
        self.request_logs = _RequestLogsRecorder()
        accounts_repo = _StubAccountsRepository(accounts)
        usage_repo = _StubUsageRepository(usage_rows, secondary_rows)
        capability_lineage = AsyncMock(spec=CapabilityLineageRepository)
        capability_lineage.is_required.return_value = False

        @asynccontextmanager
        async def repo_factory() -> AsyncIterator[ProxyRepositories]:
            yield ProxyRepositories(
                accounts=cast(Any, accounts_repo),
                usage=cast(Any, usage_repo),
                request_logs=cast(RequestLogsRepository, self.request_logs),
                sticky_sessions=cast(Any, self.sticky_repo),
                api_keys=cast(ApiKeysRepository, object()),
                additional_usage=cast(AdditionalUsageRepository, object()),
                capability_lineage=capability_lineage,
            )

        self.service = proxy_service.ProxyService(repo_factory)
        self.headers = {"session_id": f"{prefix}-session", "thread-id": f"{prefix}-thread", "user-agent": "codex/1.2"}
        thread_key = proxy_affinity._codex_backend_identity(self.headers).thread_selection_key
        assert thread_key is not None
        self.thread_key: str = thread_key
        self.sticky_repo.account_ids_by_key = {self.thread_key: self.owner.id}
        self.upstream_account_ids: list[str | None] = []
        self.sticky_requests: list[tuple[frozenset[str], StickySessionKind | None, int | None, bool]] = []
        # Sticky-repo mutation count (upserts + deletes) before/after each selection.
        self.mutation_counts: list[tuple[int, int]] = []

    def account_for_upstream_id(self, account_id: str | None) -> Account:
        return next(account for account in (self.owner, self.alternate) if account.chatgpt_account_id == account_id)

    def mutation_count(self) -> int:
        return len(self.sticky_repo.upserts) + len(self.sticky_repo.deleted)

    def thread_upsert_targets(self) -> list[str]:
        return [upsert[1] for upsert in self.sticky_repo.upserts if upsert[0] == self.thread_key]


def _install(monkeypatch: pytest.MonkeyPatch, harness: _Harness, owner_failure: Exception) -> None:
    settings = _make_proxy_settings()
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    async def ensure_fresh(account: Account, **_kwargs: object) -> Account:
        return account

    monkeypatch.setattr(harness.service, "_ensure_fresh", ensure_fresh)

    async def fake_stream(payload: object, headers: object, access_token: object, account_id: str | None, **_: object):
        del payload, headers, access_token
        harness.upstream_account_ids.append(account_id)
        account = harness.account_for_upstream_id(account_id)
        if account.id == harness.owner.id and len(harness.upstream_account_ids) == 1:
            raise owner_failure
        response_id = f"resp_{len(harness.upstream_account_ids)}"
        yield f'data: {{"type":"response.completed","response":{{"id":"{response_id}"}}}}\n\n'

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    real_run = load_balancer_module.run_sticky_selection_path

    async def capture(owner_balancer: Any, *, request: Any) -> Any:
        harness.sticky_requests.append(
            (
                request.exclude_account_ids,
                request.sticky_kind,
                request.sticky_max_age_seconds,
                request.reallocate_sticky,
            )
        )
        before = harness.mutation_count()
        try:
            return await real_run(owner_balancer, request=request)
        finally:
            harness.mutation_counts.append((before, harness.mutation_count()))

    monkeypatch.setattr(load_balancer_module, "run_sticky_selection_path", capture)


async def _stream_turn(harness: _Harness) -> dict[str, Any]:
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.6-sol", "instructions": "hi", "input": [], "stream": True}
    )
    stream = harness.service.stream_responses(payload, dict(harness.headers), codex_session_affinity=True)
    chunks = [chunk async for chunk in stream]
    return json.loads(chunks[-1].split("data: ", 1)[1])


def _codeless_429() -> proxy_module.ProxyResponseError:
    # The upstream per-account burst rejection carries neither ``code`` nor
    # ``type``; the streaming path normalizes it to ``upstream_error`` and the
    # retry loop fails over to the next account.
    return proxy_module.ProxyResponseError(429, {"error": {"message": "Rate limit exceeded"}})


@pytest.mark.asyncio
async def test_http_retry_transient_owner_failure_keeps_prompt_cache_owner(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=load_balancer_module.__name__)
    harness = _Harness("http-soft-spill")
    _install(monkeypatch, harness, _codeless_429())

    event = await _stream_turn(harness)

    assert event["type"] == "response.completed"
    assert harness.upstream_account_ids == [harness.owner.chatgpt_account_id, harness.alternate.chatgpt_account_id]
    # The failover reselect excludes the owner without flipping
    # reallocate_sticky, and the thread affinity reaches selection as a
    # TTL-bounded prompt-cache row: both are required by the predicate.
    assert harness.sticky_requests == [
        (frozenset(), StickySessionKind.PROMPT_CACHE, 300, False),
        (frozenset({harness.owner.id}), StickySessionKind.PROMPT_CACHE, 300, False),
    ]
    # The first (owner) hit seeds the thread row from the process-session row;
    # the failover reselect persists nothing: no rebind, no delete.
    assert harness.mutation_counts[1][0] == harness.mutation_counts[1][1]
    assert set(harness.thread_upsert_targets()) <= {harness.owner.id}
    assert harness.sticky_repo.deleted == []
    assert harness.sticky_repo.account_ids_by_key is not None
    assert harness.sticky_repo.account_ids_by_key[harness.thread_key] == harness.owner.id
    assert "internal_soft_affinity_spillover" in caplog.text
    assert await harness.service.drain_persistence_tasks(timeout_seconds=1)

    # Next turn: the owner is healthy again and the conversation returns to it.
    harness.sticky_requests.clear()
    event = await _stream_turn(harness)
    assert event["type"] == "response.completed"
    assert harness.upstream_account_ids[-1] == harness.owner.chatgpt_account_id
    assert harness.sticky_requests == [(frozenset(), StickySessionKind.PROMPT_CACHE, 300, False)]
    assert harness.sticky_repo.account_ids_by_key[harness.thread_key] == harness.owner.id
    assert await harness.service.drain_persistence_tasks(timeout_seconds=1)


@pytest.mark.asyncio
async def test_http_retry_model_unsupported_owner_rejection_still_rebinds_prompt_cache_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness("http-model-rebind")
    _install(
        monkeypatch,
        harness,
        proxy_module.ProxyResponseError(
            400,
            openai_error("invalid_request_error", _MODEL_UNSUPPORTED_MESSAGE, error_type="invalid_request_error"),
        ),
    )

    event = await _stream_turn(harness)

    assert event["type"] == "response.completed"
    assert harness.upstream_account_ids == [harness.owner.chatgpt_account_id, harness.alternate.chatgpt_account_id]
    # The account/model rejection site excludes the owner *and* requests
    # reallocation, so the alternate becomes the durable owner as before.
    assert harness.sticky_requests[-1] == (
        frozenset({harness.owner.id}),
        StickySessionKind.PROMPT_CACHE,
        300,
        True,
    )
    assert harness.mutation_counts[-1][1] > harness.mutation_counts[-1][0]
    assert harness.thread_upsert_targets()[-1] == harness.alternate.id
    assert harness.sticky_repo.account_ids_by_key is not None
    assert harness.sticky_repo.account_ids_by_key[harness.thread_key] == harness.alternate.id
    assert await harness.service.drain_persistence_tasks(timeout_seconds=1)
