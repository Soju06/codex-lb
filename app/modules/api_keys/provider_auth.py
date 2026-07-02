"""Provider-scoped API key validator factory.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/api-keys/spec.md``
— specifically the requirement that ``provider_scope`` MUST be honored on
proxy routes.

The existing ``validate_proxy_api_key`` dependency in
:mod:`app.core.auth.dependencies` authenticates the bearer token but does not
filter by provider scope. This module wraps it with a small factory that
returns a FastAPI dependency which raises HTTP 403 when the validated key
does not authorize the requested provider string.

Usage::

    from app.modules.api_keys.provider_auth import api_key_validator_with_provider

    _claude_key = api_key_validator_with_provider("claude")

    @router.post("/claude/v1/messages")
    async def messages(..., api_key=Depends(_claude_key)): ...

The factory is intentionally tiny: we deliberately do not duplicate any of
the existing validator's caching, header extraction, or error-mapping logic.
The dependency reuses the already-validated :class:`ApiKeyData` returned by
the existing dependency and only adds one provider-scope check on top of it.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from app.core.auth.dependencies import validate_proxy_api_key


def _provider_scope_mismatch(provider: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=f"API key is not authorized for provider '{provider}'",
    )


def _enforce_provider_scope(api_key: Any, provider: str) -> Any:
    """Reject keys whose ``provider_scope`` does not include ``provider``.

    ``provider_scope`` may surface in two equivalent shapes:

    * a comma-separated string per the Phase 1 DB schema (e.g. ``"codex"``,
      ``"codex,claude"``); or
    * a sorted ``list[str]`` per the Phase 11 service-layer shape.

    An empty / missing scope is treated as no authorization rather than
    universal access.
    """
    scope = getattr(api_key, "provider_scope", None) or ""
    if isinstance(scope, (list, tuple, set, frozenset)):
        parts = [str(part).strip() for part in scope if str(part).strip()]
    else:
        parts = [part.strip() for part in str(scope).split(",") if part.strip()]
    if provider not in parts:
        raise _provider_scope_mismatch(provider)
    return api_key


def api_key_validator_with_provider(provider: str):
    """Return a FastAPI dependency that authorizes a key for ``provider``.

    The returned dependency first delegates to the existing API-key
    validator (which handles cache lookup, header parsing, and the
    ``ProxyAuthError`` -> HTTP mapping). If the resolved key's
    ``provider_scope`` does not include ``provider`` the dependency raises
    HTTP 403 with a descriptive detail string.

    The wrapper intentionally exposes ``validate`` as the public symbol so
    tests / callers can ``app.dependency_overrides[provider_auth.claude_key] = ...``
    to swap in a stub without the inner ``validate_proxy_api_key`` dependency
    being invoked.
    """

    async def validate(api_key: Any = Depends(validate_proxy_api_key)) -> Any:
        return _enforce_provider_scope(api_key, provider)

    validate.__name__ = f"validate_provider_scope_{provider}"
    return validate


__all__ = [
    "api_key_validator_with_provider",
    "_enforce_provider_scope",
]
