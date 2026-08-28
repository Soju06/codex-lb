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
        "Version": "9.9.9",
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
    assert headers["originator"] == "codex_cli_rs"
    assert headers["version"] == "0.142.0"
    assert "Version" not in headers
    assert sum(key.lower() == "originator" for key in headers) == 1
    assert sum(key.lower() == "version" for key in headers) == 1


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
    assert headers["version"] == get_settings().model_registry_client_version
    assert headers["originator"] == "codex_cli_rs"


def test_native_codex_http_request_is_left_unchanged():
    native_ua = "codex_exec/0.142.1 (Mac OS 27.0.0; arm64) unknown (codex_exec; 0.142.1)"
    headers = _build_upstream_headers(
        {"User-Agent": native_ua, "originator": "codex_exec", "version": "0.142.1"},
        "tok",
        "acct-1",
    )

    assert headers["User-Agent"] == native_ua
    assert headers["originator"] == "codex_exec"
    assert headers["version"] == "0.142.1"
    # Native requests keep the existing lowercase account header.
    assert headers["chatgpt-account-id"] == "acct-1"
    assert "ChatGPT-Account-Id" not in headers


def test_codex_desktop_native_user_agent_is_left_unchanged():
    native_ua = "Codex Desktop/0.142.0 (Mac OS 27.0.0; arm64) unknown (Codex Desktop; 26.616.71553)"
    headers = _build_upstream_headers({"User-Agent": native_ua}, "tok", None)
    assert headers["User-Agent"] == native_ua


