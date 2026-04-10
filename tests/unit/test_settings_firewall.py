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


def test_settings_parses_http_bridge_instance_ring_from_csv(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-b")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING", "instance-a, instance-b")

    settings = Settings()

    assert settings.http_responses_session_bridge_instance_ring == ["instance-a", "instance-b"]


def test_settings_rejects_http_bridge_instance_id_missing_from_ring(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-c")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING", "instance-a, instance-b")

    with pytest.raises(ValidationError):
        Settings()


def test_settings_rejects_shared_http_bridge_advertise_base_url_for_multi_replica(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-a")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING", "instance-a, instance-b")
    monkeypatch.setenv(
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL",
        "http://codex-lb-internal.default.svc.cluster.local:2455",
    )

    with pytest.raises(ValidationError):
        Settings()


def test_settings_rejects_shared_http_bridge_advertise_base_url_without_static_ring(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-a")
    monkeypatch.delenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING", raising=False)
    monkeypatch.setenv(
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL",
        "http://codex-lb-internal.default.svc.cluster.local:2455",
    )

    with pytest.raises(ValidationError):
        Settings()


def test_settings_rejects_non_loopback_ip_http_bridge_advertise_base_url(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-a")
    monkeypatch.setenv(
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL",
        "http://10.0.0.25:2455",
    )

    with pytest.raises(ValidationError):
        Settings()


def test_settings_allows_replica_specific_http_bridge_advertise_base_url(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-a")
    monkeypatch.setenv(
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL",
        "http://instance-a.codex-lb-bridge.default.svc.cluster.local:2455",
    )

    settings = Settings()

    assert settings.http_responses_session_bridge_advertise_base_url is not None
    assert settings.http_responses_session_bridge_advertise_base_url.endswith(":2455")


def test_settings_allows_pod_ip_http_bridge_advertise_base_url(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-a")
    monkeypatch.setenv("POD_IP", "10.0.0.25")
    monkeypatch.setenv(
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL",
        "http://10.0.0.25:2455",
    )

    settings = Settings()

    assert settings.http_responses_session_bridge_advertise_base_url == "http://10.0.0.25:2455"


def test_settings_allows_loopback_http_bridge_advertise_base_url(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-a")
    monkeypatch.setenv(
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL",
        "http://127.0.0.1:2455",
    )

    settings = Settings()

    assert settings.http_responses_session_bridge_advertise_base_url == "http://127.0.0.1:2455"


def test_settings_rejects_loopback_http_bridge_advertise_base_url_for_multi_replica(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "instance-a")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING", "instance-a, instance-b")
    monkeypatch.setenv(
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL",
        "http://127.0.0.1:2455",
    )

    with pytest.raises(ValidationError):
        Settings()
