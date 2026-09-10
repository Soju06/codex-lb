import json
from pathlib import Path

import pytest

from app.core.clients.native_egress import _NATIVE_PROTOCOL_VERSION, _REQUIRED_NATIVE_CAPABILITIES

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_adapter_matches_shared_native_handshake_fixture() -> None:
    fixture = json.loads(
        (_REPO_ROOT / "crates/codex-lb-protocol/tests/fixtures/handshake-v1.json").read_text(encoding="utf-8")
    )

    assert fixture["client_hello"] == {
        "type": "client_hello",
        "min_protocol_version": _NATIVE_PROTOCOL_VERSION,
        "max_protocol_version": _NATIVE_PROTOCOL_VERSION,
    }
    assert fixture["server_hello"]["protocol_version"] == _NATIVE_PROTOCOL_VERSION
    assert set(fixture["server_hello"]["capabilities"]) == _REQUIRED_NATIVE_CAPABILITIES


@pytest.mark.parametrize("dockerfile_name", ["Dockerfile", "Dockerfile.distroless"])
def test_linux_container_builds_and_installs_locked_native_egress(dockerfile_name: str) -> None:
    dockerfile = (_REPO_ROOT / dockerfile_name).read_text(encoding="utf-8")

    assert "FROM rust:1.96.0-slim-bookworm AS native-egress-build" in dockerfile
    assert "COPY Cargo.toml Cargo.lock ./" in dockerfile
    assert "COPY crates ./crates" in dockerfile
    assert "cargo build --release --locked --package codex-lb-egress-worker --bin codex-lb-native-egress" in dockerfile
    assert (
        "COPY --from=native-egress-build /tmp/codex-lb-native-egress /usr/local/bin/codex-lb-native-egress"
    ) in dockerfile

    runtime = dockerfile.rsplit(" AS runtime", maxsplit=1)[1]
    assert "cargo build" not in runtime
    assert "COPY --from=native-egress-build" in runtime


def test_native_egress_lockfile_pins_codex_release_family() -> None:
    lockfile = (_REPO_ROOT / "Cargo.lock").read_text(encoding="utf-8")

    for name, version in (
        ("reqwest", "0.12.28"),
        ("hyper", "1.8.1"),
        ("hyper-util", "0.1.20"),
        ("rustls", "0.23.36"),
        ("tokio-rustls", "0.26.4"),
        ("hyper-rustls", "0.27.7"),
        ("aws-lc-rs", "1.16.2"),
    ):
        assert f'name = "{name}"\nversion = "{version}"' in lockfile


def test_native_helper_source_supports_persistent_multiplexed_protocol() -> None:
    protocol = (_REPO_ROOT / "crates/codex-lb-protocol/src/lib.rs").read_text(encoding="utf-8")
    http = (_REPO_ROOT / "crates/codex-lb-egress/src/http.rs").read_text(encoding="utf-8")
    runtime = (_REPO_ROOT / "crates/codex-lb-egress/src/runtime.rs").read_text(encoding="utf-8")
    websocket = (_REPO_ROOT / "crates/codex-lb-egress/src/websocket.rs").read_text(encoding="utf-8")
    worker = (_REPO_ROOT / "crates/codex-lb-egress-worker/src/main.rs").read_text(encoding="utf-8")

    assert "enum NativeCommand" in protocol
    assert "ClientHello" in protocol
    assert "ServerHello" in protocol
    assert "Request(NativeRequest)" in protocol
    assert "WebsocketConnect(NativeWebSocketRequest)" in protocol
    assert "Cancel {" in protocol
    assert "struct ClientPool" in http
    assert "DeflateConfig::default()" in websocket
    assert "is_tls_verification_failure" in websocket
    assert "rustls::Error::InvalidCertificate" in websocket
    assert "tasks.spawn" in runtime
    assert "codex_lb_egress::run_stdio()" in worker


def test_native_egress_pins_codex_websocket_forks() -> None:
    manifest = (_REPO_ROOT / "Cargo.toml").read_text(encoding="utf-8")
    lockfile = (_REPO_ROOT / "Cargo.lock").read_text(encoding="utf-8")

    assert "0e5b2d73aa18dd9f0a50ee9ff199d5aef7594186" in manifest
    assert "4fffad30fe373adbdcffab9545e9e9bf4f2fc19f" in manifest
    assert 'name = "tokio-tungstenite"\nversion = "0.28.0"' in lockfile
    assert 'name = "tungstenite"\nversion = "0.27.0"' in lockfile


def test_application_shutdown_closes_native_helper_before_shared_http_client() -> None:
    source = (_REPO_ROOT / "app/main.py").read_text(encoding="utf-8")

    native_close = source.index("await close_discovered_native_egress_client()")
    http_close = source.index("await close_http_client()")
    assert native_close < http_close
