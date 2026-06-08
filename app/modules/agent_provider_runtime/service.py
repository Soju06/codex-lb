from __future__ import annotations

import asyncio
import codecs
import logging
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from aiohttp import ClientError, ClientResponse

from app.core.clients.http import lease_http_session
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError, ProxyModelNotAllowed, ProxyRateLimitError, ProxyUpstreamError
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping
from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import AgentProviderAccount
from app.modules.agent_provider_routing.service import AgentProviderRoutingNotFoundError
from app.modules.agent_provider_routing.settlement import AgentProviderUsageSettlementData
from app.modules.agent_provider_runtime.antigravity import (
    AntigravityHarnessCommand,
    AntigravityHarnessExecutionError,
    AntigravityHarnessRequest,
    AntigravityHarnessValidationError,
    AntigravityProcessResult,
    AntigravityProcessRunnerPort,
    AntigravitySubprocessRunner,
    antigravity_harness_env,
    build_antigravity_command,
    command_preview,
)
from app.modules.agent_provider_runtime.gemini import (
    GeminiAdapterError,
    GeminiChatRequest,
    build_generate_content_payload,
    build_generate_content_url,
    chat_completion_chunk_to_sse,
    generate_content_to_chat_completion,
    generate_content_to_chat_completion_chunk,
    parse_gemini_sse_data_lines,
)
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,
)

logger = logging.getLogger(__name__)


class TokenDecryptorPort(Protocol):
    def decrypt(self, encrypted: bytes) -> str: ...


class AgentProviderSelectorPort(Protocol):
    async def select_account(self, provider_id: str, *, auth_mode: str | None = None) -> Any: ...

    async def settle_usage(
        self,
        provider_id: str,
        account_id: str,
        usage: AgentProviderUsageSettlementData,
    ) -> None: ...


class ApiKeyUsagePort(Protocol):
    async def enforce_limits_for_request(
        self,
        key_id: str,
        *,
        request_model: str | None,
        request_service_tier: str | None = None,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> ApiKeyUsageReservationData: ...

    async def finalize_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        service_tier: str | None = None,
    ) -> None: ...

    async def release_usage_reservation(self, reservation_id: str) -> None: ...


class RequestLogWriterPort(Protocol):
    async def add_log(
        self,
        account_id: str | None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        **kwargs: Any,
    ) -> object: ...


class GeminiRuntimeValidationError(Exception):
    pass


class AntigravityRuntimeValidationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AntigravityHarnessResult:
    account: AgentProviderAccount
    command: AntigravityHarnessCommand
    process: AntigravityProcessResult


@dataclass(frozen=True, slots=True)
class GeminiRuntimeSelection:
    account: AgentProviderAccount
    api_key: str


@dataclass(frozen=True, slots=True)
class GeminiRuntimeRequestContext:
    api_key: ApiKeyData | None = None


@dataclass(frozen=True, slots=True)
class AntigravityRuntimeSelection:
    account: AgentProviderAccount
    api_key: str


@dataclass(frozen=True, slots=True)
class AntigravityRuntimeRequestContext:
    api_key: ApiKeyData | None = None


