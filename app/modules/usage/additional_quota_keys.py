from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")


def _normalize_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _NORMALIZE_PATTERN.sub("_", value.strip().lower()).strip("_")
    return normalized or None


@dataclass(frozen=True, slots=True)
class AdditionalQuotaDefinition:
    quota_key: str
    display_label: str
    model_ids: frozenset[str] = frozenset()
    limit_name_aliases: frozenset[str] = frozenset()
    metered_feature_aliases: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AdditionalQuotaRegistryStatus:
    path: Path
    definition_count: int


class AdditionalQuotaRegistryEntry(TypedDict, total=False):
    quota_key: str
    display_label: str
    model_ids: list[str]
    limit_name_aliases: list[str]
    metered_feature_aliases: list[str]


def _default_registry_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "additional_quota_registry.json"


def _registry_path() -> Path:
    configured = os.environ.get("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return _default_registry_path()


def _definition_from_json(item: AdditionalQuotaRegistryEntry) -> AdditionalQuotaDefinition:
    quota_key = str(item["quota_key"]).strip()
    display_label = str(item["display_label"]).strip()
    model_ids = frozenset(
        normalized
        for normalized in (_normalize_identifier(str(value)) for value in item.get("model_ids", []))
        if normalized is not None
    )
    limit_name_aliases = frozenset(
        normalized
        for normalized in (_normalize_identifier(str(value)) for value in item.get("limit_name_aliases", []))
        if normalized is not None
    )
    metered_feature_aliases = frozenset(
        normalized
        for normalized in (_normalize_identifier(str(value)) for value in item.get("metered_feature_aliases", []))
        if normalized is not None
    )
    return AdditionalQuotaDefinition(
        quota_key=quota_key,
        display_label=display_label,
        model_ids=model_ids,
        limit_name_aliases=limit_name_aliases,
        metered_feature_aliases=metered_feature_aliases,
    )


@lru_cache(maxsize=8)
def _definitions_for_path(path_str: str) -> tuple[AdditionalQuotaDefinition, ...]:
    path = Path(path_str)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"additional quota registry must be a list: {path}")
    return tuple(_definition_from_json(item) for item in data if isinstance(item, dict))


@lru_cache(maxsize=8)
def _definition_maps_for_path(
    path_str: str,
) -> tuple[
    dict[str, AdditionalQuotaDefinition],
    dict[str, str],
    dict[str, AdditionalQuotaDefinition],
    dict[str, str],
]:
    definitions = _definitions_for_path(path_str)
    by_quota_key: dict[str, AdditionalQuotaDefinition] = {}
    model_to_quota_key: dict[str, str] = {}
    model_to_definition: dict[str, AdditionalQuotaDefinition] = {}
    alias_to_quota_key: dict[str, str] = {}

    for definition in definitions:
        normalized_quota_key = _normalize_identifier(definition.quota_key)
        if normalized_quota_key is None:
            raise ValueError(f"invalid additional quota_key in registry: {definition.quota_key!r}")
        previous_quota = by_quota_key.get(normalized_quota_key)
        if previous_quota is not None:
            raise ValueError(
                "duplicate additional quota_key in registry: "
                f"{definition.quota_key!r} conflicts with {previous_quota.quota_key!r}"
            )
        by_quota_key[normalized_quota_key] = definition

        for model_id in definition.model_ids:
            previous_model = model_to_quota_key.get(model_id)
            if previous_model is not None and previous_model != definition.quota_key:
                raise ValueError(
                    "duplicate additional quota model mapping in registry: "
                    f"{model_id!r} -> {previous_model!r}/{definition.quota_key!r}"
                )
            model_to_quota_key[model_id] = definition.quota_key
            model_to_definition[model_id] = definition

        for alias in (*definition.limit_name_aliases, *definition.metered_feature_aliases):
            previous_alias = alias_to_quota_key.get(alias)
            if previous_alias is not None and previous_alias != definition.quota_key:
                raise ValueError(
                    "duplicate additional quota alias in registry: "
                    f"{alias!r} -> {previous_alias!r}/{definition.quota_key!r}"
                )
            alias_to_quota_key[alias] = definition.quota_key

    return by_quota_key, model_to_quota_key, model_to_definition, alias_to_quota_key


def clear_additional_quota_registry_cache() -> None:
    _definitions_for_path.cache_clear()
    _definition_maps_for_path.cache_clear()


def reload_additional_quota_registry() -> AdditionalQuotaRegistryStatus:
    clear_additional_quota_registry_cache()
    path_str = str(_registry_path())
    definitions = _definitions_for_path(path_str)
    _definition_maps_for_path(path_str)
    return AdditionalQuotaRegistryStatus(
        path=Path(path_str),
        definition_count=len(definitions),
    )


def canonicalize_additional_quota_key(
    *,
    model: str | None = None,
    limit_name: str | None = None,
    metered_feature: str | None = None,
) -> str | None:
    _, model_to_quota_key, _, alias_to_quota_key = _definition_maps_for_path(str(_registry_path()))

    model_key = _normalize_identifier(model)
    if model_key is not None:
        resolved = model_to_quota_key.get(model_key)
        if resolved is not None:
            return resolved

    for candidate in (limit_name, metered_feature):
        normalized = _normalize_identifier(candidate)
        if normalized is None:
            continue
        resolved = alias_to_quota_key.get(normalized)
        if resolved is not None:
            return resolved

    return _normalize_identifier(limit_name) or _normalize_identifier(metered_feature)


def get_additional_quota_key_for_model(model: str | None) -> str | None:
    return canonicalize_additional_quota_key(model=model)


def get_additional_quota_definition_for_model(model: str | None) -> AdditionalQuotaDefinition | None:
    _, _, model_to_definition, _ = _definition_maps_for_path(str(_registry_path()))
    normalized = _normalize_identifier(model)
    if normalized is None:
        return None
    return model_to_definition.get(normalized)


def get_additional_display_label_for_quota_key(quota_key: str | None) -> str | None:
    by_quota_key, _, _, _ = _definition_maps_for_path(str(_registry_path()))
    normalized = _normalize_identifier(quota_key)
    if normalized is None:
        return None
    definition = by_quota_key.get(normalized)
    return definition.display_label if definition is not None else None


def get_additional_display_label(
    *,
    quota_key: str | None = None,
    limit_name: str | None = None,
    metered_feature: str | None = None,
) -> str | None:
    resolved_key = canonicalize_additional_quota_key(
        limit_name=limit_name,
        metered_feature=metered_feature,
    )
    return get_additional_display_label_for_quota_key(quota_key or resolved_key)
