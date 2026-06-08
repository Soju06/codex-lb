from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Body, Depends, Security
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.auth.dependencies import (
    set_dashboard_error_format,
    set_openai_error_format,
    validate_dashboard_session,
    validate_proxy_api_key,
)
from app.core.errors import openai_error as _openai_error
from app.core.exceptions import (
    DashboardBadRequestError,
    DashboardRateLimitError,
    DashboardUpstreamError,
    ProxyAuthError,
    ProxyModelNotAllowed,
    ProxyRateLimitError,
    ProxyUpstreamError,
)
from app.core.types import JsonValue
from app.dependencies import AgentProviderRuntimeContext, get_agent_provider_runtime_context
from app.modules.agent_provider_runtime.antigravity import (
    AntigravityHarnessExecutionError,
    AntigravityHarnessRequest,
    AntigravityHarnessValidationError,
    command_preview,
)
from app.modules.agent_provider_runtime.schemas import (
    AntigravityHarnessPrintRequest,
    AntigravityHarnessPrintResponse,
    AntigravityManagedInteractionRunRequest,
    AntigravityManagedInteractionRunResponse,
)
from app.modules.agent_provider_runtime.service import (
    AntigravityRuntimeRequestContext,
    AntigravityRuntimeValidationError,
    GeminiRuntimeRequestContext,
    GeminiRuntimeValidationError,
    invalid_request_error,
)
from app.modules.api_keys.service import ApiKeyData

