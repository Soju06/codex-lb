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
