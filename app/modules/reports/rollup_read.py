"""Single-snapshot report sources, with exact raw complements of folded buckets."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, case, cast, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from app.db.models import AccountUsageRollupState, RequestLog, RequestReportHourlyRollup
from app.modules.accounts.usage_time_rollup import DIMENSION_SENTINEL, conversation_id_expr
from app.modules.accounts.usage_time_rollup_read import ceil_to_grid, epoch_seconds, floor_to_grid
from app.modules.reports.filters import MISSING_USERAGENT_GROUP, _normal_traffic_clause
from app.modules.reports.rollup import MEASURES, raw_measures


def _decode(column):
    return case(
        (column == DIMENSION_SENTINEL, None),
        (func.substr(column, 1, 1) == DIMENSION_SENTINEL, func.substr(column, 2)),
        else_=column,
    )


def report_source(
    session: AsyncSession,
    windows: list[tuple[str, datetime, datetime]],
    account_ids: list[str] | None = None,
    model: str | None = None,
    useragent_group: str | None = None,
    api_key_ids: list[str] | None = None,
    *,
    catalog: bool = False,
) -> Subquery:
    """UTC hours retain all report filter/distinct dimensions.

    Each day has a folded middle and two possible raw edges. Dynamic tail
    bounds are in the index range condition, so PostgreSQL need not scan all
    historical logs and discard them after checking the watermark.
    """
    values = [
        select(
            literal(label).label("report_date"),
            literal(start).label("start_at"),
            literal(end).label("end_at"),
            literal(ceil_to_grid(start, 3600)).label("lo_at"),
            literal(floor_to_grid(end, 3600)).label("hi_at"),
            literal(epoch_seconds(ceil_to_grid(start, 3600))).label("lo_epoch"),
            literal(epoch_seconds(floor_to_grid(end, 3600))).label("hi_epoch"),
        )
        for label, start, end in windows
    ]
    w = (values[0] if len(values) == 1 else union_all(*values)).cte("report_windows")
    watermark = func.coalesce(
        select(AccountUsageRollupState.reports_folded_through).where(AccountUsageRollupState.id == 1).scalar_subquery(),
        literal(datetime(1970, 1, 1)),
    )
    watermark_epoch = (
        cast(func.extract("epoch", watermark), BigInteger)
        if session.get_bind().dialect.name == "postgresql"
        else cast(func.strftime("%s", watermark), BigInteger)
    )
    r = RequestReportHourlyRollup
    folded_dimensions = [
        _decode(r.account_id).label("account_id"),
        _decode(r.api_key_id).label("api_key_id"),
        r.model,
        _decode(r.useragent_group).label("useragent_group"),
        _decode(r.conversation_id).label("conversation_id"),
    ]
    raw_dimensions = [
        RequestLog.account_id,
        RequestLog.api_key_id,
        RequestLog.model,
        RequestLog.useragent_group,
        conversation_id_expr().label("conversation_id"),
    ]
    # Catalog never calculates measures or groups by conversations.
    folded_columns = (
        folded_dimensions[2:4]
        if catalog
        else [w.c.report_date, *folded_dimensions, r.first_requested_at, *(getattr(r, n) for n in MEASURES)]
    )
    folded = (
        select(*folded_columns)
        .select_from(w)
        .join(
            r, (r.bucket_epoch >= w.c.lo_epoch) & (r.bucket_epoch < w.c.hi_epoch) & (r.bucket_epoch < watermark_epoch)
        )
    )
    raw_columns = (
        raw_dimensions[2:4]
        if catalog
        else [
            w.c.report_date,
            *raw_dimensions,
            func.min(RequestLog.requested_at).label("first_requested_at"),
            *raw_measures(),
        ]
    )
    high = case((watermark < w.c.hi_at, watermark), else_=w.c.hi_at)
    tail_start = case((high <= w.c.lo_at, w.c.start_at), else_=high)

    def raw(start, end, extra=()):
        stmt = (
            select(*raw_columns)
            .select_from(w)
            .join(RequestLog, (RequestLog.requested_at >= start) & (RequestLog.requested_at < end))
            .where(_normal_traffic_clause(), *extra)
        )
        return stmt.distinct() if catalog else stmt.group_by(w.c.report_date, *raw_dimensions)

    # A leading edge exists only when there is a folded middle.
    branches = [folded, raw(tail_start, w.c.end_at)]
    if any(start != ceil_to_grid(start, 3600) for _, start, _ in windows):
        branches.append(raw(w.c.start_at, w.c.lo_at, (high > w.c.lo_at,)))
    source = union_all(*branches).subquery("report_history")
    conditions = []
    if account_ids and not catalog:
        conditions.append(source.c.account_id.in_(account_ids))
    if api_key_ids and not catalog:
        conditions.append(source.c.api_key_id.in_(api_key_ids))
    if model:
        conditions.append(source.c.model == model)
    if useragent_group:
        conditions.append(
            source.c.useragent_group.is_(None)
            if useragent_group == MISSING_USERAGENT_GROUP
            else source.c.useragent_group == useragent_group
        )
    # Scoped catalogs need their account/API-key predicates pushed into each
    # branch, since those dimensions intentionally aren't in the result.
    if catalog:
        from app.modules.accounts.usage_time_rollup import to_dimension

        folded_filters = []
        raw_filters = []
        for selected, rc, lc in (
            (account_ids, r.account_id, RequestLog.account_id),
            (api_key_ids, r.api_key_id, RequestLog.api_key_id),
        ):
            if selected:
                folded_filters.append(rc.in_([to_dimension(value) for value in selected]))
                raw_filters.append(lc.in_(selected))
        branches = [branches[0].where(*folded_filters), *(branch.where(*raw_filters) for branch in branches[1:])]
        return union_all(*branches).subquery("report_catalog")
    return select(source).where(*conditions).subquery("filtered_report_history") if conditions else source
