from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import OAuthFlowState

_TERMINAL_OAUTH_STATUSES = {"error", "success"}


def epoch_to_naive_utc(value: float | None) -> datetime | None:
    """Convert an epoch-seconds float (as used by the in-memory store) to the
    naive-UTC datetime the rest of the schema stores."""

    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)


@dataclass(slots=True)
class OAuthFlowRecord:
    """Durable representation of a dashboard OAuth flow.

    ``code_verifier`` is held in plaintext in memory only; the repository
    encrypts it at rest and decrypts it on read.
    """

    flow_id: str
    method: str
    status: str
    state_token: str | None = None
    error_message: str | None = None
    intended_account_id: str | None = None
    code_verifier: str | None = None
    device_auth_id: str | None = None
    user_code: str | None = None
    interval_seconds: int | None = None
    expires_at: datetime | None = None
    finished_at: datetime | None = None


class OAuthFlowRepository:
    """DB access for the shared ``oauth_flow_states`` table."""

    def __init__(self, session: AsyncSession, encryptor: TokenEncryptor) -> None:
        self._session = session
        self._encryptor = encryptor

    def _to_record(self, row: OAuthFlowState) -> OAuthFlowRecord:
        verifier: str | None = None
        if row.code_verifier_encrypted is not None:
            verifier = self._encryptor.decrypt(row.code_verifier_encrypted)
        return OAuthFlowRecord(
            flow_id=row.flow_id,
            method=row.method,
            status=row.status,
            state_token=row.state_token,
            error_message=row.error_message,
            intended_account_id=row.intended_account_id,
            code_verifier=verifier,
            device_auth_id=row.device_auth_id,
            user_code=row.user_code,
            interval_seconds=row.interval_seconds,
            expires_at=row.expires_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _is_expired_pending(row: OAuthFlowState, now: datetime) -> bool:
        if row.status != "pending" or row.expires_at is None:
            return False
        # ``expires_at`` is ``DateTime(timezone=True)``: on PostgreSQL asyncpg
        # returns an offset-AWARE datetime, while ``now`` (``utcnow``) is naive
        # UTC. Normalize the row value to naive UTC before comparing so the
        # comparison never raises ``TypeError`` for offset-naive vs -aware.
        return to_utc_naive(row.expires_at) <= now

    async def create(self, record: OAuthFlowRecord) -> None:
        encrypted = None
        if record.code_verifier is not None:
            encrypted = self._encryptor.encrypt(record.code_verifier)
        row = OAuthFlowState(
            flow_id=record.flow_id,
            state_token=record.state_token,
            method=record.method,
            status=record.status,
            error_message=record.error_message,
            intended_account_id=record.intended_account_id,
            code_verifier_encrypted=encrypted,
            device_auth_id=record.device_auth_id,
            user_code=record.user_code,
            interval_seconds=record.interval_seconds,
            expires_at=record.expires_at,
            created_at=utcnow(),
            finished_at=record.finished_at,
        )
        self._session.add(row)
        await self._session.commit()

    async def get_by_flow_id(self, flow_id: str) -> OAuthFlowRecord | None:
        row = await self._session.get(OAuthFlowState, flow_id)
        if row is None or self._is_expired_pending(row, utcnow()):
            return None
        return self._to_record(row)

    async def get_by_state_token(self, state_token: str) -> OAuthFlowRecord | None:
        result = await self._session.execute(
            select(OAuthFlowState).where(OAuthFlowState.state_token == state_token).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None or self._is_expired_pending(row, utcnow()):
            return None
        return self._to_record(row)

    async def set_status(
        self,
        flow_id: str,
        *,
        status: str,
        error_message: str | None,
    ) -> bool:
        # Terminal writes are monotonic and enforced ATOMICALLY in SQL so the
        # guard holds under real cross-session/cross-replica concurrency. A
        # durable ``success`` is sticky: only a ``success`` write may land on an
        # already-``success`` row (idempotent), and any non-success write is
        # rejected by the ``WHERE`` predicate. This closes the read-then-write
        # TOCTOU race where two pollers both load a still-``pending`` row and the
        # later ``error`` writer would otherwise clobber a committed ``success``
        # (e.g. a losing/duplicate device poller receiving an OAuth error for the
        # now-consumed device code). ``pending -> terminal`` and
        # ``error -> success`` remain allowed.
        finished_at = utcnow() if status in _TERMINAL_OAUTH_STATUSES else None
        statement = update(OAuthFlowState).where(OAuthFlowState.flow_id == flow_id)
        if status != "success":
            statement = statement.where(OAuthFlowState.status != "success")
        statement = statement.values(
            status=status, error_message=error_message, finished_at=finished_at
        ).execution_options(synchronize_session=False)
        result = cast(CursorResult[Any], await self._session.execute(statement))
        await self._session.commit()
        return int(result.rowcount or 0) > 0

    async def delete_pending_device(self) -> list[str]:
        result = await self._session.execute(
            select(OAuthFlowState.flow_id).where(OAuthFlowState.method == "device", OAuthFlowState.status == "pending")
        )
        flow_ids = [row[0] for row in result.all()]
        if flow_ids:
            await self._session.execute(delete(OAuthFlowState).where(OAuthFlowState.flow_id.in_(flow_ids)))
            await self._session.commit()
        return flow_ids

    async def purge_expired(self, *, terminal_keep: int) -> None:
        now = utcnow()
        await self._session.execute(
            delete(OAuthFlowState).where(
                OAuthFlowState.status == "pending",
                OAuthFlowState.expires_at.is_not(None),
                OAuthFlowState.expires_at <= now,
            )
        )
        result = await self._session.execute(
            select(OAuthFlowState.flow_id)
            .where(OAuthFlowState.status.in_(tuple(_TERMINAL_OAUTH_STATUSES)))
            .order_by(OAuthFlowState.finished_at.desc())
            .offset(terminal_keep)
        )
        stale_terminal = [row[0] for row in result.all()]
        if stale_terminal:
            await self._session.execute(delete(OAuthFlowState).where(OAuthFlowState.flow_id.in_(stale_terminal)))
        await self._session.commit()
