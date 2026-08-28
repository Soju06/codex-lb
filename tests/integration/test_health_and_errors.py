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
        assert response.headers["cache-control"] == "no-cache"
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


def test_ensure_web_asset_mime_types_overrides_poisoned_registry():
    """Simulates the Windows HKCR poisoning from issue #1698.

    ``mimetypes.add_type`` mutates the global table, so this test re-runs the
    startup registration after poisoning and leaves the correct mappings in
    place for the rest of the suite.
    """
    import mimetypes

    from app.main import _WEB_ASSET_MIME_TYPES, _ensure_web_asset_mime_types

    for extension in _WEB_ASSET_MIME_TYPES:
        mimetypes.add_type("text/plain", extension)
    assert mimetypes.guess_type("x.js")[0] == "text/plain"

    _ensure_web_asset_mime_types()

    for extension, expected in _WEB_ASSET_MIME_TYPES.items():
        assert mimetypes.guess_type(f"x{extension}")[0] == expected, extension


@pytest.mark.asyncio
async def test_assets_js_served_as_javascript_despite_poisoned_registry(async_client):
    """Product-path regression for issue #1698: /assets/*.js must serve
    text/javascript even when the OS mimetypes sources map .js to text/plain,
    or strict browser MIME checking rejects every dashboard module script."""
    import mimetypes

    from app.main import _ensure_web_asset_mime_types

    asset_name = next(
        (candidate.name for candidate in sorted((_STATIC_DIR / "assets").glob("*.js"))),
        None,
    )
    assert asset_name is not None, "built dashboard assets missing; run cd frontend && bun run build"

    mimetypes.add_type("text/plain", ".js")
    try:
        _ensure_web_asset_mime_types()
        response = await async_client.get(f"/assets/{asset_name}")
    finally:
        _ensure_web_asset_mime_types()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
