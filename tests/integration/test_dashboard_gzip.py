from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _built_asset_name() -> str | None:
    assets_dir = Path("app/static/assets")
    if not assets_dir.is_dir():
        return None
    for candidate in sorted(assets_dir.glob("*.js")):
        if candidate.stat().st_size > 2048:
            return candidate.name
    return None


@pytest.mark.asyncio
async def test_asset_gzip_and_immutable_cache(async_client):
    asset = _built_asset_name()
    if asset is None:
        pytest.skip("frontend build output not present")
    response = await async_client.get(f"/assets/{asset}", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    assert response.headers.get("cache-control") == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
async def test_ranged_asset_requests_bypass_gzip(async_client):
    """A Range request must not pass through the compressing wrapper:
    FileResponse builds the 206/Content-Range against uncompressed bytes,
    so gzipping the body afterwards would corrupt resumable fetches."""
    asset = _built_asset_name()
    if asset is None:
        pytest.skip("frontend build output not present")
    response = await async_client.get(
        f"/assets/{asset}",
        headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"},
    )
    assert response.status_code == 206
    assert "content-encoding" not in response.headers
    assert response.headers.get("content-range", "").startswith("bytes 0-99/")
    assert len(response.content) == 100


@pytest.mark.asyncio
async def test_proxy_paths_never_gzipped(async_client):
    response = await async_client.get("/backend-api/codex/models", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers


@pytest.mark.asyncio
async def test_range_header_detection_is_case_insensitive():
    """ASGI servers are not guaranteed to lowercase header names; a
    mixed-case Range header must still bypass the compressing wrapper."""
    from app.core.middleware.dashboard_gzip import _has_range_header

    assert _has_range_header({"headers": ((b"Range", b"bytes=0-99"),)})
    assert _has_range_header({"headers": ((b"RANGE", b"bytes=0-99"),)})
    assert _has_range_header({"headers": ((b"range", b"bytes=0-99"),)})
    assert not _has_range_header({"headers": ((b"accept-encoding", b"gzip"),)})
