from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.types import JsonValue


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
    raw: dict[str, JsonValue] = field(hash=False, compare=False)


@dataclass
class ModelRegistrySnapshot:
    models: dict[str, UpstreamModel]
    model_plans: dict[str, frozenset[str]]
    plan_models: dict[str, frozenset[str]]
    fetched_at: float


class ModelRegistry:
    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._snapshot: ModelRegistrySnapshot | None = None

    def get_snapshot(self) -> ModelRegistrySnapshot | None:
        return self._snapshot

    def plan_types_for_model(self, slug: str) -> frozenset[str] | None:
        if self._snapshot is None:
            return None
        return self._snapshot.model_plans.get(slug, frozenset())

    def needs_refresh(self) -> bool:
        if self._snapshot is None:
            return True
        return (time.monotonic() - self._snapshot.fetched_at) >= self._ttl_seconds

    def update(self, per_plan_results: dict[str, list[UpstreamModel]]) -> None:
        if not per_plan_results:
            return

        previous = self._snapshot
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

        frozen_model_plans: dict[str, frozenset[str]] = {slug: frozenset(plans) for slug, plans in model_plans.items()}

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


_model_registry = ModelRegistry()


def get_model_registry() -> ModelRegistry:
    return _model_registry


def is_public_model(model: UpstreamModel, allowed_models: set[str] | None) -> bool:
    if not model.supported_in_api:
        return False
    if allowed_models is None:
        return True
    return model.slug in allowed_models
