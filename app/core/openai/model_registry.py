from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase

import anyio

from app.core.types import JsonValue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReasoningLevel:
    effort: str
    description: str


@dataclass(frozen=True)
class UpstreamModel:
    slug: str
    display_name: str
    description: str
    context_window: int
    input_modalities: tuple[str, ...]
    supported_reasoning_levels: tuple[ReasoningLevel, ...]
    default_reasoning_level: str | None
    supports_reasoning_summaries: bool
    support_verbosity: bool
    default_verbosity: str | None
    prefer_websockets: bool
    supports_parallel_tool_calls: bool
    supported_in_api: bool
    minimal_client_version: str | None
    priority: int
    available_in_plans: frozenset[str]
    base_instructions: str = ""
    raw: dict[str, JsonValue] = field(default_factory=dict, hash=False, compare=False)


@dataclass
class ModelRegistrySnapshot:
    models: dict[str, UpstreamModel]
    model_plans: dict[str, frozenset[str]]
    plan_models: dict[str, frozenset[str]]
    model_service_tier_plans: dict[str, dict[str, frozenset[str]]]
    model_service_tier_accounts: dict[str, dict[str, frozenset[str]]]
    account_plans: dict[str, str]
    fetched_at: float


_BOOTSTRAP_WEBSOCKET_PREFERRED_MODEL_PATTERNS = ("gpt-5.5", "gpt-5.5-*", "gpt-5.4", "gpt-5.4-*")

_REASONING_LEVELS_STANDARD = (
    ReasoningLevel(effort="low", description="Low reasoning effort"),
    ReasoningLevel(effort="medium", description="Medium reasoning effort"),
    ReasoningLevel(effort="high", description="High reasoning effort"),
)

_REASONING_LEVELS_EXTENDED = (
    ReasoningLevel(effort="low", description="Low reasoning effort"),
    ReasoningLevel(effort="medium", description="Medium reasoning effort"),
    ReasoningLevel(effort="high", description="High reasoning effort"),
    ReasoningLevel(effort="xhigh", description="Extra high reasoning effort"),
)

_BOOTSTRAP_AVAILABLE_IN_PLANS = frozenset(
    {
        "plus",
        "pro",
        "prolite",
        "team",
        "business",
        "enterprise",
        "edu",
        "education",
        "k12",
        "go",
        "hc",
        "finserv",
        "free",
        "free_workspace",
        "quorum",
        "self_serve_business_usage_based",
        "enterprise_cbp_usage_based",
    }
)

_BOOTSTRAP_CORE_AVAILABLE_IN_PLANS = frozenset(
    plan for plan in _BOOTSTRAP_AVAILABLE_IN_PLANS if plan not in {"free", "free_workspace", "k12"}
)


def _bootstrap_model(
    slug: str,
    display_name: str,
    *,
    prefer_websockets: bool,
    minimal_client_version: str | None,
    reasoning_levels: tuple[ReasoningLevel, ...] = _REASONING_LEVELS_EXTENDED,
    context_window: int = 272_000,
    input_modalities: tuple[str, ...] = ("text", "image"),
    default_reasoning_level: str | None = "medium",
    default_verbosity: str | None = "low",
    supported_in_api: bool = True,
    available_in_plans: frozenset[str] = _BOOTSTRAP_AVAILABLE_IN_PLANS,
    visibility: str = "list",
    shell_type: str = "shell_command",
    raw: dict[str, JsonValue] | None = None,
) -> UpstreamModel:
    raw_fields: dict[str, JsonValue] = {
        "shell_type": shell_type,
        "visibility": visibility,
        "availability_nux": None,
        "max_context_window": context_window,
    }
    if raw:
        raw_fields.update(raw)
    return UpstreamModel(
        slug=slug,
        display_name=display_name,
        description=display_name,
        context_window=context_window,
        input_modalities=input_modalities,
        supported_reasoning_levels=reasoning_levels,
        default_reasoning_level=default_reasoning_level,
        supports_reasoning_summaries=True,
        support_verbosity=True,
        default_verbosity=default_verbosity,
        prefer_websockets=prefer_websockets,
        supports_parallel_tool_calls=True,
        supported_in_api=supported_in_api,
        minimal_client_version=minimal_client_version,
        priority=0,
        available_in_plans=available_in_plans,
        raw=raw_fields,
    )


