from __future__ import annotations

from dataclasses import dataclass

from app.modules.usage.additional_quota_keys import (
    get_additional_display_label_for_quota_key,
    get_additional_quota_definition_for_model,
    get_additional_quota_key_for_model,
)


@dataclass(frozen=True, slots=True)
class AdditionalModelLimit:
    model: str
    quota_key: str
    display_label: str


def get_additional_model_limit(model: str | None) -> AdditionalModelLimit | None:
    resolved = get_additional_quota_definition_for_model(model)
    if resolved is None:
        return None
    normalized_model = model.strip().lower() if model is not None else None
    if normalized_model is None:
        return None
    return AdditionalModelLimit(
        model=normalized_model,
        quota_key=resolved.quota_key,
        display_label=resolved.display_label,
    )


def get_additional_quota_key_for_model_id(model: str | None) -> str | None:
    resolved = get_additional_model_limit(model)
    return resolved.quota_key if resolved is not None else None


def get_additional_limit_name_for_model(model: str | None) -> str | None:
    return get_additional_quota_key_for_model_id(model)


def get_additional_display_label_for_model(model: str | None) -> str | None:
    quota_key = get_additional_quota_key_for_model(model)
    return get_additional_display_label_for_quota_key(quota_key)
