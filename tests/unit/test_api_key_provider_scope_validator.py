"""Tests for the provider-scoped API key validator factory.

The factory in :mod:`app.modules.api_keys.provider_auth` wraps the existing
``validate_proxy_api_key`` dependency and rejects keys whose
``provider_scope`` does not include the requested provider. Two cases:

- codex-only key sent to a Claude route -> 403
- dual-scope (codex, claude) key sent to a Claude route -> returns key

The unit test exercises the public factory at the boundary by importing it
and asserting its returned dependency is a callable (FastAPI's
``Depends(...)`` machinery is what actually invokes it during a request). We
also cover the inner :func:`_enforce_provider_scope` helper to keep the
branch logic pinned: this helper has no FastAPI dependencies and so is
directly callable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.api_keys.provider_auth import (
    _enforce_provider_scope,
    api_key_validator_with_provider,
)

pytestmark = pytest.mark.unit


def test_factory_returns_callable_dependency() -> None:
    dep = api_key_validator_with_provider("claude")
    assert callable(dep)


def test_factory_rejects_codex_only_key() -> None:
    key = SimpleNamespace(id="k", provider_scope="codex")
    with pytest.raises(HTTPException) as exc_info:
        _enforce_provider_scope(key, "claude")
    assert exc_info.value.status_code == 403
    assert "claude" in str(exc_info.value.detail)


def test_factory_accepts_claude_only_key() -> None:
    key = SimpleNamespace(id="k", provider_scope="claude")
    assert _enforce_provider_scope(key, "claude") is key


def test_factory_accepts_dual_scope_key_for_either_provider() -> None:
    key = SimpleNamespace(id="k", provider_scope="codex,claude")
    assert _enforce_provider_scope(key, "claude") is key
    assert _enforce_provider_scope(key, "codex") is key


def test_factory_rejects_empty_scope() -> None:
    key = SimpleNamespace(id="k", provider_scope="")
    with pytest.raises(HTTPException) as exc_info:
        _enforce_provider_scope(key, "claude")
    assert exc_info.value.status_code == 403


def test_factory_rejects_missing_provider_scope_attr() -> None:
    """A key object without ``provider_scope`` is treated as no authorization."""
    key = SimpleNamespace(id="k")  # no provider_scope attribute
    with pytest.raises(HTTPException):
        _enforce_provider_scope(key, "claude")
