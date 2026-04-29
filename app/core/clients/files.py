"""Upstream client for the ChatGPT backend file upload protocol.

Codex CLI / Codex Desktop upload large prompt attachments through a
three-step protocol that is **not** subject to the 16 MiB websocket
ceiling on `/responses`:

1. ``POST {base}/files`` — register a file with ``{file_name, file_size,
   use_case}``. Upstream returns ``{file_id, upload_url}``. The
   ``upload_url`` is an Azure Blob Storage SAS link and is *not* routed
   through codex-lb on the upload step (the client PUTs the bytes
   directly to the blob).
2. ``PUT {upload_url}`` (raw blob, not in this module) — uploaded
   directly by the caller.
3. ``POST {base}/files/{file_id}/uploaded`` — finalize. Returns
   ``{status: success|retry|failed, download_url, file_name, mime_type,
   ...}``. The client polls until ``status != "retry"``.

Once a file is finalized, callers reference it from a ``/responses``
prompt as ``{"type": "input_file", "file_id": "..."}`` instead of
inlining base64 — bypassing the per-message 16 MiB limit (file storage
itself is 512 MiB per item upstream).

This module mirrors the contracts implemented in the upstream Codex
client (``codex-rs/codex-api/src/files.rs::upload_local_file``).
"""

from __future__ import annotations

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

# Default per-attempt timeouts. Operators can override via Settings.
_DEFAULT_FILE_REQUEST_TIMEOUT_SECONDS: float = 60.0
_DEFAULT_FILE_FINALIZE_TIMEOUT_SECONDS: float = 30.0


class FileProxyError(Exception):
    """Upstream returned a non-success status while creating or finalizing a file."""

    def __init__(self, status_code: int, payload: Any) -> None:
        super().__init__(f"upstream file request failed: status={status_code}")
        self.status_code = status_code
        self.payload = payload


def _build_files_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:
    """Build the Bearer-auth + chatgpt-account-id header set for /files calls."""
    headers: dict[str, str] = {}
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    # Forward selected inbound headers (user-agent, x-openai-*, x-codex-*) so
    # upstream sees the same client fingerprint as a direct Codex request.
    for key, value in inbound.items():
        lower = key.lower()
        if lower == "user-agent":
            headers.setdefault(key, value)
        elif lower.startswith(("x-openai-", "x-codex-")):
            headers.setdefault(key, value)
    return headers


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
    `file_name` / `file_size` / `use_case` so the upstream contract is
    preserved verbatim.
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
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise FileProxyError(
                502,
                openai_error(
                    "upstream_invalid_response",
                    f"Upstream /files response was not JSON: {exc}",
                ),
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

    deadline = time.monotonic() + _DEFAULT_FILE_FINALIZE_TIMEOUT_SECONDS
    last_payload: dict[str, JsonValue] | None = None
    while True:
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
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise FileProxyError(
                    502,
                    openai_error(
                        "upstream_invalid_response",
                        f"Upstream /files/{file_id}/uploaded response was not JSON: {exc}",
                    ),
                ) from exc
        last_payload = payload
        status = payload.get("status") if isinstance(payload, dict) else None
        if status != "retry":
            return payload
        if time.monotonic() >= deadline:
            return last_payload
        # Match the upstream client's 250 ms inter-poll delay.
        import asyncio

        await asyncio.sleep(0.25)


def _parse_upstream_error_body(text: str) -> Any:
    """Best-effort: return JSON when the upstream gave structured errors."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
