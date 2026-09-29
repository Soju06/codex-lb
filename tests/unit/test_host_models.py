from __future__ import annotations

from types import SimpleNamespace

from app.core.openai import host_models


def test_image_host_prefers_image_capable_current_model(monkeypatch) -> None:
    registry = SimpleNamespace(
        plan_types_for_model=lambda model: {"gpt-5.6-sol"} if model == "gpt-5.6-sol" else set(),
        is_suppressed_model=lambda model: False,
    )
    monkeypatch.setattr(host_models, "get_model_registry", lambda: registry)

    assert host_models.resolve_image_host_model() == "gpt-5.6-sol"


def test_image_host_falls_back_when_preferred_model_is_unavailable(monkeypatch) -> None:
    registry = SimpleNamespace(
        plan_types_for_model=lambda model: {"available"} if model == "gpt-6-astra" else set(),
        is_suppressed_model=lambda model: False,
    )
    monkeypatch.setattr(host_models, "get_model_registry", lambda: registry)

    assert host_models.resolve_image_host_model() == "gpt-6-astra"
