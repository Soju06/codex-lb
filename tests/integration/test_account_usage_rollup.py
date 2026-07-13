from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountUsageRollup, AccountUsageRollupState
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.usage_rollup import FOLD_LAG, run_fold_pass
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _make_account(
    account_id: str, email: str, plan_type: str = "plus", chatgpt_account_id: str | None = None
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        chatgpt_account_id=chatgpt_account_id,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _add_log(
    logs_repo: RequestLogsRepository,
    *,
    account_id: str,
    request_id: str,
    requested_at,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cached_input_tokens: int = 0,
    cost_usd: float | None = 0.01,
    request_kind: str = "normal",
):
    return await logs_repo.add_log(
        account_id=account_id,
        request_id=request_id,
        model="gpt-5.1-codex",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        latency_ms=100,
        status="success",
        error_code=None,
        requested_at=requested_at,
        cost_usd=cost_usd,
        request_kind=request_kind,
    )


async def _summaries(account_ids: list[str] | None = None):
    async with SessionLocal() as session:
        return await AccountsRepository(session).list_request_usage_summary_by_account(account_ids)


async def _rollup_rows():
    async with SessionLocal() as session:
        return (await session.execute(select(AccountUsageRollup))).scalars().all()


async def _watermark():
    async with SessionLocal() as session:
        return (
            await session.execute(select(AccountUsageRollupState.folded_through).where(AccountUsageRollupState.id == 1))
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_fold_preserves_summary_totals_and_dedupe(db_setup):
    now = utcnow()
    old = now - timedelta(days=2)
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_fold", "fold@example.com"))
        await _add_log(logs_repo, account_id="acc_fold", request_id="req_1", requested_at=old, input_tokens=1000)
        # Duplicate rows sharing (account_id, request_id, requested_at): only
        # the latest id may count (issue #904 semantics).
        dup_at = old + timedelta(minutes=5)
        await _add_log(
            logs_repo, account_id="acc_fold", request_id="req_dup", requested_at=dup_at, input_tokens=999_999
        )
        await _add_log(logs_repo, account_id="acc_fold", request_id="req_dup", requested_at=dup_at, input_tokens=200)
        # Warmup rows are excluded from summaries entirely.
        await _add_log(
            logs_repo,
            account_id="acc_fold",
            request_id="req_warm",
            requested_at=old,
            request_kind="warmup",
            input_tokens=777,
        )
        # Recent row stays on the live-tail side of the fold boundary.
        await _add_log(logs_repo, account_id="acc_fold", request_id="req_new", requested_at=now, input_tokens=10)

    before = await _summaries()
    assert before["acc_fold"].request_count == 3
    assert before["acc_fold"].total_tokens == 1000 + 200 + 10 + 3 * 50

    folded_slices = await run_fold_pass(now=now)
    assert folded_slices >= 1
    rows = await _rollup_rows()
    assert len(rows) == 1
    assert rows[0].account_id == "acc_fold"
    assert rows[0].request_count == 2
    assert rows[0].input_tokens == 1000 + 200
    assert await _watermark() <= now - FOLD_LAG

    after = await _summaries()
    assert after == before


@pytest.mark.asyncio
async def test_fold_respects_safety_lag(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_lag", "lag@example.com"))
        await _add_log(
            logs_repo,
            account_id="acc_lag",
            request_id="req_old",
            requested_at=now - timedelta(days=2),
        )
        await _add_log(
            logs_repo,
            account_id="acc_lag",
            request_id="req_young",
            requested_at=now - timedelta(minutes=10),
        )

    await run_fold_pass(now=now)
    rows = await _rollup_rows()
    assert len(rows) == 1
    assert rows[0].request_count == 1
    assert await _watermark() <= now - FOLD_LAG

    summaries = await _summaries()
    assert summaries["acc_lag"].request_count == 2


@pytest.mark.asyncio
async def test_fold_pass_is_idempotent(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_idem", "idem@example.com"))
        await _add_log(
            logs_repo,
            account_id="acc_idem",
            request_id="req_1",
            requested_at=now - timedelta(days=2),
            input_tokens=500,
        )

    assert await run_fold_pass(now=now) >= 1
    assert await run_fold_pass(now=now) == 0

    rows = await _rollup_rows()
    assert len(rows) == 1
    assert rows[0].request_count == 1
    assert rows[0].input_tokens == 500


@pytest.mark.asyncio
async def test_backfill_slices_span_long_history(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_hist", "hist@example.com"))
        for index, days_ago in enumerate((30, 16, 2)):
            await _add_log(
                logs_repo,
                account_id="acc_hist",
                request_id=f"req_{index}",
                requested_at=now - timedelta(days=days_ago),
                input_tokens=100,
            )

    committed = await run_fold_pass(now=now)
    assert committed >= 2  # 30-day span cannot fit one 7-day slice

    rows = await _rollup_rows()
    assert len(rows) == 1
    assert rows[0].request_count == 3

    summaries = await _summaries()
    assert summaries["acc_hist"].request_count == 3
    assert summaries["acc_hist"].total_tokens == 3 * 150


@pytest.mark.asyncio
async def test_concurrent_fold_passes_do_not_double_count(db_setup):
    """Two fold passes racing on an empty rollup table (first backfill, e.g.
    two replicas starting after the migration) must not fold the same window
    twice; the state-row lock serializes them."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_race", "race@example.com"))
        for index in range(3):
            await _add_log(
                logs_repo,
                account_id="acc_race",
                request_id=f"req_{index}",
                requested_at=now - timedelta(days=2, minutes=index),
                input_tokens=100,
            )

    await asyncio.gather(run_fold_pass(now=now), run_fold_pass(now=now))

    rows = await _rollup_rows()
    assert len(rows) == 1
    assert rows[0].request_count == 3
    assert rows[0].input_tokens == 300

    summaries = await _summaries()
    assert summaries["acc_race"].request_count == 3
    assert summaries["acc_race"].total_tokens == 3 * 150


@pytest.mark.asyncio
async def test_identity_merge_preserves_folded_usage(db_setup):
    """Consolidating a duplicate account into the canonical one reassigns its
    request logs; folded rollup sums must follow, or pre-watermark history
    vanishes from the canonical account's lifetime totals."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        canonical = _make_account("acc_canon", "merge@example.com", chatgpt_account_id="chatgpt_merge")
        duplicate = _make_account("acc_canon__copy", "merge@example.com", chatgpt_account_id="chatgpt_merge")
        await accounts_repo.upsert(canonical, merge_by_email=False)
        await accounts_repo.upsert(duplicate, merge_by_email=False)
        await _add_log(
            logs_repo,
            account_id="acc_canon",
            request_id="req_canon",
            requested_at=now - timedelta(days=2),
            input_tokens=1_000,
        )
        await _add_log(
            logs_repo,
            account_id="acc_canon__copy",
            request_id="req_copy",
            requested_at=now - timedelta(days=2),
            input_tokens=500,
        )

    await run_fold_pass(now=now)
    assert len(await _rollup_rows()) == 2

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        reauth = _make_account("acc_canon", "merge@example.com", chatgpt_account_id="chatgpt_merge")
        saved = await accounts_repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)
        assert saved.id == "acc_canon"

    rows = await _rollup_rows()
    assert len(rows) == 1
    assert rows[0].account_id == "acc_canon"
    assert rows[0].request_count == 2
    assert rows[0].input_tokens == 1_500

    summaries = await _summaries()
    assert "acc_canon__copy" not in summaries
    assert summaries["acc_canon"].request_count == 2
    assert summaries["acc_canon"].total_tokens == 1_500 + 2 * 50


@pytest.mark.asyncio
async def test_identity_merge_racing_fold_loses_no_usage(db_setup):
    """Consolidation and a concurrent fold must serialize on the fold-state
    lock; whichever order they land in, every request-log row's contribution
    must end up in the canonical account's totals."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        canonical = _make_account("acc_race_m", "race-m@example.com", chatgpt_account_id="chatgpt_race_m")
        duplicate = _make_account("acc_race_m__copy", "race-m@example.com", chatgpt_account_id="chatgpt_race_m")
        await accounts_repo.upsert(canonical, merge_by_email=False)
        await accounts_repo.upsert(duplicate, merge_by_email=False)
        for index in range(4):
            await _add_log(
                logs_repo,
                account_id="acc_race_m__copy",
                request_id=f"req_race_m_{index}",
                requested_at=now - timedelta(days=2, minutes=index),
                input_tokens=250,
            )

    async def _merge():
        async with SessionLocal() as session:
            reauth = _make_account("acc_race_m", "race-m@example.com", chatgpt_account_id="chatgpt_race_m")
            await AccountsRepository(session).upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

    await asyncio.gather(run_fold_pass(now=now), _merge())
    # A second fold covers the ordering where the merge landed first.
    await run_fold_pass(now=now)

    summaries = await _summaries()
    assert "acc_race_m__copy" not in summaries
    assert summaries["acc_race_m"].request_count == 4
    assert summaries["acc_race_m"].total_tokens == 4 * 300


@pytest.mark.asyncio
async def test_accounts_api_request_usage_unchanged_by_folding(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_api", "api@example.com"))
        await _add_log(
            logs_repo,
            account_id="acc_api",
            request_id="req_old",
            requested_at=now - timedelta(days=3),
            input_tokens=1_000,
            output_tokens=200,
            cached_input_tokens=400,
            cost_usd=0.25,
        )
        dup_at = now - timedelta(days=2)
        await _add_log(logs_repo, account_id="acc_api", request_id="req_dup", requested_at=dup_at, input_tokens=555_555)
        await _add_log(logs_repo, account_id="acc_api", request_id="req_dup", requested_at=dup_at, input_tokens=300)
        await _add_log(logs_repo, account_id="acc_api", request_id="req_new", requested_at=now, input_tokens=20)

    async def _api_request_usage():
        response = await async_client.get("/api/accounts")
        assert response.status_code == 200
        accounts = {item["accountId"]: item for item in response.json()["accounts"]}
        return accounts["acc_api"]["requestUsage"]

    before = await _api_request_usage()
    assert before["requestCount"] == 3
    assert before["totalTokens"] == 1_000 + 300 + 20 + 200 + 2 * 50
    assert before["cachedInputTokens"] == 400

    await run_fold_pass(now=now)
    assert len(await _rollup_rows()) == 1

    assert await _api_request_usage() == before


@pytest.mark.asyncio
async def test_account_delete_removes_rollup_row(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_del", "del@example.com"))
        await _add_log(
            logs_repo,
            account_id="acc_del",
            request_id="req_1",
            requested_at=now - timedelta(days=2),
        )

    await run_fold_pass(now=now)
    assert len(await _rollup_rows()) == 1

    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_del")
    assert await _rollup_rows() == []
    assert "acc_del" not in await _summaries()

    # History-deleting variant as well. The watermark already advanced to
    # now - FOLD_LAG above, so the new account's log must be younger than
    # that and the second fold must run with a later `now` to fold it.
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_del2", "del2@example.com"))
        await _add_log(
            logs_repo,
            account_id="acc_del2",
            request_id="req_2",
            requested_at=now - timedelta(hours=12),
        )
    await run_fold_pass(now=now + timedelta(days=1))
    assert len(await _rollup_rows()) == 1
    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_del2", delete_history=True)
    assert await _rollup_rows() == []


@pytest.mark.asyncio
async def test_backfill_start_skips_excluded_prefix(db_setup):
    """Years of soft-deleted/warmup-only history before the first countable
    row must not anchor the backfill start: the pass folds in one slice
    instead of walking empty windows while holding the fold-state lock."""
    from app.db.models import RequestLog as RequestLogModel

    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_prefix", "prefix@example.com"))
        # Excluded prefix: a warmup row and a soft-deleted account-less row,
        # both years before any countable history.
        await _add_log(
            logs_repo,
            account_id="acc_prefix",
            request_id="req_warm_old",
            requested_at=now - timedelta(days=900),
            request_kind="warmup",
        )
        orphan = RequestLogModel(
            account_id=None,
            request_id="req_orphan_old",
            model="gpt-5.1-codex",
            status="success",
            requested_at=now - timedelta(days=800),
            deleted_at=now - timedelta(days=700),
        )
        session.add(orphan)
        await session.commit()
        # Countable history spans well under one fold slice.
        await _add_log(logs_repo, account_id="acc_prefix", request_id="req_new", requested_at=now - timedelta(days=2))

    committed = await run_fold_pass(now=now)
    assert committed == 1  # one slice, not ~115 empty 7-day windows

    summaries = await _summaries()
    assert summaries["acc_prefix"].request_count == 1
