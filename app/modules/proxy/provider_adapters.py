from __future__ import annotations

import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, cast

from app.core.clients.openai_platform import OpenAIPlatformError, validate_platform_identity
from app.core.clients.openai_platform import create_compact_response as create_platform_compact_response
from app.core.clients.openai_platform import create_response as create_platform_response
from app.core.clients.openai_platform import fetch_models as fetch_platform_models
from app.core.clients.openai_platform import stream_responses as stream_platform_responses
from app.core.clients.proxy import compact_responses as _proxy_compact_responses
from app.core.clients.proxy import fetch_codex_models as _proxy_fetch_codex_models
from app.core.clients.proxy import stream_responses as _proxy_stream_responses
from app.core.clients.proxy import transcribe_audio as _proxy_transcribe_audio
from app.core.clients.proxy_websocket import (
    UpstreamResponsesWebSocket,
)
from app.core.clients.proxy_websocket import (
    connect_responses_websocket as _proxy_connect_responses_websocket,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.models import CompactResponsePayload, OpenAIResponsePayload
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import get_request_id
from app.db.models import Account
from app.modules.accounts.auth_manager import AuthManager
from app.modules.proxy.helpers import _header_account_id
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.upstream_identities.types import (
    CHATGPT_WEB_PROVIDER_KIND,
    OPENAI_PLATFORM_PROVIDER_KIND,
    OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    PHASE1_PLATFORM_ROUTE_FAMILIES,
    ProviderKind,
)
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)

type _CompactResponsesCallable = Callable[
    [ResponsesCompactRequest, Mapping[str, str], str, str | None],
    Awaitable[CompactResponsePayload],
]
type _TranscribeAudioCallable = Callable[
    ...,
    Awaitable[dict[str, JsonValue]],
]
type _ConnectResponsesWebSocketCallable = Callable[
    [Mapping[str, str], str, str | None],
    Awaitable[UpstreamResponsesWebSocket],
]


async def core_compact_responses(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> CompactResponsePayload:
    return await _proxy_compact_responses(payload, headers, access_token, account_id)


def core_stream_responses(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    *,
    raise_for_status: bool = True,
    upstream_stream_transport_override: str | None = None,
) -> AsyncIterator[str]:
    return _proxy_stream_responses(
        payload,
        headers,
        access_token,
        account_id,
        raise_for_status=raise_for_status,
        upstream_stream_transport_override=upstream_stream_transport_override,
    )


async def core_transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str | None,
    prompt: str | None,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, JsonValue]:
    return await _proxy_transcribe_audio(
        audio_bytes,
        filename=filename,
        content_type=content_type,
        prompt=prompt,
        headers=headers,
        access_token=access_token,
        account_id=account_id,
    )


connect_responses_websocket = _proxy_connect_responses_websocket

_DEFAULT_CORE_COMPACT_RESPONSES = core_compact_responses
_DEFAULT_CORE_STREAM_RESPONSES = core_stream_responses
_DEFAULT_CORE_TRANSCRIBE_AUDIO = core_transcribe_audio
_DEFAULT_CONNECT_RESPONSES_WEBSOCKET = connect_responses_websocket


def _resolve_proxy_compat_callable(name: str, default: object) -> object:
    local_candidate = globals()[name]
    if local_candidate is not default:
        return local_candidate
    try:
        from app.modules.proxy import service as proxy_service_module
    except Exception:
        return local_candidate
    service_candidate = getattr(proxy_service_module, name, local_candidate)
    if service_candidate is not default:
        return service_candidate
    return local_candidate


@dataclass(frozen=True, slots=True)
class RequestCapabilities:
    route_family: str
    route_class: str
    transport: str
    model: str | None
    continuity_param: str | None = None
    continuity_hint: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSubject:
    provider_kind: ProviderKind
    routing_subject_id: str
    account: Account | None = None
    api_key_encrypted: bytes | None = None
    organization_id: str | None = None
    project_id: str | None = None

    def require_account(self) -> Account:
        if self.account is None:
            raise ValueError("Provider subject does not include a ChatGPT-web account")
        return self.account

    def require_api_key_encrypted(self) -> bytes:
        if self.api_key_encrypted is None:
            raise ValueError("Provider subject does not include encrypted OpenAI Platform credentials")
        return self.api_key_encrypted


@dataclass(frozen=True, slots=True)
class ProviderCapabilityDecision:
    allowed: bool
    error_code: str | None = None
    error_message: str | None = None
    error_param: str | None = None
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderModelsResult:
    payload: dict[str, JsonValue]
    upstream_request_id: str | None


