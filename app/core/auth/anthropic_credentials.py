from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from app.core.clients.http import get_http_client
from app.core.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_TOKEN_PREFIX = "sk-ant-oat"
_ORG_KEY_CANDIDATES = {
    "organization_id",
    "organizationid",
    "org_id",
    "orgid",
    "active_org_id",
    "activeorgid",
    "active_organization_id",
    "activeorganizationid",
}

_cache_lock = asyncio.Lock()
_cached_credentials: AnthropicCredentials | None = None
_cached_at_monotonic = 0.0


@dataclass(frozen=True, slots=True)
class AnthropicCredentials:
    bearer_token: str
    org_id: str | None
    source: str


async def resolve_anthropic_credentials(*, force_refresh: bool = False) -> AnthropicCredentials | None:
    settings = get_settings()
    token_override = _normalize_secret(settings.anthropic_usage_bearer_token)
    org_override = _normalize_identifier(settings.anthropic_org_id)
    if token_override:
        org_id = org_override
        if org_id is None and settings.anthropic_auto_discover_org:
            org_id = await _discover_org_id(token_override, settings)
        return AnthropicCredentials(
            bearer_token=token_override,
            org_id=org_id,
            source="env",
        )

    if not settings.anthropic_credentials_discovery_enabled:
        return None
    if not _is_linux():
        return None

    ttl_seconds = settings.anthropic_credentials_cache_seconds
    if ttl_seconds > 0 and not force_refresh:
        now = time.monotonic()
        if _cached_credentials is not None and now - _cached_at_monotonic < ttl_seconds:
            return _cached_credentials

    async with _cache_lock:
        if ttl_seconds > 0 and not force_refresh:
            now = time.monotonic()
            if _cached_credentials is not None and now - _cached_at_monotonic < ttl_seconds:
                return _cached_credentials

        resolved = await _resolve_uncached(settings, org_override=org_override)
        _set_cache(resolved)
        return resolved


def clear_anthropic_credentials_cache() -> None:
    global _cached_credentials, _cached_at_monotonic
    _cached_credentials = None
    _cached_at_monotonic = 0.0


def _set_cache(value: AnthropicCredentials | None) -> None:
    global _cached_credentials, _cached_at_monotonic
    _cached_credentials = value
    _cached_at_monotonic = time.monotonic()


async def _resolve_uncached(settings: Settings, *, org_override: str | None) -> AnthropicCredentials | None:
    discovered = _discover_from_files(settings)
    if discovered is None:
        discovered = _discover_from_helper_command(settings)
    if discovered is None:
        return None

    token = discovered.bearer_token
    org_id = org_override or discovered.org_id
    if org_id is None and settings.anthropic_auto_discover_org:
        org_id = await _discover_org_id(token, settings)
    return AnthropicCredentials(
        bearer_token=token,
        org_id=org_id,
        source=discovered.source,
    )


@dataclass(frozen=True, slots=True)
class _RawDiscoveredCredentials:
    bearer_token: str
    org_id: str | None
    source: str


def _discover_from_files(settings: Settings) -> _RawDiscoveredCredentials | None:
    candidates = _credential_candidates(settings)
    for path in candidates:
        if not path.is_file():
            continue
        payload = _load_json_file(path)
        if payload is None:
            continue
        token = _extract_token(payload)
        if token is None:
            continue
        org_id = _extract_org_id(payload)
        return _RawDiscoveredCredentials(
            bearer_token=token,
            org_id=org_id,
            source=f"file:{path}",
        )
    return None


def _discover_from_helper_command(settings: Settings) -> _RawDiscoveredCredentials | None:
    command = (settings.anthropic_credentials_helper_command or "").strip()
    if not command:
        return None
    try:
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1.0, settings.upstream_connect_timeout_seconds),
        )
    except Exception:
        logger.warning("anthropic_credentials_helper_failed", exc_info=True)
        return None

    if completed.returncode != 0:
        logger.warning(
            "anthropic_credentials_helper_nonzero return_code=%s stderr=%s",
            completed.returncode,
            completed.stderr.strip(),
        )
        return None

    parsed = _parse_helper_output(completed.stdout)
    if parsed is None:
        return None
    return _RawDiscoveredCredentials(
        bearer_token=parsed.bearer_token,
        org_id=parsed.org_id,
        source="helper-command",
    )


@dataclass(frozen=True, slots=True)
class _HelperCredentials:
    bearer_token: str
    org_id: str | None


def _parse_helper_output(stdout: str) -> _HelperCredentials | None:
    raw = stdout.strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        token = _normalize_secret(raw.splitlines()[0] if raw.splitlines() else raw)
        if token is None:
            return None
        return _HelperCredentials(bearer_token=token, org_id=None)

    if isinstance(payload, dict):
        token = _normalize_secret(_read_string(payload, "token") or _read_string(payload, "bearer_token"))
        if token is None:
            token = _extract_token(payload)
        if token is None:
            return None
        org_id = _normalize_identifier(
            _read_string(payload, "org_id")
            or _read_string(payload, "organization_id")
            or _read_string(payload, "orgId")
        )
        if org_id is None:
            org_id = _extract_org_id(payload)
        return _HelperCredentials(bearer_token=token, org_id=org_id)
    return None


def _credential_candidates(settings: Settings) -> list[Path]:
    candidates: list[Path] = []
    if settings.anthropic_credentials_file is not None:
        candidates.append(settings.anthropic_credentials_file)

    home = Path.home()
    candidates.extend(
        [
            home / ".claude/.credentials.json",
            home / ".claude/credentials.json",
            home / ".config/claude/.credentials.json",
            home / ".config/claude/credentials.json",
        ]
    )
    return candidates


def _load_json_file(path: Path) -> Any | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("anthropic_credentials_invalid_json path=%s", path)
        return None


def _extract_token(payload: Any) -> str | None:
    for value in _walk_strings(payload):
        token = _normalize_secret(value)
        if token is not None and token.startswith(_TOKEN_PREFIX):
            return token
    return None


def _extract_org_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = key.strip().lower().replace("-", "_")
            if normalized_key in _ORG_KEY_CANDIDATES:
                org_value = _normalize_identifier(value if isinstance(value, str) else None)
                if org_value:
                    return org_value
            nested = _extract_org_id(value)
            if nested:
                return nested
        return None
    if isinstance(payload, list):
        for item in payload:
            nested = _extract_org_id(item)
            if nested:
                return nested
    return None


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for entry in value.values():
            yield from _walk_strings(entry)
        return
    if isinstance(value, list):
        for entry in value:
            yield from _walk_strings(entry)


def _read_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str):
        return value
    return None


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def _normalize_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


async def _discover_org_id(token: str, settings: Settings) -> str | None:
    url = f"{settings.anthropic_usage_base_url.rstrip('/')}/api/organizations"
    timeout = aiohttp.ClientTimeout(total=settings.usage_fetch_timeout_seconds)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        async with get_http_client().session.get(url, headers=headers, timeout=timeout) as resp:
            if resp.status >= 400:
                return None
            payload = await resp.json(content_type=None)
    except Exception:
        return None

    return _extract_org_id_from_orgs_payload(payload)


def _extract_org_id_from_orgs_payload(payload: Any) -> str | None:
    objects: list[Any] = []
    if isinstance(payload, list):
        objects.extend(payload)
    elif isinstance(payload, dict):
        objects.append(payload)
        for key in ("organizations", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                objects.extend(value)

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for key in ("id", "uuid", "organization_id", "organizationId"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = _extract_org_id(obj)
        if nested:
            return nested
    return None
