from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdditionalModelLimit:
    model: str
    limit_name: str
    display_label: str


_ADDITIONAL_MODEL_LIMITS: dict[str, AdditionalModelLimit] = {
    "gpt-5.3-codex-spark": AdditionalModelLimit(
        model="gpt-5.3-codex-spark",
        limit_name="GPT-5.3-Codex-Spark",
        display_label="GPT-5.3-Codex-Spark",
    ),
}


def get_additional_model_limit(model: str | None) -> AdditionalModelLimit | None:
    if model is None:
        return None
    return _ADDITIONAL_MODEL_LIMITS.get(model.strip().lower())


def get_additional_limit_name_for_model(model: str | None) -> str | None:
    resolved = get_additional_model_limit(model)
    return resolved.limit_name if resolved is not None else None


def get_additional_display_label_for_model(model: str | None) -> str | None:
    resolved = get_additional_model_limit(model)
    return resolved.display_label if resolved is not None else None