class AntigravityHarnessService:
    def __init__(
        self,
        routing_service: AgentProviderSelectorPort,
        *,
        runner: AntigravityProcessRunnerPort | None = None,
        request_logs: RequestLogWriterPort | None = None,
        executable: str = "agy",
    ) -> None:
        self._routing_service = routing_service
        self._runner = runner or AntigravitySubprocessRunner()
        self._request_logs = request_logs
        self._executable = executable

    async def print_prompt(self, request: AntigravityHarnessRequest) -> AntigravityHarnessResult:
        command = build_antigravity_command(request, executable=self._executable)
        request_id = ensure_request_id()
        selected = None
        try:
            selected = await self._routing_service.select_account("antigravity", auth_mode="cli_keyring")
            process = await self._runner.run(
                command,
                env=antigravity_harness_env(profile_id=selected.account.external_account_id),
            )
        except AgentProviderRoutingNotFoundError as exc:
            raise AntigravityHarnessValidationError(str(exc) or "No Antigravity CLI profile available") from exc
        except AntigravityHarnessExecutionError as exc:
            await self._write_harness_log(
                request_id=request_id,
                command=command,
                selection=selected,
                status="error",
                error_code="antigravity_cli_timeout",
                error_message=str(exc),
                latency_ms=None,
            )
            raise

        status = "success" if process.exit_code == 0 else "error"
        error_code = None if process.exit_code == 0 else "antigravity_cli_failed"
        error_message = None if process.exit_code == 0 else process.stderr or process.stdout or "Antigravity CLI failed"
        await self._write_harness_log(
            request_id=request_id,
            command=command,
            selection=selected,
            status=status,
            error_code=error_code,
            error_message=error_message,
            latency_ms=process.duration_ms,
        )
        if process.exit_code == 0:
            await self._routing_service.settle_usage(
                "antigravity",
                selected.account.id,
                AgentProviderUsageSettlementData(requests=1),
            )
        return AntigravityHarnessResult(account=selected.account, command=command, process=process)

    async def _write_harness_log(
        self,
        *,
        request_id: str,
        command: AntigravityHarnessCommand,
        selection: Any,
        status: str,
        error_code: str | None,
        error_message: str | None,
        latency_ms: int | None,
    ) -> None:
        if self._request_logs is None:
            return
        try:
            account = None if selection is None else selection.account
            await self._request_logs.add_log(
                None,
                request_id,
                "antigravity-cli",
                None,
                None,
                latency_ms,
                status,
                error_code,
                error_message=error_message,
                requested_at=utcnow(),
                source="antigravity",
                transport="agy_cli",
                plan_type="agent_provider",
                failure_detail=(
                    f"provider_account_id={account.id}; cwd={command.cwd}; command={' '.join(command_preview(command))}"
                    if account is not None
                    else f"cwd={command.cwd}; command={' '.join(command_preview(command))}"
                ),
            )
        except Exception:
            logger.warning(
                "Failed to write Antigravity harness request log request_id=%s",
                request_id,
                exc_info=True,
            )


