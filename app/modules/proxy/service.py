from __future__ import annotations

import asyncio
import gzip
import inspect
import json
import logging
import re
import time
from collections import deque
from collections.abc import AsyncGenerator, Collection, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Mapping, NoReturn, TypeVar, cast, overload
from urllib.parse import urlparse
from uuid import uuid4

import aiohttp
import anyio
from fastapi import WebSocket
from pydantic import ValidationError

from app.core import shutdown as shutdown_state
from app.core import usage as usage_core
from app.core.auth.refresh import (
    RefreshError,
    pop_token_refresh_timeout_override,
    push_token_refresh_timeout_override,
)
from app.core.balancer import PERMANENT_FAILURE_CODES, RoutingStrategy, failover_decision
from app.core.balancer.rendezvous_hash import select_node
from app.core.balancer.types import ClassifiedFailure, UpstreamError
from app.core.clients.openai_platform import (
    OpenAIPlatformError,
    PlatformModelsResponse,
    PlatformResponseResult,
    PlatformStreamResponse,
)
from app.core.clients.proxy import (
    ProxyResponseError,
    filter_inbound_headers,
    pop_compact_timeout_overrides,
    pop_stream_timeout_overrides,
    pop_transcribe_timeout_overrides,
    push_compact_timeout_overrides,
    push_stream_timeout_overrides,
    push_transcribe_timeout_overrides,
)
from app.core.clients.proxy_websocket import (
    UpstreamResponsesWebSocket,
    UpstreamWebSocketMessage,
    filter_inbound_websocket_headers,
)
from app.core.config.settings import Settings, get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.errors import (
    OpenAIErrorDetail,
    OpenAIErrorEnvelope,
    ResponseFailedEvent,
    openai_error,
    response_failed_event,
)
from app.core.exceptions import AppError, ProxyAuthError, ProxyRateLimitError
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_drain_recovery_allowed_total,
    bridge_durable_recover_total,
    bridge_first_turn_timeout_total,
    bridge_forward_latency_seconds,
    bridge_instance_mismatch_total,
    bridge_local_rebind_total,
    bridge_owner_forward_total,
    bridge_owner_mismatch_total,
    bridge_prompt_cache_locality_miss_total,
    bridge_reattach_total,
    bridge_same_account_takeover_total,
    bridge_soft_local_rebind_total,
    continuity_fail_closed_total,
    continuity_owner_resolution_total,
)
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.models import CompactResponsePayload, OpenAIEvent, OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.resilience.overload import is_local_overload_error_code
from app.core.types import JsonValue
from app.core.usage.types import UsageWindowRow
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.retry import backoff_seconds
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import (
    Account,
    AccountStatus,
    DashboardSettings,
    HttpBridgeSessionState,
    StickySessionKind,
    UsageHistory,
)
from app.db.session import SessionLocal
from app.modules.accounts.auth_manager import AuthManager
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeysService,
    ApiKeyUsageReservationData,
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup,
    DurableBridgeSessionCoordinator,
)
from app.modules.proxy.helpers import (
    _apply_error_metadata,
    _credits_headers,
    _credits_snapshot,
    _normalize_error_code,
    _parse_openai_error,
    _plan_type_for_accounts,
    _rate_limit_details,
    _rate_limit_headers,
    _select_accounts_for_limits,
    _summarize_window,
    _upstream_error_from_openai,
    _window_snapshot,
    classify_upstream_failure,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeForwardContext,
    HTTPBridgeOwnerClient,
    OwnerForwardRelayFailure,
)
from app.modules.proxy.load_balancer import (
    AccountSelection,
    LoadBalancer,
    _filter_accounts_for_model,
    _gated_limit_name_for_model,
)
from app.modules.proxy.provider_adapters import (
    ChatGPTWebProviderAdapter,
    OpenAIPlatformProviderAdapter,
    ProviderAdapter,
    ProviderCapabilityDecision,
    ProviderCompactResponseResult,
    ProviderModelsResult,
    ProviderSubject,
    RequestCapabilities,
)
from app.modules.proxy.provider_adapters import (
    core_compact_responses as _adapter_core_compact_responses,
)
from app.modules.proxy.provider_adapters import (
    core_stream_responses as _adapter_core_stream_responses,
)
from app.modules.proxy.provider_adapters import (
    core_transcribe_audio as _adapter_core_transcribe_audio,
)
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    normalize_responses_request_payload,
    openai_invalid_payload_error,
    openai_validation_error,
    validate_model_access,
)
from app.modules.proxy.ring_membership import (
    RING_STALE_THRESHOLD_SECONDS,
    RingMembershipService,
    dynamic_bridge_ring_membership_enabled,
)
from app.modules.proxy.types import (
    AdditionalRateLimitData,
    RateLimitStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)
from app.modules.proxy.work_admission import AdmissionLease, WorkAdmissionController
from app.modules.upstream_identities.types import (
    BACKEND_CODEX_HTTP_ROUTE_FAMILY,
    CHATGPT_PRIVATE_ROUTE_CLASS,
    CHATGPT_WEB_PROVIDER_KIND,
    OPENAI_PLATFORM_PROVIDER_KIND,
    OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    PUBLIC_MODELS_HTTP_ROUTE_FAMILY,
    PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
)
from app.modules.usage.additional_quota_keys import get_additional_display_label_for_quota_key

logger = logging.getLogger(__name__)

# Compatibility seam for tests and legacy patch points. Provider adapters consult
# these callables when they retain their default transport bindings.
core_compact_responses = _adapter_core_compact_responses
core_stream_responses = _adapter_core_stream_responses
core_transcribe_audio = _adapter_core_transcribe_audio

# Stay below the common 16 MiB websocket message ceiling so we can slim or fail
# early before upstream closes the session with 1009.
_UPSTREAM_RESPONSE_CREATE_WARN_BYTES = 12 * 1024 * 1024
_UPSTREAM_RESPONSE_CREATE_MAX_BYTES = 15 * 1024 * 1024
_OVERSIZED_RESPONSE_CREATE_DUMP_DIR = Path("/var/lib/codex-lb/debug/response-create-dumps")
_OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS = 10
_RESPONSE_CREATE_HISTORY_OMISSION_NOTICE = (
    "[codex-lb omitted {count} historical input items to fit upstream websocket budget]"
)
_RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE = (
    "[codex-lb omitted historical tool output ({bytes} bytes) to fit upstream websocket budget]"
)
_RESPONSE_CREATE_IMAGE_OMISSION_NOTICE = "[codex-lb omitted historical inline image to fit upstream websocket budget]"

_TASK_CANCEL_TIMEOUT_SECONDS = 1.0
_TaskResultT = TypeVar("_TaskResultT")
_DOWNSTREAM_WEBSOCKET_IDLE_CLOSE_REASON = "Idle downstream websocket timeout"
_DOWNSTREAM_WEBSOCKET_RECEIVE_POLL_SECONDS = 1.0


async def _await_cancelled_task(
    task: asyncio.Task[_TaskResultT],
    *,
    timeout_seconds: float = _TASK_CANCEL_TIMEOUT_SECONDS,
    label: str,
) -> bool:
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout_seconds)
    except asyncio.CancelledError:
        return True
    except TimeoutError:
        logger.warning("Timed out waiting for %s cancellation", label)
        return False
    return True


_TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
_TEXT_DONE_CONTENT_PART_TYPES = frozenset({"output_text", "refusal"})
_REQUEST_TRANSPORT_HTTP = "http"
_REQUEST_TRANSPORT_WEBSOCKET = "websocket"
_COMPACT_SAME_CONTRACT_RETRY_BUDGET = 1
_ACCOUNT_RECOVERY_RETRY_CODES = frozenset(
    {
        "rate_limit_exceeded",
        "usage_limit_reached",
        "insufficient_quota",
        "usage_not_included",
        "quota_exceeded",
        *PERMANENT_FAILURE_CODES.keys(),
    }
)
_TRANSIENT_RETRY_CODES = frozenset({"server_error"})
_MAX_TRANSIENT_SAME_ACCOUNT_RETRIES = 3
_COMPACT_MAX_ACCOUNT_ATTEMPTS = 2
_STREAM_MAX_ACCOUNT_ATTEMPTS = 3
_WEBSOCKET_MAX_ACCOUNT_ATTEMPTS = 3
_WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES = frozenset(
    {
        "rate_limit_exceeded",
        "usage_limit_reached",
        "insufficient_quota",
        "usage_not_included",
        "quota_exceeded",
    }
)
_WEBSOCKET_PREVIOUS_RESPONSE_ACCOUNT_CACHE_LIMIT = 4096


@dataclass(frozen=True, slots=True)
class _AffinityPolicy:
    key: str | None = None
    kind: StickySessionKind | None = None
    reallocate_sticky: bool = False
    max_age_seconds: int | None = None
    budget_reallocation_enabled: bool = True


@dataclass(frozen=True, slots=True)
class _SelectedPlatformIdentity:
    id: str
    api_key_encrypted: bytes
    organization_id: str | None
    project_id: str | None


@dataclass(frozen=True, slots=True)
class SelectedChatGPTSubject:
    provider_kind: str
    route_class: str
    routing_subject_id: str


@dataclass(frozen=True, slots=True)
class SelectedPlatformSubject:
    provider_kind: str
    route_class: str
    routing_subject_id: str
    identity: _SelectedPlatformIdentity


@dataclass(frozen=True, slots=True)
class ProviderSelectionFailure:
    http_status: int
    error_code: str
    error_message: str
    rejection_reason: str
    route_class: str
    error_param: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSelectionResult:
    selected: SelectedChatGPTSubject | SelectedPlatformSubject | None = None
    failure: ProviderSelectionFailure | None = None

    @property
    def provider_kind(self) -> str | None:
        return self.selected.provider_kind if self.selected is not None else None

    @property
    def is_platform(self) -> bool:
        return isinstance(self.selected, SelectedPlatformSubject)

    @property
    def is_chatgpt(self) -> bool:
        return isinstance(self.selected, SelectedChatGPTSubject)


@dataclass(frozen=True, slots=True)
class _HTTPBridgeRuntimeConfig:
    enabled: bool
    idle_ttl_seconds: float
    codex_idle_ttl_seconds: float
    max_sessions: int
    queue_limit: int
    prompt_cache_idle_ttl_seconds: float
    gateway_safe_mode: bool


def _resolve_upstream_stream_transport(upstream_stream_transport: str) -> str | None:
    if upstream_stream_transport == "default":
        return None
    return upstream_stream_transport


def _fingerprint_input_items(items: Sequence[JsonValue]) -> str:
    """Return stable SHA-256 fingerprint for input list canonical JSON."""
    canonical = json.dumps(list(items), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


class ProxyService:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._encryptor = TokenEncryptor()
        self._load_balancer = LoadBalancer(repo_factory)
        self._provider_adapters: dict[str, ProviderAdapter] = {
            CHATGPT_WEB_PROVIDER_KIND: ChatGPTWebProviderAdapter(repo_factory),
            OPENAI_PLATFORM_PROVIDER_KIND: OpenAIPlatformProviderAdapter(),
        }
        self._ring_membership = RingMembershipService(SessionLocal)
        self._durable_bridge = DurableBridgeSessionCoordinator(SessionLocal)
        self._http_bridge_owner_client = HTTPBridgeOwnerClient()
        self._http_bridge_sessions: dict[_HTTPBridgeSessionKey, _HTTPBridgeSession] = {}
        self._http_bridge_inflight_sessions: dict[_HTTPBridgeSessionKey, asyncio.Future[_HTTPBridgeSession]] = {}
        self._http_bridge_turn_state_index: dict[tuple[str, str | None], _HTTPBridgeSessionKey] = {}
        self._http_bridge_previous_response_index: dict[tuple[str, str | None], _HTTPBridgeSessionKey] = {}
        self._websocket_previous_response_account_index: dict[tuple[str, str | None, str | None], str] = {}
        self._http_bridge_lock = anyio.Lock()
        self._work_admission: WorkAdmissionController | None = None

    def _get_work_admission(self) -> WorkAdmissionController:
        if self._work_admission is None:
            settings = get_settings()
            self._work_admission = WorkAdmissionController(
                token_refresh_limit=settings.proxy_token_refresh_limit,
                websocket_connect_limit=settings.proxy_upstream_websocket_connect_limit,
                response_create_limit=settings.proxy_response_create_limit,
                compact_response_create_limit=settings.proxy_compact_response_create_limit,
                admission_wait_timeout_seconds=getattr(
                    settings,
                    "proxy_admission_wait_timeout_seconds",
                    10.0,
                ),
            )
        return self._work_admission

    def _provider_adapter(self, provider_kind: str) -> ProviderAdapter:
        return self._provider_adapters[provider_kind]

    @staticmethod
    def _platform_provider_subject(identity: _SelectedPlatformIdentity) -> ProviderSubject:
        return ProviderSubject(
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id=identity.id,
            api_key_encrypted=identity.api_key_encrypted,
            organization_id=identity.organization_id,
            project_id=identity.project_id,
        )

    @staticmethod
    def _chatgpt_provider_subject(account: Account) -> ProviderSubject:
        return ProviderSubject(
            provider_kind=CHATGPT_WEB_PROVIDER_KIND,
            routing_subject_id=account.id,
            account=account,
        )

    @staticmethod
    def _provider_selection_failure(
        decision: ProviderCapabilityDecision,
        capabilities: RequestCapabilities,
    ) -> ProviderSelectionResult:
        if decision.allowed:
            raise ValueError("Provider selection failure requested for an allowed capability decision")
        if decision.error_code is None or decision.error_message is None or decision.rejection_reason is None:
            raise ValueError("Provider capability decision is missing failure metadata")
        return ProviderSelectionResult(
            failure=ProviderSelectionFailure(
                http_status=400,
                error_code=decision.error_code,
                error_message=decision.error_message,
                rejection_reason=decision.rejection_reason,
                route_class=capabilities.route_class,
                error_param=decision.error_param,
            )
        )

    async def has_chatgpt_candidates(
        self,
        model: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> bool:
        async with self._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            allowed_account_ids = None if account_ids is None else set(account_ids)
            active_accounts = [
                account
                for account in accounts
                if account.status not in (AccountStatus.PAUSED, AccountStatus.DEACTIVATED)
                and (allowed_account_ids is None or account.id in allowed_account_ids)
            ]
            if model is None:
                return bool(active_accounts)
            return bool(_filter_accounts_for_model(active_accounts, model))

    async def has_compatible_chatgpt_candidates(
        self,
        model: str | None = None,
        *,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
    ) -> bool:
        return await self._load_balancer.has_compatible_chatgpt_candidates(
            model=model,
            additional_limit_name=additional_limit_name,
            account_ids=account_ids,
        )

    async def chatgpt_compatibility_failure(
        self,
        model: str | None = None,
        *,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
    ) -> tuple[str | None, str | None]:
        return await self._load_balancer.chatgpt_compatibility_failure(
            model=model,
            additional_limit_name=additional_limit_name,
            account_ids=account_ids,
        )

    async def should_fallback_to_platform_for_usage_drain(
        self,
        *,
        model: str | None,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
    ) -> bool:
        return await self._load_balancer.should_fallback_to_platform_for_usage_drain(
            model=model,
            additional_limit_name=additional_limit_name,
            account_ids=account_ids,
        )

    async def sticky_chatgpt_target_is_healthy_for_platform_fallback(
        self,
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        sticky_max_age_seconds: int | None,
        model: str | None,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
    ) -> bool:
        return await self._load_balancer.sticky_chatgpt_target_is_healthy_for_platform_fallback(
            sticky_key=sticky_key,
            sticky_kind=sticky_kind,
            sticky_max_age_seconds=sticky_max_age_seconds,
            model=model,
            additional_limit_name=additional_limit_name,
            account_ids=account_ids,
        )

    async def sticky_chatgpt_target_is_selectable_for_platform_fallback(
        self,
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        sticky_max_age_seconds: int | None,
        model: str | None,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
    ) -> bool:
        return await self._load_balancer.sticky_chatgpt_target_is_selectable_for_platform_fallback(
            sticky_key=sticky_key,
            sticky_kind=sticky_kind,
            sticky_max_age_seconds=sticky_max_age_seconds,
            model=model,
            additional_limit_name=additional_limit_name,
            account_ids=account_ids,
        )

    @staticmethod
    def _hard_affinity_for_provider_fallback(
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
    ) -> bool:
        return bool(sticky_key) and sticky_kind == StickySessionKind.CODEX_SESSION

    @staticmethod
    def _platform_affinity_for_selection(
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        reallocate_sticky: bool,
        sticky_max_age_seconds: int | None,
    ) -> tuple[str | None, StickySessionKind | None, bool, int | None]:
        if sticky_key and sticky_kind == StickySessionKind.PROMPT_CACHE:
            return sticky_key, sticky_kind, reallocate_sticky, sticky_max_age_seconds
        return None, None, False, None

    async def select_routing_subject(
        self,
        *,
        capabilities: RequestCapabilities,
        api_key: ApiKeyData | None = None,
        sticky_key: str | None = None,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
    ) -> ProviderSelectionResult:
        scoped_account_ids = (
            api_key.assigned_account_ids if api_key is not None and api_key.account_assignment_scope_enabled else None
        )
        additional_limit_name = _gated_limit_name_for_model(capabilities.model)
        has_active_chatgpt = await self.has_chatgpt_candidates(account_ids=scoped_account_ids)
        has_compatible_chatgpt = await self.has_compatible_chatgpt_candidates(
            capabilities.model,
            additional_limit_name=additional_limit_name,
            account_ids=scoped_account_ids,
        )
        platform_adapter = cast(
            OpenAIPlatformProviderAdapter,
            self._provider_adapter(OPENAI_PLATFORM_PROVIDER_KIND),
        )

        is_models_route = capabilities.route_family == PUBLIC_MODELS_HTTP_ROUTE_FAMILY or (
            capabilities.route_family == BACKEND_CODEX_HTTP_ROUTE_FAMILY and capabilities.model is None
        )

        if is_models_route:
            identity = await self.select_platform_identity(capabilities.route_family)
            if has_active_chatgpt:
                should_fallback = await self.should_fallback_to_platform_for_usage_drain(
                    model=None,
                    account_ids=scoped_account_ids,
                )
                if should_fallback:
                    if identity is not None:
                        decision = platform_adapter.check_capabilities(
                            self._platform_provider_subject(identity),
                            capabilities,
                        )
                        if decision.allowed:
                            return ProviderSelectionResult(
                                selected=SelectedPlatformSubject(
                                    provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                                    route_class=capabilities.route_class,
                                    routing_subject_id=identity.id,
                                    identity=identity,
                                )
                            )
                return ProviderSelectionResult(
                    selected=SelectedChatGPTSubject(
                        provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                        route_class=capabilities.route_class,
                        routing_subject_id="chatgpt_web_pool",
                    )
                )
            if identity is not None:
                return ProviderSelectionResult(
                    failure=ProviderSelectionFailure(
                        http_status=400,
                        error_code="provider_fallback_requires_chatgpt",
                        error_message="OpenAI Platform fallback requires at least one active ChatGPT-web account.",
                        rejection_reason="platform_fallback_requires_chatgpt",
                        route_class=capabilities.route_class,
                    )
                )
            return ProviderSelectionResult()

        if capabilities.transport == _REQUEST_TRANSPORT_WEBSOCKET:
            if has_compatible_chatgpt:
                return ProviderSelectionResult(
                    selected=SelectedChatGPTSubject(
                        provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                        route_class=capabilities.route_class,
                        routing_subject_id="chatgpt_web_pool",
                    )
                )
            identity = await self.select_platform_identity(capabilities.route_family)
            if identity is None:
                if has_active_chatgpt:
                    error_code, error_message = await self.chatgpt_compatibility_failure(
                        capabilities.model,
                        additional_limit_name=additional_limit_name,
                        account_ids=scoped_account_ids,
                    )
                    if error_code is not None and error_message is not None:
                        return ProviderSelectionResult(
                            failure=ProviderSelectionFailure(
                                http_status=400,
                                error_code=error_code,
                                error_message=error_message,
                                rejection_reason=error_code,
                                route_class=capabilities.route_class,
                            )
                        )
                    return ProviderSelectionResult(
                        selected=SelectedChatGPTSubject(
                            provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                            route_class=capabilities.route_class,
                            routing_subject_id="chatgpt_web_pool",
                        )
                    )
                return ProviderSelectionResult()
            decision = platform_adapter.check_capabilities(
                self._platform_provider_subject(identity),
                capabilities,
            )
            return self._provider_selection_failure(decision, capabilities)

        if capabilities.continuity_param is not None:
            if has_compatible_chatgpt:
                return ProviderSelectionResult(
                    selected=SelectedChatGPTSubject(
                        provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                        route_class=capabilities.route_class,
                        routing_subject_id="chatgpt_web_pool",
                    )
                )
            identity = await self.select_platform_identity(capabilities.route_family)
            if identity is None:
                if has_active_chatgpt:
                    error_code, error_message = await self.chatgpt_compatibility_failure(
                        capabilities.model,
                        additional_limit_name=additional_limit_name,
                        account_ids=scoped_account_ids,
                    )
                    if error_code is not None and error_message is not None:
                        return ProviderSelectionResult(
                            failure=ProviderSelectionFailure(
                                http_status=400,
                                error_code=error_code,
                                error_message=error_message,
                                rejection_reason=error_code,
                                route_class=capabilities.route_class,
                            )
                        )
                    return ProviderSelectionResult(
                        selected=SelectedChatGPTSubject(
                            provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                            route_class=capabilities.route_class,
                            routing_subject_id="chatgpt_web_pool",
                        )
                    )
                return ProviderSelectionResult()
            decision = platform_adapter.check_capabilities(
                self._platform_provider_subject(identity),
                capabilities,
            )
            return self._provider_selection_failure(decision, capabilities)

        identity = await self.select_platform_identity(capabilities.route_family)
        if has_compatible_chatgpt:
            should_fallback = await self.should_fallback_to_platform_for_usage_drain(
                model=capabilities.model,
                additional_limit_name=additional_limit_name,
                account_ids=scoped_account_ids,
            )
            if (
                should_fallback
                and not bool(getattr(get_settings(), "platform_fallback_force_enabled", False))
                and self._hard_affinity_for_provider_fallback(
                    sticky_key=sticky_key,
                    sticky_kind=sticky_kind,
                )
            ):
                sticky_chatgpt_healthy = await self.sticky_chatgpt_target_is_healthy_for_platform_fallback(
                    sticky_key=sticky_key,
                    sticky_kind=cast(StickySessionKind, sticky_kind),
                    sticky_max_age_seconds=sticky_max_age_seconds,
                    model=capabilities.model,
                    additional_limit_name=additional_limit_name,
                    account_ids=scoped_account_ids,
                )
                if sticky_chatgpt_healthy:
                    should_fallback = False
                    logger.info(
                        "Suppressed usage-drain Platform fallback for healthy sticky ChatGPT target "
                        "request_id=%s route_family=%s sticky_kind=%s continuity_hint=%s",
                        get_request_id(),
                        capabilities.route_family,
                        sticky_kind.value if sticky_kind is not None else None,
                        capabilities.continuity_hint,
                    )
                elif capabilities.continuity_hint is not None:
                    sticky_chatgpt_selectable = await self.sticky_chatgpt_target_is_selectable_for_platform_fallback(
                        sticky_key=sticky_key,
                        sticky_kind=cast(StickySessionKind, sticky_kind),
                        sticky_max_age_seconds=sticky_max_age_seconds,
                        model=capabilities.model,
                        additional_limit_name=additional_limit_name,
                        account_ids=scoped_account_ids,
                    )
                    if sticky_chatgpt_selectable:
                        should_fallback = False
                        logger.info(
                            "Suppressed usage-drain Platform fallback for selectable sticky ChatGPT target "
                            "request_id=%s route_family=%s sticky_kind=%s continuity_hint=%s",
                            get_request_id(),
                            capabilities.route_family,
                            sticky_kind.value if sticky_kind is not None else None,
                            capabilities.continuity_hint,
                        )
            if should_fallback:
                if identity is not None:
                    (
                        platform_sticky_key,
                        platform_sticky_kind,
                        platform_reallocate_sticky,
                        platform_sticky_max_age_seconds,
                    ) = self._platform_affinity_for_selection(
                        sticky_key=sticky_key,
                        sticky_kind=sticky_kind,
                        reallocate_sticky=reallocate_sticky,
                        sticky_max_age_seconds=sticky_max_age_seconds,
                    )
                    identity = await self.select_platform_identity(
                        capabilities.route_family,
                        sticky_key=platform_sticky_key,
                        sticky_kind=platform_sticky_kind,
                        reallocate_sticky=platform_reallocate_sticky,
                        sticky_max_age_seconds=platform_sticky_max_age_seconds,
                    )
                if identity is not None:
                    decision = platform_adapter.check_capabilities(
                        self._platform_provider_subject(identity),
                        capabilities,
                    )
                    if decision.allowed:
                        return ProviderSelectionResult(
                            selected=SelectedPlatformSubject(
                                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                                route_class=capabilities.route_class,
                                routing_subject_id=identity.id,
                                identity=identity,
                            )
                        )
            return ProviderSelectionResult(
                selected=SelectedChatGPTSubject(
                    provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                    route_class=capabilities.route_class,
                    routing_subject_id="chatgpt_web_pool",
                )
            )
        if identity is not None and has_active_chatgpt:
            decision = platform_adapter.check_capabilities(
                self._platform_provider_subject(identity),
                capabilities,
            )
            if decision.allowed:
                return ProviderSelectionResult(
                    selected=SelectedPlatformSubject(
                        provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                        route_class=capabilities.route_class,
                        routing_subject_id=identity.id,
                        identity=identity,
                    )
                )
            return self._provider_selection_failure(decision, capabilities)
        if identity is not None and not has_active_chatgpt:
            return ProviderSelectionResult(
                failure=ProviderSelectionFailure(
                    http_status=400,
                    error_code="provider_fallback_requires_chatgpt",
                    error_message="OpenAI Platform fallback requires at least one active ChatGPT-web account.",
                    rejection_reason="platform_fallback_requires_chatgpt",
                    route_class=capabilities.route_class,
                )
            )
        if has_active_chatgpt:
            return ProviderSelectionResult(
                selected=SelectedChatGPTSubject(
                    provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                    route_class=capabilities.route_class,
                    routing_subject_id="chatgpt_web_pool",
                )
            )
        return ProviderSelectionResult()

    async def select_platform_identity(
        self,
        route_family: str,
        *,
        sticky_key: str | None = None,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
        exclude_routing_subject_ids: Collection[str] | None = None,
    ) -> _SelectedPlatformIdentity | None:
        selection = await self._load_balancer.select_routing_subject(
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            route_family=route_family,  # type: ignore[arg-type]
            sticky_key=sticky_key,
            sticky_kind=sticky_kind,
            reallocate_sticky=reallocate_sticky,
            sticky_max_age_seconds=sticky_max_age_seconds,
            exclude_routing_subject_ids=exclude_routing_subject_ids,
        )
        if selection.routing_subject_id is None:
            return None
        async with self._repo_factory() as repos:
            platform_identities = repos.platform_identities
            if platform_identities is None:
                return None
            identity = await platform_identities.get_by_id(selection.routing_subject_id)
            if identity is None:
                return None
            return _SelectedPlatformIdentity(
                id=identity.id,
                api_key_encrypted=identity.api_key_encrypted,
                organization_id=identity.organization_id,
                project_id=identity.project_id,
            )

    async def fetch_platform_models(
        self,
        api_key: ApiKeyData | None,
        *,
        identity: _SelectedPlatformIdentity | None = None,
        route_family: str = PUBLIC_MODELS_HTTP_ROUTE_FAMILY,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> PlatformModelsResponse | None:
        if identity is None:
            identity = await self.select_platform_identity(route_family)
        if identity is None:
            return None
        adapter = cast(OpenAIPlatformProviderAdapter, self._provider_adapter(OPENAI_PLATFORM_PROVIDER_KIND))
        subject = self._platform_provider_subject(identity)
        request_id = ensure_request_id()
        start = time.monotonic()
        try:
            result = await adapter.fetch_models(subject, route_class=route_class)
        except OpenAIPlatformError as exc:
            await self._record_platform_auth_failure(identity.id, exc)
            await self._write_request_log(
                account_id=None,
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=identity.id,
                api_key=api_key,
                request_id=request_id,
                model="",
                latency_ms=int((time.monotonic() - start) * 1000),
                status="error",
                error_code=_platform_error_code(exc.payload),
                error_message=_platform_error_message(exc.payload),
                route_class=route_class,
                rejection_reason="platform_models_request_failed",
                upstream_request_id=exc.upstream_request_id,
                transport=_REQUEST_TRANSPORT_HTTP,
            )
            raise
        await self._write_request_log(
            account_id=None,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id=identity.id,
            api_key=api_key,
            request_id=request_id,
            model="",
            latency_ms=int((time.monotonic() - start) * 1000),
            status="success",
            route_class=route_class,
            upstream_request_id=result.upstream_request_id,
            transport=_REQUEST_TRANSPORT_HTTP,
        )
        return PlatformModelsResponse(
            payload=result.payload,
            upstream_request_id=result.upstream_request_id,
        )

    async def fetch_chatgpt_codex_models(
        self,
        api_key: ApiKeyData | None,
        *,
        headers: Mapping[str, str],
        route_class: str = CHATGPT_PRIVATE_ROUTE_CLASS,
    ) -> ProviderModelsResult | None:
        settings = await get_settings_cache().get()
        request_budget_seconds = getattr(get_settings(), "proxy_request_budget_seconds", 600.0)
        prefer_earlier_reset_accounts = getattr(settings, "prefer_earlier_reset_accounts", False)
        routing_strategy_value = getattr(settings, "routing_strategy", "capacity_weighted")
        if routing_strategy_value == "round_robin":
            routing_strategy: RoutingStrategy = "round_robin"
        elif routing_strategy_value == "usage_weighted":
            routing_strategy = "usage_weighted"
        else:
            routing_strategy = "capacity_weighted"
        adapter = cast(ChatGPTWebProviderAdapter, self._provider_adapter(CHATGPT_WEB_PROVIDER_KIND))
        request_id = ensure_request_id()
        start = time.monotonic()
        deadline = start + request_budget_seconds
        excluded_account_ids: set[str] = set()

        while True:
            try:
                selection = await self._select_account_with_budget_compatible(
                    deadline,
                    request_id=request_id,
                    kind="models",
                    api_key=api_key,
                    prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                    exclude_account_ids=excluded_account_ids,
                )
            except ProxyResponseError:
                logger.warning(
                    "Live ChatGPT model discovery exceeded request budget request_id=%s",
                    request_id,
                    exc_info=True,
                )
                return None

            account = selection.account
            if account is None:
                return None

            subject = self._chatgpt_provider_subject(account)
            try:
                result = await adapter.fetch_models(subject, headers=headers, route_class=route_class)
            except ProxyResponseError as exc:
                if exc.status_code == 401:
                    try:
                        refreshed_subject = await adapter.ensure_ready(subject, force=True)
                        account = refreshed_subject.require_account()
                        result = await adapter.fetch_models(
                            refreshed_subject,
                            headers=headers,
                            route_class=route_class,
                        )
                    except RefreshError:
                        logger.warning(
                            "Live ChatGPT model discovery refresh failed account_id=%s request_id=%s",
                            account.id,
                            request_id,
                            exc_info=True,
                        )
                        excluded_account_ids.add(account.id)
                        continue
                    except ProxyResponseError as retry_exc:
                        exc = retry_exc
                    else:
                        await self._load_balancer.record_success(account)
                        await self._write_request_log(
                            account_id=account.id,
                            provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                            routing_subject_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model="",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="success",
                            route_class=route_class,
                            upstream_request_id=result.upstream_request_id,
                            transport=_REQUEST_TRANSPORT_HTTP,
                        )
                        return result

                parsed_error = _parse_openai_error(exc.payload)
                error_code = _normalize_error_code(
                    parsed_error.code if parsed_error else None,
                    parsed_error.type if parsed_error else None,
                )
                error_message = parsed_error.message if parsed_error else "ChatGPT model discovery failed"
                await self._handle_proxy_error(account, exc)
                await self._write_request_log(
                    account_id=account.id,
                    provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                    routing_subject_id=account.id,
                    api_key=api_key,
                    request_id=request_id,
                    model="",
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                    error_code=error_code,
                    error_message=error_message,
                    route_class=route_class,
                    upstream_request_id=exc.upstream_request_id,
                    rejection_reason="chatgpt_models_request_failed",
                    transport=_REQUEST_TRANSPORT_HTTP,
                )
                logger.warning(
                    "Live ChatGPT model discovery failed account_id=%s request_id=%s status=%s code=%s",
                    account.id,
                    request_id,
                    exc.status_code,
                    error_code,
                )
                excluded_account_ids.add(account.id)
                continue

            await self._load_balancer.record_success(account)
            await self._write_request_log(
                account_id=account.id,
                provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                routing_subject_id=account.id,
                api_key=api_key,
                request_id=request_id,
                model="",
                latency_ms=int((time.monotonic() - start) * 1000),
                status="success",
                route_class=route_class,
                upstream_request_id=result.upstream_request_id,
                transport=_REQUEST_TRANSPORT_HTTP,
            )
            return result

    async def stream_platform_response_events(
        self,
        *,
        payload: ResponsesRequest,
        api_key: ApiKeyData | None,
        identity: _SelectedPlatformIdentity | None = None,
        route_family: str = PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> tuple[_SelectedPlatformIdentity | None, PlatformStreamResponse | None]:
        if identity is None:
            identity = await self.select_platform_identity(route_family)
        if identity is None:
            return None, None
        adapter = cast(OpenAIPlatformProviderAdapter, self._provider_adapter(OPENAI_PLATFORM_PROVIDER_KIND))
        subject = self._platform_provider_subject(identity)
        request_id = ensure_request_id()
        start = time.monotonic()
        payload_dict = payload.model_dump(mode="json", exclude_none=True)
        forwarded_service_tier = payload.platform_forwarded_service_tier()
        if forwarded_service_tier is not None:
            payload_dict["service_tier"] = forwarded_service_tier
        try:
            result = await adapter.stream_responses(
                subject,
                payload_dict,
                route_class=route_class,
            )
        except OpenAIPlatformError as exc:
            await self._record_platform_auth_failure(identity.id, exc)
            await self.write_proxy_error_log(
                account_id=None,
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=identity.id,
                api_key=api_key,
                request_id=request_id,
                model=payload.model,
                error_code=_platform_error_code(exc.payload) or "upstream_error",
                error_message=_platform_error_message(exc.payload) or "OpenAI Platform stream request failed",
                route_class=route_class,
                rejection_reason="platform_stream_request_failed",
                upstream_request_id=exc.upstream_request_id,
                transport=_REQUEST_TRANSPORT_HTTP,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
            raise
        return identity, PlatformStreamResponse(
            event_stream=result.event_stream,
            upstream_request_id=result.upstream_request_id,
        )

    async def create_platform_response(
        self,
        *,
        payload: ResponsesRequest,
        api_key: ApiKeyData | None,
        identity: _SelectedPlatformIdentity | None = None,
        route_family: str = PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    ) -> tuple[_SelectedPlatformIdentity | None, PlatformResponseResult | None]:
        if identity is None:
            identity = await self.select_platform_identity(route_family)
        if identity is None:
            return None, None
        adapter = cast(OpenAIPlatformProviderAdapter, self._provider_adapter(OPENAI_PLATFORM_PROVIDER_KIND))
        subject = self._platform_provider_subject(identity)
        request_id = ensure_request_id()
        start = time.monotonic()
        payload_dict = payload.model_dump(mode="json", exclude_none=True)
        forwarded_service_tier = payload.platform_forwarded_service_tier()
        if forwarded_service_tier is not None:
            payload_dict["service_tier"] = forwarded_service_tier
        try:
            result = await adapter.create_response(
                subject,
                payload_dict,
                route_class=route_class,
            )
        except OpenAIPlatformError as exc:
            await self._record_platform_auth_failure(identity.id, exc)
            await self.write_proxy_error_log(
                account_id=None,
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=identity.id,
                api_key=api_key,
                request_id=request_id,
                model=payload.model,
                error_code=_platform_error_code(exc.payload) or "upstream_error",
                error_message=_platform_error_message(exc.payload) or "OpenAI Platform response request failed",
                route_class=route_class,
                rejection_reason="platform_response_request_failed",
                upstream_request_id=exc.upstream_request_id,
                transport=_REQUEST_TRANSPORT_HTTP,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
            raise
        return identity, PlatformResponseResult(
            payload=result.payload,
            upstream_request_id=result.upstream_request_id,
        )

    async def write_provider_rejection_log(
        self,
        *,
        api_key: ApiKeyData | None,
        request_id: str,
        model: str | None,
        error_code: str,
        error_message: str,
        route_class: str,
        rejection_reason: str,
        transport: str = _REQUEST_TRANSPORT_HTTP,
    ) -> None:
        await self.write_proxy_error_log(
            account_id=None,
            provider_kind=None,
            routing_subject_id=None,
            api_key=api_key,
            request_id=request_id,
            model=model,
            error_code=error_code,
            error_message=error_message,
            route_class=route_class,
            rejection_reason=rejection_reason,
            transport=transport,
        )

    async def write_proxy_error_log(
        self,
        *,
        account_id: str | None,
        provider_kind: str | None,
        routing_subject_id: str | None,
        api_key: ApiKeyData | None,
        request_id: str,
        model: str | None,
        error_code: str,
        error_message: str,
        route_class: str | None,
        rejection_reason: str | None,
        upstream_request_id: str | None = None,
        transport: str = _REQUEST_TRANSPORT_HTTP,
        latency_ms: int = 0,
    ) -> None:
        await self._write_request_log(
            account_id=account_id,
            provider_kind=provider_kind,
            routing_subject_id=routing_subject_id,
            api_key=api_key,
            request_id=request_id,
            model=model,
            latency_ms=latency_ms,
            status="error",
            error_code=error_code,
            error_message=error_message,
            route_class=route_class,
            rejection_reason=rejection_reason,
            upstream_request_id=upstream_request_id,
            transport=transport,
        )

    async def _record_platform_auth_failure(self, identity_id: str, exc: OpenAIPlatformError) -> None:
        if exc.status_code not in (401, 403):
            return
        reason = _platform_error_message(exc.payload) or _platform_error_code(exc.payload) or f"http_{exc.status_code}"
        async with self._repo_factory() as repos:
            platform_identities = repos.platform_identities
            if platform_identities is None:
                return
            updated = await platform_identities.update_validation_state(
                identity_id,
                last_validated_at=None,
                last_auth_failure_reason=reason,
                status=AccountStatus.DEACTIVATED,
            )
        if updated:
            logger.warning(
                "provider_health_transition request_id=%s provider_kind=%s routing_subject_id=%s status=%s reason=%s",
                get_request_id(),
                OPENAI_PLATFORM_PROVIDER_KIND,
                identity_id,
                AccountStatus.DEACTIVATED.value,
                reason,
            )

    def stream_responses(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool = False,
        propagate_http_errors: bool = False,
        openai_cache_affinity: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
        suppress_text_done_events: bool = False,
        request_transport: str = _REQUEST_TRANSPORT_HTTP,
        codex_session_budget_reallocation_enabled: bool = True,
    ) -> AsyncIterator[str]:
        _maybe_log_proxy_request_payload("stream", payload, headers)
        filtered = filter_inbound_headers(headers)
        return self._stream_with_retry(
            payload,
            filtered,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=propagate_http_errors,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
            request_transport=request_transport,
            codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        )

    def stream_http_responses(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool = False,
        propagate_http_errors: bool = False,
        openai_cache_affinity: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
        suppress_text_done_events: bool = False,
        downstream_turn_state: str | None = None,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        codex_session_budget_reallocation_enabled: bool = True,
    ) -> AsyncIterator[str]:
        _maybe_log_proxy_request_payload("stream_http", payload, headers)
        proxy_api_authorization = _header_value_case_insensitive(headers, "authorization")
        filtered = filter_inbound_headers(headers)
        return self._stream_http_bridge_or_retry(
            payload,
            filtered,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=propagate_http_errors,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
            downstream_turn_state=downstream_turn_state,
            forwarded_request=forwarded_request,
            proxy_api_authorization=proxy_api_authorization,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        )

    async def _stream_http_bridge_or_retry(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        propagate_http_errors: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        suppress_text_done_events: bool,
        downstream_turn_state: str | None = None,
        forwarded_request: bool = False,
        proxy_api_authorization: str | None = None,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        codex_session_budget_reallocation_enabled: bool = True,
    ) -> AsyncIterator[str]:
        dashboard_settings = await get_settings_cache().get()
        runtime_config = _http_bridge_runtime_config(dashboard_settings, get_settings())
        if not runtime_config.enabled:
            async for line in self._stream_with_retry(
                payload,
                headers,
                codex_session_affinity=codex_session_affinity,
                propagate_http_errors=propagate_http_errors,
                openai_cache_affinity=openai_cache_affinity,
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                suppress_text_done_events=suppress_text_done_events,
                request_transport=_REQUEST_TRANSPORT_HTTP,
                codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
            ):
                yield line
            return

        async for line in self._stream_via_http_bridge(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=propagate_http_errors,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
            idle_ttl_seconds=runtime_config.idle_ttl_seconds,
            codex_idle_ttl_seconds=runtime_config.codex_idle_ttl_seconds,
            max_sessions=runtime_config.max_sessions,
            queue_limit=runtime_config.queue_limit,
            prompt_cache_idle_ttl_seconds=runtime_config.prompt_cache_idle_ttl_seconds,
            downstream_turn_state=downstream_turn_state,
            forwarded_request=forwarded_request,
            proxy_api_authorization=proxy_api_authorization,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        ):
            yield line

    async def _stream_via_http_bridge(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        propagate_http_errors: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        suppress_text_done_events: bool,
        idle_ttl_seconds: float,
        codex_idle_ttl_seconds: float,
        max_sessions: int,
        queue_limit: int,
        prompt_cache_idle_ttl_seconds: float | None = None,
        downstream_turn_state: str | None = None,
        forwarded_request: bool = False,
        proxy_api_authorization: str | None = None,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        codex_session_budget_reallocation_enabled: bool = True,
    ) -> AsyncIterator[str]:
        del suppress_text_done_events
        request_id = ensure_request_id()
        dashboard_settings = await get_settings_cache().get()
        runtime_config = _http_bridge_runtime_config(dashboard_settings, get_settings())
        incoming_turn_state_header = _sticky_key_from_turn_state_header(headers) if not forwarded_request else None
        incoming_session_header = _sticky_key_from_session_header(headers) if not forwarded_request else None
        had_prompt_cache_key = _prompt_cache_key_from_request_model(payload) is not None
        affinity = _sticky_key_for_responses_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=dashboard_settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=dashboard_settings.sticky_threads_enabled,
            api_key=api_key,
            codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        )
        sticky_key_source = "none"
        if affinity.kind == StickySessionKind.CODEX_SESSION:
            sticky_key_source = (
                "turn_state_header" if _sticky_key_from_turn_state_header(headers) is not None else "session_header"
            )
        elif affinity.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "stream_http_bridge",
            payload,
            headers,
            sticky_kind=affinity.kind.value if affinity.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(payload) is not None,
        )

        bridge_session_key = _make_http_bridge_session_key(
            payload,
            headers=headers,
            affinity=affinity,
            api_key=api_key,
            request_id=request_id,
            allow_forwarded_affinity_headers=forwarded_request,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
        )
        try:
            durable_lookup = await self._durable_bridge.lookup_request_targets(
                session_key_kind=bridge_session_key.affinity_kind,
                session_key_value=bridge_session_key.affinity_key,
                api_key_id=bridge_session_key.api_key_id,
                turn_state=incoming_turn_state_header,
                session_header=incoming_session_header,
                previous_response_id=payload.previous_response_id,
            )
        except Exception:
            if payload.previous_response_id is not None or bridge_session_key.strength == "hard":
                _record_continuity_fail_closed(
                    surface="http_bridge",
                    reason="durable_lookup_failed",
                    previous_response_id=payload.previous_response_id,
                    session_id=bridge_session_key.affinity_key,
                )
                raise ProxyResponseError(502, _http_bridge_owner_lookup_unavailable_error_envelope())
            logger.warning("Durable bridge lookup failed; falling back to non-durable request handling", exc_info=True)
            durable_lookup = None
        effective_payload = payload
        proxy_injected_previous_response_id = False
        fresh_upstream_request_text: str | None = None
        if durable_lookup is not None:
            bridge_session_key = _HTTPBridgeSessionKey(
                durable_lookup.canonical_kind,
                durable_lookup.canonical_key,
                bridge_session_key.api_key_id,
            )
            live_local_session_exists = await self._http_bridge_has_live_local_session(
                key=bridge_session_key,
                incoming_turn_state=incoming_turn_state_header,
                api_key=api_key,
            )
            forwards_to_active_owner = await self._http_bridge_can_forward_to_active_owner(durable_lookup)
            if (
                not live_local_session_exists
                and not forwards_to_active_owner
                and payload.previous_response_id is None
                and bridge_session_key.strength == "hard"
                and durable_lookup.latest_response_id is not None
                and not _http_bridge_payload_looks_like_full_resend(payload)
            ):
                effective_payload = payload.model_copy(
                    update={"previous_response_id": durable_lookup.latest_response_id}
                )
                proxy_injected_previous_response_id = True
                _fresh_request_state, fresh_upstream_request_text = self._prepare_http_bridge_request(
                    payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    request_id=request_id,
                )
                del _fresh_request_state
                _log_http_bridge_event(
                    "fresh_reattach_anchor_injected",
                    bridge_session_key,
                    account_id=None,
                    model=payload.model,
                    detail=f"response_id={durable_lookup.latest_response_id}",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(payload.model) if payload.model else None,
                )
        request_state, text_data = self._prepare_http_bridge_request(
            effective_payload,
            headers,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            request_id=request_id,
        )
        if downstream_turn_state is not None:
            request_state.session_id = _normalize_session_id(downstream_turn_state)
        request_state.transport = _REQUEST_TRANSPORT_HTTP
        request_state.request_stage = _http_bridge_request_stage(
            headers=headers,
            payload=effective_payload,
            durable_lookup=durable_lookup,
        )
        request_state.preferred_account_id = (
            durable_lookup.account_id
            if (
                durable_lookup is not None
                and (
                    request_state.previous_response_id is not None
                    or bridge_session_key.strength == "hard"
                    or (
                        bridge_session_key.affinity_kind == "prompt_cache"
                        and request_state.request_stage == "follow_up"
                        and durable_lookup.latest_turn_state is not None
                    )
                )
            )
            else request_state.preferred_account_id
        )
        if request_state.previous_response_id is not None and request_state.preferred_account_id is None:
            request_state.preferred_account_id = await self._http_bridge_local_owner_account_id(
                key=bridge_session_key,
                incoming_turn_state=incoming_turn_state_header,
                previous_response_id=request_state.previous_response_id,
                api_key=api_key,
            )
        if request_state.previous_response_id is not None and request_state.preferred_account_id is None:
            request_state.preferred_account_id = await self._resolve_websocket_previous_response_owner(
                previous_response_id=request_state.previous_response_id,
                api_key=api_key,
                session_id=request_state.session_id,
                surface="http_bridge",
            )
        if proxy_injected_previous_response_id:
            request_state.proxy_injected_previous_response_id = True
            request_state.fresh_upstream_request_text = fresh_upstream_request_text or text_data
            # Durable-anchor injection actually runs when the incoming
            # payload is *not* a full resend (see the
            # ``not _http_bridge_payload_looks_like_full_resend(payload)``
            # guard above), so the captured unanchored text is typically
            # just a short follow-up. Replaying it as a fresh turn would
            # drop the conversational context the anchor was pointing at.
            # Only the trim branch below (which verifies the stored prefix
            # fingerprint) is allowed to flip this flag to ``True``.
            request_state.fresh_upstream_request_is_retry_safe = False
        session_or_forward = await self._get_or_create_http_bridge_session(
            bridge_session_key,
            headers=dict(headers),
            affinity=affinity,
            api_key=api_key,
            request_model=effective_payload.model,
            idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                affinity=affinity,
                idle_ttl_seconds=idle_ttl_seconds,
                codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
            ),
            max_sessions=max_sessions,
            previous_response_id=request_state.previous_response_id,
            gateway_safe_mode=runtime_config.gateway_safe_mode,
            allow_forward_to_owner=True,
            forwarded_request=forwarded_request,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            durable_lookup=durable_lookup,
            request_stage=request_state.request_stage,
            preferred_account_id=request_state.preferred_account_id,
        )
        if isinstance(session_or_forward, _HTTPBridgeOwnerForward):
            forwarded_any = False
            try:
                async for line in self._forward_http_bridge_request_to_owner(
                    owner_forward=session_or_forward,
                    payload=effective_payload,
                    headers=headers,
                    api_key_reservation=api_key_reservation,
                    codex_session_affinity=codex_session_affinity,
                    downstream_turn_state=downstream_turn_state,
                    request_started_at=request_state.started_at,
                    proxy_api_authorization=proxy_api_authorization,
                ):
                    forwarded_any = True
                    yield line
                return
            except ProxyResponseError as exc:
                if forwarded_any:
                    raise
                should_attempt_previous_response_recovery = (
                    effective_payload.previous_response_id is not None
                    and _http_bridge_should_attempt_local_previous_response_recovery(exc)
                )
                should_attempt_bootstrap_rebind = _http_bridge_should_attempt_local_bootstrap_rebind(
                    exc,
                    key=bridge_session_key,
                    headers=headers,
                    previous_response_id=effective_payload.previous_response_id,
                )
                if not should_attempt_previous_response_recovery and not should_attempt_bootstrap_rebind:
                    raise
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(
                        path="owner_forward_fail"
                        if should_attempt_previous_response_recovery
                        else "owner_forward_bootstrap"
                    ).inc()
                _log_http_bridge_event(
                    "previous_response_recover_local"
                    if should_attempt_previous_response_recovery
                    else "bootstrap_rebind_local",
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail=(
                        "outcome=local_rebind_after_forward_failure"
                        if should_attempt_previous_response_recovery
                        else "outcome=local_bootstrap_after_forward_failure"
                    ),
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                session = await self._get_or_create_http_bridge_session(
                    bridge_session_key,
                    headers=dict(headers),
                    affinity=affinity,
                    api_key=api_key,
                    request_model=effective_payload.model,
                    idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                        affinity=affinity,
                        idle_ttl_seconds=idle_ttl_seconds,
                        codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                        prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
                    ),
                    max_sessions=max_sessions,
                    previous_response_id=request_state.previous_response_id,
                    gateway_safe_mode=runtime_config.gateway_safe_mode,
                    allow_forward_to_owner=False,
                    forwarded_request=False,
                    allow_previous_response_recovery_rebind=should_attempt_previous_response_recovery,
                    allow_bootstrap_owner_rebind=should_attempt_bootstrap_rebind,
                    durable_lookup=durable_lookup,
                    request_stage="reattach",
                    preferred_account_id=request_state.preferred_account_id,
                )
                _record_bridge_reattach(
                    path="owner_forward_fail"
                    if should_attempt_previous_response_recovery
                    else "owner_forward_bootstrap",
                    outcome="success",
                )
                retry_request_state: _WebSocketRequestState | None = None
                try:
                    retry_api_key_reservation = api_key_reservation
                    retry_reservation_reacquired = False
                    if api_key is not None and api_key_reservation is not None:
                        retry_api_key_reservation = await self._reserve_websocket_api_key_usage(
                            api_key,
                            request_model=effective_payload.model,
                            request_service_tier=_normalize_service_tier_value(
                                dict(effective_payload.to_payload()).get("service_tier"),
                            ),
                        )
                        retry_reservation_reacquired = True

                    retry_request_state, retry_text_data = self._prepare_http_bridge_request(
                        effective_payload,
                        headers,
                        api_key=api_key,
                        api_key_reservation=retry_api_key_reservation,
                        request_id=request_id,
                    )
                    if downstream_turn_state is not None:
                        retry_request_state.session_id = _normalize_session_id(downstream_turn_state)
                    retry_request_state.transport = _REQUEST_TRANSPORT_HTTP
                    retry_request_state.request_stage = "reattach"
                    retry_request_state.preferred_account_id = request_state.preferred_account_id

                    await self._submit_http_bridge_request(
                        session,
                        request_state=retry_request_state,
                        text_data=retry_text_data,
                        queue_limit=queue_limit,
                    )
                    if downstream_turn_state is not None:
                        await self._register_http_bridge_turn_state(session, downstream_turn_state)
                    event_queue = retry_request_state.event_queue
                    assert event_queue is not None
                    while True:
                        event_block = await event_queue.get()
                        if event_block is None:
                            break
                        if retry_request_state.latency_first_token_ms is None:
                            block_payload = parse_sse_data_json(event_block)
                            block_event_type = _event_type_from_payload(None, block_payload)
                            if block_event_type in _TEXT_DELTA_EVENT_TYPES:
                                retry_request_state.latency_first_token_ms = int(
                                    (time.monotonic() - retry_request_state.started_at) * 1000
                                )
                        yield event_block
                except BaseException:
                    if retry_reservation_reacquired and retry_api_key_reservation is not None:
                        await self._release_websocket_reservation(retry_api_key_reservation)
                    raise
                finally:
                    if retry_request_state is not None:
                        with anyio.CancelScope(shield=True):
                            await self._detach_http_bridge_request(session, request_state=retry_request_state)
                            session.last_used_at = time.monotonic()
                return
        session = session_or_forward
        # --- Session-level previous_response_id injection ---
        # If the client didn't send previous_response_id and the durable
        # lookup didn't inject one, but this bridge session is carrying
        # Codex-style conversational continuity and has already completed a
        # request on this logical conversation, inject the session's last
        # completed response ID so the trim branch below can strip the
        # already-stored prefix.
        #
        # Correctness guards:
        # - Soft affinity reuse (for example prompt cache / sticky-thread
        #   sharing) must stay self-contained, so only true Codex
        #   continuity sessions opt in.
        # - Injecting an anchor when the incoming payload is a full-resend
        #   whose prefix cannot be safely trimmed (non-list input, prefix
        #   mismatch, or shorter-than-stored history) would send both the
        #   full history *and* the anchor upstream, which duplicates
        #   context and distorts output/cost. Gate injection so it only
        #   fires when the trim branch below would actually succeed.
        incoming_input_preview = effective_payload.input
        stored_count_preview = session.last_completed_input_count
        stored_fingerprint_preview = session.last_completed_input_prefix_fingerprint
        session_anchor_trimmable = (
            stored_count_preview > 0
            and stored_fingerprint_preview is not None
            and isinstance(incoming_input_preview, list)
            and len(incoming_input_preview) > stored_count_preview
            and _fingerprint_input_items(cast(list[JsonValue], incoming_input_preview)[:stored_count_preview])
            == stored_fingerprint_preview
        )
        if (
            session.codex_session
            and not proxy_injected_previous_response_id
            and effective_payload.previous_response_id is None
            and session.last_completed_response_id is not None
            and session_anchor_trimmable
        ):
            fresh_upstream_request_text = text_data
            effective_payload = effective_payload.model_copy(
                update={"previous_response_id": session.last_completed_response_id}
            )
            proxy_injected_previous_response_id = True
            request_state, text_data = self._prepare_http_bridge_request(
                effective_payload,
                headers,
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                request_id=request_id,
            )
            request_state.transport = _REQUEST_TRANSPORT_HTTP
            request_state.request_stage = _http_bridge_request_stage(
                headers=headers,
                payload=effective_payload,
                durable_lookup=durable_lookup,
            )
            request_state.preferred_account_id = durable_lookup.account_id if durable_lookup is not None else None
            request_state.proxy_injected_previous_response_id = True
            request_state.fresh_upstream_request_text = fresh_upstream_request_text
            # Session-level anchor injection may be attached to a payload
            # that relied on the anchor for context (for example a
            # single-item follow-up turn whose prior history is only
            # represented by ``previous_response_id``). Replaying without
            # the anchor would silently turn it into a fresh turn and drop
            # conversational context, so opt this path out of fresh-upstream
            # fresh-turn replay.
            request_state.fresh_upstream_request_is_retry_safe = False
            logger.info(
                "session_anchor_injected request_id=%s response_id=%s",
                request_id,
                session.last_completed_response_id,
            )
        # Trim already-stored prefix when previous_response_id anchors context.
        has_previous_response_id = (
            proxy_injected_previous_response_id or effective_payload.previous_response_id is not None
        )
        incoming_input = effective_payload.input
        stored_count = session.last_completed_input_count
        stored_fingerprint = session.last_completed_input_prefix_fingerprint
        if (
            has_previous_response_id
            and stored_count > 0
            and stored_fingerprint is not None
            and isinstance(incoming_input, list)
            and len(incoming_input) > stored_count
        ):
            incoming_input_list = cast(list[JsonValue], incoming_input)
            incoming_prefix_fingerprint = _fingerprint_input_items(incoming_input_list[:stored_count])
            if incoming_prefix_fingerprint == stored_fingerprint:
                original_count = len(incoming_input_list)
                trimmed_input = incoming_input_list[stored_count:]
                trimmed_payload = effective_payload.model_copy(update={"input": trimmed_input})
                previous_preferred_account_id = request_state.preferred_account_id
                request_state, text_data = self._prepare_http_bridge_request(
                    trimmed_payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    request_id=request_id,
                )
                if downstream_turn_state is not None:
                    request_state.session_id = _normalize_session_id(downstream_turn_state)
                request_state.transport = _REQUEST_TRANSPORT_HTTP
                request_state.request_stage = _http_bridge_request_stage(
                    headers=headers,
                    payload=trimmed_payload,
                    durable_lookup=durable_lookup,
                )
                request_state.preferred_account_id = previous_preferred_account_id
                request_state.input_item_count = original_count
                request_state.input_full_fingerprint = _fingerprint_input_items(incoming_input_list)
                if proxy_injected_previous_response_id:
                    request_state.proxy_injected_previous_response_id = True
                    request_state.fresh_upstream_request_text = fresh_upstream_request_text
                    # The trim branch only fires when the untrimmed payload
                    # is a true full resend whose prefix exactly matches the
                    # already-stored context, so the unanchored request text
                    # is a safe fresh-turn replay target regardless of
                    # whether the anchor came from the durable or
                    # session-level injection path.
                    request_state.fresh_upstream_request_is_retry_safe = True
                logger.info(
                    "store_context_input_trimmed request_id=%s original_items=%s trimmed_to=%s previous_response_id=%s",
                    request_id,
                    original_count,
                    len(trimmed_input),
                    effective_payload.previous_response_id,
                )
            else:
                logger.warning(
                    "store_context_input_trim_skipped_prefix_mismatch request_id=%s incoming_items=%s "
                    "stored_items=%s previous_response_id=%s",
                    request_id,
                    len(incoming_input_list),
                    stored_count,
                    effective_payload.previous_response_id,
                )
        session_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
            session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=queue_limit,
            propagate_http_errors=propagate_http_errors,
            downstream_turn_state=downstream_turn_state,
        )
        try:
            async for event_block in session_events:
                yield event_block
        except ProxyResponseError as exc:
            is_context_overflow = _http_bridge_is_context_overflow_error(exc)
            should_rollover_after_context_overflow = _http_bridge_should_rollover_after_context_overflow(
                exc,
                key=bridge_session_key,
            )
            should_attempt_previous_response_recovery = (
                effective_payload.previous_response_id is not None
                and _http_bridge_should_attempt_local_previous_response_recovery(exc)
            )
            should_attempt_context_overflow_fresh_turn_recovery = (
                is_context_overflow
                and effective_payload.previous_response_id is not None
                and bridge_session_key.strength != "hard"
            )
            if (
                not should_attempt_previous_response_recovery
                and not should_rollover_after_context_overflow
                and not should_attempt_context_overflow_fresh_turn_recovery
            ):
                if is_context_overflow:
                    _log_http_bridge_event(
                        "context_overflow_no_rollover",
                        bridge_session_key,
                        account_id=None,
                        model=effective_payload.model,
                        detail="outcome=preserve_hard_affinity_session",
                        cache_key_family=bridge_session_key.affinity_kind,
                        model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                        owner_check_applied=True,
                    )
                raise

            if should_attempt_context_overflow_fresh_turn_recovery:
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="context_overflow_fresh_turn").inc()
                _log_http_bridge_event(
                    "context_overflow_fresh_turn_recover",
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail="outcome=retry_without_previous_response_id",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                await self._reset_http_bridge_session_after_local_terminal_error(
                    session,
                    error_code="stream_incomplete",
                    error_message="Upstream websocket closed before response.completed",
                )
                recovery_path = "context_overflow_fresh_turn"
                retry_payload = _http_bridge_payload_without_previous_response_id(effective_payload)
                retry_previous_response_id = None
                retry_request_stage = "context_overflow_recover"
                retry_preferred_account_id = None
                allow_previous_response_recovery_rebind = False
            elif should_rollover_after_context_overflow:
                _log_http_bridge_event(
                    "context_overflow_rollover",
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail="outcome=close_session_after_context_length_exceeded",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                await self._reset_http_bridge_session_after_local_terminal_error(
                    session,
                    error_code="stream_incomplete",
                    error_message="Upstream websocket closed before response.completed",
                )
                raise
            else:
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="local_previous_response_error").inc()
                _log_http_bridge_event(
                    "previous_response_recover_local",
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail="outcome=local_rebind_after_local_error",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                await self._reset_http_bridge_session_after_local_terminal_error(
                    session,
                    error_code="stream_incomplete",
                    error_message="Upstream websocket closed before response.completed",
                )
                recovery_path = "local_previous_response_error"
                retry_payload = effective_payload
                retry_previous_response_id = request_state.previous_response_id
                retry_request_stage = "reattach"
                retry_preferred_account_id = request_state.preferred_account_id
                allow_previous_response_recovery_rebind = True

            session = await self._get_or_create_http_bridge_session(
                bridge_session_key,
                headers=dict(headers),
                affinity=affinity,
                api_key=api_key,
                request_model=retry_payload.model,
                idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                    affinity=affinity,
                    idle_ttl_seconds=idle_ttl_seconds,
                    codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                    prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
                ),
                max_sessions=max_sessions,
                previous_response_id=retry_previous_response_id,
                gateway_safe_mode=runtime_config.gateway_safe_mode,
                allow_forward_to_owner=False,
                forwarded_request=False,
                allow_previous_response_recovery_rebind=allow_previous_response_recovery_rebind,
                durable_lookup=durable_lookup,
                request_stage=retry_request_stage,
                preferred_account_id=retry_preferred_account_id,
            )
            _record_bridge_reattach(path=recovery_path, outcome="success")

            try:
                retry_api_key_reservation = api_key_reservation
                retry_reservation_reacquired = False
                if api_key is not None and api_key_reservation is not None:
                    retry_api_key_reservation = await self._reserve_websocket_api_key_usage(
                        api_key,
                        request_model=retry_payload.model,
                        request_service_tier=_normalize_service_tier_value(
                            dict(retry_payload.to_payload()).get("service_tier"),
                        ),
                    )
                    retry_reservation_reacquired = True

                retry_request_state, retry_text_data = self._prepare_http_bridge_request(
                    retry_payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=retry_api_key_reservation,
                    request_id=request_id,
                )
                if downstream_turn_state is not None:
                    retry_request_state.session_id = _normalize_session_id(downstream_turn_state)
                retry_request_state.transport = _REQUEST_TRANSPORT_HTTP
                retry_request_state.request_stage = retry_request_stage
                retry_request_state.preferred_account_id = retry_preferred_account_id

                retry_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
                    session,
                    request_state=retry_request_state,
                    text_data=retry_text_data,
                    queue_limit=queue_limit,
                    propagate_http_errors=propagate_http_errors,
                    downstream_turn_state=downstream_turn_state,
                )
                try:
                    async for event_block in retry_events:
                        yield event_block
                finally:
                    try:
                        await retry_events.aclose()
                    except Exception:
                        pass
            except BaseException:
                if retry_reservation_reacquired and retry_api_key_reservation is not None:
                    await self._release_websocket_reservation(retry_api_key_reservation)
                raise
        finally:
            try:
                await session_events.aclose()
            except Exception:
                pass

    async def _reset_http_bridge_session_after_local_terminal_error(
        self,
        session: "_HTTPBridgeSession",
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        async with self._http_bridge_lock:
            if self._http_bridge_sessions.get(session.key) is session:
                self._http_bridge_sessions.pop(session.key, None)
        async with session.pending_lock:
            session.queued_request_count = 0
        await self._fail_pending_websocket_requests(
            account_id_value=session.account.id,
            pending_requests=session.pending_requests,
            pending_lock=session.pending_lock,
            error_code=error_code,
            error_message=error_message,
            api_key=None,
            response_create_gate=session.response_create_gate,
        )
        await self._close_http_bridge_session(session)

    async def _stream_http_bridge_session_events(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ) -> AsyncGenerator[str, None]:
        await self._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=queue_limit,
        )
        if downstream_turn_state is not None:
            await self._register_http_bridge_turn_state(session, downstream_turn_state)

        try:
            event_queue = request_state.event_queue
            assert event_queue is not None
            yielded_any = False
            while True:
                event_block = await event_queue.get()
                if event_block is None:
                    break
                block_payload = parse_sse_data_json(event_block)
                block_event_type = _event_type_from_payload(None, block_payload)
                if request_state.latency_first_token_ms is None and block_event_type in _TEXT_DELTA_EVENT_TYPES:
                    request_state.latency_first_token_ms = int((time.monotonic() - request_state.started_at) * 1000)
                if (
                    not yielded_any
                    and propagate_http_errors
                    and block_event_type == "response.failed"
                    and request_state.error_http_status_override is not None
                    and request_state.error_http_status_override >= 400
                ):
                    raise ProxyResponseError(
                        request_state.error_http_status_override,
                        _openai_error_envelope_from_response_failed_payload(block_payload),
                    )
                yield event_block
                yielded_any = True
        finally:
            with anyio.CancelScope(shield=True):
                await self._detach_http_bridge_request(session, request_state=request_state)
                session.last_used_at = time.monotonic()

    async def _http_bridge_has_live_local_session(
        self,
        *,
        key: "_HTTPBridgeSessionKey",
        incoming_turn_state: str | None,
        api_key: ApiKeyData | None,
    ) -> bool:
        api_key_id = api_key.id if api_key is not None else None
        async with self._http_bridge_lock:
            candidate_keys = [key]
            if incoming_turn_state is not None:
                alias_key = self._http_bridge_turn_state_index.get(
                    _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                )
                if alias_key is not None and alias_key not in candidate_keys:
                    candidate_keys.append(alias_key)
            for candidate_key in candidate_keys:
                session = self._http_bridge_sessions.get(candidate_key)
                if session is None or session.closed or session.account.status != AccountStatus.ACTIVE:
                    continue
                if not _http_bridge_session_allows_api_key(session, api_key):
                    continue
                return True
        return False

    async def _http_bridge_local_owner_account_id(
        self,
        *,
        key: "_HTTPBridgeSessionKey",
        incoming_turn_state: str | None,
        previous_response_id: str,
        api_key: ApiKeyData | None,
    ) -> str | None:
        api_key_id = api_key.id if api_key is not None else None
        candidate_keys: list[_HTTPBridgeSessionKey] = [key]
        async with self._http_bridge_lock:
            if incoming_turn_state is not None:
                alias_key = self._http_bridge_turn_state_index.get(
                    _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                )
                if alias_key is not None and alias_key not in candidate_keys:
                    candidate_keys.append(alias_key)
            previous_alias_key = _http_bridge_previous_response_alias_key(previous_response_id, api_key_id)
            previous_key = self._http_bridge_previous_response_index.get(previous_alias_key)
            if previous_key is not None and previous_key not in candidate_keys:
                candidate_keys.append(previous_key)
            for candidate_key in candidate_keys:
                session = self._http_bridge_sessions.get(candidate_key)
                if session is None or session.closed or session.account.status != AccountStatus.ACTIVE:
                    continue
                if not _http_bridge_session_allows_api_key(session, api_key):
                    continue
                if not _http_bridge_session_reusable_for_request(
                    session=session,
                    key=candidate_key,
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=previous_response_id,
                ):
                    continue
                _record_continuity_owner_resolution(
                    surface="http_bridge",
                    source="local_bridge_session",
                    outcome="hit",
                    previous_response_id=previous_response_id,
                    session_id=incoming_turn_state,
                )
                return session.account.id
        _record_continuity_owner_resolution(
            surface="http_bridge",
            source="local_bridge_session",
            outcome="miss",
            previous_response_id=previous_response_id,
            session_id=incoming_turn_state,
        )
        return None

    async def _http_bridge_can_forward_to_active_owner(
        self,
        durable_lookup: DurableBridgeLookup,
    ) -> bool:
        owner_instance = _durable_bridge_lookup_active_owner(durable_lookup)
        if owner_instance is None:
            return False
        if owner_instance == get_settings().http_responses_session_bridge_instance_id:
            return False
        if self._ring_membership is None:
            return False
        try:
            owner_endpoint = await self._ring_membership.resolve_endpoint(owner_instance)
        except Exception:
            logger.debug("Failed to resolve HTTP bridge owner endpoint during anchor injection decision", exc_info=True)
            return False
        return owner_endpoint is not None

    async def _forward_http_bridge_request_to_owner(
        self,
        *,
        owner_forward: _HTTPBridgeOwnerForward,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        api_key_reservation: ApiKeyUsageReservationData | None,
        codex_session_affinity: bool,
        downstream_turn_state: str | None,
        request_started_at: float,
        proxy_api_authorization: str | None,
    ) -> AsyncIterator[str]:
        current_instance, _ = _normalized_http_bridge_instance_ring(get_settings())
        forwarded_turn_state = _header_value_case_insensitive(headers, "x-codex-turn-state") or downstream_turn_state
        forward_context = HTTPBridgeForwardContext(
            origin_instance=current_instance,
            target_instance=owner_forward.owner_instance,
            reservation=api_key_reservation,
            codex_session_affinity=codex_session_affinity,
            downstream_turn_state=forwarded_turn_state,
            original_affinity_kind=owner_forward.key.affinity_kind,
            original_affinity_key=owner_forward.key.affinity_key,
        )
        forward_headers = _headers_with_authorization(headers, proxy_api_authorization)
        start = time.monotonic()
        _log_http_bridge_event(
            "owner_forward_start",
            owner_forward.key,
            account_id=None,
            model=payload.model,
            detail=(
                f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, "
                f"owner_endpoint={owner_forward.owner_endpoint}"
            ),
            cache_key_family=owner_forward.key.affinity_kind,
            model_class=_extract_model_class(payload.model) if payload.model else None,
            owner_check_applied=True,
        )

        forwarded_any = False
        try:
            async for event_block in self._http_bridge_owner_client.stream_responses(
                owner_endpoint=owner_forward.owner_endpoint,
                payload=payload,
                headers=forward_headers,
                context=forward_context,
                request_started_at=request_started_at,
            ):
                forwarded_any = True
                yield event_block
        except OwnerForwardRelayFailure as exc:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=(
                    f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, "
                    "error=relay_failure"
                ),
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            if forwarded_any:
                yield exc.event_block
                return
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_owner_unreachable",
                    "HTTP bridge owner relay timed out",
                    error_type="server_error",
                ),
            ) from exc
        except ProxyResponseError:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}",
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=(
                    f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, error={exc}"
                ),
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_owner_unreachable",
                    "HTTP bridge owner request failed",
                    error_type="server_error",
                ),
            ) from exc
        else:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="success").inc()
            _log_http_bridge_event(
                "owner_forward_success",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}",
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
        finally:
            if PROMETHEUS_AVAILABLE and bridge_forward_latency_seconds is not None:
                bridge_forward_latency_seconds.observe(max(time.monotonic() - start, 0.0))

    async def compact_responses(
        self,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool = False,
        openai_cache_affinity: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
        selected_subject: SelectedChatGPTSubject | SelectedPlatformSubject | None = None,
        route_family: str = BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        route_class: str = CHATGPT_PRIVATE_ROUTE_CLASS,
        codex_session_budget_reallocation_enabled: bool = True,
    ) -> CompactResponsePayload:
        _maybe_log_proxy_request_payload("compact", payload, headers)
        filtered = filter_inbound_headers(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = time.monotonic()
        base_settings = get_settings()
        deadline = start + base_settings.compact_request_budget_seconds
        account_id_value: str | None = None
        provider_kind_value: str | None = None
        routing_subject_id_value: str | None = None
        upstream_request_id: str | None = None
        log_rejection_reason: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        response: CompactResponsePayload | None = None
        requested_service_tier = _normalize_service_tier_value(payload.service_tier)
        effective_request_service_tier = requested_service_tier
        actual_service_tier: str | None = None
        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        had_prompt_cache_key = _prompt_cache_key_from_request_model(payload) is not None
        affinity = _sticky_key_for_compact_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=settings.sticky_threads_enabled,
            api_key=api_key,
            codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        )
        sticky_key_source = "none"
        if affinity.kind == StickySessionKind.CODEX_SESSION:
            sticky_key_source = "session_header"
        elif affinity.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "compact",
            payload,
            headers,
            sticky_kind=affinity.kind.value if affinity.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(payload) is not None,
        )
        routing_strategy = _routing_strategy(settings)
        try:
            selected_subject = selected_subject or SelectedChatGPTSubject(
                provider_kind=CHATGPT_WEB_PROVIDER_KIND,
                route_class=route_class,
                routing_subject_id="chatgpt_web_pool",
            )
            if isinstance(selected_subject, SelectedChatGPTSubject):
                provider_kind_value = CHATGPT_WEB_PROVIDER_KIND

            async def _call_compact_timeout(
                callback: Callable[[], Awaitable[ProviderCompactResponseResult]],
            ) -> ProviderCompactResponseResult:
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning("Compact request budget exhausted before upstream call request_id=%s", request_id)
                    _raise_proxy_budget_exhausted()
                if base_settings.upstream_compact_timeout_seconds is None:
                    timeout_tokens = push_compact_timeout_overrides(connect_timeout_seconds=remaining_budget)
                else:
                    timeout_tokens = push_compact_timeout_overrides(
                        connect_timeout_seconds=remaining_budget,
                        total_timeout_seconds=remaining_budget,
                    )
                create_lease = await self._get_work_admission().acquire_response_create(compact=True)
                try:
                    return await callback()
                finally:
                    create_lease.release()
                    pop_compact_timeout_overrides(timeout_tokens)

            async def _call_chatgpt_compact(target: Account) -> ProviderCompactResponseResult:
                adapter = cast(ChatGPTWebProviderAdapter, self._provider_adapter(CHATGPT_WEB_PROVIDER_KIND))
                return await _call_compact_timeout(
                    lambda: adapter.compact_response(
                        self._chatgpt_provider_subject(target),
                        payload,
                        filtered,
                    )
                )

            async def _call_platform_compact(
                identity: _SelectedPlatformIdentity,
            ) -> ProviderCompactResponseResult:
                adapter = cast(OpenAIPlatformProviderAdapter, self._provider_adapter(OPENAI_PLATFORM_PROVIDER_KIND))
                return await _call_compact_timeout(
                    lambda: adapter.compact_response(
                        self._platform_provider_subject(identity),
                        payload,
                        filtered,
                        route_class=route_class,
                    )
                )

            if isinstance(selected_subject, SelectedPlatformSubject):
                provider_kind_value = OPENAI_PLATFORM_PROVIDER_KIND
                effective_request_service_tier = payload.platform_forwarded_service_tier()
                (
                    platform_sticky_key,
                    platform_sticky_kind,
                    platform_reallocate_sticky,
                    platform_sticky_max_age_seconds,
                ) = self._platform_affinity_for_selection(
                    sticky_key=affinity.key,
                    sticky_kind=affinity.kind,
                    reallocate_sticky=affinity.reallocate_sticky,
                    sticky_max_age_seconds=affinity.max_age_seconds,
                )
                current_identity = selected_subject.identity
                excluded_identity_ids: set[str] = set()
                safe_retry_budget = _COMPACT_SAME_CONTRACT_RETRY_BUDGET
                transient_retries = 0
                while True:
                    routing_subject_id_value = current_identity.id
                    try:
                        platform_result = await _call_platform_compact(current_identity)
                        upstream_request_id = platform_result.upstream_request_id
                        response = platform_result.payload
                        actual_service_tier = _service_tier_from_response(response)
                        await self._settle_compact_api_key_usage(
                            api_key=api_key,
                            api_key_reservation=api_key_reservation,
                            response=response,
                            request_service_tier=effective_request_service_tier,
                        )
                        log_status = "success"
                        return response
                    except OpenAIPlatformError as exc:
                        upstream_request_id = exc.upstream_request_id
                        await self._record_platform_auth_failure(current_identity.id, exc)
                        if exc.status_code in {401, 403}:
                            excluded_identity_ids.add(current_identity.id)
                            replacement_identity = await self.select_platform_identity(
                                route_family,
                                sticky_key=platform_sticky_key,
                                sticky_kind=platform_sticky_kind,
                                reallocate_sticky=platform_reallocate_sticky,
                                sticky_max_age_seconds=platform_sticky_max_age_seconds,
                                exclude_routing_subject_ids=excluded_identity_ids,
                            )
                            if replacement_identity is not None:
                                current_identity = replacement_identity
                                safe_retry_budget = _COMPACT_SAME_CONTRACT_RETRY_BUDGET
                                transient_retries = 0
                                continue
                        if exc.status_code == 500:
                            transient_retries += 1
                            if (
                                transient_retries < _MAX_TRANSIENT_SAME_ACCOUNT_RETRIES
                                and _remaining_budget_seconds(deadline) > 0
                            ):
                                delay = backoff_seconds(transient_retries)
                                logger.info(
                                    "Transient compact error, retrying same platform identity "
                                    "request_id=%s routing_subject_id=%s retry=%s/%s delay=%.2fs",
                                    request_id,
                                    current_identity.id,
                                    transient_retries,
                                    _MAX_TRANSIENT_SAME_ACCOUNT_RETRIES,
                                    delay,
                                )
                                await asyncio.sleep(delay)
                                continue
                        if exc.status_code in {502, 503, 504} and safe_retry_budget > 0:
                            safe_retry_budget -= 1
                            continue
                        await self._settle_compact_api_key_usage(
                            api_key=api_key,
                            api_key_reservation=api_key_reservation,
                            response=None,
                            request_service_tier=effective_request_service_tier,
                        )
                        raise ProxyResponseError(
                            exc.status_code,
                            cast(OpenAIErrorEnvelope, exc.payload),
                            upstream_request_id=exc.upstream_request_id,
                            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                            routing_subject_id=current_identity.id,
                        ) from exc

            last_exc: ProxyResponseError | None = None
            excluded_account_ids: set[str] = set()
            for _account_attempt in range(_COMPACT_MAX_ACCOUNT_ATTEMPTS):
                selection = await self._select_account_with_budget_compatible(
                    deadline,
                    request_id=request_id,
                    kind="compact",
                    api_key=api_key,
                    sticky_key=affinity.key,
                    sticky_kind=affinity.kind,
                    reallocate_sticky=affinity.reallocate_sticky,
                    sticky_max_age_seconds=affinity.max_age_seconds,
                    sticky_budget_reallocation_enabled=affinity.budget_reallocation_enabled,
                    prefer_earlier_reset_accounts=prefer_earlier_reset,
                    routing_strategy=routing_strategy,
                    model=payload.model,
                    exclude_account_ids=excluded_account_ids,
                )
                account = selection.account
                if not account:
                    if last_exc is not None:
                        raise last_exc
                    log_error_code = selection.error_code or "no_accounts"
                    log_error_message = selection.error_message or "No active accounts available"
                    raise ProxyResponseError(
                        503,
                        openai_error(log_error_code, log_error_message),
                    )
                account_id_value = account.id
                routing_subject_id_value = account.id
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning("Compact request budget exhausted before freshness check request_id=%s", request_id)
                    _raise_proxy_budget_exhausted()
                try:
                    account = await self._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    logger.warning(
                        "Compact refresh/connect failed request_id=%s account_id=%s",
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")

                safe_retry_budget = _COMPACT_SAME_CONTRACT_RETRY_BUDGET
                transient_retries = 0
                refresh_retry_used = False
                transient_exhausted = False
                while True:
                    try:
                        provider_result = await _call_chatgpt_compact(account)
                        upstream_request_id = provider_result.upstream_request_id
                        response = provider_result.payload
                        actual_service_tier = _service_tier_from_response(response)
                        await self._load_balancer.record_success(account)
                        await self._settle_compact_api_key_usage(
                            api_key=api_key,
                            api_key_reservation=api_key_reservation,
                            response=response,
                            request_service_tier=effective_request_service_tier,
                        )
                        log_status = "success"
                        return response
                    except ProxyResponseError as exc:
                        if exc.status_code == 401:
                            if refresh_retry_used:
                                await self._settle_compact_api_key_usage(
                                    api_key=api_key,
                                    api_key_reservation=api_key_reservation,
                                    response=None,
                                    request_service_tier=effective_request_service_tier,
                                )
                                await self._handle_proxy_error(account, exc)
                                raise
                            try:
                                remaining_budget = _remaining_budget_seconds(deadline)
                                if remaining_budget <= 0:
                                    logger.warning(
                                        "Compact request budget exhausted before forced refresh retry request_id=%s "
                                        "account_id=%s",
                                        request_id,
                                        account.id,
                                    )
                                    _raise_proxy_budget_exhausted()
                                account = await self._ensure_fresh_with_budget(
                                    account,
                                    force=True,
                                    timeout_seconds=remaining_budget,
                                )
                            except RefreshError as refresh_exc:
                                if refresh_exc.is_permanent:
                                    await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                                await self._settle_compact_api_key_usage(
                                    api_key=api_key,
                                    api_key_reservation=api_key_reservation,
                                    response=None,
                                    request_service_tier=effective_request_service_tier,
                                )
                                raise exc
                            except (aiohttp.ClientError, asyncio.TimeoutError) as timeout_exc:
                                await self._settle_compact_api_key_usage(
                                    api_key=api_key,
                                    api_key_reservation=api_key_reservation,
                                    response=None,
                                    request_service_tier=effective_request_service_tier,
                                )
                                logger.warning(
                                    "Compact forced refresh/connect failed request_id=%s account_id=%s",
                                    request_id,
                                    account.id,
                                    exc_info=True,
                                )
                                _raise_proxy_unavailable(str(timeout_exc) or "Request to upstream timed out")
                            refresh_retry_used = True
                            continue
                        if exc.status_code == 500:
                            transient_retries += 1
                            if (
                                transient_retries < _MAX_TRANSIENT_SAME_ACCOUNT_RETRIES
                                and _remaining_budget_seconds(deadline) > 0
                            ):
                                delay = backoff_seconds(transient_retries)
                                logger.info(
                                    "Transient compact error, retrying same account "
                                    "request_id=%s account_id=%s retry=%s/%s delay=%.2fs",
                                    request_id,
                                    account.id,
                                    transient_retries,
                                    _MAX_TRANSIENT_SAME_ACCOUNT_RETRIES,
                                    delay,
                                )
                                await asyncio.sleep(delay)
                                continue
                            # Exhausted same-account transient retries — penalize and failover
                            logger.warning(
                                "Compact transient retries exhausted for account "
                                "request_id=%s account_id=%s retries=%s code=server_error",
                                request_id,
                                account.id,
                                transient_retries,
                            )
                            await self._handle_proxy_error(account, exc)
                            # Record remaining errors so total equals transient_retries,
                            # meeting the load balancer backoff threshold (error_count >= 3).
                            await self._load_balancer.record_errors(account, transient_retries - 1)
                            last_exc = exc
                            excluded_account_ids.add(account.id)
                            transient_exhausted = True
                            break  # break inner loop → outer loop tries different account
                        if exc.retryable_same_contract and safe_retry_budget > 0:
                            safe_retry_budget -= 1
                            continue
                        error = _parse_openai_error(exc.payload)
                        code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        if _is_account_neutral_error_code(code):
                            await self._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=effective_request_service_tier,
                            )
                            raise
                        classified = await self._handle_stream_error(
                            account,
                            _upstream_error_from_openai(error),
                            code,
                            http_status=exc.status_code,
                        )
                        if getattr(base_settings, "deterministic_failover_enabled", True):
                            action = failover_decision(
                                failure_class=classified["failure_class"],
                                downstream_visible=False,
                                candidates_remaining=_COMPACT_MAX_ACCOUNT_ATTEMPTS - _account_attempt - 1,
                            )
                        else:
                            action = "surface"
                        logger.info(
                            "Failover decision request_id=%s transport=compact account_id=%s "
                            "attempt=%d failure_class=%s action=%s",
                            request_id,
                            account.id,
                            _account_attempt + 1,
                            classified["failure_class"],
                            action,
                        )
                        if action == "failover_next":
                            last_exc = exc
                            excluded_account_ids.add(account.id)
                            transient_exhausted = True
                            break
                        await self._settle_compact_api_key_usage(
                            api_key=api_key,
                            api_key_reservation=api_key_reservation,
                            response=None,
                            request_service_tier=effective_request_service_tier,
                        )
                        raise
                if transient_exhausted:
                    continue  # outer loop: try different account
            # All account attempts exhausted — raise last error
            await self._settle_compact_api_key_usage(
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                response=None,
                request_service_tier=effective_request_service_tier,
            )
            if last_exc is not None:
                raise last_exc
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "All account attempts exhausted"),
            )
        except NotImplementedError as exc:
            log_error_code = "not_implemented"
            log_error_message = str(exc) or "Requested compact provider is not implemented"
            log_rejection_reason = "provider_compact_not_implemented"
            raise
        except ProxyResponseError as exc:
            error = _parse_openai_error(exc.payload)
            log_error_code = log_error_code or _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            log_error_message = log_error_message or (error.message if error else None)
            raise
        finally:
            usage = response.usage if response else None
            reasoning_effort = payload.reasoning.effort if payload.reasoning else None
            await self._write_request_log(
                account_id=account_id_value,
                provider_kind=provider_kind_value,
                routing_subject_id=routing_subject_id_value,
                api_key=api_key,
                request_id=request_id,
                model=payload.model,
                latency_ms=int((time.monotonic() - start) * 1000),
                status=log_status,
                error_code=log_error_code,
                error_message=log_error_message,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                cached_input_tokens=(
                    usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
                ),
                reasoning_tokens=(
                    usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
                ),
                reasoning_effort=reasoning_effort,
                transport=_REQUEST_TRANSPORT_HTTP,
                service_tier=_effective_service_tier(effective_request_service_tier, actual_service_tier),
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
                route_class=route_class,
                upstream_request_id=upstream_request_id,
                rejection_reason=log_rejection_reason,
            )
            _maybe_log_proxy_service_tier_trace(
                "compact",
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
            )

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers: Mapping[str, str],
        api_key: ApiKeyData | None = None,
    ) -> dict[str, JsonValue]:
        filtered = filter_inbound_headers(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = time.monotonic()
        base_settings = get_settings()
        deadline = start + base_settings.transcription_request_budget_seconds
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        transcribe_model = "gpt-4o-transcribe"

        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        routing_strategy = _routing_strategy(settings)
        try:
            selection = await self._select_account_with_budget_compatible(
                deadline,
                request_id=request_id,
                kind="transcribe",
                api_key=api_key,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=None,
            )
            account = selection.account
            if not account:
                log_error_code = selection.error_code or "no_accounts"
                log_error_message = selection.error_message or "No active accounts available"
                raise ProxyResponseError(
                    503,
                    openai_error(log_error_code, log_error_message),
                )
            account_id_value = account.id

            async def _call_transcribe(target: Account) -> dict[str, JsonValue]:
                adapter = cast(ChatGPTWebProviderAdapter, self._provider_adapter(CHATGPT_WEB_PROVIDER_KIND))
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Transcription request budget exhausted before upstream call request_id=%s account_id=%s",
                        request_id,
                        target.id,
                    )
                    _raise_proxy_budget_exhausted()
                timeout_tokens = push_transcribe_timeout_overrides(
                    connect_timeout_seconds=remaining_budget,
                    total_timeout_seconds=remaining_budget,
                )
                try:
                    return await adapter.transcribe_audio(
                        self._chatgpt_provider_subject(target),
                        audio_bytes=audio_bytes,
                        filename=filename,
                        content_type=content_type,
                        prompt=prompt,
                        headers=filtered,
                    )
                finally:
                    pop_transcribe_timeout_overrides(timeout_tokens)

            try:
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Transcription request budget exhausted before freshness check request_id=%s", request_id
                    )
                    _raise_proxy_budget_exhausted()
                try:
                    account = await self._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    logger.warning(
                        "Transcription refresh/connect failed request_id=%s account_id=%s",
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")
                result = await _call_transcribe(account)
                await self._load_balancer.record_success(account)
                log_status = "success"
                return result
            except RefreshError as refresh_exc:
                if refresh_exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                raise ProxyResponseError(
                    401,
                    openai_error(
                        "invalid_api_key",
                        refresh_exc.message,
                        error_type="invalid_request_error",
                    ),
                ) from refresh_exc
            except ProxyResponseError as exc:
                if exc.status_code != 401:
                    await self._handle_proxy_error(account, exc)
                    raise
                try:
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "Transcription request budget exhausted before forced refresh retry "
                            "request_id=%s account_id=%s",
                            request_id,
                            account.id,
                        )
                        _raise_proxy_budget_exhausted()
                    account = await self._ensure_fresh_with_budget(
                        account, force=True, timeout_seconds=remaining_budget
                    )
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    raise exc
                except (aiohttp.ClientError, asyncio.TimeoutError) as timeout_exc:
                    logger.warning(
                        "Transcription forced refresh/connect failed request_id=%s account_id=%s",
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    _raise_proxy_unavailable(str(timeout_exc) or "Request to upstream timed out")
                try:
                    result = await _call_transcribe(account)
                    await self._load_balancer.record_success(account)
                    log_status = "success"
                    return result
                except ProxyResponseError as exc:
                    await self._handle_proxy_error(account, exc)
                    raise
        except ProxyResponseError as exc:
            error = _parse_openai_error(exc.payload)
            log_error_code = log_error_code or _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            log_error_message = log_error_message or (error.message if error else None)
            raise
        finally:
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_id,
                model=transcribe_model,
                latency_ms=int((time.monotonic() - start) * 1000),
                status=log_status,
                error_code=log_error_code,
                error_message=log_error_message,
                transport=_REQUEST_TRANSPORT_HTTP,
            )

    async def proxy_responses_websocket(
        self,
        websocket: WebSocket,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
    ) -> None:
        filtered_headers = filter_inbound_websocket_headers(dict(headers))
        runtime_settings = get_settings()
        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        sticky_threads_enabled = settings.sticky_threads_enabled
        openai_cache_affinity_max_age_seconds = settings.openai_cache_affinity_max_age_seconds
        routing_strategy = _routing_strategy(settings)
        pending_requests: deque[_WebSocketRequestState] = deque()
        pending_lock = anyio.Lock()
        client_send_lock = anyio.Lock()
        response_create_gate = asyncio.Semaphore(1)
        upstream: UpstreamResponsesWebSocket | None = None
        upstream_reader: asyncio.Task[None] | None = None
        upstream_control: _WebSocketUpstreamControl | None = None
        account: Account | None = None
        upstream_turn_state: str | None = _sticky_key_from_turn_state_header(headers)
        downstream_activity = _DownstreamWebSocketActivity()
        replay_request_state: _WebSocketRequestState | None = None

        try:
            while True:
                if upstream_reader is not None and upstream_reader.done():
                    try:
                        await upstream_reader
                    except asyncio.CancelledError:
                        pass
                    if replay_request_state is None and upstream_control is not None:
                        replay_request_state = upstream_control.replay_request_state
                    upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket", exc_info=True)
                    upstream = None
                    account = None

                text_data: str | None = None
                bytes_data: bytes | None = None
                request_state: _WebSocketRequestState | None = None
                request_state_registered = False
                response_create_gate_acquired = False
                request_affinity = _AffinityPolicy()
                payload: dict[str, JsonValue] | None = None

                if replay_request_state is not None:
                    request_state = replay_request_state
                    replay_request_state = None
                    request_affinity = request_state.affinity_policy
                    text_data = request_state.request_text
                    if text_data is None:
                        await self._release_websocket_reservation(request_state.api_key_reservation)
                        await self._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code="stream_incomplete",
                            error_message="Upstream websocket closed before response.completed",
                            error_type="server_error",
                            downstream_activity=downstream_activity,
                        )
                        _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    payload = _parse_websocket_payload(text_data)
                    if payload is None:
                        await self._release_websocket_reservation(request_state.api_key_reservation)
                        await self._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code="upstream_error",
                            error_message="Invalid replay request payload",
                            error_type="server_error",
                            downstream_activity=downstream_activity,
                        )
                        _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    async with pending_lock:
                        pending_requests.append(request_state)
                    request_state_registered = True
                else:
                    downstream_idle_timeout_seconds = runtime_settings.proxy_downstream_websocket_idle_timeout_seconds
                    try:
                        message = await asyncio.wait_for(
                            websocket.receive(),
                            timeout=min(downstream_idle_timeout_seconds, _DOWNSTREAM_WEBSOCKET_RECEIVE_POLL_SECONDS),
                        )
                    except asyncio.TimeoutError:
                        if not await self._downstream_websocket_is_idle(
                            pending_requests,
                            pending_lock=pending_lock,
                            downstream_activity=downstream_activity,
                            idle_timeout_seconds=downstream_idle_timeout_seconds,
                        ):
                            continue
                        idle_close = False
                        async with client_send_lock:
                            if await self._downstream_websocket_is_idle(
                                pending_requests,
                                pending_lock=pending_lock,
                                downstream_activity=downstream_activity,
                                idle_timeout_seconds=downstream_idle_timeout_seconds,
                            ):
                                try:
                                    message = await asyncio.wait_for(websocket.receive(), timeout=0.05)
                                except asyncio.TimeoutError:
                                    try:
                                        await websocket.close(code=1001, reason=_DOWNSTREAM_WEBSOCKET_IDLE_CLOSE_REASON)
                                    except Exception:
                                        logger.debug("Failed to close idle downstream websocket", exc_info=True)
                                    idle_close = True
                        if idle_close:
                            break
                    downstream_activity.mark()
                    message_type = message["type"]

                    if message_type == "websocket.disconnect":
                        break
                    if message_type != "websocket.receive":
                        continue

                    text_data = message.get("text")
                    bytes_data = message.get("bytes")

                    if text_data is not None:
                        payload = _parse_websocket_payload(text_data)
                        if payload is not None and _is_websocket_response_create(payload):
                            try:
                                prepared_request = await self._prepare_websocket_response_create_request(
                                    payload,
                                    headers=headers,
                                    codex_session_affinity=codex_session_affinity,
                                    openai_cache_affinity=openai_cache_affinity,
                                    sticky_threads_enabled=sticky_threads_enabled,
                                    openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
                                    api_key=api_key,
                                )
                                request_state = prepared_request.request_state
                                request_affinity = prepared_request.affinity_policy
                                text_data = prepared_request.text_data
                            except ProxyResponseError as exc:
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(exc.status_code, exc.payload)
                                        )
                                    )
                                continue
                            except AppError as exc:
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(_app_error_to_websocket_event(exc))
                                    )
                                continue
                            except ClientPayloadError as exc:
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(400, openai_invalid_payload_error(exc.param))
                                        )
                                    )
                                continue
                            except ValidationError as exc:
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(400, openai_validation_error(exc))
                                        )
                                    )
                                continue

                if upstream_reader is not None and upstream_reader.done():
                    try:
                        await upstream_reader
                    except asyncio.CancelledError:
                        pass
                    if replay_request_state is None and upstream_control is not None:
                        replay_request_state = upstream_control.replay_request_state
                    upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket", exc_info=True)
                    upstream = None
                    account = None

                if (
                    request_state is not None
                    and upstream_control is not None
                    and upstream_control.reconnect_requested
                    and upstream_reader is not None
                ):
                    await upstream_reader
                    if replay_request_state is None:
                        replay_request_state = upstream_control.replay_request_state
                    upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket", exc_info=True)
                    upstream = None
                    account = None

                if (
                    request_state is not None
                    and request_state.previous_response_id is not None
                    and request_state.preferred_account_id is None
                ):
                    try:
                        request_state.preferred_account_id = await self._resolve_websocket_previous_response_owner(
                            previous_response_id=request_state.previous_response_id,
                            api_key=request_state.api_key or api_key,
                            session_id=request_state.session_id,
                            surface="websocket",
                        )
                    except ProxyResponseError as exc:
                        error = _parse_openai_error(exc.payload)
                        error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error and error.message else "Upstream error"
                        error_type = error.type if error and error.type else "server_error"
                        await self._release_websocket_reservation(request_state.api_key_reservation)
                        await self._write_websocket_connect_failure(
                            account_id=None,
                            api_key=api_key,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                        )
                        await self._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                            error_type=error_type,
                            downstream_activity=downstream_activity,
                        )
                        request_state = None
                        text_data = None
                        payload = None
                        continue

                if request_state is not None and not request_state_registered:
                    try:
                        await self._acquire_request_state_response_create_admission(
                            request_state,
                            response_create_gate=response_create_gate,
                        )
                        async with pending_lock:
                            pending_requests.append(request_state)
                        request_state_registered = True
                    except ProxyResponseError as exc:
                        error = _parse_openai_error(exc.payload)
                        error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error and error.message else "Upstream error"
                        error_type = error.type if error and error.type else "server_error"
                        await self._release_websocket_reservation(request_state.api_key_reservation)
                        await self._write_websocket_connect_failure(
                            account_id=account.id if account else None,
                            api_key=api_key,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                        )
                        await self._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                            error_type=error_type,
                            downstream_activity=downstream_activity,
                        )
                        _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    except asyncio.CancelledError:
                        await self._release_websocket_reservation(request_state.api_key_reservation)
                        if request_state_registered:
                            async with pending_lock:
                                if request_state in pending_requests:
                                    pending_requests.remove(request_state)
                        _release_websocket_response_create_gate(request_state, response_create_gate)
                        raise
                    except Exception:
                        await self._release_websocket_reservation(request_state.api_key_reservation)
                        if request_state_registered:
                            async with pending_lock:
                                if request_state in pending_requests:
                                    pending_requests.remove(request_state)
                        _release_websocket_response_create_gate(request_state, response_create_gate)
                        raise

                if upstream is None:
                    if text_data is not None and payload is None:
                        async with client_send_lock:
                            await websocket.send_text(
                                _serialize_websocket_error_event(
                                    _wrapped_websocket_error_event(400, openai_invalid_payload_error())
                                )
                            )
                        continue
                    if request_state is None:
                        async with client_send_lock:
                            await websocket.send_text(
                                _serialize_websocket_error_event(
                                    _wrapped_websocket_error_event(
                                        400,
                                        openai_error(
                                            "invalid_request_error",
                                            "WebSocket connection has no active upstream session",
                                            error_type="invalid_request_error",
                                        ),
                                    )
                                )
                            )
                        continue
                    connect_headers = _headers_with_turn_state(filtered_headers, upstream_turn_state)
                    account, upstream = await self._connect_proxy_websocket(
                        connect_headers,
                        sticky_key=request_affinity.key,
                        sticky_kind=request_affinity.kind,
                        reallocate_sticky=request_affinity.reallocate_sticky,
                        sticky_max_age_seconds=request_affinity.max_age_seconds,
                        prefer_earlier_reset=prefer_earlier_reset,
                        routing_strategy=routing_strategy,
                        model=request_state.model,
                        request_state=request_state,
                        api_key=api_key,
                        client_send_lock=client_send_lock,
                        websocket=websocket,
                    )
                    if upstream is None or account is None:
                        if request_state_registered:
                            async with pending_lock:
                                if request_state in pending_requests:
                                    pending_requests.remove(request_state)
                            _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    upstream_turn_state = _upstream_turn_state_from_socket(upstream) or upstream_turn_state
                    upstream_control = _WebSocketUpstreamControl()
                    upstream_reader = asyncio.create_task(
                        self._relay_upstream_websocket_messages(
                            websocket,
                            upstream,
                            account=account,
                            account_id_value=account.id,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            client_send_lock=client_send_lock,
                            api_key=api_key,
                            upstream_control=upstream_control,
                            response_create_gate=response_create_gate,
                            proxy_request_budget_seconds=runtime_settings.proxy_request_budget_seconds,
                            stream_idle_timeout_seconds=runtime_settings.stream_idle_timeout_seconds,
                            downstream_activity=downstream_activity,
                        )
                    )

                try:
                    if text_data is not None:
                        await upstream.send_text(text_data)
                    elif bytes_data is not None:
                        await upstream.send_bytes(bytes_data)
                except asyncio.CancelledError:
                    if request_state is not None and not request_state_registered:
                        await self._cleanup_unregistered_websocket_request(
                            request_state,
                            response_create_gate=response_create_gate,
                            gate_acquired=response_create_gate_acquired,
                        )
                    raise
                except Exception:
                    replay_candidate = await _pop_replayable_precreated_websocket_request_state(
                        pending_requests,
                        pending_lock=pending_lock,
                    )
                    if replay_candidate is not None:
                        logger.info(
                            "Transparent websocket replay after upstream send failure request_id=%s",
                            replay_candidate.request_log_id or replay_candidate.request_id,
                        )
                        replay_request_state = replay_candidate
                        if upstream_reader is not None:
                            await _await_cancelled_task(upstream_reader, label="proxy websocket upstream reader")
                            upstream_reader = None
                        upstream_control = None
                        if upstream is not None:
                            try:
                                await upstream.close()
                            except Exception:
                                logger.debug(
                                    "Failed to close upstream websocket after replayable send failure",
                                    exc_info=True,
                                )
                        upstream = None
                        account = None
                        continue
                    await self._fail_pending_websocket_requests(
                        account_id_value=account.id if account else None,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        error_code="stream_incomplete",
                        error_message="Upstream websocket closed before response.completed",
                        api_key=api_key,
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        response_create_gate=response_create_gate,
                        downstream_activity=downstream_activity,
                    )
                    if upstream_reader is not None:
                        await _await_cancelled_task(upstream_reader, label="proxy websocket upstream reader")
                        upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket after send failure", exc_info=True)
                    upstream = None
                    account = None
                    continue
        finally:
            if upstream_reader is not None:
                await _await_cancelled_task(upstream_reader, label="proxy websocket upstream reader")
            if upstream is not None:
                try:
                    await upstream.close()
                except Exception:
                    logger.debug("Failed to close upstream websocket", exc_info=True)
            await self._fail_pending_websocket_requests(
                account_id_value=account.id if account else None,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="stream_incomplete",
                error_message="Upstream websocket closed before response.completed",
                api_key=api_key,
                websocket=websocket,
                client_send_lock=client_send_lock,
                response_create_gate=response_create_gate,
                downstream_activity=downstream_activity,
            )

    async def _prepare_websocket_response_create_request(
        self,
        payload: dict[str, JsonValue],
        *,
        headers: Mapping[str, str],
        codex_session_affinity: bool,
        openai_cache_affinity: bool,
        sticky_threads_enabled: bool,
        openai_cache_affinity_max_age_seconds: int,
        api_key: ApiKeyData | None,
    ) -> _PreparedWebSocketRequest:
        refreshed_api_key = await self._refresh_websocket_api_key_policy(api_key)
        client_metadata = _response_create_client_metadata(payload, headers=headers)
        responses_payload = normalize_responses_request_payload(payload, openai_compat=openai_cache_affinity)
        apply_api_key_enforcement(responses_payload, refreshed_api_key)
        validate_model_access(refreshed_api_key, responses_payload.model)
        reservation = await self._reserve_websocket_api_key_usage(
            refreshed_api_key,
            request_model=responses_payload.model,
            request_service_tier=_normalize_service_tier_value(
                dict(responses_payload.to_payload()).get("service_tier")
            ),
        )
        try:
            session_id = _owner_lookup_session_id_from_headers(headers)
            request_state, text_data = self._prepare_response_bridge_request_state(
                responses_payload,
                api_key=refreshed_api_key,
                api_key_reservation=reservation,
                include_type_field=True,
                attach_event_queue=False,
                transport=_REQUEST_TRANSPORT_WEBSOCKET,
                client_metadata=client_metadata,
                session_id=session_id,
            )
        except ProxyResponseError:
            await self._release_websocket_reservation(reservation)
            raise
        had_prompt_cache_key = _prompt_cache_key_from_request_model(responses_payload) is not None
        affinity_policy = _sticky_key_for_responses_request(
            responses_payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=sticky_threads_enabled,
            api_key=api_key,
        )
        sticky_key_source = "none"
        if affinity_policy.kind == StickySessionKind.CODEX_SESSION:
            sticky_key_source = (
                "turn_state_header" if _sticky_key_from_turn_state_header(headers) is not None else "session_header"
            )
        elif affinity_policy.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "websocket",
            responses_payload,
            headers,
            sticky_kind=affinity_policy.kind.value if affinity_policy.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(responses_payload) is not None,
        )
        request_state.affinity_policy = affinity_policy

        return _PreparedWebSocketRequest(
            text_data=text_data,
            request_state=request_state,
            affinity_policy=affinity_policy,
        )

    def _prepare_http_bridge_request(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        request_id: str | None = None,
    ) -> tuple[_WebSocketRequestState, str]:
        return self._prepare_response_bridge_request_state(
            payload,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            include_type_field=True,
            attach_event_queue=True,
            transport=_REQUEST_TRANSPORT_HTTP,
            client_metadata=_response_create_client_metadata(payload.to_payload(), headers=headers),
            session_id=_owner_lookup_session_id_from_headers(headers),
            request_log_id=request_id or get_request_id() or ensure_request_id(None),
        )

    def _prepare_response_bridge_request_state(
        self,
        payload: ResponsesRequest,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        include_type_field: bool,
        attach_event_queue: bool,
        transport: str,
        client_metadata: Mapping[str, JsonValue] | None,
        session_id: str | None = None,
        request_id: str | None = None,
        request_log_id: str | None = None,
    ) -> tuple[_WebSocketRequestState, str]:
        upstream_payload = dict(payload.to_payload())
        service_tier = _normalize_service_tier_value(upstream_payload.get("service_tier"))
        if service_tier is not None:
            upstream_payload["service_tier"] = service_tier
        upstream_payload.pop("stream", None)
        upstream_payload.pop("background", None)
        if include_type_field:
            upstream_payload["type"] = "response.create"
        if client_metadata:
            upstream_payload["client_metadata"] = client_metadata
        forwarded_service_tier = _normalize_service_tier_value(upstream_payload.get("service_tier"))
        input_item_count = 0
        input_full_fingerprint: str | None = None
        payload_input = payload.input
        if isinstance(payload_input, list):
            payload_input_list = cast(list[JsonValue], payload_input)
            input_item_count = len(payload_input_list)
            if input_item_count > 0:
                input_full_fingerprint = _fingerprint_input_items(payload_input_list)

        request_state = _WebSocketRequestState(
            request_id=request_id or f"ws_{uuid4().hex}",
            request_log_id=request_log_id,
            model=payload.model,
            service_tier=forwarded_service_tier,
            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
            api_key_reservation=api_key_reservation,
            started_at=time.monotonic(),
            requested_service_tier=forwarded_service_tier,
            awaiting_response_created=True,
            event_queue=asyncio.Queue() if attach_event_queue else None,
            transport=transport,
            api_key=api_key,
            previous_response_id=payload.previous_response_id,
            session_id=_normalize_session_id(session_id),
            input_item_count=input_item_count,
            input_full_fingerprint=input_full_fingerprint,
        )
        text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
        payload_size = len(text_data.encode("utf-8"))
        if payload_size > _UPSTREAM_RESPONSE_CREATE_MAX_BYTES:
            slimmed_payload, slim_summary = _slim_response_create_payload_for_upstream(
                upstream_payload,
                max_bytes=_UPSTREAM_RESPONSE_CREATE_MAX_BYTES,
            )
            if slim_summary is not None:
                upstream_payload = slimmed_payload
                text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
                logger.warning(
                    (
                        "Slimmed response.create request_id=%s request_log_id=%s transport=%s "
                        "original_bytes=%s slimmed_bytes=%s "
                        "historical_tool_outputs_slimmed=%s historical_images_slimmed=%s"
                    ),
                    request_state.request_id,
                    request_state.request_log_id,
                    transport,
                    payload_size,
                    len(text_data.encode("utf-8")),
                    slim_summary["historical_tool_outputs_slimmed"],
                    slim_summary["historical_images_slimmed"],
                )
        request_state.request_text = text_data
        _enforce_response_create_size_limit(request_state)
        return request_state, text_data

    async def _acquire_request_state_response_create_admission(
        self,
        request_state: _WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
        compact: bool = False,
    ) -> None:
        request_state.response_create_gate = response_create_gate
        await response_create_gate.acquire()
        request_state.response_create_gate_acquired = True
        request_state.awaiting_response_created = True
        try:
            request_state.response_create_admission = await self._get_work_admission().acquire_response_create(
                compact=compact
            )
        except BaseException:
            _release_websocket_response_create_gate(request_state, response_create_gate)
            raise

    async def _connect_proxy_websocket(
        self,
        headers: dict[str, str],
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        prefer_earlier_reset: bool,
        routing_strategy: RoutingStrategy,
        model: str | None,
        request_state: _WebSocketRequestState,
        api_key: ApiKeyData | None,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
    ) -> tuple[Account | None, UpstreamResponsesWebSocket | None]:
        deadline = _websocket_connect_deadline(request_state, get_settings().proxy_request_budget_seconds)
        base_settings = get_settings()
        max_attempts = _WEBSOCKET_MAX_ACCOUNT_ATTEMPTS
        excluded_account_ids: set[str] = set()
        last_failover_exc: ProxyResponseError | None = None
        last_failover_account: Account | None = None
        for attempt in range(max_attempts):
            is_retry = attempt > 0
            account = await self._select_websocket_connect_account(
                deadline,
                sticky_key=sticky_key,
                sticky_kind=sticky_kind,
                prefer_earlier_reset=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=model,
                request_state=request_state,
                api_key=api_key,
                client_send_lock=client_send_lock,
                websocket=websocket,
                reallocate_sticky=True if is_retry else reallocate_sticky,
                sticky_max_age_seconds=sticky_max_age_seconds,
                exclude_account_ids=excluded_account_ids,
                preferred_account_id=request_state.preferred_account_id,
                require_preferred_account=(
                    request_state.previous_response_id is not None and request_state.preferred_account_id is not None
                ),
            )
            if account is None:
                return None, None

            try:
                connect_result = await self._try_open_websocket_connect_attempt(
                    account,
                    headers,
                    deadline=deadline,
                    api_key=api_key,
                    request_state=request_state,
                    client_send_lock=client_send_lock,
                    websocket=websocket,
                )
            except ProxyResponseError as exc:
                action = await self._decide_websocket_failover_action(
                    account=account,
                    exc=exc,
                    request_state=request_state,
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    deterministic_failover_enabled=getattr(base_settings, "deterministic_failover_enabled", True),
                )
                if action == "failover_next":
                    last_failover_exc = exc
                    last_failover_account = account
                    excluded_account_ids.add(account.id)
                    continue
                error = _parse_openai_error(exc.payload)
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                error_message = error.message if error else None
                await self._emit_websocket_connect_failure(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                    status_code=exc.status_code,
                    payload=exc.payload,
                    error_code=error_code or "upstream_error",
                    error_message=error_message or "Upstream error",
                )
                return None, None

            if connect_result is None:
                return None, None
            return connect_result

        if last_failover_exc is not None and last_failover_account is not None:
            error = _parse_openai_error(last_failover_exc.payload)
            error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            error_message = error.message if error else None
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=last_failover_account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=last_failover_exc.status_code,
                payload=last_failover_exc.payload,
                error_code=error_code or "upstream_error",
                error_message=error_message or "Upstream error",
            )
        return None, None

    async def _select_websocket_connect_account(
        self,
        deadline: float,
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        prefer_earlier_reset: bool,
        routing_strategy: RoutingStrategy,
        model: str | None,
        request_state: _WebSocketRequestState,
        api_key: ApiKeyData | None,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
        reallocate_sticky: bool,
        sticky_max_age_seconds: int | None,
        exclude_account_ids: set[str],
        preferred_account_id: str | None,
        require_preferred_account: bool,
    ) -> Account | None:
        try:
            selection = await self._select_account_with_budget_compatible(
                deadline,
                request_id=request_state.request_log_id or request_state.request_id,
                kind="websocket",
                api_key=api_key,
                sticky_key=sticky_key,
                sticky_kind=sticky_kind,
                reallocate_sticky=reallocate_sticky,
                sticky_max_age_seconds=sticky_max_age_seconds,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=model,
                exclude_account_ids=exclude_account_ids,
                preferred_account_id=preferred_account_id,
            )
        except ProxyResponseError as exc:
            if _is_proxy_budget_exhausted_error(exc):
                await self._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=None,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            raise

        account = selection.account
        if (
            account is not None
            and require_preferred_account
            and preferred_account_id is not None
            and account.id != preferred_account_id
        ):
            message = "Previous response owner account is unavailable; retry later."
            _record_continuity_fail_closed(
                surface="websocket_connect",
                reason="owner_account_unavailable",
                previous_response_id=request_state.previous_response_id,
                session_id=request_state.session_id,
                upstream_error_code="upstream_unavailable",
            )
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=preferred_account_id,
                api_key=api_key,
                request_state=request_state,
                status_code=502,
                payload=openai_error(
                    "upstream_unavailable",
                    message,
                    error_type="server_error",
                ),
                error_code="upstream_unavailable",
                error_message=message,
            )
            return None
        if account:
            return account
        error_code = selection.error_code or "no_accounts"
        error_message = selection.error_message or "No active accounts available"
        if require_preferred_account and preferred_account_id is not None:
            message = "Previous response owner account is unavailable; retry later."
            _record_continuity_fail_closed(
                surface="websocket_connect",
                reason="owner_account_unavailable",
                previous_response_id=request_state.previous_response_id,
                session_id=request_state.session_id,
                upstream_error_code=error_code,
            )
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=preferred_account_id,
                api_key=api_key,
                request_state=request_state,
                status_code=502,
                payload=openai_error(
                    "upstream_unavailable",
                    message,
                    error_type="server_error",
                ),
                error_code="upstream_unavailable",
                error_message=message,
            )
            return None
        await self._emit_websocket_connect_failure(
            websocket,
            client_send_lock=client_send_lock,
            account_id=None,
            api_key=api_key,
            request_state=request_state,
            status_code=503,
            payload=openai_error(
                error_code,
                error_message,
                error_type="server_error",
            ),
            error_code=error_code,
            error_message=error_message,
        )
        return None

    async def _try_open_websocket_connect_attempt(
        self,
        account: Account,
        headers: dict[str, str],
        *,
        deadline: float,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
    ) -> tuple[Account, UpstreamResponsesWebSocket] | None:
        try:
            remaining_budget = _remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await self._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            account = await self._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)

            remaining_budget = _remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await self._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            return account, await self._open_upstream_websocket_with_budget(
                account,
                headers,
                timeout_seconds=remaining_budget,
            )
        except ProxyResponseError as exc:
            if _is_proxy_budget_exhausted_error(exc):
                await self._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            if exc.status_code != 401:
                raise
            return await self._retry_websocket_connect_after_401(
                account,
                headers,
                deadline=deadline,
                api_key=api_key,
                request_state=request_state,
                client_send_lock=client_send_lock,
                websocket=websocket,
            )
        except RefreshError as exc:
            if exc.is_permanent:
                await self._load_balancer.mark_permanent_failure(account, exc.code)
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=401,
                payload=openai_error(
                    "invalid_api_key",
                    exc.message,
                    error_type="authentication_error",
                ),
                error_code="invalid_api_key",
                error_message=exc.message,
            )
            return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            message = str(exc) or "Request to upstream timed out"
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=502,
                payload=openai_error(
                    "upstream_unavailable",
                    message,
                    error_type="server_error",
                ),
                error_code="upstream_unavailable",
                error_message=message,
            )
            return None

    async def _retry_websocket_connect_after_401(
        self,
        account: Account,
        headers: dict[str, str],
        *,
        deadline: float,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
    ) -> tuple[Account, UpstreamResponsesWebSocket] | None:
        try:
            remaining_budget = _remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await self._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            account = await self._ensure_fresh_with_budget(
                account,
                force=True,
                timeout_seconds=remaining_budget,
            )
        except RefreshError as refresh_exc:
            if refresh_exc.is_permanent:
                await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=401,
                payload=openai_error(
                    "invalid_api_key",
                    refresh_exc.message,
                    error_type="authentication_error",
                ),
                error_code="invalid_api_key",
                error_message=refresh_exc.message,
            )
            return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as refresh_transport_exc:
            message = str(refresh_transport_exc) or "Request to upstream timed out"
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=502,
                payload=openai_error(
                    "upstream_unavailable",
                    message,
                    error_type="server_error",
                ),
                error_code="upstream_unavailable",
                error_message=message,
            )
            return None

        try:
            remaining_budget = _remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await self._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            return account, await self._open_upstream_websocket_with_budget(
                account,
                headers,
                timeout_seconds=remaining_budget,
            )
        except ProxyResponseError as exc:
            if _is_proxy_budget_exhausted_error(exc):
                await self._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            raise

    async def _decide_websocket_failover_action(
        self,
        *,
        account: Account,
        exc: ProxyResponseError,
        request_state: _WebSocketRequestState,
        attempt: int,
        max_attempts: int,
        deterministic_failover_enabled: bool,
    ) -> str:
        classified = await self._handle_websocket_connect_error(account, exc)
        failure_class = classified["failure_class"] if isinstance(classified, dict) else "non_retryable"
        if deterministic_failover_enabled:
            action = failover_decision(
                failure_class=failure_class,
                downstream_visible=False,
                candidates_remaining=max_attempts - attempt,
            )
        else:
            action = "surface"
        logger.info(
            "Failover decision request_id=%s transport=websocket account_id=%s attempt=%d failure_class=%s action=%s",
            request_state.request_log_id or request_state.request_id,
            account.id,
            attempt,
            failure_class,
            action,
        )
        return action

    async def _emit_websocket_connect_timeout(
        self,
        *,
        websocket: WebSocket,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
    ) -> None:
        await self._emit_websocket_proxy_request_timeout(
            websocket,
            client_send_lock=client_send_lock,
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
        )

    async def _open_upstream_websocket_with_budget(
        self,
        account: Account,
        headers: dict[str, str],
        *,
        timeout_seconds: float,
    ) -> UpstreamResponsesWebSocket:
        try:
            with anyio.fail_after(timeout_seconds):
                return await self._open_upstream_websocket(account, headers)
        except TimeoutError:
            _raise_proxy_budget_exhausted()

    async def _open_upstream_websocket(
        self,
        account: Account,
        headers: dict[str, str],
    ) -> UpstreamResponsesWebSocket:
        adapter = cast(ChatGPTWebProviderAdapter, self._provider_adapter(CHATGPT_WEB_PROVIDER_KIND))
        connect_lease = await self._get_work_admission().acquire_websocket_connect()
        try:
            return await adapter.open_responses_websocket(
                self._chatgpt_provider_subject(account),
                headers,
            )
        finally:
            connect_lease.release()

    async def _http_bridge_pending_count(self, session: "_HTTPBridgeSession") -> int:
        async with session.pending_lock:
            return max(len(session.pending_requests), session.queued_request_count)

    async def _select_account_with_budget_compatible(
        self,
        deadline: float,
        **kwargs: object,
    ) -> AccountSelection:
        select_account = self._select_account_with_budget
        select_account_any = cast(Any, select_account)
        try:
            signature = inspect.signature(select_account)
        except (TypeError, ValueError):
            return await select_account_any(deadline, **kwargs)

        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            return await select_account_any(deadline, **kwargs)

        supported_kwargs = {name: value for name, value in kwargs.items() if name in signature.parameters}
        return await select_account_any(deadline, **supported_kwargs)

    async def _create_http_bridge_session_compatible(
        self,
        key: "_HTTPBridgeSessionKey",
        **kwargs: object,
    ) -> "_HTTPBridgeSession":
        create_session = self._create_http_bridge_session
        create_session_any = cast(Any, create_session)
        try:
            signature = inspect.signature(create_session)
        except (TypeError, ValueError):
            return await create_session_any(key, **kwargs)

        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            return await create_session_any(key, **kwargs)

        supported_kwargs = {name: value for name, value in kwargs.items() if name in signature.parameters}
        return await create_session_any(key, **supported_kwargs)

    @overload
    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: Literal[False] = False,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
    ) -> "_HTTPBridgeSession": ...

    @overload
    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: Literal[True],
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward": ...

    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: bool = False,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward":
        settings = get_settings()
        api_key_id = api_key.id if api_key is not None else None
        incoming_turn_state = _sticky_key_from_turn_state_header(headers)
        incoming_session_key = _sticky_key_from_session_header(headers)
        if await _http_bridge_should_wait_for_registration(self, key, settings):
            skip_registration_gate = False
            async with self._http_bridge_lock:
                existing = self._http_bridge_sessions.get(key)
                if existing is not None:
                    skip_registration_gate = True
                elif incoming_turn_state is not None:
                    alias_index_key = _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                    alias_key = self._http_bridge_turn_state_index.get(alias_index_key)
                    if alias_key is not None and alias_key in self._http_bridge_sessions:
                        skip_registration_gate = True
            if not skip_registration_gate:
                import app.core.startup as startup_module

                registered = await startup_module.wait_for_bridge_registration(
                    timeout_seconds=settings.upstream_connect_timeout_seconds,
                )
                if not registered:
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_owner_unreachable",
                            "HTTP bridge registration is not ready",
                            error_type="server_error",
                        ),
                    )
        effective_idle_ttl_seconds = idle_ttl_seconds
        forwarded_affinity = (
            _forwarded_http_bridge_session_key(
                headers,
                api_key,
                forwarded_affinity_kind=forwarded_affinity_kind,
                forwarded_affinity_key=forwarded_affinity_key,
            )
            if forwarded_request
            else None
        )
        old_account_id: str | None = None
        while True:
            sessions_to_close: list[_HTTPBridgeSession] = []
            inflight_future: asyncio.Future[_HTTPBridgeSession] | None = None
            capacity_wait_future: asyncio.Future[_HTTPBridgeSession] | None = None
            owns_creation = False
            continuity_error: ProxyResponseError | None = None
            owner_mismatch_error: ProxyResponseError | None = None
            owner_forward: _HTTPBridgeOwnerForward | None = None
            force_durable_takeover = False
            missing_turn_state_alias = False
            used_session_header_fallback = False
            preserve_durable_canonical_key = (
                incoming_turn_state is not None
                and forwarded_affinity is None
                and durable_lookup is not None
                and key.affinity_kind == durable_lookup.canonical_kind
                and key.affinity_key == durable_lookup.canonical_key
                and key.affinity_kind != "turn_state_header"
            )

            async with self._http_bridge_lock:
                if (
                    incoming_turn_state is not None
                    and forwarded_affinity is None
                    and not preserve_durable_canonical_key
                ):
                    alias_index_key = _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                    alias_key = self._http_bridge_turn_state_index.get(alias_index_key)
                    if alias_key is not None:
                        key = alias_key
                        alias_session = self._http_bridge_sessions.get(alias_key)
                        if (
                            alias_session is None
                            or alias_session.closed
                            or alias_session.account.status != AccountStatus.ACTIVE
                            or not _http_bridge_session_matches_preferred_account(
                                session=alias_session,
                                previous_response_id=previous_response_id,
                                preferred_account_id=preferred_account_id,
                            )
                        ):
                            self._http_bridge_turn_state_index.pop(alias_index_key, None)
                            key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                        else:
                            self._promote_http_bridge_session_to_codex_affinity(
                                alias_session,
                                turn_state=incoming_turn_state,
                                settings=settings,
                            )
                            for alias in alias_session.downstream_turn_state_aliases:
                                self._http_bridge_turn_state_index[
                                    _http_bridge_turn_state_alias_key(alias, alias_session.key.api_key_id)
                                ] = alias_session.key
                            key = alias_session.key
                    elif incoming_turn_state.startswith("http_turn_"):
                        if previous_response_id is not None:
                            previous_alias_key = _http_bridge_previous_response_alias_key(
                                previous_response_id,
                                api_key_id,
                            )
                            previous_key = self._http_bridge_previous_response_index.get(previous_alias_key)
                            previous_session = None
                            if previous_key is not None:
                                previous_session = self._http_bridge_sessions.get(previous_key)
                            if (
                                previous_session is not None
                                and not previous_session.closed
                                and previous_session.account.status == AccountStatus.ACTIVE
                                and _http_bridge_session_matches_preferred_account(
                                    session=previous_session,
                                    previous_response_id=previous_response_id,
                                    preferred_account_id=preferred_account_id,
                                )
                            ):
                                key = previous_session.key
                                self._promote_http_bridge_session_to_codex_affinity(
                                    previous_session,
                                    turn_state=incoming_turn_state,
                                    settings=settings,
                                )
                                previous_session.downstream_turn_state_aliases.add(incoming_turn_state)
                                for alias in previous_session.downstream_turn_state_aliases:
                                    self._http_bridge_turn_state_index[
                                        _http_bridge_turn_state_alias_key(
                                            alias,
                                            previous_session.key.api_key_id,
                                        )
                                    ] = previous_session.key
                                continue
                            if previous_key is not None:
                                self._http_bridge_previous_response_index.pop(previous_alias_key, None)
                        if incoming_session_key is not None:
                            key = _HTTPBridgeSessionKey("session_header", incoming_session_key, api_key_id)
                            used_session_header_fallback = True
                        else:
                            key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                            missing_turn_state_alias = True

                await self._prune_http_bridge_sessions_locked()

                existing = self._http_bridge_sessions.get(key)
                if (
                    existing is not None
                    and not existing.closed
                    and existing.account.status == AccountStatus.ACTIVE
                    and _http_bridge_session_allows_api_key(existing, api_key)
                    and _http_bridge_session_reusable_for_request(
                        session=existing,
                        key=key,
                        incoming_turn_state=incoming_turn_state,
                        previous_response_id=previous_response_id,
                    )
                    and _http_bridge_session_matches_preferred_account(
                        session=existing,
                        previous_response_id=previous_response_id,
                        preferred_account_id=preferred_account_id,
                    )
                ):
                    current_instance = settings.http_responses_session_bridge_instance_id
                    if _durable_bridge_lookup_allows_local_reuse(durable_lookup, current_instance=current_instance):
                        existing.api_key = api_key
                        existing.request_model = request_model
                        existing.last_used_at = time.monotonic()
                        await self._refresh_durable_http_bridge_session(existing)
                        _log_http_bridge_event(
                            "reuse",
                            key,
                            account_id=existing.account.id,
                            model=existing.request_model,
                            pending_count=await self._http_bridge_pending_count(existing),
                            cache_key_family=key.affinity_kind,
                            model_class=_extract_model_class(existing.request_model)
                            if existing.request_model
                            else None,
                        )
                        return existing
                    old_account_id = existing.account.id
                    self._http_bridge_sessions.pop(key, None)
                    self._unregister_http_bridge_turn_states_locked(existing)
                    existing.closed = True
                    sessions_to_close.append(existing)
                    existing = None
                if existing is not None and not existing.closed and existing.account.status == AccountStatus.ACTIVE:
                    old_account_id = existing.account.id
                    self._http_bridge_sessions.pop(key, None)
                    self._unregister_http_bridge_turn_states_locked(existing)
                    existing.closed = True
                    sessions_to_close.append(existing)
                    existing = None

                if shutdown_state.is_bridge_drain_active() and not _http_bridge_can_recover_during_drain(
                    key=key,
                    headers=headers,
                    previous_response_id=previous_response_id,
                    durable_lookup=durable_lookup,
                ):
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_drain_active",
                            "HTTP bridge is draining — new sessions not accepted during shutdown",
                            error_type="server_error",
                        ),
                    )
                if shutdown_state.is_bridge_drain_active():
                    _record_bridge_drain_recovery_allowed()

                owner_check_required = _http_bridge_owner_check_required(
                    key,
                    gateway_safe_mode=gateway_safe_mode,
                )
                if owner_check_required or key.affinity_kind == "prompt_cache":
                    owner_instance = _durable_bridge_lookup_active_owner(durable_lookup)
                    hard_continuity_lookup = owner_check_required or previous_response_id is not None
                    ring_lookup_failed = False
                    if owner_instance is None:
                        try:
                            owner_instance = await _http_bridge_owner_instance(key, settings, self._ring_membership)
                        except Exception as exc:
                            ring_lookup_failed = True
                            if hard_continuity_lookup:
                                _record_continuity_fail_closed(
                                    surface="http_bridge",
                                    reason="owner_metadata_unavailable",
                                    previous_response_id=previous_response_id,
                                    session_id=incoming_turn_state or incoming_session_key,
                                    upstream_error_code="owner_lookup_failed",
                                )
                                raise ProxyResponseError(
                                    502,
                                    _http_bridge_owner_lookup_unavailable_error_envelope(),
                                ) from exc
                            if _http_bridge_can_local_recover_without_ring(
                                key=key,
                                headers=headers,
                                previous_response_id=previous_response_id,
                                durable_lookup=durable_lookup,
                            ):
                                logger.warning(
                                    "Bridge owner lookup failed; allowing local recovery path",
                                    exc_info=True,
                                )
                                owner_instance = settings.http_responses_session_bridge_instance_id
                            else:
                                raise
                    try:
                        current_instance, ring = await _active_http_bridge_instance_ring(
                            settings, self._ring_membership
                        )
                    except Exception as exc:
                        if hard_continuity_lookup:
                            _record_continuity_fail_closed(
                                surface="http_bridge",
                                reason="owner_metadata_unavailable",
                                previous_response_id=previous_response_id,
                                session_id=incoming_turn_state or incoming_session_key,
                                upstream_error_code="ring_lookup_failed",
                            )
                            raise ProxyResponseError(
                                502,
                                _http_bridge_owner_lookup_unavailable_error_envelope(),
                            ) from exc
                        if ring_lookup_failed or _http_bridge_can_local_recover_without_ring(
                            key=key,
                            headers=headers,
                            previous_response_id=previous_response_id,
                            durable_lookup=durable_lookup,
                        ):
                            logger.warning(
                                "Bridge ring lookup failed; falling back to local recovery ring", exc_info=True
                            )
                            current_instance = settings.http_responses_session_bridge_instance_id
                            ring = (current_instance,)
                        else:
                            raise
                    owner_mismatch = owner_instance is not None and owner_instance != current_instance
                    if owner_mismatch and (len(ring) > 1 or durable_lookup is not None):
                        if PROMETHEUS_AVAILABLE and bridge_owner_mismatch_total is not None:
                            bridge_owner_mismatch_total.labels(strength=_http_bridge_key_strength(key)).inc()
                        if (
                            owner_check_required
                            and not (previous_response_id is not None and allow_previous_response_recovery_rebind)
                            and not allow_bootstrap_owner_rebind
                        ):
                            _log_http_bridge_event(
                                "owner_mismatch",
                                key,
                                account_id=None,
                                model=request_model,
                                detail=(
                                    "expected_instance="
                                    f"{owner_instance}, current_instance={current_instance}, outcome=forward"
                                ),
                                cache_key_family=key.affinity_kind,
                                model_class=_extract_model_class(request_model) if request_model else None,
                                owner_check_applied=True,
                            )
                            if allow_forward_to_owner:
                                if forwarded_request:
                                    _log_http_bridge_event(
                                        "owner_mismatch_forward_loop",
                                        key,
                                        account_id=None,
                                        model=request_model,
                                        detail=(
                                            "expected_instance="
                                            f"{owner_instance}, current_instance={current_instance}, "
                                            "outcome=forward_loop_prevented"
                                        ),
                                        cache_key_family=key.affinity_kind,
                                        model_class=_extract_model_class(request_model) if request_model else None,
                                        owner_check_applied=True,
                                    )
                                    raise ProxyResponseError(
                                        503,
                                        openai_error(
                                            "bridge_forward_loop_prevented",
                                            (
                                                "HTTP bridge request was forwarded back to a non-owner instance; "
                                                "refusing takeover to avoid a forward loop"
                                            ),
                                            error_type="server_error",
                                        ),
                                    )
                                elif self._ring_membership is None:
                                    if _http_bridge_has_durable_recovery_anchor(
                                        previous_response_id=previous_response_id,
                                        durable_lookup=durable_lookup,
                                    ):
                                        if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                            bridge_durable_recover_total.labels(path="owner_missing").inc()
                                        _log_http_bridge_event(
                                            "owner_mismatch_local_recover",
                                            key,
                                            account_id=None,
                                            model=request_model,
                                            detail=(
                                                "expected_instance="
                                                f"{owner_instance}, current_instance={current_instance}, "
                                                "outcome=local_recover_no_ring"
                                            ),
                                            cache_key_family=key.affinity_kind,
                                            model_class=_extract_model_class(request_model) if request_model else None,
                                            owner_check_applied=True,
                                        )
                                        force_durable_takeover = True
                                    elif _http_bridge_can_single_instance_owner_takeover_without_anchor(
                                        key=key,
                                        owner_instance=owner_instance,
                                        current_instance=current_instance,
                                        ring=ring,
                                    ):
                                        if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                            bridge_durable_recover_total.labels(path="restart_takeover").inc()
                                        _log_http_bridge_event(
                                            "owner_mismatch_local_recover",
                                            key,
                                            account_id=None,
                                            model=request_model,
                                            detail=(
                                                "expected_instance="
                                                f"{owner_instance}, current_instance={current_instance}, "
                                                "outcome=single_instance_takeover_no_anchor"
                                            ),
                                            cache_key_family=key.affinity_kind,
                                            model_class=_extract_model_class(request_model) if request_model else None,
                                            owner_check_applied=True,
                                        )
                                        force_durable_takeover = True
                                    else:
                                        _log_http_bridge_event(
                                            "owner_mismatch_local_recover",
                                            key,
                                            account_id=None,
                                            model=request_model,
                                            detail=(
                                                "expected_instance="
                                                f"{owner_instance}, current_instance={current_instance}, "
                                                "outcome=local_recover_no_ring"
                                            ),
                                            cache_key_family=key.affinity_kind,
                                            model_class=_extract_model_class(request_model) if request_model else None,
                                            owner_check_applied=True,
                                        )
                                        force_durable_takeover = True
                                else:
                                    assert owner_instance is not None
                                    owner_endpoint = await self._ring_membership.resolve_endpoint(owner_instance)
                                    if owner_endpoint is None:
                                        if _http_bridge_has_durable_recovery_anchor(
                                            previous_response_id=previous_response_id,
                                            durable_lookup=durable_lookup,
                                        ):
                                            if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                                bridge_durable_recover_total.labels(path="owner_missing").inc()
                                            _log_http_bridge_event(
                                                "owner_endpoint_missing_local_recover",
                                                key,
                                                account_id=None,
                                                model=request_model,
                                                detail=(
                                                    "expected_instance="
                                                    f"{owner_instance}, current_instance={current_instance}, "
                                                    "outcome=local_recover"
                                                ),
                                                cache_key_family=key.affinity_kind,
                                                model_class=_extract_model_class(request_model)
                                                if request_model
                                                else None,
                                                owner_check_applied=True,
                                            )
                                            force_durable_takeover = True
                                        else:
                                            _log_http_bridge_event(
                                                "owner_mismatch_local_recover",
                                                key,
                                                account_id=None,
                                                model=request_model,
                                                detail=(
                                                    "expected_instance="
                                                    f"{owner_instance}, current_instance={current_instance}, "
                                                    "outcome=local_recover_no_endpoint"
                                                ),
                                                cache_key_family=key.affinity_kind,
                                                model_class=_extract_model_class(request_model)
                                                if request_model
                                                else None,
                                                owner_check_applied=True,
                                            )
                                            force_durable_takeover = True
                                    else:
                                        owner_forward = _HTTPBridgeOwnerForward(
                                            owner_instance=owner_instance,
                                            owner_endpoint=owner_endpoint,
                                            key=key,
                                        )
                            else:
                                if _http_bridge_has_durable_recovery_anchor(
                                    previous_response_id=previous_response_id,
                                    durable_lookup=durable_lookup,
                                ):
                                    if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                        bridge_durable_recover_total.labels(path="owner_missing").inc()
                                    _log_http_bridge_event(
                                        "owner_mismatch_local_recover",
                                        key,
                                        account_id=None,
                                        model=request_model,
                                        detail=(
                                            "expected_instance="
                                            f"{owner_instance}, current_instance={current_instance}, "
                                            "outcome=local_recover"
                                        ),
                                        cache_key_family=key.affinity_kind,
                                        model_class=_extract_model_class(request_model) if request_model else None,
                                        owner_check_applied=True,
                                    )
                                    force_durable_takeover = True
                                else:
                                    _log_http_bridge_event(
                                        "owner_mismatch_local_recover",
                                        key,
                                        account_id=None,
                                        model=request_model,
                                        detail=(
                                            "expected_instance="
                                            f"{owner_instance}, current_instance={current_instance}, "
                                            "outcome=local_recover_no_forward"
                                        ),
                                        cache_key_family=key.affinity_kind,
                                        model_class=_extract_model_class(request_model) if request_model else None,
                                        owner_check_applied=True,
                                    )
                                    force_durable_takeover = True
                        else:
                            _log_http_bridge_event(
                                "prompt_cache_locality_miss",
                                key,
                                account_id=None,
                                model=request_model,
                                detail=(
                                    "expected_instance="
                                    f"{owner_instance}, current_instance={current_instance}, "
                                    "outcome=local_rebind"
                                ),
                                cache_key_family=key.affinity_kind,
                                model_class=_extract_model_class(request_model) if request_model else None,
                                owner_check_applied=False,
                            )
                            if _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor(
                                key=key,
                                owner_instance=owner_instance,
                                current_instance=current_instance,
                                ring=ring,
                            ):
                                force_durable_takeover = True
                            elif allow_previous_response_recovery_rebind or allow_bootstrap_owner_rebind:
                                force_durable_takeover = True
                            _log_http_bridge_event(
                                "soft_locality_rebind",
                                key,
                                account_id=None,
                                model=request_model,
                                detail=(
                                    "expected_instance="
                                    f"{owner_instance}, current_instance={current_instance}, outcome=local_rebind"
                                ),
                                cache_key_family=key.affinity_kind,
                                model_class=_extract_model_class(request_model) if request_model else None,
                                owner_check_applied=False,
                            )
                            if PROMETHEUS_AVAILABLE:
                                if bridge_prompt_cache_locality_miss_total is not None:
                                    bridge_prompt_cache_locality_miss_total.inc()
                                if bridge_soft_local_rebind_total is not None:
                                    bridge_soft_local_rebind_total.inc()
                                if bridge_local_rebind_total is not None:
                                    bridge_local_rebind_total.labels(reason="prompt_cache_locality_miss").inc()

                if existing is not None:
                    old_account_id = existing.account.id
                    _log_http_bridge_event(
                        "discard_stale",
                        key,
                        account_id=existing.account.id,
                        model=existing.request_model,
                        cache_key_family=key.affinity_kind,
                        model_class=_extract_model_class(existing.request_model) if existing.request_model else None,
                    )
                    self._http_bridge_sessions.pop(key, None)
                    sessions_to_close.append(existing)

                if owner_mismatch_error is None:
                    inflight_future = self._http_bridge_inflight_sessions.get(key)
                    if (
                        previous_response_id is not None
                        and inflight_future is None
                        and (existing is None or existing.closed or existing.account.status != AccountStatus.ACTIVE)
                    ):
                        previous_alias_key = _http_bridge_previous_response_alias_key(previous_response_id, api_key_id)
                        previous_key = self._http_bridge_previous_response_index.get(previous_alias_key)
                        if previous_key is not None:
                            previous_session = self._http_bridge_sessions.get(previous_key)
                            if (
                                previous_session is not None
                                and not previous_session.closed
                                and previous_session.account.status == AccountStatus.ACTIVE
                            ):
                                key = previous_session.key
                                existing = previous_session
                                inflight_future = self._http_bridge_inflight_sessions.get(previous_key)
                                if incoming_turn_state:
                                    self._promote_http_bridge_session_to_codex_affinity(
                                        previous_session,
                                        turn_state=incoming_turn_state,
                                        settings=settings,
                                    )
                                    previous_session.downstream_turn_state_aliases.add(incoming_turn_state)
                                    for alias in previous_session.downstream_turn_state_aliases:
                                        self._http_bridge_turn_state_index[
                                            _http_bridge_turn_state_alias_key(
                                                alias,
                                                previous_session.key.api_key_id,
                                            )
                                        ] = previous_session.key
                                if inflight_future is None:
                                    previous_session.request_model = request_model
                                    previous_session.last_used_at = time.monotonic()
                                    await self._refresh_durable_http_bridge_session(previous_session)
                                    _log_http_bridge_event(
                                        "reuse",
                                        key,
                                        account_id=previous_session.account.id,
                                        model=previous_session.request_model,
                                        pending_count=await self._http_bridge_pending_count(previous_session),
                                        cache_key_family=key.affinity_kind,
                                        model_class=_extract_model_class(previous_session.request_model)
                                        if previous_session.request_model
                                        else None,
                                    )
                                    return previous_session
                            else:
                                self._http_bridge_previous_response_index.pop(previous_alias_key, None)
                    if (
                        previous_response_id is not None
                        and not used_session_header_fallback
                        and not allow_previous_response_recovery_rebind
                        and durable_lookup is None
                    ):
                        _record_continuity_fail_closed(
                            surface="http_bridge",
                            reason="continuity_lost",
                            previous_response_id=previous_response_id,
                            session_id=incoming_turn_state or incoming_session_key,
                        )
                        continuity_error = ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())
                    elif missing_turn_state_alias and inflight_future is None and durable_lookup is None:
                        turn_state_scope_conflict = incoming_turn_state is not None and any(
                            alias == incoming_turn_state and alias_api_key != api_key_id
                            for alias, alias_api_key in self._http_bridge_turn_state_index
                        )
                        if turn_state_scope_conflict:
                            _record_continuity_fail_closed(
                                surface="http_bridge",
                                reason="turn_state_scope_conflict",
                                previous_response_id=previous_response_id,
                                session_id=incoming_turn_state,
                            )
                            continuity_error = ProxyResponseError(
                                409,
                                openai_error(
                                    "bridge_instance_mismatch",
                                    "HTTP bridge turn-state is bound to a different API key scope",
                                    error_type="server_error",
                                ),
                            )
                        elif (
                            incoming_turn_state is not None
                            and incoming_turn_state.startswith("http_turn_")
                            and not allow_forward_to_owner
                        ):
                            _record_continuity_fail_closed(
                                surface="http_bridge",
                                reason="generated_turn_state_continuity_lost",
                                previous_response_id=previous_response_id,
                                session_id=incoming_turn_state,
                            )
                            continuity_error = ProxyResponseError(
                                409,
                                openai_error(
                                    "bridge_instance_mismatch",
                                    "HTTP bridge continuity was lost for generated turn-state",
                                    error_type="server_error",
                                ),
                            )
                        else:
                            _log_http_bridge_event(
                                "turn_state_alias_miss_local_rebind",
                                key,
                                account_id=None,
                                model=request_model,
                                detail="outcome=local_rebind_without_alias",
                                cache_key_family=key.affinity_kind,
                                model_class=_extract_model_class(request_model) if request_model else None,
                                owner_check_applied=owner_check_required,
                            )
                    elif inflight_future is None:
                        while (
                            len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= max_sessions
                            and self._http_bridge_sessions
                        ):
                            evictable_sessions: list[tuple[_HTTPBridgeSessionKey, _HTTPBridgeSession]] = []
                            for candidate_key, candidate_session in self._http_bridge_sessions.items():
                                pending_count = await self._http_bridge_pending_count(candidate_session)
                                if pending_count:
                                    continue
                                evictable_sessions.append((candidate_key, candidate_session))
                            if not evictable_sessions:
                                break
                            lru_key, lru_session = min(
                                evictable_sessions,
                                key=lambda item: _http_bridge_eviction_priority(item[1]),
                            )
                            _log_http_bridge_event(
                                "evict_lru",
                                lru_key,
                                account_id=lru_session.account.id,
                                model=lru_session.request_model,
                                cache_key_family=lru_key.affinity_kind,
                                model_class=_extract_model_class(lru_session.request_model)
                                if lru_session.request_model
                                else None,
                            )
                            self._http_bridge_sessions.pop(lru_key, None)
                            sessions_to_close.append(lru_session)
                        if len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= max_sessions:
                            if self._http_bridge_inflight_sessions:
                                capacity_wait_future = next(iter(self._http_bridge_inflight_sessions.values()))
                            else:
                                _log_http_bridge_event(
                                    "capacity_exhausted_active_sessions",
                                    key,
                                    account_id=None,
                                    model=request_model,
                                    pending_count=(
                                        len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions)
                                    ),
                                    cache_key_family=key.affinity_kind,
                                    model_class=_extract_model_class(request_model) if request_model else None,
                                )
                                raise ProxyResponseError(
                                    429,
                                    openai_error(
                                        "rate_limit_exceeded",
                                        "HTTP responses session bridge has no idle capacity",
                                        error_type="rate_limit_error",
                                    ),
                                )
                        else:
                            inflight_future = asyncio.get_running_loop().create_future()
                            self._http_bridge_inflight_sessions[key] = inflight_future
                            owns_creation = True

            for stale_session in sessions_to_close:
                await self._close_http_bridge_session(stale_session)

            if owner_forward is not None:
                return owner_forward

            if owner_mismatch_error is not None:
                raise owner_mismatch_error

            if continuity_error is not None:
                raise continuity_error

            if capacity_wait_future is not None:
                try:
                    await asyncio.shield(capacity_wait_future)
                except asyncio.CancelledError:
                    if capacity_wait_future.cancelled():
                        continue
                    raise
                except ProxyResponseError:
                    raise
                except Exception:
                    pass
                continue

            if inflight_future is not None and not owns_creation:
                try:
                    session = await asyncio.shield(inflight_future)
                except asyncio.CancelledError:
                    if inflight_future.cancelled():
                        continue
                    raise
                except Exception:
                    raise
                if session is None:
                    continue
                if (
                    not session.closed
                    and session.account.status == AccountStatus.ACTIVE
                    and _http_bridge_session_allows_api_key(session, api_key)
                    and _http_bridge_session_reusable_for_request(
                        session=session,
                        key=key,
                        incoming_turn_state=incoming_turn_state,
                        previous_response_id=previous_response_id,
                    )
                    and _http_bridge_session_matches_preferred_account(
                        session=session,
                        previous_response_id=previous_response_id,
                        preferred_account_id=preferred_account_id,
                    )
                ):
                    current_instance = settings.http_responses_session_bridge_instance_id
                    if _durable_bridge_lookup_allows_local_reuse(durable_lookup, current_instance=current_instance):
                        session.api_key = api_key
                        session.request_model = request_model
                        session.last_used_at = time.monotonic()
                        return session
                if not session.closed and session.account.status == AccountStatus.ACTIVE:
                    old_account_id = session.account.id
                    async with self._http_bridge_lock:
                        if self._http_bridge_sessions.get(key) is session:
                            self._http_bridge_sessions.pop(key, None)
                        self._unregister_http_bridge_turn_states_locked(session)
                    session.closed = True
                    await self._close_http_bridge_session(session)
                continue

            created_session: _HTTPBridgeSession | None = None
            session_registered = False
            require_preferred_account = previous_response_id is not None and preferred_account_id is not None
            try:
                created_session = await self._create_http_bridge_session_compatible(
                    key,
                    headers=headers,
                    affinity=affinity,
                    api_key=api_key,
                    request_model=request_model,
                    idle_ttl_seconds=effective_idle_ttl_seconds,
                    request_stage=request_stage,
                    preferred_account_id=preferred_account_id,
                    require_preferred_account=require_preferred_account,
                )
                await self._claim_durable_http_bridge_session(
                    created_session,
                    allow_takeover=force_durable_takeover or _http_bridge_allow_durable_takeover(durable_lookup),
                )
                async with self._http_bridge_lock:
                    current_future = self._http_bridge_inflight_sessions.get(key)
                    if current_future is inflight_future:
                        self._http_bridge_inflight_sessions.pop(key, None)
                        self._http_bridge_sessions[key] = created_session
                        session_registered = True
                        if inflight_future is not None and not inflight_future.done():
                            inflight_future.set_result(created_session)
            except BaseException as exc:
                async with self._http_bridge_lock:
                    current_future = self._http_bridge_inflight_sessions.get(key)
                    if current_future is inflight_future:
                        self._http_bridge_inflight_sessions.pop(key, None)
                        if inflight_future is not None and not inflight_future.done():
                            if isinstance(exc, asyncio.CancelledError):
                                inflight_future.cancel()
                            else:
                                inflight_future.set_exception(exc)
                                inflight_future.exception()
                if created_session is not None and not session_registered:
                    await self._close_http_bridge_session(created_session)
                raise
            assert created_session is not None
            _log_http_bridge_event(
                "create",
                key,
                account_id=created_session.account.id,
                model=created_session.request_model,
                detail=(
                    f"request_stage={request_stage}, preferred_account_id={preferred_account_id}, "
                    f"selected_account_id={created_session.account.id}, "
                    f"durable_session_id={created_session.durable_session_id}"
                ),
                cache_key_family=key.affinity_kind,
                model_class=_extract_model_class(created_session.request_model)
                if created_session.request_model
                else None,
            )
            if old_account_id is not None and old_account_id != created_session.account.id:
                _log_http_bridge_event(
                    "reallocation_orphan",
                    key,
                    account_id=created_session.account.id,
                    model=created_session.request_model,
                    detail=f"old_account={old_account_id}",
                    cache_key_family=key.affinity_kind,
                    model_class=_extract_model_class(created_session.request_model)
                    if created_session.request_model
                    else None,
                )
            return created_session

    async def close_all_http_bridge_sessions(self) -> None:
        async with self._http_bridge_lock:
            sessions_to_close = list(self._http_bridge_sessions.values())
            inflight_futures = list(self._http_bridge_inflight_sessions.values())
            self._http_bridge_sessions.clear()
            self._http_bridge_inflight_sessions.clear()
            self._http_bridge_previous_response_index.clear()

        shutdown_error = ProxyResponseError(
            503,
            openai_error(
                "upstream_unavailable",
                "HTTP responses session bridge is shutting down",
                error_type="server_error",
            ),
        )
        for inflight_future in inflight_futures:
            if inflight_future.done():
                continue
            inflight_future.set_exception(shutdown_error)
            inflight_future.exception()

        for session in sessions_to_close:
            await self._close_http_bridge_session(session)

    async def mark_http_bridge_draining(self) -> None:
        try:
            await self._durable_bridge.mark_instance_draining(
                instance_id=get_settings().http_responses_session_bridge_instance_id,
            )
        except Exception:
            logger.warning("Failed to mark durable HTTP bridge sessions draining", exc_info=True)

    async def _prune_http_bridge_sessions_locked(self) -> None:
        now = time.monotonic()
        stale_keys: list[_HTTPBridgeSessionKey] = []
        for key, session in self._http_bridge_sessions.items():
            if session.closed:
                stale_keys.append(key)
                continue
            pending_count = await self._http_bridge_pending_count(session)
            if pending_count:
                continue
            if now - session.last_used_at < session.idle_ttl_seconds:
                continue
            stale_keys.append(key)
        for key in stale_keys:
            session = self._http_bridge_sessions.pop(key, None)
            if session is not None:
                _log_http_bridge_event(
                    "evict_idle",
                    key,
                    account_id=session.account.id,
                    model=session.request_model,
                    cache_key_family=key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                await self._close_http_bridge_session(session, turn_state_lock_held=True)

    async def _close_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        turn_state_lock_held: bool = False,
    ) -> None:
        session.closed = True
        if turn_state_lock_held:
            self._unregister_http_bridge_turn_states_locked(session)
            self._unregister_http_bridge_previous_response_ids_locked(session)
        else:
            await self._unregister_http_bridge_turn_states(session)
            await self._unregister_http_bridge_previous_response_ids(session)
        if session.upstream_reader is not None:
            await _await_cancelled_task(session.upstream_reader, label="http bridge upstream reader")
        try:
            await session.upstream.close()
        except Exception:
            logger.debug("Failed to close HTTP bridge upstream websocket", exc_info=True)
        pending_requests = getattr(session, "pending_requests", None)
        pending_lock = getattr(session, "pending_lock", None)
        response_create_gate = getattr(session, "response_create_gate", None)
        if pending_requests is not None and pending_lock is not None:
            async with pending_lock:
                session.queued_request_count = 0
            await self._fail_pending_websocket_requests(
                account_id_value=session.account.id,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="stream_incomplete",
                error_message="HTTP bridge session closed before response.completed",
                api_key=None,
                response_create_gate=response_create_gate,
            )
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.release_live_session(
                    session_id=session.durable_session_id,
                    instance_id=get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    draining=shutdown_state.is_bridge_drain_active(),
                )
            except Exception:
                logger.warning("Failed to release durable HTTP bridge session", exc_info=True)
        _log_http_bridge_event(
            "close",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )

    async def _register_http_bridge_turn_state(self, session: "_HTTPBridgeSession", turn_state: str) -> None:
        async with self._http_bridge_lock:
            if session.closed:
                return
            session.downstream_turn_state_aliases.add(turn_state)
            if session.downstream_turn_state is None:
                session.downstream_turn_state = turn_state
            for alias in session.downstream_turn_state_aliases:
                self._http_bridge_turn_state_index[_http_bridge_turn_state_alias_key(alias, session.key.api_key_id)] = (
                    session.key
                )
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.register_turn_state(
                    session_id=session.durable_session_id,
                    api_key_id=session.key.api_key_id,
                    instance_id=get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    turn_state=turn_state,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                )
            except Exception:
                logger.warning("Failed to persist durable HTTP bridge turn-state alias", exc_info=True)

    async def _register_http_bridge_previous_response_id(
        self,
        session: "_HTTPBridgeSession",
        response_id: str,
    ) -> None:
        stripped_response_id = response_id.strip()
        if not stripped_response_id:
            return
        async with self._http_bridge_lock:
            if session.closed:
                return
            alias_key = _http_bridge_previous_response_alias_key(stripped_response_id, session.key.api_key_id)
            self._http_bridge_previous_response_index[alias_key] = session.key
            session.previous_response_ids.add(stripped_response_id)
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.register_previous_response_id(
                    session_id=session.durable_session_id,
                    api_key_id=session.key.api_key_id,
                    instance_id=get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    response_id=stripped_response_id,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                )
            except Exception:
                logger.warning("Failed to persist durable HTTP bridge previous_response_id alias", exc_info=True)

    async def _unregister_http_bridge_turn_states(self, session: "_HTTPBridgeSession") -> None:
        async with self._http_bridge_lock:
            self._unregister_http_bridge_turn_states_locked(session)

    async def _unregister_http_bridge_previous_response_ids(self, session: "_HTTPBridgeSession") -> None:
        async with self._http_bridge_lock:
            self._unregister_http_bridge_previous_response_ids_locked(session)

    def _unregister_http_bridge_turn_states_locked(self, session: "_HTTPBridgeSession") -> None:
        aliases = tuple(session.downstream_turn_state_aliases)
        for alias in aliases:
            self._http_bridge_turn_state_index.pop(
                _http_bridge_turn_state_alias_key(alias, session.key.api_key_id),
                None,
            )
        session.downstream_turn_state_aliases.clear()

    def _unregister_http_bridge_previous_response_ids_locked(self, session: "_HTTPBridgeSession") -> None:
        response_ids = tuple(session.previous_response_ids)
        for response_id in response_ids:
            self._http_bridge_previous_response_index.pop(
                _http_bridge_previous_response_alias_key(response_id, session.key.api_key_id),
                None,
            )
        session.previous_response_ids.clear()

    def _promote_http_bridge_session_to_codex_affinity(
        self,
        session: "_HTTPBridgeSession",
        *,
        turn_state: str,
        settings: Settings,
    ) -> None:
        session.affinity = _AffinityPolicy(key=turn_state, kind=StickySessionKind.CODEX_SESSION)
        session.codex_session = True
        session.downstream_turn_state = turn_state
        session.downstream_turn_state_aliases.add(turn_state)
        session.idle_ttl_seconds = max(
            session.idle_ttl_seconds,
            float(settings.http_responses_session_bridge_codex_idle_ttl_seconds),
        )
        session.headers = _headers_with_turn_state(session.headers, turn_state)

    async def _claim_durable_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        allow_takeover: bool,
    ) -> None:
        current_instance = get_settings().http_responses_session_bridge_instance_id
        try:
            lookup = await self._durable_bridge.claim_live_session(
                session_key_kind=session.key.affinity_kind,
                session_key_value=session.key.affinity_key,
                api_key_id=session.key.api_key_id,
                instance_id=current_instance,
                lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                account_id=session.account.id,
                model=session.request_model,
                service_tier=None,
                latest_turn_state=session.downstream_turn_state,
                latest_response_id=None,
                allow_takeover=allow_takeover,
            )
            if lookup.owner_instance_id != current_instance:
                _log_http_bridge_event(
                    "owner_mismatch_retry",
                    session.key,
                    account_id=None,
                    model=session.request_model,
                    detail=(
                        "expected_instance="
                        f"{lookup.owner_instance_id}, current_instance={current_instance}, outcome=claim_rejected"
                    ),
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    owner_check_applied=True,
                )
                if PROMETHEUS_AVAILABLE and bridge_instance_mismatch_total is not None:
                    bridge_instance_mismatch_total.labels(outcome="retry").inc()
                raise ProxyResponseError(
                    409,
                    openai_error(
                        "bridge_instance_mismatch",
                        "HTTP bridge session is owned by a different instance; retry to reach the correct replica",
                        error_type="server_error",
                    ),
                )
            session.durable_session_id = lookup.session_id
            session.durable_owner_epoch = lookup.owner_epoch
            session.headers = _headers_with_turn_state(session.headers, session.downstream_turn_state)
            if (
                PROMETHEUS_AVAILABLE
                and bridge_durable_recover_total is not None
                and allow_takeover
                and lookup.owner_epoch > 1
            ):
                bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _record_bridge_reattach(path="restart_takeover", outcome="success")
            if session.key.affinity_kind == "session_header":
                await self._durable_bridge.register_session_header(
                    session_id=lookup.session_id,
                    api_key_id=session.key.api_key_id,
                    session_header=session.key.affinity_key,
                )
        except Exception as exc:
            if _is_missing_durable_bridge_table_error(exc):
                logger.warning("Durable bridge tables missing; using in-memory bridge session fallback", exc_info=True)
                return
            raise

    async def _refresh_durable_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
    ) -> None:
        if session.durable_session_id is None or session.durable_owner_epoch is None:
            return
        try:
            lookup = await self._durable_bridge.renew_live_session(
                session_id=session.durable_session_id,
                api_key_id=session.key.api_key_id,
                instance_id=get_settings().http_responses_session_bridge_instance_id,
                owner_epoch=session.durable_owner_epoch,
                lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                latest_turn_state=session.downstream_turn_state,
                latest_response_id=None,
            )
            if lookup is not None:
                session.durable_owner_epoch = lookup.owner_epoch
        except Exception:
            logger.warning("Failed to renew durable HTTP bridge session lease", exc_info=True)

    async def _create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        require_preferred_account: bool = False,
    ) -> "_HTTPBridgeSession":
        request_state = _WebSocketRequestState(
            request_id=f"http_bridge_connect_{uuid4().hex}",
            model=request_model,
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=time.monotonic(),
            transport=_REQUEST_TRANSPORT_HTTP,
        )
        deadline = _websocket_connect_deadline(request_state, get_settings().proxy_request_budget_seconds)
        settings = await get_settings_cache().get()
        excluded_account_ids: set[str] = set()
        retry_same_account_once = preferred_account_id is not None
        preferred_candidate_id = preferred_account_id
        while True:
            selection = await self._select_account_with_budget_compatible(
                deadline,
                request_id=request_state.request_log_id or request_state.request_id,
                kind="http_bridge",
                request_stage=request_stage,
                api_key=api_key,
                sticky_key=affinity.key,
                sticky_kind=affinity.kind,
                reallocate_sticky=affinity.reallocate_sticky,
                sticky_max_age_seconds=affinity.max_age_seconds,
                sticky_budget_reallocation_enabled=affinity.budget_reallocation_enabled,
                prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                routing_strategy=_routing_strategy(settings),
                model=request_model,
                exclude_account_ids=excluded_account_ids,
                preferred_account_id=preferred_candidate_id,
            )
            account = selection.account
            if account is None:
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=None,
                )
                raise ProxyResponseError(
                    503,
                    openai_error(
                        selection.error_code or "no_accounts",
                        selection.error_message or "No active accounts available",
                        error_type="server_error",
                    ),
                )
            if require_preferred_account and preferred_account_id is not None and account.id != preferred_account_id:
                message = "Previous response owner account is unavailable; retry later."
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "upstream_unavailable",
                        message,
                        error_type="server_error",
                    ),
                )
            selected_is_preferred = preferred_account_id is not None and account.id == preferred_account_id
            try:
                account = await self._ensure_fresh_with_budget(
                    account,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                connect_headers = _headers_with_turn_state(headers, _sticky_key_from_turn_state_header(headers))
                upstream = await self._open_upstream_websocket_with_budget(
                    account,
                    connect_headers,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                break
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once and not exc.is_permanent:
                        retry_same_account_once = False
                        continue
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    continue
                if exc.is_permanent:
                    raise ProxyResponseError(
                        401,
                        openai_error(
                            "invalid_api_key",
                            exc.message,
                            error_type="authentication_error",
                        ),
                    ) from exc
                if request_stage == "first_turn":
                    _record_bridge_first_turn_timeout()
                _raise_proxy_unavailable(exc.message or "Temporary upstream refresh failure")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        continue
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    continue
                if request_stage == "first_turn":
                    _record_bridge_first_turn_timeout()
                _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")
        session = _HTTPBridgeSession(
            key=key,
            headers=connect_headers,
            affinity=affinity,
            api_key=api_key,
            request_model=request_model,
            account=account,
            upstream=upstream,
            upstream_control=_WebSocketUpstreamControl(),
            pending_requests=deque(),
            pending_lock=anyio.Lock(),
            response_create_gate=asyncio.Semaphore(1),
            queued_request_count=0,
            last_used_at=time.monotonic(),
            idle_ttl_seconds=idle_ttl_seconds,
            codex_session=affinity.kind == StickySessionKind.CODEX_SESSION,
            prewarm_lock=anyio.Lock(),
            upstream_turn_state=_upstream_turn_state_from_socket(upstream),
            downstream_turn_state=None,
        )
        session.upstream_reader = asyncio.create_task(self._relay_http_bridge_upstream_messages(session))
        return session

    async def _submit_http_bridge_request(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        if session.closed:
            # Try reconnecting the upstream websocket first.  For requests
            # carrying previous_response_id we only reconnect (send_request=
            # False) because the fresh upstream won't recognise the old
            # response id.  If reconnection itself fails, raise 502 so the
            # client retries with previous_response_id intact rather than
            # receiving 400 previous_response_not_found (which causes the
            # CLI to drop previous_response_id and resend the full
            # conversation history, inflating per-turn context by ~20x).
            recovered = await self._retry_http_bridge_request_on_fresh_upstream(
                session,
                request_state=request_state,
                text_data=text_data,
                send_request=False,
            )
            if recovered:
                session.closed = False
            else:
                _log_http_bridge_event(
                    "submit_on_closed",
                    session.key,
                    account_id=session.account.id,
                    model=session.request_model,
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_unavailable", "HTTP responses session bridge is closed"),
                )
        await self._maybe_prewarm_http_bridge_session(
            session,
            request_state=request_state,
            text_data=text_data,
        )
        gate_acquired = False
        request_enqueued = False
        async with session.pending_lock:
            if session.queued_request_count >= queue_limit:
                _log_http_bridge_event(
                    "queue_full",
                    session.key,
                    account_id=session.account.id,
                    model=session.request_model,
                    pending_count=session.queued_request_count,
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                raise ProxyResponseError(
                    429,
                    openai_error(
                        "rate_limit_exceeded",
                        "HTTP responses session bridge queue is full",
                        error_type="rate_limit_error",
                    ),
                )
            session.queued_request_count += 1
        try:
            await self._acquire_request_state_response_create_admission(
                request_state,
                response_create_gate=session.response_create_gate,
            )
            gate_acquired = True
            async with session.pending_lock:
                session.pending_requests.append(request_state)
            request_enqueued = True
            await session.upstream.send_text(text_data)
            session.last_used_at = time.monotonic()
        except ProxyResponseError:
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
            )
            raise
        except asyncio.CancelledError:
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
                release_reservation=True,
            )
            raise
        except Exception as exc:
            _log_http_bridge_event(
                "send_failure",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail=str(exc) or None,
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )
            retried = await self._retry_http_bridge_request_on_fresh_upstream(
                session,
                request_state=request_state,
                text_data=text_data,
            )
            if retried:
                return
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
            )
            await self._fail_pending_websocket_requests(
                account_id_value=session.account.id,
                pending_requests=deque([request_state]),
                pending_lock=anyio.Lock(),
                error_code="stream_incomplete",
                error_message="Upstream websocket closed before response.completed",
                api_key=None,
                response_create_gate=session.response_create_gate,
            )
            session.closed = True
            try:
                await session.upstream.close()
            except Exception:
                logger.debug("Failed to close HTTP bridge upstream websocket after send failure", exc_info=True)
            # Always raise 502 so the client can retry with
            # previous_response_id intact.  Returning 400
            # previous_response_not_found causes the client to drop
            # previous_response_id and resend the full conversation
            # history, inflating per-turn context by ~20x.
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", str(exc) or "Upstream websocket closed"),
            ) from exc

    async def _maybe_prewarm_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
    ) -> None:
        if (
            not session.codex_session
            or session.prewarmed
            or request_state.previous_response_id is not None
            or not getattr(get_settings(), "http_responses_session_bridge_codex_prewarm_enabled", False)
        ):
            return
        prewarm_lock = session.prewarm_lock
        if prewarm_lock is None:
            return
        async with prewarm_lock:
            if session.prewarmed:
                return
            warmup_text = _build_http_bridge_prewarm_text(text_data)
            session.prewarmed = True
            if warmup_text is None:
                return

            warmup_state = _WebSocketRequestState(
                request_id=f"http_prewarm_{uuid4().hex}",
                model=request_state.model,
                service_tier=request_state.service_tier,
                reasoning_effort=request_state.reasoning_effort,
                api_key_reservation=None,
                started_at=time.monotonic(),
                requested_service_tier=request_state.requested_service_tier,
                actual_service_tier=request_state.actual_service_tier,
                awaiting_response_created=True,
                event_queue=asyncio.Queue(),
                transport=_REQUEST_TRANSPORT_HTTP,
                request_text=warmup_text,
                skip_request_log=True,
            )
            gate_acquired = False
            request_enqueued = False
            try:
                event_queue = warmup_state.event_queue
                assert event_queue is not None
                await self._acquire_request_state_response_create_admission(
                    warmup_state,
                    response_create_gate=session.response_create_gate,
                )
                gate_acquired = True
                async with session.pending_lock:
                    session.pending_requests.append(warmup_state)
                request_enqueued = True
                await session.upstream.send_text(warmup_text)
                while True:
                    event_block = await event_queue.get()
                    if event_block is None:
                        break
                    payload = parse_sse_data_json(event_block)
                    event = parse_sse_event(event_block)
                    event_type = _event_type_from_payload(event, payload)
                    if event_type in {"response.failed", "response.incomplete", "error"}:
                        raise ProxyResponseError(
                            502,
                            openai_error(
                                "upstream_unavailable",
                                "HTTP responses session bridge prewarm failed",
                            ),
                        )
                session.last_used_at = time.monotonic()
            except ProxyResponseError as exc:
                error = _parse_openai_error(exc.payload)
                code = _normalize_error_code(error.code if error else None, error.type if error else None)
                await self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=warmup_state,
                    gate_acquired=gate_acquired,
                    request_enqueued=request_enqueued,
                )
                if is_local_overload_error_code(code):
                    session.prewarmed = False
                    return
                session.prewarmed = False
                raise
            except BaseException:
                session.prewarmed = False
                await self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=warmup_state,
                    gate_acquired=gate_acquired,
                    request_enqueued=request_enqueued,
                )
                raise

    async def _cleanup_http_bridge_submit_interruption(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        gate_acquired: bool,
        request_enqueued: bool,
        release_reservation: bool = False,
    ) -> None:
        async with session.pending_lock:
            if request_enqueued and request_state in session.pending_requests:
                session.pending_requests.remove(request_state)
            session.queued_request_count = max(0, session.queued_request_count - 1)
        if gate_acquired:
            _release_websocket_response_create_gate(request_state, session.response_create_gate)
        if release_reservation:
            await self._release_websocket_reservation(request_state.api_key_reservation)
            request_state.api_key_reservation = None

    async def _cleanup_unregistered_websocket_request(
        self,
        request_state: _WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
        gate_acquired: bool,
    ) -> None:
        if gate_acquired:
            request_state.response_create_gate = response_create_gate
            request_state.response_create_gate_acquired = True
            _release_websocket_response_create_gate(request_state, response_create_gate)
        await self._release_websocket_reservation(request_state.api_key_reservation)
        request_state.api_key_reservation = None

    async def _detach_http_bridge_request(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
    ) -> bool:
        removed = False
        async with session.pending_lock:
            if request_state in session.pending_requests:
                session.pending_requests.remove(request_state)
                session.queued_request_count = max(0, session.queued_request_count - 1)
                removed = True
        request_state.event_queue = None
        if not removed:
            return False
        _release_websocket_response_create_gate(request_state, session.response_create_gate)
        await self._release_websocket_reservation(request_state.api_key_reservation)
        request_state.api_key_reservation = None
        return True

    async def _relay_http_bridge_upstream_messages(
        self,
        session: "_HTTPBridgeSession",
    ) -> None:
        runtime_settings = get_settings()
        try:
            while True:
                receive_timeout = await self._next_websocket_receive_timeout(
                    session.pending_requests,
                    pending_lock=session.pending_lock,
                    proxy_request_budget_seconds=runtime_settings.proxy_request_budget_seconds,
                    stream_idle_timeout_seconds=runtime_settings.stream_idle_timeout_seconds,
                )
                try:
                    if receive_timeout is None:
                        message = await session.upstream.receive()
                    elif receive_timeout.timeout_seconds <= 0:
                        raise asyncio.TimeoutError()
                    else:
                        message = await asyncio.wait_for(
                            session.upstream.receive(),
                            timeout=receive_timeout.timeout_seconds,
                        )
                except asyncio.TimeoutError:
                    if receive_timeout is None:
                        raise
                    retried = await self._retry_http_bridge_precreated_request(session)
                    if retried:
                        continue
                    async with session.pending_lock:
                        session.queued_request_count = 0
                    await self._fail_pending_websocket_requests(
                        account_id_value=session.account.id,
                        pending_requests=session.pending_requests,
                        pending_lock=session.pending_lock,
                        error_code=receive_timeout.error_code,
                        error_message=receive_timeout.error_message,
                        api_key=None,
                        response_create_gate=session.response_create_gate,
                    )
                    session.closed = True
                    break

                if message.kind == "text" and message.text is not None:
                    await self._process_http_bridge_upstream_text(session, message.text)
                    continue

                retried = await self._retry_http_bridge_precreated_request(session)
                if retried:
                    continue
                async with session.pending_lock:
                    session.queued_request_count = 0
                await self._fail_pending_websocket_requests(
                    account_id_value=session.account.id,
                    pending_requests=session.pending_requests,
                    pending_lock=session.pending_lock,
                    error_code="stream_incomplete",
                    error_message=_upstream_websocket_disconnect_message(message),
                    api_key=None,
                    response_create_gate=session.response_create_gate,
                )
                session.closed = True
                break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "HTTP bridge upstream reader crashed account_id=%s bridge_kind=%s",
                session.account.id,
                session.key.affinity_kind,
                exc_info=True,
            )
            async with session.pending_lock:
                session.queued_request_count = 0
            await self._fail_pending_websocket_requests(
                account_id_value=session.account.id,
                pending_requests=session.pending_requests,
                pending_lock=session.pending_lock,
                error_code="stream_incomplete",
                error_message="HTTP bridge upstream reader crashed before response.completed",
                api_key=None,
                response_create_gate=session.response_create_gate,
            )
        finally:
            session.closed = True

    async def _retry_http_bridge_request_on_fresh_upstream(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        send_request: bool = True,
    ) -> bool:
        retry_text_data = text_data
        if request_state.previous_response_id is not None and send_request:
            # After an ambiguous websocket send failure we cannot prove whether
            # upstream already accepted the continuation. Re-sending the same
            # previous_response_id request can fork continuity with duplicate
            # child responses, so only reconnect-without-resend is allowed.
            # The single exception is proxy-injected anchors on trim-safe
            # full-resend payloads: dropping the anchor and replaying the
            # original unanchored request is equivalent to the client's own
            # retry. Session-level injections do not opt in because their
            # payload may depend on the anchor for context preservation.
            if (
                not request_state.proxy_injected_previous_response_id
                or not request_state.fresh_upstream_request_text
                or not request_state.fresh_upstream_request_is_retry_safe
            ):
                return False
            retry_text_data = request_state.fresh_upstream_request_text
        if request_state.replay_count >= 1:
            return False
        request_state.replay_count += 1
        _log_http_bridge_event(
            "retry_fresh_upstream",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=1,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(
                session,
                request_state=request_state,
                restart_reader=True,
            )
            if send_request:
                if retry_text_data != text_data:
                    request_state.previous_response_id = None
                    request_state.proxy_injected_previous_response_id = False
                    request_state.request_text = retry_text_data
                await session.upstream.send_text(retry_text_data)
            session.last_used_at = time.monotonic()
            return True
        except Exception:
            logger.warning("HTTP bridge retry on fresh upstream failed", exc_info=True)
            return False

    async def _retry_http_bridge_precreated_request(self, session: "_HTTPBridgeSession") -> bool:
        async with session.pending_lock:
            retryable_requests = [
                request_state
                for request_state in session.pending_requests
                if request_state.response_id is None
                and request_state.awaiting_response_created
                and bool(request_state.request_text)
            ]
            if len(retryable_requests) != 1:
                return False
            request_state = retryable_requests[0]
            if request_state.previous_response_id is not None:
                # Once a continuation is pending upstream, reconnecting without
                # replay cannot complete the current request, while replaying it
                # is unsafe without upstream idempotency guarantees.
                return False
            if request_state.replay_count >= 1:
                return False
            request_text = request_state.request_text
            assert isinstance(request_text, str)
            request_state.replay_count += 1
        _log_http_bridge_event(
            "retry_precreated",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=1,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(session, request_state=request_state)
            await session.upstream.send_text(request_text)
            session.last_used_at = time.monotonic()
            return True
        except Exception:
            logger.warning("HTTP bridge pre-created retry failed", exc_info=True)
            return False

    async def _reconnect_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        restart_reader: bool = False,
    ) -> None:
        old_account_id = session.account.id
        old_upstream = session.upstream
        old_reader = session.upstream_reader if restart_reader else None
        if old_reader is not None:
            if old_reader is not asyncio.current_task():
                cancelled = await _await_cancelled_task(old_reader, label="http bridge upstream reader")
                if not cancelled:
                    session.closed = True
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "upstream_unavailable",
                            "HTTP responses session bridge reader did not shut down cleanly",
                        ),
                    )
        try:
            await old_upstream.close()
        except Exception:
            logger.debug("Failed to close HTTP bridge upstream websocket before reconnect", exc_info=True)

        deadline = _websocket_connect_deadline(request_state, get_settings().proxy_request_budget_seconds)
        settings = await get_settings_cache().get()
        session.api_key = request_state.api_key
        excluded_account_ids: set[str] = set()
        retry_same_account_once = True
        preferred_candidate_id: str | None = session.account.id
        while True:
            selection = await self._select_account_with_budget_compatible(
                deadline,
                request_id=request_state.request_log_id or request_state.request_id,
                kind="http_bridge",
                request_stage="reattach",
                api_key=session.api_key,
                sticky_key=session.affinity.key,
                sticky_kind=session.affinity.kind,
                reallocate_sticky=session.affinity.reallocate_sticky,
                sticky_max_age_seconds=session.affinity.max_age_seconds,
                sticky_budget_reallocation_enabled=session.affinity.budget_reallocation_enabled,
                prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                routing_strategy=_routing_strategy(settings),
                model=session.request_model,
                exclude_account_ids=excluded_account_ids,
                preferred_account_id=preferred_candidate_id,
            )
            account = selection.account
            if account is None:
                _record_same_account_takeover(
                    preferred_account_id=session.account.id,
                    selected_account_id=None,
                )
                raise ProxyResponseError(
                    503,
                    openai_error(
                        selection.error_code or "no_accounts",
                        selection.error_message or "No active accounts available",
                        error_type="server_error",
                    ),
                )
            selected_is_preferred = account.id == session.account.id
            try:
                account = await self._ensure_fresh_with_budget(
                    account,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                connect_headers = _headers_with_turn_state(
                    session.headers,
                    _preferred_http_bridge_reconnect_turn_state(session),
                )
                upstream = await self._open_upstream_websocket_with_budget(
                    account,
                    connect_headers,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                _record_same_account_takeover(
                    preferred_account_id=session.account.id,
                    selected_account_id=account.id,
                )
                break
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once and not exc.is_permanent:
                        retry_same_account_once = False
                        continue
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    continue
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        continue
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    continue
                raise
        session.account = account
        session.headers = connect_headers
        session.upstream = upstream
        session.upstream_control = _WebSocketUpstreamControl()
        session.closed = False
        session.upstream_turn_state = _upstream_turn_state_from_socket(upstream) or session.upstream_turn_state
        if restart_reader:
            session.upstream_reader = asyncio.create_task(self._relay_http_bridge_upstream_messages(session))
        _log_http_bridge_event(
            "reconnect",
            session.key,
            account_id=account.id,
            model=session.request_model,
            detail=(
                f"request_stage=reattach, previous_account={old_account_id}, "
                f"preferred_account_id={old_account_id}, selected_account_id={account.id}, "
                f"durable_session_id={session.durable_session_id}"
            ),
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )

    async def _process_http_bridge_upstream_text(
        self,
        session: "_HTTPBridgeSession",
        text: str,
    ) -> None:
        event_block = f"data: {text}\n\n"
        payload = parse_sse_data_json(event_block)
        event = parse_sse_event(event_block)
        event_type = _event_type_from_payload(event, payload)
        response_id = _websocket_response_id(event, payload)
        error_message = _websocket_event_error_message(event_type, payload)
        is_previous_response_not_found_event = _is_previous_response_not_found_error(
            code=_normalize_error_code(
                _websocket_event_error_code(event_type, payload),
                _websocket_event_error_type(event_type, payload),
            ),
            param=_websocket_event_error_param(event_type, payload),
            message=error_message,
        )
        previous_response_id_hint = _previous_response_id_from_not_found_message(error_message)

        async with session.pending_lock:
            matched_request_state = None
            created_request_state = None
            has_other_pending_requests = False
            grouped_previous_response_request_states: list[_WebSocketRequestState] = []
            if event_type == "response.created":
                matched_request_state = _assign_websocket_response_id(session.pending_requests, response_id)
                created_request_state = matched_request_state
                release_create_gate = matched_request_state is not None
            elif response_id is not None:
                matched_request_state = _find_websocket_request_state_by_response_id(
                    session.pending_requests,
                    response_id,
                )
                release_create_gate = False
            elif response_id is None:
                matched_request_state = _match_websocket_request_state_for_anonymous_event(
                    session.pending_requests,
                    prefer_previous_response_not_found=is_previous_response_not_found_event,
                    previous_response_id_hint=previous_response_id_hint,
                    error_message=error_message,
                )
                release_create_gate = False
            else:
                release_create_gate = False

            if matched_request_state is not None:
                actual_service_tier = _service_tier_from_event_payload(payload)
                if actual_service_tier is not None:
                    matched_request_state.actual_service_tier = actual_service_tier
                    matched_request_state.service_tier = actual_service_tier

            terminal_request_state = None
            if event_type in {"response.completed", "response.failed", "response.incomplete", "error"}:
                terminal_request_state = _pop_terminal_websocket_request_state(
                    session.pending_requests,
                    response_id=response_id,
                    fallback_request_state=matched_request_state,
                    prefer_previous_response_not_found=is_previous_response_not_found_event,
                    previous_response_id_hint=previous_response_id_hint,
                    error_message=error_message,
                    allow_precreated_terminal_fallback=event_type
                    in {
                        "response.failed",
                        "response.incomplete",
                        "error",
                    },
                )
                if terminal_request_state is not None:
                    session.queued_request_count = max(0, session.queued_request_count - 1)
                elif is_previous_response_not_found_event:
                    grouped_previous_response_request_states = _pop_matching_websocket_request_states(
                        session.pending_requests,
                        _matching_websocket_request_states_for_previous_response_error(
                            session.pending_requests,
                            previous_response_id_hint=previous_response_id_hint,
                            error_message=error_message,
                        ),
                    )
                    if grouped_previous_response_request_states:
                        session.queued_request_count = max(
                            0,
                            session.queued_request_count - len(grouped_previous_response_request_states),
                        )
                has_other_pending_requests = bool(session.pending_requests)

        if len(grouped_previous_response_request_states) > 1:
            session.upstream_control.reconnect_requested = True
            for grouped_request_state in grouped_previous_response_request_states:
                grouped_request_state.error_http_status_override = 502
                (
                    _grouped_downstream_text,
                    grouped_event_block,
                    grouped_event,
                    grouped_payload,
                    grouped_event_type,
                ) = _build_stream_incomplete_terminal_event_for_request(grouped_request_state)
                if grouped_request_state.event_queue is not None:
                    await grouped_request_state.event_queue.put(grouped_event_block)
                    await grouped_request_state.event_queue.put(None)
                await self._finalize_websocket_request_state(
                    grouped_request_state,
                    account=session.account,
                    account_id_value=session.account.id,
                    event=grouped_event,
                    event_type=grouped_event_type,
                    payload=grouped_payload,
                    api_key=grouped_request_state.api_key,
                    upstream_control=session.upstream_control,
                    response_create_gate=session.response_create_gate,
                )
            return

        if len(grouped_previous_response_request_states) == 1 and terminal_request_state is None:
            terminal_request_state = grouped_previous_response_request_states[0]

        status_request_state = terminal_request_state or matched_request_state
        if status_request_state is None and is_previous_response_not_found_event:
            session.upstream_control.reconnect_requested = True
            return

        if (
            status_request_state is not None
            and status_request_state.previous_response_id is not None
            and is_previous_response_not_found_event
            and (response_id is not None or has_other_pending_requests)
        ):
            status_request_state.error_http_status_override = 502
            event, payload, event_type, rewritten_text = _maybe_rewrite_websocket_previous_response_not_found_event(
                request_state=status_request_state,
                event=event,
                payload=payload,
                event_type=event_type,
                upstream_control=session.upstream_control,
                original_text=text,
            )
            event_block = f"data: {rewritten_text}\n\n"

        if event_type == "response.completed" and terminal_request_state is not None:
            # Record the completed response id regardless of input shape so
            # subsequent turns (including ones that never populated
            # input_item_count, e.g. string inputs) can still reuse this
            # anchor for continuity lookups.
            if response_id is not None:
                session.last_completed_response_id = response_id
            # Prefix trimming is only meaningful for list-shaped inputs, so
            # keep the input-count / fingerprint update scoped to that path.
            if terminal_request_state.input_item_count > 0:
                session.last_completed_input_count = terminal_request_state.input_item_count
                session.last_completed_input_prefix_fingerprint = terminal_request_state.input_full_fingerprint

        if event_type == "error":
            http_status = _http_error_status_from_payload(payload)
            if status_request_state is not None:
                status_request_state.error_http_status_override = http_status
            (
                event_block,
                payload,
                event,
                event_type,
            ) = _normalize_http_bridge_error_event(
                event=event,
                payload=payload,
                request_state=terminal_request_state or matched_request_state,
            )

        if event_type == "response.created" and release_create_gate and created_request_state is not None:
            _release_websocket_response_create_gate(created_request_state, session.response_create_gate)

        if response_id is not None and matched_request_state is not None:
            await self._register_http_bridge_previous_response_id(session, response_id)

        if matched_request_state is not None and matched_request_state.event_queue is not None:
            await matched_request_state.event_queue.put(event_block)

        if terminal_request_state is None:
            return

        if terminal_request_state is not matched_request_state and terminal_request_state.event_queue is not None:
            await terminal_request_state.event_queue.put(event_block)
        if terminal_request_state.event_queue is not None:
            await terminal_request_state.event_queue.put(None)

        if event_type in {"response.failed", "response.incomplete", "error"}:
            error_code = None
            if event_type == "error":
                error = event.error if event else None
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            elif event and event.response:
                error = event.response.error
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            _log_http_bridge_event(
                "terminal_error",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail=error_code,
                pending_count=await self._http_bridge_pending_count(session),
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )

        await self._finalize_websocket_request_state(
            terminal_request_state,
            account=session.account,
            account_id_value=session.account.id,
            event=event,
            event_type=event_type,
            payload=payload,
            api_key=terminal_request_state.api_key,
            upstream_control=session.upstream_control,
            response_create_gate=session.response_create_gate,
        )

    async def _refresh_websocket_api_key_policy(self, api_key: ApiKeyData | None) -> ApiKeyData | None:
        if api_key is None:
            return None

        with anyio.CancelScope(shield=True):
            async with self._repo_factory() as repos:
                service = ApiKeysService(repos.api_keys)
                try:
                    return await service.get_key_by_id(api_key.id)
                except ApiKeyInvalidError as exc:
                    raise ProxyAuthError(str(exc)) from exc

    def _remember_websocket_previous_response_owner(
        self,
        *,
        previous_response_id: str | None,
        api_key_id: str | None,
        account_id: str | None,
        session_id: str | None = None,
    ) -> None:
        if previous_response_id is None or account_id is None:
            return
        response_id = previous_response_id.strip()
        if not response_id:
            return
        account_id_value = account_id.strip()
        if not account_id_value:
            return
        cache_keys = [(response_id, api_key_id, None)]
        normalized_session_id = _normalize_session_id(session_id)
        if normalized_session_id is not None:
            cache_keys.append((response_id, api_key_id, normalized_session_id))
        for cache_key in cache_keys:
            self._websocket_previous_response_account_index.pop(cache_key, None)
            self._websocket_previous_response_account_index[cache_key] = account_id_value
        while len(self._websocket_previous_response_account_index) > _WEBSOCKET_PREVIOUS_RESPONSE_ACCOUNT_CACHE_LIMIT:
            self._websocket_previous_response_account_index.pop(
                next(iter(self._websocket_previous_response_account_index))
            )

    def _remember_websocket_previous_response_owner_miss(
        self,
        *,
        previous_response_id: str | None,
        api_key_id: str | None,
        request_cache_scope: str | None,
    ) -> None:
        del previous_response_id, api_key_id, request_cache_scope
        # Intentionally no-op: negative caching caused stale misses under concurrent sessions.
        return None

    async def _resolve_websocket_previous_response_owner(
        self,
        *,
        previous_response_id: str | None,
        api_key: ApiKeyData | None,
        session_id: str | None = None,
        surface: str,
    ) -> str | None:
        if previous_response_id is None:
            return None
        response_id = previous_response_id.strip()
        if not response_id:
            return None
        api_key_id = api_key.id if api_key is not None else None
        session_id_value = _normalize_session_id(session_id)
        cache_key = (response_id, api_key_id, session_id_value)
        cached_account_id = self._websocket_previous_response_account_index.get(cache_key)
        if cached_account_id is not None:
            _record_continuity_owner_resolution(
                surface=surface,
                source="request_cache",
                outcome="hit",
                previous_response_id=response_id,
                session_id=session_id_value,
            )
            return cached_account_id
        fallback_account_id = (
            self._websocket_previous_response_account_index.get((response_id, api_key_id, None))
            if session_id_value is not None
            else None
        )
        try:
            async with self._repo_factory() as repos:
                account_id = await repos.request_logs.find_latest_account_id_for_response_id(
                    response_id=response_id,
                    api_key_id=api_key_id,
                    session_id=session_id_value,
                )
        except Exception as exc:
            if fallback_account_id is not None:
                _record_continuity_owner_resolution(
                    surface=surface,
                    source="request_cache_fallback",
                    outcome="hit",
                    previous_response_id=response_id,
                    session_id=session_id_value,
                )
                logger.warning(
                    "Previous response owner lookup failed; using cached owner pin",
                    exc_info=True,
                )
                return fallback_account_id
            _record_continuity_owner_resolution(
                surface=surface,
                source="request_logs",
                outcome="fail_closed",
                previous_response_id=response_id,
                session_id=session_id_value,
            )
            _record_continuity_fail_closed(
                surface=surface,
                reason="owner_lookup_failed",
                previous_response_id=response_id,
                session_id=session_id_value,
            )
            logger.warning("Previous response owner lookup failed; failing closed", exc_info=True)
            raise ProxyResponseError(
                502,
                _previous_response_owner_lookup_failed_error_envelope(),
            ) from exc
        if account_id is None:
            if fallback_account_id is not None:
                _record_continuity_owner_resolution(
                    surface=surface,
                    source="request_cache_fallback",
                    outcome="hit",
                    previous_response_id=response_id,
                    session_id=session_id_value,
                )
            else:
                _record_continuity_owner_resolution(
                    surface=surface,
                    source="request_logs",
                    outcome="miss",
                    previous_response_id=response_id,
                    session_id=session_id_value,
                )
            return fallback_account_id
        self._remember_websocket_previous_response_owner(
            previous_response_id=response_id,
            api_key_id=api_key_id,
            account_id=account_id,
            session_id=session_id_value,
        )
        _record_continuity_owner_resolution(
            surface=surface,
            source="request_logs",
            outcome="hit",
            previous_response_id=response_id,
            session_id=session_id_value,
        )
        return account_id

    async def _handle_websocket_connect_error(self, account: Account, exc: ProxyResponseError) -> ClassifiedFailure:
        error = _parse_openai_error(exc.payload)
        error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
        return await self._handle_stream_error(
            account,
            _upstream_error_from_openai(error),
            error_code,
            http_status=exc.status_code,
        )

    async def _relay_upstream_websocket_messages(
        self,
        websocket: WebSocket,
        upstream: UpstreamResponsesWebSocket,
        *,
        account: Account,
        account_id_value: str,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        client_send_lock: anyio.Lock,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
        proxy_request_budget_seconds: float,
        stream_idle_timeout_seconds: float,
        downstream_activity: _DownstreamWebSocketActivity,
    ) -> None:
        try:
            while True:
                receive_timeout = await self._next_websocket_receive_timeout(
                    pending_requests,
                    pending_lock=pending_lock,
                    proxy_request_budget_seconds=proxy_request_budget_seconds,
                    stream_idle_timeout_seconds=stream_idle_timeout_seconds,
                )
                try:
                    if receive_timeout is None:
                        message = await upstream.receive()
                    elif receive_timeout.timeout_seconds <= 0:
                        raise asyncio.TimeoutError()
                    else:
                        message = await asyncio.wait_for(
                            upstream.receive(),
                            timeout=receive_timeout.timeout_seconds,
                        )
                except asyncio.TimeoutError:
                    if receive_timeout is None:
                        raise
                    if receive_timeout.fail_all_pending:
                        await self._fail_pending_websocket_requests(
                            account_id_value=account_id_value,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            error_code=receive_timeout.error_code,
                            error_message=receive_timeout.error_message,
                            api_key=api_key,
                            websocket=websocket,
                            client_send_lock=client_send_lock,
                            response_create_gate=response_create_gate,
                        )
                        upstream_control.reconnect_requested = True
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket after timeout", exc_info=True)
                        break
                    await self._fail_expired_pending_websocket_requests(
                        account_id_value=account_id_value,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        request_budget_seconds=proxy_request_budget_seconds,
                        error_code=receive_timeout.error_code,
                        error_message=receive_timeout.error_message,
                        api_key=api_key,
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        response_create_gate=response_create_gate,
                    )
                    continue
                if message.kind == "text" and message.text is not None:
                    downstream_activity.mark()
                    downstream_text = await self._process_upstream_websocket_text(
                        message.text,
                        account=account,
                        account_id_value=account_id_value,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        api_key=api_key,
                        upstream_control=upstream_control,
                        response_create_gate=response_create_gate,
                    )
                    suppress_downstream_event = upstream_control.suppress_downstream_event
                    downstream_texts = upstream_control.downstream_texts
                    upstream_control.suppress_downstream_event = False
                    upstream_control.downstream_texts = None
                    if downstream_texts is not None:
                        for emitted_text in downstream_texts:
                            await self._send_downstream_websocket_text(
                                websocket,
                                client_send_lock=client_send_lock,
                                text=emitted_text,
                                downstream_activity=downstream_activity,
                            )
                    elif not suppress_downstream_event:
                        await self._send_downstream_websocket_text(
                            websocket,
                            client_send_lock=client_send_lock,
                            text=downstream_text,
                            downstream_activity=downstream_activity,
                        )
                    if upstream_control.reconnect_requested:
                        should_reconnect = upstream_control.replay_request_state is not None
                        if not should_reconnect:
                            async with pending_lock:
                                should_reconnect = not pending_requests
                        if should_reconnect:
                            try:
                                await upstream.close()
                            except Exception:
                                logger.debug("Failed to close upstream websocket for reconnect", exc_info=True)
                            break
                    continue
                if message.kind == "binary" and message.data is not None:
                    downstream_activity.mark()
                    await self._send_downstream_websocket_bytes(
                        websocket,
                        client_send_lock=client_send_lock,
                        data=message.data,
                        downstream_activity=downstream_activity,
                    )
                    continue
                replay_request_state = await _pop_replayable_precreated_websocket_request_state(
                    pending_requests,
                    pending_lock=pending_lock,
                )
                if replay_request_state is not None:
                    upstream_control.reconnect_requested = True
                    upstream_control.replay_request_state = replay_request_state
                    logger.info(
                        "Transparent websocket replay after upstream close request_id=%s close_code=%s",
                        replay_request_state.request_log_id or replay_request_state.request_id,
                        message.close_code,
                    )
                    try:
                        await upstream.close()
                    except Exception:
                        logger.debug("Failed to close upstream websocket for replay", exc_info=True)
                    break
                await self._fail_pending_websocket_requests(
                    account_id_value=account_id_value,
                    pending_requests=pending_requests,
                    pending_lock=pending_lock,
                    error_code="stream_incomplete",
                    error_message=_upstream_websocket_disconnect_message(message),
                    api_key=api_key,
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    response_create_gate=response_create_gate,
                    downstream_activity=downstream_activity,
                )
                break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Upstream websocket reader crashed account_id=%s",
                account_id_value,
                exc_info=True,
            )
            await self._fail_pending_websocket_requests(
                account_id_value=account_id_value,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="stream_incomplete",
                error_message="Upstream websocket reader crashed before response.completed",
                api_key=api_key,
                websocket=websocket,
                client_send_lock=client_send_lock,
                response_create_gate=response_create_gate,
                downstream_activity=downstream_activity,
            )
        finally:
            async with pending_lock:
                has_pending_requests = bool(pending_requests)
            if not upstream_control.reconnect_requested and has_pending_requests:
                try:
                    await websocket.close()
                except Exception:
                    logger.debug("Failed to close downstream websocket", exc_info=True)

    async def _process_upstream_websocket_text(
        self,
        text: str,
        *,
        account: Account,
        account_id_value: str,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
    ) -> str:
        event_block = f"data: {text}\n\n"
        payload = parse_sse_data_json(event_block)
        event = parse_sse_event(event_block)
        event_type = _event_type_from_payload(event, payload)
        response_id = _websocket_response_id(event, payload)
        error_message = _websocket_event_error_message(event_type, payload)
        is_previous_response_not_found_event = _is_previous_response_not_found_error(
            code=_normalize_error_code(
                _websocket_event_error_code(event_type, payload),
                _websocket_event_error_type(event_type, payload),
            ),
            param=_websocket_event_error_param(event_type, payload),
            message=error_message,
        )
        previous_response_id_hint = _previous_response_id_from_not_found_message(error_message)

        async with pending_lock:
            request_state = None
            created_request_state = None
            has_other_pending_requests = False
            grouped_previous_response_request_states: list[_WebSocketRequestState] = []
            if event_type == "response.created":
                request_state = _assign_websocket_response_id(pending_requests, response_id)
                created_request_state = request_state
                release_create_gate = request_state is not None
            elif response_id is not None:
                request_state = _find_websocket_request_state_by_response_id(pending_requests, response_id)
                release_create_gate = False
            elif response_id is None:
                request_state = _match_websocket_request_state_for_anonymous_event(
                    pending_requests,
                    prefer_previous_response_not_found=is_previous_response_not_found_event,
                    previous_response_id_hint=previous_response_id_hint,
                    error_message=error_message,
                )
                release_create_gate = False
            else:
                release_create_gate = False
            if request_state is not None:
                actual_service_tier = _service_tier_from_event_payload(payload)
                if actual_service_tier is not None:
                    request_state.actual_service_tier = actual_service_tier
                    request_state.service_tier = actual_service_tier
            if (
                event_type in {"response.completed", "response.failed", "response.incomplete", "error"}
                and pending_requests
            ):
                request_state = _pop_terminal_websocket_request_state(
                    pending_requests,
                    response_id=response_id,
                    fallback_request_state=request_state,
                    prefer_previous_response_not_found=is_previous_response_not_found_event,
                    previous_response_id_hint=previous_response_id_hint,
                    error_message=error_message,
                    allow_precreated_terminal_fallback=event_type
                    in {
                        "response.failed",
                        "response.incomplete",
                        "error",
                    },
                )
                if request_state is None and is_previous_response_not_found_event:
                    grouped_previous_response_request_states = _pop_matching_websocket_request_states(
                        pending_requests,
                        _matching_websocket_request_states_for_previous_response_error(
                            pending_requests,
                            previous_response_id_hint=previous_response_id_hint,
                            error_message=error_message,
                        ),
                    )
                has_other_pending_requests = bool(pending_requests)
            else:
                request_state = None

        if event_type == "response.created" and release_create_gate and created_request_state is not None:
            _release_websocket_response_create_gate(created_request_state, response_create_gate)

        if len(grouped_previous_response_request_states) > 1:
            upstream_control.reconnect_requested = True
            downstream_texts: list[str] = []
            for grouped_request_state in grouped_previous_response_request_states:
                (
                    grouped_downstream_text,
                    _grouped_event_block,
                    grouped_event,
                    grouped_payload,
                    grouped_event_type,
                ) = _build_stream_incomplete_terminal_event_for_request(grouped_request_state)
                downstream_texts.append(grouped_downstream_text)
                await self._finalize_websocket_request_state(
                    grouped_request_state,
                    account=account,
                    account_id_value=account_id_value,
                    event=grouped_event,
                    event_type=grouped_event_type,
                    payload=grouped_payload,
                    api_key=api_key,
                    upstream_control=upstream_control,
                    response_create_gate=response_create_gate,
                )
            upstream_control.suppress_downstream_event = True
            upstream_control.downstream_texts = downstream_texts
            return downstream_texts[0]

        if len(grouped_previous_response_request_states) == 1 and request_state is None:
            request_state = grouped_previous_response_request_states[0]

        if request_state is None:
            if is_previous_response_not_found_event:
                upstream_control.suppress_downstream_event = True
            return text

        retry_is_previous_response_not_found = is_previous_response_not_found_event
        retry_error_code = _websocket_precreated_retry_error_code(
            request_state,
            event_type=event_type,
            payload=payload,
            has_other_pending_requests=has_other_pending_requests,
        )
        event, payload, event_type, downstream_text = _maybe_rewrite_websocket_previous_response_not_found_event(
            request_state=request_state,
            event=event,
            payload=payload,
            event_type=event_type,
            upstream_control=upstream_control,
            original_text=text,
        )
        if retry_error_code is None:
            retry_error_code = _websocket_precreated_retry_error_code(
                request_state,
                event_type=event_type,
                payload=payload,
                has_other_pending_requests=has_other_pending_requests,
            )
        if (
            retry_error_code in _WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES
            and request_state.previous_response_id is not None
            and request_state.preferred_account_id is not None
        ):
            await self._handle_stream_error(
                account,
                {"message": _websocket_event_error_message(event_type, payload) or "Upstream error"},
                retry_error_code,
            )
            event, payload, event_type, downstream_text = _rewrite_websocket_previous_response_owner_unavailable_event(
                request_state=request_state,
            )
            retry_error_code = None
        if retry_error_code is not None:
            upstream_control.reconnect_requested = True
            if retry_is_previous_response_not_found:
                request_state.replay_count += 1
                request_state.awaiting_response_created = True
                request_state.response_id = None
                upstream_control.suppress_downstream_event = True
                upstream_control.replay_request_state = request_state
            else:
                request_state.replay_count += 1
                request_state.awaiting_response_created = True
                request_state.response_id = None
                upstream_control.suppress_downstream_event = True
                upstream_control.replay_request_state = request_state
                await self._handle_stream_error(
                    account,
                    {"message": _websocket_event_error_message(event_type, payload) or "Upstream error"},
                    retry_error_code,
                )
            return downstream_text

        await self._finalize_websocket_request_state(
            request_state,
            account=account,
            account_id_value=account_id_value,
            event=event,
            event_type=event_type,
            payload=payload,
            api_key=api_key,
            upstream_control=upstream_control,
            response_create_gate=response_create_gate,
        )
        return downstream_text

    async def _next_websocket_receive_timeout(
        self,
        pending_requests: deque[_WebSocketRequestState],
        *,
        pending_lock: anyio.Lock,
        proxy_request_budget_seconds: float,
        stream_idle_timeout_seconds: float,
    ) -> _WebSocketReceiveTimeout | None:
        async with pending_lock:
            started_ats = [request_state.started_at for request_state in pending_requests]
        return _websocket_receive_timeout_for_pending_requests(
            started_ats,
            proxy_request_budget_seconds=proxy_request_budget_seconds,
            stream_idle_timeout_seconds=stream_idle_timeout_seconds,
        )

    async def _downstream_websocket_is_idle(
        self,
        pending_requests: deque[_WebSocketRequestState],
        *,
        pending_lock: anyio.Lock,
        downstream_activity: _DownstreamWebSocketActivity,
        idle_timeout_seconds: float,
    ) -> bool:
        async with pending_lock:
            if pending_requests:
                return False
        return (time.monotonic() - downstream_activity.last_activity_at) >= idle_timeout_seconds

    async def _fail_expired_pending_websocket_requests(
        self,
        *,
        account_id_value: str | None,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        request_budget_seconds: float,
        error_code: str,
        error_message: str,
        api_key: ApiKeyData | None,
        websocket: WebSocket | None = None,
        client_send_lock: anyio.Lock | None = None,
        response_create_gate: asyncio.Semaphore | None = None,
    ) -> None:
        now = time.monotonic()
        async with pending_lock:
            expired_requests = [
                request_state
                for request_state in list(pending_requests)
                if now >= request_state.started_at + request_budget_seconds
            ]
            for request_state in expired_requests:
                pending_requests.remove(request_state)
        if not expired_requests:
            return
        await self._fail_pending_websocket_requests(
            account_id_value=account_id_value,
            pending_requests=deque(expired_requests),
            pending_lock=anyio.Lock(),
            error_code=error_code,
            error_message=error_message,
            api_key=api_key,
            websocket=websocket,
            client_send_lock=client_send_lock,
            response_create_gate=response_create_gate,
        )

    async def _finalize_websocket_request_state(
        self,
        request_state: _WebSocketRequestState,
        *,
        account: Account,
        account_id_value: str,
        event: OpenAIEvent | None,
        event_type: str | None,
        payload: dict[str, JsonValue] | None,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        status = "success"
        error_code = None
        error_message = None
        usage = None
        error_payload: UpstreamError | None = None
        response_id = request_state.response_id or request_state.request_id
        response_service_tier = request_state.service_tier

        if event_type == "error":
            status = "error"
            error = event.error if event else None
            error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            error_message = error.message if error else None
            error_payload = _upstream_error_from_openai(error)
        elif event_type in {"response.failed", "response.incomplete"}:
            status = "error"
            error = event.response.error if event and event.response else None
            error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            error_message = error.message if error else None
            if event_type == "response.failed":
                error_payload = _upstream_error_from_openai(error)
            usage = event.response.usage if event and event.response else None
            if event and event.response and event.response.id:
                response_id = event.response.id
        elif event_type == "response.completed":
            usage = event.response.usage if event and event.response else None
            if event and event.response and event.response.id:
                response_id = event.response.id

        actual_service_tier = _service_tier_from_event_payload(payload)
        if actual_service_tier is not None:
            request_state.actual_service_tier = actual_service_tier
            response_service_tier = actual_service_tier

        settlement = _StreamSettlement(
            status=status,
            model=request_state.model or "",
            service_tier=response_service_tier,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_input_tokens=(
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            ),
            error_code=error_code,
            error_message=error_message,
            error=error_payload,
        )
        if event_type in {"response.failed", "response.incomplete", "error"}:
            settlement.record_success = False
        if event_type in {"response.failed", "error"}:
            settlement.account_health_error = _should_penalize_stream_error(error_code)
        _release_websocket_response_create_gate(request_state, response_create_gate)
        await self._settle_stream_api_key_usage(
            api_key,
            request_state.api_key_reservation,
            settlement,
            response_id,
        )
        if settlement.account_health_error:
            await self._handle_stream_error(
                account,
                _stream_settlement_error_payload(settlement),
                settlement.error_code or "upstream_error",
            )
            upstream_control.reconnect_requested = True
        elif settlement.record_success:
            await self._load_balancer.record_success(account)
            self._remember_websocket_previous_response_owner(
                previous_response_id=response_id,
                api_key_id=api_key.id if api_key is not None else None,
                account_id=account_id_value,
                session_id=request_state.session_id,
            )

        latency_ms = int((time.monotonic() - request_state.started_at) * 1000)
        cached_input_tokens = usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
        reasoning_tokens = (
            usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
        )
        if not request_state.skip_request_log:
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=response_id,
                model=request_state.model or "",
                latency_ms=latency_ms,
                status=status,
                error_code=error_code,
                error_message=error_message,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_effort=request_state.reasoning_effort,
                transport=request_state.transport,
                service_tier=response_service_tier,
                requested_service_tier=request_state.requested_service_tier,
                actual_service_tier=request_state.actual_service_tier,
                latency_first_token_ms=request_state.latency_first_token_ms,
                session_id=request_state.session_id,
            )

    async def _write_websocket_connect_failure(
        self,
        *,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        error_code: str,
        error_message: str,
    ) -> None:
        if request_state.skip_request_log:
            return
        await self._write_request_log(
            account_id=account_id,
            api_key=api_key,
            request_id=request_state.request_log_id or request_state.request_id,
            model=request_state.model or "",
            latency_ms=int((time.monotonic() - request_state.started_at) * 1000),
            status="error",
            error_code=error_code,
            error_message=error_message,
            reasoning_effort=request_state.reasoning_effort,
            transport=request_state.transport,
            service_tier=request_state.service_tier,
            requested_service_tier=request_state.requested_service_tier,
            actual_service_tier=request_state.actual_service_tier,
            latency_first_token_ms=request_state.latency_first_token_ms,
            session_id=request_state.session_id,
        )

    async def _emit_websocket_connect_failure(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        status_code: int,
        payload: OpenAIErrorEnvelope,
        error_code: str,
        error_message: str,
    ) -> None:
        status_code, payload, error_code, error_message = _sanitize_websocket_connect_failure(
            request_state=request_state,
            status_code=status_code,
            payload=payload,
            error_code=error_code,
            error_message=error_message,
        )
        await self._release_websocket_reservation(request_state.api_key_reservation)
        await self._write_websocket_connect_failure(
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
            error_code=error_code,
            error_message=error_message,
        )
        response_create_gate = request_state.response_create_gate
        if response_create_gate is not None:
            _release_websocket_response_create_gate(request_state, response_create_gate)
        async with client_send_lock:
            await websocket.send_text(
                _serialize_websocket_error_event(_wrapped_websocket_error_event(status_code, payload))
            )

    async def _emit_websocket_proxy_request_timeout(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
    ) -> None:
        await self._emit_websocket_connect_failure(
            websocket,
            client_send_lock=client_send_lock,
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
            status_code=502,
            payload=openai_error(
                "upstream_request_timeout",
                "Proxy request budget exhausted",
                error_type="server_error",
            ),
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
        )

    async def _fail_pending_websocket_requests(
        self,
        *,
        account_id_value: str | None,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        error_code: str,
        error_message: str,
        api_key: ApiKeyData | None,
        websocket: WebSocket | None = None,
        client_send_lock: anyio.Lock | None = None,
        response_create_gate: asyncio.Semaphore | None = None,
        downstream_activity: _DownstreamWebSocketActivity | None = None,
    ) -> None:
        async with pending_lock:
            remaining = list(pending_requests)
            pending_requests.clear()

        last_index = len(remaining) - 1
        for index, request_state in enumerate(remaining):
            request_error_code = request_state.error_code_override or error_code
            request_error_message = request_state.error_message_override or error_message
            request_error_type = request_state.error_type_override or "server_error"
            request_error_param = request_state.error_param_override
            if index == last_index:
                _maybe_dump_oversized_response_create_request(
                    request_state,
                    account_id_value=account_id_value,
                    error_code=request_error_code,
                    error_message=request_error_message,
                )
            if response_create_gate is not None:
                _release_websocket_response_create_gate(request_state, response_create_gate)
            if request_state.event_queue is not None:
                await request_state.event_queue.put(
                    format_sse_event(
                        response_failed_event(
                            request_error_code,
                            request_error_message,
                            error_type=request_error_type,
                            response_id=request_state.response_id or request_state.request_id,
                            error_param=request_error_param,
                        )
                    )
                )
                await request_state.event_queue.put(None)
            if websocket is not None and client_send_lock is not None:
                await self._emit_websocket_terminal_error(
                    websocket,
                    client_send_lock=client_send_lock,
                    request_state=request_state,
                    error_code=request_error_code,
                    error_message=request_error_message,
                    error_type=request_error_type,
                    error_param=request_error_param,
                    downstream_activity=downstream_activity,
                )
            await self._release_websocket_reservation(request_state.api_key_reservation)
            if account_id_value is None or request_state.skip_request_log:
                continue
            latency_ms = int((time.monotonic() - request_state.started_at) * 1000)
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_state.response_id or request_state.request_log_id or request_state.request_id,
                model=request_state.model or "",
                latency_ms=latency_ms,
                status="error",
                error_code=request_error_code,
                error_message=request_error_message,
                reasoning_effort=request_state.reasoning_effort,
                transport=request_state.transport,
                service_tier=request_state.service_tier,
                requested_service_tier=request_state.requested_service_tier,
                actual_service_tier=request_state.actual_service_tier,
                latency_first_token_ms=request_state.latency_first_token_ms,
            )

    async def _emit_websocket_terminal_error(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        request_state: _WebSocketRequestState,
        error_code: str,
        error_message: str,
        error_type: str = "server_error",
        error_param: str | None = None,
        downstream_activity: _DownstreamWebSocketActivity | None = None,
    ) -> None:
        event = response_failed_event(
            error_code,
            error_message,
            error_type=error_type,
            response_id=request_state.response_id or request_state.request_id,
            error_param=error_param,
        )
        response_create_gate = request_state.response_create_gate
        if response_create_gate is not None:
            _release_websocket_response_create_gate(request_state, response_create_gate)
        try:
            await self._send_downstream_websocket_text(
                websocket,
                client_send_lock=client_send_lock,
                text=json.dumps(event, ensure_ascii=True, separators=(",", ":")),
                downstream_activity=downstream_activity,
            )
        except Exception:
            logger.debug("Failed to emit websocket terminal error", exc_info=True)

    async def _send_downstream_websocket_text(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        text: str,
        downstream_activity: _DownstreamWebSocketActivity | None = None,
    ) -> None:
        if downstream_activity is not None:
            downstream_activity.mark()
        async with client_send_lock:
            if downstream_activity is not None:
                downstream_activity.mark()
            await websocket.send_text(text)
            if downstream_activity is not None:
                downstream_activity.mark()

    async def _send_downstream_websocket_bytes(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        data: bytes,
        downstream_activity: _DownstreamWebSocketActivity | None = None,
    ) -> None:
        if downstream_activity is not None:
            downstream_activity.mark()
        async with client_send_lock:
            if downstream_activity is not None:
                downstream_activity.mark()
            await websocket.send_bytes(data)
            if downstream_activity is not None:
                downstream_activity.mark()

    async def _reserve_websocket_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        *,
        request_model: str | None,
        request_service_tier: str | None,
    ) -> ApiKeyUsageReservationData | None:
        if api_key is None:
            return None

        with anyio.CancelScope(shield=True):
            async with self._repo_factory() as repos:
                service = ApiKeysService(repos.api_keys)
                try:
                    return await service.enforce_limits_for_request(
                        api_key.id,
                        request_model=request_model,
                        request_service_tier=request_service_tier,
                    )
                except ApiKeyRateLimitExceededError as exc:
                    message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
                    raise ProxyRateLimitError(message) from exc
                except ApiKeyInvalidError as exc:
                    raise ProxyAuthError(str(exc)) from exc

    async def _release_websocket_reservation(
        self,
        reservation: ApiKeyUsageReservationData | None,
    ) -> None:
        if reservation is None:
            return
        try:
            with anyio.CancelScope(shield=True):
                async with self._repo_factory() as repos:
                    service = ApiKeysService(repos.api_keys)
                    await service.release_usage_reservation(reservation.reservation_id)
        except asyncio.CancelledError:
            logger.warning(
                "Failed to release websocket API key reservation reservation_id=%s",
                reservation.reservation_id,
                exc_info=True,
            )
        except Exception:
            logger.warning(
                "Failed to release websocket API key reservation reservation_id=%s",
                reservation.reservation_id,
                exc_info=True,
            )

    async def _settle_compact_api_key_usage(
        self,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        response: CompactResponsePayload | None,
        request_service_tier: str | None,
    ) -> None:
        if api_key is None or api_key_reservation is None:
            return

        reservation_id = api_key_reservation.reservation_id
        usage = response.usage if response is not None else None
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        cached_input_tokens = usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else 0
        model_name = api_key_reservation.model or (getattr(response, "model", None) or "")
        response_service_tier = _service_tier_from_response(response)
        service_tier = (
            response_service_tier
            if isinstance(response_service_tier, str)
            else request_service_tier
            if isinstance(request_service_tier, str)
            else None
        )

        with anyio.CancelScope(shield=True):
            try:
                async with self._repo_factory() as repos:
                    api_keys_service = ApiKeysService(repos.api_keys)
                    if response is not None and input_tokens is not None and output_tokens is not None:
                        await api_keys_service.finalize_usage_reservation(
                            reservation_id,
                            model=model_name,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cached_input_tokens=cached_input_tokens or 0,
                            service_tier=service_tier,
                        )
                    else:
                        await api_keys_service.release_usage_reservation(reservation_id)
            except Exception:
                logger.warning(
                    "Failed to settle compact API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    get_request_id(),
                    exc_info=True,
                )

    async def _settle_stream_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        settlement: _StreamSettlement,
        request_id: str,
    ) -> bool:
        """Settle stream reservation. Returns True if settled."""
        if api_key is None or api_key_reservation is None:
            return True

        reservation_id = api_key_reservation.reservation_id
        model_name = api_key_reservation.model or settlement.model or ""

        settled: bool = False
        with anyio.CancelScope(shield=True):
            try:
                async with self._repo_factory() as repos:
                    api_keys_service = ApiKeysService(repos.api_keys)
                    if (
                        settlement.status == "success"
                        and settlement.input_tokens is not None
                        and settlement.output_tokens is not None
                    ):
                        await api_keys_service.finalize_usage_reservation(
                            reservation_id,
                            model=model_name,
                            input_tokens=settlement.input_tokens,
                            output_tokens=settlement.output_tokens,
                            cached_input_tokens=settlement.cached_input_tokens or 0,
                            service_tier=settlement.service_tier,
                        )
                    else:
                        await api_keys_service.release_usage_reservation(reservation_id)
                settled = True
            except Exception:
                logger.warning(
                    "Failed to settle stream API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    request_id,
                    exc_info=True,
                )
                settled = False

        return settled

    async def rate_limit_headers(self) -> dict[str, str]:
        return await get_rate_limit_headers_cache().get(self._compute_rate_limit_headers)

    async def _compute_rate_limit_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        async with self._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            selected_accounts = _select_accounts_for_limits(accounts)
            if not selected_accounts:
                return headers

            account_map = {account.id: account for account in selected_accounts}
            primary_rows_raw, secondary_rows_raw = await asyncio.gather(
                self._latest_usage_rows(repos, account_map, "primary"),
                self._latest_usage_rows(repos, account_map, "secondary"),
            )
            primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
                primary_rows_raw,
                secondary_rows_raw,
            )

            primary_summary = _summarize_window(primary_rows, account_map, "primary")
            if primary_summary is not None:
                headers.update(_rate_limit_headers("primary", primary_summary))

            secondary_summary = _summarize_window(secondary_rows, account_map, "secondary")
            if secondary_summary is not None:
                headers.update(_rate_limit_headers("secondary", secondary_summary))

            headers.update(_credits_headers(await self._latest_usage_entries(repos, account_map)))
        return headers

    async def get_rate_limit_payload(self) -> RateLimitStatusPayloadData:
        async with self._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            await self._refresh_usage(repos, accounts)
            selected_accounts = _select_accounts_for_limits(accounts)
            if not selected_accounts:
                return RateLimitStatusPayloadData(plan_type="guest")

            account_map = {account.id: account for account in selected_accounts}
            primary_rows_raw, secondary_rows_raw = await asyncio.gather(
                self._latest_usage_rows(repos, account_map, "primary"),
                self._latest_usage_rows(repos, account_map, "secondary"),
            )
            primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
                primary_rows_raw,
                secondary_rows_raw,
            )

            primary_summary = _summarize_window(primary_rows, account_map, "primary")
            secondary_summary = _summarize_window(secondary_rows, account_map, "secondary")

            now_epoch = int(time.time())
            primary_window = _window_snapshot(primary_summary, primary_rows, "primary", now_epoch)
            secondary_window = _window_snapshot(secondary_summary, secondary_rows, "secondary", now_epoch)

            # Fetch additional rate limits
            additional_rate_limits = await self._build_additional_rate_limits(repos, account_map, now_epoch)

            return RateLimitStatusPayloadData(
                plan_type=_plan_type_for_accounts(selected_accounts),
                rate_limit=_rate_limit_details(primary_window, secondary_window),
                credits=_credits_snapshot(await self._latest_usage_entries(repos, account_map)),
                additional_rate_limits=additional_rate_limits,
            )

    async def _stream_with_retry(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        propagate_http_errors: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        suppress_text_done_events: bool,
        request_transport: str,
        codex_session_budget_reallocation_enabled: bool = True,
    ) -> AsyncIterator[str]:
        request_id = ensure_request_id()
        start = time.monotonic()
        requested_service_tier = _normalize_service_tier_value(payload.service_tier)
        base_settings = get_settings()
        settings = await get_settings_cache().get()
        deadline = start + base_settings.proxy_request_budget_seconds
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        upstream_stream_transport = _resolve_upstream_stream_transport(settings.upstream_stream_transport)
        had_prompt_cache_key = _prompt_cache_key_from_request_model(payload) is not None
        affinity = _sticky_key_for_responses_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=settings.sticky_threads_enabled,
            api_key=api_key,
            codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        )
        sticky_key_source = "none"
        if affinity.kind == StickySessionKind.CODEX_SESSION:
            sticky_key_source = "session_header"
        elif affinity.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "stream",
            payload,
            headers,
            sticky_kind=affinity.kind.value if affinity.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(payload) is not None,
        )
        routing_strategy = _routing_strategy(settings)
        max_attempts = _STREAM_MAX_ACCOUNT_ATTEMPTS
        settled = False
        any_attempt_logged = False
        settlement = _StreamSettlement()
        last_transient_exc: ProxyResponseError | None = None
        excluded_account_ids: set[str] = set()
        preferred_account_id: str | None = None
        require_preferred_account = False
        try:
            if payload.previous_response_id is not None:
                preferred_account_id = await self._resolve_websocket_previous_response_owner(
                    previous_response_id=payload.previous_response_id,
                    api_key=api_key,
                    session_id=_owner_lookup_session_id_from_headers(headers),
                    surface="http_stream",
                )
                require_preferred_account = preferred_account_id is not None
            for attempt in range(max_attempts):
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Proxy request budget exhausted before retry request_id=%s attempt=%s",
                        request_id,
                        attempt + 1,
                    )
                    await self._write_stream_preflight_error(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        start=start,
                        error_code="upstream_request_timeout",
                        error_message="Proxy request budget exhausted",
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        service_tier=requested_service_tier,
                        transport=request_transport,
                    )
                    yield format_sse_event(_proxy_request_timeout_event(request_id))
                    return
                try:
                    selection = await self._select_account_with_budget_compatible(
                        deadline,
                        request_id=request_id,
                        kind="stream",
                        api_key=api_key,
                        sticky_key=affinity.key,
                        sticky_kind=affinity.kind,
                        reallocate_sticky=affinity.reallocate_sticky,
                        sticky_max_age_seconds=affinity.max_age_seconds,
                        sticky_budget_reallocation_enabled=affinity.budget_reallocation_enabled,
                        prefer_earlier_reset_accounts=prefer_earlier_reset,
                        routing_strategy=routing_strategy,
                        model=payload.model,
                        exclude_account_ids=excluded_account_ids,
                        preferred_account_id=preferred_account_id,
                    )
                except ProxyResponseError as exc:
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                    error_message = error.message if error else None
                    if error_code == "upstream_unavailable" and error_message == "Proxy request budget exhausted":
                        await self._write_stream_preflight_error(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=requested_service_tier,
                            transport=request_transport,
                        )
                        yield format_sse_event(_proxy_request_timeout_event(request_id))
                        return
                    event = response_failed_event(
                        error_code,
                        error_message or "Upstream unavailable",
                        error_type=(error.type or "server_error") if error else "server_error",
                        response_id=request_id,
                    )
                    _apply_error_metadata(event["response"]["error"], error)
                    yield format_sse_event(event)
                    return
                account = selection.account
                if not account:
                    if require_preferred_account and preferred_account_id is not None:
                        message = "Previous response owner account is unavailable; retry later."
                        _record_continuity_fail_closed(
                            surface="http_stream",
                            reason="owner_account_unavailable",
                            previous_response_id=payload.previous_response_id,
                            session_id=headers.get("x-codex-turn-state") or headers.get("session_id"),
                            upstream_error_code="no_accounts",
                        )
                        event = response_failed_event(
                            "upstream_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await self._write_request_log(
                            account_id=preferred_account_id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code="upstream_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                        )
                        return
                    # If a prior attempt stored a transient 500 and the caller
                    # expects HTTP error propagation, re-raise the original error
                    # instead of returning a generic no_accounts event.
                    if propagate_http_errors and last_transient_exc is not None:
                        raise last_transient_exc
                    no_accounts_msg = selection.error_message or "No active accounts available"
                    error_code = selection.error_code or "no_accounts"
                    event = response_failed_event(
                        error_code,
                        no_accounts_msg,
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    await self._write_request_log(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        error_code=error_code,
                        error_message=no_accounts_msg,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        service_tier=requested_service_tier,
                        requested_service_tier=requested_service_tier,
                    )
                    return

                account_id_value = account.id
                if (
                    require_preferred_account
                    and preferred_account_id is not None
                    and account.id != preferred_account_id
                ):
                    message = "Previous response owner account is unavailable; retry later."
                    _record_continuity_fail_closed(
                        surface="http_stream",
                        reason="owner_account_unavailable",
                        previous_response_id=payload.previous_response_id,
                        session_id=headers.get("x-codex-turn-state") or headers.get("session_id"),
                        upstream_error_code="upstream_unavailable",
                    )
                    event = response_failed_event(
                        "upstream_unavailable",
                        message,
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    await self._write_request_log(
                        account_id=preferred_account_id,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        error_code="upstream_unavailable",
                        error_message=message,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        service_tier=payload.service_tier,
                        requested_service_tier=payload.service_tier,
                    )
                    return
                try:
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "Proxy request budget exhausted before freshness check "
                            "request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                        )
                        await self._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=requested_service_tier,
                            transport=request_transport,
                        )
                        yield format_sse_event(_proxy_request_timeout_event(request_id))
                        return
                    try:
                        account = await self._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        logger.warning(
                            "Stream refresh/connect failed request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                            exc_info=True,
                        )
                        message = str(exc) or "Request to upstream timed out"
                        await self._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=requested_service_tier,
                            transport=request_transport,
                        )
                        event = response_failed_event(
                            "upstream_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        return
                    any_attempt_logged = True
                    settlement = _StreamSettlement()
                    effective_attempt_timeout = _remaining_budget_seconds(deadline)
                    if effective_attempt_timeout <= 0:
                        logger.warning(
                            "Proxy request budget exhausted before stream attempt "
                            "request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                        )
                        await self._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=requested_service_tier,
                            transport=request_transport,
                        )
                        yield format_sse_event(_proxy_request_timeout_event(request_id))
                        return
                    transient_retries = 0
                    allow_retry_flag = attempt < max_attempts - 1
                    while True:
                        stream_timeout_tokens = _push_stream_attempt_timeout_overrides(
                            _remaining_budget_seconds(deadline),
                        )
                        try:
                            settlement = _StreamSettlement()
                            async for line in self._stream_once(
                                account,
                                payload,
                                headers,
                                request_id,
                                allow_retry_flag,
                                request_started_at=start,
                                allow_transient_retry=(
                                    transient_retries < _MAX_TRANSIENT_SAME_ACCOUNT_RETRIES - 1 or allow_retry_flag
                                ),
                                api_key=api_key,
                                settlement=settlement,
                                suppress_text_done_events=suppress_text_done_events,
                                upstream_stream_transport=upstream_stream_transport,
                                request_transport=request_transport,
                                preferred_account_id=preferred_account_id,
                            ):
                                yield line
                        except (_TransientStreamError, ProxyResponseError) as tex:
                            if isinstance(tex, ProxyResponseError) and tex.status_code != 500:
                                error = _parse_openai_error(tex.payload)
                                code = _normalize_error_code(
                                    error.code if error else None,
                                    error.type if error else None,
                                )
                                if _is_account_neutral_error_code(code):
                                    raise
                                classified = await self._handle_stream_error(
                                    account,
                                    _upstream_error_from_openai(error),
                                    code,
                                    http_status=tex.status_code,
                                )
                                if getattr(base_settings, "deterministic_failover_enabled", True):
                                    action = failover_decision(
                                        failure_class=classified["failure_class"],
                                        downstream_visible=False,
                                        candidates_remaining=max_attempts - attempt - 1,
                                    )
                                else:
                                    action = "surface"
                                logger.info(
                                    "Failover decision request_id=%s transport=stream account_id=%s "
                                    "attempt=%d failure_class=%s action=%s",
                                    request_id,
                                    account.id,
                                    attempt + 1,
                                    classified["failure_class"],
                                    action,
                                )
                                if action == "failover_next":
                                    last_transient_exc = tex
                                    excluded_account_ids.add(account.id)
                                    break
                                raise
                            transient_retries += 1
                            error_code = tex.code if isinstance(tex, _TransientStreamError) else "server_error"
                            error_payload: UpstreamError = (
                                tex.error
                                if isinstance(tex, _TransientStreamError)
                                else _upstream_error_from_openai(_parse_openai_error(tex.payload))
                            )
                            if (
                                transient_retries < _MAX_TRANSIENT_SAME_ACCOUNT_RETRIES
                                and _remaining_budget_seconds(deadline) > 0
                            ):
                                delay = backoff_seconds(transient_retries)
                                logger.info(
                                    "Transient stream error, retrying same account "
                                    "request_id=%s account_id=%s retry=%s/%s delay=%.2fs code=%s",
                                    request_id,
                                    account.id,
                                    transient_retries,
                                    _MAX_TRANSIENT_SAME_ACCOUNT_RETRIES,
                                    delay,
                                    error_code,
                                )
                                await asyncio.sleep(delay)
                                continue  # inner loop: retry same account
                            # Exhausted same-account retries — penalize and failover
                            logger.warning(
                                "Transient retries exhausted for account "
                                "request_id=%s account_id=%s retries=%s code=%s",
                                request_id,
                                account.id,
                                transient_retries,
                                error_code,
                            )
                            await self._handle_stream_error(account, error_payload, error_code)
                            # Record remaining errors so total equals transient_retries,
                            # meeting the load balancer backoff threshold (error_count >= 3).
                            await self._load_balancer.record_errors(account, transient_retries - 1)
                            # Preserve last ProxyResponseError for propagate_http_errors path.
                            if isinstance(tex, ProxyResponseError):
                                last_transient_exc = tex
                            excluded_account_ids.add(account.id)
                            break  # outer loop: select different account
                        finally:
                            pop_stream_timeout_overrides(stream_timeout_tokens)
                        if settlement.account_health_error:
                            await self._handle_stream_error(
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                            )
                        elif settlement.record_success:
                            await self._load_balancer.record_success(account)
                        settled = await self._settle_stream_api_key_usage(
                            api_key,
                            api_key_reservation,
                            settlement,
                            request_id,
                        )
                        return
                    continue  # outer loop: account failover after transient exhaustion
                except _RetryableStreamError as exc:
                    await self._handle_stream_error(account, exc.error, exc.code)
                    continue
                except _TerminalStreamError as exc:
                    if _should_penalize_stream_error(exc.code):
                        await self._handle_stream_error(account, exc.error, exc.code)
                    return
                except ProxyResponseError as exc:
                    if exc.status_code == 401:
                        remaining_budget = _remaining_budget_seconds(deadline)
                        if remaining_budget <= 0:
                            logger.warning(
                                "Proxy request budget exhausted before forced refresh retry "
                                "request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                            )
                            await self._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=requested_service_tier,
                                transport=request_transport,
                            )
                            yield format_sse_event(_proxy_request_timeout_event(request_id))
                            return
                        try:
                            account = await self._ensure_fresh_with_budget(
                                account,
                                force=True,
                                timeout_seconds=remaining_budget,
                            )
                        except RefreshError as refresh_exc:
                            if refresh_exc.is_permanent:
                                await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                            continue
                        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                            logger.warning(
                                "Stream forced refresh/connect failed request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                                exc_info=True,
                            )
                            message = str(exc) or "Request to upstream timed out"
                            await self._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_unavailable",
                                error_message=message,
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=requested_service_tier,
                                transport=request_transport,
                            )
                            event = response_failed_event(
                                "upstream_unavailable",
                                message,
                                response_id=request_id,
                            )
                            yield format_sse_event(event)
                            return
                        settlement = _StreamSettlement()
                        effective_attempt_timeout = _remaining_budget_seconds(deadline)
                        if effective_attempt_timeout <= 0:
                            logger.warning(
                                "Proxy request budget exhausted before post-refresh stream attempt "
                                "request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                            )
                            await self._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=requested_service_tier,
                                transport=request_transport,
                            )
                            yield format_sse_event(_proxy_request_timeout_event(request_id))
                            return
                        stream_timeout_tokens = _push_stream_attempt_timeout_overrides(effective_attempt_timeout)
                        try:
                            async for line in self._stream_once(
                                account,
                                payload,
                                headers,
                                request_id,
                                False,
                                request_started_at=start,
                                api_key=api_key,
                                settlement=settlement,
                                suppress_text_done_events=suppress_text_done_events,
                                upstream_stream_transport=upstream_stream_transport,
                                request_transport=request_transport,
                            ):
                                yield line
                        finally:
                            pop_stream_timeout_overrides(stream_timeout_tokens)
                        if settlement.account_health_error:
                            await self._handle_stream_error(
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                            )
                        elif settlement.record_success:
                            await self._load_balancer.record_success(account)
                        settled = await self._settle_stream_api_key_usage(
                            api_key,
                            api_key_reservation,
                            settlement,
                            request_id,
                        )
                        return
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                    error_message = error.message if error else None
                    error_type = error.type if error else None
                    error_param = error.param if error else None
                    if _should_penalize_stream_error(error_code):
                        await self._handle_stream_error(
                            account,
                            _upstream_error_from_openai(error),
                            error_code,
                        )
                    if propagate_http_errors:
                        raise
                    event = response_failed_event(
                        error_code,
                        error_message or "Upstream error",
                        error_type=error_type or "server_error",
                        response_id=request_id,
                        error_param=error_param,
                    )
                    _apply_error_metadata(event["response"]["error"], error)
                    yield format_sse_event(event)
                    return
                except RefreshError as exc:
                    if exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, exc.code)
                    continue
                except Exception:
                    logger.warning(
                        "Proxy streaming failed without retry account_id=%s request_id=%s",
                        account_id_value,
                        request_id,
                        exc_info=True,
                    )
                    event = response_failed_event(
                        "upstream_error",
                        "Proxy streaming failed",
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    return
            # When HTTP error propagation is enabled and the last failure was
            # a transient 500, re-raise to preserve the upstream status/payload.
            if propagate_http_errors and last_transient_exc is not None:
                raise last_transient_exc
            retries_exhausted_msg = "No available accounts after retries"
            event = response_failed_event(
                "no_accounts",
                retries_exhausted_msg,
                response_id=request_id,
            )
            yield format_sse_event(event)
            if not any_attempt_logged:
                await self._write_request_log(
                    account_id=None,
                    api_key=api_key,
                    request_id=request_id,
                    model=payload.model,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                    error_code="no_accounts",
                    error_message=retries_exhausted_msg,
                    reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                    transport=request_transport,
                    service_tier=requested_service_tier,
                    requested_service_tier=requested_service_tier,
                )
        finally:
            if not settled and api_key is not None and api_key_reservation is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        async with self._repo_factory() as repos:
                            api_keys_service = ApiKeysService(repos.api_keys)
                            await api_keys_service.release_usage_reservation(
                                api_key_reservation.reservation_id,
                            )
                    except Exception:
                        logger.warning(
                            "Failed to release stream API key reservation key_id=%s request_id=%s",
                            api_key.id,
                            request_id,
                            exc_info=True,
                        )

    async def _stream_once(
        self,
        account: Account,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        request_id: str,
        allow_retry: bool,
        *,
        request_started_at: float,
        allow_transient_retry: bool = False,
        api_key: ApiKeyData | None,
        settlement: _StreamSettlement,
        suppress_text_done_events: bool,
        upstream_stream_transport: str | None,
        request_transport: str,
        preferred_account_id: str | None = None,
    ) -> AsyncIterator[str]:
        account_id_value = account.id
        adapter = cast(ChatGPTWebProviderAdapter, self._provider_adapter(CHATGPT_WEB_PROVIDER_KIND))
        model = payload.model
        requested_service_tier = _normalize_service_tier_value(payload.service_tier)
        service_tier = requested_service_tier
        actual_service_tier: str | None = None
        reasoning_effort = payload.reasoning.effort if payload.reasoning else None
        session_id = _owner_lookup_session_id_from_headers(headers)
        start = time.monotonic()
        status = "success"
        error_code = None
        error_message = None
        response_id = request_id
        usage = None
        saw_text_delta = False
        latency_first_token_ms: int | None = None
        response_create_lease = AdmissionLease(None)

        try:
            response_create_lease = await self._get_work_admission().acquire_response_create()
            if upstream_stream_transport is not None:
                stream = await adapter.stream_response_events(
                    self._chatgpt_provider_subject(account),
                    payload,
                    headers,
                    raise_for_status=True,
                    upstream_stream_transport=upstream_stream_transport,
                )
            else:
                stream = await adapter.stream_response_events(
                    self._chatgpt_provider_subject(account),
                    payload,
                    headers,
                    raise_for_status=True,
                )
            iterator = stream.__aiter__()
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                response_create_lease.release()
                return
            response_create_lease.release()
            first_payload = parse_sse_data_json(first)
            event = parse_sse_event(first)
            event_type = _event_type_from_payload(event, first_payload)
            event_service_tier = _service_tier_from_event_payload(first_payload)
            if event_service_tier is not None:
                actual_service_tier = event_service_tier
                service_tier = event_service_tier
            terminal_stream_error: _TerminalStreamError | None = None
            if event and event.type in ("response.failed", "error"):
                if event.type == "response.failed":
                    response = event.response
                    error = response.error if response else None
                else:
                    error = event.error
                response_id = (
                    event.response.id
                    if event.type == "response.failed" and event.response and event.response.id
                    else request_id
                )
                code = _normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                )
                rewritten_error = _rewrite_previous_response_stream_error(
                    previous_response_id=payload.previous_response_id,
                    preferred_account_id=preferred_account_id,
                    error_code=code,
                    error_type=error.type if error else None,
                    error_message=error.message if error else None,
                    error_param=error.param if error else None,
                )
                status = "error"
                settlement.error = _upstream_error_from_openai(error)
                settlement.record_success = False
                if rewritten_error is not None:
                    rewritten_code, rewritten_message, upstream_error_code = rewritten_error
                    if upstream_error_code is not None:
                        await self._handle_stream_error(
                            account,
                            settlement.error,
                            upstream_error_code,
                        )
                    first, event, first_payload, event_type = _build_rewritten_stream_response_failed_event(
                        response_id=response_id,
                        error_code=rewritten_code,
                        error_message=rewritten_message,
                    )
                    error_code = rewritten_code
                    error_message = rewritten_message
                    settlement.account_health_error = False
                else:
                    error_code = code
                    error_message = error.message if error else None
                    settlement.account_health_error = _should_penalize_stream_error(code)
                    if allow_retry and _should_retry_stream_error(code):
                        raise _RetryableStreamError(code, settlement.error)
                    if allow_transient_retry and code in _TRANSIENT_RETRY_CODES:
                        raise _TransientStreamError(code, settlement.error)
                terminal_stream_error = _TerminalStreamError(
                    error_code or code,
                    settlement.error,
                )
                if allow_retry:
                    logger.info(
                        "Not retrying non-recoverable stream failure request_id=%s account_id=%s code=%s",
                        request_id,
                        account_id_value,
                        error_code or code,
                    )

            if event and event.type in ("response.completed", "response.incomplete"):
                usage = event.response.usage if event.response else None
                if event.response and event.response.id:
                    response_id = event.response.id
                if event.type == "response.incomplete":
                    status = "error"

            if suppress_text_done_events and event_type in _TEXT_DELTA_EVENT_TYPES:
                saw_text_delta = True
            if not _should_suppress_text_done_event(
                event_type=event_type,
                payload=first_payload,
                suppress_text_done_events=suppress_text_done_events,
                saw_text_delta=saw_text_delta,
            ):
                if latency_first_token_ms is None and event_type in _TEXT_DELTA_EVENT_TYPES:
                    latency_first_token_ms = int((time.monotonic() - request_started_at) * 1000)
                yield first
            if terminal_stream_error is not None:
                raise terminal_stream_error

            async for line in iterator:
                event_payload = parse_sse_data_json(line)
                event = parse_sse_event(line)
                event_type = _event_type_from_payload(event, event_payload)
                event_service_tier = _service_tier_from_event_payload(event_payload)
                if event_service_tier is not None:
                    actual_service_tier = event_service_tier
                    service_tier = event_service_tier
                if suppress_text_done_events and event_type in _TEXT_DELTA_EVENT_TYPES:
                    saw_text_delta = True
                if _should_suppress_text_done_event(
                    event_type=event_type,
                    payload=event_payload,
                    suppress_text_done_events=suppress_text_done_events,
                    saw_text_delta=saw_text_delta,
                ):
                    continue
                if event:
                    if event_type in ("response.failed", "error"):
                        status = "error"
                        if event_type == "response.failed":
                            response = event.response
                            error = response.error if response else None
                            if response and response.id:
                                response_id = response.id
                        else:
                            error = event.error
                        raw_error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        rewritten_error = _rewrite_previous_response_stream_error(
                            previous_response_id=payload.previous_response_id,
                            preferred_account_id=preferred_account_id,
                            error_code=raw_error_code,
                            error_type=error.type if error else None,
                            error_message=error.message if error else None,
                            error_param=error.param if error else None,
                        )
                        if rewritten_error is not None:
                            response_id = (
                                event.response.id
                                if event_type == "response.failed" and event.response and event.response.id
                                else request_id
                            )
                            rewritten_code, rewritten_message, upstream_error_code = rewritten_error
                            if upstream_error_code is not None:
                                await self._handle_stream_error(
                                    account,
                                    _upstream_error_from_openai(error),
                                    upstream_error_code,
                                )
                            line, event, event_payload, event_type = _build_rewritten_stream_response_failed_event(
                                response_id=response_id,
                                error_code=rewritten_code,
                                error_message=rewritten_message,
                            )
                            error_code = rewritten_code
                            error_message = rewritten_message
                            settlement.error = _upstream_error_from_openai(error)
                            settlement.record_success = False
                            settlement.account_health_error = False
                        else:
                            error_code = raw_error_code
                            error_message = error.message if error else None
                            settlement.error = _upstream_error_from_openai(error)
                            settlement.record_success = False
                            settlement.account_health_error = _should_penalize_stream_error(error_code)
                    if event_type in ("response.completed", "response.incomplete"):
                        response = event.response if event is not None else None
                        usage = response.usage if response else None
                        if response and response.id:
                            response_id = response.id
                        if event_type == "response.incomplete":
                            status = "error"
                if latency_first_token_ms is None and event_type in _TEXT_DELTA_EVENT_TYPES:
                    latency_first_token_ms = int((time.monotonic() - request_started_at) * 1000)
                yield line
        except ProxyResponseError as exc:
            response_create_lease.release()
            error = _parse_openai_error(exc.payload)
            rewritten_error = _rewrite_previous_response_stream_error(
                previous_response_id=payload.previous_response_id,
                preferred_account_id=preferred_account_id,
                error_code=_normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                ),
                error_type=error.type if error else None,
                error_message=error.message if error else None,
                error_param=error.param if error else None,
            )
            if rewritten_error is not None:
                rewritten_code, rewritten_message, upstream_error_code = rewritten_error
                if upstream_error_code is not None:
                    await self._handle_stream_error(
                        account,
                        _upstream_error_from_openai(error),
                        upstream_error_code,
                    )
                status = "error"
                error_code = rewritten_code
                error_message = rewritten_message
                settlement.record_success = False
                settlement.account_health_error = False
                yield _build_rewritten_stream_response_failed_event(
                    response_id=request_id,
                    error_code=rewritten_code,
                    error_message=rewritten_message,
                )[0]
                return
            status = "error"
            error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            error_message = error.message if error else None
            settlement.record_success = False
            settlement.account_health_error = _should_penalize_stream_error(error_code)
            raise
        finally:
            response_create_lease.release()
            input_tokens = usage.input_tokens if usage else None
            output_tokens = usage.output_tokens if usage else None
            cached_input_tokens = (
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            )
            reasoning_tokens = (
                usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
            )
            settlement.status = status
            settlement.model = model
            settlement.service_tier = service_tier
            settlement.input_tokens = input_tokens
            settlement.output_tokens = output_tokens
            settlement.cached_input_tokens = cached_input_tokens
            settlement.error_code = error_code
            settlement.error_message = error_message
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=response_id,
                model=model,
                latency_ms=int((time.monotonic() - start) * 1000),
                status=status,
                error_code=error_code,
                error_message=error_message,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_effort=reasoning_effort,
                transport=request_transport,
                service_tier=service_tier,
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
                latency_first_token_ms=latency_first_token_ms,
                session_id=session_id,
            )
            _maybe_log_proxy_service_tier_trace(
                "stream",
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
            )

    async def _write_request_log(
        self,
        *,
        account_id: str | None,
        provider_kind: str | None = None,
        routing_subject_id: str | None = None,
        api_key: ApiKeyData | None,
        request_id: str,
        model: str | None,
        latency_ms: int,
        status: str,
        latency_first_token_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        transport: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        route_class: str | None = None,
        upstream_request_id: str | None = None,
        rejection_reason: str | None = None,
        session_id: str | None = None,
    ) -> None:
        resolved_provider_kind = provider_kind
        if resolved_provider_kind is None and account_id is not None:
            resolved_provider_kind = CHATGPT_WEB_PROVIDER_KIND
        resolved_routing_subject_id = routing_subject_id or account_id
        with anyio.CancelScope(shield=True):
            try:
                async with self._repo_factory() as repos:
                    await repos.request_logs.add_log(
                        account_id=account_id,
                        provider_kind=resolved_provider_kind,
                        routing_subject_id=resolved_routing_subject_id,
                        api_key_id=api_key.id if api_key else None,
                        session_id=_normalize_session_id(session_id),
                        request_id=request_id,
                        model=model or "",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens,
                        reasoning_tokens=reasoning_tokens,
                        reasoning_effort=reasoning_effort,
                        transport=transport,
                        service_tier=service_tier,
                        requested_service_tier=requested_service_tier,
                        actual_service_tier=actual_service_tier,
                        latency_ms=latency_ms,
                        latency_first_token_ms=latency_first_token_ms,
                        status=status,
                        error_code=error_code,
                        error_message=error_message,
                        route_class=route_class,
                        upstream_request_id=upstream_request_id,
                        rejection_reason=rejection_reason,
                    )
            except Exception:
                logger.warning(
                    "Failed to persist request log account_id=%s request_id=%s",
                    account_id,
                    request_id,
                    exc_info=True,
                )

    async def _write_stream_preflight_error(
        self,
        *,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_id: str,
        model: str | None,
        start: float,
        error_code: str,
        error_message: str,
        reasoning_effort: str | None,
        service_tier: str | None,
        transport: str = _REQUEST_TRANSPORT_HTTP,
    ) -> None:
        await self._write_request_log(
            account_id=account_id,
            api_key=api_key,
            request_id=request_id,
            model=model,
            latency_ms=int((time.monotonic() - start) * 1000),
            status="error",
            error_code=error_code,
            error_message=error_message,
            reasoning_effort=reasoning_effort,
            transport=transport,
            service_tier=service_tier,
            requested_service_tier=service_tier,
        )

    async def _refresh_usage(self, repos: ProxyRepositories, accounts: list[Account]) -> None:
        adapter = cast(ChatGPTWebProviderAdapter, self._provider_adapter(CHATGPT_WEB_PROVIDER_KIND))
        await adapter.refresh_usage(
            repos,
            [self._chatgpt_provider_subject(account) for account in accounts],
        )

    async def _latest_usage_rows(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
        window: str,
    ) -> list[UsageWindowRow]:
        if not account_map:
            return []
        latest = await repos.usage.latest_by_account(window=window)
        return [
            UsageWindowRow(
                account_id=entry.account_id,
                used_percent=entry.used_percent,
                reset_at=entry.reset_at,
                window_minutes=entry.window_minutes,
                recorded_at=entry.recorded_at,
            )
            for entry in latest.values()
            if entry.account_id in account_map
        ]

    async def _latest_usage_entries(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
    ) -> list[UsageHistory]:
        if not account_map:
            return []
        latest = await repos.usage.latest_by_account()
        return [entry for entry in latest.values() if entry.account_id in account_map]

    async def _build_additional_rate_limits(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
        now_epoch: int,
    ) -> list[AdditionalRateLimitData]:
        """Build additional rate limit entries from AdditionalUsageRepository."""
        if not account_map:
            return []

        limit_names = await repos.additional_usage.list_limit_names(account_ids=list(account_map.keys()))
        additional_limits = []

        for limit_name in limit_names:
            # Fetch latest entries for this limit across all accounts
            latest_entries = await repos.additional_usage.latest_by_account(
                limit_name=limit_name,
                window="primary",
            )
            latest_secondary = await repos.additional_usage.latest_by_account(
                limit_name=limit_name,
                window="secondary",
            )

            # Filter to selected accounts
            filtered_entries = {
                account_id: entry for account_id, entry in latest_entries.items() if account_id in account_map
            }
            filtered_secondary = {
                account_id: entry for account_id, entry in latest_secondary.items() if account_id in account_map
            }

            if not filtered_entries and not filtered_secondary:
                continue

            first_entry = (
                next(iter(filtered_entries.values())) if filtered_entries else next(iter(filtered_secondary.values()))
            )
            metered_feature = first_entry.metered_feature

            window_snapshot = None
            avg_used_percent = None
            if filtered_entries:
                used_percents = [
                    entry.used_percent for entry in filtered_entries.values() if entry.used_percent is not None
                ]
                if used_percents:
                    avg_used_percent = sum(used_percents) / len(used_percents)
                    window_minutes_values = [e.window_minutes for e in filtered_entries.values() if e.window_minutes]
                    reset_at_values = [e.reset_at for e in filtered_entries.values() if e.reset_at is not None]

                    if window_minutes_values and reset_at_values:
                        window_minutes = max(window_minutes_values)
                        limit_window_seconds = int(window_minutes * 60)
                        reset_at = int(min(reset_at_values))
                        reset_after_seconds = max(0, reset_at - now_epoch)

                        window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, avg_used_percent))),
                            limit_window_seconds=limit_window_seconds,
                            reset_after_seconds=reset_after_seconds,
                            reset_at=reset_at,
                        )
                    else:
                        # Timing metadata absent — still emit used_percent
                        # so clients retain visibility into quota consumption.
                        window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, avg_used_percent))),
                        )

            secondary_window_snapshot = None
            if filtered_secondary:
                sec_used_percents = [e.used_percent for e in filtered_secondary.values() if e.used_percent is not None]
                if sec_used_percents:
                    sec_avg = sum(sec_used_percents) / len(sec_used_percents)
                    sec_window_values = [e.window_minutes for e in filtered_secondary.values() if e.window_minutes]
                    sec_reset_values = [e.reset_at for e in filtered_secondary.values() if e.reset_at is not None]

                    if sec_window_values and sec_reset_values:
                        sec_window_minutes = max(sec_window_values)
                        sec_limit_window_seconds = int(sec_window_minutes * 60)
                        sec_reset_at = int(min(sec_reset_values))
                        sec_reset_after_seconds = max(0, sec_reset_at - now_epoch)
                        secondary_window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, sec_avg))),
                            limit_window_seconds=sec_limit_window_seconds,
                            reset_after_seconds=sec_reset_after_seconds,
                            reset_at=sec_reset_at,
                        )
                    else:
                        secondary_window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, sec_avg))),
                        )

            rate_limit_details = None
            if avg_used_percent is not None or secondary_window_snapshot is not None:
                # Per-account availability: an account is available when
                # neither its primary nor secondary window is exhausted.
                # Pool is allowed when at least one account can serve.
                all_account_ids = set(filtered_entries.keys()) | set(filtered_secondary.keys())
                any_available = False
                for aid in all_account_ids:
                    pri_pct = filtered_entries[aid].used_percent if aid in filtered_entries else 0.0
                    sec_pct = filtered_secondary[aid].used_percent if aid in filtered_secondary else 0.0
                    if pri_pct < 100.0 and sec_pct < 100.0:
                        any_available = True
                        break
                rate_limit_details = RateLimitStatusDetailsData(
                    allowed=any_available,
                    limit_reached=not any_available,
                    primary_window=window_snapshot,
                    secondary_window=secondary_window_snapshot,
                )

            additional_limits.append(
                AdditionalRateLimitData(
                    quota_key=limit_name,
                    limit_name=first_entry.limit_name,
                    display_label=get_additional_display_label_for_quota_key(limit_name) or first_entry.limit_name,
                    metered_feature=metered_feature,
                    rate_limit=rate_limit_details,
                )
            )

        return additional_limits

    async def _ensure_fresh(
        self,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> Account:
        token = push_token_refresh_timeout_override(timeout_seconds)
        try:
            async with self._repo_factory() as repos:
                auth_manager = AuthManager(
                    repos.accounts,
                    acquire_refresh_admission=self._get_work_admission().acquire_token_refresh,
                )
                return await auth_manager.ensure_fresh(account, force=force)
        finally:
            pop_token_refresh_timeout_override(token)

    async def _ensure_fresh_with_budget(
        self,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> Account:
        parameters = inspect.signature(self._ensure_fresh).parameters
        if "timeout_seconds" in parameters:
            return await self._ensure_fresh(account, force=force, timeout_seconds=timeout_seconds)
        return await self._ensure_fresh(account, force=force)

    async def _select_account_with_budget(
        self,
        deadline: float,
        *,
        request_id: str,
        kind: str,
        request_stage: str = "first_turn",
        api_key: ApiKeyData | None = None,
        sticky_key: str | None = None,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
        sticky_budget_reallocation_enabled: bool = True,
        prefer_earlier_reset_accounts: bool = False,
        routing_strategy: RoutingStrategy = "capacity_weighted",
        model: str | None = None,
        additional_limit_name: str | None = None,
        exclude_account_ids: Collection[str] | None = None,
        preferred_account_id: str | None = None,
    ) -> AccountSelection:
        remaining_budget = _remaining_budget_seconds(deadline)
        if remaining_budget <= 0:
            logger.warning(
                "%s request budget exhausted before account selection request_id=%s", kind.title(), request_id
            )
            _raise_proxy_budget_exhausted()
        scoped_account_ids = (
            set(api_key.assigned_account_ids)
            if api_key is not None and api_key.account_assignment_scope_enabled
            else None
        )
        excluded_account_ids_set = set(exclude_account_ids or ())
        try:
            with anyio.fail_after(remaining_budget):
                settings = await get_settings_cache().get()
                if (
                    preferred_account_id is not None
                    and preferred_account_id not in excluded_account_ids_set
                    and (scoped_account_ids is None or preferred_account_id in scoped_account_ids)
                ):
                    preferred_selection = await self._load_balancer.select_account(
                        sticky_key=sticky_key,
                        sticky_kind=sticky_kind,
                        reallocate_sticky=reallocate_sticky,
                        sticky_max_age_seconds=sticky_max_age_seconds,
                        sticky_budget_reallocation_enabled=sticky_budget_reallocation_enabled,
                        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                        routing_strategy=routing_strategy,
                        model=model,
                        additional_limit_name=additional_limit_name,
                        account_ids={preferred_account_id},
                        budget_threshold_pct=settings.sticky_reallocation_budget_threshold_pct,
                    )
                    if preferred_selection.account is not None:
                        logger.info(
                            "Selected preferred account request_id=%s kind=%s request_stage=%s account_id=%s",
                            request_id,
                            kind,
                            request_stage,
                            preferred_account_id,
                        )
                        return preferred_selection
                selection = await self._load_balancer.select_account(
                    sticky_key=sticky_key,
                    sticky_kind=sticky_kind,
                    reallocate_sticky=reallocate_sticky,
                    sticky_max_age_seconds=sticky_max_age_seconds,
                    sticky_budget_reallocation_enabled=sticky_budget_reallocation_enabled,
                    prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                    model=model,
                    additional_limit_name=additional_limit_name,
                    account_ids=scoped_account_ids,
                    exclude_account_ids=excluded_account_ids_set,
                    budget_threshold_pct=settings.sticky_reallocation_budget_threshold_pct,
                )
                if selection.account is not None and selection.account.id in excluded_account_ids_set:
                    return AccountSelection(
                        account=None,
                        error_message="No active accounts available",
                        error_code="no_accounts",
                    )
                return selection
        except TimeoutError:
            logger.warning("%s account selection exceeded request budget request_id=%s", kind.title(), request_id)
            _raise_proxy_budget_exhausted()

    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None:
        error = _parse_openai_error(exc.payload)
        code = _normalize_error_code(
            error.code if error else None,
            error.type if error else None,
        )
        if _is_account_neutral_error_code(code):
            return
        await self._handle_stream_error(
            account,
            _upstream_error_from_openai(error),
            code,
            http_status=exc.status_code,
        )

    async def _handle_stream_error(
        self,
        account: Account,
        error: UpstreamError,
        code: str,
        http_status: int | None = None,
    ) -> ClassifiedFailure:
        classified = classify_upstream_failure(
            error_code=code,
            error=error,
            http_status=http_status,
            phase="first_event",
        )
        if _is_account_neutral_error_code(code):
            return classified
        if classified["failure_class"] == "rate_limit":
            await self._load_balancer.mark_rate_limit(account, error)
        elif classified["failure_class"] == "quota":
            await self._load_balancer.mark_quota_exceeded(account, error)
        elif code in PERMANENT_FAILURE_CODES:
            await self._load_balancer.mark_permanent_failure(account, code)
        else:
            await self._load_balancer.record_error(account)
            logger.info(
                "Recorded transient account error account_id=%s request_id=%s code=%s",
                account.id,
                get_request_id(),
                code,
            )
        return classified


class _RetryableStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error


class _TransientStreamError(Exception):
    """Transient upstream error (e.g. 500 server_error) — retry on same account first."""

    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error


class _TerminalStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error


@dataclass
class _StreamSettlement:
    """Populated by _stream_once(), consumed by _stream_with_retry() for reservation settlement."""

    status: str = "success"
    model: str = ""
    service_tier: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    error: UpstreamError | None = None
    account_health_error: bool = False
    record_success: bool = True


def _stream_settlement_error_payload(settlement: _StreamSettlement) -> UpstreamError:
    if settlement.error is not None:
        return settlement.error
    payload: UpstreamError = {}
    if settlement.error_message:
        payload["message"] = settlement.error_message
    else:
        payload["message"] = "Upstream error"
    return payload


def _should_penalize_stream_error(code: str | None) -> bool:
    if code is None:
        return False
    return code in _ACCOUNT_RECOVERY_RETRY_CODES


def _is_account_neutral_error_code(code: str | None) -> bool:
    return code in {"proxy_overloaded", "proxy_unavailable"}


@dataclass
class _WebSocketRequestState:
    request_id: str
    model: str | None
    service_tier: str | None
    reasoning_effort: str | None
    api_key_reservation: ApiKeyUsageReservationData | None
    started_at: float
    latency_first_token_ms: int | None = None
    request_log_id: str | None = None
    requested_service_tier: str | None = None
    actual_service_tier: str | None = None
    response_id: str | None = None
    awaiting_response_created: bool = False
    event_queue: asyncio.Queue[str | None] | None = None
    transport: str = _REQUEST_TRANSPORT_WEBSOCKET
    api_key: ApiKeyData | None = None
    request_text: str | None = None
    replay_count: int = 0
    skip_request_log: bool = False
    previous_response_id: str | None = None
    session_id: str | None = None
    proxy_injected_previous_response_id: bool = False
    fresh_upstream_request_text: str | None = None
    # True only when ``fresh_upstream_request_text`` contains a *safe* pre-
    # injection form of this request that can be replayed as a fresh turn.
    # Durable-anchor injection captures the original unanchored full-resend
    # payload, so dropping the anchor and replaying is equivalent to the
    # client's own retry. Session-level anchor injection does **not** set
    # this: the original payload may have omitted history the conversation
    # depended on (for example a single-item follow-up whose context came
    # entirely from the injected anchor), and dropping the anchor there
    # would silently turn a continuation into a context-free fresh turn.
    fresh_upstream_request_is_retry_safe: bool = False
    request_stage: str = "first_turn"
    preferred_account_id: str | None = None
    error_code_override: str | None = None
    error_message_override: str | None = None
    error_type_override: str | None = None
    error_param_override: str | None = None
    error_http_status_override: int | None = None
    response_create_gate_acquired: bool = False
    response_create_gate: asyncio.Semaphore | None = None
    response_create_admission: AdmissionLease | None = None
    affinity_policy: _AffinityPolicy = field(default_factory=_AffinityPolicy)
    input_item_count: int = 0
    input_full_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class _HTTPBridgeSessionKey:
    affinity_kind: str
    affinity_key: str
    api_key_id: str | None
    strength: Literal["hard", "soft"] | None = None

    def __post_init__(self) -> None:
        strength = self.strength
        if strength is None:
            strength = "hard" if self.affinity_kind in _HARD_HTTP_BRIDGE_AFFINITY_KINDS else "soft"
        object.__setattr__(self, "strength", strength)


_HARD_HTTP_BRIDGE_AFFINITY_KINDS = frozenset({"turn_state_header", "session_header"})


@dataclass(frozen=True, slots=True)
class _HTTPBridgeOwnerForward:
    owner_instance: str
    owner_endpoint: str
    key: _HTTPBridgeSessionKey


@dataclass(slots=True)
class _HTTPBridgeSession:
    key: _HTTPBridgeSessionKey
    headers: dict[str, str]
    affinity: _AffinityPolicy
    request_model: str | None
    account: Account
    upstream: UpstreamResponsesWebSocket
    upstream_control: _WebSocketUpstreamControl
    pending_requests: deque[_WebSocketRequestState]
    pending_lock: anyio.Lock
    response_create_gate: asyncio.Semaphore
    queued_request_count: int
    last_used_at: float
    idle_ttl_seconds: float
    api_key: ApiKeyData | None = None
    codex_session: bool = False
    prewarmed: bool = False
    prewarm_lock: anyio.Lock | None = None
    upstream_turn_state: str | None = None
    downstream_turn_state: str | None = None
    downstream_turn_state_aliases: set[str] = field(default_factory=set)
    previous_response_ids: set[str] = field(default_factory=set)
    last_completed_input_count: int = 0
    last_completed_response_id: str | None = None
    last_completed_input_prefix_fingerprint: str | None = None
    durable_session_id: str | None = None
    durable_owner_epoch: int | None = None
    upstream_reader: asyncio.Task[None] | None = None
    closed: bool = False


@dataclass(slots=True)
class _WebSocketUpstreamControl:
    reconnect_requested: bool = False
    suppress_downstream_event: bool = False
    replay_request_state: _WebSocketRequestState | None = None
    downstream_texts: list[str] | None = None


@dataclass(slots=True)
class _DownstreamWebSocketActivity:
    last_activity_at: float = field(default_factory=time.monotonic)

    def mark(self) -> None:
        self.last_activity_at = time.monotonic()


@dataclass(slots=True)
class _PreparedWebSocketRequest:
    text_data: str
    request_state: _WebSocketRequestState
    affinity_policy: _AffinityPolicy


@dataclass(frozen=True, slots=True)
class _WebSocketReceiveTimeout:
    timeout_seconds: float
    error_code: str
    error_message: str
    fail_all_pending: bool = False


def _event_type_from_payload(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None:
        return event.type
    if payload is None:
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    return None


def _http_error_status_from_payload(payload: dict[str, JsonValue] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if isinstance(status, int):
        return status
    return None


def _openai_error_envelope_from_response_failed_payload(
    payload: dict[str, JsonValue] | None,
) -> OpenAIErrorEnvelope:
    default_envelope = openai_error("upstream_error", "Upstream error")
    if not isinstance(payload, dict):
        return default_envelope
    response_payload = payload.get("response")
    if not isinstance(response_payload, dict):
        return default_envelope
    error_payload = response_payload.get("error")
    if not isinstance(error_payload, dict):
        return default_envelope

    message_value = error_payload.get("message")
    if isinstance(message_value, str) and message_value.strip():
        message = message_value.strip()
    else:
        message = "Upstream error"

    code_value = error_payload.get("code")
    code = code_value.strip() if isinstance(code_value, str) and code_value.strip() else "upstream_error"

    type_value = error_payload.get("type")
    error_type = type_value.strip() if isinstance(type_value, str) and type_value.strip() else "server_error"

    envelope = openai_error(code, message, error_type)
    param_value = error_payload.get("param")
    if isinstance(param_value, str) and param_value.strip():
        envelope["error"]["param"] = param_value.strip()
    error_detail = envelope["error"]
    plan_type = error_payload.get("plan_type")
    if plan_type is not None:
        error_detail["plan_type"] = str(plan_type)
    resets_at = error_payload.get("resets_at")
    if isinstance(resets_at, int | float):
        error_detail["resets_at"] = resets_at
    resets_in = error_payload.get("resets_in_seconds")
    if isinstance(resets_in, int | float):
        error_detail["resets_in_seconds"] = resets_in
    return envelope


def _normalize_http_bridge_error_event(
    *,
    event: OpenAIEvent | None,
    payload: dict[str, JsonValue] | None,
    request_state: _WebSocketRequestState | None,
) -> tuple[str, dict[str, JsonValue] | None, OpenAIEvent | None, str]:
    error_code_value: str | None = None
    error_type_value: str | None = None
    error_message_value: str | None = None
    error_param_value: str | None = None
    rate_limit_metadata: OpenAIErrorDetail = {}

    if event is not None and event.error is not None:
        error_code_value = event.error.code
        error_type_value = event.error.type
        error_message_value = event.error.message
        error_param_value = event.error.param
    elif isinstance(payload, dict):
        payload_error = payload.get("error")
        if isinstance(payload_error, dict):
            code_value = payload_error.get("code")
            if isinstance(code_value, str):
                stripped = code_value.strip()
                if stripped:
                    error_code_value = stripped
            type_value = payload_error.get("type")
            if isinstance(type_value, str):
                stripped = type_value.strip()
                if stripped:
                    error_type_value = stripped
            message_value = payload_error.get("message")
            if isinstance(message_value, str):
                stripped = message_value.strip()
                if stripped:
                    error_message_value = stripped
            param_value = payload_error.get("param")
            if isinstance(param_value, str):
                stripped = param_value.strip()
                if stripped:
                    error_param_value = stripped

    if isinstance(payload, dict):
        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            plan_type = raw_error.get("plan_type")
            if isinstance(plan_type, str):
                rate_limit_metadata["plan_type"] = plan_type
            resets_at = raw_error.get("resets_at")
            if isinstance(resets_at, int | float):
                rate_limit_metadata["resets_at"] = resets_at
            resets_in = raw_error.get("resets_in_seconds")
            if isinstance(resets_in, int | float):
                rate_limit_metadata["resets_in_seconds"] = resets_in

    normalized_error_code = _normalize_error_code(error_code_value, error_type_value) or "upstream_error"
    normalized_error_type = error_type_value or "server_error"
    normalized_error_message = error_message_value or "Upstream error"

    normalized_response_id = None
    if request_state is not None:
        normalized_response_id = request_state.response_id or request_state.request_id

    normalized_event = response_failed_event(
        normalized_error_code,
        normalized_error_message,
        error_type=normalized_error_type,
        response_id=normalized_response_id,
        error_param=error_param_value,
    )
    if rate_limit_metadata:
        normalized_event["response"]["error"].update(rate_limit_metadata)
    normalized_event_block = format_sse_event(normalized_event)
    normalized_payload = parse_sse_data_json(normalized_event_block)
    parsed_event = parse_sse_event(normalized_event_block)
    return normalized_event_block, normalized_payload, parsed_event, "response.failed"


def _websocket_response_id(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None and event.response is not None and event.response.id:
        return event.response.id
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    response_id = response.get("id")
    if not isinstance(response_id, str):
        return None
    stripped = response_id.strip()
    return stripped or None


def _websocket_event_error_code(event_type: str | None, payload: dict[str, JsonValue] | None) -> str | None:
    error = _websocket_event_error_payload(event_type, payload)
    if not isinstance(error, dict):
        return None
    code_value = error.get("code")
    if not isinstance(code_value, str):
        return None
    stripped = code_value.strip()
    return stripped or None


def _websocket_event_error_type(event_type: str | None, payload: dict[str, JsonValue] | None) -> str | None:
    error = _websocket_event_error_payload(event_type, payload)
    if not isinstance(error, dict):
        return None
    type_value = error.get("type")
    if not isinstance(type_value, str):
        return None
    stripped = type_value.strip()
    return stripped or None


def _websocket_event_error_param(event_type: str | None, payload: dict[str, JsonValue] | None) -> str | None:
    error = _websocket_event_error_payload(event_type, payload)
    if not isinstance(error, dict):
        return None
    param_value = error.get("param")
    if not isinstance(param_value, str):
        return None
    stripped = param_value.strip()
    return stripped or None


def _websocket_event_error_message(event_type: str | None, payload: dict[str, JsonValue] | None) -> str | None:
    error = _websocket_event_error_payload(event_type, payload)
    if not isinstance(error, dict):
        return None
    message_value = error.get("message")
    if not isinstance(message_value, str):
        return None
    stripped = message_value.strip()
    return stripped or None


def _is_previous_response_not_found_message(message: str | None) -> bool:
    if message is None:
        return False
    normalized = " ".join(message.lower().split())
    return "previous response" in normalized and "not found" in normalized


def _previous_response_id_from_not_found_message(message: str | None) -> str | None:
    if message is None:
        return None
    normalized = " ".join(message.split())
    match = re.search(
        r"""previous\s+response\s+with\s+id\s+['"](?P<response_id>[^'"]+)['"]\s+not\s+found""",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    response_id = match.group("response_id").strip()
    return response_id or None


def _message_mentions_previous_response_id(message: str | None, previous_response_id: str | None) -> bool:
    if message is None or previous_response_id is None:
        return False
    normalized_message = " ".join(message.split())
    normalized_previous_response_id = previous_response_id.strip()
    if not normalized_previous_response_id:
        return False
    identifier_pattern = re.escape(normalized_previous_response_id)
    return (
        re.search(
            rf"(?<![A-Za-z0-9_-]){identifier_pattern}(?![A-Za-z0-9_-])",
            normalized_message,
        )
        is not None
    )


def _normalize_session_id(session_id: str | None) -> str | None:
    if not isinstance(session_id, str):
        return None
    stripped = session_id.strip()
    return stripped or None


def _websocket_precreated_retry_error_code(
    request_state: _WebSocketRequestState | None,
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    has_other_pending_requests: bool,
) -> str | None:
    if request_state is None:
        return None
    if has_other_pending_requests:
        return None
    if request_state.response_id is not None:
        return None
    if not request_state.awaiting_response_created:
        return None
    if not request_state.request_text:
        return None
    if request_state.replay_count >= 1:
        return None
    if event_type not in {"error", "response.failed"}:
        return None

    error_code = _normalize_error_code(
        _websocket_event_error_code(event_type, payload),
        _websocket_event_error_type(event_type, payload),
    )
    error_param = _websocket_event_error_param(event_type, payload)
    error_message = _websocket_event_error_message(event_type, payload)
    if _is_previous_response_not_found_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        return "stream_incomplete"
    if error_code not in _WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES:
        return None
    return error_code


async def _pop_replayable_precreated_websocket_request_state(
    pending_requests: deque[_WebSocketRequestState],
    *,
    pending_lock: anyio.Lock,
) -> _WebSocketRequestState | None:
    async with pending_lock:
        if len(pending_requests) != 1:
            return None
        request_state = pending_requests[0]
        if request_state.response_id is not None:
            return None
        if not request_state.awaiting_response_created:
            return None
        if not request_state.request_text:
            return None
        if request_state.replay_count >= 1:
            return None
        pending_requests.popleft()
    request_state.replay_count += 1
    request_state.awaiting_response_created = True
    request_state.response_id = None
    return request_state


def _is_previous_response_not_found_error(
    *,
    code: str | None,
    param: str | None,
    message: str | None,
) -> bool:
    if code == "previous_response_not_found":
        return True
    if code != "invalid_request_error" or param != "previous_response_id":
        return False
    return _is_previous_response_not_found_message(message)


def _websocket_event_error_payload(
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    if not isinstance(payload, dict):
        return None
    if event_type == "error":
        error = payload.get("error")
    elif event_type == "response.failed":
        response = payload.get("response")
        error = response.get("error") if isinstance(response, dict) else None
    else:
        return None
    return cast(dict[str, JsonValue], error) if isinstance(error, dict) else None


def _maybe_rewrite_websocket_previous_response_not_found_event(
    *,
    request_state: _WebSocketRequestState,
    event: OpenAIEvent | None,
    payload: dict[str, JsonValue] | None,
    event_type: str | None,
    upstream_control: _WebSocketUpstreamControl,
    original_text: str,
) -> tuple[OpenAIEvent | None, dict[str, JsonValue] | None, str | None, str]:
    if request_state.previous_response_id is None:
        return event, payload, event_type, original_text

    error_code = _websocket_event_error_code(event_type, payload)
    error_param = _websocket_event_error_param(event_type, payload)
    error_message = _websocket_event_error_message(event_type, payload)
    should_rewrite = _is_previous_response_not_found_error(
        code=error_code,
        param=error_param,
        message=error_message,
    )
    if not should_rewrite:
        return event, payload, event_type, original_text

    upstream_control.reconnect_requested = True
    _record_continuity_fail_closed(
        surface="websocket_stream",
        reason="previous_response_not_found",
        previous_response_id=request_state.previous_response_id,
        session_id=request_state.session_id,
        upstream_error_code=error_code,
    )
    rewritten_event_payload = response_failed_event(
        "stream_incomplete",
        "Upstream websocket closed before response.completed",
        error_type="server_error",
        response_id=request_state.response_id or request_state.request_id,
    )
    rewritten_text = json.dumps(rewritten_event_payload, ensure_ascii=True, separators=(",", ":"))
    rewritten_event_block = format_sse_event(rewritten_event_payload)
    rewritten_payload = parse_sse_data_json(rewritten_event_block)
    rewritten_event = parse_sse_event(rewritten_event_block)
    rewritten_event_type = _event_type_from_payload(rewritten_event, rewritten_payload)
    return rewritten_event, rewritten_payload, rewritten_event_type, rewritten_text


def _rewrite_websocket_previous_response_owner_unavailable_event(
    *,
    request_state: _WebSocketRequestState,
) -> tuple[OpenAIEvent | None, dict[str, JsonValue] | None, str | None, str]:
    _record_continuity_fail_closed(
        surface="websocket_stream",
        reason="owner_account_unavailable",
        previous_response_id=request_state.previous_response_id,
        session_id=request_state.session_id,
    )
    rewritten_event_payload = response_failed_event(
        "upstream_unavailable",
        "Previous response owner account is unavailable; retry later.",
        error_type="server_error",
        response_id=request_state.response_id or request_state.request_id,
    )
    rewritten_text = json.dumps(rewritten_event_payload, ensure_ascii=True, separators=(",", ":"))
    rewritten_event_block = format_sse_event(rewritten_event_payload)
    rewritten_payload = parse_sse_data_json(rewritten_event_block)
    rewritten_event = parse_sse_event(rewritten_event_block)
    rewritten_event_type = _event_type_from_payload(rewritten_event, rewritten_payload)
    return rewritten_event, rewritten_payload, rewritten_event_type, rewritten_text


def _sanitize_websocket_connect_failure(
    *,
    request_state: _WebSocketRequestState,
    status_code: int,
    payload: OpenAIErrorEnvelope,
    error_code: str,
    error_message: str,
) -> tuple[int, OpenAIErrorEnvelope, str, str]:
    if request_state.previous_response_id is None:
        return status_code, payload, error_code, error_message

    parsed_error = _parse_openai_error(payload)
    normalized_code = _normalize_error_code(
        parsed_error.code if parsed_error else error_code,
        parsed_error.type if parsed_error else None,
    )
    normalized_message = parsed_error.message if parsed_error and parsed_error.message else error_message
    if not _is_previous_response_not_found_error(
        code=normalized_code,
        param=parsed_error.param if parsed_error else None,
        message=normalized_message,
    ):
        return status_code, payload, error_code, error_message

    rewritten_message = "Upstream websocket closed before response.completed"
    _record_continuity_fail_closed(
        surface="websocket_connect",
        reason="previous_response_not_found",
        previous_response_id=request_state.previous_response_id,
        session_id=request_state.session_id,
        upstream_error_code=normalized_code,
    )
    return (
        502,
        openai_error(
            "stream_incomplete",
            rewritten_message,
            error_type="server_error",
        ),
        "stream_incomplete",
        rewritten_message,
    )


def _rewrite_previous_response_stream_error(
    *,
    previous_response_id: str | None,
    preferred_account_id: str | None,
    error_code: str | None,
    error_type: str | None,
    error_message: str | None,
    error_param: str | None,
) -> tuple[str, str, str | None] | None:
    if previous_response_id is None:
        return None
    if _is_previous_response_not_found_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        _record_continuity_fail_closed(
            surface="http_stream",
            reason="previous_response_not_found",
            previous_response_id=previous_response_id,
            upstream_error_code=error_code,
        )
        return (
            "stream_incomplete",
            "Upstream websocket closed before response.completed",
            None,
        )
    normalized_code = _normalize_error_code(error_code, error_type)
    if preferred_account_id is not None and normalized_code in _ACCOUNT_RECOVERY_RETRY_CODES:
        _record_continuity_fail_closed(
            surface="http_stream",
            reason="owner_account_unavailable",
            previous_response_id=previous_response_id,
            upstream_error_code=normalized_code,
        )
        return (
            "upstream_unavailable",
            "Previous response owner account is unavailable; retry later.",
            normalized_code,
        )
    return None


def _build_rewritten_stream_response_failed_event(
    *,
    response_id: str,
    error_code: str,
    error_message: str,
) -> tuple[str, OpenAIEvent | None, dict[str, JsonValue] | None, str | None]:
    rewritten_event_payload = response_failed_event(
        error_code,
        error_message,
        error_type="server_error",
        response_id=response_id,
    )
    rewritten_event_block = format_sse_event(rewritten_event_payload)
    rewritten_payload = parse_sse_data_json(rewritten_event_block)
    rewritten_event = parse_sse_event(rewritten_event_block)
    rewritten_event_type = _event_type_from_payload(rewritten_event, rewritten_payload)
    return rewritten_event_block, rewritten_event, rewritten_payload, rewritten_event_type


def _find_websocket_request_state_by_response_id(
    pending_requests: deque[_WebSocketRequestState],
    response_id: str,
) -> _WebSocketRequestState | None:
    for request_state in pending_requests:
        if request_state.response_id == response_id:
            return request_state
    return None


def _assign_websocket_response_id(
    pending_requests: deque[_WebSocketRequestState],
    response_id: str | None,
) -> _WebSocketRequestState | None:
    if response_id is None:
        return None
    existing = _find_websocket_request_state_by_response_id(pending_requests, response_id)
    if existing is not None:
        return existing
    for request_state in pending_requests:
        if request_state.response_id is None:
            request_state.response_id = response_id
            return request_state
    return None


def _match_websocket_request_state_for_anonymous_event(
    pending_requests: deque[_WebSocketRequestState],
    *,
    prefer_previous_response_not_found: bool,
    previous_response_id_hint: str | None = None,
    error_message: str | None = None,
) -> _WebSocketRequestState | None:
    if prefer_previous_response_not_found:
        return _match_websocket_request_state_for_previous_response_error(
            pending_requests,
            previous_response_id_hint=previous_response_id_hint,
            error_message=error_message,
        )

    if len(pending_requests) == 1:
        return pending_requests[0]

    unresolved_requests = [request_state for request_state in pending_requests if request_state.response_id is None]
    if len(unresolved_requests) == 1:
        return unresolved_requests[0]
    return None


def _match_websocket_request_state_for_precreated_terminal_event(
    pending_requests: deque[_WebSocketRequestState],
) -> _WebSocketRequestState | None:
    unresolved_requests = [
        request_state
        for request_state in pending_requests
        if request_state.response_id is None and request_state.awaiting_response_created
    ]
    if len(unresolved_requests) == 1:
        return unresolved_requests[0]
    return None


def _match_websocket_request_state_for_previous_response_error(
    pending_requests: deque[_WebSocketRequestState],
    *,
    previous_response_id_hint: str | None = None,
    error_message: str | None = None,
) -> _WebSocketRequestState | None:
    matching_requests = _matching_websocket_request_states_for_previous_response_error(
        pending_requests,
        previous_response_id_hint=previous_response_id_hint,
        error_message=error_message,
    )
    if len(matching_requests) == 1:
        return matching_requests[0]
    return None


def _matching_websocket_request_states_for_previous_response_error(
    pending_requests: deque[_WebSocketRequestState],
    *,
    previous_response_id_hint: str | None = None,
    error_message: str | None = None,
) -> list[_WebSocketRequestState]:
    followup_requests = [
        request_state for request_state in pending_requests if request_state.previous_response_id is not None
    ]
    if not followup_requests:
        return []
    if previous_response_id_hint is not None:
        matching_requests = [
            request_state
            for request_state in followup_requests
            if request_state.previous_response_id == previous_response_id_hint
        ]
        if matching_requests:
            return matching_requests
    if error_message is not None:
        matching_requests = [
            request_state
            for request_state in followup_requests
            if _message_mentions_previous_response_id(error_message, request_state.previous_response_id)
        ]
        if matching_requests:
            return matching_requests
    unresolved_followups = [request_state for request_state in followup_requests if request_state.response_id is None]
    if len(unresolved_followups) == 1:
        return unresolved_followups
    if len(unresolved_followups) > 1:
        unique_previous_response_ids = {
            request_state.previous_response_id
            for request_state in unresolved_followups
            if request_state.previous_response_id
        }
        if len(unique_previous_response_ids) == 1:
            return unresolved_followups
    return []


def _pop_matching_websocket_request_states(
    pending_requests: deque[_WebSocketRequestState],
    matching_requests: list[_WebSocketRequestState],
) -> list[_WebSocketRequestState]:
    popped_requests: list[_WebSocketRequestState] = []
    for request_state in matching_requests:
        try:
            pending_requests.remove(request_state)
        except ValueError:
            continue
        popped_requests.append(request_state)
    return popped_requests


def _build_stream_incomplete_terminal_event_for_request(
    request_state: _WebSocketRequestState,
) -> tuple[str, str, OpenAIEvent | None, dict[str, JsonValue] | None, str | None]:
    event_block, event, payload, event_type = _build_rewritten_stream_response_failed_event(
        response_id=request_state.response_id or request_state.request_id,
        error_code="stream_incomplete",
        error_message="Upstream websocket closed before response.completed",
    )
    downstream_text = json.dumps(
        cast(
            dict[str, JsonValue],
            response_failed_event(
                "stream_incomplete",
                "Upstream websocket closed before response.completed",
                error_type="server_error",
                response_id=request_state.response_id or request_state.request_id,
            ),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return downstream_text, event_block, event, payload, event_type


def _release_websocket_response_create_gate(
    request_state: _WebSocketRequestState,
    response_create_gate: asyncio.Semaphore,
) -> None:
    if request_state.response_create_admission is not None:
        request_state.response_create_admission.release()
        request_state.response_create_admission = None
    request_state.awaiting_response_created = False
    request_state.response_create_gate = None
    if not request_state.response_create_gate_acquired:
        return
    request_state.response_create_gate_acquired = False
    response_create_gate.release()


def _response_create_too_large_error_envelope(
    actual_bytes: int,
    max_bytes: int,
) -> OpenAIErrorEnvelope:
    payload = openai_error(
        "payload_too_large",
        (
            "response.create is too large for upstream websocket "
            f"({actual_bytes} bytes > {max_bytes} bytes). "
            "Reduce historical images/screenshots or compact the thread."
        ),
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "input"
    return payload


def _slim_response_create_payload_for_upstream(
    payload: dict[str, JsonValue],
    *,
    max_bytes: int,
) -> tuple[dict[str, JsonValue], dict[str, int] | None]:
    input_value = payload.get("input")
    if not isinstance(input_value, list) or not input_value:
        return payload, None

    input_items = cast(list[JsonValue], deepcopy(input_value))
    preserve_from = _response_create_recent_suffix_start(input_items)
    historical = input_items[:preserve_from]
    recent = input_items[preserve_from:]

    tool_outputs_slimmed = 0
    images_slimmed = 0

    slimmed_historical: list[JsonValue] = []
    for item in historical:
        slimmed_item, item_tool_outputs_slimmed, item_images_slimmed = _slim_historical_response_input_item(item)
        tool_outputs_slimmed += item_tool_outputs_slimmed
        images_slimmed += item_images_slimmed
        slimmed_historical.append(slimmed_item)

    candidate_payload = dict(payload)
    candidate_payload["input"] = slimmed_historical + recent

    if tool_outputs_slimmed == 0 and images_slimmed == 0:
        return payload, None

    return candidate_payload, {
        "historical_tool_outputs_slimmed": tool_outputs_slimmed,
        "historical_images_slimmed": images_slimmed,
    }


def _response_create_recent_suffix_start(input_items: list[JsonValue]) -> int:
    last_user_index: int | None = None
    for index, item in enumerate(input_items):
        if not is_json_mapping(item):
            continue
        if item.get("role") == "user":
            last_user_index = index
    if last_user_index is not None:
        return last_user_index
    return 0


def _slim_historical_response_input_item(item: JsonValue) -> tuple[JsonValue, int, int]:
    if not is_json_mapping(item):
        return item, 0, 0

    item_mapping = dict(cast(dict[str, JsonValue], deepcopy(item)))
    tool_outputs_slimmed = 0
    images_slimmed = 0

    item_type = item_mapping.get("type")
    if item_type == "function_call_output":
        output = item_mapping.get("output")
        output_text = output if isinstance(output, str) else None
        if output_text is not None and _should_slim_historical_tool_output(output_text):
            item_mapping["output"] = _RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE.format(
                bytes=len(output_text.encode("utf-8"))
            )
            tool_outputs_slimmed += 1

    content = item_mapping.get("content")
    slimmed_content, content_images_slimmed = _slim_historical_response_content(content)
    if content_images_slimmed > 0:
        item_mapping["content"] = slimmed_content
        images_slimmed += content_images_slimmed

    if item_mapping.get("type") == "input_image" and _is_inline_image_reference(item_mapping.get("image_url")):
        return _response_create_inline_image_notice_item(), tool_outputs_slimmed, images_slimmed + 1

    return item_mapping, tool_outputs_slimmed, images_slimmed


def _slim_historical_response_content(content: JsonValue) -> tuple[JsonValue, int]:
    if is_json_mapping(content):
        return _slim_historical_response_content_part(content)
    if not isinstance(content, list):
        return content, 0

    slimmed_parts: list[JsonValue] = []
    images_slimmed = 0
    for part in content:
        slimmed_part, part_images_slimmed = _slim_historical_response_content_part(part)
        slimmed_parts.append(slimmed_part)
        images_slimmed += part_images_slimmed
    return slimmed_parts, images_slimmed


def _slim_historical_response_content_part(part: JsonValue) -> tuple[JsonValue, int]:
    if not is_json_mapping(part):
        return part, 0

    part_mapping = dict(cast(dict[str, JsonValue], deepcopy(part)))
    part_type = part_mapping.get("type")
    if part_type == "input_image" and _is_inline_image_reference(part_mapping.get("image_url")):
        return _response_create_inline_image_notice_part(), 1

    if part_type == "image_url":
        image_url_value = part_mapping.get("image_url")
        if is_json_mapping(image_url_value):
            image_url = image_url_value.get("url")
        else:
            image_url = image_url_value
        if _is_inline_image_reference(image_url):
            return _response_create_inline_image_notice_part(), 1

    return part_mapping, 0


def _response_create_inline_image_notice_part() -> dict[str, JsonValue]:
    return {"type": "input_text", "text": _RESPONSE_CREATE_IMAGE_OMISSION_NOTICE}


def _response_create_inline_image_notice_item() -> dict[str, JsonValue]:
    return {"role": "user", "content": [_response_create_inline_image_notice_part()]}


def _response_create_history_omission_notice_item(count: int) -> dict[str, JsonValue]:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": _RESPONSE_CREATE_HISTORY_OMISSION_NOTICE.format(count=count),
            }
        ],
    }


def _is_inline_image_reference(value: JsonValue) -> bool:
    return isinstance(value, str) and value.startswith("data:image/")


def _should_slim_historical_tool_output(output: str) -> bool:
    return "data:image/" in output or len(output.encode("utf-8")) > 32 * 1024


def _enforce_response_create_size_limit(request_state: _WebSocketRequestState) -> None:
    request_text = request_state.request_text
    if not request_text:
        return

    payload_bytes = request_text.encode("utf-8")
    payload_size = len(payload_bytes)
    if payload_size > _UPSTREAM_RESPONSE_CREATE_WARN_BYTES:
        logger.warning(
            (
                "Large response.create prepared request_id=%s request_log_id=%s "
                "transport=%s bytes=%s previous_response_id=%s"
            ),
            request_state.request_id,
            request_state.request_log_id,
            request_state.transport,
            payload_size,
            request_state.previous_response_id,
        )
    if payload_size <= _UPSTREAM_RESPONSE_CREATE_MAX_BYTES:
        return

    payload = _response_create_too_large_error_envelope(payload_size, _UPSTREAM_RESPONSE_CREATE_MAX_BYTES)
    error = payload["error"]
    _write_response_create_dump(
        request_state,
        account_id_value=None,
        error_code=cast(str, error.get("code") or "payload_too_large"),
        error_message=error.get("message"),
        log_prefix="guarded",
    )
    raise ProxyResponseError(
        413,
        payload,
        failure_phase="validation",
        failure_detail=f"response.create_bytes={payload_size}",
    )


def _maybe_dump_oversized_response_create_request(
    request_state: _WebSocketRequestState,
    *,
    account_id_value: str | None,
    error_code: str,
    error_message: str | None,
) -> None:
    if not _should_dump_oversized_response_create(error_code, error_message):
        return
    _write_response_create_dump(
        request_state,
        account_id_value=account_id_value,
        error_code=error_code,
        error_message=error_message,
        log_prefix="oversized",
    )


def _write_response_create_dump(
    request_state: _WebSocketRequestState,
    *,
    account_id_value: str | None,
    error_code: str,
    error_message: str | None,
    log_prefix: str,
) -> bool:
    request_text = request_state.request_text
    if not request_text:
        return False

    payload_bytes = request_text.encode("utf-8")
    request_sha = sha256(payload_bytes).hexdigest()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    dump_id = "-".join(
        (
            timestamp,
            _safe_dump_slug(request_state.transport, fallback="transport"),
            _safe_dump_slug(request_state.model, fallback="model"),
            _safe_dump_slug(
                request_state.request_log_id or request_state.response_id or request_state.request_id,
                fallback="request",
            ),
        )
    )
    dump_dir = _OVERSIZED_RESPONSE_CREATE_DUMP_DIR
    dump_path = dump_dir / f"{dump_id}.response-create.json.gz"
    meta_path = dump_dir / f"{dump_id}.meta.json"

    meta: dict[str, JsonValue] = {
        "dump_id": dump_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "reason": {
            "error_code": error_code,
            "error_message": error_message,
        },
        "request": {
            "account_id": account_id_value,
            "request_id": request_state.request_id,
            "request_log_id": request_state.request_log_id,
            "response_id": request_state.response_id,
            "transport": request_state.transport,
            "model": request_state.model,
            "reasoning_effort": request_state.reasoning_effort,
            "service_tier": request_state.service_tier,
            "requested_service_tier": request_state.requested_service_tier,
            "actual_service_tier": request_state.actual_service_tier,
            "previous_response_id": request_state.previous_response_id,
            "awaiting_response_created": request_state.awaiting_response_created,
            "replay_count": request_state.replay_count,
            "request_text_bytes": len(payload_bytes),
            "request_text_chars": len(request_text),
            "request_text_sha256": request_sha,
        },
        "paths": {
            "dump_path": str(dump_path),
            "meta_path": str(meta_path),
        },
    }

    try:
        parsed_payload = json.loads(request_text)
    except json.JSONDecodeError as exc:
        meta["parse_error"] = str(exc)
    else:
        if isinstance(parsed_payload, dict):
            meta["summary"] = _summarize_response_create_payload(parsed_payload)
        else:
            meta["summary"] = {"payload_type": type(parsed_payload).__name__}

    try:
        dump_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
            handle.write(request_text)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        logger.exception(
            "Failed to dump %s response.create payload request_id=%s request_log_id=%s",
            log_prefix,
            request_state.request_id,
            request_state.request_log_id,
        )
        return False

    logger.warning(
        "Saved %s response.create dump request_id=%s request_log_id=%s dump_path=%s meta_path=%s bytes=%s",
        log_prefix,
        request_state.request_id,
        request_state.request_log_id,
        dump_path,
        meta_path,
        len(payload_bytes),
    )
    return True


def _should_dump_oversized_response_create(error_code: str, error_message: str | None) -> bool:
    if error_code != "stream_incomplete" or not error_message:
        return False
    normalized = error_message.lower()
    return "1009" in normalized or "message too big" in normalized


def _safe_dump_slug(value: str | None, *, fallback: str) -> str:
    if not value:
        return fallback
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    if not normalized:
        return fallback
    return normalized[:80]


def _summarize_response_create_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    field_sizes = sorted(
        (
            {
                "key": key,
                "size_bytes": _json_size_bytes(value),
            }
            for key, value in payload.items()
        ),
        key=lambda item: int(item["size_bytes"]),
        reverse=True,
    )
    summary: dict[str, JsonValue] = {
        "top_level_keys": list(payload.keys()),
        "top_level_field_sizes": field_sizes,
    }
    input_summary = _summarize_response_create_input(payload.get("input"))
    if input_summary is not None:
        summary["input"] = input_summary
    return summary


def _summarize_response_create_input(input_value: JsonValue) -> dict[str, JsonValue] | None:
    if not isinstance(input_value, list):
        return None

    role_counts: dict[str, int] = {}
    item_type_counts: dict[str, int] = {}
    content_part_type_counts: dict[str, int] = {}
    largest_items: list[dict[str, JsonValue]] = []

    for index, item in enumerate(input_value):
        item_summary: dict[str, JsonValue] = {
            "index": index,
            "size_bytes": _json_size_bytes(item),
        }
        if isinstance(item, dict):
            role = item.get("role")
            if isinstance(role, str):
                item_summary["role"] = role
                role_counts[role] = role_counts.get(role, 0) + 1
            item_type = item.get("type")
            if isinstance(item_type, str):
                item_summary["type"] = item_type
                item_type_counts[item_type] = item_type_counts.get(item_type, 0) + 1
            content = item.get("content")
            if isinstance(content, list):
                item_summary["content_parts"] = len(content)
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type")
                    if isinstance(part_type, str):
                        content_part_type_counts[part_type] = content_part_type_counts.get(part_type, 0) + 1
        largest_items.append(item_summary)

    largest_items.sort(key=lambda item: int(item["size_bytes"]), reverse=True)
    summary: dict[str, JsonValue] = {
        "count": len(input_value),
        "role_counts": cast(JsonValue, role_counts),
        "item_type_counts": cast(JsonValue, item_type_counts),
        "content_part_type_counts": cast(JsonValue, content_part_type_counts),
        "largest_items": cast(JsonValue, largest_items[:_OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS]),
    }
    return summary


def _json_size_bytes(value: JsonValue) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _pop_terminal_websocket_request_state(
    pending_requests: deque[_WebSocketRequestState],
    *,
    response_id: str | None,
    fallback_request_state: _WebSocketRequestState | None,
    prefer_previous_response_not_found: bool = False,
    previous_response_id_hint: str | None = None,
    error_message: str | None = None,
    allow_precreated_terminal_fallback: bool = False,
) -> _WebSocketRequestState | None:
    if response_id is not None:
        request_state = _find_websocket_request_state_by_response_id(pending_requests, response_id)
        if request_state is not None:
            pending_requests.remove(request_state)
            return request_state
    if fallback_request_state is not None and fallback_request_state in pending_requests:
        pending_requests.remove(fallback_request_state)
        return fallback_request_state
    if response_id is not None and allow_precreated_terminal_fallback:
        request_state = _match_websocket_request_state_for_precreated_terminal_event(pending_requests)
        if request_state is not None and request_state in pending_requests:
            pending_requests.remove(request_state)
            return request_state
    if response_id is not None and prefer_previous_response_not_found:
        request_state = _match_websocket_request_state_for_previous_response_error(
            pending_requests,
            previous_response_id_hint=previous_response_id_hint,
            error_message=error_message,
        )
        if request_state is not None and request_state in pending_requests:
            pending_requests.remove(request_state)
            return request_state
    if response_id is None:
        request_state = _match_websocket_request_state_for_anonymous_event(
            pending_requests,
            prefer_previous_response_not_found=prefer_previous_response_not_found,
            previous_response_id_hint=previous_response_id_hint,
            error_message=error_message,
        )
        if request_state is not None and request_state in pending_requests:
            pending_requests.remove(request_state)
            return request_state
    return None


def _upstream_websocket_disconnect_message(message: UpstreamWebSocketMessage) -> str:
    if message.kind == "error" and message.error:
        return f"Upstream websocket closed before response.completed: {message.error}"
    if message.close_code is not None:
        return f"Upstream websocket closed before response.completed (close_code={message.close_code})"
    return "Upstream websocket closed before response.completed"


def _websocket_receive_timeout_for_pending_requests(
    started_ats: Sequence[float],
    *,
    proxy_request_budget_seconds: float,
    stream_idle_timeout_seconds: float,
) -> _WebSocketReceiveTimeout | None:
    if not started_ats:
        return None

    idle_timeout_seconds = max(0.001, stream_idle_timeout_seconds)
    oldest_started_at = min(started_ats)
    remaining_budget = _remaining_budget_seconds(oldest_started_at + proxy_request_budget_seconds)

    if remaining_budget <= 0:
        return _WebSocketReceiveTimeout(
            timeout_seconds=0.0,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
        )
    if idle_timeout_seconds <= remaining_budget:
        return _WebSocketReceiveTimeout(
            timeout_seconds=idle_timeout_seconds,
            error_code="stream_idle_timeout",
            error_message="Upstream stream idle timeout",
            fail_all_pending=True,
        )
    return _WebSocketReceiveTimeout(
        timeout_seconds=remaining_budget,
        error_code="upstream_request_timeout",
        error_message="Proxy request budget exhausted",
    )


def _routing_strategy(settings: DashboardSettings) -> RoutingStrategy:
    value = settings.routing_strategy or "capacity_weighted"
    if value == "round_robin":
        return "round_robin"
    if value == "usage_weighted":
        return "usage_weighted"
    return "capacity_weighted"


def _parse_websocket_payload(text: str) -> dict[str, JsonValue] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _is_websocket_response_create(payload: dict[str, JsonValue]) -> bool:
    payload_type = payload.get("type")
    return isinstance(payload_type, str) and payload_type == "response.create"


def _app_error_to_websocket_event(exc: AppError) -> dict[str, JsonValue]:
    return _wrapped_websocket_error_event(
        exc.status_code,
        openai_error(exc.code, exc.message, error_type=getattr(exc, "error_type", "server_error")),
    )


def _wrapped_websocket_error_event(
    status_code: int,
    payload: OpenAIErrorEnvelope,
) -> dict[str, JsonValue]:
    error_payload = cast(JsonValue, dict(payload["error"]))
    event = cast(
        dict[str, JsonValue],
        {
            "type": "error",
            "status": status_code,
            "error": error_payload,
        },
    )
    return event


def _serialize_websocket_error_event(payload: dict[str, JsonValue]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _remaining_budget_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _websocket_connect_deadline(request_state: _WebSocketRequestState, budget_seconds: float) -> float:
    started_at = request_state.started_at if request_state.started_at > 0 else time.monotonic()
    return started_at + budget_seconds


def _push_stream_attempt_timeout_overrides(
    timeout_seconds: float,
) -> tuple[float | None, float | None, float | None]:
    return push_stream_timeout_overrides(
        connect_timeout_seconds=timeout_seconds,
        idle_timeout_seconds=timeout_seconds,
        total_timeout_seconds=timeout_seconds,
    )


def _proxy_request_timeout_event(request_id: str) -> ResponseFailedEvent:
    return response_failed_event(
        "upstream_request_timeout",
        "Proxy request budget exhausted",
        response_id=request_id,
    )


def _should_retry_stream_error(code: str) -> bool:
    return code in _ACCOUNT_RECOVERY_RETRY_CODES


def _raise_proxy_budget_exhausted() -> NoReturn:
    raise ProxyResponseError(
        502,
        openai_error("upstream_unavailable", "Proxy request budget exhausted"),
    )


def _raise_proxy_unavailable(message: str) -> NoReturn:
    raise ProxyResponseError(
        502,
        openai_error("upstream_unavailable", message),
    )


def _is_proxy_budget_exhausted_error(exc: ProxyResponseError) -> bool:
    error = _parse_openai_error(exc.payload)
    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
    error_message = error.message if error else None
    return error_code == "upstream_unavailable" and error_message == "Proxy request budget exhausted"


def _should_suppress_text_done_event(
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    suppress_text_done_events: bool,
    saw_text_delta: bool,
) -> bool:
    if not suppress_text_done_events or not saw_text_delta or event_type is None:
        return False
    if event_type == "response.output_text.done":
        return True
    if event_type == "response.content_part.done":
        return _is_text_content_part(payload)
    return False


def _is_text_content_part(payload: dict[str, JsonValue] | None) -> bool:
    if payload is None:
        return False
    part = payload.get("part")
    if not isinstance(part, dict):
        return False
    part_type = part.get("type")
    return isinstance(part_type, str) and part_type in _TEXT_DONE_CONTENT_PART_TYPES


def _maybe_log_proxy_request_shape(
    kind: str,
    payload: ResponsesRequest | ResponsesCompactRequest,
    headers: Mapping[str, str],
    *,
    sticky_kind: str | None = None,
    sticky_key_source: str | None = None,
    prompt_cache_key_set: bool | None = None,
) -> None:
    settings = get_settings()
    if not settings.log_proxy_request_shape:
        return

    request_id = get_request_id()
    prompt_cache_key = _prompt_cache_key_from_request_model(payload)
    prompt_cache_key_hash = _hash_identifier(prompt_cache_key) if isinstance(prompt_cache_key, str) else None
    prompt_cache_key_raw = (
        _truncate_identifier(prompt_cache_key)
        if settings.log_proxy_request_shape_raw_cache_key and isinstance(prompt_cache_key, str)
        else None
    )

    extra_keys = sorted(payload.model_extra.keys()) if payload.model_extra else []
    fields_set = sorted(payload.model_fields_set)
    input_summary = _summarize_input(payload.input)
    header_keys = _interesting_header_keys(headers)
    session_header_present = _sticky_key_from_session_header(headers) is not None
    tools_hash = _tools_hash(payload)
    model_class = _extract_model_class(payload.model)

    logger.warning(
        "proxy_request_shape request_id=%s kind=%s model=%s stream=%s input=%s "
        "prompt_cache_key=%s prompt_cache_key_raw=%s fields=%s extra=%s headers=%s "
        "sticky_kind=%s sticky_key_source=%s prompt_cache_key_set=%s"
        " session_header_present=%s tools_hash=%s model_class=%s",
        request_id,
        kind,
        payload.model,
        getattr(payload, "stream", None),
        input_summary,
        prompt_cache_key_hash,
        prompt_cache_key_raw,
        fields_set,
        extra_keys,
        header_keys,
        sticky_kind,
        sticky_key_source,
        prompt_cache_key_set,
        session_header_present,
        tools_hash,
        model_class,
    )


def _maybe_log_proxy_request_payload(
    kind: str,
    payload: ResponsesRequest | ResponsesCompactRequest,
    headers: Mapping[str, str],
) -> None:
    settings = get_settings()
    if not settings.log_proxy_request_payload:
        return

    request_id = get_request_id()
    payload_dict = payload.model_dump(mode="json", exclude_none=True)
    extra = payload.model_extra or {}
    if extra:
        payload_dict = {**payload_dict, "_extra": extra}
    header_keys = _interesting_header_keys(headers)
    payload_json = json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))

    logger.warning(
        "proxy_request_payload request_id=%s kind=%s payload=%s headers=%s",
        request_id,
        kind,
        payload_json,
        header_keys,
    )


def _maybe_log_proxy_service_tier_trace(
    kind: str,
    *,
    requested_service_tier: str | None,
    actual_service_tier: str | None,
) -> None:
    settings = get_settings()
    if not settings.log_proxy_service_tier_trace:
        return

    logger.warning(
        "proxy_service_tier_trace request_id=%s kind=%s requested_service_tier=%s actual_service_tier=%s",
        get_request_id(),
        kind,
        requested_service_tier,
        actual_service_tier,
    )


def _hash_identifier_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return _hash_identifier(stripped)


def _record_continuity_owner_resolution(
    *,
    surface: str,
    source: str,
    outcome: str,
    previous_response_id: str | None,
    session_id: str | None,
) -> None:
    if PROMETHEUS_AVAILABLE and continuity_owner_resolution_total is not None:
        continuity_owner_resolution_total.labels(
            surface=surface,
            source=source,
            outcome=outcome,
        ).inc()
    if outcome == "miss" or (outcome == "hit" and source == "request_cache"):
        return
    logger.log(
        logging.WARNING if outcome == "fail_closed" else logging.INFO,
        "continuity_owner_resolution surface=%s source=%s outcome=%s previous_response_id=%s session_id=%s",
        surface,
        source,
        outcome,
        _hash_identifier_or_none(previous_response_id),
        _hash_identifier_or_none(session_id),
    )


def _record_continuity_fail_closed(
    *,
    surface: str,
    reason: str,
    previous_response_id: str | None,
    session_id: str | None = None,
    upstream_error_code: str | None = None,
) -> None:
    if PROMETHEUS_AVAILABLE and continuity_fail_closed_total is not None:
        continuity_fail_closed_total.labels(
            surface=surface,
            reason=reason,
        ).inc()
    logger.warning(
        "continuity_fail_closed surface=%s reason=%s previous_response_id=%s session_id=%s upstream_error_code=%s",
        surface,
        reason,
        _hash_identifier_or_none(previous_response_id),
        _hash_identifier_or_none(session_id),
        upstream_error_code,
    )


def _hash_identifier(value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _summarize_input(items: JsonValue) -> str:
    if items is None:
        return "0"
    if isinstance(items, str):
        return "str"
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        if not items:
            return "0"
        type_counts: dict[str, int] = {}
        for item in items:
            type_name = type(item).__name__
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
        summary = ",".join(f"{key}={type_counts[key]}" for key in sorted(type_counts))
        return f"{len(items)}({summary})"
    return type(items).__name__


def _http_bridge_payload_looks_like_full_resend(payload: ResponsesRequest) -> bool:
    input_value = payload.input
    if isinstance(input_value, str):
        return len(input_value) >= 4096
    if isinstance(input_value, Sequence) and not isinstance(input_value, (str, bytes, bytearray)):
        if len(input_value) > 1:
            return True
        if len(input_value) == 1:
            try:
                return len(json.dumps(input_value[0], ensure_ascii=True, separators=(",", ":"))) >= 4096
            except TypeError:
                return False
    return False


def _truncate_identifier(value: str, *, max_length: int = 96) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:48]}...{value[-16:]}"


def _tools_hash(payload: ResponsesRequest | ResponsesCompactRequest) -> str | None:
    payload_tools = payload.to_payload().get("tools")
    if not isinstance(payload_tools, list) or not payload_tools:
        return None
    serialized = json.dumps(payload_tools, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _hash_identifier(serialized)


def _interesting_header_keys(headers: Mapping[str, str]) -> list[str]:
    allowlist = {
        "user-agent",
        "x-request-id",
        "request-id",
        "session_id",
        "x-openai-client-id",
        "x-openai-client-version",
        "x-openai-client-arch",
        "x-openai-client-os",
        "x-openai-client-user-agent",
        "x-codex-session-id",
        "x-codex-conversation-id",
    }
    return sorted({key.lower() for key in headers.keys() if key.lower() in allowlist})


def _prompt_cache_key_from_request_model(payload: ResponsesRequest | ResponsesCompactRequest) -> str | None:
    typed_value = getattr(payload, "prompt_cache_key", None)
    if isinstance(typed_value, str) and typed_value:
        return typed_value
    if not payload.model_extra:
        return None
    extra_value = payload.model_extra.get("prompt_cache_key")
    if isinstance(extra_value, str) and extra_value:
        return extra_value
    camel_value = payload.model_extra.get("promptCacheKey")
    if isinstance(camel_value, str) and camel_value:
        return camel_value
    return None


def _extract_model_class(model: str) -> str:
    """Extract model class from model name for cache key prefix.

    Classification:
    - "mini" for gpt-5.4-mini
    - "codex" for gpt-5.3-codex* (any variant)
    - "std" for all others
    """
    if "codex" in model:
        return "codex"
    if "mini" in model:
        return "mini"
    return "std"


def _derive_prompt_cache_key(
    payload: ResponsesRequest | ResponsesCompactRequest,
    api_key: ApiKeyData | None,
) -> str:
    """Derive a stable, session-scoped prompt_cache_key when the client does not provide one.

    The generated key is scoped to (model-class, api-key, instructions-prefix, first-user-input) so that:
    - Different model classes get *different* keys (prevents cache pollution).
    - Parallel sessions from the same API key get *different* keys (different first input).
    - Successive turns within one session get the *same* key (first input stays constant).
    - Different API keys never collide.
    """
    parts: list[str] = []
    model = getattr(payload, "model", None)
    model_class = _extract_model_class(model) if isinstance(model, str) and model else None

    if api_key is not None:
        parts.append(api_key.id[:12])

    instructions = getattr(payload, "instructions", None)
    if isinstance(instructions, str) and instructions:
        parts.append(sha256(instructions[:512].encode()).hexdigest()[:12])

    first_user_text = _extract_first_user_input(payload)
    if first_user_text:
        parts.append(sha256(first_user_text[:512].encode()).hexdigest()[:12])

    if not parts:
        random_suffix = uuid4().hex[:24]
        return f"{model_class}-{random_suffix}" if model_class is not None else random_suffix

    return "-".join([model_class, *parts]) if model_class is not None else "-".join(parts)


def _extract_first_user_input(payload: ResponsesRequest | ResponsesCompactRequest) -> str | None:
    """Return a text representation of the first user input item for cache key derivation."""
    input_value = getattr(payload, "input", None)
    if isinstance(input_value, str):
        return input_value[:512]
    if not isinstance(input_value, list):
        return None
    for item in input_value:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "user":
            content = item.get("content")
            if isinstance(content, str):
                return content[:512]
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str):
                            return text[:512]
            return json.dumps(item, sort_keys=True, ensure_ascii=False)[:512]
    return None


def _sticky_key_from_payload(payload: ResponsesRequest) -> str | None:
    value = _prompt_cache_key_from_request_model(payload)
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _sticky_key_from_session_header(headers: Mapping[str, str]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for key in ("session_id", "x-codex-session-id", "x-codex-conversation-id"):
        value = normalized.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _sticky_key_from_turn_state_header(headers: Mapping[str, str]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    value = normalized.get("x-codex-turn-state")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _owner_lookup_session_id_from_headers(headers: Mapping[str, str]) -> str | None:
    # `x-codex-turn-state` is per conversation turn/thread and is more specific
    # than `session_id`, which may be shared across multiple terminals.
    turn_state = _sticky_key_from_turn_state_header(headers)
    if turn_state is not None:
        return turn_state
    return _sticky_key_from_session_header(headers)


def ensure_downstream_turn_state(headers: Mapping[str, str]) -> str:
    existing = _sticky_key_from_turn_state_header(headers)
    if existing is not None:
        return existing
    return f"turn_{uuid4().hex}"


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


def ensure_http_downstream_turn_state(headers: Mapping[str, str]) -> str:
    existing = _sticky_key_from_turn_state_header(headers)
    if existing is not None:
        return existing
    return f"http_turn_{uuid4().hex}"


def build_downstream_turn_state_accept_headers(turn_state: str) -> list[tuple[bytes, bytes]]:
    return [(b"x-codex-turn-state", turn_state.encode("utf-8"))]


def build_downstream_turn_state_response_headers(turn_state: str) -> dict[str, str]:
    return {"x-codex-turn-state": turn_state}


def _upstream_turn_state_from_socket(upstream: UpstreamResponsesWebSocket | None) -> str | None:
    if upstream is None:
        return None
    getter = getattr(upstream, "response_header", None)
    if not callable(getter):
        return None
    value = getter("x-codex-turn-state")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _response_create_client_metadata(
    payload: Mapping[str, JsonValue],
    *,
    headers: Mapping[str, str],
) -> Mapping[str, JsonValue] | None:
    raw_value = payload.get("client_metadata")
    client_metadata: dict[str, JsonValue] = {}
    if is_json_mapping(raw_value):
        for key, value in raw_value.items():
            if isinstance(key, str):
                client_metadata[key] = value

    normalized_headers = {key.lower(): value for key, value in headers.items()}
    turn_metadata = normalized_headers.get("x-codex-turn-metadata")
    if isinstance(turn_metadata, str) and turn_metadata.strip():
        client_metadata.setdefault("x-codex-turn-metadata", turn_metadata)

    return client_metadata or None


def _headers_with_turn_state(headers: Mapping[str, str], turn_state: str | None) -> dict[str, str]:
    forwarded = dict(headers)
    if turn_state:
        forwarded["x-codex-turn-state"] = turn_state
    return forwarded


def _preferred_http_bridge_reconnect_turn_state(session: "_HTTPBridgeSession") -> str | None:
    if (
        session.codex_session
        and session.downstream_turn_state is not None
        and session.affinity.kind == StickySessionKind.CODEX_SESSION
        and session.affinity.key == session.downstream_turn_state
    ):
        return session.downstream_turn_state
    return session.upstream_turn_state


def _http_bridge_turn_state_alias_key(turn_state: str, api_key_id: str | None) -> tuple[str, str | None]:
    return (turn_state, api_key_id)


def _http_bridge_previous_response_alias_key(response_id: str, api_key_id: str | None) -> tuple[str, str | None]:
    return (response_id.strip(), api_key_id)


def _http_bridge_session_allows_api_key(session: "_HTTPBridgeSession", api_key: ApiKeyData | None) -> bool:
    if api_key is None or not api_key.account_assignment_scope_enabled:
        return True
    return session.account.id in api_key.assigned_account_ids


def _http_bridge_session_reusable_for_request(
    *,
    session: "_HTTPBridgeSession",
    key: "_HTTPBridgeSessionKey",
    incoming_turn_state: str | None,
    previous_response_id: str | None,
) -> bool:
    if key.affinity_kind != "prompt_cache":
        return True
    if incoming_turn_state is not None:
        return True
    if previous_response_id is not None:
        return True
    return not session.codex_session


def _http_bridge_session_matches_preferred_account(
    *,
    session: "_HTTPBridgeSession",
    previous_response_id: str | None,
    preferred_account_id: str | None,
) -> bool:
    if previous_response_id is None or preferred_account_id is None:
        return True
    return session.account.id == preferred_account_id


def _resolve_prompt_cache_key(
    payload: ResponsesRequest | ResponsesCompactRequest,
    *,
    openai_cache_affinity: bool,
    api_key: ApiKeyData | None,
) -> tuple[str | None, str]:
    cache_key = _prompt_cache_key_from_request_model(payload)
    if isinstance(cache_key, str):
        stripped = cache_key.strip()
        if stripped:
            if stripped != cache_key:
                payload.prompt_cache_key = stripped
            return stripped, "payload"
    if not openai_cache_affinity:
        return None, "none"
    settings = get_settings()
    if not settings.openai_prompt_cache_key_derivation_enabled:
        return None, "none"
    cache_key = _derive_prompt_cache_key(payload, api_key)
    payload.prompt_cache_key = cache_key
    return cache_key, "derived"


def _sticky_key_for_responses_request(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    openai_cache_affinity: bool,
    openai_cache_affinity_max_age_seconds: int,
    sticky_threads_enabled: bool,
    api_key: ApiKeyData | None = None,
    codex_session_budget_reallocation_enabled: bool = True,
) -> _AffinityPolicy:
    cache_key, _ = _resolve_prompt_cache_key(
        payload,
        openai_cache_affinity=openai_cache_affinity,
        api_key=api_key,
    )
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key:
        return _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
            budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        )
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return _AffinityPolicy(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
                budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
            )
    if openai_cache_affinity:
        return _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=openai_cache_affinity_max_age_seconds,
        )
    if sticky_threads_enabled:
        return _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.STICKY_THREAD,
            reallocate_sticky=True,
        )
    return _AffinityPolicy()


def _make_http_bridge_session_key(
    payload: ResponsesRequest,
    *,
    headers: Mapping[str, str],
    affinity: _AffinityPolicy,
    api_key: ApiKeyData | None,
    request_id: str,
    allow_forwarded_affinity_headers: bool = False,
    forwarded_affinity_kind: str | None = None,
    forwarded_affinity_key: str | None = None,
) -> _HTTPBridgeSessionKey:
    forwarded_key = (
        _forwarded_http_bridge_session_key(
            headers,
            api_key,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
        )
        if allow_forwarded_affinity_headers
        else None
    )
    if forwarded_key is not None:
        return forwarded_key
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key is not None:
        affinity_key = turn_state_key
        affinity_kind = "turn_state_header"
        strength: Literal["hard", "soft"] = "hard"
    else:
        session_key = _sticky_key_from_session_header(headers)
        if session_key is not None:
            affinity_key = session_key
            affinity_kind = "session_header"
            strength = "hard"
        else:
            affinity_key = affinity.key or request_id
            affinity_kind = affinity.kind.value if affinity.kind is not None else "request"
            strength = "soft"
    return _HTTPBridgeSessionKey(
        affinity_kind=affinity_kind,
        affinity_key=affinity_key,
        api_key_id=api_key.id if api_key is not None else None,
        strength=strength,
    )


async def _http_bridge_should_wait_for_registration(
    self,
    key: _HTTPBridgeSessionKey,
    settings: Settings,
) -> bool:
    import app.core.startup as startup_module

    if startup_module._bridge_registration_complete:
        return False
    if key.strength != "hard":
        return False
    if _http_bridge_requires_cluster_registration(settings):
        return True
    if self._ring_membership is None:
        return False
    try:
        active_members = await self._ring_membership.list_active()
    except Exception:
        logger.debug("Skipping bridge registration gate because active ring lookup failed", exc_info=True)
        return False
    current_instance = settings.http_responses_session_bridge_instance_id
    return any(member != current_instance for member in active_members)


def _durable_bridge_lookup_active_owner(lookup: DurableBridgeLookup | None) -> str | None:
    if lookup is None:
        return None
    if lookup.state == "closed":
        return None
    if lookup.owner_instance_id is None or lookup.lease_expires_at is None:
        return None
    lease_expires_at = to_utc_naive(lookup.lease_expires_at)
    if lease_expires_at <= utcnow():
        return None
    return lookup.owner_instance_id


def _durable_bridge_lookup_allows_local_reuse(
    lookup: DurableBridgeLookup | None,
    *,
    current_instance: str,
) -> bool:
    if lookup is None:
        return True
    owner_instance = _durable_bridge_lookup_active_owner(lookup)
    if owner_instance is None:
        return True
    return owner_instance == current_instance


def _http_bridge_allow_durable_takeover(lookup: DurableBridgeLookup | None) -> bool:
    owner_instance = _durable_bridge_lookup_active_owner(lookup)
    if owner_instance is None:
        return True
    if lookup is None:
        return False
    return lookup.state in {
        HttpBridgeSessionState.DRAINING,
        HttpBridgeSessionState.CLOSED,
    }


def _http_bridge_has_durable_recovery_anchor(
    *,
    previous_response_id: str | None,
    durable_lookup: DurableBridgeLookup | None,
) -> bool:
    if previous_response_id is not None:
        return True
    if durable_lookup is None or durable_lookup.latest_response_id is None:
        return False
    return durable_lookup.canonical_kind in {"turn_state_header", "session_header"}


def _http_bridge_can_local_recover_without_ring(
    *,
    key: _HTTPBridgeSessionKey,
    headers: Mapping[str, str],
    previous_response_id: str | None,
    durable_lookup: DurableBridgeLookup | None,
) -> bool:
    if _http_bridge_has_durable_recovery_anchor(
        previous_response_id=previous_response_id,
        durable_lookup=durable_lookup,
    ):
        return True
    return (
        key.affinity_kind == "session_header"
        and previous_response_id is None
        and _sticky_key_from_turn_state_header(headers) is None
    )


def _http_bridge_can_single_instance_owner_takeover_without_anchor(
    *,
    key: _HTTPBridgeSessionKey,
    owner_instance: str | None,
    current_instance: str,
    ring: tuple[str, ...],
) -> bool:
    if key.strength != "hard":
        return False
    if owner_instance is None or owner_instance == current_instance:
        return False
    if len(ring) != 1:
        return False
    if ring[0] != current_instance:
        return False
    return owner_instance not in ring


def _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor(
    *,
    key: _HTTPBridgeSessionKey,
    owner_instance: str | None,
    current_instance: str,
    ring: tuple[str, ...],
) -> bool:
    if key.affinity_kind != "prompt_cache":
        return False
    if owner_instance is None or owner_instance == current_instance:
        return False
    if len(ring) != 1:
        return False
    if ring[0] != current_instance:
        return False
    return owner_instance not in ring


def _http_bridge_can_recover_during_drain(
    *,
    key: _HTTPBridgeSessionKey,
    headers: Mapping[str, str],
    previous_response_id: str | None,
    durable_lookup: DurableBridgeLookup | None,
) -> bool:
    return _http_bridge_has_durable_recovery_anchor(
        previous_response_id=previous_response_id,
        durable_lookup=durable_lookup,
    )


def _http_bridge_request_stage(
    *,
    headers: Mapping[str, str],
    payload: ResponsesRequest,
    durable_lookup: DurableBridgeLookup | None,
) -> str:
    del durable_lookup
    if (
        payload.previous_response_id is not None
        or _sticky_key_from_turn_state_header(headers) is not None
        or _sticky_key_from_session_header(headers) is not None
    ):
        return "follow_up"
    return "first_turn"


def _record_same_account_takeover(*, preferred_account_id: str | None, selected_account_id: str | None) -> None:
    if not PROMETHEUS_AVAILABLE or bridge_same_account_takeover_total is None or preferred_account_id is None:
        return
    if selected_account_id is None:
        bridge_same_account_takeover_total.labels(outcome="fail").inc()
    elif selected_account_id == preferred_account_id:
        bridge_same_account_takeover_total.labels(outcome="success").inc()
    else:
        bridge_same_account_takeover_total.labels(outcome="fallback").inc()


def _record_bridge_reattach(*, path: str, outcome: str) -> None:
    if PROMETHEUS_AVAILABLE and bridge_reattach_total is not None:
        bridge_reattach_total.labels(path=path, outcome=outcome).inc()


def _record_bridge_first_turn_timeout() -> None:
    if PROMETHEUS_AVAILABLE and bridge_first_turn_timeout_total is not None:
        bridge_first_turn_timeout_total.inc()


def _record_bridge_drain_recovery_allowed() -> None:
    if PROMETHEUS_AVAILABLE and bridge_drain_recovery_allowed_total is not None:
        bridge_drain_recovery_allowed_total.inc()


def _is_missing_durable_bridge_table_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "http_bridge_sessions" not in message and "http_bridge_session_aliases" not in message:
        return False
    return "no such table" in message or "does not exist" in message or "undefinedtable" in message


def _http_bridge_durable_lease_ttl_seconds() -> float:
    return float(RING_STALE_THRESHOLD_SECONDS)


def _forwarded_http_bridge_session_key(
    headers: Mapping[str, str],
    api_key: ApiKeyData | None,
    *,
    forwarded_affinity_kind: str | None = None,
    forwarded_affinity_key: str | None = None,
) -> _HTTPBridgeSessionKey | None:
    affinity_kind = forwarded_affinity_kind or _header_value_case_insensitive(headers, "x-codex-bridge-affinity-kind")
    affinity_key = forwarded_affinity_key or _header_value_case_insensitive(headers, "x-codex-bridge-affinity-key")
    if affinity_kind is None or affinity_key is None:
        return None
    strength: Literal["hard", "soft"]
    if affinity_kind in {"turn_state_header", "session_header"}:
        strength = "hard"
    else:
        strength = "soft"
    return _HTTPBridgeSessionKey(
        affinity_kind=affinity_kind,
        affinity_key=affinity_key,
        api_key_id=api_key.id if api_key is not None else None,
        strength=strength,
    )


def _http_bridge_requires_cluster_registration(settings: Settings) -> bool:
    if len(settings.http_responses_session_bridge_instance_ring) > 1:
        return True
    advertise_base_url = settings.http_responses_session_bridge_advertise_base_url
    if advertise_base_url is None:
        return False
    hostname = urlparse(advertise_base_url).hostname
    if hostname is None:
        return False
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        return True
    return not parsed_ip.is_loopback


def _effective_http_bridge_idle_ttl_seconds(
    *,
    affinity: _AffinityPolicy,
    idle_ttl_seconds: float,
    codex_idle_ttl_seconds: float,
    prompt_cache_idle_ttl_seconds: float | None = None,
) -> float:
    if affinity.kind == StickySessionKind.CODEX_SESSION:
        return max(idle_ttl_seconds, codex_idle_ttl_seconds)
    if affinity.kind == StickySessionKind.PROMPT_CACHE and prompt_cache_idle_ttl_seconds is not None:
        return prompt_cache_idle_ttl_seconds
    return idle_ttl_seconds


def _http_bridge_eviction_priority(session: _HTTPBridgeSession) -> tuple[int, float]:
    return (0 if not session.codex_session else 1, session.last_used_at)


def _build_http_bridge_prewarm_text(text_data: str) -> str | None:
    try:
        payload = json.loads(text_data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("generate") is False:
        return None
    previous_response_id = payload.get("previous_response_id")
    if isinstance(previous_response_id, str) and previous_response_id.strip():
        return None
    warmup_payload = dict(payload)
    warmup_payload["generate"] = False
    return json.dumps(warmup_payload, ensure_ascii=True, separators=(",", ":"))


def _http_bridge_payload_without_previous_response_id(payload: ResponsesRequest) -> ResponsesRequest:
    if payload.previous_response_id is None:
        return payload
    return payload.model_copy(update={"previous_response_id": None})


def _http_bridge_previous_response_error_envelope(
    previous_response_id: str,
    detail: str,
) -> OpenAIErrorEnvelope:
    payload = openai_error(
        "previous_response_not_found",
        f"Previous response with id '{previous_response_id}' not found. {detail}",
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "previous_response_id"
    return payload


def _http_bridge_continuity_lost_error_envelope() -> OpenAIErrorEnvelope:
    return openai_error(
        "stream_incomplete",
        "Upstream websocket closed before response.completed",
        error_type="server_error",
    )


def _http_bridge_owner_lookup_unavailable_error_envelope() -> OpenAIErrorEnvelope:
    return openai_error(
        "upstream_unavailable",
        "HTTP bridge owner metadata unavailable; retry later.",
        error_type="server_error",
    )


def _previous_response_owner_lookup_failed_error_envelope() -> OpenAIErrorEnvelope:
    return openai_error(
        "upstream_unavailable",
        "Previous response owner lookup failed; retry later.",
        error_type="server_error",
    )


def _mark_request_state_previous_response_not_found(
    request_state: _WebSocketRequestState,
    detail: str,
) -> None:
    previous_response_id = request_state.previous_response_id
    if previous_response_id is None:
        return
    payload = _http_bridge_previous_response_error_envelope(previous_response_id, detail)
    error = payload["error"]
    request_state.error_code_override = error.get("code")
    request_state.error_message_override = error.get("message")
    request_state.error_type_override = error.get("type")
    request_state.error_param_override = error.get("param")


def _http_bridge_should_attempt_local_previous_response_recovery(exc: ProxyResponseError) -> bool:
    payload = exc.payload
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    if code in {
        "bridge_owner_unreachable",
        "previous_response_not_found",
        "bridge_instance_mismatch",
    }:
        return True
    param_value = error.get("param")
    param = param_value.strip() if isinstance(param_value, str) and param_value.strip() else None
    message_value = error.get("message")
    message = message_value.strip() if isinstance(message_value, str) and message_value.strip() else None
    return _is_previous_response_not_found_error(code=code, param=param, message=message)


def _http_bridge_is_context_overflow_error(exc: ProxyResponseError) -> bool:
    payload = exc.payload
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code_value = error.get("code")
    code = code_value.strip() if isinstance(code_value, str) and code_value.strip() else None
    type_value = error.get("type")
    error_type = type_value.strip() if isinstance(type_value, str) and type_value.strip() else None
    normalized_code = _normalize_error_code(code, error_type)
    return normalized_code == "context_length_exceeded"


def _http_bridge_should_rollover_after_context_overflow(
    exc: ProxyResponseError,
    *,
    key: _HTTPBridgeSessionKey | None = None,
) -> bool:
    if not _http_bridge_is_context_overflow_error(exc):
        return False
    if key is not None and key.strength == "hard":
        return False
    return True


def _http_bridge_should_attempt_local_bootstrap_rebind(
    exc: ProxyResponseError,
    *,
    key: _HTTPBridgeSessionKey,
    headers: Mapping[str, str],
    previous_response_id: str | None,
) -> bool:
    if key.affinity_kind != "session_header":
        return False
    if previous_response_id is not None:
        return False
    if _sticky_key_from_turn_state_header(headers) is not None:
        return False
    payload = exc.payload
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    return code in {
        "bridge_owner_unreachable",
        "bridge_instance_mismatch",
    }


def _normalized_http_bridge_instance_ring(settings: Settings) -> tuple[str, tuple[str, ...]]:
    instance_id = settings.http_responses_session_bridge_instance_id.strip()
    if not instance_id:
        instance_id = "codex-lb"
    ring_entries: list[str] = []
    for entry in settings.http_responses_session_bridge_instance_ring:
        stripped = entry.strip()
        if stripped:
            ring_entries.append(stripped)
    if not ring_entries:
        ring_entries.append(instance_id)
    return instance_id, tuple(sorted(set(ring_entries)))


async def _active_http_bridge_instance_ring(
    settings: Settings,
    ring_membership: RingMembershipService | None,
) -> tuple[str, tuple[str, ...]]:
    instance_id, static_ring = _normalized_http_bridge_instance_ring(settings)
    if not dynamic_bridge_ring_membership_enabled(settings):
        return instance_id, static_ring
    if ring_membership is None:
        return instance_id, static_ring
    try:
        active_members = await ring_membership.list_active(require_endpoint=True)
    except Exception:
        logger.warning("Bridge ring lookup failed — refusing to fall back to static ring", exc_info=True)
        raise
    if not active_members:
        return instance_id, (instance_id,)
    normalized_members = tuple(
        sorted({member.strip() for member in active_members if isinstance(member, str) and member.strip()})
    )
    if not normalized_members:
        return instance_id, static_ring
    return instance_id, normalized_members


async def _http_bridge_owner_instance(
    key: _HTTPBridgeSessionKey,
    settings: Settings,
    ring_membership: RingMembershipService | None = None,
) -> str | None:
    instance_id, ring = await _active_http_bridge_instance_ring(settings, ring_membership)
    if len(ring) <= 1:
        return instance_id
    hash_input = f"{key.affinity_kind}:{key.affinity_key}:{key.api_key_id or ''}"
    return select_node(hash_input, ring)


def _http_bridge_runtime_config(
    dashboard_settings: DashboardSettings,
    app_settings: Settings,
) -> _HTTPBridgeRuntimeConfig:
    return _HTTPBridgeRuntimeConfig(
        enabled=app_settings.http_responses_session_bridge_enabled,
        idle_ttl_seconds=app_settings.http_responses_session_bridge_idle_ttl_seconds,
        codex_idle_ttl_seconds=app_settings.http_responses_session_bridge_codex_idle_ttl_seconds,
        max_sessions=app_settings.http_responses_session_bridge_max_sessions,
        queue_limit=app_settings.http_responses_session_bridge_queue_limit,
        prompt_cache_idle_ttl_seconds=float(
            dashboard_settings.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
        ),
        gateway_safe_mode=dashboard_settings.http_responses_session_bridge_gateway_safe_mode,
    )


def _http_bridge_owner_check_required(
    key: _HTTPBridgeSessionKey,
    *,
    gateway_safe_mode: bool,
) -> bool:
    if key.strength == "hard":
        return True
    return gateway_safe_mode and key.affinity_kind == "sticky_thread"


def _header_value_case_insensitive(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _headers_with_authorization(headers: Mapping[str, str], authorization: str | None) -> dict[str, str]:
    merged = dict(headers)
    if authorization is None:
        return merged
    if _header_value_case_insensitive(merged, "authorization") is not None:
        return merged
    merged["Authorization"] = authorization
    return merged


def _http_bridge_key_strength(key: _HTTPBridgeSessionKey) -> str:
    return key.strength or "soft"


def _log_http_bridge_event(
    event: str,
    key: _HTTPBridgeSessionKey,
    *,
    account_id: str | None,
    model: str | None,
    pending_count: int | None = None,
    detail: str | None = None,
    cache_key_family: str | None = None,
    model_class: str | None = None,
    owner_check_applied: bool | None = None,
) -> None:
    level = logging.INFO
    if event in {
        "queue_full",
        "submit_on_closed",
        "send_failure",
        "retry_fresh_upstream",
        "retry_precreated",
        "reconnect",
        "terminal_error",
        "capacity_exhausted_active_sessions",
        "owner_mismatch",
        "owner_forward_fail",
        "prompt_cache_locality_miss",
        "reallocation_orphan",
        "context_overflow_rollover",
    }:
        level = logging.WARNING
    logger.log(
        level,
        "http_bridge_event event=%s bridge_kind=%s bridge_key=%s account_id=%s"
        " model=%s pending=%s detail=%s cache_key_family=%s model_class=%s"
        " key_strength=%s owner_check_applied=%s",
        event,
        key.affinity_kind,
        _hash_identifier(key.affinity_key),
        account_id,
        model,
        pending_count,
        detail,
        cache_key_family,
        model_class,
        _http_bridge_key_strength(key),
        owner_check_applied,
    )


def _sticky_key_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    value = _prompt_cache_key_from_request_model(payload)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _sticky_key_for_compact_request(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    openai_cache_affinity: bool,
    openai_cache_affinity_max_age_seconds: int,
    sticky_threads_enabled: bool,
    api_key: ApiKeyData | None = None,
    codex_session_budget_reallocation_enabled: bool = True,
) -> _AffinityPolicy:
    cache_key, _ = _resolve_prompt_cache_key(
        payload,
        openai_cache_affinity=openai_cache_affinity,
        api_key=api_key,
    )
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return _AffinityPolicy(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
                budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
            )
    if openai_cache_affinity:
        return _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=openai_cache_affinity_max_age_seconds,
        )
    if sticky_threads_enabled:
        return _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.STICKY_THREAD,
            reallocate_sticky=True,
        )
    return _AffinityPolicy()


def _service_tier_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    return _normalize_service_tier_value(payload.service_tier)


def _service_tier_from_response(
    response: OpenAIResponsePayload | CompactResponsePayload | None,
) -> str | None:
    if response is None:
        return None
    extra = response.model_extra
    if not isinstance(extra, Mapping):
        return None
    return _normalize_service_tier_value(extra.get("service_tier"))


def _service_tier_from_event_payload(payload: dict[str, JsonValue] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    return _normalize_service_tier_value(response.get("service_tier"))


def _effective_service_tier(requested_service_tier: str | None, actual_service_tier: str | None) -> str | None:
    if isinstance(actual_service_tier, str):
        return actual_service_tier
    if isinstance(requested_service_tier, str):
        return requested_service_tier
    return None


def _normalize_service_tier_value(value: JsonValue) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.lower() == "fast":
        return "priority"
    return stripped