# Static bundled fallback models used before the first upstream registry refresh.
# This mirrors Codex's model-manager pattern: ship a conservative catalog so
# startup/offline paths have usable metadata, then treat the live upstream
# registry as authoritative once a refresh succeeds. Keep compatibility fields
# explicit rather than inherited from helper defaults; every slug must exist
# upstream, and live upstream data always takes precedence once available.
_BOOTSTRAP_STATIC_MODELS: tuple[UpstreamModel, ...] = (
    _bootstrap_model(
        "gpt-5.5",
        "GPT-5.5",
        prefer_websockets=True,
        minimal_client_version="0.124.0",
    ),
    _bootstrap_model(
        "gpt-5.4",
        "GPT-5.4",
        prefer_websockets=True,
        minimal_client_version="0.98.0",
        available_in_plans=_BOOTSTRAP_CORE_AVAILABLE_IN_PLANS,
        raw={"max_context_window": 1_000_000},
    ),
    _bootstrap_model(
        "gpt-5.4-mini",
        "GPT-5.4 Mini",
        prefer_websockets=True,
        default_verbosity="medium",
        minimal_client_version="0.98.0",
    ),
    _bootstrap_model(
        "gpt-5.3-codex",
        "GPT-5.3 Codex",
        prefer_websockets=True,
        minimal_client_version="0.98.0",
        available_in_plans=_BOOTSTRAP_CORE_AVAILABLE_IN_PLANS,
    ),
    _bootstrap_model(
        "gpt-5.3-codex-spark",
        "GPT-5.3 Codex Spark",
        prefer_websockets=True,
        context_window=128_000,
        input_modalities=("text",),
        default_reasoning_level="high",
        minimal_client_version="0.100.0",
    ),
    _bootstrap_model(
        "gpt-5.2",
        "GPT-5.2",
        prefer_websockets=True,
        minimal_client_version="0.0.1",
    ),
    _bootstrap_model(
        "codex-auto-review",
        "Codex Auto Review",
        prefer_websockets=True,
        minimal_client_version="0.98.0",
        available_in_plans=_BOOTSTRAP_CORE_AVAILABLE_IN_PLANS,
        visibility="hide",
        raw={"max_context_window": 1_000_000},
    ),
)


# Speed/service-tier metadata must aggregate (union) when the same slug is
# fetched from multiple accounts/plans, rather than be overwritten
# last-writer-wins. Otherwise a single account without Fast entitlement returns
# an empty tier list and erases Fast from the shared catalog for every account
# (issue #1100).
_SERVICE_TIER_OBJECT_KEY_FIELDS = ("slug", "name", "id", "tier")


def _union_string_tiers(primary: JsonValue, secondary: JsonValue) -> list[JsonValue] | None:
    merged: list[JsonValue] = []
    seen: set[str] = set()
    for source in (primary, secondary):
        if isinstance(source, list):
            for item in source:
                if not isinstance(item, str):
                    continue
                identity = _canonical_service_tier_value(item)
                if identity not in seen:
                    seen.add(identity)
                    merged.append(item)
    return merged or None


def _service_tier_identity(entry: JsonValue) -> str:
    if isinstance(entry, dict):
        identities: list[str] = []
        for key in _SERVICE_TIER_OBJECT_KEY_FIELDS:
            value = entry.get(key)
            if isinstance(value, str) and value:
                canonical = _canonical_service_tier_value(value)
                if canonical:
                    identities.append(canonical)
        if "priority" in identities:
            return "tier:priority"
        if identities:
            return f"tier:{identities[0]}"
    return json.dumps(entry, sort_keys=True, default=str)


def _canonical_service_tier_value(value: str) -> str:
    canonical = value.strip().lower()
    if canonical in {"fast", "priority"}:
        return "priority"
    return canonical


