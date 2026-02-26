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
from app.db.models import Account

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
    refresh_token: str | None = None
    expires_at_ms: int | None = None
    source_path: Path | None = None


@dataclass(frozen=True, slots=True)
class AnthropicAuthFile:
    access_token: str
    refresh_token: str | None
    org_id: str | None
    expires_at_ms: int | None
    email: str | None


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


def credentials_from_account(account: Account) -> AnthropicCredentials | None:
    try:
        from app.core.crypto import TokenEncryptor

        encryptor = TokenEncryptor()
        encrypted_access = bytes(account.access_token_encrypted)
        encrypted_refresh = bytes(account.refresh_token_encrypted)
        access_token = _normalize_secret(encryptor.decrypt(encrypted_access))
        if access_token is None or not access_token.startswith(_TOKEN_PREFIX):
            return None
        refresh_token = _normalize_secret(encryptor.decrypt(encrypted_refresh))
    except Exception:
        return None

    return AnthropicCredentials(
        bearer_token=access_token,
        org_id=None,
        source=f"db-account:{account.id}",
        refresh_token=refresh_token,
    )


def parse_anthropic_auth_json(raw: bytes) -> AnthropicAuthFile:
    data = json.loads(raw)
    structured = _extract_structured_credentials(data)
    if structured is None:
        raise ValueError("Unable to extract Anthropic OAuth credentials")

    email = _extract_email(data)
    return AnthropicAuthFile(
        access_token=structured.bearer_token,
        refresh_token=structured.refresh_token,
        org_id=structured.org_id,
        expires_at_ms=structured.expires_at_ms,
        email=email,
    )


def _set_cache(value: AnthropicCredentials | None) -> None:
    global _cached_credentials, _cached_at_monotonic
    _cached_credentials = value
    _cached_at_monotonic = time.monotonic()


def _parse_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _parse_expires_at_ms(payload: dict[str, Any]) -> int | None:
    expires_at = _parse_int(payload.get("expires_at") or payload.get("expiresAt"))
    if expires_at is not None:
        if expires_at < 10_000_000_000:
            return expires_at * 1000
        return expires_at

    expires_in = _parse_int(payload.get("expires_in") or payload.get("expiresIn"))
    if expires_in is None:
        return None
    now_seconds = int(time.time())
    return (now_seconds + expires_in) * 1000


async def _safe_json_response(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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
        refresh_token=discovered.refresh_token,
        expires_at_ms=discovered.expires_at_ms,
        source_path=discovered.source_path,
    )


async def refresh_anthropic_access_token(
    credentials: AnthropicCredentials,
) -> AnthropicCredentials | None:
    refresh_token = _normalize_secret(credentials.refresh_token)
    if not refresh_token:
        return None

    settings = get_settings()
    timeout = aiohttp.ClientTimeout(total=settings.oauth_timeout_seconds)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.anthropic_oauth_client_id,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        async with get_http_client().session.post(
            settings.anthropic_oauth_token_url,
            json=payload,
            headers=headers,
            timeout=timeout,
        ) as response:
            data = await _safe_json_response(response)
            if response.status >= 400:
                return None
    except Exception:
        return None

    access_token = _normalize_secret(_read_string(data, "access_token"))
    if access_token is None:
        return None

    new_refresh_token = _normalize_secret(_read_string(data, "refresh_token")) or refresh_token
    expires_at_ms = _parse_expires_at_ms(data)
    refreshed = AnthropicCredentials(
        bearer_token=access_token,
        org_id=credentials.org_id,
        source=f"{credentials.source}:refreshed",
        refresh_token=new_refresh_token,
        expires_at_ms=expires_at_ms,
        source_path=credentials.source_path,
    )
    _set_cache(refreshed)
    return refreshed


@dataclass(frozen=True, slots=True)
class _RawDiscoveredCredentials:
    bearer_token: str
    org_id: str | None
    source: str
    refresh_token: str | None = None
    expires_at_ms: int | None = None
    source_path: Path | None = None


def _discover_from_files(settings: Settings) -> _RawDiscoveredCredentials | None:
    candidates = _credential_candidates(settings)
    for path in candidates:
        if not path.is_file():
            continue
        payload = _load_json_file(path)
        if payload is None:
            continue
        structured = _extract_structured_credentials(payload)
        token = structured.bearer_token if structured is not None else _extract_token(payload)
        if token is None:
            continue
        org_id = structured.org_id if structured is not None else _extract_org_id(payload)
        return _RawDiscoveredCredentials(
            bearer_token=token,
            org_id=org_id,
            source=f"file:{path}",
            refresh_token=structured.refresh_token if structured is not None else None,
            expires_at_ms=structured.expires_at_ms if structured is not None else None,
            source_path=path,
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
        refresh_token=parsed.refresh_token,
        expires_at_ms=parsed.expires_at_ms,
    )


@dataclass(frozen=True, slots=True)
class _HelperCredentials:
    bearer_token: str
    org_id: str | None
    refresh_token: str | None = None
    expires_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class _StructuredCredentials:
    bearer_token: str
    org_id: str | None
    refresh_token: str | None
    expires_at_ms: int | None


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
        structured = _extract_structured_credentials(payload)
        if structured is not None:
            return _HelperCredentials(
                bearer_token=structured.bearer_token,
                org_id=structured.org_id,
                refresh_token=structured.refresh_token,
                expires_at_ms=structured.expires_at_ms,
            )

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


def _extract_structured_credentials(payload: Any) -> _StructuredCredentials | None:
    if not isinstance(payload, dict):
        return None

    claude_oauth = payload.get("claudeAiOauth")
    if isinstance(claude_oauth, dict):
        access_token = _normalize_secret(_read_string(claude_oauth, "accessToken"))
        if access_token is not None:
            refresh_token = _normalize_secret(_read_string(claude_oauth, "refreshToken"))
            expires_at_ms = _parse_int(claude_oauth.get("expiresAt"))
            org_id = _extract_org_id(payload)
            return _StructuredCredentials(
                bearer_token=access_token,
                org_id=org_id,
                refresh_token=refresh_token,
                expires_at_ms=expires_at_ms,
            )

    session = payload.get("session")
    if isinstance(session, dict):
        oauth = session.get("oauth")
        if isinstance(oauth, dict):
            access_token = _normalize_secret(
                _read_string(oauth, "token")
                or _read_string(oauth, "access_token")
                or _read_string(oauth, "accessToken")
            )
            if access_token is not None:
                refresh_token = _normalize_secret(
                    _read_string(oauth, "refresh_token") or _read_string(oauth, "refreshToken")
                )
                expires_at_ms = _parse_expires_at_ms(oauth)
                org_id = _extract_org_id(payload)
                return _StructuredCredentials(
                    bearer_token=access_token,
                    org_id=org_id,
                    refresh_token=refresh_token,
                    expires_at_ms=expires_at_ms,
                )

    return None


def _extract_email(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = key.strip().lower().replace("-", "_")
            if normalized_key == "email" and isinstance(value, str):
                email = value.strip()
                if "@" in email:
                    return email
            nested = _extract_email(value)
            if nested is not None:
                return nested
        return None
    if isinstance(payload, list):
        for item in payload:
            nested = _extract_email(item)
            if nested is not None:
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
