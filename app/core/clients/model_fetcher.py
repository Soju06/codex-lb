from __future__ import annotations

import logging
from typing import Any

import aiohttp

from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SECONDS = 15.0
_FILTERED_FIELDS = {"base_instructions", "model_messages"}


class ModelFetchError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _parse_upstream_model(data: dict[str, Any]) -> UpstreamModel:
    raw = {k: v for k, v in data.items() if k not in _FILTERED_FIELDS}

    reasoning_levels_raw = data.get("supported_reasoning_levels") or []
    reasoning_levels = tuple(
        ReasoningLevel(effort=rl.get("effort", ""), description=rl.get("description", ""))
        for rl in reasoning_levels_raw
        if isinstance(rl, dict)
    )

    available_plans_raw = data.get("available_in_plans") or []
    available_in_plans = frozenset(p for p in available_plans_raw if isinstance(p, str))

    input_modalities_raw = data.get("input_modalities") or []
    input_modalities = tuple(m for m in input_modalities_raw if isinstance(m, str))

    return UpstreamModel(
        slug=data.get("slug", ""),
        display_name=data.get("display_name", ""),
        description=data.get("description", ""),
        context_window=data.get("context_window", 0),
        input_modalities=input_modalities,
        supported_reasoning_levels=reasoning_levels,
        default_reasoning_level=data.get("default_reasoning_level"),
        supports_reasoning_summaries=bool(data.get("supports_reasoning_summaries")),
        support_verbosity=bool(data.get("support_verbosity")),
        default_verbosity=data.get("default_verbosity"),
        prefer_websockets=bool(data.get("prefer_websockets")),
        supports_parallel_tool_calls=bool(data.get("supports_parallel_tool_calls")),
        supported_in_api=bool(data.get("supported_in_api", True)),
        minimal_client_version=data.get("minimal_client_version"),
        priority=int(data.get("priority", 0)),
        available_in_plans=available_in_plans,
        raw=raw,
    )


async def fetch_models_for_plan(
    access_token: str,
    account_id: str | None,
) -> list[UpstreamModel]:
    settings = get_settings()
    upstream_base = settings.upstream_base_url.rstrip("/")
    client_version = settings.model_registry_client_version
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
