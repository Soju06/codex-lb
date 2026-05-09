"""Upstream client for the ChatGPT backend file upload protocol.

Codex CLI / Codex Desktop upload large prompt attachments through a
three-step protocol that is **not** subject to the 16 MiB websocket
ceiling on `/responses`:

1. ``POST {base}/files`` -- register a file with ``{file_name, file_size,
   use_case}``. Upstream returns ``{file_id, upload_url}``. The
   ``upload_url`` is an Azure Blob Storage SAS link and is *not* routed
   through codex-lb on the upload step (the client PUTs the bytes
   directly to the blob).
2. ``PUT {upload_url}`` (raw blob, not in this module) -- uploaded
   directly by the caller.
3. ``POST {base}/files/{file_id}/uploaded`` -- finalize. Returns
   ``{status: success|retry|failed, download_url, file_name, mime_type,
   ...}``. The client polls until ``status != "retry"``.

Once a file is finalized, callers reference it from a ``/responses``
prompt as ``{"type": "input_file", "file_id": "..."}`` instead of
inlining base64 -- bypassing the per-message 16 MiB limit (file storage
itself is 512 MiB per item upstream).

This module mirrors the contracts implemented in the upstream Codex
client (``codex-rs/codex-api/src/files.rs::upload_local_file``).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import time
from collections.abc import Mapping
from typing import Any

import aiohttp

from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.types import JsonValue

# Matches the upstream Codex client constant.
OPENAI_FILE_UPLOAD_LIMIT_BYTES: int = 512 * 1024 * 1024

# Matches upstream Codex CLI's ``OPENAI_FILE_USE_CASE``.
OPENAI_FILE_USE_CASE: str = "codex"

# Default per-attempt timeouts. Operators can override via Settings.
_DEFAULT_FILE_REQUEST_TIMEOUT_SECONDS: float = 60.0

# Total budget for the finalize-poll loop. Mirrors upstream Codex CLI's
# 30 s deadline on ``POST /files/{id}/uploaded``.
_DEFAULT_FILE_FINALIZE_BUDGET_SECONDS: float = 30.0

# Inter-poll delay during finalize polling. Mirrors upstream's 250 ms.
_FILE_FINALIZE_POLL_DELAY_SECONDS: float = 0.25

# Headers under these prefixes are forwarded so upstream sees the same
# client fingerprint as a direct Codex request. Matches the
# ``_TRANSCRIBE_FORWARD_HEADER_PREFIXES`` policy in proxy.py.
_FILES_FORWARD_HEADER_PREFIXES: tuple[str, ...] = ("x-openai-", "x-codex-")

# Per-call timeout overrides set by the proxy service so that file
# create / finalize calls inherit the per-request budget the same way
# the transcribe path does. ``None`` means "use the module default".
_FILES_CONNECT_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_FILES_CONNECT_TIMEOUT_OVERRIDE", default=None
)
_FILES_TOTAL_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_FILES_TOTAL_TIMEOUT_OVERRIDE", default=None
)


def push_files_timeout_overrides(
    *,
    connect_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> tuple[contextvars.Token[float | None], contextvars.Token[float | None]]:
    """Push per-call timeout overrides for the file create/finalize calls.

    Mirrors ``push_transcribe_timeout_overrides`` so ``ProxyService`` can
    propagate the remaining request budget into ``create_file`` /
    ``finalize_file`` instead of letting them use the fixed 60 s default.
    """
    return (
        _FILES_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout_seconds),
        _FILES_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout_seconds),
    )


def pop_files_timeout_overrides(
    tokens: tuple[contextvars.Token[float | None], contextvars.Token[float | None]],
) -> None:
    connect_token, total_token = tokens
    _FILES_CONNECT_TIMEOUT_OVERRIDE.reset(connect_token)
    _FILES_TOTAL_TIMEOUT_OVERRIDE.reset(total_token)


def _effective_files_total_timeout(default_seconds: float = _DEFAULT_FILE_REQUEST_TIMEOUT_SECONDS) -> float:
    override = _FILES_TOTAL_TIMEOUT_OVERRIDE.get()
    if override is None:
        return default_seconds
    return max(0.001, override)


def _effective_files_connect_timeout(default_seconds: float) -> float:
    override = _FILES_CONNECT_TIMEOUT_OVERRIDE.get()
    if override is None:
        return default_seconds
    return max(0.001, override)


class FileProxyError(Exception):
    """Upstream returned a non-success status while creating or finalizing a file.

    ``status_code`` is the upstream HTTP status (or 5xx synthesized for
    transport failures / invalid JSON). ``payload`` is either the
    upstream JSON error body, an ``openai_error()`` envelope synthesized
    for transport failures, or the raw text when upstream returned
    non-JSON.
    """

    def __init__(self, status_code: int, payload: Any) -> None:
        super().__init__(f"upstream file request failed: status={status_code}")
        self.status_code = status_code
        self.payload = payload


def _build_files_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:
    """Build the Bearer-auth + chatgpt-account-id header set for /files calls.

    Mirrors ``_build_upstream_transcribe_headers``: we omit bulk-forwarded
    inbound headers (which trigger upstream WAF rejections on /files) and
    only forward ``User-Agent`` plus ``x-openai-*`` / ``x-codex-*`` keys.
    """
    headers: dict[str, str] = {}
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    for key, value in inbound.items():
        lower = key.lower()
        if lower == "user-agent":
            headers.setdefault(key, value)
        elif lower.startswith(_FILES_FORWARD_HEADER_PREFIXES):
            headers.setdefault(key, value)
    return headers


def _parse_upstream_error_body(text: str) -> Any:
    """Best-effort: return JSON when the upstream gave structured errors."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def create_file(
    *,
    payload: Mapping[str, JsonValue],
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, JsonValue]:
    """Register a new file. Returns the upstream `{file_id, upload_url}` JSON.

    The caller is expected to forward the entire body without rewriting
    ``file_name`` / ``file_size`` / ``use_case`` so the upstream contract
    is preserved verbatim.
    """
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/files"
    upstream_headers = _build_files_headers(headers, access_token, account_id)
    effective_total = _effective_files_total_timeout()
    effective_connect = _effective_files_connect_timeout(settings.upstream_connect_timeout_seconds)
    timeout = aiohttp.ClientTimeout(
        total=effective_total,
        sock_connect=effective_connect,
    )
    client_session = session or get_http_client().session
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    try:
        async with client_session.post(
            url,
            data=body,
            headers=upstream_headers,
            timeout=timeout,
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise FileProxyError(response.status, _parse_upstream_error_body(text))
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise FileProxyError(
                    502,
                    openai_error(
                        "upstream_error",
                        f"Upstream /files response was not JSON: {exc}",
                    ),
                ) from exc
            if not isinstance(parsed, dict):
                raise FileProxyError(
                    502,
                    openai_error(
                        "upstream_error",
                        "Upstream /files response was not a JSON object",
                    ),
                )
            return parsed
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        message = str(exc) or "Request to upstream timed out"
        raise FileProxyError(
            502,
            openai_error("upstream_unavailable", message),
        ) from exc


async def finalize_file(
    *,
    file_id: str,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, JsonValue]:
    """Finalize an uploaded file. Returns the upstream finalization JSON.

    Codex CLI polls this endpoint with a small retry budget while the
    upload is still being indexed. We mirror that loop server-side so a
    direct Codex client (which already polls on its own) can call us
    once and we keep the same contract.

    The poll loop:
    - Polls ``POST /files/{file_id}/uploaded`` (empty body) every
      ``_FILE_FINALIZE_POLL_DELAY_SECONDS`` (250 ms) while
      ``status == "retry"``.
    - Stops and returns the most recent payload after
      ``_DEFAULT_FILE_FINALIZE_BUDGET_SECONDS`` (30 s).
    - Returns immediately on any non-retry status (``success`` /
      ``failed``).
    """
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/files/{file_id}/uploaded"
    upstream_headers = _build_files_headers(headers, access_token, account_id)
    # The finalize-poll loop runs up to ``_DEFAULT_FILE_FINALIZE_BUDGET_SECONDS``
    # but each individual ``POST`` should never block longer than the
    # remaining request budget. Cap the per-poll timeout at the smaller
    # of the standard 60 s request budget and the override (if set).
    effective_per_poll_total = _effective_files_total_timeout()
    effective_connect = _effective_files_connect_timeout(settings.upstream_connect_timeout_seconds)
    client_session = session or get_http_client().session

    # The finalize budget cannot exceed the caller's per-request budget;
    # otherwise we would keep polling well past the parent timeout.
    finalize_budget = min(_DEFAULT_FILE_FINALIZE_BUDGET_SECONDS, effective_per_poll_total)
    deadline = time.monotonic() + finalize_budget
    parsed: dict[str, JsonValue] = {"status": "retry"}
    while True:
        # Recompute the per-poll timeout each iteration from the time
        # left until ``deadline``. A late retry must not start with the
        # full original budget when only a few hundred ms remain --
        # otherwise we can blow past both the 30 s finalize budget and
        # the parent request budget on slow networks.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Already past the deadline before issuing the next ``POST``;
            # surface the previous payload (or the seeded ``retry``
            # placeholder when we have not yet polled even once).
            return parsed
        per_poll_total = min(effective_per_poll_total, remaining)
        timeout = aiohttp.ClientTimeout(
            total=per_poll_total,
            sock_connect=min(effective_connect, per_poll_total),
        )
        try:
            async with client_session.post(
                url,
                data=b"{}",
                headers=upstream_headers,
                timeout=timeout,
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    raise FileProxyError(response.status, _parse_upstream_error_body(text))
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise FileProxyError(
                        502,
                        openai_error(
                            "upstream_error",
                            f"Upstream /files/{file_id}/uploaded response was not JSON: {exc}",
                        ),
                    ) from exc
                if not isinstance(parsed, dict):
                    raise FileProxyError(
                        502,
                        openai_error(
                            "upstream_error",
                            "Upstream /files/{file_id}/uploaded response was not a JSON object",
                        ),
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            message = str(exc) or "Request to upstream timed out"
            raise FileProxyError(
                502,
                openai_error("upstream_unavailable", message),
            ) from exc

        status = parsed.get("status")
        if status != "retry":
            return parsed
        if time.monotonic() >= deadline:
            # Budget exhausted while still ``retry`` -- return the last
            # payload verbatim so the caller can decide what to do (the
            # upstream contract treats a final ``retry`` as a soft
            # failure that the client should surface).
            return parsed
        await asyncio.sleep(_FILE_FINALIZE_POLL_DELAY_SECONDS)
        if time.monotonic() >= deadline:
            # Re-check after sleeping so we never overshoot the budget by
            # issuing one extra ``POST`` whose own request timeout could
            # block well past ``_DEFAULT_FILE_FINALIZE_BUDGET_SECONDS``.
            return parsed


async def fetch_file_bytes(
    download_url: str,
    expected_mime: str | None,
    max_bytes: int,
    *,
    session: aiohttp.ClientSession | None = None,
) -> bytes:
    """Fetch file bytes from the finalize SAS URL with a hard size cap."""
    client_session = session or get_http_client().session
    headers = {"Accept": expected_mime or "*/*"}
    timeout = aiohttp.ClientTimeout(total=_effective_files_total_timeout())
    try:
        async with client_session.get(download_url, headers=headers, timeout=timeout) as response:
            if response.status >= 400:
                raise FileProxyError(
                    502,
                    openai_error(
                        "upstream_unavailable",
                        f"File download failed with status {response.status}",
                    ),
                )
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = None
                if declared_length is not None and declared_length > max_bytes:
                    raise FileProxyError(
                        400,
                        openai_error(
                            "file_too_large",
                            f"Fetched file exceeds the {max_bytes} byte inline image limit",
                        ),
                    )
            chunks: list[bytes] = []
            total_bytes = 0
            async for chunk in response.content.iter_chunked(64 * 1024):
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise FileProxyError(
                        400,
                        openai_error(
                            "file_too_large",
                            f"Fetched file exceeds the {max_bytes} byte inline image limit",
                        ),
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except FileProxyError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        message = str(exc) or "Request to file download URL timed out"
        raise FileProxyError(
            502,
            openai_error("upstream_unavailable", message),
        ) from exc
