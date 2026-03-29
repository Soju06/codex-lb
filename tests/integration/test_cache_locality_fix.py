from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_mini_and_large_requests_use_different_cache_keys():
    """Verify that gpt-5.4-mini and gpt-5.3-codex requests produce different prompt_cache_keys
    due to model-class prefix separation."""
    from app.modules.proxy.service import _derive_prompt_cache_key
    from app.core.openai.requests import ResponsesRequest

    mini_payload = ResponsesRequest(
        model="gpt-5.4-mini",
        instructions="You are helpful.",
        input=[{"role": "user", "content": "Hello"}],
    )

    large_payload = ResponsesRequest(
        model="gpt-5.3-codex",
        instructions="You are helpful.",
        input=[{"role": "user", "content": "Hello"}],
    )

    mini_key = _derive_prompt_cache_key(mini_payload, None)
    large_key = _derive_prompt_cache_key(large_payload, None)

    assert mini_key != large_key, "Mini and large models should produce different cache keys"
    assert mini_key.startswith("mini-"), f"Mini key should start with 'mini-', got {mini_key}"
    assert large_key.startswith("codex-"), f"Large key should start with 'codex-', got {large_key}"


def test_same_model_class_produces_same_cache_key():
    """Verify that two requests with the same model class produce the same cache key."""
    from app.modules.proxy.service import _derive_prompt_cache_key
    from app.core.openai.requests import ResponsesRequest

    payload = ResponsesRequest(
        model="gpt-5.3-codex",
        instructions="You are helpful.",
        input=[{"role": "user", "content": "Hello"}],
    )

    key1 = _derive_prompt_cache_key(payload, None)
    key2 = _derive_prompt_cache_key(payload, None)

    assert key1 == key2, "Same model class should produce identical cache keys"


def test_prompt_cache_bridge_idle_ttl_from_settings():
    """Verify that PROMPT_CACHE bridge idle TTL is read from dashboard settings."""
    from app.modules.proxy.service import _effective_http_bridge_idle_ttl_seconds, _AffinityPolicy
    from app.db.models import StickySessionKind

    affinity = _AffinityPolicy(
        key="cache-key-123",
        kind=StickySessionKind.PROMPT_CACHE,
    )

    result = _effective_http_bridge_idle_ttl_seconds(
        affinity=affinity,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=900.0,
        prompt_cache_idle_ttl_seconds=3600.0,
    )

    assert result == 3600.0, "PROMPT_CACHE should use prompt_cache_idle_ttl_seconds"


def test_codex_session_idle_ttl_unchanged():
    """Verify that CODEX_SESSION idle TTL behavior is unchanged."""
    from app.modules.proxy.service import _effective_http_bridge_idle_ttl_seconds, _AffinityPolicy
    from app.db.models import StickySessionKind

    affinity = _AffinityPolicy(
        key="session-123",
        kind=StickySessionKind.CODEX_SESSION,
    )

    result = _effective_http_bridge_idle_ttl_seconds(
        affinity=affinity,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=900.0,
        prompt_cache_idle_ttl_seconds=3600.0,
    )

    assert result == 900.0, "CODEX_SESSION should use max(idle_ttl_seconds, codex_idle_ttl_seconds)"


def test_model_class_extraction_for_all_model_types():
    """Verify model class extraction works correctly for mini, codex, and standard models."""
    from app.modules.proxy.service import _extract_model_class

    # Mini models (checked first, so mini takes precedence)
    assert _extract_model_class("gpt-5.4-mini") == "mini"
    assert _extract_model_class("gpt-4-mini") == "mini"

    # Codex models (checked second)
    assert _extract_model_class("gpt-5.3-codex") == "codex"
    assert _extract_model_class("gpt-5.3-codex-spark") == "codex"

    # Standard models (default)
    assert _extract_model_class("gpt-5.4") == "std"
    assert _extract_model_class("gpt-4") == "std"
    assert _extract_model_class("gpt-4-turbo") == "std"
