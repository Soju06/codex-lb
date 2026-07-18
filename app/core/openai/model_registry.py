from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase

import anyio

from app.core.types import JsonValue

logger = logging.getLogger(__name__)

MODEL_SOURCE_KIND_SUBSCRIPTION = "subscription"
MODEL_SOURCE_KIND_OPENAI_COMPATIBLE = "openai_compatible"


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
    source_kind: str = MODEL_SOURCE_KIND_SUBSCRIPTION
    source_id: str | None = None
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
    model_accounts: dict[str, frozenset[str]] = field(default_factory=dict)
    account_catalogs_authoritative: bool = False
    bootstrap_floor_active: bool = False
    suppressed_model_slugs: frozenset[str] = field(default_factory=frozenset)


@dataclass(slots=True)
class ModelRegistryExport:
    """Complete registry state as exchanged with the persisted snapshot store.

    ``snapshot is None`` represents the cleared/bootstrap-floor state.
    """

    snapshot: ModelRegistrySnapshot | None
    metadata_models: dict[str, UpstreamModel] | None


_BOOTSTRAP_WEBSOCKET_PREFERRED_MODEL_PATTERNS = (
    "gpt-5.6-*",
    "gpt-5.5",
    "gpt-5.5-*",
    "gpt-5.4",
    "gpt-5.4-*",
)

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

_REASONING_LEVELS_MAX = (
    ReasoningLevel(effort="low", description="Fast responses with lighter reasoning"),
    ReasoningLevel(effort="medium", description="Balances speed and reasoning depth for everyday tasks"),
    ReasoningLevel(effort="high", description="Greater reasoning depth for complex problems"),
    ReasoningLevel(effort="xhigh", description="Extra high reasoning depth for complex problems"),
    ReasoningLevel(effort="max", description="Maximum reasoning depth for the hardest problems"),
)

_REASONING_LEVELS_ULTRA = (
    *_REASONING_LEVELS_MAX,
    ReasoningLevel(effort="ultra", description="Maximum reasoning with automatic task delegation"),
)

_BOOTSTRAP_FAST_SERVICE_TIERS: list[JsonValue] = [
    {
        "id": "priority",
        "name": "Fast",
        "description": "1.5x speed, increased usage",
    }
]

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

# GPT-5.6 ships to four additional plan tiers upstream
# (codex-rs/models-manager/models.json at rust-v0.144.1).
_BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS = frozenset(
    {
        *_BOOTSTRAP_AVAILABLE_IN_PLANS,
        "edu_plus",
        "edu_pro",
        "enterprise_cbp_automation",
        "sci",
    }
)

_GPT56_SOL_AVAILABILITY_NUX: dict[str, JsonValue] = {
    "message": (
        "Our most capable model yet. GPT-5.6 Sol can tackle complex code changes, "
        "dig into research, produce polished documents, and take on your most "
        "ambitious work. Sol is highly capable at lower reasoning efforts—try "
        "starting lower, then turn it up for harder jobs."
    )
}


def _bootstrap_model(
    slug: str,
    display_name: str,
    *,
    prefer_websockets: bool,
    minimal_client_version: str | None,
    description: str | None = None,
    reasoning_levels: tuple[ReasoningLevel, ...] = _REASONING_LEVELS_EXTENDED,
    context_window: int = 272_000,
    input_modalities: tuple[str, ...] = ("text", "image"),
    default_reasoning_level: str | None = "medium",
    default_verbosity: str | None = "low",
    supported_in_api: bool = True,
    available_in_plans: frozenset[str] = _BOOTSTRAP_AVAILABLE_IN_PLANS,
    visibility: str = "list",
    shell_type: str = "shell_command",
    priority: int = 0,
    raw: dict[str, JsonValue] | None = None,
) -> UpstreamModel:
    raw_fields: dict[str, JsonValue] = {
        "shell_type": shell_type,
        "visibility": visibility,
        "availability_nux": None,
        "max_context_window": context_window,
        "truncation_policy": {"mode": "tokens", "limit": 10_000},
        "experimental_supported_tools": [],
    }
    if raw:
        raw_fields.update(raw)
    return UpstreamModel(
        slug=slug,
        display_name=display_name,
        description=description or display_name,
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
        priority=priority,
        available_in_plans=available_in_plans,
        raw=raw_fields,
    )