def _union_object_tiers(primary: JsonValue, secondary: JsonValue) -> list[JsonValue] | None:
    merged: list[JsonValue] = []
    seen: set[str] = set()
    for source in (primary, secondary):
        if isinstance(source, list):
            for item in source:
                identity = _service_tier_identity(item)
                if identity not in seen:
                    seen.add(identity)
                    merged.append(item)
    return merged or None


def _merge_default_service_tier(primary: JsonValue, secondary: JsonValue) -> str | None:
    if not isinstance(primary, str) or not primary:
        return secondary if isinstance(secondary, str) and secondary else None
    if not isinstance(secondary, str) or not secondary:
        return primary
    if primary == secondary:
        return primary

    plain_defaults = {"auto", "default"}
    if primary in plain_defaults and secondary not in plain_defaults:
        return secondary
    return primary


def _model_service_tier_keys(model: UpstreamModel) -> frozenset[str]:
    tiers: set[str] = set()

    def add_tier(value: str) -> None:
        tiers.add(value)
        canonical = _canonical_service_tier_value(value)
        if canonical:
            tiers.add(canonical)

    additional_speed_tiers = model.raw.get("additional_speed_tiers")
    if isinstance(additional_speed_tiers, list):
        for item in additional_speed_tiers:
            if isinstance(item, str) and item:
                add_tier(item)

    service_tiers = model.raw.get("service_tiers")
    if isinstance(service_tiers, list):
        for item in service_tiers:
            if not isinstance(item, dict):
                continue
            for key in _SERVICE_TIER_OBJECT_KEY_FIELDS:
                value = item.get(key)
                if isinstance(value, str) and value:
                    add_tier(value)

    default_service_tier = model.raw.get("default_service_tier")
    if isinstance(default_service_tier, str) and default_service_tier:
        add_tier(default_service_tier)

    return frozenset(tiers)


def _merge_service_tier_metadata(existing: UpstreamModel, incoming: UpstreamModel) -> UpstreamModel:
    """Combine two same-slug models so the speed/service-tier metadata is the
    union of both. ``incoming`` stays the base (last-writer-wins for every other
    field); only the tier fields aggregate, so an account without Fast cannot
    strip Fast contributed by another account (issue #1100)."""
    merged_raw = dict(incoming.raw)

    speed_tiers = _union_string_tiers(
        incoming.raw.get("additional_speed_tiers"),
        existing.raw.get("additional_speed_tiers"),
    )
    if speed_tiers is not None:
        merged_raw["additional_speed_tiers"] = speed_tiers

    service_tiers = _union_object_tiers(
        incoming.raw.get("service_tiers"),
        existing.raw.get("service_tiers"),
    )
    if service_tiers is not None:
        merged_raw["service_tiers"] = service_tiers

    default_service_tier = _merge_default_service_tier(
        incoming.raw.get("default_service_tier"),
        existing.raw.get("default_service_tier"),
    )
    if default_service_tier is not None:
        merged_raw["default_service_tier"] = default_service_tier

    if merged_raw == incoming.raw:
        return incoming
    return replace(incoming, raw=merged_raw)