def test_sdk_request_replaying_turn_state_is_still_normalized():
    # Regression for the Codex P2 finding: an HTTP SDK client replays the
    # x-codex-turn-state continuity token the upstream returned. That transport
    # header must NOT make the request count as native, or the downgraded SDK
    # fingerprint would reach upstream unchanged. The continuity header itself
    # is preserved (only the fingerprint is normalized).
    inbound = {
        "User-Agent": "OpenAI/Python 2.24.0",
        "x-openai-client-version": "2.24.0",
        "x-codex-turn-state": "abc",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_headers(inbound, "tok", None)
    assert headers["User-Agent"].startswith("codex_cli_rs/")
    lowered = _lower_keys(headers)
    assert "x-openai-client-version" not in lowered
    # Continuity header is preserved for sticky routing.
    assert headers["x-codex-turn-state"] == "abc"


def test_native_originator_header_marks_request_native():
    # A native Codex originator identifies a first-party client and is left
    # unchanged.
    inbound = {"User-Agent": "OpenAI/Python 2.24.0", "originator": "codex_vscode"}
    headers = _build_upstream_headers(inbound, "tok", None)
    assert headers["User-Agent"] == "OpenAI/Python 2.24.0"
    assert headers["originator"] == "codex_vscode"


def test_first_party_codex_sdk_ts_originator_is_native():
    # Regression for the Codex P2 finding: codex_sdk_ts is a first-party Codex
    # originator the backend whitelists (named in proposal.md). It must be
    # treated as native so its User-Agent and originator are not rewritten.
    inbound = {"User-Agent": "OpenAI/Node 5.0.0", "originator": "codex_sdk_ts"}
    headers = _build_upstream_headers(inbound, "tok", None)
    assert headers["User-Agent"] == "OpenAI/Node 5.0.0"
    assert headers["originator"] == "codex_sdk_ts"


def test_codex_sdk_ts_user_agent_prefix_is_native():
    # A codex_sdk_ts User-Agent prefix also identifies a first-party client.
    native_ua = "codex_sdk_ts/5.0.0 (Mac OS 27.0.0; arm64)"
    headers = _build_upstream_headers({"User-Agent": native_ua}, "tok", "acct-1")
    assert headers["User-Agent"] == native_ua
    assert headers["chatgpt-account-id"] == "acct-1"
    assert "ChatGPT-Account-Id" not in headers


def test_websocket_non_native_sdk_request_is_normalized():
    # Regression for the Codex P1 finding: with upstream_stream_transport="auto",
    # a non-native SDK follow-up that replays x-codex-turn-state is routed onto
    # the websocket path. The websocket builder must apply the same codex_cli_rs
    # persona rewrite as the HTTP builder, otherwise the SDK fingerprint reaches
    # upstream unchanged and the priority-downgrade mitigation is bypassed.
    from app.core.clients.proxy import _build_upstream_websocket_headers

    inbound = {
        "User-Agent": "OpenAI/Python 2.24.0",
        "x-openai-client-version": "2.24.0",
        "x-codex-turn-state": "abc",
        "originator": "sdk",
        "Version": "9.9.9",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_websocket_headers(inbound, "tok", "acct-1")

    assert headers["User-Agent"] == "codex_cli_rs/0.142.0 (Mac OS 26.5.0; arm64) iTerm.app/3.6.10"
    lowered = _lower_keys(headers)
    assert "x-openai-client-version" not in lowered
    assert headers["originator"] == "codex_cli_rs"
    assert headers["version"] == "0.142.0"
    assert "Version" not in headers
    # Non-native uses the PascalCase account header, mirroring the HTTP builder.
    assert headers["ChatGPT-Account-Id"] == "acct-1"
    assert "chatgpt-account-id" not in headers
    # Continuity header is preserved for sticky routing.
    assert headers["x-codex-turn-state"] == "abc"


def test_websocket_native_codex_request_is_left_unchanged():
    # A first-party Codex websocket client must keep its native fingerprint and
    # the existing lowercase account header.
    from app.core.clients.proxy import _build_upstream_websocket_headers

    native_ua = "codex_cli_rs/0.142.0 (Mac OS 27.0.0; arm64) iTerm.app/3.6.10"
    inbound = {"User-Agent": native_ua, "x-codex-turn-state": "abc"}
    headers = _build_upstream_websocket_headers(inbound, "tok", "acct-1")
    assert headers["User-Agent"] == native_ua
    assert headers["chatgpt-account-id"] == "acct-1"
    assert "ChatGPT-Account-Id" not in headers
    assert headers["x-codex-turn-state"] == "abc"


def test_websocket_connect_preserves_canonical_installation_header_after_filtering():
    # Regression for the Codex P2 finding: a native direct websocket request that
    # supplies both x-codex-installation-id and x-codex-turn-metadata has its
    # standalone installation header stripped by filter_inbound_websocket_headers
    # (it lives in IGNORE_INBOUND_HEADERS). The connect path re-applies that filter
    # inside _build_upstream_websocket_headers, so the selected-account header that
    # apply_codex_installation_headers injects must survive it -- otherwise the
    # websocket handshake loses the header parity the HTTP /codex/responses egress
    # keeps. This mirrors what _open_upstream_websocket does before connecting.
    from app.core.clients.proxy import apply_codex_installation_headers
    from app.core.clients.proxy_websocket import (
        _build_upstream_websocket_headers,
        filter_inbound_websocket_headers,
    )

    native_ua = "codex_cli_rs/0.142.0 (Mac OS 27.0.0; arm64) iTerm.app/3.6.10"
    client_inbound = {
        "User-Agent": native_ua,
        "x-codex-installation-id": "client-installation",
        "x-codex-turn-metadata": '{"installation_id":"client-installation","turn":1}',
    }
    # proxy_responses_websocket first filters inbound headers (drops installation id)...
    filtered = filter_inbound_websocket_headers(client_inbound)
    assert "x-codex-installation-id" not in _lower_keys(filtered)
    # ...then _open_upstream_websocket normalizes the selected account's canonical id.
    normalized = apply_codex_installation_headers(filtered, "acct-canonical")

    headers = _build_upstream_websocket_headers(normalized, "tok", "acct-1")

    lowered = {key.lower(): value for key, value in headers.items()}
    assert lowered["x-codex-installation-id"] == "acct-canonical"
    import json

    turn_metadata = json.loads(lowered["x-codex-turn-metadata"])
    assert turn_metadata["installation_id"] == "acct-canonical"


def test_non_native_request_strips_x_stainless_sdk_headers():
    # Regression for the Codex P2 finding: OpenAI SDKs attach an x-stainless-*
    # header family the API layer treats as an OpenAI SDK signal. Installing the
    # codex_cli_rs User-Agent is not enough; the x-stainless-* headers must be
    # stripped by prefix or upstream can still distinguish SDK traffic and apply
    # the downgrade this change avoids.
    inbound = {
        "User-Agent": "OpenAI/Python 2.24.0",
        "x-stainless-os": "MacOS",
        "x-stainless-arch": "arm64",
        "x-stainless-runtime": "CPython",
        "x-stainless-runtime-version": "3.13.0",
        "x-stainless-package-version": "2.24.0",
        "x-stainless-lang": "python",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_headers(inbound, "tok", None)
    assert headers["User-Agent"].startswith("codex_cli_rs/")
    assert not any(key.lower().startswith("x-stainless-") for key in headers)


def test_websocket_non_native_request_strips_x_stainless_sdk_headers():
    # The websocket builder must strip x-stainless-* too, for the auto-transport
    # turn-state continuity path.
    from app.core.clients.proxy import _build_upstream_websocket_headers

    inbound = {
        "User-Agent": "OpenAI/Python 2.24.0",
        "x-stainless-os": "MacOS",
        "x-stainless-runtime-version": "3.13.0",
        "x-codex-turn-state": "abc",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_websocket_headers(inbound, "tok", None)
    assert headers["User-Agent"].startswith("codex_cli_rs/")
    assert not any(key.lower().startswith("x-stainless-") for key in headers)
    assert headers["x-codex-turn-state"] == "abc"


def test_upstream_log_account_id_lookup_is_case_insensitive():
    # Regression for the Codex P3 finding: a normalized non-native request carries
    # the account id under PascalCase ChatGPT-Account-Id. The upstream log helper
    # must read it case-insensitively or per-account diagnostics are lost for
    # exactly the SDK traffic this feature investigates.
    from app.core.clients.proxy import _account_id_for_upstream_log

    assert _account_id_for_upstream_log({"ChatGPT-Account-Id": "acct-9"}) == "acct-9"
    assert _account_id_for_upstream_log({"chatgpt-account-id": "acct-9"}) == "acct-9"
    assert _account_id_for_upstream_log({"CHATGPT-ACCOUNT-ID": "acct-9"}) == "acct-9"
    assert _account_id_for_upstream_log({"User-Agent": "x"}) is None