class AntigravityManagedAgentService:
    def __init__(
        self,
        routing_service: AgentProviderSelectorPort,
        *,
        decryptor: TokenDecryptorPort | None = None,
        api_key_service: ApiKeyUsagePort | None = None,
        request_logs: RequestLogWriterPort | None = None,
    ) -> None:
        self._routing_service = routing_service
        self._decryptor = decryptor or TokenEncryptor()
        self._api_key_service = api_key_service
        self._request_logs = request_logs

    async def select_account(self) -> AntigravityRuntimeSelection:
        try:
            selected = await self._routing_service.select_account("antigravity", auth_mode="api_key")
        except AgentProviderRoutingNotFoundError as exc:
            raise ProxyRateLimitError(str(exc) or "No Antigravity API-key account available") from exc
        account = selected.account
        if account.api_key_encrypted is None:
            raise ProxyUpstreamError("Selected Antigravity account has no API key")
        return AntigravityRuntimeSelection(account=account, api_key=self._decryptor.decrypt(account.api_key_encrypted))

    async def create_interaction(
        self,
        payload: Mapping[str, JsonValue],
        context: AntigravityRuntimeRequestContext | None = None,
    ) -> dict[str, JsonValue]:
        api_key = None if context is None else context.api_key
        request_payload = _normalize_antigravity_interaction_payload(_interaction_payload_for_api_key(payload, api_key))
        agent = _interaction_agent(request_payload)
        request_id = ensure_request_id()
        started_at = time.perf_counter()
        reservation = await self._reserve_api_key_usage(context, model=agent)
        selection: AntigravityRuntimeSelection | None = None
        try:
            selection = await self.select_account()
            async with lease_http_session() as session:
                try:
                    async with session.post(
                        "https://generativelanguage.googleapis.com/v1beta/interactions",
                        json=request_payload,
                        headers={
                            "Content-Type": "application/json",
                            "x-goog-api-key": selection.api_key,
                            "Api-Revision": "2026-05-20",
                        },
                    ) as response:
                        await _raise_for_gemini_error(response)
                        data = await response.json()
                except ClientError as exc:
                    raise ProxyUpstreamError(str(exc) or "Antigravity upstream request failed") from exc
            if not is_json_mapping(data):
                raise ProxyUpstreamError("Antigravity upstream returned an invalid response")
        except Exception as exc:
            await self._release_api_key_usage(reservation)
            await self._write_request_log(
                request_id=request_id,
                agent=agent,
                context=context,
                selection=selection,
                status="error",
                error_code=_error_code(exc),
                error_message=str(exc) or None,
                latency_ms=_elapsed_ms(started_at),
            )
            raise

        usage = _usage_settlement_from_payload(data)
        if usage.requests == 0:
            usage = AgentProviderUsageSettlementData(requests=1)
        await self._finalize_api_key_usage(reservation, model=agent, usage=usage)
        await self._settle_provider_usage(selection, usage)
        await self._write_request_log(
            request_id=request_id,
            agent=agent,
            context=context,
            selection=selection,
            status="success",
            error_code=None,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            latency_ms=_elapsed_ms(started_at),
        )
        return cast(dict[str, JsonValue], data)

    async def complete_chat(
        self,
        payload: Mapping[str, JsonValue],
        context: AntigravityRuntimeRequestContext | None = None,
    ) -> dict[str, JsonValue]:
        api_key = None if context is None else context.api_key
        request = parse_chat_completion_request(_payload_for_api_key(payload, api_key))
        if request.stream:
            raise AntigravityRuntimeValidationError("Antigravity chat compatibility does not support streaming")
        if request.tools:
            raise AntigravityRuntimeValidationError("Antigravity chat compatibility does not support function tools")
        interaction = await self.create_interaction(
            {
                "agent": request.model,
                "input": _chat_messages_to_antigravity_input(request.messages),
                "environment": "remote",
            },
            context,
        )
        return _antigravity_interaction_to_chat_completion(interaction, model=request.model)

    async def _reserve_api_key_usage(
        self,
        context: AntigravityRuntimeRequestContext | None,
        *,
        model: str,
    ) -> ApiKeyUsageReservationData | None:
        api_key = None if context is None else context.api_key
        if api_key is None or self._api_key_service is None:
            return None
        try:
            return await self._api_key_service.enforce_limits_for_request(
                api_key.id,
                request_model=model,
                request_service_tier=None,
                request_usage_budget=None,
            )
        except ApiKeyRateLimitExceededError as exc:
            message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
            raise ProxyRateLimitError(message) from exc
        except ApiKeyInvalidError as exc:
            raise ProxyAuthError(str(exc)) from exc

    async def _finalize_api_key_usage(
        self,
        reservation: ApiKeyUsageReservationData | None,
        *,
        model: str,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        if reservation is None or self._api_key_service is None:
            return
        try:
            input_tokens = usage.prompt_tokens
            output_tokens = usage.completion_tokens
            await self._api_key_service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
                cached_input_tokens=0,
                service_tier=None,
            )
        except Exception:
            logger.warning(
                "Failed to finalize Antigravity API key reservation reservation_id=%s model=%s",
                reservation.reservation_id,
                model,
                exc_info=True,
            )

    async def _settle_provider_usage(
        self,
        selection: AntigravityRuntimeSelection,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        try:
            await self._routing_service.settle_usage("antigravity", selection.account.id, usage)
        except Exception:
            logger.warning(
                "Failed to settle Antigravity provider usage account_id=%s",
                selection.account.id,
                exc_info=True,
            )

    async def _release_api_key_usage(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is None or self._api_key_service is None:
            return
        try:
            await self._api_key_service.release_usage_reservation(reservation.reservation_id)
        except Exception:
            logger.warning(
                "Failed to release Antigravity API key reservation reservation_id=%s",
                reservation.reservation_id,
                exc_info=True,
            )

    async def _write_request_log(
        self,
        *,
        request_id: str,
        agent: str,
        context: AntigravityRuntimeRequestContext | None,
        selection: AntigravityRuntimeSelection | None,
        status: str,
        error_code: str | None,
        latency_ms: int | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._request_logs is None:
            return
        api_key = None if context is None else context.api_key
        try:
            await self._request_logs.add_log(
                None,
                request_id,
                agent,
                input_tokens,
                output_tokens,
                latency_ms,
                status,
                error_code,
                error_message=error_message,
                requested_at=utcnow(),
                api_key_id=None if api_key is None else api_key.id,
                source="antigravity",
                transport="interactions_api",
                plan_type="agent_provider",
                failure_detail=None if selection is None else f"provider_account_id={selection.account.id}",
            )
        except Exception:
            logger.warning(
                "Failed to write Antigravity request log request_id=%s agent=%s",
                request_id,
                agent,
                exc_info=True,
            )


class GeminiRuntimeService:
    def __init__(
        self,
        routing_service: AgentProviderSelectorPort,
        *,
        decryptor: TokenDecryptorPort | None = None,
        api_key_service: ApiKeyUsagePort | None = None,
        request_logs: RequestLogWriterPort | None = None,
    ) -> None:
        self._routing_service = routing_service
        self._decryptor = decryptor or TokenEncryptor()
        self._api_key_service = api_key_service
        self._request_logs = request_logs

    async def select_account(self) -> GeminiRuntimeSelection:
        try:
            selected = await self._routing_service.select_account("gemini", auth_mode="api_key")
        except AgentProviderRoutingNotFoundError as exc:
            raise ProxyRateLimitError(str(exc) or "No Gemini provider account available") from exc
        account = selected.account
        if account.api_key_encrypted is None:
            raise ProxyUpstreamError("Selected Gemini account has no API key")
        return GeminiRuntimeSelection(account=account, api_key=self._decryptor.decrypt(account.api_key_encrypted))

    async def complete_chat(
        self,
        payload: Mapping[str, JsonValue],
        context: GeminiRuntimeRequestContext | None = None,
    ) -> dict[str, JsonValue]:
        api_key = None if context is None else context.api_key
        request = parse_chat_completion_request(_payload_for_api_key(payload, api_key))
        request_id = ensure_request_id()
        started_at = time.perf_counter()
        reservation = await self._reserve_api_key_usage(context, model=request.model)
        selection: GeminiRuntimeSelection | None = None
        try:
            selection = await self.select_account()
            upstream_payload = build_generate_content_payload(request)
            url = build_generate_content_url(request.model)
            async with lease_http_session() as session:
                try:
                    async with session.post(
                        url,
                        json=upstream_payload,
                        headers={
                            "Content-Type": "application/json",
                            "x-goog-api-key": selection.api_key,
                        },
                    ) as response:
                        await _raise_for_gemini_error(response)
                        data = await response.json()
                except ClientError as exc:
                    raise ProxyUpstreamError(str(exc) or "Gemini upstream request failed") from exc
            if not is_json_mapping(data):
                raise ProxyUpstreamError("Gemini upstream returned an invalid response")
        except Exception as exc:
            await self._release_api_key_usage(reservation)
            await self._write_request_log(
                request_id=request_id,
                request=request,
                context=context,
                selection=selection,
                status="error",
                error_code=_error_code(exc),
                error_message=str(exc) or None,
                latency_ms=_elapsed_ms(started_at),
            )
            raise

        usage = _usage_settlement_from_payload(data)
        await self._finalize_api_key_usage(reservation, request=request, usage=usage)
        await self._settle_provider_usage(selection, usage)
        await self._write_request_log(
            request_id=request_id,
            request=request,
            context=context,
            selection=selection,
            status="success",
            error_code=None,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            latency_ms=_elapsed_ms(started_at),
        )
        return cast(dict[str, JsonValue], generate_content_to_chat_completion(data, model=request.model))

    async def stream_chat(
        self,
        payload: Mapping[str, JsonValue],
        context: GeminiRuntimeRequestContext | None = None,
    ) -> AsyncIterator[str]:
        api_key = None if context is None else context.api_key
        request = parse_chat_completion_request(_payload_for_api_key(payload, api_key))
        request_id = ensure_request_id()
        reservation = await self._reserve_api_key_usage(context, model=request.model)

        async def body() -> AsyncIterator[str]:
            started_at = time.perf_counter()
            usage_payload: Mapping[str, JsonValue] | None = None
            selection: GeminiRuntimeSelection | None = None
            try:
                selection = await self.select_account()
                upstream_payload = build_generate_content_payload(request)
                url = build_generate_content_url(request.model, stream=True)
                async with lease_http_session() as session:
                    try:
                        async with session.post(
                            url,
                            json=upstream_payload,
                            headers={
                                "Content-Type": "application/json",
                                "x-goog-api-key": selection.api_key,
                            },
                        ) as response:
                            await _raise_for_gemini_error(response)
                            async for event in _iter_gemini_sse_events(response):
                                if is_json_mapping(event.get("usageMetadata")):
                                    usage_payload = event
                                chunk = generate_content_to_chat_completion_chunk(event, model=request.model)
                                yield chat_completion_chunk_to_sse(cast(Mapping[str, JsonValue], chunk))
                    except ClientError as exc:
                        raise ProxyUpstreamError(str(exc) or "Gemini upstream stream failed") from exc
            except asyncio.CancelledError:
                if selection is None or usage_payload is None:
                    await self._release_api_key_usage(reservation)
                else:
                    usage = _usage_settlement_from_payload(usage_payload)
                    await self._finalize_api_key_usage(reservation, request=request, usage=usage)
                    await self._settle_provider_usage(selection, usage)
                await self._write_request_log(
                    request_id=request_id,
                    request=request,
                    context=context,
                    selection=selection,
                    status="error",
                    error_code="client_cancelled",
                    error_message="Gemini stream cancelled",
                    latency_ms=_elapsed_ms(started_at),
                )
                raise
            except Exception as exc:
                if selection is None:
                    await self._release_api_key_usage(reservation)
                else:
                    usage = _usage_settlement_from_payload(usage_payload or {})
                    await self._finalize_api_key_usage(reservation, request=request, usage=usage)
                    await self._settle_provider_usage(selection, usage)
                await self._write_request_log(
                    request_id=request_id,
                    request=request,
                    context=context,
                    selection=selection,
                    status="error",
                    error_code=_error_code(exc),
                    error_message=str(exc) or None,
                    latency_ms=_elapsed_ms(started_at),
                )
                raise
            usage = _usage_settlement_from_payload(usage_payload or {})
            assert selection is not None
            await self._finalize_api_key_usage(reservation, request=request, usage=usage)
            await self._settle_provider_usage(selection, usage)
            await self._write_request_log(
                request_id=request_id,
                request=request,
                context=context,
                selection=selection,
                status="success",
                error_code=None,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                latency_ms=_elapsed_ms(started_at),
            )
            yield "data: [DONE]\n\n"

        return body()

    async def _reserve_api_key_usage(
        self,
        context: GeminiRuntimeRequestContext | None,
        *,
        model: str,
    ) -> ApiKeyUsageReservationData | None:
        api_key = None if context is None else context.api_key
        if api_key is None or self._api_key_service is None:
            return None
        try:
            return await self._api_key_service.enforce_limits_for_request(
                api_key.id,
                request_model=model,
                request_service_tier=None,
                request_usage_budget=None,
            )
        except ApiKeyRateLimitExceededError as exc:
            message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
            raise ProxyRateLimitError(message) from exc
        except ApiKeyInvalidError as exc:
            raise ProxyAuthError(str(exc)) from exc

    async def _finalize_api_key_usage(
        self,
        reservation: ApiKeyUsageReservationData | None,
        *,
        request: GeminiChatRequest,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        if reservation is None or self._api_key_service is None:
            return
        try:
            input_tokens = usage.prompt_tokens
            output_tokens = usage.completion_tokens
            await self._api_key_service.finalize_usage_reservation(
                reservation.reservation_id,
                model=request.model,
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
                cached_input_tokens=0,
                service_tier=None,
            )
        except Exception:
            logger.warning(
                "Failed to finalize Gemini API key reservation reservation_id=%s model=%s",
                reservation.reservation_id,
                request.model,
                exc_info=True,
            )

    async def _settle_provider_usage(
        self,
        selection: GeminiRuntimeSelection,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        try:
            await self._routing_service.settle_usage("gemini", selection.account.id, usage)
        except Exception:
            logger.warning(
                "Failed to settle Gemini provider usage account_id=%s",
                selection.account.id,
                exc_info=True,
            )

    async def _release_api_key_usage(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is None or self._api_key_service is None:
            return
        try:
            await self._api_key_service.release_usage_reservation(reservation.reservation_id)
        except Exception:
            logger.warning(
                "Failed to release Gemini API key reservation reservation_id=%s",
                reservation.reservation_id,
                exc_info=True,
            )

    async def _write_request_log(
        self,
        *,
        request_id: str,
        request: GeminiChatRequest,
        context: GeminiRuntimeRequestContext | None,
        selection: GeminiRuntimeSelection | None,
        status: str,
        error_code: str | None,
        latency_ms: int | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._request_logs is None:
            return
        api_key = None if context is None else context.api_key
        try:
            await self._request_logs.add_log(
                None,
                request_id,
                request.model,
                input_tokens,
                output_tokens,
                latency_ms,
                status,
                error_code,
                error_message=error_message,
                requested_at=utcnow(),
                api_key_id=None if api_key is None else api_key.id,
                source="gemini",
                transport="gemini_native",
                plan_type="agent_provider",
                failure_detail=None if selection is None else f"provider_account_id={selection.account.id}",
            )
        except Exception:
            logger.warning(
                "Failed to write Gemini request log request_id=%s model=%s",
                request_id,
                request.model,
                exc_info=True,
            )


def parse_chat_completion_request(payload: Mapping[str, JsonValue]) -> GeminiChatRequest:
    model = payload.get("model")
    messages = payload.get("messages")
    if not isinstance(model, str) or not model.strip():
        raise GeminiRuntimeValidationError("model is required")
    if not is_json_list(messages) or not all(is_json_mapping(message) for message in messages):
        raise GeminiRuntimeValidationError("messages must be an array")
    try:
        return GeminiChatRequest(
            model=model,
            messages=cast(list[Mapping[str, JsonValue]], messages),
            stream=payload.get("stream") is True,
            temperature=_float_or_none(payload.get("temperature")),
            top_p=_float_or_none(payload.get("top_p")),
            max_tokens=_int_or_none(payload.get("max_tokens")) or _int_or_none(payload.get("max_completion_tokens")),
            stop=_stop_or_none(payload.get("stop")),
            tools=_tools_or_none(payload.get("tools")),
            response_format=payload.get("response_format"),
        )
    except GeminiAdapterError as exc:
        raise GeminiRuntimeValidationError(str(exc)) from exc


def _payload_for_api_key(payload: Mapping[str, JsonValue], api_key: ApiKeyData | None) -> Mapping[str, JsonValue]:
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        return payload
    enforced_model = None if api_key is None else api_key.enforced_model
    effective_model = enforced_model or model
    if api_key is not None and api_key.allowed_models and effective_model not in api_key.allowed_models:
        raise ProxyModelNotAllowed(f"Model is not allowed for this API key: {effective_model}")
    if enforced_model is None:
        return payload
    updated = dict(payload)
    updated["model"] = enforced_model
    return updated


def _interaction_payload_for_api_key(
    payload: Mapping[str, JsonValue],
    api_key: ApiKeyData | None,
) -> Mapping[str, JsonValue]:
    agent = payload.get("agent")
    if not isinstance(agent, str) or not agent.strip():
        return payload
    enforced_model = None if api_key is None else api_key.enforced_model
    effective_agent = enforced_model or agent
    if api_key is not None and api_key.allowed_models and effective_agent not in api_key.allowed_models:
        raise ProxyModelNotAllowed(f"Model is not allowed for this API key: {effective_agent}")
    if enforced_model is None:
        return payload
    updated = dict(payload)
    updated["agent"] = enforced_model
    return updated


async def _iter_gemini_sse_events(response: ClientResponse) -> AsyncIterator[dict[str, JsonValue]]:
    buffer = ""
    decoder = codecs.getincrementaldecoder("utf-8")()
    async for chunk in response.content.iter_any():
        buffer += decoder.decode(chunk)
        buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            for event in parse_gemini_sse_data_lines(raw_event.splitlines()):
                yield event
    buffer += decoder.decode(b"", final=True)
    buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
    if buffer.strip():
        for event in parse_gemini_sse_data_lines(buffer.splitlines()):
            yield event


async def _raise_for_gemini_error(response: ClientResponse) -> None:
    if response.status < 400:
        return
    message = await _gemini_error_message(response)
    if response.status == 429:
        raise ProxyRateLimitError(message, retry_after=response.headers.get("Retry-After"))
    raise ProxyUpstreamError(message)


async def _gemini_error_message(response: ClientResponse) -> str:
    try:
        payload = await response.json()
    except Exception:
        text = await response.text()
        return text or f"Gemini upstream returned HTTP {response.status}"
    if is_json_mapping(payload):
        error = payload.get("error")
        if is_json_mapping(error):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
    return f"Gemini upstream returned HTTP {response.status}"


def _float_or_none(value: JsonValue) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _int_or_none(value: JsonValue) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _stop_or_none(value: JsonValue) -> str | list[str] | None:
    if isinstance(value, str):
        return value
    if is_json_list(value) and all(isinstance(item, str) for item in value):
        return cast(list[str], value)
    return None


def _tools_or_none(value: JsonValue) -> list[JsonValue] | None:
    if is_json_list(value):
        return value
    return None


def invalid_request_error(message: str) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        openai_error("invalid_request_error", message, error_type="invalid_request_error"),
    )


def _normalize_antigravity_interaction_payload(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    agent = _interaction_agent(payload)
    interaction_input = payload.get("input")
    if interaction_input is None:
        raise AntigravityRuntimeValidationError("input is required")
    environment = payload.get("environment", "remote")
    if not (isinstance(environment, str) and environment.strip()) and not is_json_mapping(environment):
        raise AntigravityRuntimeValidationError("environment must be a string or object")
    normalized: dict[str, JsonValue] = {
        "agent": agent,
        "input": interaction_input,
        "environment": environment,
    }
    tools = payload.get("tools")
    if tools is not None:
        if not is_json_list(tools):
            raise AntigravityRuntimeValidationError("tools must be an array")
        normalized["tools"] = tools
    return normalized


def _interaction_agent(payload: Mapping[str, JsonValue]) -> str:
    agent = payload.get("agent")
    if not isinstance(agent, str) or not agent.strip():
        raise AntigravityRuntimeValidationError("agent is required")
    if not agent.startswith("antigravity-"):
        raise AntigravityRuntimeValidationError("agent must be an Antigravity model")
    return agent.strip()


def _chat_messages_to_antigravity_input(messages: list[Mapping[str, JsonValue]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = message.get("role")
        if not isinstance(role, str):
            raise AntigravityRuntimeValidationError("message role must be a string")
        if role == "tool":
            raise AntigravityRuntimeValidationError("Antigravity chat compatibility does not support tool messages")
        text = _message_text_content(message.get("content"))
        if text:
            lines.append(f"{role}: {text}")
    if not lines:
        raise AntigravityRuntimeValidationError("Antigravity request requires at least one text message")
    return "\n\n".join(lines)


def _message_text_content(content: JsonValue) -> str:
    if isinstance(content, str):
        return content
    if is_json_list(content):
        texts: list[str] = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif is_json_mapping(part):
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "\n".join(texts)
    return ""


def _antigravity_interaction_to_chat_completion(
    payload: Mapping[str, JsonValue],
    *,
    model: str,
) -> dict[str, JsonValue]:
    return {
        "id": _antigravity_response_id(payload),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": _antigravity_output_text(payload)},
                "finish_reason": "stop",
            }
        ],
    }


def _antigravity_response_id(payload: Mapping[str, JsonValue]) -> str:
    for key in ("id", "name", "interactionId", "interaction_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return "antigravity-interaction"


def _antigravity_output_text(payload: Mapping[str, JsonValue]) -> str:
    for key in ("output_text", "outputText", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    output = payload.get("output")
    if isinstance(output, str):
        return output
    if is_json_mapping(output):
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


def _antigravity_steps_output_text(payload: Mapping[str, JsonValue]) -> str:
    steps = payload.get("steps")
    if not is_json_list(steps):
        return ""
    for step in reversed(steps):
        if not is_json_mapping(step) or step.get("type") != "model_output":
            continue
        text = _antigravity_content_text(step.get("content"))
        if text:
            return text
    return ""


def _antigravity_outputs_text(payload: Mapping[str, JsonValue]) -> str:
    outputs = payload.get("outputs")
    if not is_json_list(outputs):
        return ""
    return _antigravity_content_text(outputs)


def _antigravity_content_text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    if is_json_mapping(value):
        text = value.get("text")
        return text if isinstance(text, str) else ""
    if not is_json_list(value):
        return ""
    texts: list[str] = []
    for block in value:
        if isinstance(block, str):
            texts.append(block)
        elif is_json_mapping(block):
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "".join(texts)


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(exc, AntigravityRuntimeValidationError):
        return "invalid_request_error"
    if isinstance(exc, GeminiRuntimeValidationError):
        return "invalid_request_error"
    return "upstream_error"


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _usage_settlement_from_payload(payload: Mapping[str, JsonValue]) -> AgentProviderUsageSettlementData:
    usage = payload.get("usageMetadata")
    if is_json_mapping(usage):
        return AgentProviderUsageSettlementData(
            requests=1,
            prompt_tokens=_int_or_none(usage.get("promptTokenCount")),
            completion_tokens=_int_or_none(usage.get("candidatesTokenCount")),
            total_tokens=_int_or_none(usage.get("totalTokenCount")),
        )
    interaction_usage = payload.get("usage")
    if is_json_mapping(interaction_usage):
        return AgentProviderUsageSettlementData(
            requests=1,
            prompt_tokens=_int_or_none(interaction_usage.get("total_input_tokens")),
            completion_tokens=_int_or_none(interaction_usage.get("total_output_tokens")),
            total_tokens=_int_or_none(interaction_usage.get("total_tokens")),
        )
    return AgentProviderUsageSettlementData()
