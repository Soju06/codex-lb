from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.openai.models import OpenAIError, OpenAIErrorEnvelope
from app.core.openai.requests import ResponsesRequest
from app.db.models import ModelSourceModel
from app.modules.model_sources.service import ModelSourceValidationError, _validate_fallback_configuration
from app.modules.proxy._service.streaming import retry as retry_module
from app.modules.proxy._service.streaming.retry import _StreamingRetryMixin
from app.modules.proxy.api import _is_usage_limit_proxy_error, _source_has_fallback_model
from app.modules.proxy.subscription_fallback import (
    usage_limit_reservation_transfer,
    usage_limit_reservation_transfer_enabled,
)


def _model(name: str, *, enabled: bool = True) -> ModelSourceModel:
    return ModelSourceModel(model=name, is_enabled=enabled)


def _request(**overrides: Any) -> ResponsesRequest:
    data: dict[str, Any] = {
        "model": "gpt-5",
        "instructions": "Be concise.",
        "input": "hello",
    }
    data.update(overrides)
    return ResponsesRequest.model_validate(data)


def _error(*, code: str, error_type: str | None = None) -> OpenAIErrorEnvelope:
    return OpenAIErrorEnvelope(
        error=OpenAIError(
            message="test",
            type=error_type or code,
            code=code,
        )
    )


def test_usage_limit_reservation_transfer_is_scoped() -> None:
    assert usage_limit_reservation_transfer_enabled() is False
    with usage_limit_reservation_transfer(True):
        assert usage_limit_reservation_transfer_enabled() is True
    assert usage_limit_reservation_transfer_enabled() is False


def test_fallback_requires_enabled_source() -> None:
    with pytest.raises(ModelSourceValidationError, match="must be enabled"):
        _validate_fallback_configuration(
            is_subscription_fallback=True,
            is_enabled=False,
            supports_responses=True,
            fallback_model=None,
            models=[_model("gpt-5")],
        )


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


def test_usage_limit_trigger_is_exact() -> None:
    assert _is_usage_limit_proxy_error(_error(code="usage_limit_reached")) is True
    assert _is_usage_limit_proxy_error(_error(code="rate_limit_exceeded")) is False
    assert _is_usage_limit_proxy_error(_error(code="insufficient_quota")) is False
    assert _is_usage_limit_proxy_error(_error(code="local_account_cap")) is False


def test_fallback_model_must_be_enabled_and_streamable_when_required() -> None:
    source = cast(
        Any,
        SimpleNamespace(
            models=[
                SimpleNamespace(model="disabled", is_enabled=False, supports_streaming=True),
                SimpleNamespace(model="batch-only", is_enabled=True, supports_streaming=False),
                SimpleNamespace(model="streaming", is_enabled=True, supports_streaming=True),
            ]
        ),
    )

    assert _source_has_fallback_model(source, "disabled", require_streaming=False) is False
    assert _source_has_fallback_model(source, "batch-only", require_streaming=False) is True
    assert _source_has_fallback_model(source, "batch-only", require_streaming=True) is False
    assert _source_has_fallback_model(source, "streaming", require_streaming=True) is True


def test_fresh_account_neutral_request_is_replayable() -> None:
    mixin = _StreamingRetryMixin()
    payload = _request()

    replay = mixin.external_fallback_replay_payload(payload, {}, api_key=None)

    assert replay is not None
    assert replay.model == "gpt-5"
    assert replay.previous_response_id is None


def test_account_owned_conversation_is_not_replayable() -> None:
    mixin = _StreamingRetryMixin()

    replay = mixin.external_fallback_replay_payload(
        _request(conversation="conv_chatgpt_owned"),
        {},
        api_key=None,
    )

    assert replay is None


def test_file_pinned_request_is_not_replayable() -> None:
    mixin = _StreamingRetryMixin()
    payload = _request(
        input=[
            {
                "role": "user",
                "content": [{"type": "input_file", "file_id": "file_chatgpt_owned"}],
            }
        ]
    )

    replay = mixin.external_fallback_replay_payload(payload, {}, api_key=None)

    assert replay is None


def test_unverified_previous_response_is_not_replayable(monkeypatch: pytest.MonkeyPatch) -> None:
    mixin = _StreamingRetryMixin()
    payload = _request(previous_response_id="resp_chatgpt_owned")
    calls = 0

    def reject_unverified(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        del args, kwargs
        calls += 1
        return None

    monkeypatch.setattr(retry_module, "_verified_cross_transport_fresh_replay", reject_unverified)

    replay = mixin.external_fallback_replay_payload(payload, {}, api_key=None)

    assert replay is None
    assert calls == 1


def test_verified_previous_response_replay_drops_chatgpt_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    mixin = _StreamingRetryMixin()
    payload = _request(previous_response_id="resp_chatgpt_owned")
    verified = _request(input="complete self-contained replay")

    monkeypatch.setattr(
        retry_module,
        "_verified_cross_transport_fresh_replay",
        lambda *args, **kwargs: verified,
    )

    replay = mixin.external_fallback_replay_payload(payload, {}, api_key=None)

    assert replay is not None
    assert replay.previous_response_id is None
    assert replay.input == verified.input
