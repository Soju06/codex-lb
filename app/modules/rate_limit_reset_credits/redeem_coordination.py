"""Cross-replica coordination primitives for reset-credit redemption.

Two shared-database primitives back the redeem path:

- A durable idempotency ledger (``reset_credit_redeem_requests``) that pins the
  (account_id, redeem_request_id) pair to the credit selected on the first
  attempt, so a retry served by ANY replica retargets the same credit instead
  of burning a second one.
- A per-account claim row (``reset_credit_redeem_claims``) that serializes
  redemption across processes sharing one SQLite file via a single atomic
  conditional upsert with a lease the holder renews on a heartbeat while the
  redeem section runs. PostgreSQL keeps ``pg_advisory_xact_lock``.

All statements run on dedicated short-lived sessions committed immediately;
they never join the caller's transaction.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import ResetCreditRedeemClaim, ResetCreditRedeemRequest
from app.db.session import SessionLocal, close_session

logger = logging.getLogger(__name__)

REDEEM_CLAIM_LEASE_SECONDS = 30.0
REDEEM_CLAIM_RENEW_INTERVAL_SECONDS = 10.0
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
        result = await session.execute(stmt.returning(ResetCreditRedeemClaim.account_id))
        await session.commit()
        return result.scalar_one_or_none() is not None
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


async def renew_redeem_claim(
    account_id: str,
    holder_id: str,
    *,
    lease_seconds: float = REDEEM_CLAIM_LEASE_SECONDS,
) -> bool:
    """Extend the holder's lease; False when the row is no longer held by this holder."""
    now = datetime.now(UTC)
    session = SessionLocal()
    try:
        result = await session.execute(
            update(ResetCreditRedeemClaim)
            .where(
                ResetCreditRedeemClaim.account_id == account_id,
                ResetCreditRedeemClaim.holder_id == holder_id,
            )
            .values(expires_at=now + timedelta(seconds=lease_seconds))
            .returning(ResetCreditRedeemClaim.account_id)
        )
        await session.commit()
        return result.scalar_one_or_none() is not None
    finally:
        await close_session(session)


async def renew_redeem_claim_periodically(
    account_id: str,
    holder_id: str,
    *,
    lease_seconds: float = REDEEM_CLAIM_LEASE_SECONDS,
    renew_interval_seconds: float = REDEEM_CLAIM_RENEW_INTERVAL_SECONDS,
) -> None:
    """Heartbeat that keeps a held claim's lease alive while the redeem section runs.

    Mirrors the scheduler-leader renew pattern: the holder extends
    ``expires_at`` every ``renew_interval_seconds`` (a fraction of the lease)
    so a legitimately slow redemption — usage fetch retries plus the upstream
    consume can exceed one lease — is not taken over by a second process.
    Transient renewal errors are logged and retried on the next tick; a renew
    reporting the row gone means an expired claim was already taken over, so
    the loop stops (exclusivity is lost and lease takeover semantics apply).
    The caller cancels this task before releasing the claim.
    """
    while True:
        await asyncio.sleep(renew_interval_seconds)
        try:
            renewed = await renew_redeem_claim(account_id, holder_id, lease_seconds=lease_seconds)
        except Exception:
            logger.warning(
                "reset-credit redeem claim renewal failed account_id=%s (retrying next tick)",
                account_id,
                exc_info=True,
            )
            continue
        if not renewed:
            logger.warning(
                "reset-credit redeem claim lost before renewal account_id=%s holder_id=%s",
                account_id,
                holder_id,
            )
            return


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
    """Read the credit pinned to this redeem_request_id by any replica.

    Rows older than the 24h TTL are ignored (the read TTL matches the purge
    TTL applied by ``pin_redeem_request``), so an expired pin reads as absent.
    The caller then re-selects a fresh credit and re-pins it via
    ``pin_redeem_request`` instead of forwarding the redemption for a stale
    credit id that the purge path would otherwise drop.
    """
    now = datetime.now(UTC)
    session = SessionLocal()
    try:
        return await session.scalar(
            select(ResetCreditRedeemRequest.credit_id).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.redeem_request_id == redeem_request_id,
                ResetCreditRedeemRequest.created_at >= now - REDEEM_REQUEST_TTL,
            )
        )
    finally:
        await close_session(session)


async def pin_redeem_request(account_id: str, redeem_request_id: str, credit_id: str) -> str:
    """Durably pin the selected credit to this redeem request; first writer wins.

    Returns the authoritative credit id (the previously pinned one on
    conflict). Rows older than the 24h TTL for this account are purged in the
    same transaction BEFORE the insert, so a ``redeem_request_id`` reused after
    its prior row has aged past the TTL is re-pinned to the new credit instead
    of colliding with the stale row via ``ON CONFLICT DO NOTHING`` and losing
    the new pin.
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
        # Purge expired rows first: an expired row for the SAME
        # (account_id, redeem_request_id) would otherwise absorb the insert
        # below via ON CONFLICT DO NOTHING and then be deleted, leaving the new
        # attempt with no durable pin.
        await session.execute(
            delete(ResetCreditRedeemRequest).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.created_at < now - REDEEM_REQUEST_TTL,
            )
        )
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
