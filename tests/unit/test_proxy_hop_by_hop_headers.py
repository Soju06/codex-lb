from __future__ import annotations

from unittest.mock import patch

from app.core.clients import proxy as proxy_module
from app.core.clients.proxy import _build_upstream_headers


def _lower_keys(headers: dict[str, str]) -> set[str]:
    return {key.lower() for key in headers}


def test_http_builder_strips_hop_by_hop_and_connection_nominated_headers():
    native_ua = "codex_exec/0.151.0 (Ubuntu 24.4.0; x86_64) dumb"
    inbound = {
        "User-Agent": native_ua,
        "originator": "codex_exec",
        "version": "0.151.0",
        "Authorization": "Bearer inbound-token",
        "chatgpt-account-id": "inbound-account",
        "x-codex-turn-state": "turn-state",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Transfer-Encoding": "chunked",
        "TE": "trailers",
        "Trailer": "x-checksum",
        "Upgrade": "websocket",
        "Connection": "keep-alive, x-client-hop, authorization, chatgpt-account-id, accept, content-type",
        "Keep-Alive": "timeout=5",
        "Proxy-Connection": "keep-alive",
        "x-client-hop": "drop-me",
    }

    headers = _build_upstream_headers(inbound, "selected-token", "selected-account")
    lowered = {key.lower(): value for key, value in headers.items()}

    for name in (
        "connection",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "x-client-hop",
    ):
        assert name not in lowered
    assert lowered["user-agent"] == native_ua
    assert lowered["originator"] == "codex_exec"
    assert lowered["version"] == "0.151.0"
    assert lowered["authorization"] == "Bearer selected-token"
    assert lowered["chatgpt-account-id"] == "selected-account"
    assert lowered["x-codex-turn-state"] == "turn-state"
    assert lowered["accept"] == "text/event-stream"
    assert lowered["content-type"] == "application/json"


def test_connection_nominated_originator_does_not_mark_request_native():
    inbound = {
        "User-Agent": "OpenAI/Python 2.24.0",
        "originator": "codex_exec",
        "version": "0.151.0",
        "Connection": "keep-alive, originator",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_headers(inbound, "tok", None)

    assert headers["User-Agent"].startswith("codex_cli_rs/0.142.0")
    assert headers["originator"] == "codex_cli_rs"
    assert headers["version"] == "0.142.0"


def test_connection_nominated_native_user_agent_does_not_mark_request_native():
    native_ua = "codex_exec/0.151.0 (Ubuntu 24.4.0; x86_64) dumb"
    inbound = {
        "User-Agent": native_ua,
        "originator": "sdk",
        "Connection": "User-Agent",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_headers(inbound, "tok", None)

    assert headers["User-Agent"] != native_ua
    assert headers["User-Agent"].startswith("codex_cli_rs/0.142.0")
    assert headers["originator"] == "codex_cli_rs"
    assert headers["version"] == "0.142.0"


def test_non_nominated_native_identity_stays_native():
    native_ua = "codex_exec/0.151.0 (Ubuntu 24.4.0; x86_64) dumb"
    inbound = {
        "User-Agent": native_ua,
        "originator": "codex_exec",
        "version": "0.151.0",
        "Connection": "keep-alive, x-client-hop",
        "x-client-hop": "drop-me",
    }

    headers = _build_upstream_headers(inbound, "tok", None)

    assert headers["User-Agent"] == native_ua
    assert headers["originator"] == "codex_exec"
    assert headers["version"] == "0.151.0"
    assert "connection" not in _lower_keys(headers)
    assert "x-client-hop" not in _lower_keys(headers)


def test_http_sanitation_preserves_synthesized_routing_hint():
    inbound = {
        "User-Agent": "OpenAI/Python 2.24.0",
        "Connection": "keep-alive, x-client-hop",
        "x-client-hop": "drop-me",
        "X-Codex-Routing-Hint": "model=untrusted;tier=default",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_headers(
            inbound,
            "tok",
            "acct-1",
            routing_hint=("gpt-6-astra", "priority"),
        )

    assert "x-client-hop" not in _lower_keys(headers)
    assert headers["x-codex-routing-hint"] == "model=gpt-6-astra;tier=priority"