router = APIRouter(
    prefix="/v1/gemini",
    tags=["agent-provider-runtime"],
    dependencies=[Depends(set_openai_error_format)],
)
antigravity_router = APIRouter(
    prefix="/v1/antigravity",
    tags=["agent-provider-runtime"],
    dependencies=[Depends(set_openai_error_format)],
)
dashboard_router = APIRouter(
    prefix="/api/agent-providers",
    tags=["agent-provider-runtime"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.post("/chat/completions")
async def create_gemini_chat_completion(
    payload: dict[str, JsonValue] = Body(...),
    context: AgentProviderRuntimeContext = Depends(get_agent_provider_runtime_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
):
    try:
        runtime_context = GeminiRuntimeRequestContext(api_key=api_key)
        stream = payload.get("stream") is True
        if stream:
            body = await context.gemini_service.stream_chat(payload, runtime_context)
            body = await _probe_gemini_stream_startup(body)
            return StreamingResponse(
                body,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        response = await context.gemini_service.complete_chat(payload, runtime_context)
    except GeminiRuntimeValidationError as exc:
        return JSONResponse(status_code=400, content=invalid_request_error(str(exc)))
    except (ProxyAuthError, ProxyModelNotAllowed, ProxyRateLimitError, ProxyUpstreamError) as exc:
        headers = {"Retry-After": exc.retry_after} if isinstance(exc, ProxyRateLimitError) and exc.retry_after else None
        return JSONResponse(
            status_code=exc.status_code,
            content=_openai_error(exc.code, exc.message, error_type=exc.error_type),
            headers=headers,
        )
    return JSONResponse(content=response)


@antigravity_router.post("/interactions")
async def create_antigravity_interaction(
    payload: dict[str, JsonValue] = Body(...),
    context: AgentProviderRuntimeContext = Depends(get_agent_provider_runtime_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
):
    try:
        response = await context.antigravity_managed_service.create_interaction(
            payload,
            AntigravityRuntimeRequestContext(api_key=api_key),
        )
    except AntigravityRuntimeValidationError as exc:
        return JSONResponse(status_code=400, content=invalid_request_error(str(exc)))
    return JSONResponse(content=response)


@dashboard_router.post(
    "/antigravity/interactions/run",
    response_model=AntigravityManagedInteractionRunResponse,
)
async def run_antigravity_managed_interaction(
    payload: AntigravityManagedInteractionRunRequest = Body(...),
    context: AgentProviderRuntimeContext = Depends(get_agent_provider_runtime_context),
) -> AntigravityManagedInteractionRunResponse:
    try:
        request_payload: dict[str, JsonValue] = {
            "agent": payload.agent,
            "input": payload.input,
            "environment": payload.environment,
        }
        if payload.tools:
            request_payload["tools"] = cast(JsonValue, payload.tools)
        response = await context.antigravity_managed_service.create_interaction(request_payload)
    except AntigravityRuntimeValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_antigravity_interaction_request") from exc
    except ProxyRateLimitError as exc:
        raise DashboardRateLimitError(str(exc), retry_after=0, code="antigravity_interaction_unavailable") from exc
    except Exception as exc:
        raise DashboardUpstreamError(str(exc), code="antigravity_interaction_failed") from exc
    return AntigravityManagedInteractionRunResponse(
        provider_id="antigravity",
        agent=payload.agent,
        output_text=_antigravity_output_text(response),
        response=response,
    )


@dashboard_router.post("/antigravity/harness/print", response_model=AntigravityHarnessPrintResponse)
async def run_antigravity_harness_print(
    payload: AntigravityHarnessPrintRequest = Body(...),
    context: AgentProviderRuntimeContext = Depends(get_agent_provider_runtime_context),
) -> AntigravityHarnessPrintResponse:
    try:
        result = await context.antigravity_service.print_prompt(
            AntigravityHarnessRequest(
                prompt=payload.prompt,
                workspace_path=payload.workspace_path,
                timeout_seconds=payload.timeout_seconds,
                add_dirs=tuple(payload.add_dirs),
                conversation_id=payload.conversation_id,
                continue_conversation=payload.continue_conversation,
                sandbox=payload.sandbox,
            )
        )
    except AntigravityHarnessValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_antigravity_harness_request") from exc
    except AntigravityHarnessExecutionError as exc:
        raise DashboardUpstreamError(str(exc), code="antigravity_harness_failed") from exc
    return AntigravityHarnessPrintResponse(
        provider_id="antigravity",
        account_id=result.account.id,
        external_account_id=result.account.external_account_id,
        command=list(command_preview(result.command)),
        cwd=str(result.command.cwd),
        exit_code=result.process.exit_code,
        stdout=result.process.stdout,
        stderr=result.process.stderr,
        duration_ms=result.process.duration_ms,
    )


def _antigravity_output_text(payload: dict[str, JsonValue]) -> str:
    for key in ("output_text", "outputText", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    output = payload.get("output")
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        text = output.get("text")
        if isinstance(text, str):
            return text
    steps_text = _antigravity_steps_output_text(payload)
    if steps_text:
        return steps_text
    outputs_text = _antigravity_outputs_text(payload)
    if outputs_text:
        return outputs_text
    return ""


def _antigravity_steps_output_text(payload: dict[str, JsonValue]) -> str:
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return ""
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        step_mapping = cast(dict[str, object], step)
        if step_mapping.get("type") != "model_output":
            continue
        text = _antigravity_content_text(step_mapping.get("content"))
        if text:
            return text
    return ""


def _antigravity_outputs_text(payload: dict[str, JsonValue]) -> str:
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return ""
    return _antigravity_content_text(outputs)


def _antigravity_content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = cast(dict[str, object], value).get("text")
        return text if isinstance(text, str) else ""
    if not isinstance(value, list):
        return ""
    texts: list[str] = []
    for block in value:
        if isinstance(block, str):
            texts.append(block)
        elif isinstance(block, dict):
            text = cast(dict[str, object], block).get("text")
            if isinstance(text, str):
                texts.append(text)
    return "".join(texts)


async def _probe_gemini_stream_startup(body: AsyncIterator[str]) -> AsyncIterator[str]:
    try:
        first = await body.__anext__()
    except StopAsyncIteration:
        return body
    return _prepend_gemini_stream_first(first, body)


async def _prepend_gemini_stream_first(first: str, body: AsyncIterator[str]) -> AsyncIterator[str]:
    yield first
    async for chunk in body:
        yield chunk
