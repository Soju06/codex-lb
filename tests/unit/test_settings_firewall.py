from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


def test_settings_parses_firewall_trusted_proxy_cidrs_from_csv(monkeypatch):
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32, 10.0.0.0/8")
    settings = Settings()
    assert settings.firewall_trusted_proxy_cidrs == ["127.0.0.1/32", "10.0.0.0/8"]


def test_settings_rejects_invalid_firewall_trusted_proxy_cidr(monkeypatch):
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "not-a-cidr")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_defaults_compact_upstream_read_timeout(monkeypatch):
    monkeypatch.delenv("CODEX_LB_COMPACT_UPSTREAM_READ_TIMEOUT_SECONDS", raising=False)
    settings = Settings()
    assert settings.compact_upstream_read_timeout_seconds == 120.0
