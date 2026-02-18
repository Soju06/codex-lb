from __future__ import annotations

import logging

import aiohttp

from app.core.clients.codex_version import get_codex_version_cache
from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel
from app.core.types import JsonValue

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SECONDS = 15.0
_FILTERED_FIELDS = {"base_instructions", "model_messages"}


class ModelFetchError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


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
        return v  # type: ignore[return-value]
    return []


def _parse_upstream_model(data: dict[str, JsonValue]) -> UpstreamModel:
    raw = {k: v for k, v in data.items() if k not in _FILTERED_FIELDS}

    reasoning_levels = tuple(
        ReasoningLevel(effort=rl.get("effort", ""), description=rl.get("description", ""))
        for rl in _list_raw(data, "supported_reasoning_levels")
        if isinstance(rl, dict)
    )

    available_in_plans = frozenset(p for p in _list_raw(data, "available_in_plans") if isinstance(p, str))
    input_modalities = tuple(m for m in _list_raw(data, "input_modalities") if isinstance(m, str))

    return UpstreamModel(
        slug=_str(data, "slug"),
        display_name=_str(data, "display_name"),
        description=_str(data, "description"),
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


async def fetch_models_for_plan(
    access_token: str,
    account_id: str | None,
) -> list[UpstreamModel]:
    settings = get_settings()
    upstream_base = settings.upstream_base_url.rstrip("/")
    client_version = await get_codex_version_cache().get_version()
    url = f"{upstream_base}/codex/models?client_version={client_version}"

    headers: dict[str, str] = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id

    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS)
    session = get_http_client().session

    async with session.get(url, headers=headers, timeout=timeout) as resp:
        if resp.status >= 400:
            text = await resp.text()
            raise ModelFetchError(resp.status, f"HTTP {resp.status}: {text[:200]}")

        data = await resp.json(content_type=None)

    if not isinstance(data, dict):
        raise ModelFetchError(502, "Invalid response format from upstream models API")

    models_raw = data.get("models")
    if not isinstance(models_raw, list):
        raise ModelFetchError(502, "Missing 'models' key in upstream response")

    result: list[UpstreamModel] = []
    for entry in models_raw:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        try:
            result.append(_parse_upstream_model(entry))
        except Exception:
            logger.warning("Failed to parse upstream model entry slug=%s", slug, exc_info=True)
            continue

    return result
