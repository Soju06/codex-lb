from __future__ import annotations

import pytest

from app.core.middleware.path_rewrite import (
    _canonicalize_backend_api_codex_path,
    _canonicalize_raw_path,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Aliased prefix collapses.
        ("/backend-api/codex/v1/models", "/backend-api/codex/models"),
        ("/backend-api/codex/v1/responses", "/backend-api/codex/responses"),
        (
            "/backend-api/codex/v1/responses/compact",
            "/backend-api/codex/responses/compact",
        ),
        # Canonical paths are left alone.
        ("/backend-api/codex/models", "/backend-api/codex/models"),
        ("/backend-api/codex/responses", "/backend-api/codex/responses"),
        # No-rest sentinels MUST NOT be rewritten -- they are legal
        # paths a future contributor could register, and collapsing
        # them would silently change routing semantics.
        ("/backend-api/codex", "/backend-api/codex"),
        ("/backend-api/codex/v1", "/backend-api/codex/v1"),
        # Top-level /v1 is the canonical OpenAI-style namespace and is
        # explicitly out of scope.
        ("/v1/models", "/v1/models"),
        ("/v1/responses", "/v1/responses"),
        # Unrelated paths.
        ("/api/settings", "/api/settings"),
        ("/", "/"),
    ],
)
def test_canonicalize_backend_api_codex_path(raw: str, expected: str) -> None:
    assert _canonicalize_backend_api_codex_path(raw) == expected


def test_canonicalize_backend_api_codex_path_is_idempotent() -> None:
    once = _canonicalize_backend_api_codex_path("/backend-api/codex/v1/responses")
    twice = _canonicalize_backend_api_codex_path(once)
    assert once == twice == "/backend-api/codex/responses"


def test_canonicalize_raw_path_preserves_query_segment() -> None:
    # raw_path in ASGI includes only the path; query lives in
    # scope["query_string"]. The rewrite must therefore not split on
    # "?", but it should still byte-equal the canonical form.
    raw = b"/backend-api/codex/v1/models"
    assert _canonicalize_raw_path(raw) == b"/backend-api/codex/models"


def test_canonicalize_raw_path_noop_for_canonical() -> None:
    raw = b"/backend-api/codex/models"
    assert _canonicalize_raw_path(raw) is raw or _canonicalize_raw_path(raw) == raw
