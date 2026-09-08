"""Subscription-exhaustion overflow designation (#2123 WP-B).

Dashboard-only helpers behind the ``subscription_overflow_source_id`` setting:
the drain-deadline arithmetic, the eligibility rule a designated source must
satisfy, and the read-only preflight report. Nothing on the request path reads
the designation in this stage -- ``tests/unit/test_subscription_overflow_inert.py``
pins that -- so overflow routing stays unreachable until the routing stages
(WP-C1/WP-C2) deliberately relax the ratchet and import the constants below.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DashboardBadRequestError
from app.core.openai.model_registry import (
    MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
    MODEL_SOURCE_KIND_SUBSCRIPTION,
    UpstreamModel,
    get_model_registry,
)
from app.db.models import ApiKeyModelSourceAssignment, ModelSource, ModelSourceModel, ModelSourcePin
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
