from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.e2e

_RUN_E2E = os.environ.get("CODEX_LB_RUN_CODEX_PROFILE_E2E") == "1"
_ROOT = Path(__file__).resolve().parents[2]
_BASE_CONFIG = _ROOT / "docs/examples/codex/config.toml"
_PROFILE = _ROOT / "docs/examples/codex/daybreak-blue.config.toml"
_API_KEY = "inert-daybreak-profile-key"
_CapturedRequest = tuple[str, str, dict[str, str]]


class _Server(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[_CapturedRequest] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _capture(self) -> None:
        assert isinstance(self.server, _Server)
        self.server.requests.append(
            (self.command, urlsplit(self.path).path, {name.lower(): value for name, value in self.headers.items()})
        )

    def _respond(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if body:
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._capture()
        self._respond(503)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if length := int(self.headers.get("content-length", "0")):
            self.rfile.read(length)
        self._capture()
        self._respond(400, b'{"error":{"code":"inert_probe_complete","message":"inert probe complete"}}')

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib override
        del format, args


def _write_config(codex_home: Path, port: int) -> None:
    config = _BASE_CONFIG.read_text(encoding="utf-8")
    for provider, path in (("codex-lb", "ordinary"), ("codex-lb-daybreak-blue", "daybreak")):
        section = f"[model_providers.{provider}]"
        section_start = config.index(section)
        base_start = config.index('base_url = "', section_start)
        base_end = config.index("\n", base_start)
        config = config[:base_start] + f'base_url = "http://127.0.0.1:{port}/{path}"' + config[base_end:]
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(config, encoding="utf-8")
    shutil.copyfile(_PROFILE, codex_home / "daybreak-blue.config.toml")


def _run_codex(
    codex: str, sandbox_exec: str, root: Path, port: int, *, include_key: bool
) -> subprocess.CompletedProcess[str]:
    codex_home = root / "codex-home"
    _write_config(codex_home, port)
    env = {
        "CODEX_HOME": str(codex_home),
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "NO_PROXY": "127.0.0.1,localhost",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "SHELL": "/bin/sh",
        "TERM": "dumb",
        "TMPDIR": str(root),
        "USER": "codex-profile-e2e",
    }
    if include_key:
        env["CODEX_LB_API_KEY"] = _API_KEY
    policy = f'(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:{port}"))'
    command = [
        sandbox_exec,
        "-p",
        policy,
        codex,
        "exec",
        "--strict-config",
        "--profile",
        "daybreak-blue",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-rules",
        "-C",
        str(root),
        "Reply with OK only.",
    ]
    return subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=45, check=False)


@pytest.mark.skipif(not _RUN_E2E, reason="set CODEX_LB_RUN_CODEX_PROFILE_E2E=1 for the installed-Codex proof")
def test_installed_codex_daybreak_profile_emits_authenticated_capability_before_fallback(tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("the network-deny harness requires macOS sandbox-exec")
    codex, sandbox_exec = shutil.which("codex"), shutil.which("sandbox-exec")
    if codex is None or sandbox_exec is None:
        pytest.skip("installed codex and sandbox-exec are required")

    server = _Server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        result = _run_codex(codex, sandbox_exec, tmp_path / "configured", port, include_key=True)
        assert server.requests, result.stdout + result.stderr
        method, path, headers = server.requests[0]
        assert (method, path, headers["upgrade"].lower()) == ("GET", "/daybreak/responses", "websocket")
        assert headers["authorization"] == f"Bearer {_API_KEY}"
        assert headers["x-codex-lb-required-capability"] == "trusted_cyber"

        fallbacks = [(path, headers) for method, path, headers in server.requests if method == "POST"]
        assert fallbacks, result.stdout + result.stderr
        assert all(path == "/daybreak/responses" for path, _headers in fallbacks)
        assert all(headers["authorization"] == f"Bearer {_API_KEY}" for _path, headers in fallbacks)
        assert all(headers["x-codex-lb-required-capability"] == "trusted_cyber" for _path, headers in fallbacks)

        server.requests.clear()
        missing = _run_codex(codex, sandbox_exec, tmp_path / "missing-key", port, include_key=False)
        output = missing.stdout + missing.stderr
        assert missing.returncode != 0
        assert "Missing environment variable" in output and "CODEX_LB_API_KEY" in output
        assert server.requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