class ModelRegistry:
    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._snapshot: ModelRegistrySnapshot | None = None
        self._bootstrap_models: dict[str, UpstreamModel] = {m.slug: m for m in _BOOTSTRAP_STATIC_MODELS}
        self._lock = anyio.Lock()

    def get_snapshot(self) -> ModelRegistrySnapshot | None:
        return self._snapshot

    def get_models_with_fallback(self) -> dict[str, UpstreamModel]:
        snapshot = self._snapshot
        if snapshot is not None:
            return snapshot.models
        return self._bootstrap_models

    def plan_types_for_model(self, slug: str) -> frozenset[str] | None:
        normalized_slug = slug.strip().lower()
        if self._snapshot is None:
            model = self._bootstrap_models.get(slug) or self._bootstrap_models.get(normalized_slug)
            return model.available_in_plans if model is not None else None
        return self._snapshot.model_plans.get(slug) or self._snapshot.model_plans.get(normalized_slug, frozenset())

    def plan_types_for_model_service_tier(self, slug: str, service_tier: str | None) -> frozenset[str] | None:
        if service_tier is None:
            return self.plan_types_for_model(slug)
        normalized_slug = slug.strip().lower()
        normalized_service_tier = service_tier.strip()
        if not normalized_slug or not normalized_service_tier:
            return self.plan_types_for_model(slug)
        if normalized_service_tier == "fast":
            normalized_service_tier = "priority"

        if self._snapshot is None:
            return self.plan_types_for_model(slug)

        tier_plans = self._snapshot.model_service_tier_plans.get(slug) or self._snapshot.model_service_tier_plans.get(
            normalized_slug
        )
        if tier_plans is None:
            return self.plan_types_for_model(slug)
        return tier_plans.get(normalized_service_tier, frozenset())

    def account_ids_for_model_service_tier(self, slug: str, service_tier: str | None) -> frozenset[str] | None:
        if service_tier is None or self._snapshot is None:
            return None
        normalized_slug = slug.strip().lower()
        normalized_service_tier = _canonical_service_tier_value(service_tier)
        if not normalized_slug or not normalized_service_tier:
            return None

        tier_accounts = self._snapshot.model_service_tier_accounts.get(
            slug
        ) or self._snapshot.model_service_tier_accounts.get(normalized_slug)
        if tier_accounts is None:
            return None
        return tier_accounts.get(normalized_service_tier, frozenset())

    def prefers_websockets(self, slug: str | None) -> bool:
        if not isinstance(slug, str):
            return False
        normalized_slug = slug.strip().lower()
        if not normalized_slug:
            return False

        if self._snapshot is not None:
            model = self._snapshot.models.get(slug) or self._snapshot.models.get(normalized_slug)
            if model is not None:
                return model.prefer_websockets
            return False

        bootstrap_model = self._bootstrap_models.get(slug) or self._bootstrap_models.get(normalized_slug)
        if bootstrap_model is not None:
            return bootstrap_model.prefer_websockets

        return any(fnmatchcase(normalized_slug, pattern) for pattern in _BOOTSTRAP_WEBSOCKET_PREFERRED_MODEL_PATTERNS)

    def needs_refresh(self) -> bool:
        if self._snapshot is None:
            return True
        return (time.monotonic() - self._snapshot.fetched_at) >= self._ttl_seconds

    async def update(
        self,
        per_plan_results: dict[str, list[UpstreamModel]],
        *,
        per_account_results: dict[str, tuple[str, list[UpstreamModel]]] | None = None,
        active_account_plans: dict[str, str] | None = None,
    ) -> None:
        if not per_plan_results:
            logger.warning("Model registry refresh produced no plan results; keeping cached snapshot")
            return

        async with self._lock:
            previous = self._snapshot
            try:
                models: dict[str, UpstreamModel] = {}
                model_plans: dict[str, set[str]] = {}
                model_service_tier_plans: dict[str, dict[str, set[str]]] = {}
                model_service_tier_accounts: dict[str, dict[str, set[str]]] = {}
                account_plans: dict[str, str] = {}

                # Carry over data from plans not present in per_plan_results
                if previous is not None:
                    previous_plans = set(previous.plan_models.keys())
                    refreshed_plans = set(per_plan_results.keys())
                    stale_plans = previous_plans - refreshed_plans
                    stale_account_ids = {
                        account_id
                        for account_id, plan_type in previous.account_plans.items()
                        if plan_type in stale_plans
                    }
                    if active_account_plans is not None:
                        refreshed_account_ids = set(per_account_results or {})
                        stale_account_ids.update(
                            account_id
                            for account_id in previous.account_plans
                            if account_id in active_account_plans and account_id not in refreshed_account_ids
                        )

                    for plan_type in stale_plans:
                        stale_slugs = previous.plan_models.get(plan_type, frozenset())
                        for slug in stale_slugs:
                            if slug not in models and slug in previous.models:
                                models[slug] = previous.models[slug]
                            model_plans.setdefault(slug, set()).add(plan_type)
                            stale_tier_plans = previous.model_service_tier_plans.get(slug, {})
                            for service_tier, plans in stale_tier_plans.items():
                                if plan_type in plans:
                                    model_service_tier_plans.setdefault(slug, {}).setdefault(service_tier, set()).add(
                                        plan_type
                                    )
                    for account_id in stale_account_ids:
                        plan_type = (
                            active_account_plans.get(account_id)
                            if active_account_plans is not None
                            else previous.account_plans.get(account_id)
                        )
                        if plan_type is not None:
                            account_plans[account_id] = plan_type
                    for slug, tier_accounts in previous.model_service_tier_accounts.items():
                        for service_tier, account_ids in tier_accounts.items():
                            stale_tier_accounts = account_ids & stale_account_ids
                            if stale_tier_accounts:
                                model_service_tier_accounts.setdefault(slug, {}).setdefault(service_tier, set()).update(
                                    stale_tier_accounts
                                )

                # Merge newly fetched results, aggregating service-tier metadata
                # across plans refreshed in this pass. Stale plan slugs still map
                # to the model, but their previous global union must not re-add a
                # tier that no current per-plan source has (issue #1106).
                refreshed_model_slugs: set[str] = set()
                for plan_type, plan_models_list in per_plan_results.items():
                    for model in plan_models_list:
                        existing = models.get(model.slug)
                        if existing is not None and model.slug in refreshed_model_slugs:
                            models[model.slug] = _merge_service_tier_metadata(existing, model)
                        else:
                            models[model.slug] = model
                        refreshed_model_slugs.add(model.slug)
                        model_plans.setdefault(model.slug, set()).add(plan_type)
                        for service_tier in _model_service_tier_keys(model):
                            model_service_tier_plans.setdefault(model.slug, {}).setdefault(service_tier, set()).add(
                                plan_type
                            )
                if per_account_results is not None:
                    for account_id, (plan_type, account_models) in per_account_results.items():
                        account_plans[account_id] = plan_type
                        for model in account_models:
                            for service_tier in _model_service_tier_keys(model):
                                model_service_tier_accounts.setdefault(model.slug, {}).setdefault(
                                    service_tier, set()
                                ).add(account_id)

                frozen_model_plans: dict[str, frozenset[str]] = {
                    slug: frozenset(plans) for slug, plans in model_plans.items()
                }
                frozen_model_service_tier_plans: dict[str, dict[str, frozenset[str]]] = {
                    slug: {service_tier: frozenset(plans) for service_tier, plans in tier_plans.items()}
                    for slug, tier_plans in model_service_tier_plans.items()
                }
                frozen_model_service_tier_accounts: dict[str, dict[str, frozenset[str]]] = {
                    slug: {service_tier: frozenset(account_ids) for service_tier, account_ids in tier_accounts.items()}
                    for slug, tier_accounts in model_service_tier_accounts.items()
                }

                # Build reverse index: plan_type -> set of slugs
                plan_models_index: dict[str, set[str]] = {}
                for slug, plans in frozen_model_plans.items():
                    for plan_type in plans:
                        plan_models_index.setdefault(plan_type, set()).add(slug)

                frozen_plan_models: dict[str, frozenset[str]] = {
                    plan_type: frozenset(slugs) for plan_type, slugs in plan_models_index.items()
                }

                self._snapshot = ModelRegistrySnapshot(
                    models=models,
                    model_plans=frozen_model_plans,
                    plan_models=frozen_plan_models,
                    model_service_tier_plans=frozen_model_service_tier_plans,
                    model_service_tier_accounts=frozen_model_service_tier_accounts,
                    account_plans=account_plans,
                    fetched_at=time.monotonic(),
                )
            except Exception:
                self._snapshot = previous
                logger.warning("Model registry refresh failed; keeping cached snapshot", exc_info=True)
                raise


_model_registry = ModelRegistry()


def get_model_registry() -> ModelRegistry:
    return _model_registry


def is_public_model(model: UpstreamModel, allowed_models: set[str] | None) -> bool:
    if not model.supported_in_api:
        return False
    if allowed_models is None:
        return True
    return model.slug in allowed_models
