"""Unit tests for the provider filter on ``LoadBalancer.select_account``.

See ``openspec/changes/add-claude-oauth-pool/specs/account-routing/spec.md``:
``Requirement: Provider-discriminated account pool`` (3 scenarios) and
``Requirement: Claude rate-limit cooldown mirrors Codex cooldown``
(2 scenarios + a Codex-path regression check).

The tests drive a real ``LoadBalancer`` through stub repositories so the
filter is exercised the way ``ProxyService`` would exercise it — through
``_load_selection_inputs`` and the in-memory cooldown bookkeeping. They
do NOT touch ``app.modules.proxy.service``; that file is out of scope
per CLAUDE.md / ADR-0001 / the OpenSpec design.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind, UsageHistory
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_account(account_id: str, *, provider: str = "codex") -> Account:
    """Build a single Account row with a configurable ``provider`` value.

    Mirrors the shape produced by ``Account`` in production — only the
    attributes ``LoadBalancer._load_selection_inputs`` actually reads are
    populated. Tokens are encrypted with a fresh ``TokenEncryptor`` so the
    Account is a valid ORM instance without needing the cipher env.
    """
    encryptor = TokenEncryptor()
    now = datetime.now(tz=timezone.utc)
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com" if provider == "codex" else None,
        plan_type="plus",
        provider=provider,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


class _StubAccountsRepository:
    """In-memory accounts repo exposing the methods LoadBalancer exercises."""

    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = list(accounts)
        # Recorded calls for assertions on cooldown-persistence tests.
        self.status_updates: list[dict[str, Any]] = []
        self.rate_limit_cache_writes: list[dict[str, Any]] = []

    async def list_accounts(self) -> list[Account]:
        return list(self._accounts)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = ...,
    ) -> bool:
        self.status_updates.append(
            {
                "account_id": account_id,
                "status": status,
                "reset_at": reset_at,
            }
        )
        return True

    async def update_status_if_current(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def update_rate_limit_cache(self, account_id: str, fields: dict[str, object]) -> bool:
        """Narrow helper used by the Claude cooldown branch in T8.2.

        Captures the parsed fields for the assertion in tests. This method
        exists on the repo only because LoadBalancer._persist_claude_rate_limit
        writes through it; tests stub it on the fly.
        """
        self.rate_limit_cache_writes.append({"account_id": account_id, "fields": dict(fields)})
        return True


class _StubUsageRepository:
    def __init__(
        self,
        primary: dict[str, UsageHistory] | None = None,
        secondary: dict[str, UsageHistory] | None = None,
    ) -> None:
        self._primary = primary or {}
        self._secondary = secondary or {}

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        del account_ids
        if window == "secondary":
            return self._secondary
        return self._primary


class _StubStickySessionsRepository:
    def __init__(self) -> None:
        self.account_id: str | None = None
        self.upserts: list[tuple[str, str, StickySessionKind | None]] = []
        self.deleted: list[tuple[str, StickySessionKind | None]] = []

    async def get_account_id(self, *args: Any, **kwargs: Any) -> str | None:
        del args, kwargs
        return self.account_id

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        sticky_key = cast(str, args[0])
        account_id = cast(str, args[1])
        self.account_id = account_id
        self.upserts.append((sticky_key, account_id, kwargs.get("kind")))
        return None

    async def delete(self, *args: Any, **kwargs: Any) -> bool:
        sticky_key = cast(str, args[0])
        self.deleted.append((sticky_key, kwargs.get("kind")))
        self.account_id = None
        return True


@asynccontextmanager
async def _repo_factory(
    accounts_repo: _StubAccountsRepository,
    usage_repo: _StubUsageRepository,
    sticky_repo: _StubStickySessionsRepository | None = None,
) -> AsyncIterator[ProxyRepositories]:
    sticky_repo = sticky_repo or _StubStickySessionsRepository()
    yield ProxyRepositories(
        accounts=cast(Any, accounts_repo),
        usage=cast(Any, usage_repo),
        request_logs=cast(RequestLogsRepository, object()),
        sticky_sessions=cast(Any, sticky_repo),
        api_keys=cast(ApiKeysRepository, object()),
        additional_usage=cast(AdditionalUsageRepository, object()),
    )


def _seed_pool(*account_ids_provider: tuple[str, str]) -> tuple[_StubAccountsRepository, _StubUsageRepository]:
    """Build a stub repo seeded with ``(account_id, provider)`` tuples."""
    accounts = [_make_account(aid, provider=provider) for aid, provider in account_ids_provider]
    return _StubAccountsRepository(accounts), _StubUsageRepository()


# ---------------------------------------------------------------------------
# Task 8.1 — provider filter on select_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_account_filters_by_provider_codex() -> None:
    """Scenario 1: provider='codex' excludes Claude accounts from the pool."""
    accounts_repo, usage_repo = _seed_pool(
        ("codex-acc-1", "codex"),
        ("codex-acc-2", "codex"),
        ("claude-acc-1", "claude"),
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    chosen = []
    # Sample many times so the assertion is robust against the balancer's
    # random weighted pick. Only codex-* ids must ever appear.
    for _ in range(50):
        result = await balancer.select_account(provider="codex")
        if result.account is not None:
            chosen.append(result.account.id)

    assert chosen, "expected at least one selection from a healthy codex pool"
    assert set(chosen).issubset({"codex-acc-1", "codex-acc-2"})
    assert "claude-acc-1" not in chosen


@pytest.mark.asyncio
async def test_select_account_filters_by_provider_claude() -> None:
    """Scenario 2: provider='claude' excludes Codex accounts from the pool."""
    accounts_repo, usage_repo = _seed_pool(
        ("codex-acc-1", "codex"),
        ("codex-acc-2", "codex"),
        ("claude-acc-1", "claude"),
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    result = await balancer.select_account(provider="claude")

    assert result.account is not None
    assert result.account.id == "claude-acc-1"
    # The Codex accounts must not appear anywhere in the snapshot.
    assert result.account.provider == "claude"


@pytest.mark.asyncio
async def test_select_account_claude_no_candidates_returns_empty() -> None:
    """Scenario 3: provider='claude' with no Claude accounts returns no candidate.

    The proxy layer maps this selection failure to a 503 with an
    OpenAI-compatible error envelope (see ``account-routing/spec.md``
    Scenario 3). The balancer itself just returns ``AccountSelection(account=None)``
    with no error code so the upstream ProxyService / ClaudeProxyService
    can shape the response.
    """
    accounts_repo, usage_repo = _seed_pool(
        ("codex-acc-1", "codex"),
        ("codex-acc-2", "codex"),
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    result = await balancer.select_account(provider="claude")

    assert result.account is None
    # No candidates selected — caller decides the user-facing envelope.
    assert result.error_code is None or result.error_message is not None


@pytest.mark.asyncio
async def test_select_account_provider_none_preserves_legacy_behavior() -> None:
    """Back-compat: provider=None (default) returns the Codex pool unchanged.

    Existing Codex callers do not pass ``provider``. They must continue to
    see only the Codex pool — Claude accounts in the DB must be ignored
    by Codex requests even when ``provider=None`` is explicit.
    """
    accounts_repo, usage_repo = _seed_pool(
        ("codex-acc-1", "codex"),
        ("codex-acc-2", "codex"),
        ("claude-acc-1", "claude"),
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    chosen = []
    for _ in range(50):
        result = await balancer.select_account(provider=None)
        if result.account is not None:
            chosen.append(result.account.id)

    assert chosen, "expected at least one selection from a healthy codex pool"
    assert set(chosen).issubset({"codex-acc-1", "codex-acc-2"})
    assert "claude-acc-1" not in chosen


# ---------------------------------------------------------------------------
# Task 8.2 — Claude rate-limit cooldown branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_429_sets_cooldown_and_persists_rate_limit_headers() -> None:
    """Anthropic 429 on a Claude account must set RATE_LIMITED + persist headers.

    The branch is gated on ``provider == "claude"``. The mock upstream
    returns 429 with the verified ``anthropic-ratelimit-*`` header set;
    the cooldown bookkeeping is expected to:

    1. Set ``Account.status = RATE_LIMITED``.
    2. Set ``Account.reset_at`` to a future unix timestamp parsed from
       ``anthropic-ratelimit-requests-reset`` (RFC 3339 → unix epoch).
    3. Persist the 7 parsed rate-limit columns via the repo helper.
    """
    claude_account = _make_account("claude-1", provider="claude")
    accounts_repo = _StubAccountsRepository([claude_account])
    usage_repo = _StubUsageRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    reset_iso = "2030-01-01T12:00:00Z"
    expected_reset_epoch = int(datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
    headers = {
        "anthropic-ratelimit-requests-remaining": "0",
        "anthropic-ratelimit-requests-reset": reset_iso,
        "anthropic-ratelimit-input-tokens-remaining": "0",
        "anthropic-ratelimit-input-tokens-reset": reset_iso,
        "anthropic-ratelimit-output-tokens-remaining": "0",
        "anthropic-ratelimit-output-tokens-reset": reset_iso,
        "anthropic-ratelimit-status": "rejected",
    }

    await balancer.record_claude_rate_limit_response(
        account=claude_account,
        headers=headers,
    )

    # Cooldown state: status + reset_at must be persisted to the DB.
    assert len(accounts_repo.status_updates) == 1
    update = accounts_repo.status_updates[0]
    assert update["account_id"] == "claude-1"
    assert update["status"] == AccountStatus.RATE_LIMITED
    assert update["reset_at"] == expected_reset_epoch
    assert update["reset_at"] > int(time.time())

    # Rate-limit cache: all 7 parsed columns must be persisted via the
    # dedicated repo helper, keyed by account_id.
    assert len(accounts_repo.rate_limit_cache_writes) == 1
    write = accounts_repo.rate_limit_cache_writes[0]
    assert write["account_id"] == "claude-1"
    fields = write["fields"]
    assert fields["rate_limit_requests_remaining"] == 0
    assert fields["rate_limit_input_tokens_remaining"] == 0
    assert fields["rate_limit_output_tokens_remaining"] == 0
    assert fields["rate_limit_status"] == "rejected"
    # Reset columns are tz-aware datetimes; just confirm they're present.
    assert "rate_limit_requests_reset_at" in fields
    assert "rate_limit_input_tokens_reset_at" in fields
    assert "rate_limit_output_tokens_reset_at" in fields


@pytest.mark.asyncio
async def test_claude_200_clears_stale_cooldown_and_persists_rate_limit_headers() -> None:
    """Anthropic 200 on a Claude account with a stale cooldown clears it.

    A Claude account whose ``reset_at`` is already in the past should be
    returned to ACTIVE on a successful 200, AND the rate-limit cache
    columns should be refreshed from the new response headers.
    """
    stale_reset = int(time.time()) - 60
    claude_account = _make_account("claude-1", provider="claude")
    claude_account.status = AccountStatus.RATE_LIMITED
    claude_account.reset_at = stale_reset
    accounts_repo = _StubAccountsRepository([claude_account])
    usage_repo = _StubUsageRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    headers = {
        "anthropic-ratelimit-requests-remaining": "42",
        "anthropic-ratelimit-requests-reset": "2030-01-01T12:00:00Z",
        "anthropic-ratelimit-status": "allowed",
    }

    await balancer.record_claude_rate_limit_response(
        account=claude_account,
        headers=headers,
        is_rate_limited_response=False,
    )

    # A status update must have been written to clear the stale cooldown.
    assert len(accounts_repo.status_updates) == 1
    update = accounts_repo.status_updates[0]
    assert update["account_id"] == "claude-1"
    assert update["status"] == AccountStatus.ACTIVE

    # Rate-limit cache must be refreshed from the 200 response headers.
    assert len(accounts_repo.rate_limit_cache_writes) == 1
    fields = accounts_repo.rate_limit_cache_writes[0]["fields"]
    assert fields["rate_limit_requests_remaining"] == 42
    assert fields["rate_limit_status"] == "allowed"


@pytest.mark.asyncio
async def test_codex_429_uses_existing_codex_path() -> None:
    """Codex 429 must NOT touch the Claude-specific branch.

    The Claude branch is gated on ``provider == "claude"``. Codex
    requests that surface a 429 must follow the existing
    ``mark_rate_limit`` / ``handle_rate_limit`` path, which only writes
    ``status`` + ``reset_at`` (via the standard ``update_status`` call),
    and MUST NOT call the dedicated ``update_rate_limit_cache`` helper.
    """
    codex_account = _make_account("codex-1", provider="codex")
    accounts_repo = _StubAccountsRepository([codex_account])
    usage_repo = _StubUsageRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    # Drive the existing Codex-side bookkeeping path directly. The new
    # Claude helper exists but must NOT be invoked for Codex accounts.
    await balancer.mark_rate_limit(
        codex_account,
        cast(Any, {"message": "codex upstream 429", "resets_at": int(time.time()) + 30}),
    )

    # The Codex path goes through ``update_status`` (1 call) and persists
    # ONLY the rate-limit fields the production schema already supports
    # for Codex — the new Claude cache helper must not run.
    assert len(accounts_repo.status_updates) == 1
    assert accounts_repo.status_updates[0]["account_id"] == "codex-1"
    assert accounts_repo.status_updates[0]["status"] == AccountStatus.RATE_LIMITED
    assert accounts_repo.rate_limit_cache_writes == []
