from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
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
        supported_in_api=False,
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

    async def update(self, per_plan_results: dict[str, list[UpstreamModel]]) -> None:
        if not per_plan_results:
            logger.warning("Model registry refresh produced no plan results; keeping cached snapshot")
            return

        async with self._lock:
            previous = self._snapshot
            try:
                models: dict[str, UpstreamModel] = {}
                model_plans: dict[str, set[str]] = {}

                # Carry over data from plans not present in per_plan_results
                if previous is not None:
                    previous_plans = set(previous.plan_models.keys())
                    refreshed_plans = set(per_plan_results.keys())
                    stale_plans = previous_plans - refreshed_plans

                    for plan_type in stale_plans:
                        stale_slugs = previous.plan_models.get(plan_type, frozenset())
                        for slug in stale_slugs:
                            if slug not in models and slug in previous.models:
                                models[slug] = previous.models[slug]
                            model_plans.setdefault(slug, set()).add(plan_type)

                # Merge newly fetched results
                for plan_type, plan_models_list in per_plan_results.items():
                    for model in plan_models_list:
                        models[model.slug] = model
                        model_plans.setdefault(model.slug, set()).add(plan_type)

                frozen_model_plans: dict[str, frozenset[str]] = {
                    slug: frozenset(plans) for slug, plans in model_plans.items()
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
    if allowed_models is None:
        return True
    return model.slug in allowed_models