@dataclass(frozen=True, slots=True)
class ProviderCreateResponseResult:
    payload: OpenAIResponsePayload | dict[str, JsonValue]
    upstream_request_id: str | None


@dataclass(frozen=True, slots=True)
class ProviderCompactResponseResult:
    payload: CompactResponsePayload
    upstream_request_id: str | None


@dataclass(frozen=True, slots=True)
class ProviderStreamResponseResult:
    event_stream: AsyncIterator[str]
    upstream_request_id: str | None


class ProviderAdapter(Protocol):
    provider_kind: ProviderKind

    async def ensure_ready(
        self,
        subject: ProviderSubject,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> ProviderSubject: ...

    def check_capabilities(
        self,
        subject: ProviderSubject,
        capabilities: RequestCapabilities,
    ) -> ProviderCapabilityDecision: ...

    async def fetch_models(
        self,
        subject: ProviderSubject,
        *,
        headers: Mapping[str, str] | None = None,
        route_class: str | None = None,
    ) -> ProviderModelsResult: ...

    async def create_response(
        self,
        subject: ProviderSubject,
        payload: Mapping[str, JsonValue],
    ) -> ProviderCreateResponseResult: ...

    async def compact_response(
        self,
        subject: ProviderSubject,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
    ) -> ProviderCompactResponseResult: ...

    async def stream_responses(
        self,
        subject: ProviderSubject,
        payload: Mapping[str, JsonValue],
    ) -> ProviderStreamResponseResult: ...


class ChatGPTWebProviderAdapter:
    provider_kind: ProviderKind = CHATGPT_WEB_PROVIDER_KIND

    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._encryptor = TokenEncryptor()

    def _upstream_auth(self, subject: ProviderSubject) -> tuple[str, str | None]:
        account = subject.require_account()
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        return access_token, _header_account_id(account.chatgpt_account_id)

    async def ensure_ready(
        self,
        subject: ProviderSubject,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> ProviderSubject:
        account = subject.require_account()
        async with self._repo_factory() as repos:
            auth_manager = AuthManager(repos.accounts)
            refreshed = await auth_manager.ensure_fresh(account, force=force)
        return replace(subject, account=refreshed)

    def check_capabilities(
        self,
        subject: ProviderSubject,
        capabilities: RequestCapabilities,
    ) -> ProviderCapabilityDecision:
        del subject, capabilities
        return ProviderCapabilityDecision(allowed=True)

    async def fetch_models(
        self,
        subject: ProviderSubject,
        *,
        headers: Mapping[str, str] | None = None,
        route_class: str | None = None,
    ) -> ProviderModelsResult:
        del route_class
        access_token, account_id = self._upstream_auth(subject)
        result = await _proxy_fetch_codex_models(headers or {}, access_token, account_id)
        return ProviderModelsResult(payload=result.payload, upstream_request_id=result.upstream_request_id)

    async def create_response(
        self,
        subject: ProviderSubject,
        payload: Mapping[str, JsonValue],
    ) -> ProviderCreateResponseResult:
        del subject, payload
        raise NotImplementedError("ChatGPT-web response execution stays on existing proxy paths in phase 1")

    async def stream_responses(
        self,
        subject: ProviderSubject,
        payload: Mapping[str, JsonValue],
    ) -> ProviderStreamResponseResult:
        del subject, payload
        raise NotImplementedError("ChatGPT-web streaming stays on existing proxy paths in phase 1")

    async def compact_response(
        self,
        subject: ProviderSubject,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
    ) -> ProviderCompactResponseResult:
        access_token, account_id = self._upstream_auth(subject)
        compact_impl = cast(
            _CompactResponsesCallable,
            _resolve_proxy_compat_callable("core_compact_responses", _DEFAULT_CORE_COMPACT_RESPONSES),
        )
        return ProviderCompactResponseResult(
            payload=await compact_impl(payload, headers, access_token, account_id),
            upstream_request_id=None,
        )

    async def stream_response_events(
        self,
        subject: ProviderSubject,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        raise_for_status: bool = True,
        upstream_stream_transport: str | None = None,
    ) -> AsyncIterator[str]:
        access_token, account_id = self._upstream_auth(subject)
        stream_impl = cast(
            "Callable[..., AsyncIterator[str]]",
            _resolve_proxy_compat_callable("core_stream_responses", _DEFAULT_CORE_STREAM_RESPONSES),
        )
        kwargs: dict[str, object] = {"raise_for_status": raise_for_status}
        stream_signature = inspect.signature(stream_impl)
        supports_transport_override = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == "upstream_stream_transport_override"
            for parameter in stream_signature.parameters.values()
        )
        if supports_transport_override:
            kwargs["upstream_stream_transport_override"] = upstream_stream_transport
        return stream_impl(payload, headers, access_token, account_id, **kwargs)

    async def transcribe_audio(
        self,
        subject: ProviderSubject,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers: Mapping[str, str],
    ) -> dict[str, JsonValue]:
        access_token, account_id = self._upstream_auth(subject)
        transcribe_impl = cast(
            _TranscribeAudioCallable,
            _resolve_proxy_compat_callable("core_transcribe_audio", _DEFAULT_CORE_TRANSCRIBE_AUDIO),
        )
        return await transcribe_impl(
            audio_bytes,
            filename=filename,
            content_type=content_type,
            prompt=prompt,
            headers=headers,
            access_token=access_token,
            account_id=account_id,
        )

    async def open_responses_websocket(
        self,
        subject: ProviderSubject,
        headers: Mapping[str, str],
    ) -> UpstreamResponsesWebSocket:
        access_token, account_id = self._upstream_auth(subject)
        connect_impl = cast(
            _ConnectResponsesWebSocketCallable,
            _resolve_proxy_compat_callable(
                "connect_responses_websocket",
                _DEFAULT_CONNECT_RESPONSES_WEBSOCKET,
            ),
        )
        return await connect_impl(dict(headers), access_token, account_id)

    async def refresh_usage(
        self,
        repos: ProxyRepositories,
        subjects: Sequence[ProviderSubject],
    ) -> None:
        accounts = [subject.account for subject in subjects if subject.account is not None]
        if not accounts:
            return
        latest_usage = await repos.usage.latest_by_account()
        updater = UsageUpdater(repos.usage, repos.accounts, repos.additional_usage)
        await updater.refresh_accounts(accounts, latest_usage)


class OpenAIPlatformProviderAdapter:
    provider_kind: ProviderKind = OPENAI_PLATFORM_PROVIDER_KIND

    def __init__(self, *, base_url: str = "https://api.openai.com") -> None:
        self._base_url = base_url
        self._encryptor = TokenEncryptor()

    def _maybe_log_request_start(
        self,
        *,
        kind: str,
        method: str,
        subject: ProviderSubject,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> float:
        started_at = time.monotonic()
        if not get_settings().log_upstream_request_summary:
            return started_at
        logger.info(
            (
                "upstream_request_start request_id=%s kind=%s method=%s target=%s "
                "provider_kind=%s route_class=%s routing_subject_id=%s account_id=%s upstream_request_id=%s"
            ),
            get_request_id(),
            kind,
            method,
            self._base_url,
            self.provider_kind,
            route_class,
            subject.routing_subject_id,
            None,
            None,
        )
        return started_at

    def _maybe_log_request_complete(
        self,
        *,
        kind: str,
        method: str,
        subject: ProviderSubject,
        started_at: float,
        status_code: int | None,
        error_code: str | None,
        error_message: str | None,
        upstream_request_id: str | None = None,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> None:
        if not get_settings().log_upstream_request_summary:
            return
        level = logging.INFO
        if status_code is not None and status_code >= 500:
            level = logging.ERROR
        elif (status_code is not None and status_code >= 400) or error_code is not None:
            level = logging.WARNING
        logger.log(
            level,
            (
                "upstream_request_complete request_id=%s kind=%s method=%s target=%s "
                "provider_kind=%s route_class=%s routing_subject_id=%s account_id=%s "
                "status=%s duration_ms=%s error_code=%s error_message=%s upstream_request_id=%s"
            ),
            get_request_id(),
            kind,
            method,
            self._base_url,
            self.provider_kind,
            route_class,
            subject.routing_subject_id,
            None,
            status_code,
            int((time.monotonic() - started_at) * 1000),
            error_code,
            error_message,
            upstream_request_id,
        )

    async def ensure_ready(
        self,
        subject: ProviderSubject,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> ProviderSubject:
        del force
        del timeout_seconds
        return subject

    def check_capabilities(
        self,
        subject: ProviderSubject,
        capabilities: RequestCapabilities,
    ) -> ProviderCapabilityDecision:
        del subject
        if capabilities.transport == "websocket":
            return ProviderCapabilityDecision(
                allowed=False,
                error_code="provider_transport_unsupported",
                error_message="OpenAI Platform identities do not support websocket /v1/responses routes in phase 1.",
                error_param="transport",
                rejection_reason="provider_transport_unsupported",
            )
        if capabilities.continuity_param is not None:
            return ProviderCapabilityDecision(
                allowed=False,
                error_code="provider_continuity_unsupported",
                error_message=(
                    f"OpenAI Platform identities do not support "
                    f"'{capabilities.continuity_param}' continuity in phase 1."
                ),
                error_param=capabilities.continuity_param,
                rejection_reason="platform_continuity_unsupported",
            )
        if capabilities.route_family not in PHASE1_PLATFORM_ROUTE_FAMILIES:
            return ProviderCapabilityDecision(
                allowed=False,
                error_code="provider_feature_unsupported",
                error_message=f"OpenAI Platform identities do not support {capabilities.route_family} in phase 1.",
                rejection_reason="provider_feature_unsupported",
            )
        return ProviderCapabilityDecision(allowed=True)

    async def fetch_models(
        self,
        subject: ProviderSubject,
        *,
        headers: Mapping[str, str] | None = None,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> ProviderModelsResult:
        del headers
        started_at = self._maybe_log_request_start(
            kind="platform_models",
            method="GET",
            subject=subject,
            route_class=route_class,
        )
        try:
            result = await fetch_platform_models(
                base_url=self._base_url,
                api_key=self._encryptor.decrypt(subject.require_api_key_encrypted()),
                organization=subject.organization_id,
                project=subject.project_id,
            )
        except OpenAIPlatformError as exc:
            self._maybe_log_request_complete(
                kind="platform_models",
                method="GET",
                subject=subject,
                started_at=started_at,
                status_code=exc.status_code,
                error_code=_platform_error_code(exc.payload),
                error_message=_platform_error_message(exc.payload),
                upstream_request_id=exc.upstream_request_id,
                route_class=route_class,
            )
            raise
        self._maybe_log_request_complete(
            kind="platform_models",
            method="GET",
            subject=subject,
            started_at=started_at,
            status_code=200,
            error_code=None,
            error_message=None,
            upstream_request_id=result.upstream_request_id,
            route_class=route_class,
        )
        return ProviderModelsResult(payload=result.payload, upstream_request_id=result.upstream_request_id)

    async def create_response(
        self,
        subject: ProviderSubject,
        payload: Mapping[str, JsonValue],
        *,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> ProviderCreateResponseResult:
        started_at = self._maybe_log_request_start(
            kind="platform_responses",
            method="POST",
            subject=subject,
            route_class=route_class,
        )
        try:
            result = await create_platform_response(
                base_url=self._base_url,
                payload=payload,
                api_key=self._encryptor.decrypt(subject.require_api_key_encrypted()),
                organization=subject.organization_id,
                project=subject.project_id,
            )
        except OpenAIPlatformError as exc:
            self._maybe_log_request_complete(
                kind="platform_responses",
                method="POST",
                subject=subject,
                started_at=started_at,
                status_code=exc.status_code,
                error_code=_platform_error_code(exc.payload),
                error_message=_platform_error_message(exc.payload),
                upstream_request_id=exc.upstream_request_id,
                route_class=route_class,
            )
            raise
        status = _platform_response_status(result.payload)
        self._maybe_log_request_complete(
            kind="platform_responses",
            method="POST",
            subject=subject,
            started_at=started_at,
            status_code=200,
            error_code=None if status != "failed" else _platform_response_error_code(result.payload),
            error_message=None if status != "failed" else _platform_response_error_message(result.payload),
            upstream_request_id=result.upstream_request_id,
            route_class=route_class,
        )
        return ProviderCreateResponseResult(
            payload=result.payload,
            upstream_request_id=result.upstream_request_id,
        )

    async def compact_response(
        self,
        subject: ProviderSubject,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
        *,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> ProviderCompactResponseResult:
        del headers
        started_at = self._maybe_log_request_start(
            kind="platform_compact",
            method="POST",
            subject=subject,
            route_class=route_class,
        )
        try:
            payload_dict: dict[str, JsonValue] = dict(payload.to_payload().items())
            forwarded_service_tier = payload.platform_forwarded_service_tier()
            if forwarded_service_tier is not None:
                payload_dict["service_tier"] = forwarded_service_tier
            result = await create_platform_compact_response(
                base_url=self._base_url,
                payload=payload_dict,
                api_key=self._encryptor.decrypt(subject.require_api_key_encrypted()),
                organization=subject.organization_id,
                project=subject.project_id,
            )
        except OpenAIPlatformError as exc:
            self._maybe_log_request_complete(
                kind="platform_compact",
                method="POST",
                subject=subject,
                started_at=started_at,
                status_code=exc.status_code,
                error_code=_platform_error_code(exc.payload),
                error_message=_platform_error_message(exc.payload),
                upstream_request_id=exc.upstream_request_id,
                route_class=route_class,
            )
            raise
        status = result.payload.status
        self._maybe_log_request_complete(
            kind="platform_compact",
            method="POST",
            subject=subject,
            started_at=started_at,
            status_code=200,
            error_code=None if status != "failed" else result.payload.error.code if result.payload.error else None,
            error_message=(
                None if status != "failed" else result.payload.error.message if result.payload.error else None
            ),
            upstream_request_id=result.upstream_request_id,
            route_class=route_class,
        )
        return ProviderCompactResponseResult(
            payload=result.payload,
            upstream_request_id=result.upstream_request_id,
        )

    async def stream_responses(
        self,
        subject: ProviderSubject,
        payload: Mapping[str, JsonValue],
        *,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> ProviderStreamResponseResult:
        started_at = self._maybe_log_request_start(
            kind="platform_responses",
            method="POST",
            subject=subject,
            route_class=route_class,
        )
        try:
            result = await stream_platform_responses(
                base_url=self._base_url,
                payload=payload,
                api_key=self._encryptor.decrypt(subject.require_api_key_encrypted()),
                organization=subject.organization_id,
                project=subject.project_id,
            )
        except OpenAIPlatformError as exc:
            self._maybe_log_request_complete(
                kind="platform_responses",
                method="POST",
                subject=subject,
                started_at=started_at,
                status_code=exc.status_code,
                error_code=_platform_error_code(exc.payload),
                error_message=_platform_error_message(exc.payload),
                upstream_request_id=exc.upstream_request_id,
                route_class=route_class,
            )
            raise
        self._maybe_log_request_complete(
            kind="platform_responses",
            method="POST",
            subject=subject,
            started_at=started_at,
            status_code=200,
            error_code=None,
            error_message=None,
            upstream_request_id=result.upstream_request_id,
            route_class=route_class,
        )
        return ProviderStreamResponseResult(
            event_stream=result.event_stream,
            upstream_request_id=result.upstream_request_id,
        )

    async def validate_identity(
        self,
        *,
        api_key: str,
        organization: str | None = None,
        project: str | None = None,
    ) -> ProviderModelsResult:
        subject = ProviderSubject(
            provider_kind=self.provider_kind,
            routing_subject_id="platform_validation",
        )
        started_at = self._maybe_log_request_start(kind="platform_models_validate", method="GET", subject=subject)
        try:
            result = await validate_platform_identity(
                base_url=self._base_url,
                api_key=api_key,
                organization=organization,
                project=project,
            )
        except OpenAIPlatformError as exc:
            self._maybe_log_request_complete(
                kind="platform_models_validate",
                method="GET",
                subject=subject,
                started_at=started_at,
                status_code=exc.status_code,
                error_code=_platform_error_code(exc.payload),
                error_message=_platform_error_message(exc.payload),
                upstream_request_id=exc.upstream_request_id,
            )
            raise
        self._maybe_log_request_complete(
            kind="platform_models_validate",
            method="GET",
            subject=subject,
            started_at=started_at,
            status_code=200,
            error_code=None,
            error_message=None,
            upstream_request_id=result.upstream_request_id,
        )
        return ProviderModelsResult(payload=result.payload, upstream_request_id=result.upstream_request_id)


def _platform_error_code(payload: Mapping[str, JsonValue]) -> str | None:
    error = payload.get("error")
    if not is_json_mapping(error):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def _platform_error_message(payload: Mapping[str, JsonValue]) -> str | None:
    error = payload.get("error")
    if not is_json_mapping(error):
        return None
    message = error.get("message")
    return message if isinstance(message, str) else None


def _platform_response_status(payload: OpenAIResponsePayload | dict[str, JsonValue]) -> str | None:
    if isinstance(payload, OpenAIResponsePayload):
        return payload.status
    status = payload.get("status")
    return status if isinstance(status, str) else None


def _platform_response_error_code(payload: OpenAIResponsePayload | dict[str, JsonValue]) -> str | None:
    if isinstance(payload, OpenAIResponsePayload):
        return payload.error.code if payload.error is not None else None
    return _platform_error_code(payload)


def _platform_response_error_message(payload: OpenAIResponsePayload | dict[str, JsonValue]) -> str | None:
    if isinstance(payload, OpenAIResponsePayload):
        return payload.error.message if payload.error is not None else None
    return _platform_error_message(payload)
