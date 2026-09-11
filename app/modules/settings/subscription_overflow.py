"""Subscription-exhaustion overflow designation: the dashboard side (#2123 WP-B, WP-G).

Dashboard-only helpers behind the ``subscription_overflow_source_id`` setting:
the drain-deadline arithmetic, the eligibility rule a designated source must
satisfy, the read-only preflight report, and the overview's windowed spend and
live-pin aggregate. The pin lifetimes and ``PIN_KIND_THREAD`` are defined here
and imported by the routing stage's pin primitive so the 29-day drain window is
written down once.

This module never touches the request path itself; the positive ratchet in
``tests/unit/test_subscription_overflow_inert.py`` records which production files
may name the designation, which may name the pin table, and which may import
this module (the settings and model-source APIs, plus the dashboard overview's
repository). Nothing here imports ``app.modules.proxy.overflow``: the decision
module owns the load-balancer probe, the pin primitive and the dispatch owner,
and none of them belong in a dashboard read's import graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DashboardBadRequestError
from app.core.openai.model_registry import (
    MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
    MODEL_SOURCE_KIND_SUBSCRIPTION,
    UpstreamModel,
    get_model_registry,
)
from app.db.models import (
    ApiKeyModelSourceAssignment,
    DashboardSettings,
    ModelSource,
    ModelSourceModel,
    ModelSourcePin,
    RequestLog,
)
from app.modules.model_sources.catalog import source_model_supported_tool_types
from app.modules.model_sources.repository import ModelSourcesRepository
from app.modules.settings.schemas import (
    SubscriptionOverflowContextWindowMismatch,
    SubscriptionOverflowPreflightModel,
    SubscriptionOverflowPreflightResponse,
)

# Pin lifetimes (design §3). The pin repository (WP-C1) imports these so the
# 29-day drain window is defined exactly once.
PIN_IDLE_TTL = timedelta(days=7)
PIN_TOMBSTONE_GRACE = timedelta(days=21)
DRAIN_WINDOW = PIN_IDLE_TTL + PIN_TOMBSTONE_GRACE + timedelta(days=1)

SUBSCRIPTION_OVERFLOW_SOURCE_INVALID = "subscription_overflow_source_invalid"
BLOCKER_SOURCE_KIND_UNSUPPORTED = "source_kind_unsupported"
BLOCKER_SOURCE_RESPONSES_UNSUPPORTED = "source_responses_unsupported"

NEVER_OVERFLOWS_RESPONSES_LITE = "responses_lite"
NEVER_OVERFLOWS_CODE_MODE_ONLY = "code_mode_only"
NEVER_OVERFLOWS_NOT_IN_REGISTRY = "not_in_registry"

WARNING_UNDECLARED_TOOL_TYPES = "undeclared_tool_types"
WARNING_NO_VISION = "no_vision"
WARNING_NO_STREAMING = "no_streaming"
WARNING_UNPRICED = "unpriced"
WARNING_CONTEXT_WINDOW_SMALLER = "context_window_smaller"
WARNING_CONTEXT_WINDOW_MISSING = "context_window_missing"
WARNING_RESPONSES_LITE_EXCLUDED = "responses_lite_excluded"
WARNING_NOT_IN_REGISTRY = "not_in_registry"

# Non-function Responses tool types Codex sends that a source must declare on
# its model entries (``supports_search_tool`` / ``experimental_supported_tools``
# in ``raw_metadata_json``) before overflow can forward them; function tools
# are always forwarded.
CODEX_TOOL_TYPES = frozenset({"custom", "apply_patch", "web_search", "shell", "local_shell", "tool_search"})

PIN_KIND_THREAD = "thread"

# The two ``request_logs.source`` values an overflow dispatch writes, spelled as
# literals rather than imported from ``app.modules.proxy.overflow``: this module
# is read by the dashboard, and importing the decision module would pull the
# load-balancer probe, the pin primitive and the dispatch owner into the
# dashboard's import graph. ``tests/unit/test_subscription_overflow_dashboard_summary.py``
# pins them to ``REQUEST_LOG_SOURCE_FRESH`` / ``REQUEST_LOG_SOURCE_PINNED`` so the
# two spellings cannot drift. A closed equality set, never a prefix ``LIKE``:
# ``idx_logs_source_requested_at`` serves ``IN`` as two index probes on both
# dialects, while a prefix ``LIKE`` degrades to a sequential scan under a
# non-C PostgreSQL collation.
OVERFLOW_REQUEST_LOG_SOURCES: tuple[str, str] = ("subscription_overflow", "subscription_overflow_pinned")


def resolve_drain_until(
    current_source_id: str | None,
    new_source_id: str | None,
    current_drain_until: datetime | None,
    now: datetime,
) -> datetime | None:
    """Drain deadline after a designation write (design §3, §8.8).

    Clearing the designation arms ``now + DRAIN_WINDOW`` so conversations
    already pinned to the source keep resolving for the pin idle TTL plus the
    tombstone grace; designating a source clears any pending deadline; a
    same-state write or a switch between two sources leaves it untouched.
    """
    if current_source_id is not None and new_source_id is None:
        return now + DRAIN_WINDOW
    if current_source_id is None and new_source_id is not None:
        return None
    return current_drain_until


def resolve_pins_expire_by(drain_until: datetime | None) -> datetime | None:
    """Latest instant a conversation pinned to the cleared source can still resolve.

    The drain cap (design §3, §8.8) bounds every pin written before or during the
    drain to ``expires_at <= drain_until - PIN_TOMBSTONE_GRACE - 1 d``, i.e. the
    clear time plus ``PIN_IDLE_TTL``. The remaining 22 days of the lookup window
    only keep expired pins answerable as tombstones, so this -- not
    ``drain_until`` -- is the date the dashboard shows while draining.
    """
    if drain_until is None:
        return None
    return drain_until - PIN_TOMBSTONE_GRACE - timedelta(days=1)


def overflow_source_blockers(source: ModelSource) -> list[str]:
    blockers: list[str] = []
    if source.kind != MODEL_SOURCE_KIND_OPENAI_COMPATIBLE:
        blockers.append(BLOCKER_SOURCE_KIND_UNSUPPORTED)
    if not source.supports_responses:
        blockers.append(BLOCKER_SOURCE_RESPONSES_UNSUPPORTED)
    return blockers


def validate_overflow_source(source: ModelSource | None) -> None:
    """Reject a designation unless the source can serve Responses traffic.

    Enabled-ness is deliberately not validated: disabling the source is one of
    the kill switches, so an operator must be able to keep a disabled source
    designated (and re-enable it) without touching this setting.
    """
    if source is None or overflow_source_blockers(source):
        raise DashboardBadRequestError(
            "subscriptionOverflowSourceId must name an OpenAI-compatible model source that supports the Responses API",
            code=SUBSCRIPTION_OVERFLOW_SOURCE_INVALID,
        )


def never_overflows_reason(registry_model: UpstreamModel | None) -> str | None:
    """Why a registry slug can never overflow in this version, or ``None``.

    The gpt-5.6 family is served through Responses-Lite in code mode; its
    request bodies are not portable to an OpenAI-compatible source (design
    §15 Q15), so overflow never applies to those conversations.
    """
    if registry_model is None:
        return NEVER_OVERFLOWS_NOT_IN_REGISTRY
    raw = registry_model.raw
    if raw.get("use_responses_lite") is True:
        return NEVER_OVERFLOWS_RESPONSES_LITE
    if raw.get("tool_mode") == "code_mode_only":
        return NEVER_OVERFLOWS_CODE_MODE_ONLY
    return None


def _subscription_registry_models(registry_models: dict[str, UpstreamModel]) -> dict[str, UpstreamModel]:
    return {
        slug: model for slug, model in registry_models.items() if model.source_kind == MODEL_SOURCE_KIND_SUBSCRIPTION
    }


def _registry_lookup(subscription_models: dict[str, UpstreamModel], slug: str) -> UpstreamModel | None:
    return subscription_models.get(slug) or subscription_models.get(slug.strip().lower())


def _preflight_model(
    source: ModelSource,
    entry: ModelSourceModel,
    subscription_models: dict[str, UpstreamModel],
) -> SubscriptionOverflowPreflightModel:
    registry_model = _registry_lookup(subscription_models, entry.model)
    reason = never_overflows_reason(registry_model)
    priced = entry.input_per_1m is not None and entry.output_per_1m is not None
    if reason is not None:
        # A model that can never overflow has nothing else worth warning about.
        exclusion = (
            WARNING_NOT_IN_REGISTRY if reason == NEVER_OVERFLOWS_NOT_IN_REGISTRY else WARNING_RESPONSES_LITE_EXCLUDED
        )
        return SubscriptionOverflowPreflightModel(
            slug=entry.model,
            enabled=entry.is_enabled,
            never_overflows=True,
            never_overflows_reason=reason,
            undeclared_tool_types=[],
            supports_vision=entry.supports_vision,
            supports_streaming=entry.supports_streaming,
            priced=priced,
            context_window_mismatch=None,
            warnings=[exclusion],
        )
    assert registry_model is not None
    warnings: list[str] = []
    undeclared = sorted(CODEX_TOOL_TYPES - source_model_supported_tool_types(source, entry.model))
    if undeclared:
        warnings.append(WARNING_UNDECLARED_TOOL_TYPES)
    if not entry.supports_vision:
        warnings.append(WARNING_NO_VISION)
    if not entry.supports_streaming:
        warnings.append(WARNING_NO_STREAMING)
    if not priced:
        warnings.append(WARNING_UNPRICED)
    mismatch: SubscriptionOverflowContextWindowMismatch | None = None
    # Codex compacts on the registry's context-window arithmetic, so a source
    # window that is missing or smaller yields a pre-stream 400 on every turn.
    if entry.context_window is None:
        warnings.append(WARNING_CONTEXT_WINDOW_MISSING)
    elif entry.context_window < registry_model.context_window:
        warnings.append(WARNING_CONTEXT_WINDOW_SMALLER)
    if entry.context_window is None or entry.context_window < registry_model.context_window:
        mismatch = SubscriptionOverflowContextWindowMismatch(
            registry=registry_model.context_window,
            source=entry.context_window,
            max_output_tokens=entry.max_output_tokens,
        )
    return SubscriptionOverflowPreflightModel(
        slug=entry.model,
        enabled=entry.is_enabled,
        never_overflows=False,
        never_overflows_reason=None,
        undeclared_tool_types=undeclared,
        supports_vision=entry.supports_vision,
        supports_streaming=entry.supports_streaming,
        priced=priced,
        context_window_mismatch=mismatch,
        warnings=warnings,
    )


def build_preflight(
    source: ModelSource,
    *,
    registry_models: dict[str, UpstreamModel],
    scoped_api_key_count: int,
    live_pin_count: int,
    tombstone_count: int,
    drain_until: datetime | None,
) -> SubscriptionOverflowPreflightResponse:
    """Pure readiness report for designating ``source`` (design §10).

    Only the kind/Responses checks block; everything else is a warning the
    operator weighs. ``missing_models`` lists the registry's subscription slugs
    that could overflow (not Responses-Lite / code-mode) but are not enabled on
    the source.
    """
    subscription_models = _subscription_registry_models(registry_models)
    blockers = overflow_source_blockers(source)
    served = [
        _preflight_model(source, entry, subscription_models)
        for entry in sorted(source.models, key=lambda candidate: candidate.model)
    ]
    # Resolve enabled entries through the same lookup ``served_models`` uses so
    # a differently cased entry (``GPT-5.4``) counts as serving ``gpt-5.4``
    # instead of being reported both served and missing.
    served_registry_slugs = {
        registry_model.slug
        for entry in source.models
        if entry.is_enabled and (registry_model := _registry_lookup(subscription_models, entry.model)) is not None
    }
    missing = sorted(
        slug
        for slug, model in subscription_models.items()
        if model.slug not in served_registry_slugs and never_overflows_reason(model) is None
    )
    return SubscriptionOverflowPreflightResponse(
        source_id=source.id,
        source_name=source.name,
        source_enabled=source.is_enabled,
        eligible=not blockers,
        blockers=blockers,
        drain_until=drain_until,
        served_models=served,
        missing_models=missing,
        scoped_api_key_count=scoped_api_key_count,
        live_pin_count=live_pin_count,
        tombstone_count=tombstone_count,
    )


async def count_thread_pins(session: AsyncSession, source_id: str, *, now: datetime) -> tuple[int, int]:
    """``(live, tombstone)`` thread pins on ``source_id`` (dashboard read only).

    A row answers lookups while ``purge_at > now``; ``expires_at <= now`` marks
    it a tombstone. ``now`` must be timezone-aware UTC to match the
    ``DateTime(timezone=True)`` pin columns on both dialects.
    """
    live_case = case((ModelSourcePin.expires_at > now, 1), else_=0)
    tombstone_case = case((ModelSourcePin.expires_at <= now, 1), else_=0)
    stmt = (
        select(
            func.coalesce(func.sum(live_case), 0),
            func.coalesce(func.sum(tombstone_case), 0),
        )
        .select_from(ModelSourcePin)
        .where(
            ModelSourcePin.kind == PIN_KIND_THREAD,
            ModelSourcePin.source_id == source_id,
            ModelSourcePin.purge_at > now,
        )
    )
    row = (await session.execute(stmt)).one()
    return int(row[0]), int(row[1])


@dataclass(frozen=True, slots=True)
class SubscriptionOverflowActivity:
    """Windowed overflow spend plus the live-pin count, for the dashboard tile.

    ``cost_usd`` is a *slice* of the estimated-cost figure the overview already
    reports, not an addition to it: overflow rows carry ``request_kind`` ``normal``
    and are counted by the activity aggregate like any other row.
    ``usage_less_requests`` counts the rows whose source reported no usage at all
    (both token columns null, the estimate-settled and usage-unavailable cases);
    those contribute nothing to ``cost_usd``, so the tile can say the window is
    under-reported instead of presenting an incomplete sum as the truth.
    """

    requests: int
    cost_usd: float
    usage_less_requests: int
    live_pins: int


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def overflow_window_statement(*, since: datetime, until: datetime) -> Select[tuple[int, float | None, int]]:
    """``(requests, cost_usd, usage_less_requests)`` over the two overflow sources.

    The ``source`` predicate is an ``IN`` over the closed pair, never a prefix
    ``LIKE``: only equality rides ``idx_logs_source_requested_at`` on both
    dialects. ``tests/unit/test_subscription_overflow_dashboard_summary.py``
    compiles this statement and fails on a ``LIKE``.
    """
    usage_less = case((RequestLog.input_tokens.is_(None) & RequestLog.output_tokens.is_(None), 1), else_=0)
    return (
        select(
            func.count(),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
            func.coalesce(func.sum(usage_less), 0),
        )
        .select_from(RequestLog)
        .where(
            RequestLog.source.in_(OVERFLOW_REQUEST_LOG_SOURCES),
            RequestLog.requested_at >= since,
            RequestLog.requested_at <= until,
            RequestLog.deleted_at.is_(None),
        )
    )


def overflow_existence_statement() -> Select[tuple[int]]:
    """Has an overflow row ever been written? One bounded probe on the same index."""
    return (
        select(literal(1)).select_from(RequestLog).where(RequestLog.source.in_(OVERFLOW_REQUEST_LOG_SOURCES)).limit(1)
    )


async def count_live_thread_pins(session: AsyncSession, *, now: datetime) -> int:
    """Live thread pins across every source (dashboard read only).

    Deliberately not scoped to a source id: pins outlive de-designation for the
    drain window, and the tile has no source to scope by while draining --
    ``count_thread_pins`` requires one. Anchor and bounce pins are excluded
    because the tile counts conversations, and tombstones (``expires_at <= now``)
    are excluded because they no longer resolve.
    """
    moment = _aware_utc(now)
    stmt = (
        select(func.count())
        .select_from(ModelSourcePin)
        .where(
            ModelSourcePin.kind == PIN_KIND_THREAD,
            ModelSourcePin.expires_at > moment,
            ModelSourcePin.purge_at > moment,
        )
    )
    return int(await session.scalar(stmt) or 0)


def never_designated(settings: DashboardSettings) -> bool:
    """Has overflow never been switched on here? Two attribute reads, no statement.

    The same two columns the request path's ship-dark gate reads
    (``app.modules.proxy.overflow._settings_off``), and the pair is only ever
    written together by ``resolve_drain_until``: clearing a designation arms the
    drain deadline, designating one clears it. So both ``NULL`` means no source
    was ever designated, which in turn means no overflow request-log row and no
    pin can exist -- the aggregate below is provably empty and is skipped rather
    than issued. A drain deadline that has already elapsed deliberately does
    *not* count as never-designated: the request path is off, but the history
    the tile reports is real and stays visible.
    """
    return settings.subscription_overflow_source_id is None and settings.subscription_overflow_drain_until is None


async def load_subscription_overflow_activity(
    session: AsyncSession,
    *,
    settings: DashboardSettings,
    since: datetime,
    until: datetime,
    now: datetime,
) -> SubscriptionOverflowActivity | None:
    """Overflow activity in ``[since, until]``, or ``None`` when there is nothing to show.

    ``None`` means the installation has never dispatched an overflow request and
    holds no live pin, which is the state of every install that never designated
    a source -- the dashboard then renders neither the tile nor the source
    filter. ``since``/``until`` must be naive UTC to match ``requested_at``;
    ``now`` may be naive or aware and is coerced for the timezone-aware pin
    columns.

    ``settings`` gates the whole read: on a ship-dark install this returns
    ``None`` without issuing a statement, so an overview poll costs exactly what
    it cost before this tile existed. ``tests/integration/test_dashboard_overview.py``
    counts the statements of a default-install poll to keep that true.
    """
    if never_designated(settings):
        return None
    row = (await session.execute(overflow_window_statement(since=since, until=until))).one()
    requests = int(row[0])
    cost_usd = float(row[1] or 0.0)
    usage_less_requests = int(row[2] or 0)

    live_pins = await count_live_thread_pins(session, now=now)
    if requests > 0:
        ever_dispatched = True
    else:
        # Existence probe on the same index, so a post-drain or out-of-window
        # install still gets the tile (with its neutral empty state) while a
        # never-overflowed one gets nothing. Soft-deleted rows count here: the
        # dispatch still happened.
        ever_dispatched = await session.scalar(overflow_existence_statement()) is not None
    if not ever_dispatched and live_pins == 0:
        return None
    return SubscriptionOverflowActivity(
        requests=requests,
        cost_usd=cost_usd,
        usage_less_requests=usage_less_requests,
        live_pins=live_pins,
    )


async def count_scoped_api_keys(session: AsyncSession, source_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(ApiKeyModelSourceAssignment)
        .where(ApiKeyModelSourceAssignment.source_id == source_id)
    )
    return int(await session.scalar(stmt) or 0)


async def load_subscription_overflow_preflight(
    session: AsyncSession,
    source_id: str,
    *,
    drain_until: datetime | None,
) -> SubscriptionOverflowPreflightResponse | None:
    """Assemble the preflight for ``source_id``; ``None`` when the source does not exist."""
    source = await ModelSourcesRepository(session).get_by_id(source_id)
    if source is None:
        return None
    scoped_api_key_count = await count_scoped_api_keys(session, source_id)
    live_pin_count, tombstone_count = await count_thread_pins(session, source_id, now=datetime.now(timezone.utc))
    return build_preflight(
        source,
        registry_models=get_model_registry().get_models_with_fallback(),
        scoped_api_key_count=scoped_api_key_count,
        live_pin_count=live_pin_count,
        tombstone_count=tombstone_count,
        drain_until=drain_until,
    )
