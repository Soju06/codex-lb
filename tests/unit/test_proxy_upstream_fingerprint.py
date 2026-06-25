from __future__ import annotations

from unittest.mock import patch

from app.core.clients import proxy as proxy_module
from app.core.clients.proxy import (
    _build_upstream_headers,
    build_codex_user_agent,
)


def _lower_keys(headers: dict[str, str]) -> set[str]:
    return {key.lower() for key in headers}


def test_build_codex_user_agent_matches_codex_cli_format():
    ua = build_codex_user_agent("0.142.0")
    assert ua == "codex_cli_rs/0.142.0 (Mac OS 26.5.0; arm64) iTerm.app/3.6.10"


def test_non_native_sdk_http_request_is_rewritten_to_codex_cli_fingerprint():
    inbound = {
        "User-Agent": "OpenAI/Python 2.24.0",
        "x-openai-client-version": "2.24.0",
        "x-openai-client-os": "MacOS",
        "x-openai-client-arch": "arm64",
        "x-openai-client-id": "abc",
        "x-openai-client-user-agent": "OpenAI/Python 2.24.0",
        "originator": "sdk",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_headers(inbound, "tok", "acct-123")

    assert headers["User-Agent"] == "codex_cli_rs/0.142.0 (Mac OS 26.5.0; arm64) iTerm.app/3.6.10"
    lowered = _lower_keys(headers)
    assert "x-openai-client-version" not in lowered
    assert "x-openai-client-os" not in lowered
    assert "x-openai-client-arch" not in lowered
    assert "x-openai-client-id" not in lowered
    assert "x-openai-client-user-agent" not in lowered
    # No originator header is added; inbound SDK originator is stripped.
    assert "originator" not in lowered


def test_non_native_request_uses_pascalcase_account_header():
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_headers({"User-Agent": "OpenAI/Python 2.24.0"}, "tok", "acct-9")

    assert headers["ChatGPT-Account-Id"] == "acct-9"
    assert "chatgpt-account-id" not in headers


def test_non_native_request_version_falls_back_to_settings_default():
    cache = proxy_module.get_codex_version_cache()
    # Real synchronous fallback path: empty cache -> settings default.
    with patch.object(cache, "_cached_version", None):
        headers = _build_upstream_headers({"User-Agent": "OpenAI/Python 2.24.0"}, "tok", None)

    ua = headers["User-Agent"]
    assert ua.startswith("codex_cli_rs/")
    # The fallback version is the configured client-version default.
    from app.core.config.settings import get_settings

    assert get_settings().model_registry_client_version in ua


def test_native_codex_http_request_is_left_unchanged():
    native_ua = "codex_exec/0.142.1 (Mac OS 27.0.0; arm64) unknown (codex_exec; 0.142.1)"
    headers = _build_upstream_headers({"User-Agent": native_ua}, "tok", "acct-1")

    assert headers["User-Agent"] == native_ua
    # Native requests keep the existing lowercase account header.
    assert headers["chatgpt-account-id"] == "acct-1"
    assert "ChatGPT-Account-Id" not in headers


def test_codex_desktop_native_user_agent_is_left_unchanged():
    native_ua = "Codex Desktop/0.142.0 (Mac OS 27.0.0; arm64) unknown (Codex Desktop; 26.616.71553)"
    headers = _build_upstream_headers({"User-Agent": native_ua}, "tok", None)
    assert headers["User-Agent"] == native_ua


def test_request_with_native_codex_transport_header_is_treated_as_native():
    # Non-Codex UA but carries an x-codex-* stream header -> native, untouched.
    inbound = {"User-Agent": "OpenAI/Python 2.24.0", "x-codex-turn-state": "abc"}
    headers = _build_upstream_headers(inbound, "tok", None)
    assert headers["User-Agent"] == "OpenAI/Python 2.24.0"


def test_websocket_header_builder_is_untouched_by_normalization():
    # The websocket path uses a different builder and must not rewrite the UA.
    from app.core.clients.proxy import _build_upstream_websocket_headers

    inbound = {"User-Agent": "OpenAI/Python 2.24.0", "x-openai-client-version": "2.24.0"}
    headers = _build_upstream_websocket_headers(inbound, "tok", "acct-1")
    assert headers["User-Agent"] == "OpenAI/Python 2.24.0"
    assert headers["x-openai-client-version"] == "2.24.0"
