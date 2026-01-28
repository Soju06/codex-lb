from __future__ import annotations

import json

import pytest
import zstandard as zstd

from app.core.config.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_zstd_request_decompression(async_client, monkeypatch):
    payload = {
        "stickyThreadsEnabled": True,
        "preferEarlierResetAccounts": False,
    }
    body = json.dumps(payload).encode("utf-8")

    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", str(len(body) + 8))
    get_settings.cache_clear()

    compressed = zstd.ZstdCompressor().compress(body)
    response = await async_client.put(
        "/api/settings",
        content=compressed,
        headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stickyThreadsEnabled"] is True
    assert data["preferEarlierResetAccounts"] is False


@pytest.mark.asyncio
async def test_zstd_request_decompression_rejects_large_payload(async_client, monkeypatch):
    payload = {
        "stickyThreadsEnabled": True,
        "preferEarlierResetAccounts": False,
        "padding": "A" * 512,
    }
    body = json.dumps(payload).encode("utf-8")

    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "128")
    get_settings.cache_clear()

    compressed = zstd.ZstdCompressor().compress(body)
    response = await async_client.put(
        "/api/settings",
        content=compressed,
        headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    payload = response.json()
    assert payload["error"]["code"] == "payload_too_large"
