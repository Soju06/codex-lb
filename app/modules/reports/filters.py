from __future__ import annotations

from sqlalchemy import and_, or_

from app.db.models import RequestLog

_INTERNAL_LIMIT_WARMUP_SOURCE = "limit_warmup"
_INTERNAL_WARMUP_REQUEST_KINDS = ("warmup", "limit_warmup")
MISSING_USERAGENT_GROUP = "Missing User-Agent"


def _useragent_group_filter_clause(useragent_group: str | None):
    if not useragent_group:
        return None
    if useragent_group == MISSING_USERAGENT_GROUP:
        return RequestLog.useragent_group.is_(None)
    return RequestLog.useragent_group == useragent_group


def _normal_traffic_clause():
    return and_(
        or_(RequestLog.source.is_(None), RequestLog.source != _INTERNAL_LIMIT_WARMUP_SOURCE),
        or_(
            RequestLog.request_kind.is_(None),
            RequestLog.request_kind.not_in(_INTERNAL_WARMUP_REQUEST_KINDS),
        ),
    )
