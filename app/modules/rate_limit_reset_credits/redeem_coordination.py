"""Cross-replica coordination primitives for reset-credit redemption.

Two shared-database primitives back the redeem path:

- A durable idempotency ledger (``reset_credit_redeem_requests``) that pins the
  (account_id, redeem_request_id) pair to the credit selected on the first
  attempt, so a retry served by ANY replica retargets the same credit instead
  of burning a second one.
- A per-account claim row (``reset_credit_redeem_claims``) that serializes
  redemption across processes sharing one SQLite file via a single atomic
  conditional upsert with a lease. PostgreSQL keeps ``pg_advisory_xact_lock``.

All statements run on dedicated short-lived sessions committed immediately;
they never join the caller's transaction.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import ResetCreditRedeemClaim, ResetCreditRedeemRequest
from app.db.session import SessionLocal, close_session

logger = logging.getLogger(__name__)

REDEEM_CLAIM_LEASE_SECONDS = 30.0
REDEEM_CLAIM_RETRY_INTERVAL_SECONDS = 0.1
REDEEM_CLAIM_TIMEOUT_SECONDS = 15.0
REDEEM_REQUEST_TTL = timedelta(hours=24)


class RedeemClaimTimeoutError(Exception):
    """The per-account redeem claim stayed held past the acquisition timeout."""


def new_redeem_claim_holder_id() -> str:
    return uuid.uuid4().hex


async def try_acquire_redeem_claim(
    account_id: str,
    holder_id: str,
    *,
    lease_seconds: float = REDEEM_CLAIM_LEASE_SECONDS,
) -> bool:
    """Attempt to claim the per-account redeem slot; True when claimed.

    Uses one atomic ``INSERT ... ON CONFLICT(account_id) DO UPDATE ... WHERE
    expires_at < now`` so only a missing or lease-expired claim can be taken —
    the same conditional-upsert shape as the scheduler-leader lease.
    """
    now = datetime.now(UTC)
    session = SessionLocal()
    try:
        insert_stmt = sqlite_insert(ResetCreditRedeemClaim).values(
            account_id=account_id,
            holder_id=holder_id,
            expires_at=now + timedelta(seconds=lease_seconds),
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[ResetCreditRedeemClaim.account_id],
            set_={
                "holder_id": insert_stmt.excluded.holder_id,
                "expires_at": insert_stmt.excluded.expires_at,
            },
            where=ResetCreditRedeemClaim.expires_at < now,
        )
        result = await session.execute(stmt)
        await session.commit()
        return bool(result.rowcount)
    finally:
        await close_session(session)


async def acquire_redeem_claim(
    account_id: str,
    holder_id: str,
    *,
    lease_seconds: float = REDEEM_CLAIM_LEASE_SECONDS,
    retry_interval_seconds: float = REDEEM_CLAIM_RETRY_INTERVAL_SECONDS,
    timeout_seconds: float = REDEEM_CLAIM_TIMEOUT_SECONDS,
) -> None:
    """Acquire the per-account redeem claim, retrying until the timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        if await try_acquire_redeem_claim(account_id, holder_id, lease_seconds=lease_seconds):
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RedeemClaimTimeoutError(
                f"reset-credit redeem claim for account {account_id} not acquired within {timeout_seconds}s"
            )
        await asyncio.sleep(min(retry_interval_seconds, remaining))


async def release_redeem_claim(account_id: str, holder_id: str) -> None:
    """Release the claim if this holder still owns it (lease expiry is the backstop)."""
    session = SessionLocal()
    try:
        await session.execute(
            delete(ResetCreditRedeemClaim).where(
                ResetCreditRedeemClaim.account_id == account_id,
                ResetCreditRedeemClaim.holder_id == holder_id,
            )
        )
        await session.commit()
    except Exception:
        logger.warning(
            "reset-credit redeem claim release failed account_id=%s (lease expiry will recover)",
            account_id,
            exc_info=True,
        )
    finally:
        await close_session(session)


async def get_pinned_redeem_credit_id(account_id: str, redeem_request_id: str) -> str | None:
    """Read the credit pinned to this redeem_request_id by any replica."""
    session = SessionLocal()
    try:
        return await session.scalar(
            select(ResetCreditRedeemRequest.credit_id).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.redeem_request_id == redeem_request_id,
            )
        )
    finally:
        await close_session(session)


async def pin_redeem_request(account_id: str, redeem_request_id: str, credit_id: str) -> str:
    """Durably pin the selected credit to this redeem request; first writer wins.

    Returns the authoritative credit id (the previously pinned one on
    conflict). Rows older than the 24h TTL for this account are purged in the
    same transaction.
    """
    now = datetime.now(UTC)
    session = SessionLocal()
    try:
        values = {
            "account_id": account_id,
            "redeem_request_id": redeem_request_id,
            "credit_id": credit_id,
            "created_at": now,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            await session.execute(
                pg_insert(ResetCreditRedeemRequest)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        ResetCreditRedeemRequest.account_id,
                        ResetCreditRedeemRequest.redeem_request_id,
                    ]
                )
            )
        else:
            await session.execute(
                sqlite_insert(ResetCreditRedeemRequest)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        ResetCreditRedeemRequest.account_id,
                        ResetCreditRedeemRequest.redeem_request_id,
                    ]
                )
            )
        await session.execute(
            delete(ResetCreditRedeemRequest).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.created_at < now - REDEEM_REQUEST_TTL,
            )
        )
        await session.commit()
        stored = await session.scalar(
            select(ResetCreditRedeemRequest.credit_id).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.redeem_request_id == redeem_request_id,
            )
        )
        return stored if stored is not None else credit_id
    finally:
        await close_session(session)
