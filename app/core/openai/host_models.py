"""Internal Responses model selection for images and default account probes."""

from app.core.openai.model_registry import get_model_registry

_HOST_MODEL_CANDIDATES = ("gpt-5.6-luna", "gpt-5.5")


def resolve_default_host_model() -> str:
    """Prefer a visible current host; catalog visibility is not account entitlement."""
    registry = get_model_registry()
    for slug in _HOST_MODEL_CANDIDATES:
        if registry.plan_types_for_model(slug) and not registry.is_suppressed_model(slug):
            return slug
    return _HOST_MODEL_CANDIDATES[0]