def _gpt56_raw(
    *,
    multi_agent_version: str,
    availability_nux: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """Raw catalog fields for the GPT-5.6 family, mirroring the upstream
    bundled catalog (codex-rs/models-manager/models.json at rust-v0.144.1)
    field-for-field. The ~16.5 KB ``base_instructions`` string and the
    personality-templated ``model_messages`` object are deliberately not
    bundled; the live upstream registry supplies them on the first refresh.
    """
    return {
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "supports_image_detail_original": True,
        "truncation_policy": {"mode": "tokens", "limit": 10_000},
        # ``tool_mode`` / ``multi_agent_version`` drive Codex client tool
        # assembly and the ``ultra`` proactive multi-agent mode; omitting them
        # would make a bootstrap-served catalog change client behavior.
        "tool_mode": "code_mode_only",
        "multi_agent_version": multi_agent_version,
        "use_responses_lite": True,
        "include_skills_usage_instructions": False,
        "auto_review_model_override": None,
        "auto_compact_token_limit": None,
        "comp_hash": "3000",
        "reasoning_summary_format": "experimental",
        "default_reasoning_summary": "none",
        "availability_nux": availability_nux,
        "upgrade": None,
        # Required (no serde default) by the Rust ``ModelInfo`` deserializer;
        # a Codex client rejects catalog entries that omit it.
        "experimental_supported_tools": [],
        "supports_search_tool": True,
        "default_service_tier": None,
        "service_tiers": _BOOTSTRAP_FAST_SERVICE_TIERS,
        "additional_speed_tiers": ["fast"],
    }


# Static bundled fallback models used before the first upstream registry refresh.
# This mirrors Codex's model-manager pattern: ship a conservative catalog so
# startup/offline paths have usable metadata, then treat the live upstream
# registry as authoritative once a refresh succeeds. Keep compatibility fields
# explicit rather than inherited from helper defaults; every slug must be a
# real upstream slug (never invented), and live upstream data always takes
# precedence once available. ``gpt-5.3-codex`` / ``gpt-5.3-codex-spark`` were
# dropped from upstream's bundled catalog at rust-v0.144.x but are retained
# here for older pinned clients; the upstream backend still serves them.
_BOOTSTRAP_STATIC_MODELS: tuple[UpstreamModel, ...] = (
    _bootstrap_model(
        "gpt-5.6-sol",
        "GPT-5.6-Sol",
        description="Latest frontier agentic coding model.",
        prefer_websockets=True,
        minimal_client_version="0.144.0",
        reasoning_levels=_REASONING_LEVELS_ULTRA,
        context_window=372_000,
        default_reasoning_level="low",
        priority=1,
        available_in_plans=_BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS,
        raw=_gpt56_raw(multi_agent_version="v2", availability_nux=_GPT56_SOL_AVAILABILITY_NUX),
    ),
    _bootstrap_model(
        "gpt-5.6-terra",
        "GPT-5.6-Terra",
        description="Balanced agentic coding model for everyday work.",
        prefer_websockets=True,
        minimal_client_version="0.144.0",
        reasoning_levels=_REASONING_LEVELS_ULTRA,
        context_window=372_000,
        default_reasoning_level="medium",
        priority=2,
        available_in_plans=_BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS,
        raw=_gpt56_raw(multi_agent_version="v2"),
    ),
    _bootstrap_model(
        "gpt-5.6-luna",
        "GPT-5.6-Luna",
        description="Fast and affordable agentic coding model.",
        prefer_websockets=True,
        minimal_client_version="0.144.0",
        reasoning_levels=_REASONING_LEVELS_MAX,
        context_window=372_000,
        default_reasoning_level="medium",
        priority=3,
        available_in_plans=_BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS,
        raw=_gpt56_raw(multi_agent_version="v1"),
    ),
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
        raw={"truncation_policy": {"mode": "bytes", "limit": 10_000}},
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
                identity = canonical_service_tier_value(item)
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
                canonical = canonical_service_tier_value(value)
                if canonical:
                    identities.append(canonical)
        if "priority" in identities:
            return "tier:priority"
        if identities:
            return f"tier:{identities[0]}"
    return json.dumps(entry, sort_keys=True, default=str)


def canonical_service_tier_value(value: str) -> str:
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
        canonical = canonical_service_tier_value(value)
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
        self._metadata_models: dict[str, UpstreamModel] | None = None
        self._applied_content_hash: str | None = None
        self._lock = anyio.Lock()

    @property
    def applied_content_hash(self) -> str | None:
        """Content hash of the persisted snapshot this registry last applied or persisted."""
        return self._applied_content_hash

    def note_applied_content_hash(self, content_hash: str | None) -> None:
        self._applied_content_hash = content_hash

    async def export_state(self) -> ModelRegistryExport:
        async with self._lock:
            return ModelRegistryExport(snapshot=self._snapshot, metadata_models=self._metadata_models)

    async def import_state(self, state: ModelRegistryExport, *, content_hash: str) -> None:
        """Replace the registry state with a decoded persisted snapshot."""
        async with self._lock:
            self._snapshot = state.snapshot
            self._metadata_models = state.metadata_models
            self._applied_content_hash = content_hash

    def get_snapshot(self) -> ModelRegistrySnapshot | None:
        return self._snapshot

    def get_models_with_fallback(self) -> dict[str, UpstreamModel]:
        snapshot = self._snapshot
        if snapshot is not None:
            if snapshot.bootstrap_floor_active:
                models = dict(self._bootstrap_models)
                for slug in snapshot.suppressed_model_slugs:
                    models.pop(slug, None)
                models.update(snapshot.models)
                return models
            return snapshot.models
        return self._bootstrap_models

    def get_models_for_metadata(self) -> dict[str, UpstreamModel]:
        if self._metadata_models is not None:
            return self._metadata_models
        return self._bootstrap_models

    def plan_types_for_model(self, slug: str) -> frozenset[str] | None:
        normalized_slug = slug.strip().lower()
        bootstrap_model = self._bootstrap_models.get(slug) or self._bootstrap_models.get(normalized_slug)
        if self._snapshot is None:
            return bootstrap_model.available_in_plans if bootstrap_model is not None else None
        if slug in self._snapshot.model_plans:
            return self._snapshot.model_plans[slug]
        if normalized_slug in self._snapshot.model_plans:
            return self._snapshot.model_plans[normalized_slug]
        if (
            self._snapshot.bootstrap_floor_active
            and bootstrap_model is not None
            and normalized_slug not in self._snapshot.suppressed_model_slugs
        ):
            return bootstrap_model.available_in_plans
        snapshot_plans = frozenset()
        return snapshot_plans

    def is_suppressed_model(self, slug: str) -> bool:
        """Whether catalog evidence explicitly withdrew a previously known slug.

        This differs from an operator-mapped slug simply absent from catalog
        discovery: that slug keeps the existing unfiltered fallback, whereas a
        suppressed model must not select any account.
        """
        snapshot = self._snapshot
        if snapshot is None:
            return False
        return slug.strip().lower() in snapshot.suppressed_model_slugs

    def plan_types_for_model_service_tier(self, slug: str, service_tier: str | None) -> frozenset[str] | None:
        if service_tier is None:
            return self.plan_types_for_model(slug)
        normalized_slug = slug.strip().lower()
        normalized_service_tier = canonical_service_tier_value(service_tier)
        if not normalized_slug or not normalized_service_tier:
            return self.plan_types_for_model(slug)

        if self._snapshot is None:
            return self.plan_types_for_model(slug)
        if not self._snapshot.account_catalogs_authoritative and self._snapshot.account_plans:
            return self.plan_types_for_model(slug)

        tier_plans = self._snapshot.model_service_tier_plans.get(slug) or self._snapshot.model_service_tier_plans.get(
            normalized_slug
        )
        if tier_plans is None:
            return frozenset() if self._snapshot.account_catalogs_authoritative else self.plan_types_for_model(slug)
        return tier_plans.get(normalized_service_tier, frozenset())

    def account_ids_for_model_service_tier(self, slug: str, service_tier: str | None) -> frozenset[str] | None:
        if service_tier is None or self._snapshot is None:
            return None
        if not self._snapshot.account_catalogs_authoritative:
            return None
        normalized_slug = slug.strip().lower()
        normalized_service_tier = canonical_service_tier_value(service_tier)
        if not normalized_slug or not normalized_service_tier:
            return None

        tier_accounts = self._snapshot.model_service_tier_accounts.get(
            slug
        ) or self._snapshot.model_service_tier_accounts.get(normalized_slug)
        if tier_accounts is None:
            if self._snapshot.account_catalogs_authoritative:
                return frozenset()
            return None
        return tier_accounts.get(normalized_service_tier, frozenset())

    def account_ids_for_model(self, slug: str) -> frozenset[str] | None:
        """Return exact supporting accounts, or ``None`` while coverage is incomplete."""
        if self._snapshot is None or not self._snapshot.account_catalogs_authoritative:
            return None
        normalized_slug = slug.strip().lower()
        if not normalized_slug:
            return None
        return self._snapshot.model_accounts.get(slug) or self._snapshot.model_accounts.get(
            normalized_slug, frozenset()
        )

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
            if self._snapshot.bootstrap_floor_active and normalized_slug not in self._snapshot.suppressed_model_slugs:
                bootstrap_model = self._bootstrap_models.get(slug) or self._bootstrap_models.get(normalized_slug)
                if bootstrap_model is not None:
                    return bootstrap_model.prefer_websockets
            return False

        bootstrap_model = self._bootstrap_models.get(slug) or self._bootstrap_models.get(normalized_slug)
        if bootstrap_model is not None:
            return bootstrap_model.prefer_websockets

        return any(fnmatchcase(normalized_slug, pattern) for pattern in _BOOTSTRAP_WEBSOCKET_PREFERRED_MODEL_PATTERNS)

    def needs_refresh(self) -> bool:
        if self._snapshot is None:
            return True
        return (time.monotonic() - self._snapshot.fetched_at) >= self._ttl_seconds

    async def clear(self) -> None:
        """Drop live capability state when no active accounts remain.

        This resets to the bootstrap-floor state (equivalent to never having
        refreshed): every reader falls back to the static bootstrap catalog. We
        must NOT publish an authoritative-empty snapshot here. If we did, then in
        the window after an account is added but before the next scheduled refresh
        tick, ``plan_types_for_model`` / ``get_models_with_fallback`` would report
        even canonical models as absent (because ``_snapshot`` is no longer
        ``None``), so ``_mapped_model_has_registry_entry`` would skip model/plan
        filtering and let an unsupported plan be selected, and ``/v1/models`` would
        stay empty until the timer fires. Bootstrap remains the discovery/gating
        floor whenever there is no authoritative account coverage.
        """
        async with self._lock:
            self._snapshot = None
            self._metadata_models = None
            self._applied_content_hash = None

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
                model_accounts: dict[str, set[str]] = {}
                account_plans: dict[str, str] = {}
                suppressed_model_slugs: set[str] = (
                    set(previous.suppressed_model_slugs) if previous is not None else set()
                )

                # Carry over data from plans not present in per_plan_results
                if previous is not None:
                    previous_plans = set(previous.plan_models.keys())
                    refreshed_plans = set(per_plan_results.keys())
                    stale_plans = previous_plans - refreshed_plans
                    if active_account_plans is not None:
                        stale_plans.intersection_update(active_account_plans.values())
                        stale_account_ids = {
                            account_id
                            for account_id, plan_type in previous.account_plans.items()
                            if account_id in active_account_plans
                            and plan_type in stale_plans
                            and active_account_plans[account_id] == plan_type
                        }
                        refreshed_account_ids = set(per_account_results or {})
                        stale_account_ids.update(
                            account_id
                            for account_id, previous_plan_type in previous.account_plans.items()
                            if account_id in active_account_plans
                            and account_id not in refreshed_account_ids
                            # A retained catalog proves capabilities only for the
                            # plan that produced it. A plan change without a fresh
                            # catalog leaves the active account unknown instead of
                            # re-labeling old entitlements as new-plan support.
                            and active_account_plans[account_id] == previous_plan_type
                        )
                    else:
                        stale_account_ids = {
                            account_id
                            for account_id, plan_type in previous.account_plans.items()
                            if plan_type in stale_plans
                        }

                    # Carryover invariant: never carry forward a stale-plan model
                    # that no currently-active account of that plan advertises, per
                    # the last-known per-account catalogs in ``previous.model_accounts``.
                    #
                    # This runs whenever we know the active account set
                    # (``active_account_plans`` is not None), REGARDLESS of whether the
                    # previous snapshot was authoritative. The authoritative flag governs
                    # whether per-account *routing* is trusted (see ``account_ids_for_model``),
                    # not whether a dead model is dropped from discovery: a first-refresh
                    # (non-authoritative) snapshot can still record that a private model
                    # was advertised only by an account that is now removed/paused, and
                    # that model must not linger in discovery once no active account serves it.
                    #
                    # An active account whose refresh transiently failed keeps its
                    # last-known models (it is in ``stale_account_ids`` and still appears
                    # in ``previous.model_accounts``). A model with NO per-account
                    # provenance at all (an older/plan-only snapshot that never captured
                    # per-account catalogs) is preserved rather than dropped, so we
                    # degrade safe when we cannot attribute a model to any account.
                    supported_stale_slugs: dict[str, set[str]] | None = None
                    if active_account_plans is not None:
                        supported_stale_slugs = {}
                        for slug, account_ids in previous.model_accounts.items():
                            for account_id in account_ids:
                                if account_id not in stale_account_ids:
                                    continue
                                plan_of_account = active_account_plans.get(
                                    account_id, previous.account_plans.get(account_id)
                                )
                                if plan_of_account in stale_plans:
                                    supported_stale_slugs.setdefault(plan_of_account, set()).add(slug)

                    for plan_type in stale_plans:
                        stale_slugs: Iterable[str] = previous.plan_models.get(plan_type, frozenset())
                        if supported_stale_slugs is not None:
                            supported = supported_stale_slugs.get(plan_type, set())
                            stale_slugs = [
                                slug
                                for slug in stale_slugs
                                # Unknown per-account provenance -> keep (degrade safe).
                                if not previous.model_accounts.get(slug)
                                # Known provenance -> keep only with an active advertiser.
                                or slug in supported
                            ]
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
                    for slug, account_ids in previous.model_accounts.items():
                        if slug not in previous.models:
                            continue
                        stale_model_accounts = account_ids & stale_account_ids
                        if not stale_model_accounts:
                            continue
                        model_accounts.setdefault(slug, set()).update(stale_model_accounts)
                        if slug in previous.models:
                            models.setdefault(slug, previous.models[slug])
                        for account_id in stale_model_accounts:
                            plan_type = account_plans.get(account_id)
                            if plan_type is not None:
                                model_plans.setdefault(slug, set()).add(plan_type)
                    for slug, tier_accounts in previous.model_service_tier_accounts.items():
                        if slug not in previous.models:
                            continue
                        for service_tier, account_ids in tier_accounts.items():
                            stale_tier_accounts = account_ids & stale_account_ids
                            if stale_tier_accounts:
                                model_service_tier_accounts.setdefault(slug, {}).setdefault(service_tier, set()).update(
                                    stale_tier_accounts
                                )
                                for account_id in stale_tier_accounts:
                                    plan_type = account_plans.get(account_id)
                                    if plan_type is not None:
                                        model_service_tier_plans.setdefault(slug, {}).setdefault(
                                            service_tier, set()
                                        ).add(plan_type)

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
                            if model.slug not in models:
                                continue
                            model_accounts.setdefault(model.slug, set()).add(account_id)
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
                frozen_model_accounts = {slug: frozenset(account_ids) for slug, account_ids in model_accounts.items()}
                authoritative_account_catalogs = per_account_results is not None and set(
                    active_account_plans or per_account_results
                ).issubset(account_plans)

                if authoritative_account_catalogs:
                    for slug in self._bootstrap_models:
                        if slug not in models:
                            suppressed_model_slugs.add(slug)
                    for _plan_type, account_models in (per_account_results or {}).values():
                        for model in account_models:
                            if model.slug not in models:
                                suppressed_model_slugs.add(model.slug)
                if previous is not None:
                    for slug, previous_account_ids in previous.model_accounts.items():
                        if slug in models or not previous_account_ids:
                            continue
                        suppressed_model_slugs.add(slug)
                for slug in tuple(suppressed_model_slugs):
                    if slug in models:
                        suppressed_model_slugs.discard(slug)

                # Build reverse index: plan_type -> set of slugs
                plan_models_index: dict[str, set[str]] = {}
                for slug, plans in frozen_model_plans.items():
                    for plan_type in plans:
                        plan_models_index.setdefault(plan_type, set()).add(slug)

                frozen_plan_models: dict[str, frozenset[str]] = {
                    plan_type: frozenset(slugs) for plan_type, slugs in plan_models_index.items()
                }
                bootstrap_floor_active = per_account_results is not None and not authoritative_account_catalogs

                metadata_models = {
                    slug: model
                    for slug, model in (self._metadata_models or self._bootstrap_models).items()
                    if slug in self._bootstrap_models
                }
                if per_account_results is not None:
                    account_metadata_models: dict[str, UpstreamModel] = {}
                    for _account_id, (_plan_type, account_models) in per_account_results.items():
                        for model in account_models:
                            if model.slug in self._bootstrap_models:
                                account_metadata_models.setdefault(model.slug, model)
                    metadata_models.update(account_metadata_models)
                metadata_models.update(models)
                self._snapshot = ModelRegistrySnapshot(
                    models=models,
                    model_plans=frozen_model_plans,
                    plan_models=frozen_plan_models,
                    model_service_tier_plans=frozen_model_service_tier_plans,
                    model_service_tier_accounts=frozen_model_service_tier_accounts,
                    account_plans=account_plans,
                    fetched_at=time.monotonic(),
                    model_accounts=frozen_model_accounts,
                    account_catalogs_authoritative=authoritative_account_catalogs,
                    bootstrap_floor_active=bootstrap_floor_active,
                    suppressed_model_slugs=frozenset(suppressed_model_slugs),
                )
                self._metadata_models = metadata_models
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
