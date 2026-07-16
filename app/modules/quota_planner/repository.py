from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, Integer, and_, cast, func, literal, or_, select, text, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import (
    Account,
    QuotaPlannerDecision,
    QuotaPlannerSettings,
    QuotaWindowObservation,
    RequestLog,
)
from app.db.session import sqlite_writer_section
from app.modules.quota_planner.logic import PlannerSettings, encode_working_days, parse_working_days

_SETTINGS_ID = 1


def _to_db_naive_utc(value: datetime | None) -> datetime | None:
    """Normalize a decision datetime for the timezone-naive DB columns.

    ``QuotaPlannerDecision.scheduled_at`` / ``executed_at`` are timezone-naive
    ``DateTime`` columns. Persisting timezone-aware instants into them fails on
    Postgres/asyncpg ("can't subtract offset-naive and offset-aware datetimes").
    Aware values are converted to UTC and stripped of ``tzinfo`` so the absolute
    instant is preserved; naive values are returned unchanged.
    """

    if value is None:
        return None
    return to_utc_naive(value)


@dataclass(frozen=True, slots=True)
class DemandBin:
    slot_epoch: int
    account_id: str | None
    api_key_id: str | None
    model: str
    reasoning_effort: str | None
    request_kind: str
    status: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float
    request_count: int


_WARMUP_BUDGET_LOCK_KEY = "quota_planner:warmup_budget"


def _active_warmup_budget_clause(since: datetime) -> ColumnElement[bool]:
    """Filter warmup decisions consuming the daily count budget since ``since``.

    ``executed`` decisions count by ``executed_at`` (completion time).
    In-flight ``executing`` decisions count by the claim timestamp that
    ``claim_warmup_decision`` stamps into ``executed_at`` at the
    planned -> executing transition — not by ``created_at``, because the
    scheduler persists future-scheduled decisions whose ``created_at`` can
    precede the daily boundary; a decision planned before midnight but claimed
    after midnight must consume the claim day's budget. Executing rows without
    a claim timestamp (claimed by a version that predates claim stamping) fall
    back to ``created_at``.
    """
    return and_(
        QuotaPlannerDecision.action == "warmup",
        or_(
            and_(
                QuotaPlannerDecision.status == "executed",
                QuotaPlannerDecision.executed_at >= since,
            ),
            and_(
                QuotaPlannerDecision.status == "executing",
                or_(
                    QuotaPlannerDecision.executed_at >= since,
                    and_(
                        QuotaPlannerDecision.executed_at.is_(None),
                        QuotaPlannerDecision.created_at >= since,
                    ),
                ),
            ),
        ),
    )


class QuotaPlannerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _dialect_name(self) -> str:
        bind = self._session.get_bind()
        return bind.dialect.name if bind else "sqlite"

    async def get_settings(self) -> PlannerSettings:
        row = await self._session.get(QuotaPlannerSettings, _SETTINGS_ID)
        if row is None:
            return PlannerSettings()
        return _settings_from_row(row)

    async def upsert_settings(self, settings: PlannerSettings) -> PlannerSettings:
        row = await self._session.get(QuotaPlannerSettings, _SETTINGS_ID)
        if row is None:
            row = QuotaPlannerSettings(id=_SETTINGS_ID)
            self._session.add(row)
        row.mode = settings.mode
        row.timezone = settings.timezone
        row.working_days_json = encode_working_days(settings.working_days)
        row.working_hours_start = settings.working_hours_start
        row.working_hours_end = settings.working_hours_end
        row.prewarm_enabled = settings.prewarm_enabled
        row.prewarm_lead_minutes = settings.prewarm_lead_minutes
        row.max_warmups_per_day = settings.max_warmups_per_day
        row.max_warmup_credits_per_day = settings.max_warmup_credits_per_day
        row.min_expected_gain = settings.min_expected_gain
        row.forecast_quantile = settings.forecast_quantile
        row.allow_synthetic_traffic = settings.allow_synthetic_traffic
        row.warmup_model_preference = settings.warmup_model_preference
        row.dry_run = settings.dry_run
        async with sqlite_writer_section():
            await self._session.commit()
            await self._session.refresh(row)
        return _settings_from_row(row)

    async def list_accounts(self) -> list[Account]:
        result = await self._session.execute(select(Account))
        return list(result.scalars().all())

    async def log_decision(
        self,
        *,
        mode: str,
        action: str,
        idempotency_key: str,
        account_id: str | None = None,
        scheduled_at: datetime | None = None,
        score: float = 0.0,
        reason: str | None = None,
        forecast_snapshot_hash: str | None = None,
        state_before_json: str | None = None,
        state_after_json: str | None = None,
        status: str = "planned",
        executed_at: datetime | None = None,
    ) -> QuotaPlannerDecision:
        values = {
            "mode": mode,
            "action": action,
            "account_id": account_id,
            "scheduled_at": _to_db_naive_utc(scheduled_at),
            "executed_at": _to_db_naive_utc(executed_at),
            "score": score,
            "reason": reason,
            "forecast_snapshot_hash": forecast_snapshot_hash,
            "state_before_json": state_before_json,
            "state_after_json": state_after_json,
            "status": status,
            "idempotency_key": idempotency_key,
        }
        # Idempotency-key upsert: concurrent writers (e.g. overlapping planner
        # leaders during a leadership handover) converge on the surviving row
        # instead of raising IntegrityError and aborting the planning tick.
        if self._dialect_name() == "postgresql":
            insert_stmt = postgresql.insert(QuotaPlannerDecision).values(**values)
        else:
            insert_stmt = sqlite.insert(QuotaPlannerDecision).values(**values)
        stmt = insert_stmt.on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(QuotaPlannerDecision.id)
        async with sqlite_writer_section():
            inserted_id = await self._session.scalar(stmt)
            await self._session.commit()
        if inserted_id is not None:
            row = await self._session.get(QuotaPlannerDecision, inserted_id)
            if row is not None:
                return row
        surviving = await self._session.scalar(
            select(QuotaPlannerDecision).where(QuotaPlannerDecision.idempotency_key == idempotency_key)
        )
        if surviving is None:
            raise RuntimeError(f"Quota planner decision vanished for idempotency key {idempotency_key!r}")
        return surviving

    async def recent_decisions(self, limit: int = 50) -> list[QuotaPlannerDecision]:
        stmt = select(QuotaPlannerDecision).order_by(QuotaPlannerDecision.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_decision(self, decision_id: str) -> QuotaPlannerDecision | None:
        return await self._session.get(QuotaPlannerDecision, decision_id)

    async def get_decision_fresh(self, decision_id: str) -> QuotaPlannerDecision | None:
        """Re-read a decision bypassing the identity map (fresh DB state)."""
        return await self._session.get(QuotaPlannerDecision, decision_id, populate_existing=True)

    async def claim_warmup_decision(
        self,
        decision_id: str,
        *,
        since: datetime,
        max_warmups: int,
        max_credits: float,
    ) -> QuotaPlannerDecision | None:
        """Atomically claim a planned warmup decision within the daily budgets.

        The planned -> executing transition is the single authoritative budget
        enforcement point: one conditional UPDATE whose WHERE clause embeds both
        daily guards. The count budget includes in-flight ``executing`` decisions
        in addition to ``executed`` ones so a probe reserves budget the moment it
        is claimed. The claim stamps its own timestamp into ``executed_at``
        (overwritten with the completion time when the probe finishes), and
        executing rows count against the day they were claimed: a decision the
        scheduler planned before midnight but that is claimed after midnight
        consumes the new day's budget even though its ``created_at`` is
        yesterday.

        PostgreSQL serializes concurrent claims with ``pg_advisory_xact_lock`` on
        a fixed warmup-budget key (released at commit); under READ COMMITTED two
        concurrent UPDATEs on different decision rows would otherwise each
        evaluate the count subquery against their own snapshot and could both
        pass. SQLite needs no lock: the whole UPDATE (subqueries included)
        executes atomically under SQLite's database-level single-writer lock,
        safe across processes sharing one file.

        Returns the claimed decision, or ``None`` when the claim was refused
        (already claimed elsewhere, or a budget guard failed).
        """
        since = to_utc_naive(since)
        active_warmups = (
            select(func.count(QuotaPlannerDecision.id)).where(_active_warmup_budget_clause(since)).scalar_subquery()
        )
        warmup_cost = (
            select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
            .where(
                and_(
                    RequestLog.request_kind == "warmup",
                    RequestLog.requested_at >= since,
                    RequestLog.deleted_at.is_(None),
                )
            )
            .scalar_subquery()
        )
        stmt = (
            update(QuotaPlannerDecision)
            .where(
                QuotaPlannerDecision.id == decision_id,
                QuotaPlannerDecision.status == "planned",
                active_warmups < max_warmups,
                warmup_cost < max_credits,
            )
            .values(status="executing", reason="warmup_executing", executed_at=to_utc_naive(utcnow()))
            .returning(QuotaPlannerDecision.id)
        )
        async with sqlite_writer_section():
            # Issue the claim at the start of a fresh transaction so its
            # statement snapshot (and, on PostgreSQL, the advisory lock scope)
            # is not tied to earlier reads on this session.
            await self._session.commit()
            if self._dialect_name() == "postgresql":
                await self._session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": _WARMUP_BUDGET_LOCK_KEY},
                )
            claimed_id = await self._session.scalar(stmt)
            await self._session.commit()
        if claimed_id is None:
            return None
        return await self._session.get(QuotaPlannerDecision, claimed_id, populate_existing=True)

    async def update_decision_status(
        self,
        decision_id: str,
        *,
        status: str,
        reason: str | None = None,
        executed_at: datetime | None = None,
        state_after_json: str | None = None,
        expected_status: str | Collection[str] | None = None,
    ) -> QuotaPlannerDecision | None:
        values: dict[str, object] = {"status": status}
        if reason is not None:
            values["reason"] = reason
        if executed_at is not None:
            values["executed_at"] = _to_db_naive_utc(executed_at)
        if state_after_json is not None:
            values["state_after_json"] = state_after_json
        stmt = update(QuotaPlannerDecision).where(QuotaPlannerDecision.id == decision_id).values(**values)
        if expected_status is not None:
            if isinstance(expected_status, str):
                stmt = stmt.where(QuotaPlannerDecision.status == expected_status)
            else:
                stmt = stmt.where(QuotaPlannerDecision.status.in_(tuple(expected_status)))
        stmt = stmt.returning(QuotaPlannerDecision.id)
        async with sqlite_writer_section():
            updated_id = await self._session.scalar(stmt)
            await self._session.commit()
        if updated_id is None:
            return None
        row = await self._session.get(QuotaPlannerDecision, updated_id)
        if row is None:
            return None
        await self._session.refresh(row)
        return row

    async def count_active_warmups_since(self, since: datetime) -> int:
        """Count warmup decisions consuming the daily count budget.

        Includes ``executed`` decisions (by ``executed_at``) and in-flight
        ``executing`` decisions by the claim timestamp stamped into
        ``executed_at`` when they were claimed (see
        ``_active_warmup_budget_clause``).
        """
        since = to_utc_naive(since)
        stmt = select(func.count(QuotaPlannerDecision.id)).where(_active_warmup_budget_clause(since))
        return int(await self._session.scalar(stmt) or 0)

    async def count_executed_warmups_since(self, since: datetime) -> int:
        since = to_utc_naive(since)
        stmt = select(func.count(QuotaPlannerDecision.id)).where(
            and_(
                QuotaPlannerDecision.action == "warmup",
                QuotaPlannerDecision.status == "executed",
                QuotaPlannerDecision.executed_at >= since,
            )
        )
        return int(await self._session.scalar(stmt) or 0)

    async def warmup_cost_since(self, since: datetime) -> float:
        since = to_utc_naive(since)
        stmt = select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0)).where(
            and_(
                RequestLog.request_kind == "warmup",
                RequestLog.requested_at >= since,
                RequestLog.deleted_at.is_(None),
            )
        )
        return float(await self._session.scalar(stmt) or 0.0)

    async def latest_warmup_effect_observation(
        self,
        *,
        account_id: str,
        model: str,
    ) -> QuotaWindowObservation | None:
        stmt = (
            select(QuotaWindowObservation)
            .where(
                and_(
                    QuotaWindowObservation.account_id == account_id,
                    QuotaWindowObservation.model == model,
                    QuotaWindowObservation.source == "warmup_probe",
                    QuotaWindowObservation.confidence.in_(("observed", "known", "high")),
                )
            )
            .order_by(QuotaWindowObservation.observed_at.desc(), QuotaWindowObservation.id.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def add_window_observation(
        self,
        *,
        account_id: str,
        source: str,
        observed_at: datetime | None = None,
        model: str | None = None,
        primary_remaining_percent: float | None = None,
        primary_reset_at: int | None = None,
        secondary_remaining_percent: float | None = None,
        secondary_reset_at: int | None = None,
        confidence: str = "unknown",
    ) -> QuotaWindowObservation:
        row = QuotaWindowObservation(
            account_id=account_id,
            observed_at=_to_db_naive_utc(observed_at) or utcnow(),
            model=model,
            primary_remaining_percent=primary_remaining_percent,
            primary_reset_at=primary_reset_at,
            secondary_remaining_percent=secondary_remaining_percent,
            secondary_reset_at=secondary_reset_at,
            source=source,
            confidence=confidence,
        )
        self._session.add(row)
        async with sqlite_writer_section():
            await self._session.commit()
            await self._session.refresh(row)
        return row

    async def aggregate_demand_bins(
        self,
        *,
        since: datetime | None = None,
        bucket_seconds: int = 900,
    ) -> list[DemandBin]:
        since = to_utc_naive(since) if since is not None else (utcnow() - timedelta(days=28))
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = func.floor(func.extract("epoch", RequestLog.requested_at) / bucket_seconds) * bucket_seconds
        else:
            epoch_col = cast(func.strftime("%s", RequestLog.requested_at), Integer)
            bucket_expr = cast(epoch_col / bucket_seconds, Integer) * bucket_seconds
        bucket_col = bucket_expr.label("slot_epoch")
        request_kind = func.coalesce(RequestLog.request_kind, literal("real")).label("request_kind")
        stmt = (
            select(
                bucket_col,
                RequestLog.account_id,
                RequestLog.api_key_id,
                RequestLog.model,
                RequestLog.reasoning_effort,
                request_kind,
                RequestLog.status,
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(
                    func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)),
                    0,
                ).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count(RequestLog.id).label("request_count"),
            )
            .where(and_(RequestLog.requested_at >= since, RequestLog.deleted_at.is_(None)))
            .group_by(
                bucket_col,
                RequestLog.account_id,
                RequestLog.api_key_id,
                RequestLog.model,
                RequestLog.reasoning_effort,
                request_kind,
                RequestLog.status,
            )
            .order_by(bucket_col)
        )
        result = await self._session.execute(stmt)
        return [
            DemandBin(
                slot_epoch=int(row.slot_epoch),
                account_id=row.account_id,
                api_key_id=row.api_key_id,
                model=row.model,
                reasoning_effort=row.reasoning_effort,
                request_kind=row.request_kind,
                status=row.status,
                input_tokens=int(row.input_tokens or 0),
                cached_input_tokens=int(row.cached_input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cost_usd=float(row.cost_usd or 0.0),
                request_count=int(row.request_count or 0),
            )
            for row in result.all()
        ]


def _settings_from_row(row: QuotaPlannerSettings) -> PlannerSettings:
    return PlannerSettings(
        mode=row.mode,
        timezone=row.timezone,
        working_days=parse_working_days(row.working_days_json),
        working_hours_start=row.working_hours_start,
        working_hours_end=row.working_hours_end,
        prewarm_enabled=row.prewarm_enabled,
        prewarm_lead_minutes=row.prewarm_lead_minutes,
        max_warmups_per_day=row.max_warmups_per_day,
        max_warmup_credits_per_day=row.max_warmup_credits_per_day,
        min_expected_gain=row.min_expected_gain,
        forecast_quantile=row.forecast_quantile,
        allow_synthetic_traffic=row.allow_synthetic_traffic,
        warmup_model_preference=row.warmup_model_preference,
        dry_run=row.dry_run,
    )
