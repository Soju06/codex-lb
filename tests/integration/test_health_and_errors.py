from __future__ import annotations

from pathlib import Path

import pytest

from app import __version__
from app.main import _resolve_static_asset_path

pytestmark = pytest.mark.integration

_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "static"


def test_resolve_static_asset_rejects_parent_traversal(tmp_path):
    static_root = tmp_path / "static"
    static_root.mkdir()
    (tmp_path / "secret.txt").write_text("secret")

    assert _resolve_static_asset_path(static_root.resolve(), "../secret.txt") is None


def test_resolve_static_asset_tolerates_missing_static_root(tmp_path):
    static_root = tmp_path / "missing-static"

    assert _resolve_static_asset_path(static_root, "dashboard/settings") is None


def test_resolve_static_asset_rejects_symlink_escape(tmp_path):
    static_root = tmp_path / "static"
    static_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (static_root / "outside.txt").symlink_to(outside)

    assert _resolve_static_asset_path(static_root.resolve(), "outside.txt") is None


def test_resolve_static_asset_accepts_file_under_static_root(tmp_path):
    static_root = tmp_path / "static"
    static_root.mkdir()
    asset = static_root / "assets" / "app.js"
    asset.parent.mkdir()
    asset.write_text("console.log('ok')")

    assert _resolve_static_asset_path(static_root.resolve(), "assets/app.js") == asset.resolve()


@pytest.mark.asyncio
async def test_health_endpoint_ok(async_client):
    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-App-Version"] == __version__


@pytest.mark.asyncio
async def test_api_validation_error_returns_dashboard_payload(async_client):
    response = await async_client.get("/api/usage/history?hours=0")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"] == "Invalid request payload"
    assert response.headers["X-App-Version"] == __version__


@pytest.mark.asyncio
async def test_api_not_found_returns_dashboard_payload(async_client):
    response = await async_client.get("/api/does-not-exist")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "http_404"
    assert payload["error"]["message"] == "Not Found"
    assert response.headers["X-App-Version"] == __version__


@pytest.mark.asyncio
async def test_spa_route_path_returns_index_html(async_client, tmp_path):
    index = _STATIC_DIR / "index.html"
    created = not index.exists()
    if created:
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text("<!doctype html><html></html>")
    try:
        response = await async_client.get("/dashboard/settings")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["X-App-Version"] == __version__
    finally:
        if created:
            index.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_missing_static_asset_returns_not_found(async_client):
    response = await async_client.get("/assets/missing.js")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
    assert response.headers["X-App-Version"] == __version__
