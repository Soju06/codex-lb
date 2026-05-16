from __future__ import annotations

import logging

from pydantic import ValidationError

from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.exceptions import ProxyModelNotAllowed
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.model_registry import ModelRegistry, get_model_registry
from app.core.openai.requests import ResponsesCompactRequest, ResponsesReasoning, ResponsesRequest
from app.core.openai.strict_schema import (
    validate_strict_function_tool_schema,
    validate_strict_json_schema,
)
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


def openai_client_payload_error(exc: ClientPayloadError) -> OpenAIErrorEnvelope:
    """Render a ``ClientPayloadError`` as an OpenAI error envelope.

    Falls back to ``openai_invalid_payload_error`` for legacy callsites
    that raise ``ClientPayloadError`` without ``code`` / ``error_type``.
    """
    if exc.code is None and exc.error_type is None:
        return openai_invalid_payload_error(exc.param)
    code = exc.code or "invalid_request_error"
    error_type = exc.error_type or "invalid_request_error"
    error = openai_error(code, str(exc), error_type=error_type)
    if exc.param:
        error["error"]["param"] = exc.param
    return error


def normalize_responses_request_payload(
    payload: dict[str, JsonValue],
    *,
    openai_compat: bool,
) -> ResponsesRequest:
    if openai_compat:
        responses = V1ResponsesRequest.model_validate(payload).to_responses_request()
    else:
        responses = ResponsesRequest.model_validate(payload)
    enforce_strict_text_format(responses)
    enforce_strict_function_tools_format(responses.tools)
    return responses


def enforce_strict_text_format(request: ResponsesRequest) -> None:
    """Reject strict-mode JSON schemas that violate OpenAI structured-outputs rules.

    The Codex backend mirrors OpenAI's strict-mode policy and closes the
    websocket with ``close_code=1000`` (delivering the original
    ``invalid_json_schema`` payload via ``response.failed``). The local
    pre-check raises a deterministic 400 before any upstream connection
    is opened, keeping ``/v1/responses`` and the chat-conversion path
    consistent and avoiding pointless retry/reconnect cycles for
    permanently invalid schemas.
    """
    if request.text is None or request.text.format is None:
        return
    text_format = request.text.format
    if text_format.type != "json_schema" or text_format.strict is not True:
        return
    if text_format.schema_ is None:
        return
    violation = validate_strict_json_schema(
        text_format.schema_,
        name=text_format.name,
        param="text.format.schema",
    )
    if violation is None:
        return
    raise ClientPayloadError(
        violation.message,
        param=violation.param,
        code=violation.code,
        error_type="invalid_request_error",
    )


def enforce_strict_function_tools_format(
    tools: list[JsonValue] | None,
    *,
    param_template: str = "tools[{index}].parameters",
    nested: bool = False,
) -> None:
    """Reject strict-mode function tools whose parameter schemas violate OpenAI rules.

    Mirrors :func:`enforce_strict_text_format` for function tools that
    set ``strict: true``. The Codex backend rejects an invalid strict
    tool schema by closing the WebSocket with ``close_code=1000``; the
    surfaced error is a generic ``upstream_rejected_input`` 502, which
    well-behaved retry loops misclassify as transient. Real OpenAI
    returns a deterministic ``400 invalid_function_parameters`` for the
    same payload, so codex-lb pre-validates here.

    ``param_template`` controls how the rejected parameter is named in
    the error envelope: native ``/v1/responses`` callers see
    ``tools[<i>].parameters``; chat-completions callers pass
    ``"tools[{index}].function.parameters"`` to mirror the inbound
    shape.

    ``nested`` selects the shape of an inbound function tool:

    * ``False`` (default) — flat ``{"type": "function", "name": ...,
      "parameters": ..., "strict": ...}``. This is the
      ``/v1/responses`` request shape (and the shape produced by
      ``_normalize_chat_tools``).
    * ``True`` — chat-completions shape with the function payload
      wrapped under ``"function"``:
      ``{"type": "function", "function": {"name": ..., "parameters":
      ..., "strict": ...}}``. The chat handler MUST pass ``True`` and
      hand in the *original* request payload's ``tools`` list (not the
      list returned by ``ChatCompletionsRequest.to_responses_request``,
      which may drop or reorder entries) so the ``{index}`` slot in
      ``param_template`` lines up with the inbound payload.
    """
    if not tools:
        return
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            continue
        if nested:
            function_value = tool.get("function")
            if not isinstance(function_value, dict):
                continue
            descriptor = function_value
        else:
            descriptor = tool
        if descriptor.get("strict") is not True:
            continue
        parameters = descriptor.get("parameters")
        if parameters is None:
            continue
        raw_name = descriptor.get("name")
        name = raw_name if isinstance(raw_name, str) else None
        violation = validate_strict_function_tool_schema(
            parameters,
            name=name,
            param=param_template.format(index=index),
        )
        if violation is None:
            continue
        raise ClientPayloadError(
            violation.message,
            param=violation.param,
            code=violation.code,
            error_type="invalid_request_error",
        )
