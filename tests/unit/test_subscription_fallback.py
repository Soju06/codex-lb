from __future__ import annotations

import pytest

from app.db.models import ModelSourceModel
from app.modules.model_sources.service import ModelSourceValidationError, _validate_fallback_configuration
from app.modules.proxy.subscription_fallback import (
    usage_limit_reservation_transfer,
    usage_limit_reservation_transfer_enabled,
)


def _model(name: str, *, enabled: bool = True) -> ModelSourceModel:
    return ModelSourceModel(model=name, is_enabled=enabled)


def test_usage_limit_reservation_transfer_is_scoped() -> None:
    assert usage_limit_reservation_transfer_enabled() is False
    with usage_limit_reservation_transfer(True):
        assert usage_limit_reservation_transfer_enabled() is True
    assert usage_limit_reservation_transfer_enabled() is False


def test_fallback_requires_responses_capability() -> None:
    with pytest.raises(ModelSourceValidationError, match="Responses API"):
        _validate_fallback_configuration(
            is_subscription_fallback=True,
            is_enabled=True,
            supports_responses=False,
            fallback_model=None,
            models=[_model("gpt-5")],
        )


def test_fallback_model_override_must_exist_and_be_enabled() -> None:
    with pytest.raises(ModelSourceValidationError, match="must be an enabled model"):
        _validate_fallback_configuration(
            is_subscription_fallback=True,
            is_enabled=True,
            supports_responses=True,
            fallback_model="missing",
            models=[_model("present")],
        )

    _validate_fallback_configuration(
        is_subscription_fallback=True,
        is_enabled=True,
        supports_responses=True,
        fallback_model="present",
        models=[_model("present")],
    )
