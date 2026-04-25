from __future__ import annotations

import logging

from pydantic import ValidationError

from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.exceptions import ProxyModelNotAllowed
from app.core.openai.model_registry import ModelRegistry, get_model_registry
from app.core.openai.requests import ResponsesCompactRequest, ResponsesReasoning, ResponsesRequest
from app.core.openai.v1_requests import V1ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id
from app.modules.api_keys.service import ApiKeyData

logger = logging.getLogger(__name__)

# Reasoning efforts that the upstream ChatGPT/Codex backend silently drops
# (the WebSocket never produces ``response.completed``). When a client sends
# one of these we transparently rewrite it to a value the resolved model
# advertises in its ``supported_reasoning_levels`` so the request does not
# hang. ``minimal`` is a valid value on the OpenAI Platform Responses API for
# GPT-5 family models, but the ChatGPT backend codex-lb proxies to does not
# accept it as of 2026-04. See https://github.com/Soju06/codex-lb/issues/493
_UNSUPPORTED_UPSTREAM_REASONING_EFFORTS: frozenset[str] = frozenset({"minimal"})
_DEFAULT_REASONING_EFFORT_FALLBACK = "low"


def validate_model_access(api_key: ApiKeyData | None, model: str | None) -> None:
    if api_key is None:
        return
    allowed_models = api_key.allowed_models
    if not allowed_models:
        return
    if model is None or model in allowed_models:
        return
    raise ProxyModelNotAllowed(f"This API key does not have access to model '{model}'")


def apply_api_key_enforcement(
    payload: ResponsesRequest | ResponsesCompactRequest,
    api_key: ApiKeyData | None,
) -> None:
    if api_key is None:
        normalize_unsupported_reasoning_effort(payload)
        return

    if api_key.enforced_model and payload.model != api_key.enforced_model:
        logger.info(
            "api_key_model_enforced request_id=%s key_id=%s requested_model=%s enforced_model=%s",
            get_request_id(),
            api_key.id,
            payload.model,
            api_key.enforced_model,
        )
        payload.model = api_key.enforced_model

    if api_key.enforced_reasoning_effort is not None:
        requested_effort = payload.reasoning.effort if payload.reasoning else None
        if payload.reasoning is None:
            payload.reasoning = ResponsesReasoning(effort=api_key.enforced_reasoning_effort)
        else:
            payload.reasoning.effort = api_key.enforced_reasoning_effort
        if requested_effort != api_key.enforced_reasoning_effort:
            logger.info(
                "api_key_reasoning_enforced request_id=%s key_id=%s requested_effort=%s enforced_effort=%s",
                get_request_id(),
                api_key.id,
                requested_effort,
                api_key.enforced_reasoning_effort,
            )

    normalize_unsupported_reasoning_effort(payload)

    if api_key.enforced_service_tier is not None:
        requested_service_tier = getattr(payload, "service_tier", None)
        setattr(payload, "service_tier", api_key.enforced_service_tier)
        if requested_service_tier != api_key.enforced_service_tier:
            logger.info(
                "api_key_service_tier_enforced request_id=%s key_id=%s "
                "requested_service_tier=%s enforced_service_tier=%s",
                get_request_id(),
                api_key.id,
                requested_service_tier,
                api_key.enforced_service_tier,
            )


def normalize_unsupported_reasoning_effort(
    payload: ResponsesRequest | ResponsesCompactRequest,
    *,
    registry: ModelRegistry | None = None,
) -> None:
    """Rewrite ``reasoning.effort`` values the upstream backend rejects.

    Some efforts that codex-lb accepts at the API surface (notably
    ``"minimal"``) are silently dropped by the ChatGPT/Codex WebSocket
    backend, which causes the response stream to hang with no completion.
    For those values we map to a value the resolved model actually supports
    so clients (e.g. Codex CLI's ``--reasoning-effort minimal``) keep
    working. Mapping picks the model's lowest advertised effort, falling
    back to ``low`` when the registry has no metadata yet.
    """

    if payload.reasoning is None or payload.reasoning.effort is None:
        return

    requested_effort = payload.reasoning.effort
    normalized_effort = requested_effort.strip().lower()
    if normalized_effort not in _UNSUPPORTED_UPSTREAM_REASONING_EFFORTS:
        return

    fallback = _resolve_reasoning_effort_fallback(
        payload.model,
        registry=registry or get_model_registry(),
    )
    payload.reasoning.effort = fallback
    logger.info(
        "reasoning_effort_normalized request_id=%s model=%s requested_effort=%s normalized_effort=%s",
        get_request_id(),
        payload.model,
        requested_effort,
        fallback,
    )


def _resolve_reasoning_effort_fallback(
    model: str | None,
    *,
    registry: ModelRegistry,
) -> str:
    if not model:
        return _DEFAULT_REASONING_EFFORT_FALLBACK
    snapshot = registry.get_snapshot()
    if snapshot is None:
        return _DEFAULT_REASONING_EFFORT_FALLBACK
    upstream = snapshot.models.get(model) or snapshot.models.get(model.strip().lower())
    if upstream is None:
        return _DEFAULT_REASONING_EFFORT_FALLBACK
    advertised = [level.effort for level in upstream.supported_reasoning_levels if level.effort]
    # Prefer the order the model registry advertises (already lowest -> highest
    # for the GPT-5 family), but always pick the first advertised effort that
    # is not itself an unsupported value.
    for effort in advertised:
        if effort.strip().lower() not in _UNSUPPORTED_UPSTREAM_REASONING_EFFORTS:
            return effort
    return _DEFAULT_REASONING_EFFORT_FALLBACK


def openai_validation_error(exc: ValidationError) -> OpenAIErrorEnvelope:
    error = openai_invalid_payload_error()
    if exc.errors():
        first = exc.errors()[0]
        loc = first.get("loc", [])
        if isinstance(loc, (list, tuple)):
            param = ".".join(str(part) for part in loc if part != "body")
            if param:
                error["error"]["param"] = param
    return error


def openai_invalid_payload_error(param: str | None = None) -> OpenAIErrorEnvelope:
    error = openai_error("invalid_request_error", "Invalid request payload", error_type="invalid_request_error")
    if param:
        error["error"]["param"] = param
    return error


def normalize_responses_request_payload(
    payload: dict[str, JsonValue],
    *,
    openai_compat: bool,
) -> ResponsesRequest:
    if openai_compat:
        return V1ResponsesRequest.model_validate(payload).to_responses_request()
    return ResponsesRequest.model_validate(payload)
