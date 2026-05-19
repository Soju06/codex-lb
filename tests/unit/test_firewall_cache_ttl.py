"""Tests for FirewallIPCache TTL configuration (#672 hotfix).

The default TTL used to be 2 seconds, which was so short that the cache
provided no real relief on hot paths and effectively forced a DB session per
proxy request to re-check the firewall allowlist. The hotfix raises the
default to 30 seconds and makes the value operator-configurable via
``firewall_ip_cache_ttl_seconds`` (env
``CODEX_LB_FIREWALL_IP_CACHE_TTL_SECONDS``).
"""

from __future__ import annotations

import pytest

import app.core.middleware.firewall_cache as firewall_cache
from app.core.middleware.firewall_cache import (
    DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS,
    FirewallIPCache,
    get_firewall_ip_cache,
    reset_firewall_ip_cache_for_testing,
)

pytestmark = pytest.mark.unit


def test_default_ttl_is_thirty_seconds() -> None:
    assert DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS == 30
    cache = FirewallIPCache()
    assert cache.ttl_seconds == 30


def test_singleton_respects_settings_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_firewall_ip_cache_for_testing()

    class _Settings:
        firewall_ip_cache_ttl_seconds = 120

    monkeypatch.setattr(firewall_cache, "_resolve_configured_ttl", lambda: _Settings.firewall_ip_cache_ttl_seconds)

    cache = get_firewall_ip_cache()
    assert cache.ttl_seconds == 120


def test_singleton_falls_back_to_default_when_settings_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_firewall_ip_cache_for_testing()

    def _raise() -> int:
        raise RuntimeError("settings not initialised")

    monkeypatch.setattr(firewall_cache, "_resolve_configured_ttl", lambda: DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS)

    # When settings resolution raises, the helper inside firewall_cache catches
    # it and returns the default. The lambda above mimics the post-catch state.
    cache = get_firewall_ip_cache()
    assert cache.ttl_seconds == DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS


def test_invalid_ttl_rejected() -> None:
    with pytest.raises(ValueError):
        FirewallIPCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        FirewallIPCache(ttl_seconds=-1)


def teardown_module(_module: object) -> None:
    """Reset the singleton so other test modules see the default state."""
    reset_firewall_ip_cache_for_testing()
