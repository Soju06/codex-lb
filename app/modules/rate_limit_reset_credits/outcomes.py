"""Durable consume evidence. Callers hold the existing per-account serializer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import or_, select, update

from app.core.clients.rate_limit_reset_credits import ConsumeResetCreditResponse
from app.db.models import AuditLog, ResetCreditRedeemRequest
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits.redeem_coordination import redeem_credit_is_live

type RedeemOutcome = Literal["pending", "retryable", "unknown", "confirmed_reset", "no_reset", "expired"]


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def retry_at(now: datetime, expires_at: datetime | None, *, attempt_count: int = 0) -> datetime:
    delay = float(min(300, 30 * 2 ** min(attempt_count, 4)))
    if expires_at is not None:
        remaining = (utc(expires_at) - now).total_seconds()
        # Reserve half the remaining lifetime for the next network attempt.
        # Stop at the deadline instead of spinning through subsecond retries.
        if remaining <= 2:
            return max(now, utc(expires_at))
        delay = min(delay, remaining / 2)
    return now + timedelta(seconds=delay)


async def get_request(account_id: str, request_id: str) -> ResetCreditRedeemRequest | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(ResetCreditRedeemRequest).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.redeem_request_id == request_id,
                redeem_credit_is_live(datetime.now(UTC)),
            )
        )


async def find_credit_request(account_id: str, credit_id: str) -> ResetCreditRedeemRequest | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(ResetCreditRedeemRequest)
            .where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.credit_id == credit_id,
                redeem_credit_is_live(datetime.now(UTC)),
            )
            .order_by(
                (ResetCreditRedeemRequest.origin == "alias").asc(),
                ResetCreditRedeemRequest.created_at,
                ResetCreditRedeemRequest.redeem_request_id,
            )
            .limit(1)
        )


async def unresolved_requests() -> list[ResetCreditRedeemRequest]:
    async with SessionLocal() as session:
        return list(
            (
                await session.scalars(
                    select(ResetCreditRedeemRequest).where(
                        or_(
                            (ResetCreditRedeemRequest.origin == "automatic")
                            & (ResetCreditRedeemRequest.credit_expires_at > datetime.now(UTC))
                            & ResetCreditRedeemRequest.outcome.in_(["pending", "retryable", "unknown"]),
                            (ResetCreditRedeemRequest.outcome == "confirmed_reset")
                            & ResetCreditRedeemRequest.usage_verified.is_(False),
                        ),
                        redeem_credit_is_live(datetime.now(UTC)),
                    )
                )
            ).all()
        )


async def begin_attempt(account_id: str, request_id: str, *, expires_at: datetime | None, automatic: bool) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(ResetCreditRedeemRequest)
            .where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.redeem_request_id == request_id,
            )
            .values(
                origin="automatic" if automatic else "manual",
                credit_expires_at=expires_at,
                outcome="pending",
                attempt_count=ResetCreditRedeemRequest.attempt_count + 1,
                updated_at=datetime.now(UTC),
                next_retry_at=None,
            )
        )
        await session.commit()


def classify(result: ConsumeResetCreditResponse, credit_id: str) -> RedeemOutcome:
    if result.credit.id != credit_id:
        return "unknown"
    if result.credit.status == "redeemed":
        if result.code == "reset" and result.windows_reset > 0:
            return "confirmed_reset"
        if result.windows_reset == 0:
            return "no_reset"
    if result.credit.status == "expired":
        return "expired"
    return "unknown"


async def finish_attempt(
    account_id: str,
    request_id: str,
    outcome: RedeemOutcome,
    *,
    result: ConsumeResetCreditResponse | None = None,
    actor_ip: str | None = None,
) -> None:
    async with SessionLocal() as session:
        row = await session.get(ResetCreditRedeemRequest, (account_id, request_id))
        if row is None:
            raise RuntimeError("Reset-credit outcome has no durable request pin")
        if row.outcome == "confirmed_reset":
            return
        now = datetime.now(UTC)
        row.outcome = outcome
        row.updated_at = now
        if result is not None:
            row.upstream_code = result.code
            row.windows_reset = result.windows_reset
            row.redeemed_at = result.credit.redeemed_at
        row.next_retry_at = None
        if outcome in {"retryable", "unknown"}:
            row.next_retry_at = retry_at(now, row.credit_expires_at, attempt_count=row.attempt_count)
        session.add(
            AuditLog(
                action="account_rate_limit_reset_credit_consumed"
                if outcome == "confirmed_reset"
                else "account_reset_credit_attempt",
                actor_ip=actor_ip,
                request_id=request_id,
                details=json.dumps(
                    {
                        "account_id": account_id,
                        "credit_id": row.credit_id,
                        "credit_expires_at": utc(row.credit_expires_at).isoformat() if row.credit_expires_at else None,
                        "redeem_request_id": request_id,
                        "origin": row.origin,
                        "outcome": outcome,
                        "attempt_count": row.attempt_count,
                        "consume_code": row.upstream_code,
                        "windows_reset": row.windows_reset,
                        "usage_verified": row.usage_verified,
                    }
                ),
            )
        )
        await session.commit()


async def mark_usage_verified(account_id: str, request_id: str) -> None:
    async with SessionLocal() as session:
        changed = await session.scalar(
            update(ResetCreditRedeemRequest)
            .where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.redeem_request_id == request_id,
                ResetCreditRedeemRequest.usage_verified.is_(False),
            )
            .values(usage_verified=True)
            .returning(ResetCreditRedeemRequest.credit_id)
        )
        if changed is not None:
            session.add(
                AuditLog(
                    action="account_reset_credit_usage_verified",
                    request_id=request_id,
                    details=json.dumps(
                        {
                            "account_id": account_id,
                            "credit_id": changed,
                            "redeem_request_id": request_id,
                            "usage_verified": True,
                        }
                    ),
                )
            )
        await session.commit()
