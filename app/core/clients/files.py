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
    timeout = aiohttp.ClientTimeout(
        total=_DEFAULT_FILE_REQUEST_TIMEOUT_SECONDS,
        sock_connect=settings.upstream_connect_timeout_seconds,
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
    timeout = aiohttp.ClientTimeout(
        total=_DEFAULT_FILE_REQUEST_TIMEOUT_SECONDS,
        sock_connect=settings.upstream_connect_timeout_seconds,
    )
    client_session = session or get_http_client().session

    deadline = time.monotonic() + _DEFAULT_FILE_FINALIZE_BUDGET_SECONDS
    while True:
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
