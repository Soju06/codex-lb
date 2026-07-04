"""Tests for ``AccountsRepository.update_rate_limit_cache``.

Phase 9 (Task 9.0) of the Claude OAuth pool. The Claude cooldown branch in
``LoadBalancer.record_claude_rate_limit_response`` writes parsed
``anthropic-ratelimit-*`` headers through this helper, so the partial-update
contract MUST hold: passing a single key leaves the other rate-limit columns
untouched on the row.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/account-routing/spec.md``
— *Claude rate-limit cooldown mirrors Codex cooldown*.

The repo writes through a real SQLAlchemy async session against the shared
test schema (``tests/conftest.py::_reset_db_state``); partial-update assertions
read the row back through ``AccountsRepository.get_by_id`` so the SQLite
``UPDATE ... WHERE id = ?`` round-trip is exercised end-to-end.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.unit


def _make_account(account_id: str) -> Account:
    """Build a Claude-flavored Account row with all required NOT-NULL columns
    populated so the CHECK constraint on ``claude_refresh_token_encrypted``
    accepts the insert.
    """
    return Account(
        id=account_id,
        provider="claude",
        plan_type="claude_subscription",
        routing_policy="normal",
        access_token_encrypted=b"placeholder-at",
        refresh_token_encrypted=b"placeholder-rt",
        id_token_encrypted=b"placeholder-it",
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        # Claude columns — refresh_token_encrypted is required by the CHECK
        # constraint when provider='claude'.
        claude_account_uuid=account_id.removeprefix("claude-"),
        claude_refresh_token_encrypted=b"placeholder-rt-claude",
        claude_access_token_encrypted=b"placeholder-at-claude",
        claude_access_token_expires_at=datetime.now(tz=timezone.utc),
    )


def _as_utc_epoch(value: datetime) -> int:
    """Compare datetimes ignoring whether the ORM round-trip preserved tzinfo.

    SQLite (the test backend) drops tzinfo on read; if the value is naive we
    assume the canonical UTC clock was stored. Production paths always
    pass tz-aware datetimes, so the prod contract is unaffected.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


async def _insert(account_id: str) -> None:
    async with SessionLocal() as session:
        session.add(_make_account(account_id))
        await session.commit()


async def _fetch(account_id: str) -> Account | None:
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        return await repo.get_by_id(account_id)


async def test_update_rate_limit_cache_partial_update_does_not_overwrite_other_columns(
    db_setup: None,
) -> None:
    """A single-field update must NOT clobber the other rate-limit columns.

    Seed the row with all 7 rate-limit columns populated; call the helper with
    just ``rate_limit_requests_remaining``; the other six columns must
    survive unchanged.
    """
    account_id = "claude-cache-partial"
    await _insert(account_id)

    seeded_reset = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    seeded_epoch = _as_utc_epoch(seeded_reset)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.update_rate_limit_cache(
            account_id,
            {
                "rate_limit_requests_remaining": 7,
                "rate_limit_requests_reset_at": seeded_reset,
                "rate_limit_input_tokens_remaining": 100,
                "rate_limit_input_tokens_reset_at": seeded_reset,
                "rate_limit_output_tokens_remaining": 200,
                "rate_limit_output_tokens_reset_at": seeded_reset,
                "rate_limit_status": "allowed",
            },
        )

    # Update only one column.
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.update_rate_limit_cache(
            account_id,
            {"rate_limit_requests_remaining": 99},
        )

    row = await _fetch(account_id)
    assert row is not None
    # Updated field reflects the new value.
    assert row.rate_limit_requests_remaining == 99
    # Other columns untouched. SQLite strips tzinfo on read; compare epoch.
    assert _as_utc_epoch(row.rate_limit_requests_reset_at) == seeded_epoch  # ty:ignore[invalid-argument-type]
    assert row.rate_limit_input_tokens_remaining == 100
    assert _as_utc_epoch(row.rate_limit_input_tokens_reset_at) == seeded_epoch  # ty:ignore[invalid-argument-type]
    assert row.rate_limit_output_tokens_remaining == 200
    assert _as_utc_epoch(row.rate_limit_output_tokens_reset_at) == seeded_epoch  # ty:ignore[invalid-argument-type]
    assert row.rate_limit_status == "allowed"


async def test_update_rate_limit_cache_empty_fields_is_noop(db_setup: None) -> None:
    """Calling the helper with an empty dict MUST NOT issue a write.

    Phase 8's Claude cooldown path already guards on ``if parsed:``; this
    test pins the symmetric guarantee on the repo side so a caller that
    forwards an empty parsed dict does not write a row.
    """
    account_id = "claude-cache-empty"
    await _insert(account_id)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.update_rate_limit_cache(account_id, {})

    row = await _fetch(account_id)
    assert row is not None
    # All seven columns stay NULL (the seeded row never set them).
    assert row.rate_limit_requests_remaining is None
    assert row.rate_limit_requests_reset_at is None
    assert row.rate_limit_input_tokens_remaining is None
    assert row.rate_limit_input_tokens_reset_at is None
    assert row.rate_limit_output_tokens_remaining is None
    assert row.rate_limit_output_tokens_reset_at is None
    assert row.rate_limit_status is None


async def test_update_rate_limit_cache_writes_all_columns(db_setup: None) -> None:
    """Full update: all seven columns land on the row."""
    account_id = "claude-cache-full"
    await _insert(account_id)

    seeded_reset = datetime(2030, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    seeded_epoch = _as_utc_epoch(seeded_reset)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.update_rate_limit_cache(
            account_id,
            {
                "rate_limit_requests_remaining": 1,
                "rate_limit_requests_reset_at": seeded_reset,
                "rate_limit_input_tokens_remaining": 2,
                "rate_limit_input_tokens_reset_at": seeded_reset,
                "rate_limit_output_tokens_remaining": 3,
                "rate_limit_output_tokens_reset_at": seeded_reset,
                "rate_limit_status": "rejected",
            },
        )

    row = await _fetch(account_id)
    assert row is not None
    assert row.rate_limit_requests_remaining == 1
    assert _as_utc_epoch(row.rate_limit_requests_reset_at) == seeded_epoch  # ty:ignore[invalid-argument-type]
    assert row.rate_limit_input_tokens_remaining == 2
    assert _as_utc_epoch(row.rate_limit_input_tokens_reset_at) == seeded_epoch  # ty:ignore[invalid-argument-type]
    assert row.rate_limit_output_tokens_remaining == 3
    assert _as_utc_epoch(row.rate_limit_output_tokens_reset_at) == seeded_epoch  # ty:ignore[invalid-argument-type]
    assert row.rate_limit_status == "rejected"
