from __future__ import annotations

import asyncio
import logging
from typing import cast

import aiohttp

from app.core.clients.codex import (
    CodexClient,
    CodexTransportError,
    create_codex_session,
    require_route_or_direct_egress_opt_in,
)
from app.core.clients.codex_version import get_codex_version_cache
from app.core.clients.http import lease_http_session
from app.core.clients.native_egress import (
    NativeEgressClient,
    NativeEgressError,
    NativeEgressRequest,
    NativeEgressUnavailable,
    discover_native_egress_client,
)
from app.core.clients.proxy import build_codex_user_agent
from app.core.config.settings import get_settings
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.proxy_env import resolve_http_proxy_from_env

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SECONDS = 15.0
_CODEX_CONTROL_ORIGINATOR = "codex_cli_rs"
_MODEL_DISCOVERY_SKIP_AUTO_HEADERS = frozenset({aiohttp.hdrs.ACCEPT_ENCODING})


class ModelFetchError(Exception):
    def __init__(self, status_code: int, message: str, *, transport_error: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.transport_error = transport_error


def _str(data: dict[str, JsonValue], key: str, default: str = "") -> str:
    v = data.get(key)
    return v if isinstance(v, str) else default


def _int(data: dict[str, JsonValue], key: str, default: int = 0) -> int:
    v = data.get(key)
    if isinstance(v, bool):
        return default
    return int(v) if isinstance(v, (int, float)) else default


def _opt_str(data: dict[str, JsonValue], key: str) -> str | None:
    v = data.get(key)
    return v if isinstance(v, str) else None


def _list_raw(data: dict[str, JsonValue], key: str) -> list[JsonValue]:
    v = data.get(key)
    if isinstance(v, list):
        return cast(list[JsonValue], v)
    return []


def _parse_reasoning_level(value: JsonValue) -> ReasoningLevel | None:
    if not isinstance(value, dict):
        return None
    effort = value.get("effort")
    description = value.get("description")
    if not isinstance(effort, str) or not isinstance(description, str):
        return None
    return ReasoningLevel(effort=effort, description=description)


def _parse_upstream_model(data: dict[str, JsonValue]) -> UpstreamModel:
    # preserve all upstream catalog fields verbatim
    raw = dict(data)

    reasoning_levels = tuple(
        parsed_level
        for rl in _list_raw(data, "supported_reasoning_levels")
        if (parsed_level := _parse_reasoning_level(rl)) is not None
    )

    available_in_plans = frozenset(p for p in _list_raw(data, "available_in_plans") if isinstance(p, str))
    input_modalities = tuple(m for m in _list_raw(data, "input_modalities") if isinstance(m, str))

    return UpstreamModel(
        slug=_str(data, "slug"),
        display_name=_str(data, "display_name"),
        description=_str(data, "description"),
        base_instructions=_str(data, "base_instructions"),
        context_window=_int(data, "context_window"),
        input_modalities=input_modalities,
        supported_reasoning_levels=reasoning_levels,
        default_reasoning_level=_opt_str(data, "default_reasoning_level"),
        supports_reasoning_summaries=bool(data.get("supports_reasoning_summaries")),
        support_verbosity=bool(data.get("support_verbosity")),
        default_verbosity=_opt_str(data, "default_verbosity"),
        prefer_websockets=bool(data.get("prefer_websockets")),
        supports_parallel_tool_calls=bool(data.get("supports_parallel_tool_calls")),
        supported_in_api=bool(data.get("supported_in_api", True)),
        minimal_client_version=_opt_str(data, "minimal_client_version"),
        priority=_int(data, "priority"),
        available_in_plans=available_in_plans,
        raw=raw,
    )


def _build_model_discovery_headers(
    access_token: str,
    account_id: str | None,
    *,
    client_version: str,
) -> dict[str, str]:
    """Build the first-party Codex identity used by control-plane requests."""

    headers = {"Authorization": f"Bearer {access_token}"}
    if account_id:
        headers["chatgpt-account-id"] = account_id
    headers["Accept"] = "*/*"
    headers["originator"] = _CODEX_CONTROL_ORIGINATOR
    headers["User-Agent"] = build_codex_user_agent(client_version)
    return headers


async def fetch_models_for_plan(
    access_token: str,
    account_id: str | None,
    *,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
    native_egress_client: NativeEgressClient | None = None,
) -> list[UpstreamModel]:
    settings = get_settings()
    upstream_base = settings.upstream_base_url.rstrip("/")
    client_version = await get_codex_version_cache().get_version()
    url = f"{upstream_base}/codex/models?client_version={client_version}"
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="model discovery",
    )

    headers = _build_model_discovery_headers(
        access_token,
        account_id,
        client_version=client_version,
    )

    try:
        if route is not None:
            owns_codex_client = codex_client is None
            active_codex_client = codex_client or CodexClient(create_codex_session())
            try:
                resp = await active_codex_client.request(
                    "GET",
                    url,
                    route=route,
                    headers=headers,
                    skip_auto_headers=_MODEL_DISCOVERY_SKIP_AUTO_HEADERS,
                    timeout=_FETCH_TIMEOUT_SECONDS,
                )
            finally:
                if owns_codex_client:
                    close = getattr(active_codex_client, "close", None)
                    if callable(close):
                        await close()
            status = _codex_response_status(resp)
            if status >= 400:
                text = await _codex_response_text(resp)
                raise ModelFetchError(status, f"HTTP {status}: {text[:200]}")
            data = await _codex_response_json(resp)
        else:
            active_native_egress_client = native_egress_client or discover_native_egress_client()
            if active_native_egress_client is not None:
                try:
                    native_response = await active_native_egress_client.request(
                        NativeEgressRequest(
                            method="GET",
                            url=url,
                            headers=headers,
                            timeout_seconds=_FETCH_TIMEOUT_SECONDS,
                            proxy_url=resolve_http_proxy_from_env(url),
                        )
                    )
                except NativeEgressUnavailable:
                    native_response = None
                if native_response is not None:
                    async with native_response:
                        if native_response.status >= 400:
                            text = await native_response.text()
                            raise ModelFetchError(
                                native_response.status,
                                f"HTTP {native_response.status}: {text[:200]}",
                            )
                        data = await native_response.json(content_type=None)
                    native_response = None
                else:
                    data = await _fetch_models_direct_python(url, headers)
            else:
                data = await _fetch_models_direct_python(url, headers)
    except ModelFetchError:
        raise
    except asyncio.TimeoutError as exc:
        raise ModelFetchError(504, "Upstream models API timed out", transport_error=True) from exc
    except (aiohttp.ClientError, OSError, CodexTransportError, NativeEgressError) as exc:
        message = str(exc) or exc.__class__.__name__
        raise ModelFetchError(0, f"Transport error during model fetch: {message}", transport_error=True) from exc

    if not isinstance(data, dict):
        raise ModelFetchError(502, "Invalid response format from upstream models API")

    data_object = cast(dict[str, object], data)
    models_raw = data_object.get("models")
    if not isinstance(models_raw, list):
        raise ModelFetchError(502, "Missing 'models' key in upstream response")

    result: list[UpstreamModel] = []
    for entry in models_raw:
        if not isinstance(entry, dict):
            continue
        entry_data = cast(dict[str, JsonValue], entry)
        slug = entry_data.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        try:
            result.append(_parse_upstream_model(entry_data))
        except Exception:
            logger.warning("Failed to parse upstream model entry slug=%s", slug, exc_info=True)
            continue

    return result


async def _fetch_models_direct_python(url: str, headers: dict[str, str]) -> object:
    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS)
    async with lease_http_session() as session:
        async with session.get(
            url,
            headers=headers,
            skip_auto_headers=_MODEL_DISCOVERY_SKIP_AUTO_HEADERS,
            timeout=timeout,
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise ModelFetchError(resp.status, f"HTTP {resp.status}: {text[:200]}")
            return await resp.json(content_type=None)


def _codex_response_status(response: object) -> int:
    value = getattr(response, "status_code", getattr(response, "status", None))
    if value is None:
        return 0
    return int(value)


async def _codex_response_text(response: object) -> str:
    text_value = getattr(response, "text", None)
    if isinstance(text_value, str):
        return text_value
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, str):
        return content
    return ""


async def _codex_response_json(response: object) -> object:
    json_method = getattr(response, "json", None)
    if callable(json_method):
        data = json_method()
        if asyncio.iscoroutine(data):
            return await data
        return data
    return {}
