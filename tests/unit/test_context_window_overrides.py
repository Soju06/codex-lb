from __future__ import annotations

import pytest

from app.core.config.context_window_overrides import (
    ContextWindowOverride,
    ModelContextWindowOverridesCache,
    effective_context_window_overrides,
    resolve_context_window_overrides,
)

pytestmark = pytest.mark.unit


def test_resolve_dashboard_row_wins_over_environment_entry_and_reports_env_value() -> None:
    resolved = resolve_context_window_overrides({"gpt-5.4": 515_000}, {"gpt-5.4": 300_000})
    assert resolved == {"gpt-5.4": ContextWindowOverride("gpt-5.4", 515_000, "dashboard", 300_000)}


def test_resolve_slug_without_row_inherits_environment_entry() -> None:
    resolved = resolve_context_window_overrides({}, {"gpt-5.4": 300_000})
    assert resolved == {"gpt-5.4": ContextWindowOverride("gpt-5.4", 300_000, "env", 300_000)}


def test_resolve_dashboard_only_row_has_no_environment_value() -> None:
    resolved = resolve_context_window_overrides({"custom-model": 32_768}, {})
    assert resolved == {"custom-model": ContextWindowOverride("custom-model", 32_768, "dashboard", None)}


def test_resolve_is_per_slug_and_sorted() -> None:
    resolved = resolve_context_window_overrides({"b-model": 2}, {"a-model": 1, "b-model": 9})
    assert list(resolved) == ["a-model", "b-model"]
    assert resolved["a-model"].source == "env"
    assert resolved["b-model"] == ContextWindowOverride("b-model", 2, "dashboard", 9)


def test_effective_overrides_merge_layers_with_dashboard_first() -> None:
    assert effective_context_window_overrides({"b": 2}, {"a": 1, "b": 9}) == {"a": 1, "b": 2}
    assert effective_context_window_overrides({}, {}) == {}


def test_cache_rejects_non_positive_ttl_and_clear_forgets_snapshot() -> None:
    with pytest.raises(ValueError):
        ModelContextWindowOverridesCache(ttl_seconds=0)
    cache = ModelContextWindowOverridesCache()
    cache._cached = {"gpt-5.4": 1}
    cache._cached_at = 1.0
    cache.clear()
    assert cache._cached is None
    assert cache._cached_at == 0.0
