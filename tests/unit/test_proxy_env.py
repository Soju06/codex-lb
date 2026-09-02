import pytest

from app.core.utils.proxy_env import resolve_http_proxy_from_env

pytestmark = pytest.mark.unit


def test_https_native_egress_uses_https_proxy() -> None:
    assert (
        resolve_http_proxy_from_env(
            "https://chatgpt.com/backend-api/codex/responses",
            {"https_proxy": "http://capture.test:18081", "no_proxy": "localhost"},
        )
        == "http://capture.test:18081"
    )


def test_native_http_proxy_honors_no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.utils.proxy_env.urllib.request.proxy_bypass_environment",
        lambda host, proxies: host == "chatgpt.com:443" and proxies["https"] == "http://capture.test:18081",
    )

    assert (
        resolve_http_proxy_from_env(
            "https://chatgpt.com/backend-api/codex/responses",
            {"https_proxy": "http://capture.test:18081"},
        )
        is None
    )


def test_native_http_proxy_normalizes_socks_alias() -> None:
    assert (
        resolve_http_proxy_from_env(
            "https://chatgpt.com/backend-api/codex/responses",
            {"socks_proxy": "http://proxy.test:1080"},
        )
        == "socks5h://proxy.test:1080"
    )
